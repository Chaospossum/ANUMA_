"""DEM height-error display layer. Small synthetic GeoTIFFs only."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from fastapi.testclient import TestClient

from anuma import dem_error
from anuma.api import app, clear_cache
from anuma.config import load_config
from anuma.grid import Grid
from anuma.ingest import write_artemis_geojson
from anuma.score import score

NODATA = -9999.0


def _toy_cfg() -> dict:
    """A 26 x 26 cell grid (lat_max -89.9) so the fine raster stays tiny."""
    cfg = copy.deepcopy(load_config())
    cfg["projection"]["lat_max"] = -89.9
    return cfg


def _write_tif(path: Path, array: np.ndarray, transform: Affine, crs, nodata) -> Path:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=array.shape[1],
        height=array.shape[0],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=nodata,
    ) as ds:
        ds.write(array.astype(np.float32), 1)
    return path


def _fine_source(grid: Grid, inset: int, seed: int = 0) -> tuple[np.ndarray, Affine]:
    """20 m raster nested in the grid. inset > 0 starts it inside the grid (like
    LDEM, which begins 4 fine pixels in), inset < 0 pads it outside."""
    size = grid.width * 12 - 2 * inset
    values = np.random.default_rng(seed).uniform(0.5, 30.0, (size, size)).astype(np.float32)
    transform = Affine(20.0, 0.0, grid.xmin + 20.0 * inset, 0.0, -20.0, grid.ymax - 20.0 * inset)
    return values, transform


def _expected(grid: Grid, fine: np.ndarray, inset: int) -> np.ndarray:
    """Block means of the finite fine pixels, computed independently."""
    n = 12
    full = np.full((grid.height * n, grid.width * n), np.nan)
    if inset >= 0:
        full[inset : full.shape[0] - inset, inset : full.shape[1] - inset] = fine
    else:
        full[:] = fine[-inset : fine.shape[0] + inset, -inset : fine.shape[1] + inset]
    blocks = full.reshape(grid.height, n, grid.width, n)
    count = np.isfinite(blocks).sum(axis=(1, 3))
    total = np.nansum(blocks, axis=(1, 3))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 0, total / count, np.nan)


@pytest.mark.parametrize("inset", [4, 0, -3])
def test_block_mean_is_exact(tmp_path, inset):
    grid = Grid(_toy_cfg())
    fine, transform = _fine_source(grid, inset)
    src = _write_tif(tmp_path / "err.tif", fine, transform, grid.crs, NODATA)
    out, info = dem_error.aggregate(src, grid, band_rows=5)
    assert out.dtype == np.float32
    assert out.shape == (grid.height, grid.width)
    assert info["nested"] is True
    assert info["resampling"] == "block_mean"
    assert info["grid_origin_in_fine_pixels"] == [float(-inset), float(-inset)]
    np.testing.assert_allclose(out, _expected(grid, fine, inset), rtol=1e-6, atol=1e-5)
    assert np.isfinite(out).all()


def test_block_mean_hand_values(tmp_path):
    grid = Grid(_toy_cfg())
    fine = np.zeros((grid.height * 12, grid.width * 12), dtype=np.float32)
    fine[:12, :12] = np.arange(144, dtype=np.float32).reshape(12, 12)  # mean 71.5
    fine[:12, 12:24] = 3.0
    src = _write_tif(
        tmp_path / "err.tif", fine, Affine(20.0, 0.0, grid.xmin, 0.0, -20.0, grid.ymax),
        grid.crs, NODATA,
    )
    out, _ = dem_error.aggregate(src, grid)
    assert out[0, 0] == pytest.approx(71.5)
    assert out[0, 1] == pytest.approx(3.0)
    assert out[1, 1] == 0.0


def test_nodata_becomes_nan(tmp_path):
    grid = Grid(_toy_cfg())
    fine, transform = _fine_source(grid, 0)
    fine[:12, :12] = NODATA  # whole cell (0, 0)
    fine[12:18, :12] = NODATA  # top half of cell (1, 0)
    src = _write_tif(tmp_path / "err.tif", fine, transform, grid.crs, NODATA)
    out, _ = dem_error.aggregate(src, grid)
    assert np.isnan(out[0, 0])
    assert out[1, 0] == pytest.approx(float(fine[18:24, :12].astype(np.float64).mean()), rel=1e-6)
    assert np.isfinite(out).sum() == out.size - 1


def test_nan_nodata_source(tmp_path):
    grid = Grid(_toy_cfg())
    fine, transform = _fine_source(grid, 0)
    fine[24:36, 24:36] = np.nan
    fine[0, 0] = np.nan
    src = _write_tif(tmp_path / "err.tif", fine, transform, grid.crs, float("nan"))
    out, _ = dem_error.aggregate(src, grid)
    assert np.isnan(out[2, 2])
    assert out[0, 0] == pytest.approx(float(np.nanmean(fine[:12, :12].astype(np.float64))), rel=1e-6)


def test_unnested_source_is_refused(tmp_path):
    grid = Grid(_toy_cfg())
    fine, _ = _fine_source(grid, 0)
    shifted = Affine(20.0, 0.0, grid.xmin + 7.0, 0.0, -20.0, grid.ymax)
    src = _write_tif(tmp_path / "err.tif", fine, shifted, grid.crs, NODATA)
    with pytest.raises(ValueError, match="not nested"):
        dem_error.aggregate(src, grid)
    out, info = dem_error.aggregate(src, grid, allow_unnested=True)
    assert info["resampling"] == "gdal_average"
    assert np.isfinite(out).any()


def test_run_writes_npy_and_sidecar(tmp_path):
    cfg = _toy_cfg()
    grid = Grid(cfg)
    fine, transform = _fine_source(grid, 4)
    src = _write_tif(tmp_path / "err.tif", fine, transform, grid.crs, NODATA)
    out = tmp_path / "grid" / "dem_error.npy"
    grid.save_json(out.parent / "grid.json")
    sidecar = dem_error.run(src, out, cfg)
    array = np.load(out)
    assert array.dtype == np.float32
    inside = grid.inside_latitude(cfg["projection"]["lat_max"])
    assert np.isnan(array[~inside]).all()
    assert np.isfinite(array[inside]).all()
    on_disk = json.loads(out.with_suffix(".json").read_text())
    assert on_disk == sidecar
    assert sidecar["source"]["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
    assert sidecar["source"]["sha256_matches_expected"] is False
    assert sidecar["resampling"] == "block_mean"
    assert sidecar["display_only"] is True
    assert sidecar["unit"] == "m"
    assert sidecar["grid_sha256"] == dem_error.grid_sha256(grid)
    assert sidecar["grid"]["width"] == grid.width
    finite = array[np.isfinite(array)]
    stats = sidecar["stats"]
    assert stats["finite_cells"] == finite.size
    assert stats["min"] == pytest.approx(float(finite.min()))
    assert stats["median"] == pytest.approx(float(np.median(finite)))
    assert stats["p95"] == pytest.approx(float(np.percentile(finite, 95)))
    assert stats["max"] == pytest.approx(float(finite.max()))

    skipped = dem_error.run(src, out, cfg, hash_source=False)
    assert skipped["source"]["sha256"] is None
    assert skipped["source"]["sha256_skipped"] is True


def test_run_refuses_a_different_grid_json(tmp_path):
    cfg = _toy_cfg()
    grid = Grid(cfg)
    fine, transform = _fine_source(grid, 0)
    src = _write_tif(tmp_path / "err.tif", fine, transform, grid.crs, NODATA)
    out = tmp_path / "grid" / "dem_error.npy"
    Grid(load_config()).save_json(out.parent / "grid.json")
    with pytest.raises(ValueError, match="grid.json"):
        dem_error.run(src, out, cfg, hash_source=False)
    assert not out.exists()


def test_cli(tmp_path, monkeypatch, capsys):
    grid = Grid(load_config())
    # A small raster on the real grid's corner; the rest of the grid is NaN.
    fine = np.full((48, 48), 2.0, dtype=np.float32)
    transform = Affine(20.0, 0.0, -1200.0, 0.0, -20.0, 1200.0)
    src = _write_tif(tmp_path / "err.tif", fine, transform, grid.crs, NODATA)
    out = tmp_path / "dem_error.npy"
    dem_error.main(["--src", str(src), "--out", str(out), "--no-hash"])
    array = np.load(out)
    assert np.isfinite(array).sum() == 16
    assert np.nanmax(array) == 2.0
    assert "Wrote" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        dem_error.main(["--src", str(tmp_path / "missing.tif"), "--out", str(out)])


# --- scoring and API --------------------------------------------------------


def _layers(grid: Grid) -> dict[str, np.ndarray]:
    shape = (grid.height, grid.width)
    rng = np.random.default_rng(7)
    layers = {
        "slope": rng.uniform(0.0, 12.0, shape).astype(np.float32),
        "illumination": rng.uniform(0.0, 1.0, shape).astype(np.float32),
        "hydrogen": rng.uniform(50.0, 200.0, shape).astype(np.float32),
        "earth_visibility": rng.uniform(-5.0, 5.0, shape).astype(np.float32),
    }
    error = rng.uniform(0.5, 20.0, shape).astype(np.float32)
    error[: shape[0] // 2] = np.nan  # NaN must not mask the score
    layers["dem_error"] = error
    return layers


def test_score_ignores_dem_error():
    cfg = load_config()
    grid = Grid(cfg)
    with_error = _layers(grid)
    without = {k: v for k, v in with_error.items() if k != "dem_error"}
    base = score(without, grid=grid, cfg=cfg)
    same = score(with_error, grid=grid, cfg=cfg)
    zero = score(
        with_error,
        weights={**cfg["scoring"]["weights"], "dem_error": 0.0},
        grid=grid,
        cfg=cfg,
    )
    for result in (same, zero):
        np.testing.assert_array_equal(result.combined, base.combined)
        np.testing.assert_array_equal(result.hard_mask, base.hard_mask)
        assert result.weights == base.weights
    with pytest.raises(ValueError, match="display only"):
        score(with_error, weights={"illumination": 1.0, "dem_error": 0.5}, grid=grid, cfg=cfg)
    assert "dem_error" not in cfg["scoring"]["weights"]
    assert cfg["layers"]["dem_error"]["display_only"] is True
    assert "mode" not in cfg["layers"]["dem_error"]


@pytest.fixture
def api_root(tmp_path, monkeypatch) -> Path:
    cfg = load_config()
    grid = Grid(cfg)
    gdir = tmp_path / cfg["paths"]["grid"]
    gdir.mkdir(parents=True)
    grid.save_json(gdir / "grid.json")
    for name, array in _layers(grid).items():
        if name != "dem_error":
            np.save(gdir / f"{name}.npy", array)
    write_artemis_geojson(cfg, tmp_path / cfg["paths"]["artemis_geojson"])
    monkeypatch.setenv("ANUMA_ROOT", str(tmp_path))
    clear_cache()
    return tmp_path


def _add_dem_error(root: Path) -> np.ndarray:
    cfg = load_config()
    array = _layers(Grid(cfg))["dem_error"]
    np.save(root / cfg["paths"]["grid"] / "dem_error.npy", array)
    clear_cache()
    return array


def _dem_error_entry(client: TestClient) -> dict:
    body = client.get("/layers").json()
    return next(layer for layer in body["layers"] if layer["name"] == "dem_error")


def test_api_dem_error_absent(api_root):
    client = TestClient(app)
    entry = _dem_error_entry(client)
    assert entry["display_only"] is True
    assert entry["scored"] is False
    assert entry["available"] is False
    assert entry["present"] is False
    assert entry["label"] == "DEM height error (m), LOLA PGDA 90"
    assert entry["unit"] == "m"
    assert "Barker" in entry["citation"]
    assert "anuma.dem_error" in entry["command"]
    assert client.get("/layers").json()["ingested"] is True
    response = client.get("/layer/dem_error.png")
    assert response.status_code == 404
    assert "anuma.dem_error" in response.json()["detail"]
    assert client.post("/score", json={}).status_code == 200


def test_api_dem_error_not_ingested(tmp_path, monkeypatch):
    monkeypatch.setenv("ANUMA_ROOT", str(tmp_path))
    clear_cache()
    response = TestClient(app).get("/layer/dem_error.png")
    assert response.status_code == 412


def test_api_dem_error_present(api_root):
    client = TestClient(app)
    before = client.post("/score", json={}).json()
    cfg = load_config()
    late = _layers(Grid(cfg))["dem_error"]
    np.save(api_root / cfg["paths"]["grid"] / "dem_error.npy", late)
    # Built after the store was cached: still served without a restart.
    assert client.get("/layer/dem_error.png").status_code == 200
    array = _add_dem_error(api_root)
    assert _dem_error_entry(client)["available"] is True
    response = client.get("/layer/dem_error.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    finite = array[np.isfinite(array)]
    assert float(response.headers["X-Anuma-Range-Min"]) == pytest.approx(float(finite.min()), rel=1e-5)
    assert float(response.headers["X-Anuma-Range-Max"]) == pytest.approx(
        float(np.percentile(finite, 95)), rel=1e-5
    )
    assert response.headers["X-Anuma-Unit"] == "m"
    assert response.headers["X-Anuma-Display-Only"] == "true"
    assert client.get("/layer/dem_error.png?start=2026-01&end=2026-02").status_code == 400

    after = client.post("/score", json={}).json()
    assert after["sites"] == before["sites"]
    assert after["png"] == before["png"]
    weights = {"illumination": 0.5, "hydrogen": 0.3, "earth_visibility": 0.2}
    zero = client.post("/score", json={"weights": {**weights, "dem_error": 0.0}}).json()
    assert zero["sites"] == before["sites"]
    bad = client.post("/score", json={"weights": {**weights, "dem_error": 0.4}})
    assert bad.status_code == 400
    assert "display only" in bad.json()["detail"]
