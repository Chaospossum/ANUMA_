"""Stage B: full-cap 20 m slope, score/rank comparison, agreement products.

Soft weights, the 8° hard mask, and ``decided_per_layer`` normalization stay
frozen in ``config/layers.yaml`` / ``config/demo_weights.yaml``. Illumination,
hydrogen, and Earth visibility remain the 240 m layers and are sampled onto
20 m by nearest 240 m pixel centre. Only slope is recomputed at 20 m.

Scientific deliverables are the agreement map and the rank table. Slope tiles,
merged slope, and manifests exist so the two products can be reproduced and so
Stage D can reuse the 20 m slope without a second full-cap run.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
import yaml

from anuma.config import load_config, repo_root
from anuma.grid import Grid
from anuma.hpc_smoke import sha256_file, validate_input_manifest
from anuma.score import hard_mask, rank_sites, score
from anuma.slope20 import (
    compute_slope_tiled,
    dem_shape,
    plan_tiles,
    project_slope_node_hours,
    compute_tile_slope,
)

FROZEN_STOP_RMSE = 0.05
SOFT_LAYERS = ("illumination", "hydrogen", "earth_visibility")


def load_stage_b_config(path: Path | None = None) -> dict:
    cfg_path = Path(path) if path else repo_root() / "config" / "stage-b.yaml"
    with cfg_path.open() as handle:
        return yaml.safe_load(handle)


def load_demo_weights(path: Path | None = None) -> dict[str, float]:
    weights_path = Path(path) if path else repo_root() / "config" / "demo_weights.yaml"
    raw = yaml.safe_load(weights_path.read_text())
    weights = raw.get("weights") if isinstance(raw, dict) else None
    if not isinstance(weights, dict):
        raise ValueError("demo_weights.yaml must contain a weights mapping")
    return {str(name): float(value) for name, value in weights.items()}


def require_stage_a_acceptance(path: Path, *, stop_rmse: float = FROZEN_STOP_RMSE) -> dict:
    """Refuse Stage B unless Stage A recorded a pass at the frozen RMSE gate."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Stage A acceptance record is missing: {path}")
    report = json.loads(path.read_text())
    if float(stop_rmse) != FROZEN_STOP_RMSE:
        raise ValueError("Stage B must not change stop_rmse from 0.05")
    metrics = report.get("metrics") or {}
    if float(metrics.get("stop_rmse", -1)) != FROZEN_STOP_RMSE:
        raise ValueError("Stage A record stop_rmse must remain 0.05")
    if report.get("status") != "passed" or metrics.get("passed") is not True:
        raise ValueError("Stage A acceptance record has not passed")
    rmse = float(metrics.get("rmse", float("inf")))
    if not np.isfinite(rmse) or rmse > FROZEN_STOP_RMSE:
        raise ValueError(
            f"Stage A RMSE {rmse} exceeds the frozen stop_rmse {FROZEN_STOP_RMSE}"
        )
    return report


def dem_affine(dem_path: Path) -> tuple[object, int, int]:
    with rasterio.open(dem_path) as dataset:
        return dataset.transform, int(dataset.height), int(dataset.width)


def sample_coarse_to_fine(
    coarse: np.ndarray,
    grid: Grid,
    fine_transform,
    fine_shape: tuple[int, int],
) -> np.ndarray:
    """Nearest-neighbour sample a 240 m layer onto the 20 m DEM grid."""
    height, width = fine_shape
    out = np.full((height, width), np.nan, dtype=np.float32)
    # Axis-aligned stereo: column from x only, row from y only.
    cols = np.arange(width, dtype=np.float64) + 0.5
    rows = np.arange(height, dtype=np.float64) + 0.5
    x = fine_transform.c + cols * fine_transform.a
    y = fine_transform.f + rows * fine_transform.e
    # floor gives the coarse pixel that contains the fine centre. rint would
    # pick the nearest coarse corner and shift half the samples by one cell.
    # Fine centres never fall on a 240 m edge for these two grids.
    coarse_cols = np.floor((x - grid.affine.c) / grid.affine.a).astype(np.int64)
    coarse_rows = np.floor((y - grid.affine.f) / grid.affine.e).astype(np.int64)
    valid_cols = (coarse_cols >= 0) & (coarse_cols < grid.width)
    # Fill row-wise to avoid a full-cap coordinate mesh in memory.
    for row_index, coarse_row in enumerate(coarse_rows):
        if coarse_row < 0 or coarse_row >= grid.height:
            continue
        if not valid_cols.any():
            continue
        out[row_index, valid_cols] = coarse[int(coarse_row), coarse_cols[valid_cols]]
    return out


