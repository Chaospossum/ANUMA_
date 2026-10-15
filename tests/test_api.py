"""Phase 3 API tests. Constructed arrays only — not ingest --synthetic."""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import shutil
import time
from datetime import date
from pathlib import Path

import matplotlib.image as mpimg
import numpy as np
import pytest
from fastapi.testclient import TestClient

from anuma.api import INGEST_COMMAND, app, clear_cache
from anuma.config import load_config, repo_root
from anuma.daily_lit import DailyLitCube
from anuma.grid import Grid
from anuma.ingest import write_artemis_geojson
from tests.test_daily_lit import _write_synthetic_output


def _pixel(grid: Grid, lon: float, lat: float) -> tuple[int, int]:
    row, col = grid.to_pixel(lon, lat)
    return int(np.floor(row)), int(np.floor(col))


def _empty(shape: tuple[int, int]) -> np.ndarray:
    return np.full(shape, np.nan, dtype=np.float32)


@pytest.fixture(scope="module")
def ingested_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("anuma_api")
    cfg = load_config()
    grid = Grid(cfg)
    gdir = root / cfg["paths"]["grid"]
    gdir.mkdir(parents=True)
    grid.save_json(gdir / "grid.json")
    shape = (grid.height, grid.width)
    slope = _empty(shape)
    illumination = _empty(shape)
    hydrogen = _empty(shape)
    earth_visibility = _empty(shape)

    xa, ya = grid.fwd.transform(0.0, -87.0)
    lon_far, lat_far = grid.inv.transform(xa, ya + 50_000.0)
    peaks = [
        (_pixel(grid, 0.0, -87.0), 100.0, 1.0),
        (_pixel(grid, float(lon_far), float(lat_far)), 80.0, 2.0),
        (_pixel(grid, 45.0, -87.0), 100.0, 20.0),
    ]
    for (row, col), value, slope_deg in peaks:
        slope[row, col] = slope_deg
        illumination[row, col] = value
        hydrogen[row, col] = value
        earth_visibility[row, col] = value

    np.save(gdir / "slope.npy", slope)
    np.save(gdir / "illumination.npy", illumination)
    np.save(gdir / "hydrogen.npy", hydrogen)
    np.save(gdir / "earth_visibility.npy", earth_visibility)
    write_artemis_geojson(cfg, root / cfg["paths"]["artemis_geojson"])
    return root


