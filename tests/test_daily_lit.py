"""Stage E daily lit hours: binning, bounds, cumulative reader, resume, fill."""

from __future__ import annotations

import hashlib
import json
import sys
import types
from datetime import date, timedelta

import numpy as np
import pytest

from anuma import daily_lit as dl
from anuma.sun import _advance_sunlight_block

DISK = 0.27


def _south_pole_vector(elevation_deg: float) -> np.ndarray:
    z_component = -np.sin(np.deg2rad(elevation_deg))
    x_component = np.sqrt(max(0.0, 1.0 - z_component * z_component))
    return np.array([x_component, 0.0, z_component], dtype=np.float64)


LIT = _south_pole_vector(5.0)
DARK = _south_pole_vector(-5.0)


def _pole_pixel():
    return (
        np.zeros((1, 1, 1), dtype=np.float32),
        np.array([[0.0]]),
        np.array([[-90.0]]),
    )


def _random_fixture(rows=9, cols=5, azimuths=6, seed=4):
    generator = np.random.default_rng(seed)
    horizons = generator.normal(size=(azimuths, rows, cols)).astype(np.float32)
    longitude = generator.uniform(-180.0, 180.0, size=(rows, cols))
    latitude = generator.uniform(-90.0, -80.0, size=(rows, cols))
    return horizons, longitude, latitude


def _random_vectors(count, seed=7):
    generator = np.random.default_rng(seed)
    vectors = generator.normal(size=(count, 3))
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


# ---------------------------------------------------------------- calendar


def test_day_epochs_split_at_utc_midnight():
    epochs = dl.day_epochs(date(2027, 12, 31), 2)
    assert len(epochs) == 48
    assert epochs[0] == "2027-12-31T00:00:00Z"
    assert epochs[23] == "2027-12-31T23:00:00Z"
    assert epochs[24] == "2028-01-01T00:00:00Z"
    assert epochs[-1] == "2028-01-01T23:00:00Z"


def test_epochs_either_side_of_midnight_land_in_their_own_day():
    horizons, lon, lat = _pole_pixel()
    vectors = np.stack([DARK] * 48)
    vectors[23] = LIT  # 23:00Z on day 0
    vectors[24] = LIT  # 00:00Z on day 1
    vectors[47] = LIT  # 23:00Z on day 1
    counts = dl.daily_lit_hours(
        horizons, lon, lat, vectors, disk_radius_deg=DISK, azimuth_origin_deg=0.0
    )
    assert counts.shape == (2, 1, 1)
    assert counts[:, 0, 0].tolist() == [1, 2]


def test_year_chunks_and_month_boundaries_follow_the_calendar():
    chunks = dl.year_chunks(date(2027, 12, 30), date(2029, 1, 3))
    assert [c["label"] for c in chunks] == ["2027", "2028", "2029"]
    assert [c["day_count"] for c in chunks] == [2, 366, 2]
    assert [c["offset_days"] for c in chunks] == [0, 2, 368]
    bounds = dl.month_boundaries(date(2027, 12, 30), date(2028, 3, 1))
    assert bounds == [date(2027, 12, 30), date(2028, 1, 1), date(2028, 2, 1), date(2028, 3, 1)]
    assert dl.month_boundaries(date(2026, 1, 1), date(2026, 1, 8)) == [
        date(2026, 1, 1),
        date(2026, 1, 8),
    ]


def test_parse_day_refuses_non_midnight_and_local_time():
    assert dl.parse_day("2030-02-03") == date(2030, 2, 3)
    assert dl.parse_day("2030-02-03T00:00:00Z") == date(2030, 2, 3)
    with pytest.raises(ValueError, match="midnight"):
        dl.parse_day("2030-02-03T01:00:00Z")
    with pytest.raises(ValueError, match="Zulu"):
        dl.parse_day("2030-02-03T00:00:00")


def test_config_spans_one_nodal_cycle_and_fits_the_cap():
    cfg = dl.load_daily_lit_config()
    start, end = dl.period_days(cfg, "production")
    assert start == date(2026, 1, 1)
    assert end == date(2044, 9, 1)
    assert (end - start).days >= 18.6 * 365.25
    assert end.day == 1
    pilot_start, pilot_end = dl.period_days(cfg, "pilot")
    assert (pilot_end - pilot_start).days == 7
    exe = cfg["execution"]
    assert exe["memory"] == "240G"
    assert exe["production_walltime"] == "03:00:00"
    assert exe["pilot_node_hours"] + exe["production_node_hours"] <= exe["stage_node_hour_cap"]
    assert cfg["stage_a"]["stop_rmse"] == 0.05
    assert cfg["acceptance"]["tune_after_run"] is False


