"""Tiled 20 m Horn slope over the full-cap LDEM GeoTIFF.

Each tile is read with a one-pixel halo so the 3x3 Horn gradient at tile
edges matches an untiled full-array result. Halo outside the DEM is NaN so
the finite-neighbour mask matches ``horn_slope_deg``.

Workers open the DEM themselves and read only their window. Large arrays are
never pickled into the pool. Finished tiles are checkpointed so a walltime
kill keeps completed work. ``workers=1`` is the serial reference.
"""

from __future__ import annotations

import json
import math
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

from anuma.slope import horn_slope_deg

HALO = 1
DEFAULT_TILE_SIZE = 2048
RESOLUTION_M = 20.0


@dataclass(frozen=True)
class TileSpec:
    """One interior tile on the DEM, identified by its row/column indices."""

    tile_row: int
    tile_col: int
    row0: int
    row1: int
    col0: int
    col1: int

    @property
    def key(self) -> str:
        return f"r{self.tile_row:03d}-c{self.tile_col:03d}"

    @property
    def shape(self) -> tuple[int, int]:
        return (self.row1 - self.row0, self.col1 - self.col0)


def dem_shape(dem_path: Path) -> tuple[int, int]:
    with rasterio.open(dem_path) as dataset:
        return int(dataset.height), int(dataset.width)


def plan_tiles(
    height: int,
    width: int,
    tile_size: int = DEFAULT_TILE_SIZE,
) -> list[TileSpec]:
    """Cover the DEM with non-overlapping interior tiles."""
    if tile_size <= 0:
        raise ValueError("tile_size must be positive")
    if height <= 0 or width <= 0:
        raise ValueError("DEM dimensions must be positive")
    tiles: list[TileSpec] = []
    n_rows = int(math.ceil(height / tile_size))
    n_cols = int(math.ceil(width / tile_size))
    for tile_row in range(n_rows):
        row0 = tile_row * tile_size
        row1 = min(height, row0 + tile_size)
        for tile_col in range(n_cols):
            col0 = tile_col * tile_size
            col1 = min(width, col0 + tile_size)
            tiles.append(
                TileSpec(
                    tile_row=tile_row,
                    tile_col=tile_col,
                    row0=row0,
                    row1=row1,
                    col0=col0,
                    col1=col1,
                )
            )
    return tiles


def _read_elev_with_halo(
    dataset: rasterio.io.DatasetReader,
    tile: TileSpec,
    halo: int = HALO,
) -> np.ndarray:
    """Read elevation for one tile plus a halo.

    Halo pixels that fall inside the DEM are real neighbours so tile edges
    match an untiled Horn pass. Halo outside the DEM is NaN so the finite
    3x3 mask matches ``horn_slope_deg`` (constant-zero weight outside the
    array), which leaves a one-pixel NaN frame on the DEM border.
    """
    row0 = tile.row0 - halo
    row1 = tile.row1 + halo
    col0 = tile.col0 - halo
    col1 = tile.col1 + halo
    src_row0 = max(0, row0)
    src_row1 = min(dataset.height, row1)
    src_col0 = max(0, col0)
    src_col1 = min(dataset.width, col1)
    window = Window(
        src_col0,
        src_row0,
        src_col1 - src_col0,
        src_row1 - src_row0,
    )
    block = dataset.read(1, window=window).astype(np.float64, copy=False)
    pad_top = src_row0 - row0
    pad_bottom = row1 - src_row1
    pad_left = src_col0 - col0
    pad_right = col1 - src_col1
    if pad_top or pad_bottom or pad_left or pad_right:
        block = np.pad(
            block,
            ((pad_top, pad_bottom), (pad_left, pad_right)),
            mode="constant",
            constant_values=np.nan,
        )
    expected = (tile.row1 - tile.row0 + 2 * halo, tile.col1 - tile.col0 + 2 * halo)
    if block.shape != expected:
        raise RuntimeError(
            f"halo window shape {block.shape} does not match expected {expected}"
        )
    return block


