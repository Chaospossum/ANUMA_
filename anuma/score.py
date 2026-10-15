"""Landing-site scoring on the common south-polar grid.

Thresholds, weights, percentile clips, and separation come from
`config/layers.yaml`. Do not copy those numbers into callers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

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
    normalization_policy: str
    cfg: dict = field(repr=False)
    ranking_inputs: dict[str, np.ndarray] = field(repr=False)
    terrain_gate: dict = field(default_factory=dict)


def _cfg(cfg: dict | None = None) -> dict:
    return cfg if cfg is not None else load_config()


def terrain_pass(
    fraction: np.ndarray, min_fraction: float, shape: tuple[int, ...] | None = None
) -> np.ndarray:
    """True where the 1 km flat-ground fraction is finite and >= ``min_fraction``."""
    fraction = np.asarray(fraction)
    if shape is not None and fraction.shape != tuple(shape):
        raise ValueError("terrain fraction layer does not match the slope grid")
    with np.errstate(invalid="ignore"):
        return np.isfinite(fraction) & (fraction >= float(min_fraction))


def hard_mask(
    slope: np.ndarray,
    max_slope_deg: float | None = None,
    cfg: dict | None = None,
    slope_radius_m: float | None = None,
    resolution_m: float | None = None,
    terrain_fraction: np.ndarray | None = None,
    terrain_min_fraction: float | None = None,
) -> np.ndarray:
    """True where slope is finite and at or below the HLS threshold.

    With ``terrain_fraction`` (the Stage F ``terrain_1km_fraction`` layer) a
    pixel must also have a finite fraction ``>= terrain_min_fraction``; NaN
    fails. Callers decide whether the gate applies (``terrain_gate``).

    With ``slope_radius_m`` > 0 a pixel passes only if every pixel whose
    centre lies within that distance of its own centre also passes. Pixel
    centres beyond the array edge count as failing, because their slope is
    unknown. ``resolution_m`` is the pixel size of ``slope`` and is required
    whenever the radius is positive.
    """
    cfg = _cfg(cfg)
    if max_slope_deg is None:
        max_slope_deg = float(cfg["hard"]["max_slope_deg"])
    if slope_radius_m is None:
        slope_radius_m = float(cfg["hard"].get("slope_radius_m", 0.0))
    radius = float(slope_radius_m)
    if not np.isfinite(radius) or radius < 0.0:
        raise ValueError("slope_radius_m must be finite and non-negative")
    finite = np.isfinite(slope)
    passing = finite & (slope <= float(max_slope_deg))
    if terrain_fraction is not None:
        if terrain_min_fraction is None:
            raise ValueError("terrain_min_fraction is required with terrain_fraction")
        terrain_ok = terrain_pass(terrain_fraction, terrain_min_fraction, slope.shape)
    else:
        terrain_ok = None
    if radius == 0.0:
        return passing if terrain_ok is None else passing & terrain_ok
    if resolution_m is None:
        raise ValueError("resolution_m is required when slope_radius_m is positive")
    # Distance from each pixel centre to the nearest failing centre, with a
    # failing one-pixel frame standing in for the area off the grid.
    from scipy.ndimage import distance_transform_edt

    padded = np.pad(passing, 1, constant_values=False)
    distance_m = distance_transform_edt(padded, sampling=float(resolution_m))[1:-1, 1:-1]
    within = distance_m > radius
    return within if terrain_ok is None else within & terrain_ok


def normalization_rule(cfg: dict, policy: str, layer_name: str) -> dict:
    """Resolve one layer's declared rule for a named normalization policy."""
    policies = cfg["scoring"]["normalization_policies"]
    if policy not in policies:
        raise ValueError(f"Unknown normalization policy: {policy!r}")
    policy_cfg = policies[policy]
    rule = (policy_cfg.get("layers") or {}).get(layer_name, policy_cfg.get("default"))
    if not isinstance(rule, dict):
        raise ValueError(f"Policy {policy!r} has no rule for layer {layer_name!r}")
    return rule


