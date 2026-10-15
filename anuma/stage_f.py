"""Stage F: full-cap 20 m terrain gate (share of flat ground within 1 km).

Implements the plan frozen in ``docs/stage-f-plan.md``. For every 240 m cell
of the common grid it counts, among the 20 m pixels whose centres lie within
1000 m of the 240 m cell centre,

- ``finite``: pixels with a finite Horn slope, and
- ``passing``: pixels with a finite Horn slope <= ``hard.max_slope_deg`` (8°),

and writes ``terrain_1km_fraction = passing / finite`` (float32, NaN outside
the 80°S cap or where ``finite`` is 0) with the two integer counts.

Geometry is exactly Stage D's zero-noise 1 km statistic
(``anuma.stage_d.window_masks`` / ``slope_statistics``): the 240 m grid nests
12 x 12 in the 20 m grid with its origin 4 fine pixels outside, so each
240 m centre lies on a 20 m pixel corner; the circle is the 7,860 fine pixel
centres within 1000 m of it.

Counting is exact integer arithmetic: per fine row, a cumulative sum of the
boolean mask gives every horizontal chord of the disk in O(1), and the 100
chords of the disk are summed at every 240 m centre. No floating-point
convolution is involved, so serial and parallel runs are bit-identical by
construction and are still compared.

Modes:

- ``pilot`` / ``production``: MeluXina, reading the Stage B ``slope-20m.npy``
  (job 5334425) after verifying its digest and the DEM's;
- ``local``: laptop, any 240 m row/column window, Horn slope computed from the
  local 20 m DEM through ``anuma.slope20`` (testing only; never writes a
  full-cap layer);
- ``local-check``: laptop, the frozen acceptance sites from the local DEM.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path
from typing import Sequence

import numpy as np

from anuma.config import load_config, repo_root
from anuma.stage_f_gate import expected_inputs, sha256_file, verify_input_digests

SCHEMA_VERSION = 1
STAGE = "F-terrain-1km-20m"
LAYER = "terrain_1km_fraction"
PASS_COUNT_FILE = "terrain_1km_pass_count.npy"
FINITE_COUNT_FILE = "terrain_1km_finite_count.npy"
FRACTION_FILE = f"{LAYER}.npy"
SIDECAR_FILE = f"{LAYER}.json"
STAGE_B_CHECKS = Path("data") / "checks" / "stage-b-1km.json"
STAGE_D_CHECKS = Path("data") / "checks" / "stage-d-noise.json"

# Frozen in docs/stage-f-plan.md on 2026-10-15, before any Stage F run.
FROZEN = {
    "hard.max_slope_deg": 8.0,
    "hard.terrain_1km.min_fraction": 0.5,
    "hard.terrain_1km.radius_m": 1000.0,
    "hard.terrain_1km.layer": LAYER,
    "hard.terrain_1km.max_slope_deg_from": "hard.max_slope_deg",
    "projection.resolution_m": 240.0,
    "projection.lat_max": -80.0,
}
FINE_M = 20.0
FINE_PER_CELL = 12
CIRCLE_PIXELS = 7860
BAND_ROWS = 32
BRUTE_FORCE_SEED = 20261017
BRUTE_FORCE_SAMPLES = {"pilot": 512, "production": 4096}
# Stage D zero-noise statistic (job 5345050, stage-d-summary.json sha256
# bee287c7...): passing pixels out of 7,860 finite circle pixels. Exact
# integers: fraction_1km * 7860 is an integer for every site.
STAGE_D_ZERO_NOISE_COUNTS = {
    "1318:1219": 3863,
    "1292:1255": 821,
    "1350:1392": 5176,
    "791:1624": 3920,
    "779:1564": 3701,
    "1252:1287": 1638,
    "1277:1142": 3073,
    "1206:1114": 2509,
}
STAGE_D_FRACTION_ATOL = 1.0e-6
STAGE_B_TWO_DECIMAL_ATOL = 0.005 + 1e-12
WORKERS_MAX = 128


# --------------------------------------------------------------------------
# Frozen configuration
# --------------------------------------------------------------------------


def _lookup(tree: dict, dotted: str):
    node = tree
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"config is missing {dotted}")
        node = node[part]
    return node


def require_frozen_config(cfg: dict) -> dict:
    """The config must still hold the plan's values; return the gate spec."""
    for key, expected in FROZEN.items():
        value = _lookup(cfg, key)
        same = (
            math.isclose(float(value), float(expected), rel_tol=0.0, abs_tol=1e-12)
            if isinstance(expected, float)
            else value == expected
        )
        if not same:
            raise ValueError(f"{key} is {value!r}; docs/stage-f-plan.md froze {expected!r}")
    spec = dict(cfg["hard"]["terrain_1km"])
    spec["max_slope_deg"] = float(_lookup(cfg, spec["max_slope_deg_from"]))
    return spec


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DiskKernel:
    """Fine pixels whose centres lie within ``radius_m`` of a pixel corner.

    Offsets are relative to the corner index ``K``: fine pixel ``K + a`` has
    its centre ``(a + 0.5) * cell_m`` from the corner. ``chords`` holds, per
    row offset ``a``, the inclusive column offsets ``(lo, hi)``.
    """

    radius_m: float
    cell_m: float
    half: int
    mask: np.ndarray  # (2 * half, 2 * half) bool; [0, 0] is offset (-half, -half)
    chords: tuple[tuple[int, int, int], ...]

    @property
    def pixels(self) -> int:
        return int(np.count_nonzero(self.mask))