@pytest.fixture
def client_empty(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ANUMA_ROOT", str(tmp_path))
    clear_cache()
    return TestClient(app)


@pytest.fixture
def client_ingested(ingested_root, monkeypatch) -> TestClient:
    monkeypatch.setenv("ANUMA_ROOT", str(ingested_root))
    clear_cache()
    return TestClient(app)


def test_layers_not_ingested(client_empty: TestClient):
    cfg = load_config()
    response = client_empty.get("/layers")
    assert response.status_code == 200
    body = response.json()
    assert body["ingested"] is False
    assert body["ingest_command"] == INGEST_COMMAND
    assert "grid.json" in body["missing"]
    names = [layer["name"] for layer in body["layers"]]
    assert "illumination" in names
    illum = next(layer for layer in body["layers"] if layer["name"] == "illumination")
    assert illum["unit"] == cfg["layers"]["illumination"]["unit"]
    assert illum["mode"] == "higher_better"
    assert cfg["layers"]["illumination"]["citation"] in illum["citation"]
    assert body["proj4"] == cfg["projection"]["proj4"]
    assert len(body["bounds"]) == 4
    assert body["bounds"][0] < 0 < body["bounds"][2]


def test_score_not_ingested_returns_command(client_empty: TestClient):
    response = client_empty.post(
        "/score",
        json={
            "weights": {"illumination": 0.5, "hydrogen": 0.3, "earth_visibility": 0.2},
            "hard": {"max_slope_deg": 8},
            "n": 10,
        },
    )
    assert response.status_code == 412
    detail = response.json()["detail"]
    assert detail["error"] == "not_ingested"
    assert detail["ingest_command"] == "python3 -m anuma.ingest"
    assert "python3 -m anuma.ingest" in detail["message"]


def test_score_slope_radius_masks_isolated_cells(client_ingested: TestClient):
    request = {
        "weights": {"illumination": 0.5, "hydrogen": 0.3, "earth_visibility": 0.2},
        "hard": {"max_slope_deg": 8, "slope_radius_m": 0},
        "n": 10,
    }
    assert len(client_ingested.post("/score", json=request).json()["sites"]) == 2
    # Each fixture peak is one finite cell among no-data cells.
    request["hard"]["slope_radius_m"] = 240
    response = client_ingested.post("/score", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["sites"] == []


def test_score_rejects_negative_slope_radius(client_ingested: TestClient):
    response = client_ingested.post(
        "/score", json={"hard": {"max_slope_deg": 8, "slope_radius_m": -1}}
    )
    assert response.status_code == 422


def test_layers_reports_slope_radius_default(client_empty: TestClient):
    cfg = load_config()
    body = client_empty.get("/layers").json()
    assert body["defaults"]["slope_radius_m"] == float(cfg["hard"]["slope_radius_m"])


def test_layer_png_not_ingested(client_empty: TestClient):
    response = client_empty.get("/layer/illumination.png")
    assert response.status_code == 412
    assert response.json()["detail"]["ingest_command"] == INGEST_COMMAND


def test_artemis_geojson_names_only_without_ingest(client_empty: TestClient):
    cfg = load_config()
    response = client_empty.get("/artemis3.geojson")
    assert response.status_code == 200
    data = response.json()
    names = [feat["properties"]["name"] for feat in data["features"]]
    assert names == [region["name"] for region in cfg["artemis3"]["regions"]]
    assert any(feat["geometry"] is not None for feat in data["features"])
    assert any(feat["geometry"] is None for feat in data["features"])
    assert data["properties"]["point_source_doi"] == cfg["artemis3"]["point_source"]["doi"]


def test_score_returns_heatmap_sites_and_artemis(client_ingested: TestClient):
    t0 = time.perf_counter()
    response = client_ingested.post(
        "/score",
        json={
            "weights": {"illumination": 0.5, "hydrogen": 0.3, "earth_visibility": 0.2},
            "hard": {"max_slope_deg": 8},
            "n": 10,
        },
    )
    elapsed = time.perf_counter() - t0
    assert response.status_code == 200, response.text
    assert elapsed < 2.0
    body = response.json()
    png = base64.b64decode(body["png"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    image = mpimg.imread(io.BytesIO(png))
    assert image.ndim == 3 and image.shape[2] == 4
    assert float(np.min(image[..., 3])) == 0.0
    assert float(np.max(image[..., 3])) > 0.0
    assert len(body["bounds"]) == 4
    assert body["bounds"][0] < 0 < body["bounds"][2]
    assert len(body["sites"]) == 2
    top = body["sites"][0]
    assert top["lat"] < -80.0
    assert set(top["contributions"]) == {"illumination", "hydrogen", "earth_visibility"}
    np.testing.assert_allclose(
        sum(top["contributions"].values()), top["total"], atol=1e-5
    )
    grid = Grid(load_config())
    assert (top["row"], top["col"]) == _pixel(grid, 0.0, -87.0)
    assert isinstance(top["nearest"], str) and top["nearest"]
    assert top["distance_km"] is not None and top["distance_km"] >= 0.0
    assert len(body["artemis"]["regions"]) == 17
    assert body["artemis"]["coordinate_status"] == "nasa_centres_unpublished"
    assert "Goossens" in body["artemis"]["point_source"]
    unpublished = [
        region
        for region in body["artemis"]["regions"]
        if region["coordinate_status"] == "not_published"
    ]
    assert len(unpublished) == 4
    assert all(region["rank"] is None for region in unpublished)


def test_ranked_site_downloads(client_ingested: TestClient):
    request = {
        "weights": {"illumination": 0.5, "hydrogen": 0.3, "earth_visibility": 0.2},
        "hard": {"max_slope_deg": 8},
        "n": 10,
    }
    csv_response = client_ingested.post("/score.csv", json=request)
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_response.headers["content-disposition"]
    lines = csv_response.text.splitlines()
    comments = [line for line in lines if line.startswith("#")]
    assert lines[: len(comments)] == comments
    assert json.loads(comments[0].split(": ", 1)[1]) == {
        "source": "published",
        "label": load_config()["illumination_window"]["default_label"],
    }
    assert comments[1] == "# keepout: null"
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[len(comments) :]))))
    assert [row["rank"] for row in rows] == ["1", "2"]
    assert rows[0]["nearest_artemis_region"]
    assert float(rows[0]["latitude_deg"]) < -80.0

    geo_response = client_ingested.post("/score.geojson", json=request)
    assert geo_response.status_code == 200
    assert geo_response.headers["content-type"].startswith("application/geo+json")
    collection = geo_response.json()
    assert collection["type"] == "FeatureCollection"
    assert collection["properties"]["illumination_window"]["source"] == "published"
    assert collection["properties"]["keepout"] is None
    assert len(collection["features"]) == 2
    assert collection["features"][0]["geometry"]["type"] == "Point"
    assert collection["features"][0]["properties"]["rank"] == 1
    assert "Lunar longitude/latitude" in collection["coordinate_reference"]