def normalize(
    layer: np.ndarray,
    mode: str,
    *,
    layer_name: str,
    policy: str,
    valid_mask: np.ndarray | None = None,
    cfg: dict | None = None,
) -> np.ndarray:
    """Scale one layer to 0–1 using its configured policy rule."""
    cfg = _cfg(cfg)
    rule = normalization_rule(cfg, policy, layer_name)
    out = np.full(layer.shape, np.nan, dtype=np.float32)
    finite = np.isfinite(layer)
    if rule.get("scope", "valid") == "hard_mask":
        if valid_mask is None:
            raise ValueError(f"{layer_name} normalization requires the hard mask")
        finite &= valid_mask
    if not finite.any():
        return out
    values = layer[finite].astype(np.float64)
    method = rule["method"]
    if method == "min_max":
        low = float(np.min(values))
        high = float(np.max(values))
    elif method == "percentile":
        low = float(np.percentile(values, float(rule["percentile_low"])))
        high = float(np.percentile(values, float(rule["percentile_high"])))
    else:
        raise ValueError(f"Unknown normalization method: {method!r}")
    span = high - low
    clipped = np.clip(layer.astype(np.float64), low, high)
    if span == 0.0:
        scaled = np.full(layer.shape, 0.5, dtype=np.float64)
    elif mode == "higher_better":
        scaled = (clipped - low) / span
    elif mode == "lower_better":
        scaled = (high - clipped) / span
    else:
        raise ValueError(f"Unknown normalize mode: {mode!r}")
    out[finite] = scaled[finite].astype(np.float32)
    return out


def is_display_only(cfg: dict, name: str) -> bool:
    return bool((cfg["layers"].get(name) or {}).get("display_only"))


def is_gate_layer(cfg: dict, name: str) -> bool:
    return bool((cfg["layers"].get(name) or {}).get("gate"))


TERRAIN_STATUSES = ("applied", "disabled", "unavailable", "slope_mismatch")


def terrain_gate(
    layers: dict[str, np.ndarray], hard: dict, cfg: dict, max_slope_deg: float
) -> dict:
    """Decide whether the Stage F 20 m terrain gate applies to this request.

    ``hard`` keys read here (all optional):

    - ``terrain_1km``: True/False forces the gate on/off; None uses config;
    - ``terrain_1km_min_fraction``: threshold in [0, 1], default from config;
    - ``terrain_1km_max_slope_deg``: slope limit the layer was built for
      (the API passes its sidecar value), default ``hard.max_slope_deg``;
    - ``terrain_1km_unavailable_reason``: why the layer is absent.

    The fraction exists for one slope limit only. If the request's max slope
    differs, the gate is not applied (status ``slope_mismatch``), because
    an 8° flat-ground rule under a different slope limit would be a third
    rule nobody asked for; an explicit ``terrain_1km: True`` then raises.
    """
    spec = cfg["hard"].get("terrain_1km") or {}
    name = str(spec.get("layer", "terrain_1km_fraction"))
    requested = hard.get("terrain_1km")
    enabled = bool(spec.get("enabled", False)) if requested is None else bool(requested)
    min_fraction = hard.get("terrain_1km_min_fraction")
    if min_fraction is None:
        min_fraction = spec.get("min_fraction", 0.5)
    min_fraction = float(min_fraction)
    if not (np.isfinite(min_fraction) and 0.0 <= min_fraction <= 1.0):
        raise ValueError("terrain_1km_min_fraction must be between 0 and 1")
    layer_slope = hard.get("terrain_1km_max_slope_deg")
    if layer_slope is None:
        layer_slope = cfg["hard"]["max_slope_deg"]
    layer_slope = float(layer_slope)
    info = {
        "status": "applied",
        "layer": name,
        "requested": requested,
        "min_fraction": min_fraction,
        "radius_m": float(spec.get("radius_m", 1000.0)),
        "layer_max_slope_deg": layer_slope,
        "max_slope_deg": float(max_slope_deg),
        "message": None,
    }
    if not enabled:
        info.update(status="disabled", message="20 m terrain rule switched off")
    elif name not in layers:
        reason = hard.get("terrain_1km_unavailable_reason") or f"{name}.npy is not on this machine"
        info.update(
            status="unavailable",
            message=f"20 m terrain rule not applied: {reason}",
        )
    elif not np.isclose(float(max_slope_deg), layer_slope, rtol=0.0, atol=1e-9):
        message = (
            f"20 m terrain rule uses {layer_slope:g}° and cannot be recomputed for "
            f"{float(max_slope_deg):g}°; not applied"
        )
        if requested is True:
            raise ValueError(message)
        info.update(status="slope_mismatch", message=message)
    return info