# ---------------------------------------------------------------- bounds


def test_lit_hours_are_uint8_and_never_exceed_24():
    horizons, lon, lat = _pole_pixel()
    counts = dl.daily_lit_hours(
        horizons, lon, lat, np.stack([LIT] * 72), disk_radius_deg=DISK, azimuth_origin_deg=0.0
    )
    assert counts.dtype == np.uint8
    assert counts[:, 0, 0].tolist() == [24, 24, 24]
    horizons, lon, lat = _random_fixture()
    random = dl.daily_lit_hours(
        horizons, lon, lat, _random_vectors(96), disk_radius_deg=DISK, azimuth_origin_deg=0.0
    )
    assert random.dtype == np.uint8 and random.max() <= 24
    with pytest.raises(ValueError, match="whole days"):
        dl.daily_lit_hours(
            horizons, lon, lat, _random_vectors(30), disk_radius_deg=DISK, azimuth_origin_deg=0.0
        )


def test_daily_sum_equals_stage_a_accumulator_and_parallel_matches_serial():
    horizons, lon, lat = _random_fixture()
    vectors = _random_vectors(72)
    serial = dl.daily_lit_hours(
        horizons, lon, lat, vectors, disk_radius_deg=DISK, azimuth_origin_deg=0.0
    )
    parallel = dl.daily_lit_hours(
        horizons, lon, lat, vectors, disk_radius_deg=DISK, azimuth_origin_deg=0.0, workers=3
    )
    assert np.array_equal(serial, parallel)
    total, _, _ = _advance_sunlight_block(
        horizons,
        lon,
        lat,
        vectors,
        np.zeros(lon.shape),
        np.zeros(lon.shape, dtype=np.int64),
        np.zeros(lon.shape, dtype=np.int64),
        disk_radius_deg=DISK,
        azimuth_origin_deg=0.0,
    )
    assert np.array_equal(serial.sum(axis=0), total.astype(np.int64))


# ---------------------------------------------------------------- outputs


def _write_synthetic_output(tmp_path, start, end, shape=(4, 6), seed=11):
    generator = np.random.default_rng(seed)
    days = (end - start).days
    data = generator.integers(0, 25, size=(days, *shape), dtype=np.uint8)
    valid = generator.random(shape) > 0.25
    data[:, ~valid] = dl.FILL_UINT8
    run_key = {"test": True}
    chunks = dl.year_chunks(start, end)
    for chunk in chunks:
        offset = chunk["offset_days"]
        dl.write_chunk(tmp_path, chunk, data[offset : offset + chunk["day_count"]], run_key)
    np.save(tmp_path / "valid-mask.npy", valid)
    boundaries, checks = dl.build_cumulative(tmp_path, start, end, chunks, valid)
    dl.write_index(tmp_path, start, end, shape, chunks, boundaries)
    return data, valid, checks


def test_range_mean_from_cumulative_equals_brute_force(tmp_path):
    start, end = date(2027, 11, 20), date(2028, 3, 10)
    data, valid, checks = _write_synthetic_output(tmp_path, start, end)
    assert checks == {
        "lit_hours_max_valid": int(data[:, valid].max()),
        "lit_hours_within_0_24": True,
        "fill_matches_valid_mask": True,
    }
    cube = dl.DailyLitCube(tmp_path)
    generator = np.random.default_rng(3)
    days = (end - start).days
    ranges = [(0, days), (0, 1), (days - 1, days), (11, 12), (12, 43), (5, 70), (40, 41)]
    ranges += [tuple(sorted(generator.choice(days + 1, 2, replace=False))) for _ in range(40)]
    for lo, hi in ranges:
        first, last = start + timedelta(days=int(lo)), start + timedelta(days=int(hi))
        brute = data[lo:hi].astype(np.float64).mean(axis=0)
        got = cube.lit_hours_mean(first, last)
        assert np.allclose(got[valid], brute[valid], rtol=0, atol=1e-5), (lo, hi)
        assert np.isnan(got[~valid]).all()
        window = (slice(1, 3), slice(2, 5))
        windowed = dl.lit_hours_mean(tmp_path, first.isoformat(), last.isoformat(), window)
        assert np.array_equal(windowed, got[window], equal_nan=True)
    with pytest.raises(ValueError, match="outside"):
        cube.lit_hours_mean(start - timedelta(days=1), end)
    with pytest.raises(ValueError, match="outside"):
        cube.lit_hours_mean(start + timedelta(days=3), start + timedelta(days=3))