def test_layer_png_greyscale(client_ingested: TestClient):
    response = client_ingested.get("/layer/illumination.png")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    image = mpimg.imread(io.BytesIO(response.content))
    assert image.ndim == 3 and image.shape[2] == 4
    rgb = image[..., :3]
    opaque = image[..., 3] > 0.1
    assert opaque.any()
    np.testing.assert_allclose(rgb[opaque, 0], rgb[opaque, 1], atol=0.08)
    np.testing.assert_allclose(rgb[opaque, 1], rgb[opaque, 2], atol=0.08)


@pytest.mark.parametrize("name", ["slope", "illumination"])
def test_layer_png_with_slope_radius(client_ingested: TestClient, monkeypatch, name):
    cfg = load_config()
    cfg["hard"]["slope_radius_m"] = 240.0
    monkeypatch.setattr("anuma.api._cfg", lambda: cfg)
    response = client_ingested.get(f"/layer/{name}.png")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("image/png")


def test_layer_png_score_uses_defaults(client_ingested: TestClient):
    response = client_ingested.get("/layer/score.png")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    expected = client_ingested.post("/score", json={}).json()["png"]
    assert response.content == base64.b64decode(expected)


def test_unknown_layer_is_404(client_ingested: TestClient):
    response = client_ingested.get("/layer/not_a_layer.png")
    assert response.status_code == 404


def test_negative_weight_is_rejected(client_ingested: TestClient):
    response = client_ingested.post(
        "/score", json={"weights": {"illumination": -0.01}}
    )
    assert response.status_code == 422


def test_keepout_radius_is_required(client_ingested: TestClient):
    response = client_ingested.post(
        "/score",
        json={
            "keepout": {
                "points": [{"lon": 0.0, "lat": -87.0}],
                "mode": "hard",
            }
        },
    )
    assert response.status_code == 400
    assert "radius_km" in response.json()["detail"]


def test_keepout_hard_mask_removes_a_site(client_ingested: TestClient):
    request = {
        "weights": {"illumination": 0.5, "hydrogen": 0.3, "earth_visibility": 0.2},
        "hard": {"max_slope_deg": 8},
        "n": 10,
    }
    first = client_ingested.post("/score", json=request)
    assert first.status_code == 200
    top = first.json()["sites"][0]
    blocked = client_ingested.post(
        "/score",
        json={
            **request,
            "keepout": {
                "points": [{"lon": top["lon"], "lat": top["lat"]}],
                "radius_km": 1.0,
                "mode": "hard",
            },
        },
    )
    assert blocked.status_code == 200, blocked.text
    replacement = blocked.json()["sites"][0]
    assert (replacement["row"], replacement["col"]) != (top["row"], top["col"])


def test_layers_ingested_flag(client_ingested: TestClient):
    response = client_ingested.get("/layers")
    assert response.status_code == 200
    body = response.json()
    assert body["ingested"] is True
    assert body["missing"] == []
    present = {layer["name"]: layer["present"] for layer in body["layers"]}
    assert present["slope"] is True
    assert present["illumination"] is True
    assert present["dem"] is False


