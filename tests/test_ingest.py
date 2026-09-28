"""Phase 1 ingest and grid tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from anuma.config import load_config
from anuma.grid import Grid
from anuma.ingest import run


@pytest.fixture(scope="module")
def synthetic_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("anuma_data")
    cfg = load_config()
    run(cfg, root, synthetic=True)
    return root


def test_grid_roundtrip_within_one_pixel():
    grid = Grid()
    samples = [
        (0.0, -90.0),
        (0.0, -85.0),
        (45.0, -82.0),
        (180.0, -81.0),
        (-90.0, -87.0),
        (120.0, -80.5),
    ]
    for lon, lat in samples:
        row, col = grid.to_pixel(lon, lat)
        lon2, lat2 = grid.to_lonlat(row, col)
        row2, col2 = grid.to_pixel(lon2, lat2)
        dist = float(np.hypot(row2 - row, col2 - col))
        assert dist < 1.0, (lon, lat, dist)


def test_grid_covers_80s_circle():
    grid = Grid()
    rho_80 = grid._circle_radius_m(-80.0)
    assert grid.xmax >= rho_80
    assert -grid.xmin >= rho_80
    row, col = grid.to_pixel(0.0, -80.0)
    assert 0.0 <= row <= grid.height
    assert 0.0 <= col <= grid.width


def test_slope_finite_inside_85s(synthetic_root: Path):
    cfg = load_config()
    grid = Grid(cfg)
    slope = np.load(synthetic_root / cfg["paths"]["grid"] / "slope.npy")
    inside = grid.inside_latitude(-85.0)
    assert np.isfinite(slope[inside]).all()


def test_slope_range(synthetic_root: Path):
    cfg = load_config()
    slope = np.load(synthetic_root / cfg["paths"]["grid"] / "slope.npy")
    finite = slope[np.isfinite(slope)]
    assert finite.size > 0
    assert float(finite.min()) >= 0.0
    assert float(finite.max()) <= 60.0


def test_quicklooks_exist(synthetic_root: Path):
    cfg = load_config()
    qdir = synthetic_root / cfg["paths"]["quicklook"]
    for name in ("dem", "slope", "illumination", "hydrogen", "earth_visibility"):
        path = qdir / f"{name}.png"
        assert path.is_file(), path
        assert path.stat().st_size > 0


def test_npy_and_grid_json(synthetic_root: Path):
    cfg = load_config()
    gdir = synthetic_root / cfg["paths"]["grid"]
    assert (gdir / "grid.json").is_file()
    for name in ("dem", "slope", "illumination", "hydrogen", "earth_visibility"):
        assert (gdir / f"{name}.npy").is_file()


def test_artemis_geojson_has_nine_named_regions(synthetic_root: Path):
    import json

    cfg = load_config()
    path = synthetic_root / cfg["paths"]["artemis_geojson"]
    data = json.loads(path.read_text())
    names = [f["properties"]["name"] for f in data["features"]]
    assert names == cfg["artemis3"]["regions"]
    assert len(names) == 9
    assert data["properties"]["source"] == cfg["artemis3"]["source_url"]
    assert all(f["geometry"] is None for f in data["features"])
