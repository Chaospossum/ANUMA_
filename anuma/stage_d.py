"""Stage D: site-local LOLA DEM-noise check on the 20 m DEM.

Implements the plan frozen in ``docs/stage-d-plan.md`` and
``hpc_studies.dem_noise`` in ``config/layers.yaml``. Nothing here may be
tuned after a noise result; ``require_frozen_config`` refuses drift.

Per site (8, in the frozen scope order) and per run (0 = primary,
sigma = median EFFRES / 2.355; 1 = sensitivity, sigma = median EFFRES), each
of 200 draws:

1. ``w``: standard normal white noise on the ±5 km window, its one-pixel
   Horn halo and a pad of ceil(4 sigma / 20 m) pixels;
2. ``g``: ``w`` convolved with a normalized Gaussian kernel ``K`` (sum 1,
   truncated at 4 sigma; separable, so the truncation is a square);
3. ``u = g / sqrt(sum K**2)``, unit standard deviation per pixel;
4. noise = ERR x u, added to the DEM; the pad is cropped;
5. Horn 3x3 slope at 20 m through ``anuma.slope20.slope_tile_from_array``.

Every draw has its own stream
``default_rng(SeedSequence(seed, spawn_key=(run, site, draw)))``, so the
output does not depend on worker count or scheduling.

The 1 km circle, the 144 fine pixels of the 240 m cell and the 8° limit
give the per-draw statistics. The decision is distributional: a site is
noise-unstable when the 5-95 % interval of its ≤ 8° fraction within 1 km is
wider than 0.10 or straddles 0.50 (primary run only).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path
from typing import Sequence

import numpy as np
from scipy import ndimage

from anuma.config import load_config, repo_root
from anuma.slope20 import HALO, TileSpec, _read_elev_with_halo, slope_tile_from_array
from anuma.stage_d_gate import (
    STUDY_SUMMARY_SHA256,
    expected_inputs,
    sha256_file,
    verify_input_digests,
)

SCHEMA_VERSION = 1
STAGE = "D-dem-noise-20m"
RUN_NAMES = ("primary", "sensitivity")
RANKING_DEPTH = 15
# Pilot scope from the plan's acceptance checks: 2 sites x 8 draws, primary
# run. The first two sites in scope order use the same streams as production.
PILOT_SITE_COUNT = 2
PILOT_DRAWS = 8
STAGE_B_CHECKS = Path("data") / "checks" / "stage-b-1km.json"

# Values frozen in docs/stage-d-plan.md on 2026-10-15. The config must
# still hold them; the code never adopts a changed value silently.
FROZEN = {
    "frozen_on": "2026-10-15",
    "stability_rule.source_study": "clip_weight_monte_carlo",
    "stability_rule.source_job": "5332991",
    "stability_rule.policy": "decided_per_layer",
    "stability_rule.min_top10_frequency": 0.80,
    "stability_rule.rank_margin": 2,
    "stability_rule.frozen_unstable_ids": ["779:1564", "1252:1287", "1277:1142", "1206:1114"],
    "controls.top_ranks": 3,
    "controls.extra_ranks": [8],
    "controls.frozen_ids": ["1318:1219", "1292:1255", "1350:1392", "791:1624"],
    "site_order": [
        "1318:1219", "1292:1255", "1350:1392", "791:1624",
        "779:1564", "1252:1287", "1277:1142", "1206:1114",
    ],
    "noise_model.error_filename": "LDEM_80S_20MPP_ADJ_ERR.TIF",
    "noise_model.effres_filename": "LDEM_80S_20MPP_ADJ_EFFRES.TIF",
    "noise_model.slope_error_filename": "LDSM_80S_20MPP_ADJ_ERR.TIF",
    "noise_model.error_is_one_sigma": True,
    "noise_model.distribution": "gaussian",
    "noise_model.correlation": "gaussian_kernel",
    "noise_model.effres_window_statistic": "median",
    "noise_model.effres_is_fwhm": True,
    "noise_model.fwhm_to_sigma_divisor": 2.355,
    "noise_model.sensitivity_sigma_equals_effres": True,
    "noise_model.fallback_fwhm_m": 40.0,
    "noise_model.rescale_to_error_map": True,
    "ellipse.radius_m": 1000.0,
    "ellipse.max_slope_deg_from": "hard.max_slope_deg",
    "ellipse.threshold_fraction": 0.50,
    "ellipse.interval_percentiles": [5, 95],
    "ellipse.max_interval_width": 0.10,
    "ellipse.percentile_method": "linear",
    "ellipse.borderline_width_band": [0.085, 0.115],
    "ellipse.patch_connectivity": 8,
    "in_cell_fine_pixels": 144,
    "draws": 200,
    "seed": 20261016,
    "window_half_size_m": 5000.0,
    "resolution_m": 20.0,
    "slope_method": "horn_3x3",
    "acceptance.zero_noise_slope_atol_deg": 1.0e-4,
    "acceptance.noise_std_ratio_tolerance": 0.05,
    "acceptance.slope_noise_vs_ldsm_err_max_factor": 2.0,
    "acceptance.slope_noise_check_decisive_runs": ["primary"],
    "acceptance.stage_b_fraction_check_ranks": [1, 10],
    "execution.nodes": 1,
    "execution.pilot_qos": "test",
    "execution.pilot_walltime": "00:30:00",
    "execution.production_node_hours": 2.0,
    "execution.stage_node_hour_cap": 50.0,
}
FROZEN_SCORING = {
    "hard.max_slope_deg": 8.0,
    "scoring.default_normalization_policy": "decided_per_layer",
    "scoring.n_sites": 10,
    "scoring.min_separation_km": 10.0,
    "scoring.weights": {"illumination": 0.5, "hydrogen": 0.3, "earth_visibility": 0.2},
}


# --------------------------------------------------------------------------
# Frozen configuration and stability rule
# --------------------------------------------------------------------------


def _lookup(tree: dict, dotted: str):
    node = tree
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"config is missing {dotted}")
        node = node[part]
    return node


def require_frozen_config(cfg: dict) -> dict:
    """Return ``hpc_studies.dem_noise`` after checking every frozen value."""
    dn = cfg["hpc_studies"]["dem_noise"]
    for key, value in FROZEN.items():
        if _lookup(dn, key) != value:
            raise ValueError(f"hpc_studies.dem_noise.{key} differs from the frozen plan")
    for key, value in FROZEN_SCORING.items():
        actual = _lookup(cfg, key)
        if isinstance(value, dict):
            actual = {name: float(weight) for name, weight in actual.items()}
        if actual != value:
            raise ValueError(f"{key} differs from the default the rule was frozen on")
    return dn


def load_study_summary(path: Path) -> dict:
    """Read the job 5332991 summary after checking the digest the plan names."""
    path = Path(path)
    digest = sha256_file(path)
    if digest != STUDY_SUMMARY_SHA256:
        raise ValueError(
            f"study summary digest {digest} is not the job 5332991 digest "
            f"{STUDY_SUMMARY_SHA256}"
        )
    return json.loads(path.read_text())


def default_ranking(layers: dict[str, np.ndarray], grid, cfg: dict) -> list[dict]:
    """Default score and ``rank_sites(n=15)``: weights, policy and mask from config."""
    from anuma.score import rank_sites, score

    result = score(layers, grid=grid, cfg=cfg)
    if result.normalization_policy != "decided_per_layer":
        raise ValueError("default ranking must use decided_per_layer")
    return rank_sites(result, n=RANKING_DEPTH)


def site_id(site: dict) -> str:
    return f"{int(site['row'])}:{int(site['col'])}"


def recompute_stability_rule(summary: dict, ranked: Sequence[dict], cfg: dict) -> dict:
    """Recompute rank-unstable sites and controls; assert the frozen lists.

    Clause 1: default top 10 and top-10 frequency < 0.80 under
    ``decided_per_layer``. Clause 2: default rank within ``rank_margin`` of
    the cut (ranks 9-12). Controls: ranks 1..top_ranks plus extra ranks.
    """
    dn = require_frozen_config(cfg)
    rule = dn["stability_rule"]
    n_sites = int(cfg["scoring"]["n_sites"])
    margin = int(rule["rank_margin"])
    min_freq = float(rule["min_top10_frequency"])
    if int(summary.get("draw_count", -1)) != 500:
        raise ValueError("study summary is not the 500-draw run")
    if len(ranked) < n_sites + margin:
        raise ValueError(f"default ranking has {len(ranked)} sites; need {n_sites + margin}")
    entries = summary["policies"][rule["policy"]]["top10_membership_frequency"]
    frequency = {str(entry["id"]): float(entry["frequency"]) for entry in entries}

    table = []
    unstable = []
    for rank, site in enumerate(ranked, start=1):
        sid = site_id(site)
        freq = frequency.get(sid, 0.0)
        by_frequency = rank <= n_sites and freq < min_freq
        by_margin = n_sites - margin + 1 <= rank <= n_sites + margin
        if by_frequency or by_margin:
            unstable.append(sid)
        table.append(
            {
                "default_rank": rank,
                "id": sid,
                "row": int(site["row"]),
                "col": int(site["col"]),
                "lon": float(site["lon"]),
                "lat": float(site["lat"]),
                "score": float(site["total"]),
                "top10_frequency": freq,
                "clause_frequency": bool(by_frequency),
                "clause_rank_margin": bool(by_margin),
            }
        )
    controls_cfg = dn["controls"]
    control_ranks = list(range(1, int(controls_cfg["top_ranks"]) + 1)) + [
        int(rank) for rank in controls_cfg["extra_ranks"]
    ]
    controls = [table[rank - 1]["id"] for rank in control_ranks]
    scope = controls + [sid for sid in unstable if sid not in controls]

    if unstable != list(rule["frozen_unstable_ids"]):
        raise ValueError(
            f"recomputed unstable sites {unstable} do not reproduce "
            f"frozen_unstable_ids {rule['frozen_unstable_ids']}"
        )
    if controls != list(controls_cfg["frozen_ids"]):
        raise ValueError(f"recomputed controls {controls} differ from the frozen controls")
    if scope != list(dn["site_order"]):
        raise ValueError(f"recomputed scope {scope} differs from the frozen site order")
    return {
        "unstable_ids": unstable,
        "control_ids": controls,
        "control_ranks": control_ranks,
        "site_order": scope,
        "reproduces_frozen_unstable_ids": True,
        "rule": {
            "min_top10_frequency": min_freq,
            "rank_margin": margin,
            "rank_margin_ranks": [n_sites - margin + 1, n_sites + margin],
            "policy": rule["policy"],
            "source_job": rule["source_job"],
            "study_summary_sha256": STUDY_SUMMARY_SHA256,
        },
        "ranking": table,
    }


# --------------------------------------------------------------------------
# Site windows
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SiteWindow:
    """A site's 20 m window: ``size`` x ``size`` pixels from (row0, col0)."""

    site_id: str
    site_index: int
    default_rank: int
    row_240: int
    col_240: int
    centre_x: float
    centre_y: float
    row0: int
    col0: int
    size: int

    def tile(self) -> TileSpec:
        return TileSpec(
            tile_row=0,
            tile_col=0,
            row0=self.row0,
            row1=self.row0 + self.size,
            col0=self.col0,
            col1=self.col0 + self.size,
        )


