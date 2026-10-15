"""Stage F 20 m terrain gate on synthetic arrays (real DEM only if present)."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from affine import Affine
from rasterio.transform import from_origin

from anuma import stage_d, stage_f, stage_f_gate
from anuma.config import load_config, repo_root
from anuma.slope import horn_slope_deg

REPO = Path(__file__).resolve().parents[1]
# Same nesting as the real grids: 240 m origin 80 m (4 fine pixels) outside.
FINE = from_origin(0.0, 4000.0, 20.0, 20.0)
COARSE = Affine(240.0, 0.0, -80.0, 0.0, -240.0, 4080.0)
NEST = stage_f.nesting_from_transforms(COARSE, FINE, 16, 16)
KERNEL = stage_f.disk_kernel(1000.0)
REAL_DEM = Path("/home/possum/projects/ANUMA_/data/raw/LDEM_80S_20MPP_ADJ.TIF")


def _random_slope(shape=(200, 200), nan_share=0.1, seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    slope = rng.uniform(0.0, 16.0, shape).astype(np.float32)
    slope[rng.random(shape) < nan_share] = np.nan
    return slope


def _terrain(shape=(200, 200)) -> np.ndarray:
    rows, cols = np.mgrid[0 : shape[0], 0 : shape[1]].astype(np.float64)
    x, y = cols * 20.0, rows * 20.0
    return 1000.0 + 120.0 * np.sin(x / 300.0) * np.cos(y / 400.0) + 0.03 * x


def _stage_d_counts(slope: np.ndarray, row: int, col: int) -> tuple[int, int]:
    """Stage D's own window, circle and statistic for one 240 m cell."""
    window = stage_d.site_window(
        site_id=f"{row}:{col}", site_index=0, default_rank=0, row_240=row, col_240=col,
        coarse_transform=COARSE, fine_transform=FINE, half_size_px=51,
    )
    block = stage_f.ArraySlope(slope).read(
        window.row0, window.row0 + window.size, window.col0, window.col0 + window.size
    )
    circle, cell = stage_d.window_masks(window, FINE, radius_m=1000.0, cell_size_m=240.0)
    stats = stage_d.slope_statistics(block, circle, cell, max_slope_deg=8.0, cell_m=20.0)
    finite = stats["circle_finite_pixels"]
    passing = int(round(stats["fraction_1km"] * finite)) if finite else 0
    return finite, passing


# -- geometry ------------------------------------------------------------------


def test_disk_has_7860_pixels_and_contiguous_chords():
    assert KERNEL.pixels == stage_f.CIRCLE_PIXELS == 7860
    assert KERNEL.half == 50
    assert len(KERNEL.chords) == 100
    assert sum(hi - lo + 1 for _, lo, hi in KERNEL.chords) == 7860
    # Symmetric about the corner: offsets a and -1-a mirror each other.
    assert np.array_equal(KERNEL.mask, KERNEL.mask[::-1, :])
    assert np.array_equal(KERNEL.mask, KERNEL.mask[:, ::-1])


def test_disk_equals_stage_d_circle_at_a_corner_centre():
    window = stage_d.site_window(
        site_id="8:8", site_index=0, default_rank=0, row_240=8, col_240=8,
        coarse_transform=COARSE, fine_transform=FINE, half_size_px=50,
    )
    circle, _ = stage_d.window_masks(window, FINE, radius_m=1000.0, cell_size_m=240.0)
    # stage_d's window starts 50 px before the corner pixel: same 100 x 100 frame.
    assert circle.shape == (101, 101)
    assert np.array_equal(circle[:100, :100], KERNEL.mask)
    assert not circle[100].any() and not circle[:, 100].any()


