"""Mean LOLA height error per 240 m cell. Display only, not scored.

Aggregates PGDA product 90 `LDEM_80S_20MPP_ADJ_ERR.TIF` (20 m per-pixel
height error in metres, Barker et al. 2023) onto the common grid. The 20 m
grid nests in the 240 m grid, so each cell is the exact mean of its finite
12 x 12 fine pixels (block mean, the nested case of average resampling).
Source nodata becomes NaN, and a cell with no finite fine pixel is NaN.
The source is read in bands of grid rows, so the 3.7 GB raster is never held
in memory. GDAL's WarpedVRT average is used only for an unnested source
(`--allow-unnested`); it mis-weights cells the source covers only in part.

    python -m anuma.dem_error --src data/raw/LDEM_80S_20MPP_ADJ_ERR.TIF \
        --out data/grid/dem_error.npy

A sidecar `<out>.json` records the source sha256, resampling, stats and grid.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

from anuma.config import load_config, repo_root
from anuma.grid import Grid
from anuma.ingest import FILL, mask_outside_cap
from anuma.stage_d import require_same_projection

LAYER = "dem_error"
FINE_PER_CELL = 12


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def grid_sha256(grid: Grid) -> str:
    """Identity of the 240 m grid: sha256 of its canonical grid.json fields."""
    text = json.dumps(grid.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def nesting(fine_transform, grid: Grid) -> dict:
    """How the 20 m pixels sit inside the 240 m cells."""
    coarse = grid.affine
    ratio_x = coarse.a / fine_transform.a
    ratio_y = coarse.e / fine_transform.e
    off_col = (coarse.c - fine_transform.c) / fine_transform.a
    off_row = (coarse.f - fine_transform.f) / fine_transform.e
    nested = (
        fine_transform.b == 0.0
        and fine_transform.d == 0.0
        and abs(ratio_x - FINE_PER_CELL) < 1e-9
        and abs(ratio_y - FINE_PER_CELL) < 1e-9
        and abs(off_col - round(off_col)) < 1e-6
        and abs(off_row - round(off_row)) < 1e-6
    )
    return {
        "nested": bool(nested),
        "fine_pixels_per_cell": [float(ratio_y), float(ratio_x)],
        "grid_origin_in_fine_pixels": [float(off_row), float(off_col)],
    }


def _block_mean(src, grid: Grid, nodata, origin: tuple[int, int], band_rows: int) -> np.ndarray:
    """Exact mean of the finite fine pixels in each cell of a nested grid."""
    off_row, off_col = origin
    n = FINE_PER_CELL
    out = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
    # Fine columns covered by the grid, clipped to the source.
    c0, c1 = off_col, off_col + n * grid.width
    sc0, sc1 = max(c0, 0), min(c1, src.width)
    for r0 in range(0, grid.height, band_rows):
        r1 = min(r0 + band_rows, grid.height)
        f0, f1 = off_row + n * r0, off_row + n * r1
        sf0, sf1 = max(f0, 0), min(f1, src.height)
        if sf0 >= sf1 or sc0 >= sc1:
            continue
        block = np.full((f1 - f0, c1 - c0), np.nan, dtype=np.float32)
        window = Window(sc0, sf0, sc1 - sc0, sf1 - sf0)
        part = src.read(1, window=window, out_dtype="float32")
        if nodata is not None and not np.isnan(nodata):
            part[part == np.float32(nodata)] = np.nan
        block[sf0 - f0 : sf1 - f0, sc0 - c0 : sc1 - c0] = part
        del part
        cells = block.reshape(r1 - r0, n, grid.width, n)
        finite = np.isfinite(cells)
        count = finite.sum(axis=(1, 3))
        total = np.where(finite, cells, 0.0).sum(axis=(1, 3), dtype=np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[r0:r1] = np.where(count > 0, total / count, np.nan).astype(np.float32)
    return out


def _warp_average(src, grid: Grid, nodata, warp_mem_limit_mb: int, num_threads: str) -> np.ndarray:
    with WarpedVRT(
        src,
        crs=grid.crs,
        transform=grid.affine,
        width=grid.width,
        height=grid.height,
        resampling=Resampling.average,
        src_nodata=nodata,
        nodata=FILL,
        dtype="float32",
        warp_mem_limit=int(warp_mem_limit_mb),
        warp_extras={"NUM_THREADS": str(num_threads)},
    ) as vrt:
        data = vrt.read(1).astype(np.float32, copy=False)
    data[data == np.float32(FILL)] = np.nan
    return data


def aggregate(
    src_path: Path,
    grid: Grid,
    *,
    src_nodata: float | None = None,
    allow_unnested: bool = False,
    band_rows: int = 64,
    warp_mem_limit_mb: int = 1024,
    num_threads: str = "ALL_CPUS",
) -> tuple[np.ndarray, dict]:
    """Mean of the fine pixels in each grid cell, float32, nodata as NaN."""
    with rasterio.open(src_path) as src:
        if src.crs is None:
            raise ValueError(f"{src_path} has no CRS")
        projection = require_same_projection(src.crs, grid.proj4)
        nest = nesting(src.transform, grid)
        nodata = src.nodata if src_nodata is None else src_nodata
        if nest["nested"]:
            origin = tuple(int(round(v)) for v in nest["grid_origin_in_fine_pixels"])
            data = _block_mean(src, grid, nodata, origin, max(1, int(band_rows)))
            resampling = "block_mean"
        elif allow_unnested:
            data = _warp_average(src, grid, nodata, warp_mem_limit_mb, num_threads)
            resampling = "gdal_average"
        else:
            raise ValueError(
                f"{src_path} is not nested {FINE_PER_CELL} x {FINE_PER_CELL} in the "
                f"240 m grid: {nest}"
            )
        info = {
            "resampling": resampling,
            "source_shape": [src.height, src.width],
            "source_transform": list(src.transform)[:6],
            "source_nodata": None if nodata is None else float(nodata),
            "projection_check": projection,
            **nest,
        }
    # NaN inside a float source is not a value either.
    data[~np.isfinite(data)] = np.nan
    return data, info


def stats(array: np.ndarray) -> dict:
    finite = array[np.isfinite(array)]
    if finite.size == 0:
        return {"finite_cells": 0, "min": None, "median": None, "p95": None, "max": None, "mean": None}
    return {
        "finite_cells": int(finite.size),
        "min": float(finite.min()),
        "median": float(np.median(finite)),
        "p95": float(np.percentile(finite, 95)),
        "max": float(finite.max()),
        "mean": float(finite.mean()),
    }


def run(
    src: Path,
    out: Path,
    cfg: dict,
    *,
    hash_source: bool = True,
    src_nodata: float | None = None,
    allow_unnested: bool = False,
    warp_mem_limit_mb: int = 1024,
    num_threads: str = "ALL_CPUS",
) -> dict:
    grid = Grid(cfg)
    grid_json = out.parent / "grid.json"
    if grid_json.is_file():
        Grid.from_json(grid_json, cfg)  # refuses a grid that differs from config
    array, info = aggregate(
        src,
        grid,
        src_nodata=src_nodata,
        allow_unnested=allow_unnested,
        warp_mem_limit_mb=warp_mem_limit_mb,
        num_threads=num_threads,
    )
    lat_max = float(cfg["projection"]["lat_max"])
    array = mask_outside_cap(array, grid, lat_max)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out, array)
    meta = cfg["layers"][LAYER]
    digest = _sha256(src) if hash_source else None
    expected = meta.get("sha256")
    matches = None if digest is None or expected is None else digest == expected
    if matches is False:
        print(
            f"WARNING: {src.name} sha256 {digest} differs from config {expected}",
            file=sys.stderr,
        )
    sidecar = {
        "schema_version": 1,
        "layer": LAYER,
        "display_only": True,
        "unit": meta.get("unit"),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "filename": src.name,
            "bytes": src.stat().st_size,
            "sha256": digest,
            "sha256_skipped": not hash_source,
            "expected_sha256": expected,
            "sha256_matches_expected": matches,
            "product_name": meta.get("product_name"),
            "citation": meta.get("citation"),
        },
        "resampling": info["resampling"],
        "method": (
            f"Mean of the finite {FINE_PER_CELL} x {FINE_PER_CELL} fine pixels per 240 m "
            "cell (block_mean on nested grids; gdal_average only if --allow-unnested). "
            "Source nodata -> NaN. Cells outside lat_max -> NaN."
        ),
        "nodata": "NaN",
        "lat_max": lat_max,
        "aggregation": info,
        "stats": stats(array),
        "grid_sha256": grid_sha256(grid),
        "grid": grid.to_dict(),
        "versions": {"rasterio": rasterio.__version__, "gdal": rasterio.__gdal_version__},
    }
    sidecar_path = out.with_suffix(".json")
    sidecar_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True) + "\n")
    return sidecar


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Aggregate LOLA height error to the 240 m grid")
    parser.add_argument("--src", type=Path, required=True, help="LDEM_80S_20MPP_ADJ_ERR.TIF")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output .npy (default: <repo>/data/grid/dem_error.npy).",
    )
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--no-hash", action="store_true", help="Skip the source sha256.")
    parser.add_argument(
        "--src-nodata",
        type=float,
        default=None,
        help="Override the source nodata value (default: the GeoTIFF's own).",
    )
    parser.add_argument(
        "--allow-unnested",
        action="store_true",
        help="Accept a source that is not 12 x 12 fine pixels per cell.",
    )
    parser.add_argument("--warp-mem-mb", type=int, default=1024)
    parser.add_argument("--threads", default="ALL_CPUS")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    out = args.out if args.out else repo_root() / cfg["paths"]["grid"] / f"{LAYER}.npy"
    try:
        sidecar = run(
            args.src,
            out,
            cfg,
            hash_source=not args.no_hash,
            src_nodata=args.src_nodata,
            allow_unnested=args.allow_unnested,
            warp_mem_limit_mb=args.warp_mem_mb,
            num_threads=args.threads,
        )
    except (OSError, ValueError, rasterio.errors.RasterioError) as exc:
        print(f"dem_error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Wrote {out} and {out.with_suffix('.json')}")
    print(json.dumps(sidecar["stats"], sort_keys=True))


if __name__ == "__main__":
    main()