def slope_tile_from_array(
    elev_with_halo: np.ndarray,
    cellsize_m: float = RESOLUTION_M,
    halo: int = HALO,
) -> np.ndarray:
    """Horn slope for one halo-padded block; return the interior only."""
    slope = horn_slope_deg(elev_with_halo, cellsize_m)
    if halo == 0:
        return slope
    return slope[halo:-halo, halo:-halo].astype(np.float32, copy=False)


def compute_tile_slope(
    dem_path: Path | str,
    tile: TileSpec,
    *,
    cellsize_m: float = RESOLUTION_M,
    halo: int = HALO,
) -> np.ndarray:
    """Open the DEM, read one halo window, and return the interior slope."""
    with rasterio.open(dem_path) as dataset:
        elev = _read_elev_with_halo(dataset, tile, halo=halo)
    return slope_tile_from_array(elev, cellsize_m=cellsize_m, halo=halo)


def _tile_checkpoint_path(checkpoint_dir: Path, tile: TileSpec) -> Path:
    return checkpoint_dir / f"tile-{tile.key}.npy"


def _tile_done(checkpoint_dir: Path, tile: TileSpec) -> bool:
    path = _tile_checkpoint_path(checkpoint_dir, tile)
    if not path.is_file():
        return False
    try:
        array = np.load(path, mmap_mode="r", allow_pickle=False)
    except (OSError, ValueError):
        return False
    return array.shape == tile.shape