def test_index_folio_is_local():
    clear_cache()
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    text = response.text
    assert "anuma" in text
    assert "Student project · NeoSpace Challenge 2026 · 80°S–90°S" in text
    assert "ispace-EUROPE" not in text
    assert "/vendor/leaflet.js" in text
    assert "/vendor/proj4.js" in text
    assert "/vendor/proj4leaflet.js" in text
    assert "cdn" not in text.lower()
    assert "unpkg" not in text.lower()
    assert "Download CSV" in text
    assert "Not affiliated with or" in text
    css = client.get("/style.css")
    assert css.status_code == 200
    assert "Outfit" in css.text
    assert "Cormorant" not in css.text
    assert "cdn" not in css.text.lower()
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def _write_stability_summary(root: Path) -> None:
    cfg = load_config()
    path = root / cfg["stability"]["monte_carlo_summary"]
    path.parent.mkdir(parents=True, exist_ok=True)
    site = {"id": "1318:1219", "row": 1318, "col": 1219, "lat": -89.4, "lon": -137.3}
    other = {"id": "753:1681", "row": 753, "col": 1681, "lat": -84.8, "lon": 38.9}
    summary = {
        "draw_count": 500,
        "weight_ranges": {"illumination": [0.4, 0.6]},
        "policies": {
            "decided_per_layer": {
                "top10_membership_frequency": [
                    {**site, "count": 500, "frequency": 1.0},
                    {**other, "count": 422, "frequency": 0.844},
                ],
                "winner_stability": {
                    "winners": [
                        {**site, "count": 499, "frequency": 0.998},
                        {**other, "count": 1, "frequency": 0.002},
                    ],
                },
            },
            "legacy_global_2_98": {
                "top10_membership_frequency": [],
                "winner_stability": {"winners": []},
            },
        },
    }
    path.write_text(json.dumps(summary))


def test_stability_reports_default_policy(tmp_path, monkeypatch):
    _write_stability_summary(tmp_path)
    monkeypatch.setenv("ANUMA_ROOT", str(tmp_path))
    clear_cache()
    body = TestClient(app).get("/stability.json").json()
    assert body["available"] is True
    assert body["job_id"] == "5332991"
    assert body["policy"] == "decided_per_layer"
    assert body["draw_count"] == 500
    assert "±0.1" in body["note"] and "legacy_global_2_98" in body["note"]
    assert body["winners"][0]["frequency"] == 0.998
    rows = {row["id"]: row["frequency"] for row in body["top10_membership_frequency"]}
    assert rows == {"1318:1219": 1.0, "753:1681": 0.844}
    assert all({"row", "col"} <= set(row) for row in body["top10_membership_frequency"])


def test_stability_without_study_file(client_empty: TestClient):
    response = client_empty.get("/stability.json")
    assert response.status_code == 200
    assert response.json() == {"available": False}


def test_stage_b_checks_file_schema(client_empty: TestClient):
    cfg = load_config()
    on_disk = json.loads((repo_root() / cfg["stability"]["stage_b_checks"]).read_text())
    response = client_empty.get("/checks/stage-b-1km.json")
    assert response.status_code == 200
    body = response.json()
    assert body == on_disk
    assert body["source"]["job"] == "5334425"
    assert body["match_km"] == 0.3
    assert body["flag"]["field"] == "within_1km"
    assert [site["rank_240m"] for site in body["sites"]] == list(range(1, 11))
    for site in body["sites"]:
        assert -90.0 <= site["lat"] <= -80.0
        assert -180.0 <= site["lon"] <= 180.0
        fractions = site["fraction_le_8deg"]
        assert set(fractions) == {"in_cell", "within_500m", "within_1km"}
        assert all(0.0 <= value <= 1.0 for value in fractions.values())
    flagged = [
        site["rank_240m"]
        for site in body["sites"]
        if site["fraction_le_8deg"]["within_1km"] < body["flag"]["below"]
    ]
    assert set(flagged) == {2, 7, 10}


def test_stage_d_noise_file_schema(client_empty: TestClient):
    cfg = load_config()
    on_disk = json.loads((repo_root() / cfg["stability"]["stage_d_checks"]).read_text())
    response = client_empty.get("/checks/stage-d-noise.json")
    assert response.status_code == 200
    body = response.json()
    assert body == on_disk
    assert body["source"]["job"] == "5345050"
    assert body["draws"] == 200
    assert body["threshold"] == 0.5
    assert set(body["text"]) == {"stable_pass", "stable_fail", "unstable"}
    assert [site["default_rank"] for site in body["sites"]] == [1, 2, 3, 8, 9, 10, 11, 12]
    for site in body["sites"]:
        assert site["site_id"] == f"{site['row']}:{site['col']}"
        assert 0.0 <= site["pass_rate"] <= 1.0
        # The UI shows a tick or cross only for an all-or-nothing stable site.
        if not site["noise_unstable"]:
            assert site["pass_rate"] in (0.0, 1.0)
    unstable = [site["default_rank"] for site in body["sites"] if site["noise_unstable"]]
    passing = [site["default_rank"] for site in body["sites"] if site["pass_rate"] == 1.0]
    assert unstable == [8]
    assert passing == [3]