def disk_kernel(radius_m: float, cell_m: float = FINE_M) -> DiskKernel:
    """Same test as ``stage_d.window_masks``: ``hypot(dx, dy) <= radius_m``."""
    if not (math.isfinite(radius_m) and radius_m > 0.0 and cell_m > 0.0):
        raise ValueError("radius and cell size must be finite and positive")
    half = int(math.ceil(float(radius_m) / float(cell_m)))
    offsets = (np.arange(2 * half, dtype=np.float64) - half + 0.5) * float(cell_m)
    dx, dy = np.meshgrid(offsets, offsets)
    mask = np.hypot(dx, dy) <= float(radius_m)
    chords = []
    for k in range(2 * half):
        cols = np.flatnonzero(mask[k])
        if cols.size == 0:
            continue
        if int(cols[-1] - cols[0] + 1) != int(cols.size):
            raise RuntimeError("disk row is not contiguous")
        chords.append((k - half, int(cols[0]) - half, int(cols[-1]) - half))
    return DiskKernel(float(radius_m), float(cell_m), half, mask, tuple(chords))


@dataclass(frozen=True)
class Nesting:
    """Where each 240 m centre sits on the fine grid (a fine pixel corner)."""

    off_row: int  # fine row index of the 240 m grid's top edge
    off_col: int
    per_cell: int
    height: int  # 240 m grid
    width: int

    def corner_rows(self, rows: np.ndarray) -> np.ndarray:
        return self.off_row + self.per_cell * np.asarray(rows, dtype=np.int64) + self.per_cell // 2

    def corner_cols(self, cols: np.ndarray) -> np.ndarray:
        return self.off_col + self.per_cell * np.asarray(cols, dtype=np.int64) + self.per_cell // 2


def nesting_from_transforms(coarse_transform, fine_transform, height: int, width: int) -> Nesting:
    """Refuse anything but north-up, 12 x 12, integer-offset nesting."""
    for name in ("b", "d"):
        if getattr(fine_transform, name) != 0.0 or getattr(coarse_transform, name) != 0.0:
            raise ValueError("grids must be north-up without rotation")
    if abs(fine_transform.a - FINE_M) > 1e-9 or abs(fine_transform.e + FINE_M) > 1e-9:
        raise ValueError("fine grid is not 20 m north-up")
    ratio_x = coarse_transform.a / fine_transform.a
    ratio_y = coarse_transform.e / fine_transform.e
    if abs(ratio_x - FINE_PER_CELL) > 1e-9 or abs(ratio_y - FINE_PER_CELL) > 1e-9:
        raise ValueError("240 m grid is not 12 fine pixels per cell")
    off_col = (coarse_transform.c - fine_transform.c) / fine_transform.a
    off_row = (coarse_transform.f - fine_transform.f) / fine_transform.e
    for value in (off_col, off_row):
        if abs(value - round(value)) > 1e-6:
            raise ValueError(f"240 m grid is offset {value} fine pixels (not an integer)")
    return Nesting(int(round(off_row)), int(round(off_col)), FINE_PER_CELL, int(height), int(width))


# --------------------------------------------------------------------------
# Exact disk counts
# --------------------------------------------------------------------------


def disk_counts(
    finite: np.ndarray,
    passing: np.ndarray,
    *,
    origin_row: int,
    origin_col: int,
    corner_rows: np.ndarray,
    corner_cols: np.ndarray,
    kernel: DiskKernel,
) -> tuple[np.ndarray, np.ndarray]:
    """Exact counts of ``finite`` and ``passing`` pixels in the disk at each centre.

    ``finite`` and ``passing`` are boolean blocks whose ``[0, 0]`` is fine
    pixel ``(origin_row, origin_col)``; they must cover every disk. Returns
    two int32 arrays of shape ``(len(corner_rows), len(corner_cols))``.
    """
    if finite.shape != passing.shape or finite.ndim != 2:
        raise ValueError("finite and passing must be same-shaped 2-D blocks")
    rows = np.asarray(corner_rows, dtype=np.int64) - int(origin_row)
    cols = np.asarray(corner_cols, dtype=np.int64) - int(origin_col)
    h = kernel.half
    if rows.size == 0 or cols.size == 0:
        empty = np.zeros((rows.size, cols.size), dtype=np.int32)
        return empty, empty.copy()
    if rows.min() - h < 0 or rows.max() + h > finite.shape[0]:
        raise ValueError("block does not cover every disk row")
    if cols.min() - h < 0 or cols.max() + h > finite.shape[1]:
        raise ValueError("block does not cover every disk column")
    out = []
    for mask in (finite, passing):
        prefix = np.zeros((mask.shape[0], mask.shape[1] + 1), dtype=np.int32)
        np.cumsum(mask, axis=1, dtype=np.int32, out=prefix[:, 1:])
        total = np.zeros((rows.size, cols.size), dtype=np.int32)
        for a, lo, hi in kernel.chords:
            selected = prefix[rows + a]
            total += selected[:, cols + hi + 1] - selected[:, cols + lo]
        out.append(total)
    return out[0], out[1]


def brute_force_counts(
    slope: np.ndarray, *, origin_row: int, origin_col: int, corner_row: int, corner_col: int,
    kernel: DiskKernel, max_slope_deg: float,
) -> tuple[int, int]:
    """Reference count for one centre by direct masking (tests and spot checks)."""
    h = kernel.half
    r0 = int(corner_row) - h - int(origin_row)
    c0 = int(corner_col) - h - int(origin_col)
    block = slope[r0 : r0 + 2 * h, c0 : c0 + 2 * h]
    finite = np.isfinite(block) & kernel.mask
    passing = finite & (np.where(np.isfinite(block), block, np.inf) <= float(max_slope_deg))
    return int(np.count_nonzero(finite)), int(np.count_nonzero(passing))


