"""Shared fixtures. Constructed arrays only, not ingest --synthetic."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from anuma.config import load_config
from anuma.grid import Grid
from anuma.ingest import write_artemis_geojson


def _pixel(grid: Grid, lon: float, lat: float) -> tuple[int, int]:
    row, col = grid.to_pixel(lon, lat)
    return int(np.floor(row)), int(np.floor(col))


def _empty(shape: tuple[int, int]) -> np.ndarray:
    return np.full(shape, np.nan, dtype=np.float32)


# One full-grid root per test session (about 100 MB), shared by
# test_api and test_terrain_gate. Tests must not write into this root.
@pytest.fixture(scope="session")
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