def test_nesting_matches_real_offset_and_refuses_others():
    assert (NEST.off_row, NEST.off_col) == (-4, -4)
    # 240 m centre of cell (r, c) is fine corner 12 r + 2.
    assert NEST.corner_rows(np.array([0, 1, 1318]))[2] == 12 * 1318 + 2
    with pytest.raises(ValueError, match="not an integer"):
        stage_f.nesting_from_transforms(Affine(240.0, 0.0, -70.0, 0.0, -240.0, 4080.0), FINE, 4, 4)
    with pytest.raises(ValueError, match="12 fine pixels"):
        stage_f.nesting_from_transforms(Affine(200.0, 0.0, -80.0, 0.0, -200.0, 4080.0), FINE, 4, 4)
    with pytest.raises(ValueError, match="20 m"):
        stage_f.nesting_from_transforms(COARSE, from_origin(0.0, 4000.0, 10.0, 10.0), 4, 4)


# -- exact counts ----------------------------------------------------------------


def test_disk_counts_equal_brute_force_everywhere():
    slope = _random_slope()
    finite, passing = stage_f.compute_window(
        stage_f.ArraySlope(slope), NEST, KERNEL, rows=(0, 16), cols=(0, 16), max_slope_deg=8.0
    )
    assert finite.dtype == passing.dtype == np.int32
    for row in range(16):
        for col in range(16):
            ref = stage_f.brute_force_counts(
                stage_f._padded_slice(slope, -200, 400, -200, 400),
                origin_row=-200, origin_col=-200,
                corner_row=int(NEST.corner_rows(np.array([row]))[0]),
                corner_col=int(NEST.corner_cols(np.array([col]))[0]),
                kernel=KERNEL, max_slope_deg=8.0,
            )
            assert (int(finite[row, col]), int(passing[row, col])) == ref


def test_centre_convention_identical_to_stage_d_on_a_synthetic_dem():
    """Horn slope of a synthetic DEM, then Stage D's window path per cell."""
    dem = _terrain()
    slope = horn_slope_deg(dem, 20.0).astype(np.float32)
    finite, passing = stage_f.compute_window(
        stage_f.ArraySlope(slope), NEST, KERNEL, rows=(0, 16), cols=(0, 16), max_slope_deg=8.0
    )
    assert 0 < int(passing.sum()) < int(finite.sum())
    for row in range(16):
        for col in range(16):
            assert (int(finite[row, col]), int(passing[row, col])) == _stage_d_counts(slope, row, col)


def test_stage_d_prepare_site_fraction_equals_stage_f():
    dem = _terrain()
    window = stage_d.site_window(
        site_id="8:8", site_index=0, default_rank=1, row_240=8, col_240=8,
        coarse_transform=COARSE, fine_transform=FINE, half_size_px=60,
    )
    r0, c0, size = window.row0, window.col0, window.size
    halo = dem[r0 - 1 : r0 + size + 1, c0 - 1 : c0 + size + 1]
    site = stage_d.prepare_site(
        window, dem=halo, err=np.full(halo.shape, 1.0), effres=None, ldsm_err=None,
        fine_transform=FINE, nm=stage_d.require_frozen_config(load_config())["noise_model"],
        radius_m=1000.0, cell_size_m=240.0, cell_m=20.0, max_slope_deg=8.0,
    )
    slope = horn_slope_deg(dem, 20.0).astype(np.float32)
    finite, passing = stage_f.compute_window(
        stage_f.ArraySlope(slope), NEST, KERNEL, rows=(8, 9), cols=(8, 9), max_slope_deg=8.0
    )
    zero = site.window_stats["zero_noise"]
    assert int(finite[0, 0]) == zero["circle_finite_pixels"] == 7860
    assert float(stage_f.fraction_from_counts(finite, passing)[0, 0]) == pytest.approx(
        zero["fraction_1km"], abs=1e-7
    )