def fraction_from_counts(finite: np.ndarray, passing: np.ndarray) -> np.ndarray:
    """``passing / finite`` in float64, cast to float32; NaN where ``finite`` is 0."""
    finite = np.asarray(finite)
    passing = np.asarray(passing)
    with np.errstate(invalid="ignore", divide="ignore"):
        fraction = np.where(finite > 0, passing / np.maximum(finite, 1), np.nan)
    return fraction.astype(np.float32)


# --------------------------------------------------------------------------
# Slope sources
# --------------------------------------------------------------------------


def _padded_slice(array, r0: int, r1: int, c0: int, c1: int) -> np.ndarray:
    """``array[r0:r1, c0:c1]`` as float32, NaN where it leaves the array."""
    out = np.full((r1 - r0, c1 - c0), np.nan, dtype=np.float32)
    sr0, sr1 = max(r0, 0), min(r1, array.shape[0])
    sc0, sc1 = max(c0, 0), min(c1, array.shape[1])
    if sr0 < sr1 and sc0 < sc1:
        out[sr0 - r0 : sr1 - r0, sc0 - c0 : sc1 - c0] = array[sr0:sr1, sc0:sc1]
    return out


@dataclass(frozen=True)
class NpySlope:
    """The Stage B full-cap ``slope-20m.npy``, memory-mapped per read."""

    path: str

    def read(self, r0: int, r1: int, c0: int, c1: int) -> np.ndarray:
        array = np.load(self.path, mmap_mode="r", allow_pickle=False)
        return _padded_slice(array, r0, r1, c0, c1)


@dataclass(frozen=True)
class DemSlope:
    """Horn slope from the 20 m DEM, the Stage B code path (laptop testing)."""

    path: str

    def read(self, r0: int, r1: int, c0: int, c1: int) -> np.ndarray:
        import rasterio

        from anuma.slope20 import HALO, TileSpec, _read_elev_with_halo, slope_tile_from_array

        tile = TileSpec(tile_row=0, tile_col=0, row0=r0, row1=r1, col0=c0, col1=c1)
        with rasterio.open(self.path) as dataset:
            block = _read_elev_with_halo(dataset, tile, halo=HALO)
            nodata = dataset.nodata
        if nodata is not None and np.isfinite(nodata):
            block = np.where(block == nodata, np.nan, block)
        return slope_tile_from_array(block, cellsize_m=FINE_M, halo=HALO)


@dataclass(frozen=True)
class ArraySlope:
    """An in-memory fine slope array (tests)."""

    array: np.ndarray

    def read(self, r0: int, r1: int, c0: int, c1: int) -> np.ndarray:
        return _padded_slice(self.array, r0, r1, c0, c1)


# --------------------------------------------------------------------------
# Bands
# --------------------------------------------------------------------------


def plan_bands(height: int, band_rows: int = BAND_ROWS) -> list[tuple[int, int]]:
    if band_rows <= 0:
        raise ValueError("band_rows must be positive")
    return [(r0, min(r0 + band_rows, height)) for r0 in range(0, height, band_rows)]