def sample_fine_mask_to_coarse(
    fine_mask: np.ndarray,
    fine_transform,
    grid: Grid,
) -> tuple[np.ndarray, np.ndarray]:
    """Nearest-neighbour sample a 20 m boolean mask onto 240 m centres."""
    x, y = grid.pixel_centers()
    cols = np.rint((x - fine_transform.c) / fine_transform.a).astype(np.int64)
    rows = np.rint((y - fine_transform.f) / fine_transform.e).astype(np.int64)
    height, width = fine_mask.shape
    inside = (rows >= 0) & (rows < height) & (cols >= 0) & (cols < width)
    out = np.zeros((grid.height, grid.width), dtype=bool)
    valid = np.zeros(out.shape, dtype=bool)
    out[inside] = fine_mask[rows[inside], cols[inside]]
    valid[inside] = True
    return out, valid


def agreement_map(
    mask_240: np.ndarray,
    mask_20: np.ndarray,
    fine_transform,
    grid: Grid,
    *,
    valid_240: np.ndarray | None = None,
    valid_20: np.ndarray | None = None,
) -> np.ndarray:
    """1 where hard masks agree at 240 m centres, 0 disagree, NaN invalid.

    A centre is invalid when it falls outside the 20 m grid, or when either
    slope is not finite there (for example outside the 80°S cap, where the
    240 m slope is NaN but the 20 m DEM still has values).
    """
    sampled_20, inside_20 = sample_fine_mask_to_coarse(mask_20, fine_transform, grid)
    valid = inside_20.copy()
    if valid_240 is not None:
        valid &= np.asarray(valid_240, dtype=bool)
    if valid_20 is not None:
        sampled_valid, _ = sample_fine_mask_to_coarse(
            np.asarray(valid_20, dtype=bool), fine_transform, grid
        )
        valid &= sampled_valid
    agree = sampled_20 == np.asarray(mask_240, dtype=bool)
    out = np.full(mask_240.shape, np.nan, dtype=np.float32)
    out[valid] = np.where(agree[valid], 1.0, 0.0).astype(np.float32)
    return out


def _load_240m_layers(files: dict[str, Path], grid: Grid) -> dict[str, np.ndarray]:
    expected = (grid.height, grid.width)
    layers: dict[str, np.ndarray] = {}
    for name in ("slope", *SOFT_LAYERS):
        array = np.load(files[name], mmap_mode="r", allow_pickle=False)
        if array.shape != expected:
            raise ValueError(f"{name} shape {array.shape} != grid {expected}")
        layers[name] = np.asarray(array)
    return layers


def score_and_rank_240(
    layers: dict[str, np.ndarray],
    grid: Grid,
    cfg: dict,
    weights: dict[str, float],
) -> tuple[object, list[dict]]:
    result = score(
        layers,
        weights=weights,
        grid=grid,
        cfg=cfg,
        normalization_policy=str(cfg["scoring"]["default_normalization_policy"]),
    )
    return result, rank_sites(result)


def score_and_rank_20(
    slope_20: np.ndarray,
    soft_20: dict[str, np.ndarray],
    grid_meta: dict,
    cfg: dict,
    weights: dict[str, float],
) -> tuple[object, list[dict]]:
    """Score on the 20 m arrays using the frozen 240 m Grid only for lon/lat.

    Ranking uses projected coordinates from the 20 m affine stored in
    ``grid_meta`` via a thin adapter around the existing ``rank_sites`` path.
    """
    layers = {"slope": slope_20, **soft_20}
    # Build a Grid-like object with the 20 m transform for site lon/lat.
    fine_grid = _FineGrid(grid_meta)
    result = score(
        layers,
        weights=weights,
        grid=fine_grid,  # type: ignore[arg-type]
        cfg=cfg,
        normalization_policy=str(cfg["scoring"]["default_normalization_policy"]),
    )
    return result, rank_sites(result)