def test_nan_handling_denominator_edges_and_empty_circles():
    slope = np.full((200, 200), 3.0, dtype=np.float32)
    slope[:, 100:] = np.nan  # right half missing
    slope[0:60, 0:60] = 20.0  # steep corner
    finite, passing = stage_f.compute_window(
        stage_f.ArraySlope(slope), NEST, KERNEL, rows=(0, 16), cols=(0, 16), max_slope_deg=8.0
    )
    fraction = stage_f.fraction_from_counts(finite, passing)
    # Fully inside the finite half and away from the steep corner: all pass.
    assert finite[10, 4] == 7860 and passing[10, 4] == 7860 and fraction[10, 4] == 1.0
    # Circle straddling the NaN edge: denominator is the finite part only.
    assert 0 < finite[8, 8] < 7860 and passing[8, 8] == finite[8, 8]
    # Far inside the NaN half: no finite pixel, so NaN, never 0.
    assert finite[8, 14] == 0 and np.isnan(fraction[8, 14])
    # Off-raster pixels (top-left cell) count as missing, not as failing.
    assert finite[0, 0] < 7860
    for row, col in ((0, 0), (8, 8), (10, 4), (8, 14), (2, 2), (12, 3)):
        assert (int(finite[row, col]), int(passing[row, col])) == _stage_d_counts(slope, row, col)


def test_slope_exactly_at_the_limit_passes_and_half_is_exactly_half():
    slope = np.full((200, 200), 8.0, dtype=np.float32)
    finite, passing = stage_f.compute_window(
        stage_f.ArraySlope(slope), NEST, KERNEL, rows=(8, 9), cols=(8, 9), max_slope_deg=8.0
    )
    assert passing[0, 0] == finite[0, 0] == 7860
    half = stage_f.fraction_from_counts(np.array([7860]), np.array([3930]))
    below = stage_f.fraction_from_counts(np.array([7860]), np.array([3929]))
    assert half[0] == np.float32(0.5) and below[0] < np.float32(0.5)


def test_block_must_cover_every_disk():
    mask = np.ones((50, 50), dtype=bool)
    with pytest.raises(ValueError, match="cover"):
        stage_f.disk_counts(
            mask, mask, origin_row=0, origin_col=0, corner_rows=np.array([25]),
            corner_cols=np.array([25]), kernel=KERNEL,
        )


def test_serial_and_parallel_bands_are_bit_identical():
    slope = _random_slope((260, 260), seed=3)
    nest = stage_f.nesting_from_transforms(COARSE, FINE, 21, 21)
    bands = stage_f.plan_bands(21, band_rows=4)
    assert bands[-1] == (20, 21)
    source = stage_f.ArraySlope(slope)
    serial = stage_f.compute_bands(source, nest, KERNEL, bands, max_slope_deg=8.0, workers=1)
    parallel = stage_f.compute_bands(
        source, nest, KERNEL, list(reversed(bands)), max_slope_deg=8.0, workers=3
    )
    assert stage_f.results_identical(serial, parallel)
    finite, passing = stage_f.assemble(parallel, 21, 21)
    whole = stage_f.compute_window(source, nest, KERNEL, rows=(0, 21), cols=(0, 21), max_slope_deg=8.0)
    assert np.array_equal(finite, whole[0]) and np.array_equal(passing, whole[1])
    assert finite.dtype == np.int16
    with pytest.raises(ValueError, match="cover"):
        stage_f.assemble({k: v for k, v in parallel.items() if k != (0, 4)}, 21, 21)
    with pytest.raises(ValueError, match="workers"):
        stage_f.compute_bands(source, nest, KERNEL, bands, max_slope_deg=8.0, workers=129)


def test_brute_force_check_uses_stage_d_circles():
    slope = _random_slope(seed=5)
    source = stage_f.ArraySlope(slope)
    finite, passing = stage_f.compute_window(source, NEST, KERNEL, rows=(0, 16), cols=(0, 16), max_slope_deg=8.0)
    cells = stage_f.sample_cells([(0, 16)], 16, 40, seed=1)
    ok = stage_f.brute_force_check(
        source, NEST, KERNEL, finite.astype(np.int16), passing.astype(np.int16), cells=cells,
        max_slope_deg=8.0, fine_transform=FINE, coarse_transform=COARSE,
    )
    assert ok["all_equal"] and ok["samples"] == 40
    tampered = passing.copy()
    tampered[cells[0]] += 1
    bad = stage_f.brute_force_check(
        source, NEST, KERNEL, finite, tampered, cells=cells[:1], max_slope_deg=8.0,
        fine_transform=FINE, coarse_transform=COARSE,
    )
    assert not bad["all_equal"]


