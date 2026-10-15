"""Phase 3 API tests. Constructed arrays only — not ingest --synthetic."""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import time
from pathlib import Path

import matplotlib.image as mpimg
import numpy as np
import pytest
from fastapi.testclient import TestClient

from anuma.api import INGEST_COMMAND, app, clear_cache
from anuma.config import load_config, repo_root
from anuma.grid import Grid
from anuma.ingest import write_artemis_geojson


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
    rows = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert [row["rank"] for row in rows] == ["1", "2"]
    assert rows[0]["nearest_artemis_region"]
    assert float(rows[0]["latitude_deg"]) < -80.0

    geo_response = client_ingested.post("/score.geojson", json=request)
    assert geo_response.status_code == 200
    assert geo_response.headers["content-type"].startswith("application/geo+json")
    collection = geo_response.json()
    assert collection["type"] == "FeatureCollection"
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
    assert 2 in flagged and 10 in flagged
