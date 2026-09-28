"""Landing-site scoring on the common south-polar grid.

Thresholds, weights, percentile clips, and separation come from
`config/layers.yaml`. Do not copy those numbers into callers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from matplotlib.path import Path as MplPath

from anuma.config import load_config
from anuma.grid import Grid


@dataclass
class ScoreResult:
    """Weighted score, per-layer 0–1 maps, and the hard-constraint mask."""

    combined: np.ndarray
    normalized: dict[str, np.ndarray]
    hard_mask: np.ndarray
    weights: dict[str, float]
    grid: Grid
    cfg: dict = field(repr=False)


def _cfg(cfg: dict | None = None) -> dict:
    return cfg if cfg is not None else load_config()


def hard_mask(
    slope: np.ndarray,
    max_slope_deg: float | None = None,
    cfg: dict | None = None,
) -> np.ndarray:
    """True where slope is finite and at or below the HLS threshold."""
    cfg = _cfg(cfg)
    if max_slope_deg is None:
        max_slope_deg = float(cfg["hard"]["max_slope_deg"])
    finite = np.isfinite(slope)
    return finite & (slope <= float(max_slope_deg))


def normalize(
    layer: np.ndarray,
    mode: str,
    cfg: dict | None = None,
) -> np.ndarray:
    """Scale a layer to 0–1 with 2nd/98th percentile clip from config."""
    cfg = _cfg(cfg)
    low_p = float(cfg["scoring"]["percentile_low"])
    high_p = float(cfg["scoring"]["percentile_high"])
    out = np.full(layer.shape, np.nan, dtype=np.float32)
    finite = np.isfinite(layer)
    if not finite.any():
        return out
    values = layer[finite].astype(np.float64)
    p_low = float(np.percentile(values, low_p))
    p_high = float(np.percentile(values, high_p))
    span = p_high - p_low
    clipped = np.clip(layer.astype(np.float64), p_low, p_high)
    if span == 0.0:
        scaled = np.full(layer.shape, 0.5, dtype=np.float64)
    elif mode == "higher_better":
        scaled = (clipped - p_low) / span
    elif mode == "lower_better":
        scaled = (p_high - clipped) / span
    else:
        raise ValueError(f"Unknown normalize mode: {mode!r}")
    out[finite] = scaled[finite].astype(np.float32)
    return out


def score(
    layers: dict[str, np.ndarray],
    weights: dict[str, float] | None = None,
    hard: dict | None = None,
    grid: Grid | None = None,
    cfg: dict | None = None,
) -> ScoreResult:
    """Weighted sum of normalized soft layers, masked by slope and no-data."""
    cfg = _cfg(cfg)
    if weights is None:
        weights = dict(cfg["scoring"]["weights"])
    if hard is None:
        hard = {"max_slope_deg": float(cfg["hard"]["max_slope_deg"])}
    if grid is None:
        grid = Grid(cfg)
    if "slope" not in layers:
        raise KeyError("layers must include 'slope' for the hard mask")
    mask = hard_mask(layers["slope"], hard.get("max_slope_deg"), cfg=cfg)
    normalized: dict[str, np.ndarray] = {}
    combined = np.zeros(layers["slope"].shape, dtype=np.float64)
    for name, weight in weights.items():
        if name not in layers:
            raise KeyError(f"weighted layer {name!r} missing from layers")
        mode = cfg["layers"][name]["mode"]
        norm = normalize(layers[name], mode, cfg=cfg)
        normalized[name] = norm
        mask = mask & np.isfinite(layers[name]) & np.isfinite(norm)
        combined = combined + float(weight) * norm.astype(np.float64)
    combined = np.where(mask, combined, np.nan).astype(np.float32)
    return ScoreResult(
        combined=combined,
        normalized=normalized,
        hard_mask=mask,
        weights={k: float(v) for k, v in weights.items()},
        grid=grid,
        cfg=cfg,
    )


def _pixel_center_lonlat(grid: Grid, row: int, col: int) -> tuple[float, float]:
    return grid.to_lonlat(row + 0.5, col + 0.5)


def rank_sites(
    result: ScoreResult,
    n: int | None = None,
    min_separation_km: float | None = None,
) -> list[dict]:
    """Greedy non-maximum suppression on the masked score."""
    cfg = result.cfg
    if n is None:
        n = int(cfg["scoring"]["n_sites"])
    if min_separation_km is None:
        min_separation_km = float(cfg["scoring"]["min_separation_km"])
    sep_m = float(min_separation_km) * 1000.0
    remaining = result.combined.astype(np.float64).copy()
    x, y = result.grid.pixel_centers()
    sites: list[dict] = []
    for _ in range(int(n)):
        if not np.isfinite(remaining).any():
            break
        flat = int(np.nanargmax(remaining))
        row, col = np.unravel_index(flat, remaining.shape)
        row_i, col_i = int(row), int(col)
        total = float(remaining[row_i, col_i])
        contributions = {
            name: float(weight * result.normalized[name][row_i, col_i])
            for name, weight in result.weights.items()
        }
        lon, lat = _pixel_center_lonlat(result.grid, row_i, col_i)
        sites.append(
            {
                "lon": lon,
                "lat": lat,
                "row": row_i,
                "col": col_i,
                "total": total,
                "contributions": contributions,
            }
        )
        dx = x - x[row_i, col_i]
        dy = y - y[row_i, col_i]
        remaining[(dx * dx + dy * dy) <= sep_m * sep_m] = np.nan
    return sites


def _load_geojson(regions_geojson) -> dict:
    if isinstance(regions_geojson, dict):
        return regions_geojson
    path = Path(regions_geojson)
    return json.loads(path.read_text())


def _rings_lonlat(geometry: dict | None) -> list[np.ndarray]:
    """Exterior rings as (N, 2) lon/lat arrays. Empty if geometry is null."""
    if not geometry or not geometry.get("coordinates"):
        return []
    gtype = geometry.get("type")
    coords = geometry["coordinates"]
    if gtype == "Polygon":
        return [np.asarray(coords[0], dtype=np.float64)]
    if gtype == "MultiPolygon":
        return [np.asarray(poly[0], dtype=np.float64) for poly in coords]
    return []


def _ring_xy(grid: Grid, ring_ll: np.ndarray) -> np.ndarray:
    lon = ring_ll[:, 0]
    lat = ring_ll[:, 1]
    x, y = grid.fwd.transform(lon, lat)
    return np.column_stack([np.asarray(x), np.asarray(y)])


def _point_to_segment_m(
    px: float, py: float, ax: np.ndarray, ay: np.ndarray, bx: np.ndarray, by: np.ndarray
) -> np.ndarray:
    abx = bx - ax
    aby = by - ay
    ab2 = abx * abx + aby * aby
    t = np.where(ab2 > 0.0, ((px - ax) * abx + (py - ay) * aby) / ab2, 0.0)
    t = np.clip(t, 0.0, 1.0)
    qx = ax + t * abx
    qy = ay + t * aby
    return np.hypot(px - qx, py - qy)


def _distance_km(grid: Grid, lon: float, lat: float, rings_xy: list[np.ndarray]) -> float | None:
    """Distance to the nearest ring in the stereo plane. 0 if inside. None if no rings."""
    if not rings_xy:
        return None
    px, py = grid.fwd.transform(lon, lat)
    px, py = float(px), float(py)
    inside = False
    best = np.inf
    for ring in rings_xy:
        path = MplPath(ring)
        if path.contains_point((px, py)):
            inside = True
        ax, ay = ring[:-1, 0], ring[:-1, 1]
        bx, by = ring[1:, 0], ring[1:, 1]
        if ax.size:
            best = min(best, float(np.min(_point_to_segment_m(px, py, ax, ay, bx, by))))
    if inside:
        return 0.0
    if not np.isfinite(best):
        return None
    return best / 1000.0


def _region_mean_score(
    result: ScoreResult, rings_xy: list[np.ndarray]
) -> float:
    if not rings_xy:
        return float("nan")
    x, y = result.grid.pixel_centers()
    inside = np.zeros(result.combined.shape, dtype=bool)
    for ring in rings_xy:
        xmin, xmax = float(ring[:, 0].min()), float(ring[:, 0].max())
        ymin, ymax = float(ring[:, 1].min()), float(ring[:, 1].max())
        bbox = (x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax)
        if not bbox.any():
            continue
        pts = np.column_stack([x[bbox], y[bbox]])
        hit = MplPath(ring).contains_points(pts)
        sel = np.zeros(result.combined.shape, dtype=bool)
        sel[bbox] = hit
        inside |= sel
    values = result.combined[inside]
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return float("nan")
    return float(np.mean(finite))


def artemis_overlap(
    sites: list[dict],
    regions_geojson,
    result: ScoreResult | None = None,
) -> dict:
    """Nearest region per site, plus mean score and rank among the regions.

    Null geometries are skipped: nearest is None and mean_score is NaN.
    Raster mean uses `result` when given; otherwise the mean of ranked
    sites assigned to that region.
    """
    cfg = result.cfg if result is not None else load_config()
    grid = result.grid if result is not None else Grid(cfg)
    collection = _load_geojson(regions_geojson)
    features = collection.get("features") or []
    prepared = []
    for feat in features:
        props = feat.get("properties") or {}
        name = props.get("name")
        rings = [_ring_xy(grid, ring) for ring in _rings_lonlat(feat.get("geometry"))]
        prepared.append({"name": name, "rings": rings})

    site_rows = []
    assigned_totals: dict[str, list[float]] = {p["name"]: [] for p in prepared}
    for site in sites:
        nearest = None
        nearest_d = None
        for region in prepared:
            dist = _distance_km(grid, site["lon"], site["lat"], region["rings"])
            if dist is None:
                continue
            if nearest_d is None or dist < nearest_d:
                nearest = region["name"]
                nearest_d = dist
        row = dict(site)
        row["nearest"] = nearest
        row["distance_km"] = nearest_d
        site_rows.append(row)
        if nearest is not None:
            assigned_totals[nearest].append(float(site["total"]))

    region_rows = []
    for region in prepared:
        if result is not None and region["rings"]:
            mean_score = _region_mean_score(result, region["rings"])
        elif assigned_totals[region["name"]]:
            mean_score = float(np.mean(assigned_totals[region["name"]]))
        else:
            mean_score = float("nan")
        region_rows.append(
            {"name": region["name"], "mean_score": mean_score, "rank": None}
        )

    order = sorted(
        range(len(region_rows)),
        key=lambda i: (
            not np.isfinite(region_rows[i]["mean_score"]),
            -(region_rows[i]["mean_score"])
            if np.isfinite(region_rows[i]["mean_score"])
            else 0.0,
            region_rows[i]["name"] or "",
        ),
    )
    rank = 1
    for i in order:
        if np.isfinite(region_rows[i]["mean_score"]):
            region_rows[i]["rank"] = rank
            rank += 1
        else:
            region_rows[i]["rank"] = None

    return {"sites": site_rows, "regions": region_rows}