# -- frozen values and acceptance ------------------------------------------------


def test_frozen_config_refuses_drift():
    cfg = load_config()
    spec = stage_f.require_frozen_config(cfg)
    assert spec["max_slope_deg"] == 8.0 and spec["min_fraction"] == 0.5
    cfg["hard"]["terrain_1km"]["radius_m"] = 900.0
    with pytest.raises(ValueError, match="radius_m"):
        stage_f.require_frozen_config(cfg)
    cfg = load_config()
    cfg["hard"]["max_slope_deg"] = 10.0
    with pytest.raises(ValueError, match="max_slope_deg"):
        stage_f.require_frozen_config(cfg)


def test_frozen_stage_d_counts_match_committed_noise_file():
    table = {s["site_id"]: s for s in stage_f.load_stage_d_table()}
    assert set(table) == set(stage_f.STAGE_D_ZERO_NOISE_COUNTS)
    for sid, count in stage_f.STAGE_D_ZERO_NOISE_COUNTS.items():
        assert abs(count / 7860 - table[sid]["zero_noise_within_1km"]) <= 5e-5


def test_frozen_stage_d_counts_match_full_precision_summary_when_present():
    path = Path("/home/possum/projects/ANUMA_/out/hpc/stage-d-production-5345050/stage-d-summary.json")
    if not path.is_file():
        pytest.skip("Stage D summary is not on this machine")
    summary = json.loads(path.read_text())
    for sid, count in stage_f.STAGE_D_ZERO_NOISE_COUNTS.items():
        zero = summary["sites"][sid]["window_stats"]["zero_noise"]
        assert zero["circle_finite_pixels"] == 7860
        assert count / 7860 == zero["fraction_1km"]


def test_acceptance_passes_on_frozen_counts_and_fails_on_drift():
    stage_b = {(s["row"], s["col"]): s["within_1km"] for s in stage_f.load_stage_b_table()}
    stage_d_cells = {(s["row"], s["col"]): s["site_id"] for s in stage_f.load_stage_d_table()}

    def lookup(row, col):
        sid = stage_d_cells.get((row, col))
        if sid is not None:
            return 7860, stage_f.STAGE_D_ZERO_NOISE_COUNTS[sid]
        return 7860, int(round(stage_b[(row, col)] * 7860))

    checks = stage_f.acceptance_checks(lookup)
    assert checks["stage_b_reproduced"] and checks["stage_d_reproduced"]
    assert len(checks["stage_b_ranks_1_10"]) == 10 and len(checks["stage_d_zero_noise"]) == 8

    def off_by_one(row, col):
        finite, passing = lookup(row, col)
        return finite, passing + (1 if (row, col) == (791, 1624) else 0)

    drifted = stage_f.acceptance_checks(off_by_one)
    assert not drifted["stage_d_reproduced"]
    assert drifted["stage_b_reproduced"]  # one pixel is below two decimals


def test_pilot_bands_hold_every_acceptance_site():
    bands = stage_f.pilot_bands(2534)
    assert len(bands) == 8 and len(stage_f.plan_bands(2534)) == 80
    for row, _ in stage_f.acceptance_sites():
        assert any(r0 <= row < r1 for r0, r1 in bands)


def test_projection_is_conservative_and_flags_overruns():
    fits = stage_f.project_production(
        fixed_seconds=60.0, check_seconds=10.0, serial_band_seconds=8.0, pilot_band_count=8,
        production_band_count=80, workers=128, walltime_hours=1.0, node_hours=1.0,
    )
    assert fits["fits_walltime"] and fits["projected_parallel_seconds"] >= 1.0
    slow = stage_f.project_production(
        fixed_seconds=60.0, check_seconds=10.0, serial_band_seconds=8.0 * 3600, pilot_band_count=8,
        production_band_count=80, workers=128, walltime_hours=1.0, node_hours=1.0,
    )
    assert not slow["fits_walltime"]