def compute_window(
    source,
    nesting: Nesting,
    kernel: DiskKernel,
    *,
    rows: tuple[int, int],
    cols: tuple[int, int],
    max_slope_deg: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Counts for the 240 m cells ``rows[0]:rows[1]`` x ``cols[0]:cols[1]``."""
    corner_rows = nesting.corner_rows(np.arange(rows[0], rows[1]))
    corner_cols = nesting.corner_cols(np.arange(cols[0], cols[1]))
    h = kernel.half
    r0, r1 = int(corner_rows.min()) - h, int(corner_rows.max()) + h
    c0, c1 = int(corner_cols.min()) - h, int(corner_cols.max()) + h
    slope = source.read(r0, r1, c0, c1)
    finite = np.isfinite(slope)
    passing = finite & (np.where(finite, slope, np.inf) <= float(max_slope_deg))
    del slope
    return disk_counts(
        finite,
        passing,
        origin_row=r0,
        origin_col=c0,
        corner_rows=corner_rows,
        corner_cols=corner_cols,
        kernel=kernel,
    )


_WORKER: dict = {}


def _init_worker(source, nesting: Nesting, kernel: DiskKernel, max_slope_deg: float) -> None:
    _WORKER.update(source=source, nesting=nesting, kernel=kernel, max_slope_deg=max_slope_deg)


def _band_task(band: tuple[int, int]) -> tuple[tuple[int, int], np.ndarray, np.ndarray]:
    finite, passing = compute_window(
        _WORKER["source"],
        _WORKER["nesting"],
        _WORKER["kernel"],
        rows=band,
        cols=(0, _WORKER["nesting"].width),
        max_slope_deg=_WORKER["max_slope_deg"],
    )
    return band, finite, passing


def compute_bands(
    source,
    nesting: Nesting,
    kernel: DiskKernel,
    bands: Sequence[tuple[int, int]],
    *,
    max_slope_deg: float,
    workers: int = 1,
) -> dict[tuple[int, int], tuple[np.ndarray, np.ndarray]]:
    """Full-width counts for each band; ``workers=1`` is the serial reference."""
    if workers <= 0 or workers > WORKERS_MAX:
        raise ValueError(f"workers must be between 1 and {WORKERS_MAX}")
    results: dict = {}
    if workers == 1:
        _init_worker(source, nesting, kernel, max_slope_deg)
        try:
            for band in bands:
                key, finite, passing = _band_task(tuple(band))
                results[key] = (finite, passing)
        finally:
            _WORKER.clear()
        return results
    with ProcessPoolExecutor(
        max_workers=min(int(workers), len(bands)) or 1,
        mp_context=get_context("fork"),
        initializer=_init_worker,
        initargs=(source, nesting, kernel, max_slope_deg),
    ) as pool:
        for key, finite, passing in pool.map(_band_task, [tuple(b) for b in bands]):
            results[key] = (finite, passing)
    return results


def assemble(
    results: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]], height: int, width: int
) -> tuple[np.ndarray, np.ndarray]:
    finite = np.zeros((height, width), dtype=np.int16)
    passing = np.zeros((height, width), dtype=np.int16)
    covered = np.zeros(height, dtype=bool)
    for (r0, r1), (f, p) in results.items():
        if f.max(initial=0) > np.iinfo(np.int16).max:
            raise ValueError("count exceeds int16")
        finite[r0:r1] = f
        passing[r0:r1] = p
        covered[r0:r1] = True
    if not covered.all():
        raise ValueError("bands do not cover the grid")
    return finite, passing


def results_identical(first: dict, second: dict) -> bool:
    if set(first) != set(second):
        return False
    return all(
        np.array_equal(first[k][0], second[k][0]) and np.array_equal(first[k][1], second[k][1])
        for k in first
    )


# --------------------------------------------------------------------------
# Acceptance
# --------------------------------------------------------------------------


def load_stage_b_table(path: Path | None = None) -> list[dict]:
    path = Path(path) if path else repo_root() / STAGE_B_CHECKS
    payload = json.loads(path.read_text())
    return [
        {
            "site_id": f"{int(s['row'])}:{int(s['col'])}",
            "rank": int(s["rank_240m"]),
            "row": int(s["row"]),
            "col": int(s["col"]),
            "within_1km": float(s["fraction_le_8deg"]["within_1km"]),
        }
        for s in payload["sites"]
    ]


def load_stage_d_table(path: Path | None = None) -> list[dict]:
    path = Path(path) if path else repo_root() / STAGE_D_CHECKS
    payload = json.loads(path.read_text())
    return [
        {
            "site_id": str(s["site_id"]),
            "row": int(s["row"]),
            "col": int(s["col"]),
            "zero_noise_within_1km": float(s["zero_noise_within_1km"]),
        }
        for s in payload["sites"]
    ]


def acceptance_sites() -> list[tuple[int, int]]:
    cells = {(s["row"], s["col"]) for s in load_stage_b_table()}
    cells |= {(s["row"], s["col"]) for s in load_stage_d_table()}
    return sorted(cells)


def pilot_bands(height: int, band_rows: int = BAND_ROWS) -> list[tuple[int, int]]:
    """The bands that hold every acceptance site (frozen pilot subset)."""
    bands = plan_bands(height, band_rows)
    rows = {row for row, _ in acceptance_sites()}
    return [band for band in bands if any(band[0] <= r < band[1] for r in rows)]


def acceptance_checks(lookup) -> dict:
    """``lookup(row, col)`` returns ``(finite, passing)`` counts for one cell."""
    stage_b = []
    for site in load_stage_b_table():
        finite, passing = lookup(site["row"], site["col"])
        fraction = float(fraction_from_counts(np.array(finite), np.array(passing)))
        stage_b.append(
            {
                **site,
                "finite": int(finite),
                "passing": int(passing),
                "fraction": fraction,
                "matches_two_decimals": bool(
                    abs(fraction - site["within_1km"]) <= STAGE_B_TWO_DECIMAL_ATOL
                ),
            }
        )
    stage_d = []
    for site in load_stage_d_table():
        finite, passing = lookup(site["row"], site["col"])
        expected = STAGE_D_ZERO_NOISE_COUNTS[site["site_id"]]
        fraction = float(fraction_from_counts(np.array(finite), np.array(passing)))
        exact = expected / CIRCLE_PIXELS
        stage_d.append(
            {
                **site,
                "finite": int(finite),
                "passing": int(passing),
                "expected_passing": expected,
                "expected_finite": CIRCLE_PIXELS,
                "fraction": fraction,
                "abs_diff_vs_stage_d": abs(fraction - exact),
                "counts_equal": bool(int(finite) == CIRCLE_PIXELS and int(passing) == expected),
                "within_1e-6": bool(abs(fraction - exact) <= STAGE_D_FRACTION_ATOL),
            }
        )
    return {
        "stage_b_ranks_1_10": stage_b,
        "stage_b_reproduced": all(s["matches_two_decimals"] for s in stage_b),
        "stage_d_zero_noise": stage_d,
        "stage_d_reproduced": all(s["counts_equal"] and s["within_1e-6"] for s in stage_d),
    }


def stage_d_path_check(dem_path: Path, coarse_transform, lookup, max_slope_deg: float) -> dict:
    """Recompute each acceptance site with Stage D's own code from the DEM.

    Reads a small DEM window (``stage_d.read_window``), takes the Horn slope
    (``slope_tile_from_array``), builds the circle with ``window_masks`` and
    counts with ``slope_statistics``; the result must equal ``lookup``.
    """
    import rasterio

    from anuma import stage_d
    from anuma.slope20 import HALO, slope_tile_from_array

    rows = []
    with rasterio.open(dem_path) as dataset:
        for row, col in acceptance_sites():
            window = stage_d.site_window(
                site_id=f"{row}:{col}", site_index=0, default_rank=0, row_240=row, col_240=col,
                coarse_transform=coarse_transform, fine_transform=dataset.transform,
                half_size_px=60,
            )
            slope = slope_tile_from_array(
                stage_d.read_window(dataset, window, halo=HALO), cellsize_m=FINE_M, halo=HALO
            )
            circle, cell = stage_d.window_masks(
                window, dataset.transform, radius_m=1000.0, cell_size_m=abs(coarse_transform.a)
            )
            stats = stage_d.slope_statistics(
                slope, circle, cell, max_slope_deg=max_slope_deg, cell_m=FINE_M
            )
            finite, passing = lookup(row, col)
            # Count directly with Stage D's circle and limit; the fraction
            # from slope_statistics is checked against the same counts.
            ok = np.isfinite(slope) & circle
            expected_passing = int(
                np.count_nonzero(ok & (np.where(ok, slope, np.inf) <= float(max_slope_deg)))
            )
            if int(np.count_nonzero(ok)) != stats["circle_finite_pixels"]:
                raise RuntimeError("direct circle count disagrees with slope_statistics")
            rows.append(
                {
                    "site_id": f"{row}:{col}",
                    "stage_d_path": {"finite": stats["circle_finite_pixels"], "passing": expected_passing},
                    "stage_f": {"finite": int(finite), "passing": int(passing)},
                    "circle_pixels": int(np.count_nonzero(circle)),
                    "equal": bool(
                        int(finite) == stats["circle_finite_pixels"]
                        and int(passing) == expected_passing
                    ),
                }
            )
    return {"sites": rows, "all_equal": all(r["equal"] for r in rows)}


def brute_force_check(
    source, nesting: Nesting, kernel: DiskKernel, finite: np.ndarray, passing: np.ndarray,
    *, cells: Sequence[tuple[int, int]], max_slope_deg: float, fine_transform, coarse_transform,
) -> dict:
    """Sampled cells counted with Stage D's ``window_masks`` circle on the slope."""
    from anuma import stage_d

    mismatches = []
    for row, col in cells:
        window = stage_d.site_window(
            site_id=f"{row}:{col}", site_index=0, default_rank=0, row_240=int(row),
            col_240=int(col), coarse_transform=coarse_transform, fine_transform=fine_transform,
            half_size_px=kernel.half + 1,
        )
        slope = source.read(window.row0, window.row0 + window.size, window.col0, window.col0 + window.size)
        circle, _ = stage_d.window_masks(
            window, fine_transform, radius_m=kernel.radius_m, cell_size_m=abs(coarse_transform.a)
        )
        ok = np.isfinite(slope) & circle
        n_finite = int(np.count_nonzero(ok))
        n_pass = int(np.count_nonzero(ok & (np.where(ok, slope, np.inf) <= float(max_slope_deg))))
        if n_finite != int(finite[row, col]) or n_pass != int(passing[row, col]):
            mismatches.append(
                {"cell": [int(row), int(col)], "brute": [n_finite, n_pass],
                 "stage_f": [int(finite[row, col]), int(passing[row, col])]}
            )
    return {"samples": len(cells), "mismatches": mismatches[:20], "all_equal": not mismatches}


def sample_cells(bands: Sequence[tuple[int, int]], width: int, n: int, seed: int) -> list[tuple[int, int]]:
    rng = np.random.default_rng(int(seed))
    rows = np.concatenate([np.arange(r0, r1) for r0, r1 in bands])
    picked_rows = rng.choice(rows, size=int(n), replace=True)
    picked_cols = rng.integers(0, int(width), size=int(n))
    return [(int(r), int(c)) for r, c in zip(picked_rows, picked_cols)]


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------


def _log(log_path: Path | None, message: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} {message}"
    print(line, flush=True)
    if log_path is None:
        return
    with Path(log_path).open("a") as handle:
        handle.write(line + "\n")


def _atomic_json(path: Path, payload: dict) -> None:
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=True) + "\n")
    os.replace(temporary, path)