def require_nested_grids(coarse_transform, fine_transform) -> None:
    """The 240 m grid must be exactly 12 x 12 fine pixels per cell, north-up."""
    for name, value in (("b", fine_transform.b), ("d", fine_transform.d)):
        if value != 0.0 or getattr(coarse_transform, name) != 0.0:
            raise ValueError("grids must be north-up without rotation")
    if abs(coarse_transform.a - 12.0 * fine_transform.a) > 1e-9 or abs(
        coarse_transform.e - 12.0 * fine_transform.e
    ) > 1e-9:
        raise ValueError("240 m grid is not 12 fine pixels per cell")


def require_same_projection(fine_crs, proj4: str) -> dict:
    """The DEM CRS must place test points where the 240 m grid proj4 does.

    The DEM carries an IAU WKT and the grid a proj4 string, so the check is
    numeric: forward-project points across the cap with both and compare.
    """
    from pyproj import CRS, Transformer

    geodetic = CRS.from_proj4("+proj=longlat +R=1737400 +no_defs")
    grid = Transformer.from_crs(geodetic, CRS.from_proj4(proj4), always_xy=True)
    dem = Transformer.from_crs(geodetic, CRS.from_user_input(fine_crs), always_xy=True)
    worst = 0.0
    for lon in (0.0, 45.0, 137.0, -90.0, 180.0):
        for lat in (-80.5, -85.0, -89.5):
            x0, y0 = grid.transform(lon, lat)
            x1, y1 = dem.transform(lon, lat)
            worst = max(worst, float(math.hypot(x1 - x0, y1 - y0)))
    if not worst <= 1e-3:
        raise ValueError(f"DEM CRS differs from the grid projection by {worst} m")
    return {"max_offset_m": worst}


def site_window(
    *,
    site_id: str,
    site_index: int,
    default_rank: int,
    row_240: int,
    col_240: int,
    coarse_transform,
    fine_transform,
    half_size_px: int,
) -> SiteWindow:
    """Window centred on the fine pixel at (or containing) the 240 m centre.

    On the real grids the 240 m centre falls on a fine-pixel corner; the
    window then starts ``half_size_px`` pixels before the pixel whose upper
    left corner is the centre. The circle and the cell are measured from the
    exact centre, so this half-pixel choice does not move them. Any other
    nesting (not 12 fine pixels per 240 m cell, or a centre off the fine
    corners) is refused.
    """
    require_nested_grids(coarse_transform, fine_transform)
    centre_x, centre_y = coarse_transform @ (col_240 + 0.5, row_240 + 0.5)
    frac_col = (centre_x - fine_transform.c) / fine_transform.a
    frac_row = (centre_y - fine_transform.f) / fine_transform.e
    for value in (frac_col, frac_row):
        if abs(value - round(value)) > 1e-6:
            raise ValueError(
                f"site {site_id}: 240 m centre is not on a 20 m pixel corner "
                f"(fractional index {value})"
            )
    fine_col = int(round(frac_col))
    fine_row = int(round(frac_row))
    return SiteWindow(
        site_id=site_id,
        site_index=site_index,
        default_rank=default_rank,
        row_240=int(row_240),
        col_240=int(col_240),
        centre_x=float(centre_x),
        centre_y=float(centre_y),
        row0=fine_row - half_size_px,
        col0=fine_col - half_size_px,
        size=2 * half_size_px + 1,
    )