def test_sidecar_validation():
    from anuma.grid import Grid

    cfg = load_config()
    grid = Grid(cfg)
    spec = stage_f.require_frozen_config(cfg)
    pinned = {
        "dem": {"sha256": stage_f_gate.DEM_SHA256},
        "stage_b_slope": {"sha256": stage_f_gate.SLOPE_20M_SHA256},
    }
    meta = stage_f.sidecar(spec=spec, grid=grid, coverage="full_cap", inputs=pinned, stats={}, job="1")
    meta["fraction_sha256"] = "a" * 64
    assert stage_f.validate_sidecar(meta, grid, cfg) is None
    assert "job id" in stage_f.validate_sidecar({**meta, "job": None}, grid, cfg)
    unpinned = {**pinned, "stage_b_slope": {"sha256": "496f554f" + "0" * 56}}
    assert "pinned Stage B" in stage_f.validate_sidecar({**meta, "inputs": unpinned}, grid, cfg)
    assert "fraction_sha256" in stage_f.validate_sidecar({**meta, "fraction_sha256": None}, grid, cfg)
    assert "full cap" in stage_f.validate_sidecar({**meta, "coverage": "window"}, grid, cfg)
    assert "grid" in stage_f.validate_sidecar({**meta, "grid_sha256": "0" * 64}, grid, cfg)
    assert "radius" in stage_f.validate_sidecar({**meta, "radius_m": 500.0}, grid, cfg)


@pytest.mark.skipif(not REAL_DEM.is_file(), reason="20 m DEM is not on this machine")
def test_local_check_reproduces_stage_b_and_stage_d_from_the_real_dem():
    checks = stage_f.run_local_check(dem=REAL_DEM)
    assert checks["stage_b_reproduced"], checks["stage_b_ranks_1_10"]
    assert checks["stage_d_reproduced"], checks["stage_d_zero_noise"]
    assert checks["stage_d_path"]["all_equal"]
    assert checks["passed"]


# -- gate, preflight and scripts ---------------------------------------------------


def _staged(tmp_path: Path, monkeypatch) -> dict:
    dem = tmp_path / "LDEM_80S_20MPP_ADJ.TIF"
    dem.write_bytes(b"dem")
    slope = tmp_path / "slope-20m.npy"
    slope.write_bytes(b"slope")
    monkeypatch.setattr(stage_f_gate, "DEM_SHA256", hashlib.sha256(b"dem").hexdigest())
    monkeypatch.setattr(stage_f_gate, "SLOPE_20M_SHA256", hashlib.sha256(b"slope").hexdigest())
    return {"dem": dem, "stage_b_slope": slope}


def test_gate_digests_are_the_stage_b_pins():
    assert stage_f_gate.DEM_SHA256.startswith("09b7ca80f9e6a146")
    assert stage_f_gate.SLOPE_20M_SHA256 == (
        "08b2e17b98f6c639068493e1f4007194d5cc41ad2d78cb850e593aef9542bd3a"
    )


def test_gate_rejects_wrong_digest_and_changed_inputs(tmp_path, monkeypatch):
    paths = _staged(tmp_path, monkeypatch)
    assert set(stage_f_gate.verify_input_digests(stage_f_gate.expected_inputs(**paths))) == {
        "dem", "stage_b_slope",
    }
    paths["dem"].write_bytes(b"other")
    with pytest.raises(ValueError, match="checksum mismatch for Stage F input dem"):
        stage_f_gate.verify_input_digests(stage_f_gate.expected_inputs(**paths))
    paths["dem"].write_bytes(b"dem")
    receipt = {
        "account": "p201619",
        "stage_f_inputs_verified": True,
        **stage_f_gate.receipt_pin(**paths, mode="production"),
    }
    stage_f_gate.reject_stage_f_receipt(receipt, **paths, mode="production")
    with pytest.raises(ValueError, match="stage_f_mode"):
        stage_f_gate.reject_stage_f_receipt(receipt, **paths, mode="pilot")
    paths["stage_b_slope"].write_bytes(b"slope-longer")
    with pytest.raises(ValueError, match="stage_f_inputs"):
        stage_f_gate.reject_stage_f_receipt(receipt, **paths, mode="production")
    with pytest.raises(ValueError, match="missing"):
        stage_f_gate.expected_inputs(dem=tmp_path / "nope", stage_b_slope=paths["stage_b_slope"])