def scored_weights(weights: dict[str, float], cfg: dict) -> dict[str, float]:
    """Validate weights and drop display-only layers given weight 0.

    A display-only layer (e.g. dem_error) never enters the score or the
    no-data mask; a positive weight for one is an error.
    """
    validated: dict[str, float] = {}
    for name, weight in weights.items():
        value = float(weight)
        if not np.isfinite(value) or value < 0.0:
            raise ValueError(f"weight for {name!r} must be finite and non-negative")
        if is_display_only(cfg, name):
            if value != 0.0:
                raise ValueError(f"{name!r} is display only and cannot be weighted")
            continue
        if is_gate_layer(cfg, name):
            if value != 0.0:
                raise ValueError(f"{name!r} is a hard-gate layer and cannot be weighted")
            continue
        validated[name] = value
    return validated


def score(
    layers: dict[str, np.ndarray],
    weights: dict[str, float] | None = None,
    hard: dict | None = None,
    grid: Grid | None = None,
    cfg: dict | None = None,
    normalization_policy: str | None = None,
) -> ScoreResult:
    """Weighted sum of normalized soft layers, masked by slope and no-data."""
    cfg = _cfg(cfg)
    if normalization_policy is None:
        normalization_policy = str(cfg["scoring"]["default_normalization_policy"])
    if weights is None:
        weights = dict(cfg["scoring"]["weights"])
    if hard is None:
        hard = {"max_slope_deg": float(cfg["hard"]["max_slope_deg"])}
    if grid is None:
        grid = Grid(cfg)
    if "slope" not in layers:
        raise KeyError("layers must include 'slope' for the hard mask")
    weights = scored_weights(weights, cfg)
    max_slope = hard.get("max_slope_deg")
    if max_slope is None:
        max_slope = float(cfg["hard"]["max_slope_deg"])
    gate = terrain_gate(layers, hard, cfg, float(max_slope))
    slope_only = hard_mask(
        layers["slope"],
        max_slope,
        cfg=cfg,
        slope_radius_m=hard.get("slope_radius_m"),
        resolution_m=grid.resolution_m,
    )
    mask = slope_only
    if gate["status"] == "applied":
        terrain_ok = terrain_pass(layers[gate["layer"]], gate["min_fraction"], slope_only.shape)
        mask = slope_only & terrain_ok
        gate["cells_passing_slope"] = int(np.count_nonzero(slope_only))
        gate["cells_removed"] = int(np.count_nonzero(slope_only & ~terrain_ok))
    normalized: dict[str, np.ndarray] = {}
    combined = np.zeros(layers["slope"].shape, dtype=np.float64)
    for name, weight in weights.items():
        if name not in layers:
            raise KeyError(f"weighted layer {name!r} missing from layers")
        mode = cfg["layers"][name]["mode"]
        norm = normalize(
            layers[name],
            mode,
            layer_name=name,
            policy=normalization_policy,
            valid_mask=mask,
            cfg=cfg,
        )
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
        normalization_policy=normalization_policy,
        cfg=cfg,
        ranking_inputs={
            name: layers[name]
            for name in ("slope", "illumination")
            if name in layers
        },
        terrain_gate=gate,
    )


def apply_keepout(
    result: ScoreResult,
    points: list[tuple[float, float]],
    radius_km: float | None,
    mode: str,
    penalty: float | None = None,
) -> ScoreResult:
    """Mask or penalise pixels near user-supplied landing points.

    ``radius_km`` is required. There is no default plume radius.
    ``mode`` is ``hard`` (drop the disk from the feasible set) or ``penalty``
    (subtract a caller-supplied penalty from the weighted score). Orbital
    debris is not part of this layer.
    """
    if radius_km is None:
        raise ValueError(
            "keep-out radius_km is required and must be positive; there is no default"
        )
    radius = float(radius_km)
    if not np.isfinite(radius) or radius <= 0.0:
        raise ValueError(
            "keep-out radius_km is required and must be positive; there is no default"
        )
    if not points:
        raise ValueError("keep-out requires at least one landing point")
    if mode not in ("hard", "penalty"):
        raise ValueError("keep-out mode must be 'hard' or 'penalty'")
    if mode == "penalty" and (penalty is None or not np.isfinite(float(penalty))):
        raise ValueError("penalty mode requires a finite penalty; there is no default")

    x, y = result.grid.pixel_centers()
    radius_m = radius * 1000.0
    disk = np.zeros(x.shape, dtype=bool)
    for lon, lat in points:
        px, py = result.grid.fwd.transform(float(lon), float(lat))
        disk |= (x - float(px)) ** 2 + (y - float(py)) ** 2 <= radius_m * radius_m

    combined = result.combined.astype(np.float64, copy=True)
    mask = np.array(result.hard_mask, copy=True)
    if mode == "hard":
        combined[disk] = np.nan
        mask[disk] = False
    else:
        finite = disk & np.isfinite(combined)
        combined[finite] = combined[finite] - float(penalty)
    return ScoreResult(
        combined=combined.astype(np.float32),
        normalized=result.normalized,
        hard_mask=mask,
        weights=result.weights,
        grid=result.grid,
        normalization_policy=result.normalization_policy,
        cfg=result.cfg,
        ranking_inputs=result.ranking_inputs,
        terrain_gate=result.terrain_gate,
    )