def test_fill_values_in_daily_cumulative_and_mean(tmp_path):
    start, end = date(2026, 1, 1), date(2026, 3, 4)
    data, valid, _ = _write_synthetic_output(tmp_path, start, end, seed=5)
    cumulative = np.load(tmp_path / "cumulative-monthly.npy")
    assert cumulative.dtype == np.uint32
    assert cumulative.shape[0] == 4  # Jan 1, Feb 1, Mar 1, Mar 4
    assert (cumulative[:, ~valid] == dl.FILL_UINT32).all()
    assert (cumulative[0, valid] == 0).all()
    assert np.array_equal(
        cumulative[-1, valid], data[:, valid].sum(axis=0, dtype=np.uint32)
    )
    mean = np.load(tmp_path / "mean-lit-fraction.npy")
    assert np.isnan(mean[~valid]).all()
    assert np.allclose(mean[valid], data[:, valid].mean(axis=0) / 24.0, atol=1e-6)
    cube = dl.DailyLitCube(tmp_path)
    assert (cube.daily(start, end)[:, ~valid] == dl.FILL_UINT8).all()
    assert (cube.lit_hours_sum(start, end)[~valid] == -1).all()
    index = json.loads((tmp_path / dl.INDEX_NAME).read_text())
    assert index["fill"]["lit_hours_uint8"] == 255
    assert index["fill"]["cumulative_uint32"] == 4294967295


def test_cumulative_check_flags_a_broken_fill(tmp_path):
    start, end = date(2026, 1, 1), date(2026, 1, 5)
    shape = (2, 2)
    data = np.full((4, *shape), 3, dtype=np.uint8)
    valid = np.array([[True, False], [True, True]])
    data[:, ~valid] = dl.FILL_UINT8
    data[2, 0, 1] = 7  # an out-of-cap pixel lost its fill on one day
    chunks = dl.year_chunks(start, end)
    dl.write_chunk(tmp_path, chunks[0], data, {"test": True})
    _, checks = dl.build_cumulative(tmp_path, start, end, chunks, valid)
    assert checks["fill_matches_valid_mask"] is False
    assert checks["lit_hours_within_0_24"] is True


# ---------------------------------------------------------------- resume


def test_compute_chunks_skips_finished_years_and_recomputes_missing(tmp_path):
    horizons, lon, lat = _random_fixture(rows=4, cols=3)
    valid = dl.valid_pixels(horizons, lon, lat)
    start, end = date(2027, 12, 30), date(2028, 1, 2)
    chunks = dl.year_chunks(start, end)
    calls = []

    def vectors_for(epochs):
        calls.append(epochs[0])
        seed = int(epochs[0][:4]) + int(epochs[0][5:7]) * 31 + int(epochs[0][8:10])
        return _random_vectors(len(epochs), seed=seed)

    kwargs = dict(
        horizons=horizons,
        longitude=lon,
        latitude=lat,
        valid=valid,
        run_key={"k": 1},
        vectors_for=vectors_for,
        workers=1,
        chunk_days=1,
        disk_radius_deg=DISK,
        azimuth_origin_deg=0.0,
    )
    first = dl.compute_chunks(tmp_path, chunks, **kwargs)
    assert first["computed"] == ["2027", "2028"]
    reference = np.load(dl.chunk_paths(tmp_path, "2028")[0])
    second = dl.compute_chunks(tmp_path, chunks, **kwargs)
    assert second["computed"] == [] and second["skipped"] == ["2027", "2028"]
    assert len(calls) == 2

    # A kill between the array and its record: the array alone is not trusted.
    dl.chunk_paths(tmp_path, "2028")[1].unlink()
    third = dl.compute_chunks(tmp_path, chunks, **kwargs)
    assert third["computed"] == ["2028"] and third["skipped"] == ["2027"]
    assert np.array_equal(np.load(dl.chunk_paths(tmp_path, "2028")[0]), reference)

    with pytest.raises(ValueError, match="run_key"):
        dl.compute_chunks(tmp_path, chunks, **{**kwargs, "run_key": {"k": 2}})


