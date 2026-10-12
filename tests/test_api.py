"""Phase 3 API tests. Constructed arrays only — not ingest --synthetic."""

from __future__ import annotations

import base64
import csv
import io
import os
import time
from pathlib import Path

import matplotlib.image as mpimg
import numpy as np
import pytest
from fastapi.testclient import TestClient

from anuma.api import INGEST_COMMAND, app, clear_cache
from anuma.config import load_config
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
    assert names == cfg["artemis3"]["regions"]
    assert all(feat["geometry"] is None for feat in data["features"])
    assert data["properties"]["source"] == cfg["artemis3"]["source_url"]


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
    assert top["nearest"] is None
    assert top["distance_km"] is None
    assert len(body["artemis"]["regions"]) == 9
    assert body["artemis"]["coordinate_status"] == "not_published_by_nasa"
    assert all(region["rank"] is None for region in body["artemis"]["regions"])


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
    assert rows[0]["nearest_artemis_region"] == ""
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


def test_unknown_layer_is_404(client_ingested: TestClient):
    response = client_ingested.get("/layer/not_a_layer.png")
    assert response.status_code == 404


def test_negative_weight_is_rejected(client_ingested: TestClient):
    response = client_ingested.post(
        "/score", json={"weights": {"illumination": -0.01}}
    )
    assert response.status_code == 422


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
    assert "ispace-EUROPE" in text
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