def _pixel_center_lonlat(grid: Grid, row: int, col: int) -> tuple[float, float]:
    return grid.to_lonlat(row + 0.5, col + 0.5)


def rank_sites(
    result: ScoreResult,
    n: int | None = None,
    min_separation_km: float | None = None,
) -> list[dict]:
    """Greedy NMS with explicit, reproducible scientific tie-breakers.

    Equal combined scores prefer lower raw slope, then higher raw
    illumination. Exact residual ties use row/column order only as a stable
    final fallback.
    """
    cfg = result.cfg
    if n is None:
        n = int(cfg["scoring"]["n_sites"])
    if min_separation_km is None:
        min_separation_km = float(cfg["scoring"]["min_separation_km"])
    sep_m = float(min_separation_km) * 1000.0
    sep_pixels = sep_m / result.grid.resolution_m
    radius = int(np.ceil(sep_pixels))
    remaining = result.combined.astype(np.float64).copy()
    sites: list[dict] = []
    for _ in range(int(n)):
        if not np.isfinite(remaining).any():
            break
        best_score = float(np.nanmax(remaining))
        candidates = np.isfinite(remaining) & (remaining == best_score)

        slope = result.ranking_inputs.get("slope")
        if slope is not None:
            candidate_slopes = np.where(candidates, slope, np.nan)
            if np.isfinite(candidate_slopes).any():
                candidates &= slope == float(np.nanmin(candidate_slopes))

        illumination = result.ranking_inputs.get("illumination")
        if illumination is not None:
            candidate_illumination = np.where(candidates, illumination, np.nan)
            if np.isfinite(candidate_illumination).any():
                candidates &= illumination == float(np.nanmax(candidate_illumination))

        flat = int(np.flatnonzero(candidates)[0])
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
        row_min = max(0, row_i - radius)
        row_max = min(remaining.shape[0], row_i + radius + 1)
        col_min = max(0, col_i - radius)
        col_max = min(remaining.shape[1], col_i + radius + 1)
        rows = np.arange(row_min, row_max, dtype=np.float64) - row_i
        cols = np.arange(col_min, col_max, dtype=np.float64) - col_i
        dc, dr = np.meshgrid(cols, rows)
        local = remaining[row_min:row_max, col_min:col_max]
        local[(dr * dr + dc * dc) <= sep_pixels * sep_pixels] = np.nan
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
    from matplotlib.path import Path as MplPath

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


def _finite_inside(result: ScoreResult, rings_xy: list[np.ndarray]) -> np.ndarray:
    from matplotlib.path import Path as MplPath

    if not rings_xy:
        return np.asarray([], dtype=np.float64)
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
    return values[np.isfinite(values)].astype(np.float64)


def _region_mean_score(result: ScoreResult, rings_xy: list[np.ndarray]) -> float:
    values = _finite_inside(result, rings_xy)
    if values.size == 0:
        return float("nan")
    return float(np.mean(values))


def _region_best_score(result: ScoreResult, rings_xy: list[np.ndarray]) -> float:
    values = _finite_inside(result, rings_xy)
    if values.size == 0:
        return float("nan")
    return float(np.max(values))


def _sample_score(result: ScoreResult, lon: float, lat: float) -> float:
    row_f, col_f = result.grid.to_pixel(lon, lat)
    row, col = int(np.floor(row_f)), int(np.floor(col_f))
    height, width = result.combined.shape
    if row < 0 or col < 0 or row >= height or col >= width:
        return float("nan")
    value = float(result.combined[row, col])
    return value if np.isfinite(value) else float("nan")