def _atomic_npy(path: Path, array: np.ndarray) -> None:
    path = Path(path)
    temporary = path.with_name(path.stem + ".tmp.npy")
    np.save(temporary, array, allow_pickle=False)
    os.replace(temporary, path)


def _software() -> dict:
    import rasterio
    import scipy

    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "rasterio": rasterio.__version__,
        "config_layers_sha256": sha256_file(repo_root() / "config" / "layers.yaml"),
        "stage_b_checks_sha256": sha256_file(repo_root() / STAGE_B_CHECKS),
        "stage_d_checks_sha256": sha256_file(repo_root() / STAGE_D_CHECKS),
    }


def _slurm_metadata() -> dict:
    keys = (
        "SLURM_JOB_ID", "SLURM_JOB_ACCOUNT", "SLURM_JOB_PARTITION", "SLURM_JOB_QOS",
        "SLURM_CPUS_PER_TASK", "SLURM_JOB_NUM_NODES", "SLURM_MEM_PER_NODE",
    )
    return {key: os.environ[key] for key in keys if key in os.environ}


def layer_stats(fraction: np.ndarray, finite: np.ndarray, inside: np.ndarray, min_fraction: float) -> dict:
    values = fraction[np.isfinite(fraction)]
    return {
        "cells_in_cap": int(np.count_nonzero(inside)),
        "cells_with_fraction": int(values.size),
        "cells_in_cap_without_data": int(np.count_nonzero(inside & ~np.isfinite(fraction))),
        "cells_in_cap_partial_circle": int(
            np.count_nonzero(inside & (finite > 0) & (finite < CIRCLE_PIXELS))
        ),
        "cells_passing_gate": int(np.count_nonzero(values >= float(min_fraction))),
        "share_passing_gate": float(np.mean(values >= float(min_fraction))) if values.size else float("nan"),
        "fraction_min": float(values.min()) if values.size else float("nan"),
        "fraction_median": float(np.median(values)) if values.size else float("nan"),
        "fraction_max": float(values.max()) if values.size else float("nan"),
    }


