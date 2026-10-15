"""Stage G darkness in the API and UI, on a synthetic darkness output.

The synthetic output is written by the same streaming kernel and writers the
HPC job uses (``runs_from_lit``, ``_write_period_maps``, ``_build_years``,
``write_index``) and checked against brute force on the lit matrix.
"""

from __future__ import annotations

import io
import shutil
from datetime import date, datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from anuma import darkness as dk
from anuma.api import app, clear_cache
from anuma.config import load_config, repo_root
from anuma.grid import Grid
from anuma.heatmap import FLAG_INDEX, TRANSPARENT_INDEX, render_ramp_png
from tests.test_daily_lit import _write_synthetic_output
from tests.test_darkness import _random_lit

# Daily-lit cube (for /score month windows) and the darkness period.
LIT_START, LIT_END = date(2027, 1, 1), date(2027, 7, 1)
DARK_START, DARK_END = date(2026, 12, 1), date(2027, 8, 1)


def write_synthetic_darkness(out, start, end, shape, seed=7):
    """A Stage G output directory from a random lit matrix; returns (lit, valid)."""
    months = dk.month_table(start, end)
    years = dk.year_table(months)
    hours = sum(m["hours"] for m in months)
    rng = np.random.default_rng(seed)
    lit = _random_lit(rng, hours, shape[0] * shape[1])
    period, triples = dk.runs_from_lit(lit, [m["hours"] for m in months])
    valid = rng.random(shape) > 0.1
    valid.reshape(-1)[:4] = True  # the fixed never-lit / never-dark columns
    out.mkdir(parents=True)
    np.save(out / "valid-mask.npy", valid)
    dk._write_period_maps(out, {k: v.reshape(shape) for k, v in period.items()}, valid)
    (out / "monthly").mkdir()
    for i, field in enumerate(dk.MONTH_FIELDS):
        values = triples[:, i, :].reshape(len(months), *shape)
        np.save(out / f"monthly/dark-{field}.npy", np.where(valid, values, dk.FILL_UINT16).astype(np.uint16))
    dk._build_years(out, months, years, valid)
    dk.write_index(out, start, end, shape, months, years)
    return lit, valid


def _hour(day: date) -> int:
    return (day - DARK_START).days * 24


@pytest.fixture
def dark_client(tmp_path, monkeypatch):
    cfg = load_config()
    cfg["projection"]["resolution_m"] = 12000.0
    grid = Grid(cfg)
    shape = (grid.height, grid.width)
    gdir = tmp_path / cfg["paths"]["grid"]
    gdir.mkdir(parents=True)
    grid.save_json(gdir / "grid.json")
    generator = np.random.default_rng(4)
    np.save(gdir / "slope.npy", np.ones(shape, dtype=np.float32))
    for name in ("illumination", "hydrogen", "earth_visibility"):
        np.save(gdir / f"{name}.npy", generator.random(shape).astype(np.float32))
    cube_dir = tmp_path / cfg["paths"]["daily_lit"]
    cube_dir.mkdir(parents=True)
    _write_synthetic_output(cube_dir, LIT_START, LIT_END, shape=shape)
    dark_dir = tmp_path / cfg["paths"]["darkness"]
    lit, valid = write_synthetic_darkness(dark_dir, DARK_START, DARK_END, shape)
    monkeypatch.setenv("ANUMA_ROOT", str(tmp_path))
    monkeypatch.setattr("anuma.api._cfg", lambda: cfg)
    clear_cache()
    yield {
        "client": TestClient(app),
        "lit": lit,
        "valid": valid,
        "shape": shape,
        "dir": dark_dir,
        "brute": dk.brute_force_runs(lit),
    }
    clear_cache()


def _flat(row, col, shape):
    return row * shape[1] + col


def _png_index(response) -> np.ndarray:
    image = Image.open(io.BytesIO(response.content))
    assert image.mode == "P"
    return np.asarray(image)


def test_layers_report_darkness(dark_client):
    body = dark_client["client"].get("/layers").json()
    meta = body["darkness"]
    assert meta["available"] is True and meta["monthly_available"] is True
    assert meta["period"]["start_day_utc"] == "2026-12-01"
    assert meta["job"] == "5345264"
    entries = {layer["name"]: layer for layer in body["layers"]}
    dark = entries["longest_dark"]
    assert dark["available"] is True and dark["darkness"] is True and dark["scored"] is False
    assert dark["label"] == "Longest continuous darkness (days), 2026–2044 modelled"
    assert dark["windowed"] is True and entries["longest_lit"]["windowed"] is False