def test_stage_d_noise_file_missing_is_404(client_empty: TestClient, monkeypatch):
    import anuma.api as api

    cfg = load_config()
    cfg["stability"]["stage_d_checks"] = "data/checks/no-such-file.json"
    monkeypatch.setattr(api, "_cfg", lambda: cfg)
    response = client_empty.get("/checks/stage-d-noise.json")
    assert response.status_code == 404


def test_sites_table_has_noise_column():
    html = (repo_root() / "static" / "index.html").read_text()
    app_js = (repo_root() / "static" / "app.js").read_text()
    assert ">DEM-<br />noise</th>" in html
    assert ">Flat ground<br />1 km</th>" in html
    assert "/checks/stage-d-noise.json" in app_js
    assert "check.row === site.row && check.col === site.col" in app_js
    # A tested site with a mixed pass rate is borderline, not untested.
    assert 'return "borderline: " + pass' in app_js
    # Verdicts are in a visible legend row, not only in tooltips.
    assert 'id="table-legend"' in html and "not checked</span>" in html
    assert 'id="legend-note">no colour (grey terrain) = fails slope rule' in html


def _artemis_points(client: TestClient, lists: set[str]) -> list[dict]:
    """Keep-out points as the GUI builds them from the served GeoJSON."""
    features = client.get("/artemis3.geojson").json()["features"]
    return [
        {"lon": feat["geometry"]["coordinates"][0], "lat": feat["geometry"]["coordinates"][1]}
        for feat in features
        if feat["geometry"] is not None and lists & set(feat["properties"]["lists"])
    ]


def test_keepout_artemis_points_change_the_ranking(client_ingested: TestClient):
    request = {"hard": {"max_slope_deg": 8}, "n": 50}
    default = client_ingested.post("/score", json=request).json()["sites"]
    points = _artemis_points(client_ingested, {"2022", "2024"})
    cited = [r for r in load_config()["artemis3"]["regions"] if r.get("lon_east") is not None]
    assert len(points) == len(cited)
    hard = client_ingested.post(
        "/score",
        json={**request, "keepout": {"points": points, "radius_km": 50.0, "mode": "hard"}},
    )
    assert hard.status_code == 200, hard.text
    sites = hard.json()["sites"]
    assert [(s["row"], s["col"]) for s in sites] != [(s["row"], s["col"]) for s in default]
    # distance_km is the distance to the nearest cited Artemis III point.
    assert all(site["distance_km"] > 50.0 for site in sites)

    penalty = client_ingested.post(
        "/score",
        json={
            **request,
            "keepout": {"points": points, "radius_km": 50.0, "mode": "penalty", "penalty": 0.25},
        },
    ).json()["sites"]
    by_cell = {(s["row"], s["col"]): s["total"] for s in penalty}
    near = [s for s in default if s["distance_km"] <= 50.0]
    assert near
    for site in near:
        assert (site["row"], site["col"]) in by_cell
        assert by_cell[(site["row"], site["col"])] == pytest.approx(site["total"] - 0.25, abs=1e-5)


def test_keepout_is_recorded_in_exports(client_ingested: TestClient):
    keepout = {"points": [{"lon": 0.0, "lat": -87.0}], "radius_km": 5.0, "mode": "hard"}
    collection = client_ingested.post("/score.geojson", json={"keepout": keepout}).json()
    assert collection["properties"]["keepout"]["radius_km"] == 5.0
    assert collection["properties"]["keepout"]["points"] == keepout["points"]
    text = client_ingested.post("/score.csv", json={"keepout": keepout}).text
    assert text.splitlines()[1].startswith('# keepout: {"points":[{"lon":0.0,"lat":-87.0}]')


def test_keepout_2024_list_has_fewer_points(client_empty: TestClient):
    both = _artemis_points(client_empty, {"2022", "2024"})
    only_2024 = _artemis_points(client_empty, {"2024"})
    assert 0 < len(only_2024) < len(both)


# ------------------------------------------------- Stage E month window


WINDOW_START, WINDOW_END = date(2027, 1, 1), date(2027, 7, 1)