def window_masks(
    window: SiteWindow,
    fine_transform,
    *,
    radius_m: float,
    cell_size_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    """1 km circle (pixel centres within ``radius_m``) and the 240 m cell."""
    cols = window.col0 + np.arange(window.size, dtype=np.float64) + 0.5
    rows = window.row0 + np.arange(window.size, dtype=np.float64) + 0.5
    x = fine_transform.c + cols * fine_transform.a
    y = fine_transform.f + rows * fine_transform.e
    dx, dy = np.meshgrid(x - window.centre_x, y - window.centre_y)
    circle = np.hypot(dx, dy) <= float(radius_m)
    half = 0.5 * float(cell_size_m)
    cell = (np.abs(dx) < half) & (np.abs(dy) < half)
    return circle, cell


def read_window(dataset, window: SiteWindow, *, halo: int) -> np.ndarray:
    """Window plus ``halo`` as float64, nodata and off-raster as NaN."""
    block = _read_elev_with_halo(dataset, window.tile(), halo=halo)
    nodata = dataset.nodata
    if nodata is not None and np.isfinite(nodata):
        block = np.where(block == nodata, np.nan, block)
    return block


# --------------------------------------------------------------------------
# Noise model
# --------------------------------------------------------------------------


def kernel_radius_px(sigma_m: float, cell_m: float) -> int:
    """Truncation at 4 sigma: ceil(4 sigma / cell) pixels, also the noise pad."""
    if not (math.isfinite(sigma_m) and sigma_m > 0.0):
        raise ValueError("kernel sigma must be finite and positive")
    return int(math.ceil(4.0 * float(sigma_m) / float(cell_m)))


def gaussian_kernel_1d(sigma_m: float, cell_m: float) -> np.ndarray:
    """Normalized 1-D Gaussian, truncated at 4 sigma. ``K = outer(k, k)``."""
    radius = kernel_radius_px(sigma_m, cell_m)
    offsets = np.arange(-radius, radius + 1, dtype=np.float64) * float(cell_m)
    kernel = np.exp(-0.5 * (offsets / float(sigma_m)) ** 2)
    return kernel / kernel.sum()


def kernel_rms_norm(kernel_1d: np.ndarray) -> float:
    """sqrt(sum K**2) for the separable ``K = outer(k, k)``: equals sum k**2."""
    return float(np.sum(kernel_1d**2))


def draw_rng(seed: int, run_index: int, site_index: int, draw: int) -> np.random.Generator:
    """The one stream for (run, site, draw), independent of scheduling."""
    sequence = np.random.SeedSequence(
        int(seed), spawn_key=(int(run_index), int(site_index), int(draw))
    )
    return np.random.default_rng(sequence)


def unit_correlated_noise(
    rng: np.random.Generator, shape: tuple[int, int], kernel_1d: np.ndarray
) -> np.ndarray:
    """Steps 1-3: white noise on ``shape`` plus pad, smoothed, rescaled, cropped.

    The pad equals the kernel radius, so every kept pixel has full kernel
    support. The two 1-D passes are exactly the 2-D convolution with
    ``outer(k, k)``.
    """
    radius = (len(kernel_1d) - 1) // 2
    rows, cols = int(shape[0]), int(shape[1])
    white = rng.standard_normal((rows + 2 * radius, cols + 2 * radius))
    smooth = ndimage.correlate1d(white, kernel_1d, axis=0, mode="constant", cval=0.0)
    smooth = smooth[radius : radius + rows]
    smooth = ndimage.correlate1d(smooth, kernel_1d, axis=1, mode="constant", cval=0.0)
    smooth = smooth[:, radius : radius + cols]
    return smooth / kernel_rms_norm(kernel_1d)


def sigmas_from_effres(effres_median_m: float | None, nm: dict) -> dict:
    """Primary sigma = EFFRES / 2.355 (FWHM reading); sensitivity sigma = EFFRES.

    Without EFFRES the plan's 40 m FWHM fallback stands in for the window
    median, for both runs.
    """
    divisor = float(nm["fwhm_to_sigma_divisor"])
    if effres_median_m is None or not (
        math.isfinite(effres_median_m) and effres_median_m > 0.0
    ):
        effres = float(nm["fallback_fwhm_m"])
        source = "fallback_fwhm_m"
    else:
        effres = float(effres_median_m)
        source = "effres_window_median"
    return {
        "effres_m_used": effres,
        "effres_source": source,
        "sigma_m": {"primary": effres / divisor, "sensitivity": effres},
    }


# --------------------------------------------------------------------------
# Per-draw statistics
# --------------------------------------------------------------------------


def slope_statistics(
    slope: np.ndarray,
    circle: np.ndarray,
    cell: np.ndarray,
    *,
    max_slope_deg: float,
    cell_m: float,
) -> dict:
    """≤ 8° fraction within the circle, in-cell count, largest 8-connected patch."""
    finite = np.isfinite(slope)
    passing = finite & (slope <= float(max_slope_deg))
    circle_finite = int(np.count_nonzero(circle & finite))
    in_circle = passing & circle
    fraction = (
        float(np.count_nonzero(in_circle)) / float(circle_finite)
        if circle_finite
        else float("nan")
    )
    labels, count = ndimage.label(in_circle, structure=np.ones((3, 3), dtype=bool))
    largest = int(np.bincount(labels.ravel())[1:].max()) if count else 0
    return {
        "fraction_1km": fraction,
        "circle_finite_pixels": circle_finite,
        "in_cell_count": int(np.count_nonzero(passing & cell)),
        "largest_patch_m2": float(largest) * float(cell_m) ** 2,
    }


@dataclass
class PreparedSite:
    """Everything a worker needs for one site; read once, shared by fork."""

    window: SiteWindow
    dem: np.ndarray  # window + Horn halo, float64
    err: np.ndarray  # same shape, metres
    zero_slope: np.ndarray  # window, float32
    circle: np.ndarray
    cell: np.ndarray
    sigma_m: dict
    effres_source: str
    effres_m_used: float
    window_stats: dict
    kernels: dict = field(default_factory=dict)

    def kernel(self, run_index: int, cell_m: float) -> np.ndarray:
        if run_index not in self.kernels:
            self.kernels[run_index] = gaussian_kernel_1d(
                self.sigma_m[RUN_NAMES[run_index]], cell_m
            )
        return self.kernels[run_index]


def prepare_site(
    window: SiteWindow,
    *,
    dem: np.ndarray,
    err: np.ndarray,
    effres: np.ndarray | None,
    ldsm_err: np.ndarray | None,
    fine_transform,
    nm: dict,
    radius_m: float,
    cell_size_m: float,
    cell_m: float,
    max_slope_deg: float,
) -> PreparedSite:
    """Zero-noise slope, masks, sigma and window statistics for one site.

    ``dem`` and ``err`` carry the one-pixel Horn halo; ``effres`` and
    ``ldsm_err`` are the bare window.
    """
    expected = (window.size + 2 * HALO, window.size + 2 * HALO)
    if dem.shape != expected or err.shape != expected:
        raise ValueError(f"site {window.site_id}: halo window shape is not {expected}")
    zero = slope_tile_from_array(dem, cellsize_m=cell_m, halo=HALO)
    circle, cell = window_masks(window, fine_transform, radius_m=radius_m, cell_size_m=cell_size_m)
    stats: dict = {}
    effres_median = None
    if effres is not None:
        values = effres[np.isfinite(effres) & (effres > 0.0)]
        if values.size:
            effres_median = float(np.median(values))
            stats["effres_m"] = {
                "min": float(values.min()),
                "median": effres_median,
                "max": float(values.max()),
                "valid_pixels": int(values.size),
            }
        else:
            stats["effres_m"] = {"valid_pixels": 0}
    else:
        stats["effres_m"] = None
    # Noise is ERR x u on the window plus halo; a NaN ERR under a finite DEM
    # would turn a valid pixel into a missing one in every draw. Fail closed.
    err_gaps = int(np.count_nonzero(np.isfinite(dem) & ~np.isfinite(err)))
    stats["err_nan_under_finite_dem"] = err_gaps
    if err_gaps:
        raise ValueError(
            f"site {window.site_id}: ERR is missing at {err_gaps} pixels where the DEM "
            "is finite"
        )
    interior_err = err[HALO:-HALO, HALO:-HALO]
    err_valid = interior_err[np.isfinite(interior_err) & (interior_err > 0.0)]
    stats["err_m"] = {
        "median": float(np.median(err_valid)) if err_valid.size else float("nan"),
        "min": float(err_valid.min()) if err_valid.size else float("nan"),
        "max": float(err_valid.max()) if err_valid.size else float("nan"),
        "valid_pixels": int(err_valid.size),
        "window_pixels": int(interior_err.size),
    }
    if ldsm_err is not None:
        ldsm = ldsm_err[np.isfinite(ldsm_err) & (ldsm_err > 0.0)]
        stats["ldsm_err_deg_median"] = float(np.median(ldsm)) if ldsm.size else float("nan")
        stats["ldsm_err_valid_pixels"] = int(ldsm.size)
    sigmas = sigmas_from_effres(effres_median, nm)
    if effres is not None and sigmas["effres_source"] == "fallback_fwhm_m":
        sigmas["effres_source"] = "fallback_fwhm_m (no valid EFFRES in window)"
    elif effres is None:
        sigmas["effres_source"] = "fallback_fwhm_m (EFFRES not staged)"
    stats["sigma_m"] = sigmas["sigma_m"]
    stats["kernel_radius_px"] = {
        name: kernel_radius_px(sigma, cell_m) for name, sigma in sigmas["sigma_m"].items()
    }
    stats["zero_noise"] = slope_statistics(
        zero, circle, cell, max_slope_deg=max_slope_deg, cell_m=cell_m
    )
    if int(np.count_nonzero(cell)) <= 0:
        raise ValueError(f"site {window.site_id}: empty 240 m cell mask")
    stats["cell_pixels"] = int(np.count_nonzero(cell))
    stats["circle_pixels"] = int(np.count_nonzero(circle))
    return PreparedSite(
        window=window,
        dem=dem,
        err=err,
        zero_slope=zero,
        circle=circle,
        cell=cell,
        sigma_m=sigmas["sigma_m"],
        effres_source=sigmas["effres_source"],
        effres_m_used=sigmas["effres_m_used"],
        window_stats=stats,
    )


def evaluate_draw(
    site: PreparedSite,
    run_index: int,
    draw: int,
    *,
    seed: int,
    max_slope_deg: float,
    cell_m: float,
) -> dict:
    """One noise draw for one site and run. Deterministic in its arguments."""
    kernel = site.kernel(run_index, cell_m)
    rng = draw_rng(seed, run_index, site.window.site_index, draw)
    unit = unit_correlated_noise(rng, site.dem.shape, kernel)
    noisy = site.dem + site.err * unit
    slope = slope_tile_from_array(noisy, cellsize_m=cell_m, halo=HALO)
    stats = slope_statistics(
        slope, site.circle, site.cell, max_slope_deg=max_slope_deg, cell_m=cell_m
    )
    expected = site.window_stats["zero_noise"]["circle_finite_pixels"]
    if stats["circle_finite_pixels"] != expected:
        raise ValueError(
            f"site {site.window.site_id} run {run_index} draw {draw}: "
            f"{stats['circle_finite_pixels']} finite circle pixels, zero noise has {expected}"
        )
    interior_unit = unit[HALO:-HALO, HALO:-HALO]
    interior_err = site.err[HALO:-HALO, HALO:-HALO]
    valid = np.isfinite(interior_err) & (interior_err > 0.0)
    # noise / ERR == u wherever ERR is finite and positive.
    ratio = interior_unit[valid]
    diff_valid = np.isfinite(slope) & np.isfinite(site.zero_slope)
    diff = slope[diff_valid].astype(np.float64) - site.zero_slope[diff_valid].astype(np.float64)
    return {
        "run": RUN_NAMES[run_index],
        "run_index": int(run_index),
        "site_id": site.window.site_id,
        "site_index": int(site.window.site_index),
        "draw": int(draw),
        **stats,
        "noise_over_err": {
            "n": int(ratio.size),
            "sum": float(ratio.sum()),
            "sum_sq": float(np.square(ratio).sum()),
        },
        "slope_diff_deg": {
            "n": int(diff.size),
            "sum": float(diff.sum()),
            "sum_sq": float(np.square(diff).sum()),
        },
    }


_WORKER: dict = {}


def _evaluate_task(task: tuple[int, int, int]) -> dict:
    run_index, site_index, draw = task
    context = _WORKER
    return evaluate_draw(
        context["sites"][site_index],
        run_index,
        draw,
        seed=context["seed"],
        max_slope_deg=context["max_slope_deg"],
        cell_m=context["cell_m"],
    )


def evaluate_tasks(
    sites: dict[int, PreparedSite],
    tasks: Sequence[tuple[int, int, int]],
    *,
    seed: int,
    max_slope_deg: float,
    cell_m: float,
    workers: int,
) -> list[dict]:
    """Evaluate (run, site_index, draw) tasks in task order.

    ``workers=1`` is the serial reference. Parallel workers are forked and
    inherit the prepared windows; only the task tuple is pickled.
    """
    if workers <= 0 or workers > 128:
        raise ValueError("workers must be between 1 and 128")
    _WORKER.clear()
    _WORKER.update(
        {
            "sites": sites,
            "seed": int(seed),
            "max_slope_deg": float(max_slope_deg),
            "cell_m": float(cell_m),
        }
    )
    # Build kernels before forking so workers do not each recompute them.
    for run_index, site_index, _ in tasks:
        sites[site_index].kernel(run_index, cell_m)
    try:
        if workers == 1 or len(tasks) <= 1:
            return [_evaluate_task(task) for task in tasks]
        context = get_context("fork")
        chunksize = max(1, len(tasks) // (4 * workers))
        with ProcessPoolExecutor(max_workers=min(workers, len(tasks)), mp_context=context) as pool:
            return list(pool.map(_evaluate_task, tasks, chunksize=chunksize))
    finally:
        _WORKER.clear()


def records_identical(first: Sequence[dict], second: Sequence[dict]) -> bool:
    """Bit-identical per-draw records (floats compared by exact repr)."""
    def canonical(records):
        return [json.dumps(record, sort_keys=True) for record in records]

    return canonical(first) == canonical(second)


# --------------------------------------------------------------------------
# Decision and per-site summaries
# --------------------------------------------------------------------------


def _percentiles(values: np.ndarray, qs: Sequence[float], method: str) -> list[float]:
    return [float(v) for v in np.percentile(values, list(qs), method=method)]


def interval_decision(fractions: Sequence[float], ellipse: dict) -> dict:
    """Frozen distributional rule on the ≤ 8° fraction within 1 km."""
    values = np.asarray(fractions, dtype=np.float64)
    if values.size == 0 or not np.isfinite(values).all():
        raise ValueError("interval decision needs finite fractions")
    low_q, high_q = (float(q) for q in ellipse["interval_percentiles"])
    low, high = _percentiles(values, (low_q, high_q), str(ellipse["percentile_method"]))
    width = high - low
    threshold = float(ellipse["threshold_fraction"])
    width_fired = bool(width > float(ellipse["max_interval_width"]))
    straddle_fired = bool(low < threshold <= high)
    band_low, band_high = (float(v) for v in ellipse["borderline_width_band"])
    if width_fired and straddle_fired:
        clause = "both"
    elif width_fired:
        clause = "width"
    elif straddle_fired:
        clause = "straddle"
    else:
        clause = "neither"
    return {
        "p_low": low,
        "p_high": high,
        "percentiles": [low_q, high_q],
        "width": width,
        "width_clause": width_fired,
        "straddle_clause": straddle_fired,
        "clause_fired": clause,
        "noise_unstable": bool(width_fired or straddle_fired),
        "borderline_width": bool(band_low <= width <= band_high),
    }


def summarize_site_run(zero: dict, records: Sequence[dict], ellipse: dict) -> dict:
    """Reported quantities for one site and run (plan table), plus the decision."""
    method = str(ellipse["percentile_method"])
    threshold = float(ellipse["threshold_fraction"])
    fractions = np.asarray([r["fraction_1km"] for r in records], dtype=np.float64)
    in_cell = np.asarray([r["in_cell_count"] for r in records], dtype=np.float64)
    patch = np.asarray([r["largest_patch_m2"] for r in records], dtype=np.float64)
    zero_fraction = float(zero["fraction_1km"])
    delta = fractions - zero_fraction
    zero_pass = bool(zero_fraction >= threshold)
    passes = fractions >= threshold
    decision = interval_decision(fractions, ellipse)
    p5_fraction, p95_fraction = decision["p_low"], decision["p_high"]
    return {
        "draws": int(fractions.size),
        "fraction_1km": {
            "zero_noise": zero_fraction,
            "mean": float(fractions.mean()),
            "p5": p5_fraction,
            "p95": p95_fraction,
        },
        "decision": decision,
        "delta_vs_zero_noise": {
            "mean": float(delta.mean()),
            "p5": _percentiles(delta, (5,), method)[0],
            "p95": _percentiles(delta, (95,), method)[0],
        },
        "pass_fail": {
            "threshold": threshold,
            "zero_noise_pass": zero_pass,
            "pass_rate": float(passes.mean()),
            "flip_rate": float(np.mean(passes != zero_pass)),
        },
        "in_cell": {
            "fine_pixels": int(zero["cell_pixels"]),
            "zero_noise_count": int(zero["in_cell_count"]),
            "min": float(in_cell.min()),
            "p5": _percentiles(in_cell, (5,), method)[0],
            "median": _percentiles(in_cell, (50,), method)[0],
            "p95": _percentiles(in_cell, (95,), method)[0],
            "fraction_of_draws_count_gt_0": float(np.mean(in_cell > 0)),
        },
        "largest_patch_m2": {
            "zero_noise": float(zero["largest_patch_m2"]),
            "mean": float(patch.mean()),
            "p5": _percentiles(patch, (5,), method)[0],
            "p95": _percentiles(patch, (95,), method)[0],
        },
    }


def _pooled_std(parts: Sequence[dict]) -> float:
    n = sum(int(p["n"]) for p in parts)
    if n < 2:
        return float("nan")
    total = sum(float(p["sum"]) for p in parts)
    total_sq = sum(float(p["sum_sq"]) for p in parts)
    variance = (total_sq - total * total / n) / (n - 1)
    return float(math.sqrt(max(variance, 0.0)))


def noise_checks(
    site: PreparedSite, records: Sequence[dict], acceptance: dict, run: str
) -> dict:
    """Checks 4 and 5 for one site and run, pooled over window and draws."""
    tolerance = float(acceptance["noise_std_ratio_tolerance"])
    factor = float(acceptance["slope_noise_vs_ldsm_err_max_factor"])
    noise_std = _pooled_std([r["noise_over_err"] for r in records])
    slope_std = _pooled_std([r["slope_diff_deg"] for r in records])
    ldsm = site.window_stats.get("ldsm_err_deg_median")
    ratio = (
        slope_std / ldsm
        if ldsm is not None and math.isfinite(ldsm) and ldsm > 0.0
        else float("nan")
    )
    decisive = run in acceptance["slope_noise_check_decisive_runs"]
    within = bool(math.isfinite(ratio) and 1.0 / factor <= ratio <= factor)
    return {
        "noise_std_over_err": {
            "pooled_sample_std": noise_std,
            "within_tolerance": bool(abs(noise_std - 1.0) <= tolerance),
            "tolerance": tolerance,
        },
        "slope_noise_vs_ldsm_err": {
            "slope_diff_std_deg": slope_std,
            "ldsm_err_median_deg": ldsm,
            "ratio": ratio,
            "bounds": [1.0 / factor, factor],
            "within_bounds": within,
            "decisive": bool(decisive),
        },
    }


def zero_noise_check(
    site: PreparedSite,
    stage_b_slope: np.ndarray,
    stage_b_table: dict[str, float],
    *,
    atol_deg: float,
    fraction_ranks: Sequence[int],
) -> dict:
    """Check 3: zero-noise window equals the Stage B slope slice; 1 km fraction."""
    w = site.window
    if w.row0 < 0 or w.col0 < 0:
        raise ValueError(f"site {w.site_id} window leaves the Stage B grid")
    reference = np.asarray(
        stage_b_slope[w.row0 : w.row0 + w.size, w.col0 : w.col0 + w.size],
        dtype=np.float32,
    )
    if reference.shape != site.zero_slope.shape:
        raise ValueError(f"site {w.site_id} Stage B slice has shape {reference.shape}")
    both = np.isfinite(reference) & np.isfinite(site.zero_slope)
    same_nan = bool(np.array_equal(np.isfinite(reference), np.isfinite(site.zero_slope)))
    max_abs = (
        float(np.max(np.abs(reference[both].astype(np.float64) - site.zero_slope[both])))
        if both.any()
        else float("nan")
    )
    slope_ok = bool(same_nan and math.isfinite(max_abs) and max_abs <= float(atol_deg))
    zero_fraction = float(site.window_stats["zero_noise"]["fraction_1km"])
    low_rank, high_rank = (int(v) for v in fraction_ranks)
    fraction: dict = {"applies": bool(low_rank <= w.default_rank <= high_rank)}
    if fraction["applies"]:
        table_value = stage_b_table.get(w.site_id)
        if table_value is None:
            raise ValueError(f"Stage B table has no 1 km fraction for {w.site_id}")
        fraction.update(
            {
                "stage_b_table": float(table_value),
                "zero_noise": zero_fraction,
                # "Equal to two decimals": within half a unit of the second decimal.
                "matches_two_decimals": bool(
                    abs(zero_fraction - float(table_value)) <= 0.005 + 1e-12
                ),
            }
        )
    passed = slope_ok and (not fraction["applies"] or fraction["matches_two_decimals"])
    return {
        "slope_slice": {
            "max_abs_diff_deg": max_abs,
            "atol_deg": float(atol_deg),
            "nan_mask_equal": same_nan,
            "within_atol": slope_ok,
        },
        "fraction_1km": fraction,
        "passed": bool(passed),
    }


def load_stage_b_table(path: Path | None = None) -> dict[str, float]:
    path = Path(path) if path else repo_root() / STAGE_B_CHECKS
    payload = json.loads(path.read_text())
    return {
        f"{int(s['row'])}:{int(s['col'])}": float(s["fraction_le_8deg"]["within_1km"])
        for s in payload["sites"]
    }


# --------------------------------------------------------------------------
# Study driver
# --------------------------------------------------------------------------


def task_list(
    site_indices: Sequence[int], run_indices: Sequence[int], draws: int
) -> list[tuple[int, int, int]]:
    return [
        (run_index, site_index, draw)
        for run_index in run_indices
        for site_index in site_indices
        for draw in range(int(draws))
    ]


def run_noise_study(
    sites: dict[int, PreparedSite],
    *,
    site_indices: Sequence[int],
    run_indices: Sequence[int],
    draws: int,
    seed: int,
    max_slope_deg: float,
    cell_m: float,
    workers: int,
    ellipse: dict,
    acceptance: dict,
    log_path: Path | None = None,
) -> dict:
    """Serial reference, parallel run, identity check and per-site summaries."""
    tasks = task_list(site_indices, run_indices, draws)
    clock = time.perf_counter()
    serial = evaluate_tasks(
        sites, tasks, seed=seed, max_slope_deg=max_slope_deg, cell_m=cell_m, workers=1
    )
    serial_seconds = time.perf_counter() - clock
    _log(log_path, f"serial pass: {len(tasks)} draws in {serial_seconds:.1f} s")
    clock = time.perf_counter()
    parallel = evaluate_tasks(
        sites, tasks, seed=seed, max_slope_deg=max_slope_deg, cell_m=cell_m, workers=workers
    )
    parallel_seconds = time.perf_counter() - clock
    _log(
        log_path,
        f"parallel pass: {len(tasks)} draws, {workers} workers, {parallel_seconds:.1f} s",
    )
    identical = records_identical(serial, parallel)

    per_site: dict[str, dict] = {}
    for site_index in site_indices:
        site = sites[site_index]
        zero = {**site.window_stats["zero_noise"], "cell_pixels": site.window_stats["cell_pixels"]}
        runs = {}
        for run_index in run_indices:
            run = RUN_NAMES[run_index]
            records = [
                r for r in parallel if r["run_index"] == run_index and r["site_index"] == site_index
            ]
            runs[run] = {
                "sigma_m": site.sigma_m[run],
                "decisive": run == "primary",
                **summarize_site_run(zero, records, ellipse),
                "checks": noise_checks(site, records, acceptance, run),
            }
        per_site[site.window.site_id] = runs
    return {
        "records": parallel,
        "per_site": per_site,
        "serial_parallel_identical": identical,
        "timings_seconds": {
            "serial": serial_seconds,
            "parallel": parallel_seconds,
            "tasks": len(tasks),
            "workers": int(workers),
        },
    }


def project_production_node_hours(
    *,
    fixed_seconds: float,
    serial_seconds: float,
    parallel_seconds: float,
    pilot_tasks: Sequence[tuple[int, int]],
    production_tasks: Sequence[tuple[int, int]],
    workers: int,
    write_seconds: float,
    production_node_hours: float,
    walltime_hours: float,
) -> dict:
    """Bound production wall time from the pilot.

    ``*_tasks`` are (padded pixels, kernel length) per draw. A draw's cost is
    taken as the mean pilot draw time scaled by its padded pixel count and by
    its kernel length when longer than the pilot's (the convolution term), so
    the bound is conservative. Production runs every draw twice (serial
    reference, then parallel); the parallel pass is charged its pilot pool
    overhead plus the serial time over ``workers`` at 50 % efficiency.
    """
    if not pilot_tasks or not production_tasks or workers <= 0:
        raise ValueError("projection needs pilot tasks, production tasks and workers")
    if min(fixed_seconds, serial_seconds, parallel_seconds, write_seconds) < 0.0:
        raise ValueError("measured durations must be non-negative")
    per_draw = float(serial_seconds) / len(pilot_tasks)
    ref_pixels = float(np.mean([p for p, _ in pilot_tasks]))
    ref_length = float(np.mean([k for _, k in pilot_tasks]))
    serial_total = sum(
        per_draw * (pixels / ref_pixels) * max(1.0, length / ref_length)
        for pixels, length in production_tasks
    )
    parallel_total = float(parallel_seconds) + 2.0 * serial_total / int(workers)
    write_total = float(write_seconds) * len(production_tasks) / len(pilot_tasks)
    wall = float(fixed_seconds) + serial_total + parallel_total + write_total
    hours = wall / 3600.0
    return {
        "pilot_seconds_per_draw": per_draw,
        "pilot_draws": len(pilot_tasks),
        "production_draws": len(production_tasks),
        "projected_fixed_seconds": float(fixed_seconds),
        "projected_serial_seconds": serial_total,
        "projected_parallel_seconds": parallel_total,
        "projected_write_seconds": write_total,
        "projected_wall_seconds": wall,
        "projected_node_hours": hours,
        "production_node_hours": float(production_node_hours),
        "production_walltime_hours": float(walltime_hours),
        "fits_production_cap": bool(hours <= float(production_node_hours)),
        "fits_walltime": bool(hours <= float(walltime_hours)),
        "method": (
            "mean pilot draw x padded-pixel ratio x max(1, kernel-length ratio) "
            "per production draw; serial pass + parallel pass (pilot pool time "
            "+ 2 x serial / workers) + measured fixed time + scaled writes"
        ),
    }


def _log(log_path: Path | None, message: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} {message}"
    if log_path is None:
        return
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as handle:
        handle.write(line + "\n")


def _atomic_json(path: Path, payload: dict) -> None:
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=True) + "\n")
    os.replace(temporary, path)