def test_output_dir_resumes_only_its_own_run(tmp_path):
    out = tmp_path / "out"
    assert dl._prepare_output_dir(out, {"a": 1}) is False
    assert dl._prepare_output_dir(out, {"a": 1}) is True
    with pytest.raises(ValueError, match="different run"):
        dl._prepare_output_dir(out, {"a": 2})
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "other.txt").write_text("x")
    with pytest.raises(ValueError, match="not empty"):
        dl._prepare_output_dir(foreign, {"a": 1})


# ---------------------------------------------------------------- SPICE


def test_window_and_interval_intersection():
    spans = [(0.0, 10.0), (20.0, 30.0)]
    assert dl.window_inside(spans, 1.0, 9.0)
    assert not dl.window_inside(spans, 9.0, 21.0)
    assert dl._intersect([[(0.0, 10.0)], [(5.0, 15.0)], [(-1.0, 8.0)]]) == [(5.0, 8.0)]
    assert dl._intersect([[(0.0, 1.0)], [(2.0, 3.0)]]) == []


def _fake_kernel_manifest(tmp_path, roles):
    entries = []
    for role in roles:
        path = tmp_path / f"{role}.bin"
        path.write_bytes(role.encode())
        entries.append(
            {"role": role, "path": path.name, "sha256": hashlib.sha256(role.encode()).hexdigest()}
        )
    manifest = tmp_path / "kernel-manifest.json"
    manifest.write_text(json.dumps({"kernels": entries}))
    return manifest


def _fake_spice(spk_end: float):
    """A stand-in for spiceypy with ET = seconds since 2000-01-01T12:00 TDB."""

    class Cell(list):
        pass

    def str2et(text):
        from datetime import datetime

        moment = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
        return (moment - datetime(2000, 1, 1, 12)).total_seconds()

    def kinds(path):
        name = path.rsplit("/", 1)[-1]
        return {
            "de440_ephemeris.bin": ("DAF", "SPK"),
            "lunar_orientation.bin": ("DAF", "PCK"),
        }.get(name, ("KPL", "FK"))

    module = types.SimpleNamespace(
        kclear=lambda: None,
        furnsh=lambda path: None,
        str2et=str2et,
        et2utc=lambda et, fmt, prec: f"ET{int(et)}",
        getfat=kinds,
        cell_double=lambda size: Cell(),
        cell_int=lambda size: Cell(),
        spkcov=lambda path, body, cell: cell.append((-1.4e10, spk_end)),
        pckfrm=lambda path, ids: ids.append(31008),
        card=lambda cell: len(cell),
        pckcov=lambda path, frame, cell: cell.append((-1.4e10, 2.0e10)),
        wncard=lambda cell: len(cell),
        wnfetd=lambda cell, i: cell[i],
    )
    return module


def test_spice_coverage_reads_binary_kernels_and_refuses_short_coverage(
    tmp_path, monkeypatch
):
    roles = ["de440_ephemeris", "lunar_orientation", "lunar_frame", "leap_seconds"]
    manifest = _fake_kernel_manifest(tmp_path, roles)
    monkeypatch.setitem(sys.modules, "spiceypy", _fake_spice(spk_end=2.0e10))
    report = dl.require_spice_coverage(manifest, roles, date(2026, 1, 1), date(2044, 9, 1))
    assert report["covers_window"] is True
    assert report["window"] == ["2026-01-01T00:00:00Z", "2044-08-31T23:00:00Z"]
    assert report["roles"]["lunar_frame"]["coverage"].startswith("text kernel")
    assert set(report["roles"]["de440_ephemeris"]["bodies"]) == {"10", "3", "301"}

    # SPK ending 2040 does not cover the period: refuse before any epoch.
    monkeypatch.setitem(sys.modules, "spiceypy", _fake_spice(spk_end=1.26e9))
    with pytest.raises(ValueError, match="do not cover"):
        dl.require_spice_coverage(manifest, roles, date(2026, 1, 1), date(2044, 9, 1))