def _prepare_region(grid: Grid, feature: dict) -> dict:
    props = feature.get("properties") or {}
    geometry = feature.get("geometry")
    prepared = {
        "name": props.get("name"),
        "kind": "none",
        "rings": [],
        "xy": None,
        "lon": None,
        "lat": None,
        "coordinate_status": props.get("coordinate_status"),
        "lists": props.get("lists"),
    }
    if not geometry or not geometry.get("coordinates"):
        return prepared
    if geometry.get("type") == "Point":
        lon, lat = float(geometry["coordinates"][0]), float(geometry["coordinates"][1])
        x, y = grid.fwd.transform(lon, lat)
        prepared.update(
            {"kind": "point", "xy": (float(x), float(y)), "lon": lon, "lat": lat}
        )
        return prepared
    rings = [_ring_xy(grid, ring) for ring in _rings_lonlat(geometry)]
    if rings:
        prepared["kind"] = "polygon"
        prepared["rings"] = rings
    return prepared


def _region_distance_km(grid: Grid, lon: float, lat: float, region: dict) -> float | None:
    if region["kind"] == "point":
        px, py = grid.fwd.transform(float(lon), float(lat))
        x, y = region["xy"]
        return float(np.hypot(float(px) - x, float(py) - y) / 1000.0)
    if region["kind"] == "polygon":
        return _distance_km(grid, lon, lat, region["rings"])
    return None


def artemis_overlap(
    sites: list[dict],
    regions_geojson,
    result: ScoreResult | None = None,
) -> dict:
    """Nearest region and distance for each site; best score and rank per region.

    Null geometries are skipped. Point distance is in the stereographic plane.
    Polygon distance is 0 inside the ring. ``best_score`` is the maximum finite
    raster value inside a polygon, or the value at a point. Rank follows
    ``best_score``. ``mean_score`` is retained for polygon checks.
    """
    cfg = result.cfg if result is not None else load_config()
    grid = result.grid if result is not None else Grid(cfg)
    collection = _load_geojson(regions_geojson)
    prepared = [_prepare_region(grid, feat) for feat in (collection.get("features") or [])]

    site_rows = []
    assigned_totals: dict[str, list[float]] = {p["name"]: [] for p in prepared}
    for site in sites:
        nearest = None
        nearest_d = None
        for region in prepared:
            dist = _region_distance_km(grid, site["lon"], site["lat"], region)
            if dist is None:
                continue
            if nearest_d is None or dist < nearest_d or (
                dist == nearest_d and str(region["name"]) < str(nearest)
            ):
                nearest = region["name"]
                nearest_d = dist
        row = dict(site)
        row["nearest"] = nearest
        row["distance_km"] = nearest_d
        site_rows.append(row)
        total = site.get("total")
        if nearest is not None and total is not None and np.isfinite(float(total)):
            assigned_totals[nearest].append(float(total))

    region_rows = []
    for region in prepared:
        assigned = assigned_totals[region["name"]]
        if result is not None and region["kind"] == "polygon" and region["rings"]:
            mean_score = _region_mean_score(result, region["rings"])
            best_score = _region_best_score(result, region["rings"])
        elif result is not None and region["kind"] == "point":
            best_score = _sample_score(result, region["lon"], region["lat"])
            mean_score = best_score
        elif assigned:
            best_score = float(np.max(assigned))
            mean_score = float(np.mean(assigned))
        else:
            best_score = float("nan")
            mean_score = float("nan")
        region_rows.append(
            {
                "name": region["name"],
                "mean_score": mean_score,
                "best_score": best_score,
                "rank": None,
                "coordinate_status": region["coordinate_status"],
                "lists": region["lists"],
            }
        )

    order = sorted(
        range(len(region_rows)),
        key=lambda i: (
            not np.isfinite(region_rows[i]["best_score"]),
            -(region_rows[i]["best_score"])
            if np.isfinite(region_rows[i]["best_score"])
            else 0.0,
            region_rows[i]["name"] or "",
        ),
    )
    rank = 1
    for i in order:
        if np.isfinite(region_rows[i]["best_score"]):
            region_rows[i]["rank"] = rank
            rank += 1
        else:
            region_rows[i]["rank"] = None

    return {"sites": site_rows, "regions": region_rows}