def test_stage_f_preflight_hashes_inputs_without_myquota(tmp_path, monkeypatch):
    from tests import test_hpc_preflight as base

    preflight = base.preflight
    base._configured_environment(tmp_path, monkeypatch)
    paths = _staged(tmp_path, monkeypatch)
    monkeypatch.setenv("ANUMA_JOB_PROFILE", "stage-f-pilot")
    monkeypatch.setenv("ANUMA_ACCOUNT", "p201619")
    base._write_evidence(
        Path(os.environ["ANUMA_ALLOCATION_EVIDENCE_FILE"]),
        {
            "source": "test fixture",
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "account": "p201619",
            "remaining_cpu_node_hours": 10.0,
        },
    )
    monkeypatch.setenv("ANUMA_DEM", str(paths["dem"]))
    monkeypatch.setenv("ANUMA_STAGE_B_SLOPE", str(paths["stage_b_slope"]))
    original = preflight.run_checked

    def fake_command(command):
        if "show assoc" in " ".join(str(item) for item in command):
            return "cluster|p201619|tester|cpu|test\n"
        return original(command)

    monkeypatch.setattr(preflight, "run_checked", fake_command)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    with pytest.raises(RuntimeError, match="inside a compute allocation"):
        preflight.run_preflight(tmp_path / "receipt-login.json")
    monkeypatch.setenv("SLURM_JOB_ID", "654321")
    monkeypatch.setattr(
        preflight.shutil, "which", lambda command: None if command == "myquota" else f"/bin/{command}"
    )
    receipt = preflight.run_preflight(tmp_path / "receipt-stage-f.json")
    assert receipt["job_profile"] == "stage-f-pilot"
    assert receipt["stage_f_inputs_verified"] is True
    assert receipt["stage_f_preflight_slurm_job_id"] == "654321"
    assert receipt["job_walltime"] == "00:30:00"
    stage_f_gate.reject_stage_f_receipt(receipt, **paths, mode="pilot")
    paths["stage_b_slope"].write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        preflight.run_preflight(tmp_path / "receipt-stage-f-bad.json")


def test_stage_f_profiles_and_scripts_fit_the_cap():
    from tests import test_hpc_preflight as base

    preflight = base.preflight
    pilot = preflight.JOB_PROFILES["stage-f-pilot"]
    production = preflight.JOB_PROFILES["stage-f"]
    assert pilot["qos"] == "test" and pilot["walltime"] == "00:30:00"
    assert production["qos"] == "default"
    assert pilot["node_hours"] + production["node_hours"] <= 2.0
    assert pilot["cpus"] == production["cpus"] == 128
    assert stage_f_gate.WALLTIME == {"pilot": pilot["walltime"], "production": production["walltime"]}
    assert stage_f_gate.NODE_HOURS == {"pilot": pilot["node_hours"], "production": production["node_hours"]}
    wrapper = (REPO / "hpc" / "submit-stage-f.sh").read_text()
    assert "reject_stage_f_receipt" in wrapper and "p201619" in wrapper
    assert "sha256sum" not in wrapper and "--gpu" not in wrapper.lower()
    assert "stage_d" not in wrapper
    for mode, profile in (("pilot", pilot), ("production", production)):
        script = (REPO / "hpc" / "slurm" / f"stage-f-{mode}.sbatch").read_text()
        assert f'stage_f_mode="{mode}"' in script
        assert f'expected_qos="{profile["qos"]}"' in script
        assert f'expected_time="{profile["walltime"]}"' in script
        assert 'expected_memory_mb="245760"' in script
        assert "reject_stage_f_receipt" in script
        assert "-m anuma.stage_f" in script and "--workers 128" in script
        assert "module load env/release/2024.1" in script
        assert "Python/3.12.3-GCCcore-13.3.0" in script
        assert "myquota_unavailable_on_compute_node" in script
        assert "--gpu" not in script.lower()
        assert f'walltime="{profile["walltime"]}"' in wrapper
    assert repo_root() == REPO