@pytest.fixture
def window_client(tmp_path, monkeypatch):
    """A coarse grid with finite layers and a synthetic daily-lit cube of the same shape."""
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
    data, valid, _ = _write_synthetic_output(cube_dir, WINDOW_START, WINDOW_END, shape=shape)
    monkeypatch.setenv("ANUMA_ROOT", str(tmp_path))
    monkeypatch.setattr("anuma.api._cfg", lambda: cfg)
    clear_cache()
    yield TestClient(app), data, valid, cube_dir
    clear_cache()


def test_month_window_changes_the_score(window_client):
    client, data, valid, _ = window_client
    default = client.post("/score", json={}).json()
    assert default["illumination_window"]["source"] == "published"
    windowed = client.post(
        "/score", json={"illumination_months": {"start": "2027-02", "end": "2027-03"}}
    )
    assert windowed.status_code == 200, windowed.text
    body = windowed.json()
    assert body["illumination_window"]["source"] == "daily_lit"
    assert body["illumination_window"]["start_day"] == "2027-02-01"
    # End month is inclusive: March 2027 runs to 1 April.
    assert body["illumination_window"]["end_day_exclusive"] == "2027-04-01"
    assert [s["total"] for s in body["sites"]] != [s["total"] for s in default["sites"]]
    assert body["png"] != default["png"]
    # Out-of-cap cube pixels are NaN, so no ranked site sits on one.
    assert all(valid[s["row"], s["col"]] for s in body["sites"])


def test_month_window_equals_cube_mean(window_client):
    client, data, valid, cube_dir = window_client
    import anuma.api as api

    cfg = api._cfg()
    root = api.data_root()
    store = api._load_store(cfg, root)
    for start, end, days in (("2027-02", "2027-04", (31, 120)), ("2027-05", "2027-05", (120, 151))):
        window = api.MonthWindow(start=start, end=end)
        fraction, used = api._window_illumination(cfg, root, store, window)
        expected = DailyLitCube(cube_dir).lit_hours_mean(*window.days()) / 24.0
        np.testing.assert_array_equal(fraction, expected.astype(np.float32))
        brute = data[days[0] : days[1]].astype(np.float64).mean(axis=0) / 24.0
        np.testing.assert_allclose(fraction[valid], brute[valid], atol=1e-6)
        assert np.isnan(fraction[~valid]).all()
    # A single month: start == end covers that whole month.
    assert used["start_day"] == "2027-05-01" and used["end_day_exclusive"] == "2027-06-01"
    response = client.post(
        "/score", json={"illumination_months": {"start": "2027-05", "end": "2027-05"}}
    )
    assert response.status_code == 200, response.text
    assert response.json()["illumination_window"]["end_day_exclusive"] == "2027-06-01"


def test_month_window_bounds_follow_the_cube(window_client):
    client, *_ = window_client
    meta = client.get("/layers").json()["illumination_window"]
    assert meta["available"] is True
    # The synthetic cube holds 2027-01..2027-06, inside the configured range.
    assert (meta["first_month"], meta["last_month"]) == ("2027-01", "2027-06")


def test_month_window_cube_shape_mismatch_is_400(window_client):
    client, _, _, cube_dir = window_client
    shutil.rmtree(cube_dir)
    cube_dir.mkdir()
    _write_synthetic_output(cube_dir, WINDOW_START, WINDOW_END, shape=(3, 5))
    clear_cache()
    response = client.post(
        "/score", json={"illumination_months": {"start": "2027-01", "end": "2027-02"}}
    )
    assert response.status_code == 400
    assert "does not match grid" in response.json()["detail"]


def test_month_window_layer_png(window_client):
    client, *_ = window_client
    plain = client.get("/layer/illumination.png")
    windowed = client.get("/layer/illumination.png?start=2027-01&end=2027-03")
    assert plain.status_code == 200 and windowed.status_code == 200, windowed.text
    assert windowed.headers["content-type"].startswith("image/png")
    assert windowed.content != plain.content
    assert client.get("/layer/slope.png?start=2027-01&end=2027-03").status_code == 400
    assert client.get("/layer/illumination.png?start=2027-01").status_code == 400
    assert client.get("/layer/illumination.png?start=2027-13&end=2027-03").status_code == 400