def _atomic_save_npy(path: Path, array: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # np.save appends ".npy" unless the name already ends with it.
    temporary = path.with_name(path.stem + ".tmp.npy")
    np.save(temporary, array, allow_pickle=False)
    os.replace(temporary, path)


def _worker_process_tile(payload: tuple) -> str:
    dem_path, tile_dict, checkpoint_dir, cellsize_m = payload
    tile = TileSpec(**tile_dict)
    checkpoint = Path(checkpoint_dir)
    if _tile_done(checkpoint, tile):
        return tile.key
    slope = compute_tile_slope(
        dem_path, tile, cellsize_m=float(cellsize_m), halo=HALO
    )
    if slope.shape != tile.shape:
        raise RuntimeError(f"tile {tile.key} shape mismatch")
    _atomic_save_npy(_tile_checkpoint_path(checkpoint, tile), slope)
    return tile.key


def compute_slope_tiled(
    dem_path: Path,
    output_path: Path,
    *,
    workers: int = 1,
    tile_size: int = DEFAULT_TILE_SIZE,
    checkpoint_dir: Path | None = None,
    cellsize_m: float = RESOLUTION_M,
    log_path: Path | None = None,
) -> dict:
    """Compute full-cap 20 m slope with tile checkpoints and optional parallelism.

    ``workers=1`` is the serial reference. Parallel workers use a fork pool and
    each task re-opens the DEM for a windowed read so large arrays are not
    pickled per task.
    """
    dem_path = Path(dem_path)
    output_path = Path(output_path)
    if workers <= 0 or workers > 128:
        raise ValueError("workers must be between 1 and 128")
    height, width = dem_shape(dem_path)
    tiles = plan_tiles(height, width, tile_size=tile_size)
    if checkpoint_dir is None:
        checkpoint_dir = output_path.parent / "checkpoint" / "slope-tiles"
    checkpoint_dir = Path(checkpoint_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    pending = [tile for tile in tiles if not _tile_done(checkpoint_dir, tile)]
    _log(
        log_path,
        f"slope tiles total={len(tiles)} pending={len(pending)} workers={workers}",
    )

    payloads = [
        (
            str(dem_path),
            {
                "tile_row": tile.tile_row,
                "tile_col": tile.tile_col,
                "row0": tile.row0,
                "row1": tile.row1,
                "col0": tile.col0,
                "col1": tile.col1,
            },
            str(checkpoint_dir),
            float(cellsize_m),
        )
        for tile in pending
    ]

    if not payloads:
        pass
    elif workers == 1:
        for payload in payloads:
            key = _worker_process_tile(payload)
            _log(log_path, f"slope tile {key} checkpointed")
    else:
        # Fork so workers inherit only the small task payload; each opens DEM.
        context = get_context("fork")
        with ProcessPoolExecutor(
            max_workers=min(workers, len(payloads)),
            mp_context=context,
        ) as pool:
            for key in pool.map(_worker_process_tile, payloads, chunksize=1):
                _log(log_path, f"slope tile {key} checkpointed")

    missing = [tile.key for tile in tiles if not _tile_done(checkpoint_dir, tile)]
    if missing:
        raise RuntimeError(f"slope checkpoint incomplete: {missing[:5]}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    memmap = np.lib.format.open_memmap(
        output_path,
        mode="w+",
        dtype=np.float32,
        shape=(height, width),
    )
    for tile in tiles:
        array = np.load(
            _tile_checkpoint_path(checkpoint_dir, tile), allow_pickle=False
        )
        memmap[tile.row0 : tile.row1, tile.col0 : tile.col1] = array
    memmap.flush()
    del memmap

    progress = {
        "schema_version": 1,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "dem_path": str(dem_path.resolve()),
        "output_path": str(output_path.resolve()),
        "shape": [height, width],
        "tile_size": int(tile_size),
        "tile_count": len(tiles),
        "workers": int(workers),
        "halo": HALO,
        "cellsize_m": float(cellsize_m),
        "checkpoint_dir": str(checkpoint_dir.resolve()),
    }
    (checkpoint_dir / "slope-progress.json").write_text(
        json.dumps(progress, indent=2, sort_keys=True) + "\n"
    )
    _log(log_path, f"slope merged to {output_path}")
    return progress


def compute_slope_untiled(
    dem_path: Path,
    *,
    window: Window | None = None,
    cellsize_m: float = RESOLUTION_M,
) -> np.ndarray:
    """Serial reference: one Horn pass over a DEM window (or the full DEM)."""
    with rasterio.open(dem_path) as dataset:
        if window is None:
            elev = dataset.read(1).astype(np.float64, copy=False)
        else:
            elev = dataset.read(1, window=window).astype(np.float64, copy=False)
    return horn_slope_deg(elev, cellsize_m)


def project_slope_node_hours(
    *,
    tile_seconds: float,
    pilot_tiles: int,
    full_tiles: int,
    workers: int,
    scoring_seconds: float = 0.0,
    production_node_hours: float,
    stage_node_hour_cap: float,
) -> dict:
    """Project one-node wall time from timed slope tiles plus scoring."""
    if min(pilot_tiles, full_tiles, workers) <= 0:
        raise ValueError("pilot sample sizes must be positive")
    if tile_seconds < 0.0 or scoring_seconds < 0.0:
        raise ValueError("measured durations must be non-negative")
    mean_tile = float(tile_seconds) / float(pilot_tiles)
    waves = math.ceil(full_tiles / workers)
    # Pilot measures wall time for pilot_tiles with the same worker count.
    pilot_waves = math.ceil(pilot_tiles / workers)
    projected_slope = float(tile_seconds) * waves / pilot_waves
    wall_seconds = projected_slope + float(scoring_seconds)
    node_hours = wall_seconds / 3600.0
    return {
        "mean_tile_seconds": mean_tile,
        "pilot_tiles": int(pilot_tiles),
        "full_tiles": int(full_tiles),
        "workers": int(workers),
        "pilot_waves": int(pilot_waves),
        "full_waves": int(waves),
        "projected_slope_seconds": projected_slope,
        "projected_scoring_seconds": float(scoring_seconds),
        "projected_wall_seconds": wall_seconds,
        "projected_node_hours": node_hours,
        "production_node_hours": float(production_node_hours),
        "stage_node_hour_cap": float(stage_node_hour_cap),
        "fits_production_cap": bool(node_hours <= float(production_node_hours)),
        "fits_stage_cap": bool(node_hours <= float(stage_node_hour_cap)),
    }


def _log(log_path: Path | None, message: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} {message}"
    if log_path is None:
        return
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as handle:
        handle.write(line + "\n")
