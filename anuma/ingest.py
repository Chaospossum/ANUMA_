"""Download sources, warp onto the common grid, write .npy and quicklooks.

`--synthetic` builds deterministic test layers only. It does not hit the
network and must not be used for a demo.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.io import MemoryFile
from rasterio.vrt import WarpedVRT

from anuma.config import load_config, repo_root
from anuma.download import download
from anuma.earth import earth_visibility
from anuma.grid import Grid
from anuma.quicklook import write_quicklook
from anuma.slope import horn_slope_deg

FILL = -1.0e20


def _paths(cfg: dict, root: Path) -> dict[str, Path]:
    p = cfg["paths"]
    return {
        "raw": root / p["raw"],
        "grid": root / p["grid"],
        "quicklook": root / p["quicklook"],
        "grid_json": root / p["grid_json"],
        "artemis_geojson": root / p["artemis_geojson"],
    }


def _illumination_affine(georef: dict) -> Affine:
    """Convert PDS3 projection offsets to a GDAL pixel-corner affine.

    PDS3 offsets locate the projection origin relative to the centre of the
    one-based first pixel.  Thus pixel (1, 1) has centre coordinates
    ``(-sample_offset * scale, line_offset * scale)``.
    """
    scale = float(georef["pixel_m"])
    s_off = float(georef["sample_projection_offset"])
    l_off = float(georef["line_projection_offset"])
    ul_x = -(s_off + 0.5) * scale
    ul_y = (l_off + 0.5) * scale
    return Affine(scale, 0.0, ul_x, 0.0, -scale, ul_y)


def warp_to_grid(
    src: rasterio.io.DatasetReader,
    grid: Grid,
    resampling: Resampling = Resampling.bilinear,
    src_nodata=None,
) -> np.ndarray:
    """Reproject `src` onto the common grid with WarpedVRT."""
    with WarpedVRT(
        src,
        crs=grid.crs,
        transform=grid.affine,
        width=grid.width,
        height=grid.height,
        resampling=resampling,
        src_nodata=src_nodata,
        nodata=FILL,
        dtype="float32",
    ) as vrt:
        data = vrt.read(1)
    data = data.astype(np.float32)
    return np.where(data == np.float32(FILL), np.nan, data)


def _memory_raster(
    array: np.ndarray,
    transform: Affine,
    crs: CRS,
    nodata=None,
) -> MemoryFile:
    mem = MemoryFile()
    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
    }
    with mem.open(**profile) as ds:
        ds.write(np.asarray(array, dtype=np.float32), 1)
    return mem


def ingest_dem(cfg: dict, paths: dict[str, Path], grid: Grid) -> np.ndarray:
    meta = cfg["layers"]["dem"]
    dest = download(
        meta["url"], paths["raw"] / meta["filename"], meta.get("expected_bytes")
    )
    with rasterio.open(dest) as src:
        if src.crs is None:
            print(
                f"Download failed\nURL: {meta['url']}\nError: GeoTIFF has no CRS",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return warp_to_grid(src, grid, Resampling.bilinear)


def ingest_illumination(cfg: dict, paths: dict[str, Path], grid: Grid) -> np.ndarray:
    meta = cfg["layers"]["illumination"]
    dest = download(
        meta["url"], paths["raw"] / meta["filename"], meta.get("expected_bytes")
    )
    for side in meta.get("sidecars", []):
        download(side["url"], paths["raw"] / side["filename"])
    georef = meta["georef"]
    src_transform = _illumination_affine(georef)
    src_crs = CRS.from_proj4(georef["proj4"])
    with rasterio.open(dest) as src:
        raw = src.read(1).astype(np.float32)
    nodata = meta.get("nodata")
    if nodata is not None:
        raw = np.where(raw == nodata, np.nan, raw)
    scaled = raw * float(meta["scale"]) + float(meta["offset"])
    mem = _memory_raster(scaled, src_transform, src_crs)
    try:
        with mem.open() as src:
            return warp_to_grid(src, grid, Resampling.bilinear)
    finally:
        mem.close()


def ingest_hydrogen(cfg: dict, paths: dict[str, Path], grid: Grid) -> np.ndarray:
    meta = cfg["layers"]["hydrogen"]
    dest = download(
        meta["url"], paths["raw"] / meta["filename"], meta.get("expected_bytes")
    )
    height, width = int(meta["height"]), int(meta["width"])
    values = np.fromfile(dest, dtype=np.dtype(meta["dtype"]))
    if values.size != height * width:
        print(
            f"Download failed\nURL: {meta['url']}\n"
            f"Error: expected {height * width} samples, got {values.size}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    ppm = values.reshape((height, width)).astype(np.float32) * float(meta["scale"])
    transform = Affine(meta["xres"], 0.0, meta["xmin"], 0.0, meta["yres"], meta["ymax"])
    crs = CRS.from_proj4(meta["geodetic_proj4"])
    mem = _memory_raster(ppm, transform, crs)
    try:
        with mem.open() as src:
            return warp_to_grid(src, grid, Resampling.bilinear)
    finally:
        mem.close()


def synthetic_layers(grid: Grid) -> dict[str, np.ndarray]:
    """Deterministic layers for unit tests. Not for scoring a real site."""
    x, y = grid.pixel_centers()
    dem = (
        120.0 * np.sin(2.0 * np.pi * x / 80_000.0) * np.cos(2.0 * np.pi * y / 80_000.0)
        + 60.0 * np.sin(2.0 * np.pi * y / 45_000.0)
    ).astype(np.float32)
    _, lat = grid.lonlat_arrays()
    illumination = np.clip(0.15 + 0.55 * ((lat + 90.0) / 10.0), 0.0, 1.0)
    illumination = illumination.astype(np.float32)
    hydrogen = np.clip(50.0 + 400.0 * np.exp(-((lat + 90.0) / 4.0) ** 2), 0.0, None)
    hydrogen = hydrogen.astype(np.float32)
    return {"dem": dem, "illumination": illumination, "hydrogen": hydrogen}


def write_artemis_geojson(cfg: dict, dest: Path) -> None:
    """Names and source URL only. NASA has not published centres."""
    art = cfg["artemis3"]
    features = []
    for name in art["regions"]:
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": name,
                    "approximate": art["approximate"],
                    "coordinate_status": art["coordinate_status"],
                    "source": art["source_url"],
                },
                "geometry": None,
            }
        )
    fc = {
        "type": "FeatureCollection",
        "properties": {
            "approximate": art["approximate"],
            "source": art["source_url"],
            "announced": art["announced"],
            "note": art["note"].strip(),
        },
        "features": features,
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(fc, indent=2) + "\n")


def mask_outside_cap(array: np.ndarray, grid: Grid, lat_max: float) -> np.ndarray:
    inside = grid.inside_latitude(lat_max)
    out = array.astype(np.float32, copy=True)
    out[~inside] = np.nan
    return out


def save_layer(name: str, array: np.ndarray, paths: dict[str, Path], cmap: str) -> None:
    paths["grid"].mkdir(parents=True, exist_ok=True)
    np.save(paths["grid"] / f"{name}.npy", array)
    write_quicklook(array, paths["quicklook"] / f"{name}.png", cmap=cmap)


def run(cfg: dict, root: Path, synthetic: bool) -> None:
    paths = _paths(cfg, root)
    for key in ("raw", "grid", "quicklook"):
        paths[key].mkdir(parents=True, exist_ok=True)
    grid = Grid(cfg)
    grid.save_json(paths["grid_json"])
    lat_max = float(cfg["projection"]["lat_max"])

    if synthetic:
        print("SYNTHETIC MODE: layers are test fixtures, not flight data.")
        layers = synthetic_layers(grid)
        dem = layers["dem"]
        illumination = layers["illumination"]
        hydrogen = layers["hydrogen"]
    else:
        print("Ingesting DEM…")
        dem = ingest_dem(cfg, paths, grid)
        print("Ingesting illumination…")
        illumination = ingest_illumination(cfg, paths, grid)
        print("Ingesting hydrogen (Lunar Prospector fallback)…")
        hydrogen = ingest_hydrogen(cfg, paths, grid)

    slope = horn_slope_deg(dem, grid.resolution_m)
    print("Computing Earth-visibility approximation…")
    vis = earth_visibility(dem, grid, cfg["earth_visibility"])

    dem = mask_outside_cap(dem, grid, lat_max)
    slope = mask_outside_cap(slope, grid, lat_max)
    illumination = mask_outside_cap(illumination, grid, lat_max)
    hydrogen = mask_outside_cap(hydrogen, grid, lat_max)
    vis = mask_outside_cap(vis, grid, lat_max)

    save_layer("dem", dem, paths, "terrain")
    save_layer("slope", slope, paths, "plasma")
    save_layer("illumination", illumination, paths, "gray")
    save_layer("hydrogen", hydrogen, paths, "gray")
    save_layer("earth_visibility", vis, paths, "gray")
    write_artemis_geojson(cfg, paths["artemis_geojson"])
    print(f"Wrote grid {grid.width}×{grid.height} to {paths['grid']}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ANUMA Phase 1 ingest")
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Build fake test layers. Unit tests only.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Root that contains data/ (default: repository root).",
    )
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    root = args.data_dir if args.data_dir else repo_root()
    run(cfg, root, synthetic=args.synthetic)


if __name__ == "__main__":
    main()