@pytest.mark.parametrize(
    ("start", "end", "status", "text"),
    [
        ("2026-13", "2027-01", 422, "YYYY-MM"),
        ("2027-1", "2027-03", 422, "YYYY-MM"),
        ("2027-03", "2027-01", 422, "after end month"),
        ("2025-12", "2026-02", 400, "outside 2026-01..2044-08"),
        ("2044-08", "2044-09", 400, "outside 2026-01..2044-08"),
        # In the configured range, but not in the synthetic cube.
        ("2030-01", "2030-02", 400, "outside 2027-01..2027-06"),
        ("2027-01\n", "2027-02", 422, "YYYY-MM"),
    ],
)
def test_month_window_rejects_bad_ranges(window_client, start, end, status, text):
    client, *_ = window_client
    response = client.post("/score", json={"illumination_months": {"start": start, "end": end}})
    assert response.status_code == status, response.text
    assert text in json.dumps(response.json())


def test_month_window_without_cube_is_400(window_client):
    client, _, _, cube_dir = window_client
    request = {"illumination_months": {"start": "2027-01", "end": "2027-02"}}
    # A directory without its index counts as missing, as does no directory.
    (cube_dir / "daily-lit-index.json").unlink()
    clear_cache()
    for _ in range(2):
        response = client.post("/score", json=request)
        assert response.status_code == 400
        assert "daily sunlight data not available on this machine" in response.json()["detail"]
        assert client.post("/score", json={}).status_code == 200
        assert client.get("/layers").json()["illumination_window"]["available"] is False
        shutil.rmtree(cube_dir, ignore_errors=True)
        clear_cache()


def test_keepout_rejects_non_finite_numbers(client_ingested: TestClient):
    body = '{"keepout": {"points": [{"lon": NaN, "lat": -87.0}], "radius_km": 5.0, "mode": "hard"}}'
    for path in ("/score", "/score.csv", "/score.geojson"):
        response = client_ingested.post(path, content=body, headers={"content-type": "application/json"})
        assert response.status_code == 400, path
        assert "finite" in response.json()["detail"]


# ------------------------------------------------- site card values


