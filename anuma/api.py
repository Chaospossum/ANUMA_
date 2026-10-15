"""FastAPI surface for scoring and layer display.

If `data/grid/*.npy` is missing, endpoints that need rasters return HTTP 412
with the ingest command. They do not invent or score `--synthetic` layers.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from anuma.artemis import distinction, feature_collection
from anuma.config import load_config, repo_root
from anuma.daily_lit import INDEX_NAME, DailyLitCube
from anuma.export import sites_csv, sites_geojson
from anuma.grid import Grid
from anuma.heatmap import displayed, render_png
from anuma.score import (
    apply_keepout,
    artemis_overlap,
    hard_mask,
    is_display_only,
    is_gate_layer,
    normalize,
    rank_sites,
    score,
    scored_weights,
)

INGEST_COMMAND = "python3 -m anuma.ingest"
REQUIRED_FOR_SCORE = ("slope", "illumination", "hydrogen", "earth_visibility")
DISPLAY_LAYERS = ("dem", "slope", "illumination", "hydrogen", "earth_visibility", "dem_error")
DEM_ERROR_COMMAND = (
    "python -m anuma.dem_error --src data/raw/LDEM_80S_20MPP_ADJ_ERR.TIF "
    "--out data/grid/dem_error.npy"
)

TERRAIN_LAYER = "terrain_1km_fraction"
STAGE_F_COMMAND = (
    "copy terrain_1km_fraction.npy and terrain_1km_fraction.json from the Stage F "
    "production output (docs/stage-f-run.md) into data/grid/"
)
# What the precomputed studies (Monte Carlo top-10 %, Stage B and Stage D
# badges) ranked: the slope-only hard mask, before the 20 m terrain gate.
STUDIES_RANKING_BASIS = "slope_only"

_STORE: dict[str, Any] = {}
_CUBES: dict[str, DailyLitCube] = {}
_MONTH = re.compile(r"\A([0-9]{4})-([0-9]{2})\Z")
DAILY_LIT_MISSING = "daily sunlight data not available on this machine"
logger = logging.getLogger(__name__)


class HardBody(BaseModel):
    max_slope_deg: float | None = None
    slope_radius_m: float | None = Field(default=None, ge=0.0)
    # Stage F 20 m terrain gate: None = config default, False = off, True = on
    # (400 if it cannot apply at the requested max slope).
    terrain_1km: bool | None = None
    terrain_1km_min_fraction: float | None = Field(default=None, ge=0.0, le=1.0)


class KeepOutPoint(BaseModel):
    lon: float
    lat: float


class KeepOutBody(BaseModel):
    """User landing points. Radius is required; there is no default."""

    points: list[KeepOutPoint] = Field(default_factory=list)
    radius_km: float | None = None
    mode: str = "hard"
    penalty: float | None = None


def _parse_month(value: str) -> date:
    match = _MONTH.fullmatch(value)
    if match is None or not 1 <= int(match.group(2)) <= 12:
        raise ValueError(f"month {value!r} must be YYYY-MM with MM in 01..12")
    return date(int(match.group(1)), int(match.group(2)), 1)


def _next_month(day: date) -> date:
    return date(day.year + day.month // 12, day.month % 12 + 1, 1)


class MonthWindow(BaseModel):
    """Inclusive month range for the Stage E illumination scenario."""

    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _month_format(cls, value: str) -> str:
        _parse_month(value)
        return value

    @model_validator(mode="after")
    def _ordered(self) -> "MonthWindow":
        if _parse_month(self.start) > _parse_month(self.end):
            raise ValueError(f"start month {self.start} is after end month {self.end}")
        return self

    def days(self) -> tuple[date, date]:
        """First day of start, and first day after the inclusive end month."""
        return _parse_month(self.start), _next_month(_parse_month(self.end))


class ScoreBody(BaseModel):
    weights: dict[str, Annotated[float, Field(ge=0.0)]] | None = None
    hard: HardBody | None = None
    n: int | None = Field(default=None, ge=1, le=50)
    keepout: KeepOutBody | None = None
    illumination_months: MonthWindow | None = None


def data_root() -> Path:
    override = os.environ.get("ANUMA_ROOT")
    return Path(override) if override else repo_root()


def clear_cache() -> None:
    _STORE.clear()
    _CUBES.clear()


def _cfg() -> dict:
    return load_config()


def _paths(cfg: dict, root: Path) -> dict[str, Path]:
    p = cfg["paths"]
    return {
        "grid": root / p["grid"],
        "grid_json": root / p["grid_json"],
        "artemis_geojson": root / p["artemis_geojson"],
        "daily_lit": root / p["daily_lit"],
    }


def _missing_layers(cfg: dict, root: Path) -> list[str]:
    gdir = _paths(cfg, root)["grid"]
    missing = []
    if not (gdir / "grid.json").is_file():
        missing.append("grid.json")
    for name in REQUIRED_FOR_SCORE:
        if not (gdir / f"{name}.npy").is_file():
            missing.append(name)
    return missing


def _not_ingested(missing: list[str]) -> HTTPException:
    return HTTPException(
        status_code=412,
        detail={
            "error": "not_ingested",
            "missing": missing,
            "ingest_command": INGEST_COMMAND,
            "message": (
                "Grid layers are not ingested. Real LOLA/PDS products must be "
                f"warped onto the common grid first. Run: {INGEST_COMMAND}"
            ),
        },
    )


def _load_store(cfg: dict, root: Path) -> dict[str, Any]:
    key = str(root.resolve())
    cached = _STORE.get(key)
    if cached is not None:
        return cached
    paths = _paths(cfg, root)
    missing = _missing_layers(cfg, root)
    if missing:
        raise _not_ingested(missing)
    grid = Grid.from_json(paths["grid_json"], cfg)
    layers: dict[str, np.ndarray] = {}
    for name in DISPLAY_LAYERS:
        npy = paths["grid"] / f"{name}.npy"
        if npy.is_file():
            layers[name] = np.load(npy)
    store = {"grid": grid, "layers": layers, "root": root}
    _STORE[key] = store
    return store


def _terrain_layer(cfg: dict, root: Path, store: dict) -> dict:
    """The Stage F fraction layer and its sidecar, validated once per store.

    Returns ``{"array": ndarray | None, "available": bool, "reason": str |
    None, "meta": dict | None}``. A layer without a valid full-cap sidecar is
    unavailable, never applied.
    """
    cached = store.get("terrain")
    if cached is not None and cached["available"]:
        return cached
    gdir = _paths(cfg, root)["grid"]
    npy = gdir / f"{TERRAIN_LAYER}.npy"
    side = gdir / f"{TERRAIN_LAYER}.json"
    info: dict = {"array": None, "available": False, "reason": None, "meta": None}
    if not npy.is_file():
        info["reason"] = f"{TERRAIN_LAYER}.npy is not on this machine"
    elif not side.is_file():
        info["reason"] = f"{TERRAIN_LAYER}.json (Stage F sidecar) is missing"
    else:
        from anuma.stage_f import validate_sidecar

        try:
            meta = json.loads(side.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            meta, problem = None, f"sidecar unreadable: {exc}"
        else:
            problem = validate_sidecar(meta, store["grid"], cfg)
        array = None
        if problem is None:
            array = np.load(npy, allow_pickle=False)
            if array.shape != (store["grid"].height, store["grid"].width):
                problem = f"layer shape {array.shape} does not match the grid"
        if problem is None:
            info.update(array=array, available=True, meta=meta)
        else:
            info["reason"] = problem
            info["meta"] = meta
    store["terrain"] = info
    return info


def _terrain_summary(cfg: dict, terrain: dict) -> dict:
    spec = cfg["hard"]["terrain_1km"]
    meta = terrain.get("meta") or {}
    return {
        "available": bool(terrain["available"]),
        "reason": terrain.get("reason"),
        "enabled_by_default": bool(spec.get("enabled", False)),
        "min_fraction": float(spec["min_fraction"]),
        "radius_m": float(spec["radius_m"]),
        "layer": TERRAIN_LAYER,
        "layer_max_slope_deg": _finite_or_none(meta.get("max_slope_deg")),
        "job": meta.get("job"),
        "build": None if terrain["available"] else STAGE_F_COMMAND,
    }


def _ranking_basis(gate: dict) -> dict:
    basis = "slope_and_terrain_1km" if gate["status"] == "applied" else "slope_only"
    return {
        "basis": basis,
        "studies_basis": STUDIES_RANKING_BASIS,
        "studies_match": basis == STUDIES_RANKING_BASIS,
        "note": (
            None
            if basis == STUDIES_RANKING_BASIS
            else "Top-10 %, Stage B and Stage D badges were computed for the previous "
            "(slope-only) ranking"
        ),
    }


def _previous_month(day: date) -> date:
    return date(day.year - (day.month == 1), (day.month - 2) % 12 + 1, 1)


def _window_bounds(cfg: dict, cube: DailyLitCube | None = None) -> tuple[date, date]:
    """Allowed first and last month. A loaded cube narrows the config range to
    the whole months it holds, so config and cube cannot disagree."""
    spec = cfg["illumination_window"]
    first, last = _parse_month(spec["first_month"]), _parse_month(spec["last_month"])
    if cube is not None:
        cube_first = cube.start if cube.start.day == 1 else _next_month(cube.start)
        # The last month must end on or before the exclusive cube end.
        cube_last = _previous_month(date(cube.end.year, cube.end.month, 1))
        first, last = max(first, cube_first), min(last, cube_last)
    return first, last


def _month_text(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _check_window(window: "MonthWindow", bounds: tuple[date, date]) -> None:
    first, last = bounds
    if _parse_month(window.start) < first or _parse_month(window.end) > last:
        raise HTTPException(
            status_code=400,
            detail=(
                f"illumination months {window.start}..{window.end} are outside "
                f"{_month_text(first)}..{_month_text(last)}"
            ),
        )


def _daily_lit_ready(path: Path) -> bool:
    return (path / INDEX_NAME).is_file()


def _open_cube(cfg: dict, root: Path) -> DailyLitCube | None:
    """Cached cube, or None when this machine has no daily-lit output."""
    path = _paths(cfg, root)["daily_lit"]
    key = str(path.resolve())
    cube = _CUBES.get(key)
    if cube is None:
        if not _daily_lit_ready(path):
            return None
        try:
            cube = DailyLitCube(path)
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("daily-lit cube at %s could not be opened: %s", path, exc)
            return None
        _CUBES[key] = cube
    return cube


def _load_cube(cfg: dict, root: Path, shape: tuple[int, int]) -> DailyLitCube:
    cube = _open_cube(cfg, root)
    if cube is None:
        path = _paths(cfg, root)["daily_lit"]
        raise HTTPException(status_code=400, detail=f"{DAILY_LIT_MISSING} ({path})")
    if tuple(cube.shape) != tuple(shape):
        raise HTTPException(
            status_code=400,
            detail=f"daily sunlight cube shape {tuple(cube.shape)} does not match grid {tuple(shape)}",
        )
    return cube


def _window_illumination(
    cfg: dict, root: Path, store: dict, window: MonthWindow
) -> tuple[np.ndarray, dict]:
    """Mean lit fraction over the month window, NaN out of cap like the PDS layer."""
    _check_window(window, _window_bounds(cfg))
    grid = store["grid"]
    cube = _load_cube(cfg, root, (grid.height, grid.width))
    _check_window(window, _window_bounds(cfg, cube))
    start, end = window.days()
    try:
        hours = cube.lit_hours_mean(start, end)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f"{DAILY_LIT_MISSING}: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"daily sunlight cube: {exc}") from exc
    fraction = (hours / 24.0).astype(np.float32)
    used = {
        "source": "daily_lit",
        "start": window.start,
        "end": window.end,
        "start_day": start.isoformat(),
        "end_day_exclusive": end.isoformat(),
        "note": cfg["illumination_window"]["note"],
    }
    return fraction, used


def _window_meta(cfg: dict, root: Path) -> dict:
    cube = _open_cube(cfg, root)
    first, last = _window_bounds(cfg, cube)
    return {
        "first_month": _month_text(first),
        "last_month": _month_text(last),
        "default_label": cfg["illumination_window"]["default_label"],
        "note": cfg["illumination_window"]["note"],
        "available": cube is not None,
    }


def _default_window(cfg: dict) -> dict:
    return {"source": "published", "label": cfg["illumination_window"]["default_label"]}


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return number


def _site_payload(site: dict) -> dict:
    contrib = {
        name: _finite_or_none(val) for name, val in (site.get("contributions") or {}).items()
    }
    return {
        "lon": _finite_or_none(site.get("lon")),
        "lat": _finite_or_none(site.get("lat")),
        "row": int(site["row"]) if site.get("row") is not None else None,
        "col": int(site["col"]) if site.get("col") is not None else None,
        "total": _finite_or_none(site.get("total")),
        "contributions": contrib,
        "nearest": site.get("nearest"),
        "distance_km": _finite_or_none(site.get("distance_km")),
        "terrain_1km_fraction": _finite_or_none(site.get("terrain_1km_fraction")),
    }


def _artemis_payload(overlap: dict, cfg: dict) -> dict:
    regions = []
    for row in overlap.get("regions") or []:
        regions.append(
            {
                "name": row.get("name"),
                "mean_score": _finite_or_none(row.get("mean_score")),
                "best_score": _finite_or_none(row.get("best_score")),
                "rank": row.get("rank"),
                "coordinate_status": row.get("coordinate_status"),
                "lists": row.get("lists"),
            }
        )
    art = cfg["artemis3"]
    return {
        "regions": regions,
        "approximate": art.get("approximate"),
        "coordinate_status": art.get("coordinate_status"),
        "source": art["lists"]["2024"]["source_url"],
        "point_source": art["point_source"]["citation"],
        "note": distinction(cfg),
    }


def _stability_payload(cfg: dict, root: Path) -> dict:
    """Top-10 membership and winner frequency for the default policy, if on disk."""
    spec = cfg["stability"]
    path = root / spec["monte_carlo_summary"]
    if not path.is_file():
        return {"available": False}
    summary = json.loads(path.read_text())
    policy = spec["policy"]
    study = summary["policies"][policy]
    return {
        "available": True,
        "ranking_basis": STUDIES_RANKING_BASIS,
        "job_id": str(spec["monte_carlo_job"]),
        "policy": policy,
        "draw_count": int(summary["draw_count"]),
        "weight_ranges": summary.get("weight_ranges"),
        "note": spec["note"],
        "winners": study["winner_stability"]["winners"],
        "top10_membership_frequency": study["top10_membership_frequency"],
    }


def _artemis_feature_collection(cfg: dict, root: Path) -> dict:
    """Cited points come from config, not from a stale on-disk copy."""
    del root
    return feature_collection(cfg)


def _with_keepout(result, body: ScoreBody | None):
    if body is None or body.keepout is None:
        return result
    spec = body.keepout
    numbers = [spec.radius_km, spec.penalty] + [v for p in spec.points for v in (p.lon, p.lat)]
    if not all(math.isfinite(v) for v in numbers if v is not None):
        raise HTTPException(status_code=400, detail="keepout values must be finite numbers")
    try:
        return apply_keepout(
            result,
            [(point.lon, point.lat) for point in spec.points],
            spec.radius_km,
            spec.mode,
            spec.penalty,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _run_score(body: ScoreBody | None = None, include_png: bool = True) -> dict:
    cfg = _cfg()
    root = data_root()
    store = _load_store(cfg, root)
    body = body or ScoreBody()
    weights = dict(body.weights) if body.weights else dict(cfg["scoring"]["weights"])
    try:
        weights = scored_weights(weights, cfg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    max_slope = (
        body.hard.max_slope_deg
        if body.hard is not None and body.hard.max_slope_deg is not None
        else float(cfg["hard"]["max_slope_deg"])
    )
    slope_radius = (
        body.hard.slope_radius_m
        if body.hard is not None and body.hard.slope_radius_m is not None
        else float(cfg["hard"]["slope_radius_m"])
    )
    n = body.n if body.n is not None else int(cfg["scoring"]["n_sites"])
    layers = store["layers"]
    window = _default_window(cfg)
    if body.illumination_months is not None:
        layers = dict(layers)
        layers["illumination"], window = _window_illumination(
            cfg, root, store, body.illumination_months
        )
    needed = ["slope", *weights.keys()]
    absent = [name for name in needed if name not in layers]
    if absent:
        raise _not_ingested(absent)
    terrain = _terrain_layer(cfg, root, store)
    hard = {"max_slope_deg": max_slope, "slope_radius_m": slope_radius}
    if body.hard is not None:
        hard["terrain_1km"] = body.hard.terrain_1km
        hard["terrain_1km_min_fraction"] = body.hard.terrain_1km_min_fraction
    if terrain["available"]:
        layers = {**layers, TERRAIN_LAYER: terrain["array"]}
        hard["terrain_1km_max_slope_deg"] = float(terrain["meta"]["max_slope_deg"])
    else:
        hard["terrain_1km_unavailable_reason"] = terrain["reason"]
    try:
        result = score(
            layers,
            weights=weights,
            hard=hard,
            grid=store["grid"],
            cfg=cfg,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = _with_keepout(result, body)
    sites = rank_sites(result, n=n)
    if terrain["available"]:
        for site in sites:
            site["terrain_1km_fraction"] = float(terrain["array"][site["row"], site["col"]])
    geojson = _artemis_feature_collection(cfg, root)
    overlap = artemis_overlap(sites, geojson, result=result)
    grid = store["grid"]
    payload = {
        "bounds": [grid.xmin, grid.ymin, grid.xmax, grid.ymax],
        "sites": [_site_payload(s) for s in overlap["sites"]],
        "artemis": _artemis_payload(overlap, cfg),
        "illumination_window": window,
        "terrain_gate": result.terrain_gate["status"],
        "terrain_gate_detail": result.terrain_gate,
        "ranking_basis": _ranking_basis(result.terrain_gate),
    }
    if include_png:
        png = render_png(result.combined, cmap="viridis")
        payload["png"] = base64.b64encode(png).decode("ascii")
    return payload


def _scenario(payload: dict, body: ScoreBody | None) -> dict:
    """The illumination window and keep-out behind an exported ranking."""
    keepout = body.keepout.model_dump() if body is not None and body.keepout else None
    detail = payload["terrain_gate_detail"]
    return {
        "illumination_window": payload["illumination_window"],
        "keepout": keepout,
        "terrain_gate": {
            key: detail.get(key)
            for key in ("status", "min_fraction", "radius_m", "layer_max_slope_deg", "message")
        },
    }


def _display_range(cfg: dict, name: str, array: np.ndarray) -> tuple[float, float] | None:
    """Colour range in layer units over the pixels the PNG draws (nanmax blocks):
    finite minimum to the configured percentile."""
    fixed = cfg["layers"][name].get("display_range")
    if fixed is not None:
        return float(fixed[0]), float(fixed[1])
    shown = displayed(array)
    finite = shown[np.isfinite(shown)]
    if finite.size == 0:
        return None
    high_pct = float(cfg["layers"][name].get("display_percentile_high", 100.0))
    low = float(finite.min())
    high = float(np.percentile(finite, high_pct))
    return low, high if high > low else low + 1.0


def _display_only_png(cfg: dict, name: str, array: np.ndarray, store: dict) -> Response:
    """Raw values in grey on a fixed range; the range goes out in headers."""
    ranges = store.setdefault("display_ranges", {})
    if name not in ranges:
        ranges[name] = _display_range(cfg, name, array)
    span = ranges[name]
    if is_gate_layer(cfg, name):
        headers = {"X-Anuma-Gate-Layer": "true", "X-Anuma-Range-Basis": "fixed"}
    else:
        headers = {"X-Anuma-Display-Only": "true", "X-Anuma-Range-Basis": "displayed-block-max"}
    if span is None:
        png = render_png(array, cmap="gray")
    else:
        png = render_png(array, cmap="gray", vmin=span[0], vmax=span[1])
        headers["X-Anuma-Range-Min"] = f"{span[0]:.6g}"
        headers["X-Anuma-Range-Max"] = f"{span[1]:.6g}"
        pct = cfg["layers"][name].get("display_percentile_high")
        if pct is not None:
            headers["X-Anuma-Range-Max-Percentile"] = f"{float(pct):g}"
    unit = cfg["layers"][name].get("unit")
    if unit:
        headers["X-Anuma-Unit"] = str(unit)
    return Response(content=png, media_type="image/png", headers=headers)


def create_app() -> FastAPI:
    app = FastAPI(
        title="ANUMA",
        description="Lunar south-pole landing-site scoring for MAGPIE.",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/layers")
    def get_layers() -> dict:
        cfg = _cfg()
        root = data_root()
        grid = Grid(cfg)
        missing = _missing_layers(cfg, root)
        layers = []
        gdir = _paths(cfg, root)["grid"]
        if missing:
            terrain = {"available": False, "reason": "grid layers are not ingested", "meta": None}
        else:
            terrain = _terrain_layer(cfg, root, _load_store(cfg, root))
        for name, meta in cfg["layers"].items():
            npy = gdir / f"{name}.npy"
            entry = {
                "name": meta.get("name", name),
                "unit": meta.get("unit"),
                "mode": meta.get("mode"),
                "citation": meta.get("citation"),
                "present": npy.is_file(),
            }
            if is_display_only(cfg, name):
                entry.update(
                    label=meta.get("label"),
                    display_only=True,
                    scored=False,
                    available=npy.is_file(),
                    command=DEM_ERROR_COMMAND if name == "dem_error" else None,
                )
            if is_gate_layer(cfg, name):
                available = name == TERRAIN_LAYER and terrain["available"]
                entry.update(
                    label=meta.get("label"),
                    display_only=False,
                    gate=True,
                    scored=False,
                    available=available,
                    command=None if available else STAGE_F_COMMAND,
                )
            layers.append(entry)
        return {
            "ingested": not missing,
            "missing": missing,
            "ingest_command": INGEST_COMMAND,
            "proj4": grid.proj4,
            "bounds": [grid.xmin, grid.ymin, grid.xmax, grid.ymax],
            "resolution_m": grid.resolution_m,
            "width": grid.width,
            "height": grid.height,
            "defaults": {
                "weights": dict(cfg["scoring"]["weights"]),
                "max_slope_deg": float(cfg["hard"]["max_slope_deg"]),
                "slope_radius_m": float(cfg["hard"]["slope_radius_m"]),
                "n": int(cfg["scoring"]["n_sites"]),
            },
            "illumination_window": _window_meta(cfg, root),
            "terrain_gate": _terrain_summary(cfg, terrain),
            "studies_ranking_basis": STUDIES_RANKING_BASIS,
            "layers": layers,
        }

    @app.post("/score")
    def post_score(body: ScoreBody | None = None) -> dict:
        return _run_score(body)

    @app.post("/score.csv")
    def post_score_csv(body: ScoreBody | None = None) -> Response:
        payload = _run_score(body, include_png=False)
        return Response(
            content=sites_csv(payload["sites"], _scenario(payload, body)),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="anuma-ranked-sites.csv"'},
        )

    @app.post("/score.geojson")
    def post_score_geojson(body: ScoreBody | None = None) -> Response:
        payload = _run_score(body, include_png=False)
        return Response(
            content=sites_geojson(payload["sites"], _scenario(payload, body)),
            media_type="application/geo+json",
            headers={
                "Content-Disposition": 'attachment; filename="anuma-ranked-sites.geojson"'
            },
        )

    @app.get("/layer/{name}.png")
    def get_layer_png(
        name: str,
        start: Annotated[str | None, Query()] = None,
        end: Annotated[str | None, Query()] = None,
    ) -> Response:
        window = None
        if start is not None or end is not None:
            if name != "illumination":
                raise HTTPException(
                    status_code=400, detail="start/end months apply only to illumination"
                )
            if start is None or end is None:
                raise HTTPException(status_code=400, detail="give both start and end months")
            try:
                window = MonthWindow(start=start, end=end)
            except ValidationError as exc:
                detail = "; ".join(item["msg"] for item in exc.errors())
                raise HTTPException(status_code=400, detail=detail) from exc
        if name == "score":
            # Combined score at the config defaults, in the same viridis as /score.
            png = base64.b64decode(_run_score()["png"])
            return Response(content=png, media_type="image/png")
        cfg = _cfg()
        root = data_root()
        if name not in cfg["layers"]:
            raise HTTPException(status_code=404, detail=f"Unknown layer {name!r}")
        store = _load_store(cfg, root)
        if is_gate_layer(cfg, name):
            terrain = _terrain_layer(cfg, root, store)
            if name != TERRAIN_LAYER or not terrain["available"]:
                raise HTTPException(
                    status_code=404,
                    detail=f"{name} is not available: {terrain['reason']}. {STAGE_F_COMMAND}",
                )
            return _display_only_png(cfg, name, terrain["array"], store)
        array = store["layers"].get(name)
        npy = _paths(cfg, root)["grid"] / f"{name}.npy"
        if array is None and is_display_only(cfg, name) and npy.is_file():
            # Optional layer built after the store was cached.
            array = store["layers"][name] = np.load(npy)
        if array is None and is_display_only(cfg, name):
            raise HTTPException(
                status_code=404,
                detail=(
                    f"{name}.npy is not on this machine; it is display only. "
                    f"Build it with: {DEM_ERROR_COMMAND}"
                ),
            )
        if array is None:
            raise _not_ingested([name])
        if is_display_only(cfg, name):
            return _display_only_png(cfg, name, array, store)
        if window is not None:
            array, _ = _window_illumination(cfg, root, store, window)
        mode = cfg["layers"][name].get("mode")
        if mode:
            painted = normalize(
                array,
                mode,
                layer_name=name,
                policy=cfg["scoring"]["default_normalization_policy"],
                valid_mask=hard_mask(
                    store["layers"]["slope"],
                    cfg=cfg,
                    resolution_m=store["grid"].resolution_m,
                ),
                cfg=cfg,
            )
        else:
            painted = array
        png = render_png(painted, cmap="gray")
        return Response(content=png, media_type="image/png")

    @app.get("/stability.json")
    def get_stability() -> dict:
        return _stability_payload(_cfg(), data_root())

    @app.get("/checks/stage-b-1km.json")
    def get_stage_b_checks() -> JSONResponse:
        path = repo_root() / _cfg()["stability"]["stage_b_checks"]
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Stage B check file is missing")
        return JSONResponse(json.loads(path.read_text()))

    @app.get("/checks/stage-d-noise.json")
    def get_stage_d_checks() -> JSONResponse:
        path = repo_root() / _cfg()["stability"]["stage_d_checks"]
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Stage D check file is missing")
        return JSONResponse(json.loads(path.read_text()))

    @app.get("/artemis3.geojson")
    def get_artemis_geojson() -> JSONResponse:
        cfg = _cfg()
        collection = _artemis_feature_collection(cfg, data_root())
        return JSONResponse(collection)

    static_dir = repo_root() / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    return app


app = create_app()