def sidecar(
    *, spec: dict, grid, coverage: str, inputs: dict, stats: dict, job: str | None
) -> dict:
    from anuma.dem_error import grid_sha256

    return {
        "schema_version": SCHEMA_VERSION,
        "layer": LAYER,
        "stage": STAGE,
        "plan": "docs/stage-f-plan.md",
        "coverage": coverage,
        "max_slope_deg": float(spec["max_slope_deg"]),
        "radius_m": float(spec["radius_m"]),
        "gate_min_fraction_at_build": float(spec["min_fraction"]),
        "circle_pixels": CIRCLE_PIXELS,
        "slope": "Horn 3x3 at 20 m (Stage B slope-20m.npy)",
        "centre": "240 m cell centre, on a 20 m pixel corner (Stage D convention)",
        "denominator": "finite-slope 20 m pixels in the circle; NaN if none",
        "outside_cap": "NaN (latitude above 80 S)",
        "grid_sha256": grid_sha256(grid),
        "shape": [int(grid.height), int(grid.width)],
        "inputs": inputs,
        "job": job,
        "stats": stats,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def validate_sidecar(meta: dict, grid, cfg: dict, npy_path: Path | None = None) -> str | None:
    """Why a fraction layer cannot be used as the gate, or None if it can.

    Only a layer from a pinned MeluXina run qualifies: the sidecar must name
    the pinned Stage B slope and DEM digests and a Slurm job id, and, when
    ``npy_path`` is given, that file's sha256 must equal the sidecar's
    ``fraction_sha256``.
    """
    from anuma.dem_error import grid_sha256
    from anuma.stage_f_gate import DEM_SHA256, SLOPE_20M_SHA256

    spec = cfg["hard"]["terrain_1km"]
    if meta.get("layer") != LAYER:
        return "sidecar is not for terrain_1km_fraction"
    if meta.get("coverage") != "full_cap":
        return f"layer covers {meta.get('coverage')!r}, not the full cap"
    inputs = meta.get("inputs") or {}
    if (inputs.get("stage_b_slope") or {}).get("sha256") != SLOPE_20M_SHA256:
        return "layer was not built from the pinned Stage B slope-20m.npy"
    if (inputs.get("dem") or {}).get("sha256") != DEM_SHA256:
        return "layer was not built with the pinned 20 m DEM"
    if not str(meta.get("job") or "").strip():
        return "sidecar has no Slurm job id (not a MeluXina run)"
    digest = meta.get("fraction_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        return "sidecar lacks fraction_sha256"
    if npy_path is not None and sha256_file(Path(npy_path)) != digest:
        return "terrain_1km_fraction.npy does not match the sidecar's fraction_sha256"
    if meta.get("grid_sha256") != grid_sha256(grid):
        return "layer was built for a different 240 m grid"
    try:
        radius = float(meta["radius_m"])
        slope = float(meta["max_slope_deg"])
    except (KeyError, TypeError, ValueError):
        return "sidecar lacks radius_m or max_slope_deg"
    if not math.isclose(radius, float(spec["radius_m"]), abs_tol=1e-9):
        return f"layer radius {radius} m differs from config {spec['radius_m']} m"
    if not math.isfinite(slope):
        return "sidecar max_slope_deg is not finite"
    return None


# --------------------------------------------------------------------------
# Drivers
# --------------------------------------------------------------------------


def run_local(
    *, dem: Path, rows: tuple[int, int], cols: tuple[int, int], output_dir: Path | None
) -> dict:
    """Laptop: counts for a 240 m window from the local DEM (testing only)."""
    import rasterio

    from anuma.grid import Grid

    cfg = load_config()
    spec = require_frozen_config(cfg)
    grid = Grid(cfg)
    with rasterio.open(dem) as dataset:
        nesting = nesting_from_transforms(grid.affine, dataset.transform, grid.height, grid.width)
    kernel = disk_kernel(float(spec["radius_m"]))
    started = time.perf_counter()
    finite, passing = compute_window(
        DemSlope(str(dem)), nesting, kernel, rows=rows, cols=cols,
        max_slope_deg=float(spec["max_slope_deg"]),
    )
    seconds = time.perf_counter() - started
    fraction = fraction_from_counts(finite, passing)
    report = {
        "mode": "local",
        "coverage": "window",
        "rows": list(rows),
        "cols": list(cols),
        "seconds": seconds,
        "cells": int(fraction.size),
        "share_passing_gate": float(np.nanmean(fraction >= float(spec["min_fraction"]))),
    }
    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        _atomic_npy(output_dir / f"{LAYER}-window.npy", fraction)
        _atomic_npy(output_dir / f"terrain_1km_finite_count-window.npy", finite.astype(np.int16))
        _atomic_npy(output_dir / f"terrain_1km_pass_count-window.npy", passing.astype(np.int16))
        _atomic_json(output_dir / "local-window.json", report)
    return report


def run_local_check(*, dem: Path) -> dict:
    """Laptop: acceptance values (Stage B 1-10, Stage D 8) from the local DEM."""
    import rasterio

    from anuma.grid import Grid

    cfg = load_config()
    spec = require_frozen_config(cfg)
    grid = Grid(cfg)
    with rasterio.open(dem) as dataset:
        nesting = nesting_from_transforms(grid.affine, dataset.transform, grid.height, grid.width)
    kernel = disk_kernel(float(spec["radius_m"]))
    source = DemSlope(str(dem))
    max_slope = float(spec["max_slope_deg"])

    def lookup(row: int, col: int) -> tuple[int, int]:
        f, p = compute_window(
            source, nesting, kernel, rows=(row, row + 1), cols=(col, col + 1), max_slope_deg=max_slope
        )
        return int(f[0, 0]), int(p[0, 0])

    checks = acceptance_checks(lookup)
    checks["stage_d_path"] = stage_d_path_check(Path(dem), grid.affine, lookup, max_slope)
    checks["passed"] = bool(
        checks["stage_b_reproduced"] and checks["stage_d_reproduced"]
        and checks["stage_d_path"]["all_equal"]
    )
    return checks


def project_production(
    *, fixed_seconds: float, check_seconds: float, serial_band_seconds: float,
    pilot_band_count: int, production_band_count: int, workers: int,
    walltime_hours: float, node_hours: float,
) -> dict:
    """Conservative production bound from the pilot.

    Fixed work (hashing, opening) repeats once. Parallel bands are charged at
    50 % efficiency, never less than one band. Production repeats the serial
    reference on the pilot bands, and its brute-force sample is 8x the
    pilot's, so the pilot's check time is scaled by 8. 120 s for writes.
    """
    per_band = float(serial_band_seconds) / max(1, int(pilot_band_count))
    parallel = max(
        per_band,
        2.0 * per_band * production_band_count / max(1, min(workers, production_band_count)),
    )
    serial_reference = per_band * pilot_band_count
    scale = BRUTE_FORCE_SAMPLES["production"] / BRUTE_FORCE_SAMPLES["pilot"]
    checks = float(check_seconds) * scale
    wall = float(fixed_seconds) + parallel + serial_reference + checks + 120.0
    hours = wall / 3600.0
    return {
        "pilot_seconds_per_band": per_band,
        "production_bands": int(production_band_count),
        "projected_fixed_seconds": float(fixed_seconds),
        "projected_parallel_seconds": parallel,
        "projected_serial_reference_seconds": serial_reference,
        "projected_check_seconds": checks,
        "projected_wall_seconds": wall,
        "projected_node_hours": hours,
        "production_walltime_hours": float(walltime_hours),
        "production_node_hours": float(node_hours),
        "fits_walltime": bool(hours <= float(walltime_hours)),
        "fits_node_hours": bool(hours <= float(node_hours)),
        "method": (
            "fixed + max(per-band, 2 x per-band x bands / workers) + serial reference "
            "+ 8 x pilot check time + 120 s writes"
        ),
    }


def run_stage_f(
    *, mode: str, dem: Path, stage_b_slope: Path, output_dir: Path, workers: int,
) -> dict:
    """Pilot or production on MeluXina. Refuses before computing on any bad pin."""
    import rasterio

    from anuma import stage_d
    from anuma.grid import Grid
    from anuma.ingest import mask_outside_cap
    from anuma.stage_f_gate import NODE_HOURS, WALLTIME

    if mode not in ("pilot", "production"):
        raise ValueError("mode must be pilot or production")
    if workers <= 0 or workers > WORKERS_MAX:
        raise ValueError(f"workers must be between 1 and {WORKERS_MAX}")
    started_utc = datetime.now(timezone.utc).isoformat()
    clock = time.perf_counter()
    cfg = load_config()
    spec = require_frozen_config(cfg)
    max_slope = float(spec["max_slope_deg"])
    inputs = expected_inputs(dem=dem, stage_b_slope=stage_b_slope)
    output_dir = Path(output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "progress.log"

    digests = verify_input_digests(inputs)
    _log(log_path, f"verified {len(digests)} input digests")
    grid = Grid(cfg)
    with rasterio.open(inputs["dem"]["path"]) as dataset:
        fine_transform = dataset.transform
        dem_shape = (int(dataset.height), int(dataset.width))
        projection = stage_d.require_same_projection(dataset.crs, grid.proj4)
    nesting = nesting_from_transforms(grid.affine, fine_transform, grid.height, grid.width)
    slope_path = Path(inputs["stage_b_slope"]["path"])
    slope = np.load(slope_path, mmap_mode="r", allow_pickle=False)
    if slope.shape != dem_shape or slope.dtype != np.float32:
        raise ValueError(f"slope-20m.npy is {slope.shape} {slope.dtype}, DEM is {dem_shape}")
    del slope
    kernel = disk_kernel(float(spec["radius_m"]))
    if kernel.pixels != CIRCLE_PIXELS:
        raise ValueError(f"circle has {kernel.pixels} pixels, plan says {CIRCLE_PIXELS}")
    source = NpySlope(str(slope_path))
    all_bands = plan_bands(grid.height)
    pilot = pilot_bands(grid.height)
    fixed_seconds = time.perf_counter() - clock
    _log(log_path, f"inputs ready in {fixed_seconds:.1f} s; offset {nesting.off_row},{nesting.off_col}")

    timings: dict = {"fixed": fixed_seconds}
    t0 = time.perf_counter()
    serial = compute_bands(source, nesting, kernel, pilot, max_slope_deg=max_slope, workers=1)
    timings["serial_pilot_bands"] = time.perf_counter() - t0
    _log(log_path, f"serial: {len(pilot)} bands in {timings['serial_pilot_bands']:.1f} s")
    t0 = time.perf_counter()
    bands = pilot if mode == "pilot" else all_bands
    parallel = compute_bands(source, nesting, kernel, bands, max_slope_deg=max_slope, workers=workers)
    timings["parallel"] = time.perf_counter() - t0
    _log(log_path, f"parallel: {len(bands)} bands, {workers} workers, {timings['parallel']:.1f} s")
    identical = results_identical(serial, {k: parallel[k] for k in serial})

    finite = np.zeros((grid.height, grid.width), dtype=np.int16)
    passing = np.zeros((grid.height, grid.width), dtype=np.int16)
    if mode == "production":
        finite, passing = assemble(parallel, grid.height, grid.width)
    else:
        for (r0, r1), (f, p) in parallel.items():
            finite[r0:r1] = f
            passing[r0:r1] = p

    def lookup(row: int, col: int) -> tuple[int, int]:
        return int(finite[row, col]), int(passing[row, col])

    t0 = time.perf_counter()
    checks = acceptance_checks(lookup)
    checks["stage_d_path"] = stage_d_path_check(
        Path(inputs["dem"]["path"]), grid.affine, lookup, max_slope
    )
    cells = sample_cells(bands, grid.width, BRUTE_FORCE_SAMPLES[mode], BRUTE_FORCE_SEED)
    checks["brute_force"] = brute_force_check(
        source, nesting, kernel, finite, passing, cells=cells, max_slope_deg=max_slope,
        fine_transform=fine_transform, coarse_transform=grid.affine,
    )
    checks["serial_parallel_identical"] = bool(identical)
    checks["circle_pixels"] = kernel.pixels
    timings["checks"] = time.perf_counter() - t0
    _log(log_path, f"checks done in {timings['checks']:.1f} s")
    passed = bool(
        identical and checks["stage_b_reproduced"] and checks["stage_d_reproduced"]
        and checks["stage_d_path"]["all_equal"] and checks["brute_force"]["all_equal"]
    )
    inputs_report = {
        name: {"path": str(Path(e["path"]).resolve()), "sha256": e["sha256"]}
        for name, e in inputs.items()
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "kind": "stage_f_terrain_1km",
        "plan": "docs/stage-f-plan.md",
        "mode": mode,
        "started_at_utc": started_utc,
        "inputs": inputs_report,
        "projection_check": projection,
        "nesting": {"off_row": nesting.off_row, "off_col": nesting.off_col, "per_cell": FINE_PER_CELL},
        "gate": {k: spec[k] for k in ("min_fraction", "radius_m", "max_slope_deg", "layer")},
        "bands": {"band_rows": BAND_ROWS, "total": len(all_bands), "pilot": [list(b) for b in pilot]},
        "workers": int(workers),
        "execution": _slurm_metadata(),
        "software": _software(),
        "checks": checks,
        "timings_seconds": timings,
    }
    written: list[Path] = []
    if mode == "pilot":
        projection_report = project_production(
            fixed_seconds=fixed_seconds,
            check_seconds=timings["checks"],
            serial_band_seconds=timings["serial_pilot_bands"],
            pilot_band_count=len(pilot),
            production_band_count=len(all_bands),
            workers=workers,
            walltime_hours=_hours(WALLTIME["production"]),
            node_hours=NODE_HOURS["production"],
        )
        report["projection"] = projection_report
        checks["projection_fits"] = bool(
            projection_report["fits_walltime"] and projection_report["fits_node_hours"]
        )
        passed = passed and checks["projection_fits"]
        report["passed"] = passed
        report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        path = output_dir / "pilot-measurement.json"
        _atomic_json(path, report)
        written.append(path)
    else:
        inside = grid.inside_latitude(float(cfg["projection"]["lat_max"]))
        fraction = mask_outside_cap(fraction_from_counts(finite, passing), grid, float(cfg["projection"]["lat_max"]))
        stats = layer_stats(fraction, finite, inside, float(spec["min_fraction"]))
        report["layer_stats"] = stats
        report["passed"] = passed
        job = os.environ.get("SLURM_JOB_ID")
        for name, array in (
            (FRACTION_FILE, fraction), (FINITE_COUNT_FILE, finite), (PASS_COUNT_FILE, passing),
        ):
            _atomic_npy(output_dir / name, array)
            written.append(output_dir / name)
        meta = sidecar(
            spec=spec, grid=grid, coverage="full_cap" if passed else "failed_acceptance",
            inputs=inputs_report, stats=stats, job=job,
        )
        meta["fraction_sha256"] = sha256_file(output_dir / FRACTION_FILE)
        _atomic_json(output_dir / SIDECAR_FILE, meta)
        written.append(output_dir / SIDECAR_FILE)
        report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        _atomic_json(output_dir / "stage-f-summary.json", report)
        written.append(output_dir / "stage-f-summary.json")
    outputs = {
        str(p.relative_to(output_dir)): {"sha256": sha256_file(p), "bytes": p.stat().st_size}
        for p in written
    }
    _atomic_json(output_dir / "output-manifest.json", {**report, "outputs": outputs})
    _log(log_path, f"{mode} written; passed={report['passed']}")
    return report


def _hours(walltime: str) -> float:
    h, m, s = (int(v) for v in walltime.split(":"))
    return h + m / 60.0 + s / 3600.0


def _span(text: str) -> tuple[int, int]:
    a, b = (int(v) for v in text.split(":"))
    if b <= a:
        raise argparse.ArgumentTypeError("span must be start:end with end > start")
    return a, b


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("pilot", "production", "local", "local-check"), required=True)
    parser.add_argument("--dem", type=Path, required=True)
    parser.add_argument("--stage-b-slope", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--rows", type=_span, default=None, help="local: 240 m rows start:end")
    parser.add_argument("--cols", type=_span, default=None, help="local: 240 m cols start:end")
    args = parser.parse_args(argv)
    try:
        if args.mode == "local":
            if args.rows is None or args.cols is None:
                parser.error("--rows and --cols are required for local mode")
            report = run_local(dem=args.dem, rows=args.rows, cols=args.cols, output_dir=args.output_dir)
        elif args.mode == "local-check":
            report = run_local_check(dem=args.dem)
        else:
            for name in ("stage_b_slope", "output_dir"):
                if getattr(args, name) is None:
                    parser.error(f"--{name.replace('_', '-')} is required for {args.mode}")
            report = run_stage_f(
                mode=args.mode, dem=args.dem, stage_b_slope=args.stage_b_slope,
                output_dir=args.output_dir, workers=args.workers,
            )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Stage F precondition failed: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    if args.mode == "pilot" and not report["checks"].get("projection_fits", False):
        raise SystemExit(4)
    if args.mode in ("pilot", "production", "local-check") and not report["passed"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