def test_site_returns_raw_layer_values(client_ingested: TestClient):
    cfg = load_config()
    grid = Grid(cfg)
    row, col = _pixel(grid, 0.0, -87.0)
    response = client_ingested.get(f"/site/{row}/{col}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["row"], body["col"]) == (row, col)
    # Raw layer units, not normalised scores.
    assert body["values"]["illumination"] == 100.0
    assert body["values"]["slope"] == 1.0
    assert body["values"]["hydrogen"] == 100.0
    assert body["values"]["earth_visibility"] == 100.0
    # Layers missing on this machine are null, not invented.
    assert body["values"]["dem"] is None
    assert body["units"]["hydrogen"] == cfg["layers"]["hydrogen"]["unit"]
    assert body["illumination_window"]["source"] == "published"
    lon, lat = grid.to_lonlat(row + 0.5, col + 0.5)
    assert body["lon"] == pytest.approx(lon) and body["lat"] == pytest.approx(lat)
    # Matches the coordinates /score reports for the same cell.
    sites = client_ingested.post("/score", json={}).json()["sites"]
    top = next(s for s in sites if (s["row"], s["col"]) == (row, col))
    assert body["lat"] == pytest.approx(top["lat"]) and body["lon"] == pytest.approx(top["lon"])


def test_site_nan_cell_is_null(client_ingested: TestClient):
    body = client_ingested.get("/site/0/0").json()
    assert all(value is None for value in body["values"].values())


@pytest.mark.parametrize("row,col", [(-1, 0), (0, 10**6), (10**6, 0)])
def test_site_outside_grid_is_404(client_ingested: TestClient, row, col):
    assert client_ingested.get(f"/site/{row}/{col}").status_code == 404


def test_site_not_ingested_is_412(client_empty: TestClient):
    assert client_empty.get("/site/0/0").status_code == 412


def test_site_month_window_uses_cube(window_client):
    client, data, valid, cube_dir = window_client
    row, col = map(int, np.argwhere(valid)[0])
    response = client.get(f"/site/{row}/{col}?start=2027-02&end=2027-03")
    assert response.status_code == 200, response.text
    body = response.json()
    expected = data[31:90, row, col].astype(np.float64).mean() / 24.0
    assert body["values"]["illumination"] == pytest.approx(expected, abs=1e-6)
    assert body["illumination_window"] == {"source": "daily_lit", "start": "2027-02", "end": "2027-03"}
    assert client.get(f"/site/{row}/{col}?start=2027-02").status_code == 400
    assert client.get(f"/site/{row}/{col}?start=2027-03&end=2027-02").status_code == 400
    assert client.get(f"/site/{row}/{col}?start=2020-01&end=2020-02").status_code == 400


# ------------------------------------------------- weight normalisation


def test_weights_are_normalised_to_sum_one(client_ingested: TestClient):
    base = {"illumination": 0.5, "hydrogen": 0.3, "earth_visibility": 0.2}
    scaled = {name: 4.0 * value for name, value in base.items()}
    a = client_ingested.post("/score", json={"weights": base}).json()
    b = client_ingested.post("/score", json={"weights": scaled}).json()
    # Same ratios, same ranking and the same 0..1 scores.
    assert [(s["row"], s["col"]) for s in a["sites"]] == [(s["row"], s["col"]) for s in b["sites"]]
    for x, y in zip(a["sites"], b["sites"]):
        assert y["total"] == pytest.approx(x["total"])
        assert y["contributions"] == pytest.approx(x["contributions"])
    assert b["weights_used"] == pytest.approx(base)
    assert sum(b["weights_used"].values()) == pytest.approx(1.0)
    assert all(0.0 <= s["total"] <= 1.0 + 1e-6 for s in b["sites"])


def test_unnormalised_weights_in_exports_match_score(client_ingested: TestClient):
    body = {"weights": {"illumination": 2.0, "hydrogen": 1.0, "earth_visibility": 1.0}}
    sites = client_ingested.post("/score", json=body).json()["sites"]
    text = client_ingested.post("/score.csv", json=body).text
    rows = list(csv.DictReader(line for line in text.splitlines() if not line.startswith("#")))
    assert [float(r["score"]) for r in rows] == pytest.approx([s["total"] for s in sites])


def test_all_zero_weights_are_rejected(client_ingested: TestClient):
    zero = {"illumination": 0.0, "hydrogen": 0.0, "earth_visibility": 0.0}
    for path in ("/score", "/score.csv", "/score.geojson"):
        response = client_ingested.post(path, json={"weights": zero})
        assert response.status_code == 400
        assert "All weights are zero" in response.json()["detail"]


def test_score_range_is_reported(client_ingested: TestClient):
    body = client_ingested.post("/score", json={}).json()
    low, high = body["score_range"]
    totals = [s["total"] for s in body["sites"]]
    assert low <= min(totals) and max(totals) == pytest.approx(high)


def test_api_description_is_neutral():
    assert "MAGPIE" not in (app.description or "")


def test_render_png_is_native_with_transparent_nan():
    import matplotlib

    from anuma.heatmap import render_png

    array = np.array([[0.0, np.nan], [0.5, 1.0]], dtype=np.float32)
    image = mpimg.imread(io.BytesIO(render_png(array, cmap="viridis")), format="png")
    assert image.shape[:2] == array.shape
    assert image[0, 1, 3] == 0.0
    assert image[0, 0, 3] == 1.0 and image[1, 1, 3] == 1.0
    viridis = matplotlib.colormaps["viridis"]
    np.testing.assert_allclose(image[0, 0, :3], viridis(0.0)[:3], atol=1 / 255)
    np.testing.assert_allclose(image[1, 1, :3], viridis(1.0)[:3], atol=1 / 255)


def test_exports_record_weights_and_slope_rule(client_ingested: TestClient):
    body = {"weights": {"illumination": 2.0, "hydrogen": 1.0, "earth_visibility": 1.0},
            "hard": {"max_slope_deg": 6.5, "slope_radius_m": 0}}
    lines = client_ingested.post("/score.csv", json=body).text.splitlines()
    comments = {line.split(": ", 1)[0][2:]: json.loads(line.split(": ", 1)[1])
                for line in lines if line.startswith("#")}
    assert comments["weights_used"] == pytest.approx(
        {"illumination": 0.5, "hydrogen": 0.25, "earth_visibility": 0.25}
    )
    assert comments["max_slope_deg"] == 6.5 and comments["slope_radius_m"] == 0.0
    props = client_ingested.post("/score.geojson", json=body).json()["properties"]
    assert props["max_slope_deg"] == 6.5
    assert props["weights_used"]["illumination"] == pytest.approx(0.5)