# ---------------------------------------------------------------- end to end


class _FakeGrid:
    def __init__(self, lon, lat):
        self._lon, self._lat = lon, lat
        self.height, self.width = lon.shape

    def lonlat_arrays(self):
        return self._lon, self._lat


def _stage_a_dir(tmp_path, horizons, percent, *, status="passed"):
    stage_a = tmp_path / "stage-a"
    stage_a.mkdir()
    horizon_path = stage_a / "terrain-horizon.npy"
    np.save(horizon_path, horizons)
    np.save(stage_a / "percent-time-lit.npy", percent)
    (stage_a / "validation-report.json").write_text(
        json.dumps(
            {
                "status": status,
                "metrics": {"rmse": 0.0198, "stop_rmse": 0.05, "passed": status == "passed"},
            }
        )
    )
    digest = hashlib.sha256(horizon_path.read_bytes()).hexdigest()
    (stage_a / "output-manifest.json").write_text(
        json.dumps(
            {
                "outputs": {
                    "terrain-horizon.npy": {
                        "sha256": digest,
                        "bytes": horizon_path.stat().st_size,
                    }
                }
            }
        )
    )
    return stage_a


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
    monkeypatch.setattr(
        dl, "require_spice_coverage", lambda *args: {"covers_window": True, "fake": True}
    )

    def vectors(epochs, *args, **kwargs):
        hours = np.array([int(text[11:13]) for text in epochs])
        days = np.array([int(text[8:10]) for text in epochs])
        elevation = np.where((hours + days) % 3 == 0, 2.0, -0.1 * days)
        return np.stack([_south_pole_vector(value) for value in elevation])

    monkeypatch.setattr(dl, "sun_vectors_from_spice", vectors)


def test_pilot_runs_end_to_end_and_matches_stage_a_accumulator(tmp_path, monkeypatch):
    horizons, lon, lat = _random_fixture(rows=8, cols=5, azimuths=360)
    horizons = horizons * 0.5
    horizons[:, 0, 0] = np.nan  # out-of-cap pixel
    lon[0, 0] = np.nan
    lat[0, 0] = np.nan
    percent = np.full(lon.shape, 50.0, dtype=np.float32)
    percent[0, 0] = np.nan
    stage_a = _stage_a_dir(tmp_path, horizons, percent)
    _patch_runtime(monkeypatch, tmp_path, lon, lat)
    manifest = tmp_path / "input-manifest.json"
    manifest.write_text("{}")
    kernel = tmp_path / "kernel-manifest.json"
    kernel.write_text("{}")
    cfg_path = tmp_path / "daily-lit.yaml"
    raw = (dl.config_path()).read_text().replace("check_row_stride: 32", "check_row_stride: 3")
    cfg_path.write_text(raw)
    out = tmp_path / "pilot"
    report = dl.run_daily_lit(
        mode="pilot",
        input_manifest=manifest,
        kernel_manifest=kernel,
        stage_a_dir=stage_a,
        output_dir=out,
        workers=2,
        daily_lit_config=cfg_path,
    )
    assert report["equivalence"]["identical"] is True
    assert report["equivalence"]["pixels"] > 0
    assert report["checks"]["lit_hours_within_0_24"] is True
    assert report["checks"]["fill_matches_valid_mask"] is True
    assert report["checks"]["fill_matches_stage_a_nan"] is True
    assert report["period"]["epoch_count"] == 168
    assert report["projection"]["production_node_hours"] == 3.0
    assert report["passed"] is True
    daily = np.load(out / "daily" / "lit-hours-2026.npy")
    assert daily.shape == (7, 8, 5)
    assert daily[0, 0, 0] == dl.FILL_UINT8
    manifest_out = json.loads((out / "output-manifest.json").read_text())
    assert "daily/lit-hours-2026.npy" in manifest_out["outputs"]

    # The pilot never resumes; its timings must come from one fresh run.
    with pytest.raises(ValueError, match="pilot output directory already exists"):
        dl.run_daily_lit(
            mode="pilot",
            input_manifest=manifest,
            kernel_manifest=kernel,
            stage_a_dir=stage_a,
            output_dir=out,
            workers=1,
            daily_lit_config=cfg_path,
        )

    # A short production period (test config only) resumes after a lost year.
    short = raw.replace('end_utc: "2044-09-01T00:00:00Z"', 'end_utc: "2027-02-01T00:00:00Z"')
    short = short.replace('minimum_span_years: "18.6"', 'minimum_span_years: "1.0"')
    cfg_path.write_text(short)
    production = tmp_path / "production"
    args = dict(
        mode="production",
        input_manifest=manifest,
        kernel_manifest=kernel,
        stage_a_dir=stage_a,
        output_dir=production,
        workers=2,
        daily_lit_config=cfg_path,
    )
    full = dl.run_daily_lit(**args)
    assert full["timings_seconds"]["computed"] == ["2026", "2027"]
    assert full["checks"]["lit_hours_within_0_24"] is True
    comparison = full["stage_a_comparison"]
    assert comparison["fill_matches_stage_a_nan"] is True
    assert comparison["valid_pixels"] == 39
    reference = np.load(production / "cumulative-monthly.npy")
    dl.chunk_paths(production, "2027")[1].unlink()
    resumed = dl.run_daily_lit(**args)
    assert resumed["resumed"] is True
    assert resumed["timings_seconds"]["skipped"] == ["2026"]
    assert resumed["timings_seconds"]["computed"] == ["2027"]
    assert np.array_equal(np.load(production / "cumulative-monthly.npy"), reference)
    cube = dl.DailyLitCube(production)
    brute = cube.daily("2026-03-15", "2026-11-02").astype(np.float64).mean(axis=0)
    got = cube.lit_hours_mean("2026-03-15", "2026-11-02")
    valid = cube.valid
    assert np.allclose(got[valid], brute[valid], atol=1e-5)