def test_score_sites_carry_full_period_darkness(dark_client):
    client, shape, brute = dark_client["client"], dark_client["shape"], dark_client["brute"]
    body = client.post("/score", json={}).json()
    assert body["darkness"]["available"] is True and body["darkness"]["window"] is None
    assert body["sites"]
    hour_zero = datetime(2026, 12, 1)
    checked = 0
    for site in body["sites"]:
        k = _flat(site["row"], site["col"], shape)
        if not dark_client["valid"][site["row"], site["col"]]:
            assert site["longest_dark_hours"] is None and site["never_lit"] is None
            continue
        checked += 1
        dark = int(brute["dark_hours"][k])
        assert site["longest_dark_hours"] == dark
        assert site["longest_dark_days"] == pytest.approx(dark / 24, abs=1e-3)
        assert site["longest_dark_censored"] == bool(brute["dark_flags"][k] != 0 and dark > 0)
        assert site["longest_lit_hours"] == int(brute["lit_hours"][k])
        assert site["never_lit"] == (int(brute["lit_hours"][k]) == 0)
        if dark:
            start = hour_zero + timedelta(hours=int(brute["dark_start"][k]))
            assert site["longest_dark_start_utc"] == start.strftime("%Y-%m-%dT%H:%MZ")
            end = start + timedelta(hours=dark)
            assert site["longest_dark_end_utc"] == end.strftime("%Y-%m-%dT%H:%MZ")
        for name in ("window_longest_dark_hours", "window_longest_dark_days", "window_longest_dark_censored"):
            assert site[name] is None
    assert checked >= 5


def test_inclusive_ui_months_become_the_exclusive_reader_window(dark_client):
    client, shape, lit = dark_client["client"], dark_client["shape"], dark_client["lit"]
    months = {"start": "2027-02", "end": "2027-04"}  # Feb, Mar and Apr 2027
    body = client.post("/score", json={"illumination_months": months}).json()
    assert body["darkness"]["window"] == {**months, "available": True, "reason": None}
    window = lit[_hour(date(2027, 2, 1)) : _hour(date(2027, 5, 1))]
    brute = dk.brute_force_longest_dark(window)
    checked = 0
    for site in body["sites"]:
        k = _flat(site["row"], site["col"], shape)
        if not dark_client["valid"][site["row"], site["col"]]:
            assert site["window_longest_dark_hours"] is None
            continue
        checked += 1
        assert site["window_longest_dark_hours"] == int(brute[k])
        assert site["window_never_lit"] == (int(brute[k]) == window.shape[0])
        # Same answer from /site, which also keeps the full-period values.
        one = client.get(f"/site/{site['row']}/{site['col']}", params=months).json()
        assert one["darkness"]["window_longest_dark_hours"] == int(brute[k])
        assert one["darkness"]["longest_dark_hours"] == site["longest_dark_hours"]
        assert one["darkness_status"]["window"]["available"] is True
    assert checked >= 5


def test_site_without_window_and_out_of_cap(dark_client):
    client, shape, valid, brute = (dark_client[k] for k in ("client", "shape", "valid", "brute"))
    row, col = 0, 0  # flat pixel 0: never lit in _random_lit
    body = client.get(f"/site/{row}/{col}").json()["darkness"]
    assert body["never_lit"] is True and body["longest_lit_hours"] == 0
    assert body["longest_dark_hours"] == len(dark_client["lit"]) and body["longest_dark_censored"] is True
    assert body["window_longest_dark_hours"] is None
    hidden = np.argwhere(~valid)[0]
    out = client.get(f"/site/{hidden[0]}/{hidden[1]}").json()["darkness"]
    assert all(value is None for value in out.values())