def _slurm_metadata() -> dict:
    keys = (
        "SLURM_JOB_ID",
        "SLURM_JOB_ACCOUNT",
        "SLURM_JOB_PARTITION",
        "SLURM_JOB_QOS",
        "SLURM_CPUS_PER_TASK",
        "SLURM_JOB_NUM_NODES",
        "SLURM_MEM_PER_NODE",
    )
    return {key: os.environ[key] for key in keys if key in os.environ}


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
    }


def prepare_sites_from_rasters(
    rule: dict,
    *,
    dem_path: Path,
    err_path: Path,
    effres_path: Path | None,
    ldsm_path: Path,
    coarse_transform,
    dn: dict,
    max_slope_deg: float,
    grid_proj4: str | None = None,
) -> tuple[dict[int, PreparedSite], object]:
    """Read every scope site's windows from the 20 m rasters (no full-cap load)."""
    import rasterio

    cell_m = float(dn["resolution_m"])
    half_px = int(round(float(dn["window_half_size_m"]) / cell_m))
    ranking = {row["id"]: row for row in rule["ranking"]}
    sites: dict[int, PreparedSite] = {}
    with rasterio.open(dem_path) as dem_ds, rasterio.open(err_path) as err_ds, rasterio.open(
        ldsm_path
    ) as ldsm_ds:
        fine_transform = dem_ds.transform
        if abs(fine_transform.a - cell_m) > 1e-9 or abs(fine_transform.e + cell_m) > 1e-9:
            raise ValueError("DEM is not a 20 m north-up grid")
        if grid_proj4 is not None:
            require_same_projection(dem_ds.crs, grid_proj4)
        companions = [err_ds, ldsm_ds]
        effres_ds = rasterio.open(effres_path) if effres_path is not None else None
        try:
            if effres_ds is not None:
                companions.append(effres_ds)
            for companion in companions:
                if companion.shape != dem_ds.shape or companion.transform != fine_transform:
                    raise ValueError(f"{companion.name} is not on the DEM grid")
            for site_index, sid in enumerate(rule["site_order"]):
                row = ranking[sid]
                window = site_window(
                    site_id=sid,
                    site_index=site_index,
                    default_rank=int(row["default_rank"]),
                    row_240=int(row["row"]),
                    col_240=int(row["col"]),
                    coarse_transform=coarse_transform,
                    fine_transform=fine_transform,
                    half_size_px=half_px,
                )
                sites[site_index] = prepare_site(
                    window,
                    dem=read_window(dem_ds, window, halo=HALO),
                    err=read_window(err_ds, window, halo=HALO),
                    effres=(
                        read_window(effres_ds, window, halo=0) if effres_ds is not None else None
                    ),
                    ldsm_err=read_window(ldsm_ds, window, halo=0),
                    fine_transform=fine_transform,
                    nm=dn["noise_model"],
                    radius_m=float(dn["ellipse"]["radius_m"]),
                    cell_size_m=abs(float(coarse_transform.a)),
                    cell_m=cell_m,
                    max_slope_deg=max_slope_deg,
                )
                expected_cell = int(dn["in_cell_fine_pixels"])
                if sites[site_index].window_stats["cell_pixels"] != expected_cell:
                    raise ValueError(
                        f"site {sid}: 240 m cell does not hold {expected_cell} fine pixels"
                    )
        finally:
            if effres_ds is not None:
                effres_ds.close()
    return sites, fine_transform