def test_run_refuses_unpassed_stage_a_and_a_changed_horizon(tmp_path, monkeypatch):
    horizons, lon, lat = _random_fixture(rows=3, cols=3, azimuths=360)
    percent = np.full(lon.shape, 50.0, dtype=np.float32)
    _patch_runtime(monkeypatch, tmp_path, lon, lat)
    manifest = tmp_path / "input-manifest.json"
    manifest.write_text("{}")
    common = dict(
        mode="pilot",
        input_manifest=manifest,
        kernel_manifest=manifest,
        output_dir=tmp_path / "out",
        workers=1,
    )
    (tmp_path / "a").mkdir()
    failed = _stage_a_dir(tmp_path / "a", horizons, percent, status="failed")
    with pytest.raises(ValueError, match="has not passed"):
        dl.run_daily_lit(stage_a_dir=failed, **common)
    (tmp_path / "b").mkdir()
    passed = _stage_a_dir(tmp_path / "b", horizons, percent)
    np.save(tmp_path / "other.npy", horizons + 1.0)
    with pytest.raises(ValueError, match="digest differs"):
        dl.run_daily_lit(stage_a_dir=passed, horizon_cube=tmp_path / "other.npy", **common)
    assert not (tmp_path / "out").exists()


def test_receipt_pin_rejects_drift(tmp_path):
    horizons, lon, _ = _random_fixture(rows=3, cols=3, azimuths=4)
    stage_a = _stage_a_dir(tmp_path, horizons, np.zeros(lon.shape, dtype=np.float32))
    kernel = tmp_path / "kernel-manifest.json"
    kernel.write_text("{}")
    pin = dl.receipt_pin(
        kernel_manifest=kernel,
        stage_a_dir=stage_a,
        horizon_cube=stage_a / "terrain-horizon.npy",
        mode="production",
    )
    assert pin["period_start_day"] == "2026-01-01"
    assert pin["period_end_day_exclusive"] == "2044-09-01"
    assert pin["stage_a_status"] == "passed"
    receipt = {"account": "p201619", **pin}
    args = dict(
        kernel_manifest=kernel,
        stage_a_dir=stage_a,
        horizon_cube=stage_a / "terrain-horizon.npy",
        mode="production",
    )
    dl.reject_daily_lit_receipt(receipt, **args)
    with pytest.raises(ValueError, match="daily_lit_mode"):
        dl.reject_daily_lit_receipt(receipt, **{**args, "mode": "pilot"})
    kernel.write_text('{"changed": true}')
    with pytest.raises(ValueError, match="kernel_manifest_sha256"):
        dl.reject_daily_lit_receipt(receipt, **args)