def test_longest_dark_png_full_period_and_window(dark_client):
    client, shape, lit, valid = (dark_client[k] for k in ("client", "shape", "lit", "valid"))
    response = client.get("/layer/longest_dark.png")
    assert response.status_code == 200
    assert response.headers["x-anuma-unit"] == "days"
    assert response.headers["x-anuma-range-max"] == "45"
    index = _png_index(response)
    assert index.shape == shape
    never = valid & (dark_client["brute"]["lit_hours"].reshape(shape) == 0)
    assert int(response.headers["x-anuma-never-lit-pixels"]) == int(never.sum()) > 0
    assert (index[never] == FLAG_INDEX).all()
    assert (index[~valid] == TRANSPARENT_INDEX).all()
    assert not np.isin(index[valid & ~never], [FLAG_INDEX, TRANSPARENT_INDEX]).any()

    windowed = client.get("/layer/longest_dark.png", params={"start": "2027-03", "end": "2027-03"})
    assert windowed.status_code == 200 and windowed.headers["x-anuma-window"] == "2027-03..2027-03"
    march = lit[_hour(date(2027, 3, 1)) : _hour(date(2027, 4, 1))]
    best = dk.brute_force_longest_dark(march).reshape(shape)
    dark_all_month = valid & (best == march.shape[0])
    assert int(windowed.headers["x-anuma-never-lit-pixels"]) == int(dark_all_month.sum())
    assert (_png_index(windowed)[dark_all_month] == FLAG_INDEX).all()


def test_longest_lit_png_and_bad_requests(dark_client):
    client = dark_client["client"]
    response = client.get("/layer/longest_lit.png")
    assert response.status_code == 200 and response.headers["x-anuma-range-max"] == "30"
    assert client.get("/layer/longest_lit.png", params={"start": "2027-01", "end": "2027-02"}).status_code == 400
    assert client.get("/layer/longest_dark.png", params={"start": "2027-01"}).status_code == 400
    assert client.get("/layer/longest_dark.png", params={"start": "2027-05", "end": "2027-02"}).status_code == 400
    # Inside the config range but outside this darkness output.
    outside = client.get("/layer/longest_dark.png", params={"start": "2028-01", "end": "2028-02"})
    assert outside.status_code == 400 and "darkness window" in outside.json()["detail"]
    assert client.get("/layer/slope.png", params={"start": "2027-01", "end": "2027-02"}).status_code == 400


def test_without_month_files_only_whole_years_answer(dark_client):
    client, shape, lit = dark_client["client"], dark_client["shape"], dark_client["lit"]
    shutil.rmtree(dark_client["dir"] / "monthly")
    clear_cache()
    assert client.get("/layers").json()["darkness"]["monthly_available"] is False
    body = client.post("/score", json={"illumination_months": {"start": "2027-02", "end": "2027-04"}}).json()
    status = body["darkness"]["window"]
    assert status["available"] is False and "calendar years" in status["reason"]
    assert body["sites"] and all(site["window_longest_dark_hours"] is None for site in body["sites"])
    valid = dark_client["valid"]
    assert any(site["longest_dark_hours"] is not None for site in body["sites"])
    assert all(
        (site["longest_dark_hours"] is not None) == bool(valid[site["row"], site["col"]])
        for site in body["sites"]
    )
    failed = client.get("/layer/longest_dark.png", params={"start": "2027-02", "end": "2027-04"})
    assert failed.status_code == 400 and "calendar years" in failed.json()["detail"]
    # 2027-01..2027-07 is the whole 2027 block of this output: the yearly files answer.
    whole = client.get("/layer/longest_dark.png", params={"start": "2027-01", "end": "2027-07"})
    assert whole.status_code == 200
    year = lit[_hour(date(2027, 1, 1)) :]
    best = dk.brute_force_longest_dark(year).reshape(shape)
    expected = dark_client["valid"] & (best == year.shape[0])
    assert int(whole.headers["x-anuma-never-lit-pixels"]) == int(expected.sum())


def test_missing_darkness_is_graceful(dark_client):
    client = dark_client["client"]
    shutil.rmtree(dark_client["dir"])
    clear_cache()
    layers = client.get("/layers").json()
    assert layers["darkness"]["available"] is False and "not available" in layers["darkness"]["reason"]
    entries = {layer["name"]: layer for layer in layers["layers"]}
    assert entries["longest_dark"]["available"] is False
    body = client.post("/score", json={"illumination_months": {"start": "2027-02", "end": "2027-03"}}).json()
    assert body["darkness"]["available"] is False
    assert body["darkness"]["window"]["available"] is False
    assert all(site["longest_dark_hours"] is None for site in body["sites"])
    assert client.get("/layer/longest_dark.png").status_code == 404
    site = client.get("/site/1/1").json()
    assert site["darkness"]["longest_dark_hours"] is None
    assert site["darkness_status"]["available"] is False