class _FineGrid:
    """Minimal Grid duck-type for 20 m ranking lon/lat and separation."""

    def __init__(self, meta: dict):
        from affine import Affine
        from pyproj import CRS, Transformer

        self.resolution_m = float(meta["resolution_m"])
        self.width = int(meta["width"])
        self.height = int(meta["height"])
        t = meta["transform"]
        self.affine = Affine(t[0], t[1], t[2], t[3], t[4], t[5])
        self.proj4 = str(meta["proj4"])
        self.crs = CRS.from_proj4(self.proj4)
        geodetic = CRS.from_proj4(
            meta.get("geodetic_proj4", "+proj=longlat +R=1737400 +no_defs")
        )
        self.fwd = Transformer.from_crs(geodetic, self.crs, always_xy=True)
        self.inv = Transformer.from_crs(self.crs, geodetic, always_xy=True)

    def to_lonlat(self, row: float, col: float) -> tuple[float, float]:
        x, y = self.affine * (col, row)
        lon, lat = self.inv.transform(x, y)
        return float(lon), float(lat)

    def pixel_centers(self) -> tuple[np.ndarray, np.ndarray]:
        cols = np.arange(self.width, dtype=np.float64) + 0.5
        rows = np.arange(self.height, dtype=np.float64) + 0.5
        x = self.affine.c + cols * self.affine.a
        y = self.affine.f + rows * self.affine.e
        return np.meshgrid(x, y)


def build_rank_table(
    sites_240: list[dict],
    sites_20: list[dict],
    grid: Grid,
) -> dict:
    """Side-by-side 240 m vs 20 m top-N ranks (no new mission claim)."""

    def _xy(lon: float, lat: float) -> tuple[float, float]:
        x, y = grid.fwd.transform(float(lon), float(lat))
        return float(x), float(y)

    rows = []
    for index, site_240 in enumerate(sites_240, start=1):
        entry = {
            "rank_240m": index,
            "site_240m": {
                "lon": site_240["lon"],
                "lat": site_240["lat"],
                "total": site_240["total"],
                "row": site_240["row"],
                "col": site_240["col"],
            },
            "site_20m": None,
            "distance_km": None,
        }
        if index <= len(sites_20):
            site_20 = sites_20[index - 1]
            entry["site_20m"] = {
                "lon": site_20["lon"],
                "lat": site_20["lat"],
                "total": site_20["total"],
                "row": site_20["row"],
                "col": site_20["col"],
            }
            x0, y0 = _xy(site_240["lon"], site_240["lat"])
            x1, y1 = _xy(site_20["lon"], site_20["lat"])
            entry["distance_km"] = float(np.hypot(x1 - x0, y1 - y0) / 1000.0)
        rows.append(entry)

    # Membership: 240 m sites that remain within 10 km of any 20 m top site.
    sep_km = float(load_config()["scoring"]["min_separation_km"])
    membership = []
    for index, site_240 in enumerate(sites_240, start=1):
        x0, y0 = _xy(site_240["lon"], site_240["lat"])
        best = None
        best_d = None
        for j, site_20 in enumerate(sites_20, start=1):
            x1, y1 = _xy(site_20["lon"], site_20["lat"])
            dist = float(np.hypot(x1 - x0, y1 - y0) / 1000.0)
            if best_d is None or dist < best_d:
                best_d = dist
                best = j
        membership.append(
            {
                "rank_240m": index,
                "nearest_rank_20m": best,
                "distance_km": best_d,
                "within_separation_km": bool(
                    best_d is not None and best_d <= sep_km
                ),
            }
        )

    return {
        "schema_version": 1,
        "kind": "rank_table_20m_vs_240m",
        "note": (
            "Comparison only. ANUMA is not affiliated with or endorsed by "
            "ispace, ESA, or the MAGPIE mission team."
        ),
        "paired_by_rank": rows,
        "membership_240m_in_20m": membership,
        "sites_240m": sites_240,
        "sites_20m": sites_20,
    }


