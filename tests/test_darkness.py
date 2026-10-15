"""Stage G longest dark/lit runs: kernel, censoring, stitching, engine, job."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

import numpy as np
import pytest

from anuma import daily_lit as dl
from anuma import darkness as dk

DISK = 0.27


def _random_lit(rng, hours, pixels, persistence=0.85):
    """Lit sequences with runs of many lengths, plus fixed columns."""
    lit = rng.random((hours, pixels)) < rng.uniform(0.05, 0.95, size=pixels)
    for t in range(1, hours):
        keep = rng.random(pixels) < persistence
        lit[t] = np.where(keep, lit[t - 1], lit[t])
    lit[:, 0] = False  # never lit
    lit[:, 1] = True  # never dark
    lit[:, 2] = False
    lit[hours // 2, 2] = True  # one lit hour mid-period
    lit[:, 3] = True
    lit[0, 3] = lit[-1, 3] = False  # dark only at both ends
    return lit


def _assert_same(got: dict, expected: dict):
    for key, values in expected.items():
        assert np.array_equal(
            np.asarray(got[key]).astype(np.int64), np.asarray(values).astype(np.int64)
        ), key


# ---------------------------------------------------------------- kernel


@pytest.mark.parametrize("seed", range(12))
def test_streaming_kernel_equals_brute_force_and_stitching_is_exact(seed):
    rng = np.random.default_rng(seed)
    month_hours = [int(d) * 24 for d in rng.integers(1, 5, size=rng.integers(1, 7))]
    hours = sum(month_hours)
    lit = _random_lit(rng, hours, 48, persistence=rng.uniform(0.5, 0.97))
    period, months = dk.runs_from_lit(lit, month_hours)
    _assert_same(period, dk.brute_force_runs(lit))
    edges = np.cumsum([0, *month_hours])
    for a in range(len(month_hours)):
        for b in range(a + 1, len(month_hours) + 1):
            total, lead, trail, best = dk.stitch(
                (month_hours[k], *months[k]) for k in range(a, b)
            )
            window = lit[edges[a] : edges[b]]
            assert total == edges[b] - edges[a]
            assert np.array_equal(best, dk.brute_force_longest_dark(window)), (a, b)
            # lead/trail are the dark runs touching the window edges.
            first_lit = np.where(window.any(axis=0), window.argmax(axis=0), len(window))
            last_lit = np.where(
                window.any(axis=0), len(window) - 1 - window[::-1].argmax(axis=0), -1
            )
            assert np.array_equal(lead, first_lit)
            assert np.array_equal(trail, len(window) - 1 - last_lit)


def test_combine_is_associative_on_random_blocks():
    rng = np.random.default_rng(5)
    lit = _random_lit(rng, 24 * 9, 30)
    period, months = dk.runs_from_lit(lit, [72, 72, 72])
    blocks = [(72, *m) for m in months]
    left = dk.combine(dk.combine(*[dk.stitch([b]) for b in blocks[:2]]), dk.stitch([blocks[2]]))
    right = dk.combine(dk.stitch([blocks[0]]), dk.combine(*[dk.stitch([b]) for b in blocks[1:]]))
    for x, y in zip(left, right):
        assert np.array_equal(x, y)
    assert np.array_equal(left[3], period["dark_hours"])


def test_censoring_flags_ties_and_degenerate_pixels():
    # 10 hours; columns: never lit, never dark, dark at start, dark at end,
    # interior longest, tie (earliest wins), dark at both ends with tie.
    L, D = True, False
    columns = [
        [D] * 10,
        [L] * 10,
        [D, D, D, D, L, L, D, L, L, L],
        [L, L, D, L, L, L, D, D, D, D],
        [L, D, L, D, D, D, L, D, D, L],
        [L, D, D, L, L, D, D, L, L, L],
        [D, D, L, L, L, L, L, L, D, D],
    ]
    lit = np.array(columns, dtype=bool).T
    period, months = dk.runs_from_lit(lit, [10])
    _assert_same(period, dk.brute_force_runs(lit))
    assert period["dark_hours"].tolist() == [10, 0, 4, 4, 3, 2, 2]
    assert period["dark_start"].tolist() == [0, dk.NO_RUN, 0, 6, 3, 1, 0]
    s, e = dk.CENSORED_AT_START, dk.CENSORED_AT_END
    assert period["dark_flags"].tolist() == [s | e, 0, s, e, 0, 0, s]
    assert period["dark_interior"].tolist() == [0, 0, 1, 1, 3, 2, 0]
    assert period["lit_hours"].tolist() == [0, 10, 3, 3, 1, 3, 6]
    assert period["lit_flags"].tolist() == [0, s | e, e, 0, s, e, 0]
    assert period["lit_start"][0] == dk.NO_RUN
    assert period["lit_total"].tolist() == [0, 10, 5, 5, 4, 6, 6]
    lead, trail, best = months[0]
    assert lead.tolist() == [10, 0, 4, 0, 0, 0, 2]
    assert trail.tolist() == [10, 0, 0, 4, 0, 0, 2]
    assert best.tolist() == [10, 0, 4, 4, 3, 2, 2]


def test_month_triples_fit_uint16_and_refuse_overlong_months():
    lit = np.zeros((744, 2), dtype=bool)
    lit[:, 1] = True
    _, months = dk.runs_from_lit(lit, [744])
    assert months.dtype == np.uint16
    assert months[0, :, 0].tolist() == [744, 744, 744]
    assert months[0, :, 1].tolist() == [0, 0, 0]
    state = dk.new_run_state(1)
    with pytest.raises(ValueError, match="uint16"):
        dk.end_month(state, dk.begin_month(1), 0, 70000)


def test_month_and_year_tables_follow_the_calendar():
    months = dk.month_table(date(2027, 11, 1), date(2028, 3, 1))
    assert [m["label"] for m in months] == ["2027-11", "2027-12", "2028-01", "2028-02"]
    assert [m["hours"] for m in months] == [720, 744, 744, 696]
    assert [m["first_hour"] for m in months] == [0, 720, 1464, 2208]
    years = dk.year_table(months)
    assert years == [
        {"label": "2027", "first_month": 0, "month_count": 2, "hours": 1464},
        {"label": "2028", "first_month": 2, "month_count": 2, "hours": 1440},
    ]
    full = dk.month_table(date(2026, 1, 1), date(2044, 9, 1))
    assert len(full) == 224 and sum(m["hours"] for m in full) == 163_632
    assert len(dk.year_table(full)) == 19
    assert dk.parse_month("2031-03") == date(2031, 3, 1)
    with pytest.raises(ValueError, match="1st"):
        dk.parse_month("2031-03-02")


def test_config_caps_and_pilot_months():
    cfg = dk.load_darkness_config()
    daily = dl.load_daily_lit_config()
    assert dk.period_days(cfg, daily, "production") == (date(2026, 1, 1), date(2044, 9, 1))
    assert dk.period_days(cfg, daily, "pilot") == (date(2026, 1, 1), date(2026, 4, 1))
    exe = cfg["execution"]
    assert exe["production_walltime"] == "03:00:00"
    assert exe["production_node_hours"] <= 3.0
    assert exe["projection_limit_node_hours"] <= exe["production_node_hours"]
    assert exe["pilot_node_hours"] + exe["production_node_hours"] <= exe["stage_node_hour_cap"]
    assert cfg["acceptance"]["tune_after_run"] is False
    # Stage E ran with exactly this daily-lit config (job 5342908 run key).
    digest = hashlib.sha256(dl.config_path().read_bytes()).hexdigest()
    assert digest == "29c27f85ca074a0181b22db7227e8c05baadb863487d5dadfce62453e4c16539"


# ---------------------------------------------------------------- engine


def _south_pole_vector(elevation_deg: float) -> np.ndarray:
    z_component = -np.sin(np.deg2rad(elevation_deg))
    x_component = np.sqrt(max(0.0, 1.0 - z_component * z_component))
    return np.array([x_component, 0.0, z_component], dtype=np.float64)


def _slow_vectors(hours: np.ndarray) -> np.ndarray:
    """Multi-day dark and lit spells that cross day and month boundaries."""
    elevation = 2.5 * np.sin(2 * np.pi * hours / (24 * 11.3)) + 0.8 * np.sin(
        2 * np.pi * hours / 24.0
    )
    azimuth = 2 * np.pi * hours / (24 * 27.3)
    vectors = np.stack([_south_pole_vector(value) for value in elevation])
    rotated = vectors.copy()
    rotated[:, 0] = vectors[:, 0] * np.cos(azimuth)
    rotated[:, 1] = vectors[:, 0] * np.sin(azimuth)
    return rotated


def _fixture(rows=9, cols=5, azimuths=360, seed=4):
    rng = np.random.default_rng(seed)
    horizons = (rng.normal(size=(azimuths, rows, cols)) * 0.7).astype(np.float32)
    longitude = rng.uniform(-180.0, 180.0, size=(rows, cols))
    latitude = rng.uniform(-90.0, -80.0, size=(rows, cols))
    horizons[:, 1, 1] = 89.0  # never lit
    horizons[:, 1, 2] = -89.0  # never dark
    return horizons, longitude, latitude


def test_engine_parallel_equals_serial_and_matches_lit_rule_and_stage_e():
    horizons, lon, lat = _fixture()
    months = dk.month_table(date(2026, 1, 1), date(2026, 3, 1))
    vectors = _slow_vectors(np.arange(sum(m["hours"] for m in months)))
    results = {}
    for workers in (1, 3):
        engine = dk.DarknessEngine(
            horizons, lon, lat, workers=workers, disk_radius_deg=DISK, azimuth_origin_deg=0.0
        )
        try:
            triples, dailies = [], []
            for month in months:
                lo = month["first_hour"]
                triple, daily = engine.advance_month(vectors[lo : lo + month["hours"]])
                triples.append(triple)
                dailies.append(daily)
            results[workers] = (np.stack(triples), np.concatenate(dailies), engine.finish())
        finally:
            engine.close()
    serial, parallel = results[1], results[3]
    assert np.array_equal(serial[0], parallel[0])
    assert np.array_equal(serial[1], parallel[1])
    _assert_same(parallel[2], serial[2])
    lit = dk.lit_matrix(horizons, lon, lat, vectors, disk_radius_deg=DISK, azimuth_origin_deg=0.0)
    period, month_triples = dk.runs_from_lit(lit, [m["hours"] for m in months])
    _assert_same({k: v.reshape(-1) for k, v in parallel[2].items()}, period)
    assert np.array_equal(parallel[0].reshape(len(months), 3, -1), month_triples)
    expected_daily = dl.daily_lit_hours(
        horizons, lon, lat, vectors, disk_radius_deg=DISK, azimuth_origin_deg=0.0
    )
    assert np.array_equal(parallel[1], expected_daily)
    flat = {k: v.reshape(-1) for k, v in parallel[2].items()}
    index = 1 * lon.shape[1] + 1
    assert flat["lit_total"][index] == 0 and flat["dark_hours"][index] == len(vectors)
    index = 1 * lon.shape[1] + 2
    assert flat["dark_hours"][index] == 0 and flat["lit_hours"][index] == len(vectors)
    assert 0 < flat["dark_interior"].max() < len(vectors)


# ---------------------------------------------------------------- job


class _FakeGrid:
    def __init__(self, lon, lat):
        self._lon, self._lat = lon, lat
        self.height, self.width = lon.shape

    def lonlat_arrays(self):
        return self._lon, self._lat


def _stage_a_dir(root, horizons, percent):
    stage_a = root / "stage-a"
    stage_a.mkdir()
    horizon_path = stage_a / "terrain-horizon.npy"
    np.save(horizon_path, horizons)
    np.save(stage_a / "percent-time-lit.npy", percent)
    (stage_a / "validation-report.json").write_text(
        json.dumps({"status": "passed", "metrics": {"rmse": 0.0198, "stop_rmse": 0.05, "passed": True}})
    )
    digest = hashlib.sha256(horizon_path.read_bytes()).hexdigest()
    (stage_a / "output-manifest.json").write_text(
        json.dumps(
            {"outputs": {"terrain-horizon.npy": {"sha256": digest, "bytes": horizon_path.stat().st_size}}}
        )
    )
    return stage_a


def _hours_of(epochs):
    base = datetime(2026, 1, 1)
    return np.array(
        [(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ") - base).total_seconds() / 3600 for text in epochs]
    )


def _patch_runtime(monkeypatch, tmp_path, lon, lat):
    import anuma.grid
    import anuma.hpc_smoke
    import anuma.sun_validation

    grid_file = tmp_path / "grid.json"
    grid_file.write_text("{}")
    monkeypatch.setattr(
        anuma.hpc_smoke, "validate_input_manifest", lambda path: ({}, {"grid": grid_file})
    )
    monkeypatch.setattr(anuma.grid.Grid, "from_json", classmethod(lambda cls, path: _FakeGrid(lon, lat)))
    monkeypatch.setattr(anuma.sun_validation, "_require_lon0_zero", lambda grid: None)
    monkeypatch.setattr(dl, "require_spice_coverage", lambda *args: {"covers_window": True, "window": list(map(str, args[2:]))})

    def vectors(epochs, *args, **kwargs):
        return _slow_vectors(_hours_of(epochs))

    for module in (dl, dk):
        monkeypatch.setattr(module, "sun_vectors_from_spice", vectors)
        monkeypatch.setattr(module, "verify_kernel_manifest", lambda *args: [])


@pytest.fixture
def stage_e(tmp_path, monkeypatch):
    """A passed short Stage E production run (2026-01 to 2027-03) on a tiny grid."""
    horizons, lon, lat = _fixture(rows=8, cols=5)
    horizons[:, 0, 0] = np.nan  # out of cap
    lon[0, 0] = lat[0, 0] = np.nan
    _patch_runtime(monkeypatch, tmp_path, lon, lat)
    manifest = tmp_path / "input-manifest.json"
    manifest.write_text("{}")
    kernel = tmp_path / "kernel-manifest.json"
    kernel.write_text('{"kernels": []}')
    daily_cfg = tmp_path / "daily-lit.yaml"
    raw = dl.config_path().read_text()
    raw = raw.replace('end_utc: "2044-09-01T00:00:00Z"', 'end_utc: "2027-03-01T00:00:00Z"')
    raw = raw.replace('minimum_span_years: "18.6"', 'minimum_span_years: "1.0"')
    daily_cfg.write_text(raw)
    # First run gives the mean; Stage A's percent is set to it so the second,
    # pinned run passes its Stage A comparison.
    percent = np.full(lon.shape, 50.0, dtype=np.float32)
    percent[0, 0] = np.nan
    stage_a = _stage_a_dir(tmp_path, horizons, percent)
    args = dict(
        mode="production",
        input_manifest=manifest,
        kernel_manifest=kernel,
        stage_a_dir=stage_a,
        workers=2,
        daily_lit_config=daily_cfg,
    )
    dl.run_daily_lit(output_dir=tmp_path / "e-draft", **args)
    mean = np.load(tmp_path / "e-draft" / "mean-lit-fraction.npy")
    np.save(stage_a / "percent-time-lit.npy", (mean * 100).astype(np.float32))
    report = dl.run_daily_lit(output_dir=tmp_path / "daily-lit-production-1", **args)
    assert report["passed"] is True
    g_cfg = tmp_path / "darkness.yaml"
    g_cfg.write_text(dk.config_path().read_text().replace("check_row_stride: 32", "check_row_stride: 3"))
    return {
        "horizons": horizons,
        "lon": lon,
        "lat": lat,
        "common": dict(
            input_manifest=manifest,
            kernel_manifest=kernel,
            stage_a_dir=stage_a,
            stage_e_dir=tmp_path / "daily-lit-production-1",
            darkness_config=g_cfg,
            daily_lit_config=daily_cfg,
        ),
        "root": tmp_path,
    }


def test_pilot_passes_every_check_and_refuses_reuse(stage_e):
    out = stage_e["root"] / "darkness-pilot"
    report = dk.run_darkness(mode="pilot", output_dir=out, workers=2, **stage_e["common"])
    checks = report["checks"]
    for name in (
        "valid_matches_stage_e",
        "daily_lit_hours_equal_stage_e",
        "lit_total_equals_stage_e_cumulative",
        "month_triples_within_bounds",
        "stitched_months_equal_streaming",
        "run_invariants",
        "serial_parallel_identical",
        "brute_force_identical",
        "stitching_equals_brute_force",
    ):
        assert checks[name] is True, name
    assert checks["daily_mismatched_pixel_days"] == 0
    assert report["period"]["hours"] == 2160
    assert [d["epochs"] for d in report["timings_seconds"]["dispatches"]] == [744, 672, 744]
    assert report["sample_checks"]["windows"] == 6
    assert report["projection"]["projection_limit_node_hours"] == 2.4
    assert report["passed"] is True
    assert "monthly/dark-best.npy" in json.loads((out / "output-manifest.json").read_text())["outputs"]
    with pytest.raises(ValueError, match="not empty"):
        dk.run_darkness(mode="pilot", output_dir=out, workers=1, **stage_e["common"])


def test_production_reader_windows_equal_brute_force(stage_e):
    out = stage_e["root"] / "darkness-production"
    report = dk.run_darkness(mode="production", output_dir=out, workers=3, **stage_e["common"])
    assert report["passed"] is True, report["checks"]
    assert report["period"]["months"] == 14
    cube = dk.DarknessCube(out)
    assert [y["label"] for y in cube.years] == ["2026", "2027"]
    epochs = dl.day_epochs(date(2026, 1, 1), (date(2027, 3, 1) - date(2026, 1, 1)).days)
    lit = dk.lit_matrix(
        stage_e["horizons"], stage_e["lon"], stage_e["lat"], _slow_vectors(_hours_of(epochs)),
        disk_radius_deg=DISK, azimuth_origin_deg=0.0,
    )
    valid = cube.valid.reshape(-1)
    starts = [dk.parse_month(m["first_day"]) for m in cube.months] + [cube.end]
    edges = [m["first_hour"] for m in cube.months] + [cube.total_hours]
    for a in range(len(starts) - 1):
        for b in range(a + 1, len(starts)):
            got = cube.longest_dark_hours(starts[a].strftime("%Y-%m"), starts[b]).reshape(-1)
            brute = dk.brute_force_longest_dark(lit[edges[a] : edges[b]])
            assert np.array_equal(got[valid], brute[valid]), (a, b)
            assert (got[~valid] == -1).all()
    full = cube.window_summary("2026-01", "2027-03")
    assert np.array_equal(full["longest_dark_hours"].reshape(-1)[valid], dk.brute_force_runs(lit)["dark_hours"][valid])
    window = (slice(2, 5), slice(1, 4))
    assert np.array_equal(
        cube.longest_dark_hours("2026-03", "2026-07", window),
        cube.longest_dark_hours("2026-03", "2026-07")[window],
    )
    summary = cube.window_summary("2026-02", "2026-05")
    censored = summary["edge_censored"]
    best = summary["longest_dark_hours"]
    assert np.array_equal(
        censored, cube.valid & (best > 0) & ((best == summary["lead"]) | (best == summary["trail"]))
    )
    assert cube.period("dark_hours").dtype == np.uint32
    assert (cube.period("dark_flags")[~cube.valid] == dk.FILL_FLAGS).all()
    with pytest.raises(ValueError, match="month range"):
        cube.longest_dark_hours("2025-12", "2026-03")
    with pytest.raises(ValueError, match="1st"):
        cube.longest_dark_hours("2026-01-15", "2026-03")

    # Without the month files, year-aligned windows still answer from the
    # yearly blocks; anything else needs the month files.
    reference = cube.longest_dark_hours("2026-01", "2027-01")
    for field in dk.MONTH_FIELDS:
        (out / f"monthly/dark-{field}.npy").unlink()
    lean = dk.DarknessCube(out)
    assert np.array_equal(lean.longest_dark_hours("2026-01", "2027-01"), reference)
    with pytest.raises(FileNotFoundError, match="calendar-year"):
        lean.longest_dark_hours("2026-03", "2027-01")


def test_job_refuses_a_stage_e_run_with_other_inputs(stage_e):
    common = stage_e["common"]
    stage_e_dir = common["stage_e_dir"]
    run_config = stage_e_dir / dl.RUN_CONFIG_NAME
    original = run_config.read_text()
    key = json.loads(original)
    run_config.write_text(json.dumps({**key, "horizon_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="different horizon cube"):
        dk.run_darkness(mode="pilot", output_dir=stage_e["root"] / "x", workers=1, **common)
    run_config.write_text(original)
    report_path = stage_e_dir / "daily-lit-report.json"
    report = json.loads(report_path.read_text())
    report_path.write_text(json.dumps({**report, "passed": False}))
    with pytest.raises(ValueError, match="has not passed"):
        dk.run_darkness(mode="pilot", output_dir=stage_e["root"] / "y", workers=1, **common)
    report_path.write_text(json.dumps(report))
    # A Stage E daily file that no longer matches its manifest is refused.
    daily = stage_e_dir / "daily" / "lit-hours-2026.npy"
    data = np.load(daily)
    data[0, 1, 1] ^= 1
    np.save(daily, data)
    with pytest.raises(ValueError, match="digest differs"):
        dk.run_darkness(mode="pilot", output_dir=stage_e["root"] / "z", workers=1, **common)
    assert not (stage_e["root"] / "x").exists() and not (stage_e["root"] / "y").exists()


def test_receipt_pin_rejects_drift(stage_e):
    common = stage_e["common"]
    args = dict(
        kernel_manifest=common["kernel_manifest"],
        stage_a_dir=common["stage_a_dir"],
        stage_e_dir=common["stage_e_dir"],
        horizon_cube=common["stage_a_dir"] / "terrain-horizon.npy",
        mode="pilot",
        darkness_config=common["darkness_config"],
    )
    pin = dk.receipt_pin(**args, daily_lit_config=common["daily_lit_config"])
    assert pin["darkness_mode"] == "pilot"
    assert pin["period_start_day"] == "2026-01-01"
    assert pin["period_end_day_exclusive"] == "2026-04-01"
    assert len(pin["stage_e_manifest_sha256"]) == 64
    # The repository daily-lit config is not the one this Stage E ran with.
    with pytest.raises(ValueError, match="daily-lit.yaml differs"):
        dk.receipt_pin(**args)
    common["kernel_manifest"].write_text('{"kernels": [], "changed": true}')
    with pytest.raises(ValueError, match="different kernel manifest"):
        dk.receipt_pin(**args, daily_lit_config=common["daily_lit_config"])


def test_projection_uses_the_headroom_limit():
    measured = dict(
        fixed_seconds=60.0,
        cold_excess_seconds=5.0,
        warm_seconds_per_epoch=0.03,
        spice_verify_seconds=0.2,
        spice_calls=19,
        spice_seconds_per_epoch=1e-4,
        full_epochs=163_632,
        io_seconds=10.0,
        pilot_days=90,
        full_days=6818,
        production_node_hours=3.0,
        stage_node_hour_cap=3.5,
    )
    result = dk.project_node_hours(projection_limit_node_hours=2.4, **measured)
    assert 1.3 < result["projected_node_hours"] < 1.6
    assert result["fits_production_cap"] is True
    slow = dk.project_node_hours(
        projection_limit_node_hours=2.4, **{**measured, "warm_seconds_per_epoch": 0.055}
    )
    assert 2.4 < slow["projected_node_hours"] < 3.0
    assert slow["fits_production_cap"] is False
