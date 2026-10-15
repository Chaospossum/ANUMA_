"""Stage D DEM-noise check on synthetic arrays and GeoTIFFs (no real data)."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.transform import from_origin

from anuma import stage_d, stage_d_gate
from anuma.config import load_config
from anuma.slope import horn_slope_deg
from anuma.slope20 import compute_slope_untiled

REPO = Path(__file__).resolve().parents[1]
PREFLIGHT_PATH = REPO / "hpc" / "preflight.py"
SPEC = importlib.util.spec_from_file_location("anuma_hpc_preflight_d", PREFLIGHT_PATH)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)

FINE = from_origin(0.0, 4000.0, 20.0, 20.0)
# Same nesting as the real grids: 240 m origin 80 m outside the 20 m origin.
COARSE = Affine(240.0, 0.0, -80.0, 0.0, -240.0, 4080.0)
SHAPE = (200, 200)
TABLE = [
    ("1318:1219", 1.000), ("1292:1255", 1.000), ("1350:1392", 1.000),
    ("753:1681", 1.000), ("756:1236", 1.000), ("761:1285", 0.988),
    ("619:799", 0.970), ("791:1624", 1.000), ("779:1564", 0.844),
    ("1252:1287", 0.948), ("1277:1142", 0.176), ("1206:1114", 0.022),
    ("972:515", 0.052), ("845:1086", 0.0), ("1325:1333", 0.0),
]


def _cfg() -> dict:
    return load_config()


def _dn() -> dict:
    return stage_d.require_frozen_config(_cfg())


def _terrain() -> np.ndarray:
    rows, cols = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]].astype(np.float64)
    x = cols * 20.0
    y = rows * 20.0
    terrain = 1000.0 + 100.0 * np.sin(x / 300.0) * np.cos(y / 400.0) + 0.02 * x
    # Round-trip through float32 so arrays match what the GeoTIFFs hold.
    return terrain.astype(np.float32).astype(np.float64)


def _write_tif(path: Path, array: np.ndarray, nodata=np.nan) -> Path:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype="float32",
        crs="+proj=stere +lat_0=-90 +lon_0=0 +R=1737400 +units=m +no_defs",
        transform=FINE,
        nodata=nodata,
    ) as dataset:
        dataset.write(array.astype(np.float32), 1)
    return path


def _synthetic_site(site_index: int = 0, *, err: float = 2.0, effres: float | None = 60.0, half_px: int = 60):
    row, col = [(8, 8), (5, 9)][site_index]
    window = stage_d.site_window(
        site_id=f"{row}:{col}",
        site_index=site_index,
        default_rank=site_index + 1,
        row_240=row,
        col_240=col,
        coarse_transform=COARSE,
        fine_transform=FINE,
        half_size_px=half_px,
    )
    terrain = _terrain()
    r0, c0, size = window.row0, window.col0, window.size
    dem = terrain[r0 - 1 : r0 + size + 1, c0 - 1 : c0 + size + 1].copy()
    return stage_d.prepare_site(
        window,
        dem=dem,
        err=np.full(dem.shape, err),
        effres=None if effres is None else np.full((size, size), effres),
        ldsm_err=np.full((size, size), 1.0),
        fine_transform=FINE,
        nm=_dn()["noise_model"],
        radius_m=1000.0,
        cell_size_m=240.0,
        cell_m=20.0,
        max_slope_deg=8.0,
    )


# -- noise model -----------------------------------------------------------


@pytest.mark.parametrize("sigma", [17.0, 25.5, 60.0])
def test_kernel_is_normalized_and_truncated_at_four_sigma(sigma):
    kernel = stage_d.gaussian_kernel_1d(sigma, 20.0)
    radius = math.ceil(4.0 * sigma / 20.0)
    assert len(kernel) == 2 * radius + 1
    assert kernel.sum() == pytest.approx(1.0, abs=1e-15)
    k2 = np.outer(kernel, kernel)
    assert k2.sum() == pytest.approx(1.0, abs=1e-12)
    assert stage_d.kernel_rms_norm(kernel) == pytest.approx(math.sqrt(np.sum(k2**2)), rel=1e-12)


@pytest.mark.parametrize("sigma", [17.0, 60.0])
def test_rescaled_correlated_noise_has_unit_std(sigma):
    kernel = stage_d.gaussian_kernel_1d(sigma, 20.0)
    samples = [
        stage_d.unit_correlated_noise(stage_d.draw_rng(1, 0, 0, draw), (300, 300), kernel)
        for draw in range(6)
    ]
    pooled = np.concatenate([s.ravel() for s in samples])
    assert pooled.std(ddof=1) == pytest.approx(1.0, abs=0.03)
    assert abs(pooled.mean()) < 0.05
    # Smoothing alone shrinks the variance; the rescale restores it.
    assert stage_d.kernel_rms_norm(kernel) < 0.5


def test_unit_noise_equals_two_dimensional_convolution_on_the_padded_field():
    from scipy.signal import convolve2d

    kernel = stage_d.gaussian_kernel_1d(30.0, 20.0)
    radius = (len(kernel) - 1) // 2
    shape = (40, 30)
    got = stage_d.unit_correlated_noise(stage_d.draw_rng(5, 1, 2, 3), shape, kernel)
    white = stage_d.draw_rng(5, 1, 2, 3).standard_normal((shape[0] + 2 * radius, shape[1] + 2 * radius))
    full = convolve2d(white, np.outer(kernel, kernel), mode="valid")
    expected = full / math.sqrt(np.sum(np.outer(kernel, kernel) ** 2))
    assert got.shape == shape
    np.testing.assert_allclose(got, expected, rtol=0, atol=1e-12)


def test_noise_scales_with_err_map():
    kernel = stage_d.gaussian_kernel_1d(17.0, 20.0)
    unit = stage_d.unit_correlated_noise(stage_d.draw_rng(9, 0, 0, 0), (400, 400), kernel)
    err = np.where(np.arange(400)[None, :] < 200, 1.0, 5.0) * np.ones((400, 1))
    noise = err * unit
    assert noise[:, :200].std() == pytest.approx(1.0, abs=0.06)
    assert noise[:, 200:].std() == pytest.approx(5.0, abs=0.3)


def test_sigma_from_effres_reads_fwhm_and_falls_back_to_40_m():
    nm = _dn()["noise_model"]
    staged = stage_d.sigmas_from_effres(47.1, nm)
    assert staged["sigma_m"]["primary"] == pytest.approx(47.1 / 2.355)
    assert staged["sigma_m"]["sensitivity"] == 47.1
    fallback = stage_d.sigmas_from_effres(None, nm)
    assert fallback["effres_source"] == "fallback_fwhm_m"
    assert fallback["sigma_m"]["primary"] == pytest.approx(40.0 / 2.355)
    assert fallback["sigma_m"]["primary"] == pytest.approx(17.0, abs=0.02)
    site = _synthetic_site(effres=None)
    assert site.effres_source == "fallback_fwhm_m (EFFRES not staged)"
    assert site.window_stats["effres_m"] is None


def test_window_stats_record_effres_range_and_err_median():
    site = _synthetic_site()
    assert site.window_stats["effres_m"]["min"] == site.window_stats["effres_m"]["max"] == 60.0
    assert site.window_stats["err_m"]["median"] == 2.0
    assert site.window_stats["ldsm_err_deg_median"] == 1.0
    assert site.window_stats["kernel_radius_px"] == {"primary": 6, "sensitivity": 12}


# -- geometry and zero-noise path --------------------------------------------


def test_window_centre_circle_and_cell_follow_the_nested_grids():
    site = _synthetic_site()
    w = site.window
    assert (w.centre_x, w.centre_y) == (1960.0, 2040.0)
    assert (w.row0 + 60, w.col0 + 60) == (98, 98)
    assert site.window_stats["cell_pixels"] == 144
    # Pixel centres within 1 km of a corner-centred point: same count as Stage B.
    assert site.window_stats["circle_pixels"] == 7860


def test_zero_noise_path_equals_plain_horn_slope(tmp_path):
    terrain = _terrain()
    dem_path = _write_tif(tmp_path / "dem.tif", terrain)
    site = _synthetic_site()
    w = site.window
    with rasterio.open(dem_path) as dataset:
        block = stage_d.read_window(dataset, w, halo=1)
    expected = horn_slope_deg(block, 20.0)[1:-1, 1:-1]
    np.testing.assert_array_equal(
        stage_d.slope_tile_from_array(block, 20.0, halo=1), expected
    )
    full = compute_slope_untiled(dem_path)
    np.testing.assert_array_equal(site.zero_slope, full[w.row0 : w.row0 + w.size, w.col0 : w.col0 + w.size])
    # ERR = 0 leaves every draw on the zero-noise statistics.
    zero_err = _synthetic_site(err=0.0)
    record = stage_d.evaluate_draw(zero_err, 0, 0, seed=1, max_slope_deg=8.0, cell_m=20.0)
    for key in ("fraction_1km", "in_cell_count", "largest_patch_m2"):
        assert record[key] == zero_err.window_stats["zero_noise"][key]
    assert record["slope_diff_deg"]["sum_sq"] == 0.0


def test_zero_noise_check_against_a_stage_b_style_slope(tmp_path):
    dem_path = _write_tif(tmp_path / "dem.tif", _terrain())
    full = compute_slope_untiled(dem_path)
    site = _synthetic_site()
    fraction = site.window_stats["zero_noise"]["fraction_1km"]
    table = {"8:8": round(fraction, 2)}
    check = stage_d.zero_noise_check(site, full, table, atol_deg=1e-4, fraction_ranks=[1, 10])
    assert check["passed"] and check["slope_slice"]["max_abs_diff_deg"] == 0.0
    table_off = {"8:8": round(fraction, 2) + 0.02}
    assert not stage_d.zero_noise_check(site, full, table_off, atol_deg=1e-4, fraction_ranks=[1, 10])["passed"]
    shifted = full.copy()
    shifted[100, 100] += 0.01
    assert not stage_d.zero_noise_check(site, shifted, table, atol_deg=1e-4, fraction_ranks=[1, 10])["passed"]
    # Outside the tabulated ranks only the slope slice is checked.
    assert stage_d.zero_noise_check(site, full, {}, atol_deg=1e-4, fraction_ranks=[2, 10])["passed"]


def test_slope_statistics_counts_fraction_cell_and_eight_connected_patch():
    slope = np.full((5, 5), 20.0, dtype=np.float32)
    slope[0, 0] = slope[1, 1] = 3.0  # diagonal neighbours: one 8-connected patch
    slope[4, 4] = 5.0
    slope[2, 4] = np.nan
    circle = np.ones((5, 5), dtype=bool)
    cell = np.zeros((5, 5), dtype=bool)
    cell[:2, :2] = True
    stats = stage_d.slope_statistics(slope, circle, cell, max_slope_deg=8.0, cell_m=20.0)
    assert stats["fraction_1km"] == pytest.approx(3 / 24)
    assert stats["in_cell_count"] == 2
    assert stats["largest_patch_m2"] == 2 * 400.0


# -- seeds, determinism and parallelism --------------------------------------


def test_streams_are_keyed_by_run_site_and_draw():
    a = stage_d.draw_rng(20261016, 0, 1, 2).standard_normal(4)
    np.testing.assert_array_equal(a, stage_d.draw_rng(20261016, 0, 1, 2).standard_normal(4))
    for key in ((1, 1, 2), (0, 2, 2), (0, 1, 3)):
        assert not np.array_equal(a, stage_d.draw_rng(20261016, *key).standard_normal(4))
    expected = np.random.default_rng(
        np.random.SeedSequence(20261016, spawn_key=(0, 1, 2))
    ).standard_normal(4)
    np.testing.assert_array_equal(a, expected)


def test_serial_and_parallel_draws_are_bit_identical_and_order_free():
    sites = {0: _synthetic_site(0), 1: _synthetic_site(1)}
    tasks = stage_d.task_list([0, 1], [0, 1], 3)
    kwargs = {"seed": 20261016, "max_slope_deg": 8.0, "cell_m": 20.0}
    serial = stage_d.evaluate_tasks(sites, tasks, workers=1, **kwargs)
    parallel = stage_d.evaluate_tasks(sites, tasks, workers=3, **kwargs)
    assert stage_d.records_identical(serial, parallel)
    reversed_records = stage_d.evaluate_tasks(sites, tasks[::-1], workers=2, **kwargs)
    assert stage_d.records_identical(serial, reversed_records[::-1])
    assert serial[0]["fraction_1km"] != serial[1]["fraction_1km"] or (
        serial[0]["slope_diff_deg"] != serial[1]["slope_diff_deg"]
    )


def test_noise_study_pilot_path_on_synthetic_sites():
    dn = _dn()
    sites = {0: _synthetic_site(0), 1: _synthetic_site(1)}
    study = stage_d.run_noise_study(
        sites,
        site_indices=[0, 1],
        run_indices=[0],
        draws=8,
        seed=int(dn["seed"]),
        max_slope_deg=8.0,
        cell_m=20.0,
        workers=2,
        ellipse=dn["ellipse"],
        acceptance=dn["acceptance"],
    )
    assert study["serial_parallel_identical"]
    assert len(study["records"]) == 16
    primary = study["per_site"]["8:8"]["primary"]
    assert primary["draws"] == 8 and primary["decisive"]
    assert primary["checks"]["noise_std_over_err"]["pooled_sample_std"] == pytest.approx(1.0, abs=0.05)
    assert primary["in_cell"]["fine_pixels"] == 144
    projection = stage_d.project_production_node_hours(
        fixed_seconds=10.0,
        serial_seconds=study["timings_seconds"]["serial"],
        parallel_seconds=study["timings_seconds"]["parallel"],
        pilot_tasks=[stage_d._padded_task(sites[i], "primary", 20.0) for i in (0, 1) for _ in range(8)],
        production_tasks=[
            stage_d._padded_task(sites[i], run, 20.0)
            for run in ("primary", "sensitivity")
            for i in (0, 1)
            for _ in range(200)
        ],
        workers=2,
        write_seconds=0.1,
        production_node_hours=2.0,
        walltime_hours=1.5,
    )
    assert projection["fits_production_cap"] and projection["fits_walltime"]
    assert projection["projected_serial_seconds"] > 50 * study["timings_seconds"]["serial"]


def test_prepare_sites_from_synthetic_rasters(tmp_path):
    dn = copy.deepcopy(_dn())
    dn["window_half_size_m"] = 1200.0
    terrain = _terrain()
    dem = _write_tif(tmp_path / "dem.tif", terrain)
    err = _write_tif(tmp_path / "err.tif", np.full(SHAPE, 3.0))
    effres = _write_tif(tmp_path / "effres.tif", np.full(SHAPE, 94.2), nodata=-1.0)
    ldsm = _write_tif(tmp_path / "ldsm.tif", np.full(SHAPE, 0.5))
    rule = {
        "site_order": ["8:8", "5:9"],
        "ranking": [
            {"id": "8:8", "default_rank": 1, "row": 8, "col": 8},
            {"id": "5:9", "default_rank": 2, "row": 5, "col": 9},
        ],
    }
    sites, transform = stage_d.prepare_sites_from_rasters(
        rule,
        dem_path=dem,
        err_path=err,
        effres_path=effres,
        ldsm_path=ldsm,
        coarse_transform=COARSE,
        dn=dn,
        max_slope_deg=8.0,
    )
    assert transform == FINE
    assert [sites[i].window.site_id for i in (0, 1)] == ["8:8", "5:9"]
    assert sites[0].sigma_m["primary"] == pytest.approx(94.2 / 2.355, rel=1e-6)
    np.testing.assert_array_equal(sites[0].zero_slope, _synthetic_site(0).zero_slope)
    no_effres, _ = stage_d.prepare_sites_from_rasters(
        rule, dem_path=dem, err_path=err, effres_path=None, ldsm_path=ldsm,
        coarse_transform=COARSE, dn=dn, max_slope_deg=8.0,
    )
    assert no_effres[1].sigma_m["primary"] == pytest.approx(40.0 / 2.355)


# -- decision ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "clause"),
    [
        (np.linspace(0.20, 0.25, 200), "neither"),
        (np.linspace(0.10, 0.30, 200), "width"),
        (np.linspace(0.47, 0.52, 200), "straddle"),
        (np.linspace(0.40, 0.60, 200), "both"),
    ],
)
def test_interval_decision_clauses(values, clause):
    decision = stage_d.interval_decision(values, _dn()["ellipse"])
    assert decision["clause_fired"] == clause
    assert decision["noise_unstable"] == (clause != "neither")


def test_interval_decision_uses_linear_percentiles_and_borderline_band():
    ellipse = _dn()["ellipse"]
    values = np.arange(200, dtype=np.float64) / 1990.0  # width just under 0.10
    decision = stage_d.interval_decision(values, ellipse)
    low, high = np.percentile(values, [5, 95], method="linear")
    assert decision["p_low"] == low and decision["p_high"] == high
    assert not decision["width_clause"] and decision["borderline_width"]
    wide = stage_d.interval_decision(np.linspace(0.0, 0.14, 201), ellipse)
    assert wide["width_clause"] and not wide["borderline_width"]


def test_straddle_boundary_is_low_below_and_high_at_or_above_half():
    ellipse = _dn()["ellipse"]
    at_high = np.concatenate([np.full(189, 0.49), np.full(11, 0.50)])
    # 95th percentile reaches 0.50 exactly: straddles.
    assert np.percentile(at_high, 95) == 0.50
    assert stage_d.interval_decision(at_high, ellipse)["straddle_clause"]
    at_low = np.full(200, 0.50)
    assert not stage_d.interval_decision(at_low, ellipse)["straddle_clause"]


def test_site_summary_reports_delta_flip_rate_and_in_cell_counts():
    zero = {"fraction_1km": 0.50, "in_cell_count": 10, "largest_patch_m2": 800.0, "cell_pixels": 144}
    records = [
        {"fraction_1km": f, "in_cell_count": c, "largest_patch_m2": 400.0 * c}
        for f, c in [(0.48, 0), (0.49, 3), (0.51, 5), (0.52, 7)]
    ]
    summary = stage_d.summarize_site_run(zero, records, _dn()["ellipse"])
    assert summary["pass_fail"]["zero_noise_pass"] is True
    assert summary["pass_fail"]["pass_rate"] == 0.5
    assert summary["pass_fail"]["flip_rate"] == 0.5
    assert summary["delta_vs_zero_noise"]["mean"] == pytest.approx(0.0)
    assert summary["in_cell"]["min"] == 0.0
    assert summary["in_cell"]["fraction_of_draws_count_gt_0"] == 0.75
    assert summary["in_cell"]["zero_noise_count"] == 10
    assert summary["largest_patch_m2"]["zero_noise"] == 800.0


def test_slope_noise_check_is_decisive_for_primary_only():
    site = _synthetic_site()
    records = [{"noise_over_err": {"n": 2, "sum": 0.0, "sum_sq": 2.0}, "slope_diff_deg": {"n": 2, "sum": 0.0, "sum_sq": 50.0}}]
    acceptance = _dn()["acceptance"]
    primary = stage_d.noise_checks(site, records, acceptance, "primary")
    sensitivity = stage_d.noise_checks(site, records, acceptance, "sensitivity")
    assert primary["slope_noise_vs_ldsm_err"]["ratio"] == pytest.approx(math.sqrt(50.0))
    assert not primary["slope_noise_vs_ldsm_err"]["within_bounds"]
    assert primary["slope_noise_vs_ldsm_err"]["decisive"]
    assert not sensitivity["slope_noise_vs_ldsm_err"]["decisive"]


# -- stability rule ---------------------------------------------------------


def _ranked() -> list[dict]:
    return [
        {"row": int(sid.split(":")[0]), "col": int(sid.split(":")[1]), "lon": 0.0, "lat": -89.0, "total": 1.0 - 0.01 * i}
        for i, (sid, _) in enumerate(TABLE)
    ]


def _summary(overrides: dict | None = None) -> dict:
    freq = dict(TABLE)
    freq.update(overrides or {})
    entries = [{"id": sid, "frequency": f} for sid, f in freq.items() if f > 0.0]
    return {"draw_count": 500, "policies": {"decided_per_layer": {"top10_membership_frequency": entries}}}


def test_rule_recomputation_reproduces_the_frozen_lists():
    rule = stage_d.recompute_stability_rule(_summary(), _ranked(), _cfg())
    assert rule["unstable_ids"] == ["779:1564", "1252:1287", "1277:1142", "1206:1114"]
    assert rule["control_ids"] == ["1318:1219", "1292:1255", "1350:1392", "791:1624"]
    assert rule["site_order"] == _dn()["site_order"]
    assert rule["rule"]["rank_margin_ranks"] == [9, 12]
    rows = {row["id"]: row for row in rule["ranking"]}
    assert rows["779:1564"]["clause_rank_margin"] and not rows["779:1564"]["clause_frequency"]
    assert not rows["972:515"]["clause_rank_margin"]


def test_rule_recomputation_asserts_instead_of_reading_config():
    with pytest.raises(ValueError, match="do not reproduce"):
        stage_d.recompute_stability_rule(_summary({"619:799": 0.79}), _ranked(), _cfg())
    swapped = _ranked()
    swapped[10], swapped[12] = swapped[12], swapped[10]
    with pytest.raises(ValueError, match="do not reproduce"):
        stage_d.recompute_stability_rule(_summary(), swapped, _cfg())
    with pytest.raises(ValueError, match="500-draw"):
        stage_d.recompute_stability_rule({**_summary(), "draw_count": 8}, _ranked(), _cfg())


def test_frozen_config_refuses_drift():
    cfg = _cfg()
    cfg["hpc_studies"]["dem_noise"]["ellipse"]["max_interval_width"] = 0.12
    with pytest.raises(ValueError, match="max_interval_width"):
        stage_d.require_frozen_config(cfg)
    cfg = _cfg()
    cfg["hpc_studies"]["dem_noise"]["stability_rule"]["frozen_unstable_ids"] = ["779:1564"]
    with pytest.raises(ValueError, match="frozen_unstable_ids"):
        stage_d.require_frozen_config(cfg)
    cfg = _cfg()
    cfg["hard"]["max_slope_deg"] = 10.0
    with pytest.raises(ValueError, match="max_slope_deg"):
        stage_d.require_frozen_config(cfg)


def test_study_summary_digest_is_checked(tmp_path):
    path = tmp_path / "study-summary.json"
    path.write_text(json.dumps(_summary()))
    with pytest.raises(ValueError, match="not the job 5332991 digest"):
        stage_d.load_study_summary(path)


LOCAL_GRID = REPO / "data" / "grid"
LOCAL_SUMMARY = REPO / "out" / "hpc" / "monte-carlo-full-5332991" / "study-summary.json"


@pytest.mark.skipif(
    not (LOCAL_GRID / "grid.json").is_file() or not LOCAL_SUMMARY.is_file(),
    reason="ingested 240 m arrays and the job 5332991 summary are not staged",
)
def test_rule_from_real_arrays_reproduces_frozen_ids():
    rule = stage_d.rule_from_grid_dir(LOCAL_GRID, LOCAL_SUMMARY)
    assert rule["site_order"] == _dn()["site_order"]


# -- gate, preflight and scripts --------------------------------------------


def _staged_inputs(tmp_path: Path, *, effres: bool = True):
    raw = tmp_path / "raw"
    raw.mkdir()
    files = {
        "LDEM_80S_20MPP_ADJ.TIF": b"dem",
        "LDEM_80S_20MPP_ADJ_ERR.TIF": b"err",
        "LDSM_80S_20MPP_ADJ_ERR.TIF": b"ldsm",
    }
    if effres:
        files["LDEM_80S_20MPP_ADJ_EFFRES.TIF"] = b"effres"
    lines = []
    for name, body in files.items():
        (raw / name).write_bytes(body)
        if name != "LDEM_80S_20MPP_ADJ.TIF":
            lines.append(f"{hashlib.sha256(body).hexdigest()}  ./{name}")
    (raw / "pgda90-sha256.txt").write_text("\n".join(lines) + "\n")
    slope = tmp_path / "slope-20m.npy"
    slope.write_bytes(b"slope")
    summary = tmp_path / "study-summary.json"
    summary.write_text("{}")
    return {"raw_dir": raw, "dem": raw / "LDEM_80S_20MPP_ADJ.TIF", "stage_b_slope": slope, "mc_summary": summary}


def _pin_digests(monkeypatch):
    monkeypatch.setattr(stage_d_gate, "DEM_SHA256", hashlib.sha256(b"dem").hexdigest())
    monkeypatch.setattr(stage_d_gate, "ERR_SHA256", hashlib.sha256(b"err").hexdigest())
    monkeypatch.setattr(stage_d_gate, "SLOPE_20M_SHA256", hashlib.sha256(b"slope").hexdigest())
    monkeypatch.setattr(stage_d_gate, "STUDY_SUMMARY_SHA256", hashlib.sha256(b"{}").hexdigest())


def test_sha256_list_parses_gnu_and_bsd_lines(tmp_path):
    digest = "a" * 64
    path = tmp_path / "list.txt"
    path.write_text(f"{digest} *data/raw/A.TIF\nSHA256 (B.TIF) = {digest.upper()}\n\n")
    assert stage_d_gate.parse_sha256_list(path) == {"A.TIF": digest, "B.TIF": digest}
    path.write_text("not a digest line\n")
    with pytest.raises(ValueError, match="unreadable"):
        stage_d_gate.parse_sha256_list(path)


def test_gate_pins_inputs_and_handles_missing_effres(tmp_path, monkeypatch):
    _pin_digests(monkeypatch)
    paths = _staged_inputs(tmp_path)
    inputs = stage_d_gate.expected_inputs(**paths)
    assert set(inputs) == {"dem", "err", "effres", "ldsm_err", "stage_b_slope", "mc_summary"}
    assert stage_d_gate.verify_input_digests(inputs)["effres"] == hashlib.sha256(b"effres").hexdigest()
    (paths["raw_dir"] / "LDEM_80S_20MPP_ADJ_EFFRES.TIF").unlink()
    with pytest.raises(ValueError, match="effres is missing"):
        stage_d_gate.expected_inputs(**paths)

    (tmp_path / "fallback").mkdir()
    fallback = _staged_inputs(tmp_path / "fallback", effres=False)
    pin = stage_d_gate.receipt_pin(**fallback, mode="pilot")
    assert pin["stage_d_effres_staged"] is False
    (fallback["raw_dir"] / "LDEM_80S_20MPP_ADJ_EFFRES.TIF").write_bytes(b"unlisted")
    with pytest.raises(ValueError, match="not in pgda90"):
        stage_d_gate.expected_inputs(**fallback)


def test_gate_rejects_wrong_digest_and_changed_inputs(tmp_path, monkeypatch):
    _pin_digests(monkeypatch)
    paths = _staged_inputs(tmp_path)
    (paths["raw_dir"] / "LDEM_80S_20MPP_ADJ_ERR.TIF").write_bytes(b"other")
    with pytest.raises(ValueError, match="checksum mismatch for Stage D input err"):
        stage_d_gate.verify_input_digests(stage_d_gate.expected_inputs(**paths))
    (paths["raw_dir"] / "LDEM_80S_20MPP_ADJ_ERR.TIF").write_bytes(b"err")
    receipt = {
        "account": "p201619",
        "stage_d_inputs_verified": True,
        **stage_d_gate.receipt_pin(**paths, mode="production"),
    }
    stage_d_gate.reject_stage_d_receipt(receipt, **paths, mode="production")
    with pytest.raises(ValueError, match="stage_d_mode"):
        stage_d_gate.reject_stage_d_receipt(receipt, **paths, mode="pilot")
    paths["stage_b_slope"].write_bytes(b"slope-longer")
    with pytest.raises(ValueError, match="stage_d_inputs"):
        stage_d_gate.reject_stage_d_receipt(receipt, **paths, mode="production")


def test_stage_d_preflight_hashes_inputs_and_pins_them(tmp_path, monkeypatch):
    from tests import test_hpc_preflight as base

    # Use the preflight module instance the shared fixture patches.
    preflight = base.preflight
    _write_evidence = base._write_evidence
    base._configured_environment(tmp_path, monkeypatch)
    _pin_digests(monkeypatch)
    monkeypatch.setenv("ANUMA_JOB_PROFILE", "stage-d-pilot")
    monkeypatch.setenv("ANUMA_ACCOUNT", "p201619")
    _write_evidence(
        Path(os.environ["ANUMA_ALLOCATION_EVIDENCE_FILE"]),
        {
            "source": "test fixture",
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "account": "p201619",
            "remaining_cpu_node_hours": 10.0,
        },
    )
    paths = _staged_inputs(tmp_path)
    monkeypatch.setenv("ANUMA_STAGE_D_RAW_DIR", str(paths["raw_dir"]))
    monkeypatch.setenv("ANUMA_DEM", str(paths["dem"]))
    monkeypatch.setenv("ANUMA_STAGE_B_SLOPE", str(paths["stage_b_slope"]))
    monkeypatch.setenv("ANUMA_MC_SUMMARY", str(paths["mc_summary"]))
    original = preflight.run_checked

    def fake_command(command):
        if "show assoc" in " ".join(str(item) for item in command):
            return "cluster|p201619|tester|cpu|test\n"
        return original(command)

    monkeypatch.setattr(preflight, "run_checked", fake_command)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    with pytest.raises(RuntimeError, match="inside a compute allocation"):
        preflight.run_preflight(tmp_path / "receipt-stage-d-login.json")
    monkeypatch.setenv("SLURM_JOB_ID", "123456")
    receipt = preflight.run_preflight(tmp_path / "receipt-stage-d.json")
    assert receipt["stage_d_preflight_slurm_job_id"] == "123456"
    assert receipt["stage_d_preflight_host"]
    assert receipt["job_profile"] == "stage-d-pilot"
    assert receipt["stage_d_inputs_verified"] is True
    assert receipt["stage_d_effres_staged"] is True
    assert receipt["job_walltime"] == "00:30:00"
    stage_d_gate.reject_stage_d_receipt(receipt, **paths, mode="pilot")
    (paths["raw_dir"] / "LDSM_80S_20MPP_ADJ_ERR.TIF").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        preflight.run_preflight(tmp_path / "receipt-stage-d-bad.json")


def test_stage_d_profiles_and_scripts_fit_two_node_hours():
    pilot = preflight.JOB_PROFILES["stage-d-pilot"]
    production = preflight.JOB_PROFILES["stage-d"]
    assert pilot["qos"] == "test" and pilot["walltime"] == "00:30:00"
    assert production["qos"] == "default"
    assert production["walltime_minutes"] <= 120
    assert pilot["node_hours"] + production["node_hours"] <= 2.0
    assert pilot["cpus"] == production["cpus"] == 128
    assert stage_d_gate.WALLTIME == {"pilot": pilot["walltime"], "production": production["walltime"]}
    assert stage_d_gate.NODE_HOURS == {"pilot": pilot["node_hours"], "production": production["node_hours"]}
    wrapper = (REPO / "hpc" / "submit-stage-d.sh").read_text()
    assert "reject_stage_d_receipt" in wrapper and "p201619" in wrapper
    assert "sha256sum" not in wrapper and "--gpu" not in wrapper.lower()
    for mode, profile in (("pilot", pilot), ("production", production)):
        script = (REPO / "hpc" / "slurm" / f"stage-d-{mode}.sbatch").read_text()
        assert f'stage_d_mode="{mode}"' in script
        assert f'expected_qos="{profile["qos"]}"' in script
        assert f'expected_time="{profile["walltime"]}"' in script
        assert 'expected_memory_mb="245760"' in script
        assert "reject_stage_d_receipt" in script
        assert "srun --export=ALL" in script
        assert "--workers 128" in script
        assert "'env/staging/2025\\.1'" in script
        assert "--gpu" not in script.lower()
        assert f'walltime="{profile["walltime"]}"' in wrapper


# -- fail-closed inputs and geometry -------------------------------------------


def _site_arrays(err_value: float = 2.0):
    base = _synthetic_site()
    return base.window, base.dem.copy(), np.full(base.dem.shape, err_value)


def _prepare(window, dem, err, ldsm=None):
    return stage_d.prepare_site(
        window,
        dem=dem,
        err=err,
        effres=np.full((window.size, window.size), 60.0),
        ldsm_err=np.full((window.size, window.size), 1.0) if ldsm is None else ldsm,
        fine_transform=FINE,
        nm=_dn()["noise_model"],
        radius_m=1000.0,
        cell_size_m=240.0,
        cell_m=20.0,
        max_slope_deg=8.0,
    )


def test_err_gap_under_finite_dem_fails_closed():
    window, dem, err = _site_arrays()
    assert _prepare(window, dem, err).window_stats["err_nan_under_finite_dem"] == 0
    err[0, 5] = np.nan  # halo row: still under the noise
    with pytest.raises(ValueError, match="ERR is missing at 1 pixels"):
        _prepare(window, dem, err)
    dem[0, 5] = np.nan  # no DEM there either: nothing to perturb
    assert _prepare(window, dem, err).window_stats["err_nan_under_finite_dem"] == 0


def test_draw_refuses_a_change_in_finite_circle_pixels():
    site = _synthetic_site()
    site.window_stats["zero_noise"]["circle_finite_pixels"] -= 1
    with pytest.raises(ValueError, match="finite circle pixels"):
        stage_d.evaluate_draw(site, 0, 0, seed=1, max_slope_deg=8.0, cell_m=20.0)


def test_ldsm_median_uses_positive_finite_values_and_reports_count():
    window, dem, err = _site_arrays()
    ldsm = np.full((window.size, window.size), 1.5)
    ldsm[:10] = 0.0
    ldsm[10:20] = np.nan
    stats = _prepare(window, dem, err, ldsm=ldsm).window_stats
    assert stats["ldsm_err_deg_median"] == 1.5
    assert stats["ldsm_err_valid_pixels"] == (window.size - 20) * window.size


@pytest.mark.parametrize(
    "coarse",
    [
        Affine(200.0, 0.0, -80.0, 0.0, -200.0, 4080.0),  # not 12 fine pixels per cell
        Affine(240.0, 0.0, -70.0, 0.0, -240.0, 4080.0),  # centre off the fine corners
    ],
)
def test_site_window_refuses_unnested_grids(coarse):
    with pytest.raises(ValueError, match="12 fine pixels|not on a 20 m pixel corner"):
        stage_d.site_window(
            site_id="8:8",
            site_index=0,
            default_rank=1,
            row_240=8,
            col_240=8,
            coarse_transform=coarse,
            fine_transform=FINE,
            half_size_px=60,
        )


def test_dem_projection_must_match_grid_proj4(tmp_path):
    from rasterio.crs import CRS as RioCRS

    proj4 = _cfg()["projection"]["proj4"]
    assert stage_d.require_same_projection(RioCRS.from_proj4(proj4), proj4)["max_offset_m"] == 0.0
    rotated = RioCRS.from_proj4(proj4.replace("+lon_0=0", "+lon_0=90"))
    with pytest.raises(ValueError, match="DEM CRS differs"):
        stage_d.require_same_projection(rotated, proj4)
    dn = copy.deepcopy(_dn())
    dn["window_half_size_m"] = 1200.0
    paths = {
        name: _write_tif(tmp_path / f"{name}.tif", array)
        for name, array in (
            ("dem", _terrain()),
            ("err", np.full(SHAPE, 3.0)),
            ("ldsm", np.full(SHAPE, 0.5)),
        )
    }
    rule = {"site_order": ["8:8"], "ranking": [{"id": "8:8", "default_rank": 1, "row": 8, "col": 8}]}
    kwargs = dict(
        dem_path=paths["dem"], err_path=paths["err"], effres_path=None, ldsm_path=paths["ldsm"],
        coarse_transform=COARSE, dn=dn, max_slope_deg=8.0,
    )
    stage_d.prepare_sites_from_rasters(rule, grid_proj4=proj4, **kwargs)
    with pytest.raises(ValueError, match="DEM CRS differs"):
        stage_d.prepare_sites_from_rasters(
            rule, grid_proj4=proj4.replace("+lon_0=0", "+lon_0=90"), **kwargs
        )
