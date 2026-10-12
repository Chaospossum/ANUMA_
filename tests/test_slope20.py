"""Tiled 20 m Horn slope must match the untiled serial reference."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.windows import Window
from rasterio.transform import from_origin

from anuma.slope import horn_slope_deg
from anuma.slope20 import (
    HALO,
    TileSpec,
    compute_slope_tiled,
    compute_slope_untiled,
    compute_tile_slope,
    plan_tiles,
    project_slope_node_hours,
    slope_tile_from_array,
)

REPO = Path(__file__).resolve().parents[1]
DEM_PATH = REPO / "data" / "raw" / "LDEM_80S_20MPP_ADJ.TIF"


def _write_synthetic_dem(path: Path, array: np.ndarray, resolution_m: float = 20.0) -> None:
    transform = from_origin(0.0, array.shape[0] * resolution_m, resolution_m, resolution_m)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype="float32",
        crs="+proj=stere +lat_0=-90 +lon_0=0 +R=1737400 +units=m +no_defs",
        transform=transform,
        nodata=np.nan,
    ) as dataset:
        dataset.write(array.astype(np.float32), 1)


def test_tiled_parallel_matches_untiled_serial_on_synthetic(tmp_path):
    rng = np.random.default_rng(20261012)
    dem = 1000.0 + rng.normal(0.0, 25.0, size=(96, 80)).cumsum(axis=0) * 0.1
    dem = dem.astype(np.float32)
    dem_path = tmp_path / "synthetic.tif"
    _write_synthetic_dem(dem_path, dem)

    reference = compute_slope_untiled(dem_path)
    assert reference.shape == dem.shape

    out = tmp_path / "slope.npy"
    progress = compute_slope_tiled(
        dem_path,
        out,
        workers=4,
        tile_size=32,
        checkpoint_dir=tmp_path / "ckpt",
    )
    tiled = np.load(out, allow_pickle=False)
    assert progress["tile_count"] == len(plan_tiles(96, 80, tile_size=32))
    np.testing.assert_array_equal(tiled, reference)

    # Resume must leave the merged result bit-identical.
    progress2 = compute_slope_tiled(
        dem_path,
        tmp_path / "slope-resume.npy",
        workers=2,
        tile_size=32,
        checkpoint_dir=tmp_path / "ckpt",
    )
    resumed = np.load(tmp_path / "slope-resume.npy", allow_pickle=False)
    assert progress2["tile_count"] == progress["tile_count"]
    np.testing.assert_array_equal(resumed, reference)


def test_halo_interior_matches_full_array_horn():
    z = np.arange(25.0, dtype=np.float64).reshape(5, 5) * 3.0
    full = horn_slope_deg(z, 20.0)
    # Tile covering the centre 3x3 with halo 1 uses the same 5x5 neighbourhood.
    tile = slope_tile_from_array(z, cellsize_m=20.0, halo=HALO)
    np.testing.assert_array_equal(tile, full[1:-1, 1:-1])


@pytest.mark.skipif(not DEM_PATH.is_file(), reason="20 m DEM not staged")
def test_tiled_parallel_matches_untiled_on_real_dem_window(tmp_path):
    row0, col0 = 15000, 15100
    height = width = 128
    window = Window(col0, row0, width, height)

    # Standalone crop: tiled+parallel must match untiled serial on that DEM.
    with rasterio.open(DEM_PATH) as src:
        data = src.read(1, window=window)
        transform = src.window_transform(window)
        profile = src.profile.copy()
    profile.update(
        height=height,
        width=width,
        transform=transform,
        compress=None,
    )
    crop = tmp_path / "crop.tif"
    with rasterio.open(crop, "w", **profile) as dest:
        dest.write(data, 1)

    reference_crop = compute_slope_untiled(crop)
    out = tmp_path / "slope.npy"
    compute_slope_tiled(
        crop,
        out,
        workers=3,
        tile_size=48,
        checkpoint_dir=tmp_path / "ckpt",
    )
    tiled = np.load(out, allow_pickle=False)
    np.testing.assert_array_equal(tiled, reference_crop)

    # Interior window of the full DEM: a halo-aware tile must match Horn on
    # the same elev+halo block (neighbours outside the window are real).
    tile = TileSpec(
        tile_row=0,
        tile_col=0,
        row0=row0,
        row1=row0 + height,
        col0=col0,
        col1=col0 + width,
    )
    halo_window = Window(col0 - HALO, row0 - HALO, width + 2 * HALO, height + 2 * HALO)
    with rasterio.open(DEM_PATH) as src:
        elev_halo = src.read(1, window=halo_window).astype(np.float64)
    reference_interior = slope_tile_from_array(elev_halo, cellsize_m=20.0, halo=HALO)
    from_full = compute_tile_slope(DEM_PATH, tile)
    np.testing.assert_array_equal(from_full, reference_interior)


def test_project_slope_node_hours_scales_waves():
    projected = project_slope_node_hours(
        tile_seconds=10.0,
        pilot_tiles=4,
        full_tiles=16,
        workers=2,
        scoring_seconds=5.0,
        production_node_hours=6.0,
        stage_node_hour_cap=40.0,
    )
    assert projected["pilot_waves"] == 2
    assert projected["full_waves"] == 8
    assert projected["projected_slope_seconds"] == 40.0
    assert projected["projected_scoring_seconds"] == 5.0
    assert projected["projected_wall_seconds"] == 45.0
    assert projected["fits_production_cap"] is True
    over = project_slope_node_hours(
        tile_seconds=7.0 * 3600.0,
        pilot_tiles=1,
        full_tiles=1,
        workers=1,
        scoring_seconds=0.0,
        production_node_hours=6.0,
        stage_node_hour_cap=40.0,
    )
    assert over["fits_production_cap"] is False