def test_darkness_of_another_shape_is_unavailable(dark_client):
    client = dark_client["client"]
    out = dark_client["dir"]
    shutil.rmtree(out)
    write_synthetic_darkness(out, DARK_START, DARK_END, (3, 4))
    clear_cache()
    assert "does not match grid" in client.get("/layers").json()["darkness"]["reason"]
    body = client.post("/score", json={}).json()
    assert body["darkness"]["available"] is False
    assert all(site["longest_dark_hours"] is None for site in body["sites"])


def test_ingested_root_without_darkness_scores(ingested_root, monkeypatch):
    monkeypatch.setenv("ANUMA_ROOT", str(ingested_root))
    clear_cache()
    body = TestClient(app).post("/score", json={}).json()
    assert body["darkness"]["available"] is False
    assert body["sites"] and body["sites"][0]["longest_dark_hours"] is None
    clear_cache()


def test_render_ramp_png_colours():
    values = np.array([[0.0, 10.0, 50.0], [np.nan, 5.0, 45.0]])
    flag = np.array([[False, False, False], [True, True, False]])
    png = render_ramp_png(
        values, flag, low_rgb=(242, 242, 242), high_rgb=(77, 77, 77), flag_rgb=(0, 0, 0), vmin=0, vmax=45
    )
    image = Image.open(io.BytesIO(png))
    index = np.asarray(image)
    assert index[0, 0] == 0 and index[0, 2] == FLAG_INDEX - 1 and index[1, 2] == FLAG_INDEX - 1
    assert index[1, 0] == FLAG_INDEX and index[1, 1] == FLAG_INDEX  # flag wins over NaN
    rgba = np.asarray(image.convert("RGBA"))
    assert tuple(rgba[0, 0]) == (242, 242, 242, 255)
    assert tuple(rgba[0, 2]) == (77, 77, 77, 255)
    assert tuple(rgba[1, 1]) == (0, 0, 0, 255)


def test_ui_shows_longest_dark():
    html = (repo_root() / "static" / "index.html").read_text()
    app_js = (repo_root() / "static" / "app.js").read_text()
    sunlight = html.index('<th class="contrib">Sunlight</th>')
    dark = html.index(">Longest<br />dark</th>")
    hydrogen = html.index('<th class="contrib">Hydrogen</th>')
    assert sunlight < dark < hydrogen
    assert "darkCell(site, lastRequest && lastRequest.illumination_months)" in app_js
    assert '"longest dark stretch "' in app_js
    assert 'fact("Longest darkness"' in app_js and '"Longest sunlight"' in app_js
    assert 'name === "longest_dark" ? currentWindow() : null' in app_js


def test_exports_carry_darkness_fields(dark_client):
    import csv
    import json

    client, shape, brute, valid = (dark_client[k] for k in ("client", "shape", "brute", "valid"))
    request = {"illumination_months": {"start": "2027-02", "end": "2027-04"}}
    score = client.post("/score", json=request).json()
    text = client.post("/score.csv", json=request).text
    rows = list(csv.DictReader(line for line in text.splitlines() if not line.startswith("#")))
    geo = client.post("/score.geojson", json=request).json()
    assert len(rows) == len(score["sites"]) == len(geo["features"])
    names = (
        "longest_dark_hours", "longest_dark_censored", "never_lit", "longest_dark_start_utc",
        "longest_dark_end_utc", "longest_lit_hours", "window_longest_dark_hours",
    )
    for row, feature, site in zip(rows, geo["features"], score["sites"]):
        props = feature["properties"]
        for name in names:
            assert name in row and props[name] == site[name]
        if valid[site["row"], site["col"]]:
            k = _flat(site["row"], site["col"], shape)
            assert int(row["longest_dark_hours"]) == int(brute["dark_hours"][k])
            assert row["window_longest_dark_hours"] == str(site["window_longest_dark_hours"])
        else:
            assert row["longest_dark_hours"] == ""
    assert json.dumps(geo)  # finite, serialisable