def run_production(
    input_manifest: Path,
    dem_path: Path,
    output_dir: Path,
    *,
    workers: int,
    stage_a_acceptance: Path,
    stage_b_config: Path | None = None,
) -> dict:
    """Full-cap 20 m slope, score/rank both resolutions, write the two products."""
    bcfg = load_stage_b_config(stage_b_config)
    require_stage_a_acceptance(
        stage_a_acceptance,
        stop_rmse=float(bcfg["stage_a_gate"]["stop_rmse"]),
    )
    input_meta, files = validate_input_manifest(input_manifest)
    cfg = load_config()
    weights = load_demo_weights()
    # Refuse weight drift against layers.yaml defaults.
    for name, value in cfg["scoring"]["weights"].items():
        if abs(float(weights[name]) - float(value)) > 1e-12:
            raise ValueError(f"demo weight for {name!r} does not match layers.yaml")
    if float(cfg["hard"]["max_slope_deg"]) != 8.0:
        raise ValueError("hard max_slope_deg must remain 8.0")
    if str(cfg["scoring"]["default_normalization_policy"]) != "decided_per_layer":
        raise ValueError("default normalization policy must remain decided_per_layer")

    dem_path = Path(dem_path)
    transform, height, width = dem_affine(dem_path)
    expected_h = int(bcfg["dem"]["expected_height"])
    expected_w = int(bcfg["dem"]["expected_width"])
    if (height, width) != (expected_h, expected_w):
        raise ValueError(
            f"DEM shape {(height, width)} does not match expected "
            f"{(expected_h, expected_w)}"
        )

    output_dir = output_dir.resolve()
    checkpoint_dir = output_dir / "checkpoint"
    resuming = checkpoint_dir.is_dir() and any(checkpoint_dir.iterdir())
    if output_dir.exists() and any(output_dir.iterdir()) and not resuming:
        raise ValueError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "progress.log"
    started = datetime.now(timezone.utc).isoformat()

    slope_path = output_dir / "slope-20m.npy"
    tile_size = int(bcfg["slope"]["tile_size"])
    slope_progress = compute_slope_tiled(
        dem_path,
        slope_path,
        workers=workers,
        tile_size=tile_size,
        checkpoint_dir=checkpoint_dir / "slope-tiles",
        cellsize_m=float(bcfg["dem"]["resolution_m"]),
        log_path=log_path,
    )
    slope_20 = np.load(slope_path, mmap_mode="r", allow_pickle=False)

    grid = Grid.from_json(files["grid"], cfg=cfg)
    layers_240 = _load_240m_layers(files, grid)
    result_240, sites_240 = score_and_rank_240(layers_240, grid, cfg, weights)

    soft_20 = {
        name: sample_coarse_to_fine(layers_240[name], grid, transform, (height, width))
        for name in SOFT_LAYERS
    }
    fine_meta = {
        "resolution_m": float(bcfg["dem"]["resolution_m"]),
        "width": width,
        "height": height,
        "transform": [
            transform.a,
            transform.b,
            transform.c,
            transform.d,
            transform.e,
            transform.f,
        ],
        "proj4": cfg["projection"]["proj4"],
        "geodetic_proj4": cfg["projection"]["geodetic_proj4"],
    }
    # Materialize slope for scoring (mmap is fine for reads inside score).
    result_20, sites_20 = score_and_rank_20(
        np.asarray(slope_20), soft_20, fine_meta, cfg, weights
    )

    mask_240 = hard_mask(layers_240["slope"], cfg=cfg)
    mask_20 = hard_mask(np.asarray(slope_20), cfg=cfg)
    agree = agreement_map(
        mask_240,
        mask_20,
        transform,
        grid,
        valid_240=np.isfinite(layers_240["slope"]),
        valid_20=np.isfinite(np.asarray(slope_20)),
    )
    agree_path = output_dir / "agreement-map-240m.npy"
    np.save(agree_path, agree, allow_pickle=False)

    rank_table = build_rank_table(sites_240, sites_20, grid)
    rank_path = output_dir / "rank-table-20m-vs-240m.json"
    rank_path.write_text(json.dumps(rank_table, indent=2, sort_keys=True) + "\n")

    report = {
        "schema_version": 1,
        "stage": "B-20m-slope-ranking",
        "status": "completed",
        "started_at_utc": started,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Agreement map and rank table only. Not a mission claim. "
            "ANUMA is not affiliated with or endorsed by ispace, ESA, or "
            "the MAGPIE mission team."
        ),
        "stage_a_acceptance": {
            "path": str(Path(stage_a_acceptance).resolve()),
            "sha256": sha256_file(Path(stage_a_acceptance).resolve()),
        },
        "inputs": {
            "input_manifest": {
                "path": str(input_manifest.resolve()),
                "sha256": sha256_file(input_manifest.resolve()),
                "illumination_affine_revision": input_meta["provenance"][
                    "illumination_affine_revision"
                ],
            },
            "dem": {
                "path": str(dem_path.resolve()),
                "sha256": sha256_file(dem_path.resolve()),
                "shape": [height, width],
            },
            "normalization_policy": cfg["scoring"]["default_normalization_policy"],
            "weights": weights,
            "max_slope_deg": float(cfg["hard"]["max_slope_deg"]),
            "soft_layer_sampling": bcfg["soft_layer_sampling"],
            "agreement": bcfg["agreement"],
        },
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "config_layers_sha256": sha256_file(repo_root() / "config" / "layers.yaml"),
            "config_stage_b_sha256": sha256_file(
                Path(stage_b_config)
                if stage_b_config
                else repo_root() / "config" / "stage-b.yaml"
            ),
        },
        "execution": {
            "workers": workers,
            "tile_size": tile_size,
            "slope_progress": slope_progress,
        },
        "products": {
            "agreement_map": agree_path.name,
            "rank_table": rank_path.name,
        },
        "summary": {
            "agreement_valid_pixels": int(np.isfinite(agree).sum()),
            "agreement_fraction": (
                float(np.nanmean(agree)) if np.isfinite(agree).any() else float("nan")
            ),
            "n_sites_240m": len(sites_240),
            "n_sites_20m": len(sites_20),
        },
    }
    # Operational slope artifact for Stage D reuse (not a scientific claim).
    report["operational_artifacts"] = {
        "slope_20m": {
            "path": slope_path.name,
            "sha256": sha256_file(slope_path),
            "bytes": slope_path.stat().st_size,
            "purpose": "reuse in Stage D without recomputing full-cap slope",
        }
    }
    report_path = output_dir / "output-manifest.json"
    report["outputs"] = {
        "agreement-map-240m.npy": {
            "sha256": sha256_file(agree_path),
            "bytes": agree_path.stat().st_size,
        },
        "rank-table-20m-vs-240m.json": {
            "sha256": sha256_file(rank_path),
            "bytes": rank_path.stat().st_size,
        },
        "output-manifest.json": None,
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    report["outputs"]["output-manifest.json"] = {
        "sha256": sha256_file(report_path),
        "bytes": report_path.stat().st_size,
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def run_pilot(
    dem_path: Path,
    output_dir: Path,
    *,
    workers: int,
    stage_b_config: Path | None = None,
) -> dict:
    """Time a sample of full-cap tiles and project one-node production cost.

    The pilot does not require Stage A and does not write the agreement map or
    rank table.
    """
    bcfg = load_stage_b_config(stage_b_config)
    dem_path = Path(dem_path)
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    height, width = dem_shape(dem_path)
    tile_size = int(bcfg["slope"]["tile_size"])
    tiles = plan_tiles(height, width, tile_size=tile_size)
    pilot_count = int(bcfg["pilot"]["tile_count"])
    if pilot_count <= 0 or pilot_count >= len(tiles):
        raise ValueError("pilot tile_count must be a positive sample below the full set")
    # Spread the sample across the DEM instead of only the first strip.
    step = max(1, len(tiles) // pilot_count)
    sample = [tiles[index * step] for index in range(pilot_count)]

    started = time.perf_counter()
    for tile in sample:
        compute_tile_slope(
            dem_path,
            tile,
            cellsize_m=float(bcfg["dem"]["resolution_m"]),
        )
    tile_seconds = time.perf_counter() - started

    # Laptop-scaled scoring placeholder: production measures scoring itself;
    # pilot projects slope only and adds a conservative scoring allowance from
    # the configured production budget fraction (documented, not a result).
    scoring_seconds = 0.0
    projection = project_slope_node_hours(
        tile_seconds=tile_seconds,
        pilot_tiles=len(sample),
        full_tiles=len(tiles),
        workers=workers,
        scoring_seconds=scoring_seconds,
        production_node_hours=float(bcfg["execution"]["production_node_hours"]),
        stage_node_hour_cap=float(bcfg["execution"]["stage_node_hour_cap"]),
    )
    # Add documented scoring headroom so the pilot gate covers score/rank I/O.
    # 0.25 node-hours is an engineering allowance from the laptop 240 m timing
    # scaled by pixel count; it is not a MeluXina measurement.
    scoring_allowance_hours = 0.25
    adjusted_hours = float(projection["projected_node_hours"]) + scoring_allowance_hours
    production_cap = float(bcfg["execution"]["production_node_hours"])
    stage_cap = float(bcfg["execution"]["stage_node_hour_cap"])
    projection = dict(projection)
    projection["scoring_allowance_node_hours"] = scoring_allowance_hours
    projection["projected_node_hours_with_scoring"] = adjusted_hours
    projection["fits_production_cap"] = bool(adjusted_hours <= production_cap)
    projection["fits_stage_cap"] = bool(adjusted_hours <= stage_cap)

    report = {
        "schema_version": 1,
        "stage": "B-20m-slope-pilot",
        "status": "measured",
        "gate": "Stage A RMSE gate is not applied to this sample",
        "sample": {
            "tile_count": len(sample),
            "full_tile_count": len(tiles),
            "tile_size": tile_size,
            "grid_shape": [height, width],
            "workers": workers,
            "tile_keys": [tile.key for tile in sample],
        },
        "timings_seconds": {
            "slope_sample": tile_seconds,
        },
        "projection": projection,
        "execution_config": {
            "production_walltime": bcfg["execution"]["production_walltime"],
            "production_node_hours": production_cap,
            "stage_node_hour_cap": stage_cap,
            "memory": bcfg["execution"]["memory"],
        },
        "note": (
            "Projected from timed full-cap DEM tiles plus a documented scoring "
            "allowance. Not an agreement map or rank table."
        ),
    }
    path = output_dir / "pilot-measurement.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("pilot", "production"), required=True)
    parser.add_argument("--dem", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--input-manifest", type=Path, default=None)
    parser.add_argument("--stage-a-acceptance", type=Path, default=None)
    parser.add_argument("--stage-b-config", type=Path, default=None)
    args = parser.parse_args(argv)
    bcfg = load_stage_b_config(args.stage_b_config)
    dem = args.dem or (repo_root() / bcfg["dem"]["path"])
    try:
        if args.mode == "pilot":
            report = run_pilot(
                dem,
                args.output_dir,
                workers=args.workers,
                stage_b_config=args.stage_b_config,
            )
        else:
            if args.input_manifest is None:
                raise ValueError("production requires --input-manifest")
            acceptance = args.stage_a_acceptance
            if acceptance is None:
                raise ValueError("production requires --stage-a-acceptance")
            report = run_production(
                args.input_manifest,
                dem,
                args.output_dir,
                workers=args.workers,
                stage_a_acceptance=acceptance,
                stage_b_config=args.stage_b_config,
            )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Stage B precondition failed: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.mode == "pilot":
        if not report["projection"]["fits_production_cap"]:
            raise SystemExit(4)


if __name__ == "__main__":
    main()