def _padded_task(site: PreparedSite, run: str, cell_m: float) -> tuple[int, int]:
    radius = kernel_radius_px(site.sigma_m[run], cell_m)
    side = site.dem.shape[0] + 2 * radius
    return side * side, 2 * radius + 1


def _site_report(site: PreparedSite, runs: dict, checks: dict | None) -> dict:
    w = site.window
    return {
        "site_id": w.site_id,
        "site_index": w.site_index,
        "default_rank": w.default_rank,
        "row_240m": w.row_240,
        "col_240m": w.col_240,
        "centre_xy_m": [w.centre_x, w.centre_y],
        "window": {"row0": w.row0, "col0": w.col0, "size": w.size, "halo": HALO},
        "effres_source": site.effres_source,
        "effres_m_used": site.effres_m_used,
        "window_stats": site.window_stats,
        "zero_noise_check": checks,
        "runs": runs,
    }


def run_stage_d(
    *,
    mode: str,
    input_manifest: Path,
    dem: Path,
    raw_dir: Path,
    stage_b_slope: Path,
    mc_summary: Path,
    output_dir: Path,
    workers: int,
) -> dict:
    """Pilot or production on real inputs. Refuses before noise on any bad pin."""
    from anuma.grid import Grid
    from anuma.hpc_smoke import ARRAY_FILES, validate_input_manifest

    if mode not in ("pilot", "production"):
        raise ValueError("mode must be pilot or production")
    if workers <= 0 or workers > 128:
        raise ValueError("workers must be between 1 and 128")
    started_utc = datetime.now(timezone.utc).isoformat()
    clock = time.perf_counter()
    cfg = load_config()
    dn = require_frozen_config(cfg)
    max_slope_deg = float(_lookup(cfg, dn["ellipse"]["max_slope_deg_from"]))
    cell_m = float(dn["resolution_m"])

    inputs = expected_inputs(
        raw_dir=raw_dir, dem=dem, stage_b_slope=stage_b_slope, mc_summary=mc_summary
    )
    output_dir = Path(output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "progress.log"

    # Digests are checked here, inside the compute job, before any read.
    digests = verify_input_digests(inputs)
    _log(log_path, f"verified {len(digests)} input digests")
    manifest, files = validate_input_manifest(Path(input_manifest))
    grid = Grid.from_json(files["grid"], cfg=cfg)
    layers = {
        name: np.load(files[name], mmap_mode="r", allow_pickle=False)
        for name in ARRAY_FILES
        if name != "dem"
    }
    summary = load_study_summary(inputs["mc_summary"]["path"])
    rule = recompute_stability_rule(summary, default_ranking(layers, grid, cfg), cfg)
    _log(log_path, f"stability rule reproduced: {rule['unstable_ids']}")

    effres_path = inputs["effres"]["path"] if "effres" in inputs else None
    sites, _ = prepare_sites_from_rasters(
        rule,
        dem_path=inputs["dem"]["path"],
        err_path=inputs["err"]["path"],
        effres_path=effres_path,
        ldsm_path=inputs["ldsm_err"]["path"],
        coarse_transform=grid.affine,
        dn=dn,
        max_slope_deg=max_slope_deg,
        grid_proj4=grid.proj4,
    )
    stage_b_table = load_stage_b_table()
    stage_b = np.load(inputs["stage_b_slope"]["path"], mmap_mode="r", allow_pickle=False)

    if mode == "pilot":
        site_indices = list(range(PILOT_SITE_COUNT))
        run_indices = [0]
        draws = PILOT_DRAWS
    else:
        site_indices = list(range(len(rule["site_order"])))
        run_indices = [0, 1]
        draws = int(dn["draws"])
    zero_checks = {
        sites[i].window.site_id: zero_noise_check(
            sites[i],
            stage_b,
            stage_b_table,
            atol_deg=float(dn["acceptance"]["zero_noise_slope_atol_deg"]),
            fraction_ranks=dn["acceptance"]["stage_b_fraction_check_ranks"],
        )
        for i in site_indices
    }
    fixed_seconds = time.perf_counter() - clock
    _log(log_path, f"inputs ready in {fixed_seconds:.1f} s; zero-noise checks done")

    study = run_noise_study(
        sites,
        site_indices=site_indices,
        run_indices=run_indices,
        draws=draws,
        seed=int(dn["seed"]),
        max_slope_deg=max_slope_deg,
        cell_m=cell_m,
        workers=workers,
        ellipse=dn["ellipse"],
        acceptance=dn["acceptance"],
        log_path=log_path,
    )

    clock = time.perf_counter()
    sites_dir = output_dir / "sites"
    sites_dir.mkdir()
    draws_path = output_dir / "draws.jsonl"
    with draws_path.open("w") as handle:
        for record in study["records"]:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    site_files = []
    for index in site_indices:
        site = sites[index]
        sid = site.window.site_id
        path = sites_dir / f"site-{sid.replace(':', '-')}.json"
        _atomic_json(path, _site_report(site, study["per_site"][sid], zero_checks[sid]))
        site_files.append(path)
    write_seconds = time.perf_counter() - clock

    checks = _acceptance(rule, zero_checks, study, mode)
    report: dict = {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "mode": mode,
        "kind": "screening sensitivity, not a landing recommendation",
        "note": (
            "Not affiliated with NASA, ESA or ispace. Tests LOLA height error "
            "as ANUMA reads it (1 sigma, Gaussian, one length scale per site, "
            "EFFRES as FWHM); see docs/stage-d-plan.md."
        ),
        "started_at_utc": started_utc,
        "completed_at_utc": None,
        "plan": dn["plan"],
        "frozen_on": dn["frozen_on"],
        "stability_rule": rule,
        "scope": {
            "site_order": rule["site_order"],
            "evaluated_site_ids": [sites[i].window.site_id for i in site_indices],
            "runs": [RUN_NAMES[i] for i in run_indices],
            "draws_per_site_and_run": draws,
            "seed": int(dn["seed"]),
            "stream": "default_rng(SeedSequence(seed, spawn_key=(run_index, site_index, draw)))",
        },
        "effres": {
            "staged": effres_path is not None,
            "note": (
                None
                if effres_path is not None
                else "EFFRES not staged: the 40 m FWHM fallback replaces the primary sigma"
            ),
            "per_site_source": {sites[i].window.site_id: sites[i].effres_source for i in sites},
        },
        "sites": {
            sites[i].window.site_id: {
                "default_rank": sites[i].window.default_rank,
                "window_stats": sites[i].window_stats,
                "zero_noise_check": zero_checks[sites[i].window.site_id],
                "runs": study["per_site"][sites[i].window.site_id],
            }
            for i in site_indices
        },
        "checks": checks,
        "timings_seconds": {
            "fixed": fixed_seconds,
            **study["timings_seconds"],
            "write": write_seconds,
        },
        "inputs": {
            "input_manifest": {
                "path": str(Path(input_manifest).resolve()),
                "sha256": sha256_file(Path(input_manifest).resolve()),
                "declared_id": manifest.get("id"),
            },
            **{
                name: {"path": str(Path(entry["path"]).resolve()), "sha256": digests[name]}
                for name, entry in inputs.items()
            },
            "stage_b_checks": str(STAGE_B_CHECKS),
        },
        "software": _software(),
        "execution": {"workers": int(workers), **_slurm_metadata()},
    }
    if mode == "pilot":
        production_tasks = [
            _padded_task(sites[i], RUN_NAMES[r], cell_m)
            for r in (0, 1)
            for i in sorted(sites)
            for _ in range(int(dn["draws"]))
        ]
        pilot_tasks = [
            _padded_task(sites[i], RUN_NAMES[r], cell_m)
            for r in run_indices
            for i in site_indices
            for _ in range(draws)
        ]
        from anuma.stage_d_gate import NODE_HOURS

        report["projection"] = project_production_node_hours(
            fixed_seconds=fixed_seconds,
            serial_seconds=study["timings_seconds"]["serial"],
            parallel_seconds=study["timings_seconds"]["parallel"],
            pilot_tasks=pilot_tasks,
            production_tasks=production_tasks,
            workers=workers,
            write_seconds=write_seconds,
            production_node_hours=float(dn["execution"]["production_node_hours"]),
            walltime_hours=float(NODE_HOURS["production"]),
        )
        report["window_preview"] = {
            sites[i].window.site_id: {
                "effres_source": sites[i].effres_source,
                "effres_m": sites[i].window_stats["effres_m"],
                "sigma_m": sites[i].sigma_m,
                "kernel_radius_px": sites[i].window_stats["kernel_radius_px"],
            }
            for i in sorted(sites)
        }
        checks["projection_fits"] = bool(
            report["projection"]["fits_production_cap"] and report["projection"]["fits_walltime"]
        )
        report["passed"] = bool(
            checks["rule_reproduced"]
            and checks["serial_parallel_identical"]
            and checks["zero_noise_reproduced"]
            and checks["noise_std_within_tolerance"]
            and checks["slope_noise_primary_within_bounds"]
            and checks["projection_fits"]
        )
    else:
        report["verdict"] = {
            sid: {
                "noise_unstable": runs["primary"]["decision"]["noise_unstable"],
                "clause_fired": runs["primary"]["decision"]["clause_fired"],
                "width": runs["primary"]["decision"]["width"],
                "borderline_width": runs["primary"]["decision"]["borderline_width"],
                "sensitivity_noise_unstable": runs["sensitivity"]["decision"]["noise_unstable"],
                "sensitivity_clause_fired": runs["sensitivity"]["decision"]["clause_fired"],
            }
            for sid, runs in study["per_site"].items()
        }
        report["passed"] = bool(
            checks["rule_reproduced"]
            and checks["serial_parallel_identical"]
            and checks["zero_noise_reproduced"]
            and checks["slope_noise_primary_within_bounds"]
        )
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    name = "pilot-measurement.json" if mode == "pilot" else "stage-d-summary.json"
    report_path = output_dir / name
    _atomic_json(report_path, report)
    _write_output_manifest(output_dir, report, [draws_path, *site_files, report_path])
    _log(log_path, f"{mode} written; passed={report['passed']}")
    return report


def _acceptance(rule: dict, zero_checks: dict, study: dict, mode: str) -> dict:
    noise_ok = []
    primary_slope_ok = []
    sensitivity_slope = {}
    for sid, runs in study["per_site"].items():
        for run, payload in runs.items():
            checks = payload["checks"]
            noise_ok.append(checks["noise_std_over_err"]["within_tolerance"])
            if checks["slope_noise_vs_ldsm_err"]["decisive"]:
                primary_slope_ok.append(checks["slope_noise_vs_ldsm_err"]["within_bounds"])
            else:
                sensitivity_slope[sid] = checks["slope_noise_vs_ldsm_err"]["ratio"]
    return {
        "rule_reproduced": bool(rule["reproduces_frozen_unstable_ids"]),
        "serial_parallel_identical": bool(study["serial_parallel_identical"]),
        "zero_noise_reproduced": bool(all(c["passed"] for c in zero_checks.values())),
        "noise_std_within_tolerance": bool(all(noise_ok)),
        "noise_std_decisive": mode == "pilot",
        "slope_noise_primary_within_bounds": bool(primary_slope_ok and all(primary_slope_ok)),
        "slope_noise_sensitivity_ratio_reported": sensitivity_slope,
    }


def _write_output_manifest(output_dir: Path, report: dict, paths: Sequence[Path]) -> None:
    outputs = {}
    for path in paths:
        path = Path(path)
        outputs[str(path.relative_to(output_dir))] = {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
    _atomic_json(output_dir / "output-manifest.json", {**report, "outputs": outputs})


def rule_from_grid_dir(grid_dir: Path, mc_summary: Path) -> dict:
    """Laptop path: recompute the rule from ``data/grid`` arrays."""
    from anuma.grid import Grid

    cfg = load_config()
    grid_dir = Path(grid_dir)
    grid = Grid.from_json(grid_dir / "grid.json", cfg=cfg)
    layers = {
        name: np.load(grid_dir / f"{name}.npy", mmap_mode="r", allow_pickle=False)
        for name in ("slope", "illumination", "hydrogen", "earth_visibility")
    }
    summary = load_study_summary(mc_summary)
    return recompute_stability_rule(summary, default_ranking(layers, grid, cfg), cfg)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("rule", "pilot", "production"), required=True)
    parser.add_argument("--mc-summary", type=Path, required=True)
    parser.add_argument("--grid-dir", type=Path, default=None, help="rule mode only")
    parser.add_argument("--input-manifest", type=Path, default=None)
    parser.add_argument("--dem", type=Path, default=None)
    parser.add_argument("--raw-dir", type=Path, default=None)
    parser.add_argument("--stage-b-slope", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        if args.mode == "rule":
            if args.grid_dir is None:
                parser.error("--grid-dir is required for rule mode")
            report = rule_from_grid_dir(args.grid_dir, args.mc_summary)
        else:
            for name in ("input_manifest", "dem", "raw_dir", "stage_b_slope", "output_dir"):
                if getattr(args, name) is None:
                    parser.error(f"--{name.replace('_', '-')} is required for {args.mode}")
            report = run_stage_d(
                mode=args.mode,
                input_manifest=args.input_manifest,
                dem=args.dem,
                raw_dir=args.raw_dir,
                stage_b_slope=args.stage_b_slope,
                mc_summary=args.mc_summary,
                output_dir=args.output_dir,
                workers=args.workers,
            )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Stage D precondition failed: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.mode == "pilot" and not report["checks"]["projection_fits"]:
        raise SystemExit(4)
    if args.mode != "rule" and not report["passed"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
