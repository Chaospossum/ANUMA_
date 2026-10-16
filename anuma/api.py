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
from datetime import date, datetime, timedelta, timezone
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
from anuma.darkness import INDEX_NAME as DARKNESS_INDEX
from anuma.darkness import DarknessCube
from anuma.export import sites_csv, sites_geojson
from anuma.grid import Grid
from anuma.heatmap import displayed, render_category_png, render_png, render_ramp_png
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
    terrain_pass,
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
# What the original precomputed studies (Monte Carlo top-10 %, Stage B and
# Stage D badges) ranked: the slope-only hard mask, before the 20 m terrain
# gate. Stage H repeats the Monte Carlo and DEM-noise studies for the gated
# ranking (config stability.gated); those are served for GATED_BASIS when
# present and made for the loaded gate layer.
STUDIES_RANKING_BASIS = "slope_only"
GATED_BASIS = "slope_and_terrain_1km"

_STORE: dict[str, Any] = {}
_CUBES: dict[str, DailyLitCube] = {}
_DARKNESS: dict[str, DarknessCube] = {}
_MONTH = re.compile(r"\A([0-9]{4})-([0-9]{2})\Z")
DAILY_LIT_MISSING = "daily sunlight data not available on this machine"
DARKNESS_MISSING = "darkness (Stage G) data not available on this machine"
# Stage G display layers: not in cfg["layers"], never scored.
DARKNESS_LAYERS = {
    "longest_dark": "Longest continuous darkness (days), {period} modelled",
    "longest_lit": "Longest continuous sunlight (days), {period} modelled",
}
# Grey ramps (low end, high end) and the never-lit colour, as RGB.
DARK_RAMP = ((242, 242, 242), (77, 77, 77))
LIT_RAMP = ((77, 77, 77), (242, 242, 242))
NEVER_LIT_RGB = (0, 0, 0)
logger = logging.getLogger(__name__)


class HardBody(BaseModel):
    max_slope_deg: float | None = None
    slope_radius_m: float | None = Field(default=None, ge=0.0)
    # Stage F 20 m terrain gate: None = config default (degrades to
    # "unavailable" or "slope_mismatch" with a message), False = off,
    # True = on, or 400 if the layer is unavailable or the max slope differs.
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
    # Candidates closer than this are one site (greedy NMS in rank_sites).
    min_separation_km: float | None = Field(default=None, ge=0.5, le=100.0)
    keepout: KeepOutBody | None = None
    illumination_months: MonthWindow | None = None


def data_root() -> Path:
    override = os.environ.get("ANUMA_ROOT")
    return Path(override) if override else repo_root()


def clear_cache() -> None:
    _STORE.clear()
    _CUBES.clear()
    _DARKNESS.clear()


def _cfg() -> dict:
    return load_config()


def _paths(cfg: dict, root: Path) -> dict[str, Path]:
    p = cfg["paths"]
    return {
        "grid": root / p["grid"],
        "grid_json": root / p["grid_json"],
        "artemis_geojson": root / p["artemis_geojson"],
        "daily_lit": root / p["daily_lit"],
        "darkness": root / p["darkness"] if p.get("darkness") else None,
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
            problem = validate_sidecar(meta, store["grid"], cfg, npy_path=npy)
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


def _terrain_if_ingested(cfg: dict, root: Path) -> dict:
    """The gate layer when the grid is ingested; never raises 412."""
    if _missing_layers(cfg, root):
        return {"available": False, "reason": "grid layers are not ingested", "meta": None}
    return _terrain_layer(cfg, root, _load_store(cfg, root))


def _gated_basis(cfg: dict) -> str:
    return str((cfg["stability"].get("gated") or {}).get("ranking_basis", GATED_BASIS))


def _gated_studies(cfg: dict, root: Path, terrain: dict) -> dict:
    """Stage H studies for the gated ranking, if on disk and made for this layer.

    Both files are required: the Monte Carlo summary (data root) and the
    committed DEM-noise check file (repo). Each must name the configured
    study ranking basis and the loaded layer's ``fraction_sha256``, and both
    must agree on the gate's ``min_fraction``.
    """
    spec = cfg["stability"].get("gated") or {}
    info: dict[str, Any] = {
        "available": False,
        "reason": None,
        "summary": None,
        "noise": None,
        "min_fraction": None,
    }
    if not spec:
        info["reason"] = "no gated studies configured"
        return info
    if not terrain["available"]:
        info["reason"] = "20 m terrain layer is not loaded"
        return info
    digest = (terrain.get("meta") or {}).get("fraction_sha256")
    mc_path = root / spec["monte_carlo_summary"]
    noise_path = repo_root() / spec["dem_noise_checks"]
    if not mc_path.is_file():
        info["reason"] = "Stage H Monte Carlo summary is not on this machine"
        return info
    if not noise_path.is_file():
        info["reason"] = "Stage H DEM-noise check file is missing"
        return info
    try:
        summary = json.loads(mc_path.read_text())
        noise = json.loads(noise_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        info["reason"] = f"Stage H files unreadable: {exc}"
        return info
    mc_gate = summary.get("terrain_gate") or {}
    noise_gate = noise.get("terrain_gate") or {}
    wanted = spec["study_ranking_basis"]
    if summary.get("ranking_basis") != wanted or noise.get("ranking_basis") != wanted:
        info["reason"] = f"Stage H files are not for ranking basis {wanted}"
    elif mc_gate.get("fraction_sha256") != digest or noise_gate.get("fraction_sha256") != digest:
        info["reason"] = "Stage H files were made for a different terrain layer"
    elif mc_gate.get("min_fraction") is None or not math.isclose(
        float(mc_gate["min_fraction"]), float(noise_gate.get("min_fraction", math.nan))
    ):
        info["reason"] = "Stage H files disagree on the gate threshold"
    else:
        info.update(
            available=True,
            summary=summary,
            noise=noise,
            min_fraction=float(mc_gate["min_fraction"]),
        )
    return info


def _studies_context(cfg: dict, root: Path, terrain: dict | None = None) -> dict:
    """Which studies the app serves by default, and whether gated ones exist.

    The gated studies are the default when the default ``/score`` applies the
    gate (config enabled, layer loaded, built for the default max slope) and
    the Stage H files are valid for that layer; otherwise the slope-only
    studies are, as before.
    """
    if terrain is None:
        terrain = _terrain_if_ingested(cfg, root)
    gated = _gated_studies(cfg, root, terrain)
    spec = cfg["hard"]["terrain_1km"]
    layer_slope = (terrain.get("meta") or {}).get("max_slope_deg")
    default_applies = bool(
        spec.get("enabled", False)
        and terrain["available"]
        and layer_slope is not None
        and math.isclose(float(layer_slope), float(cfg["hard"]["max_slope_deg"]))
    )
    served = _gated_basis(cfg) if default_applies and gated["available"] else STUDIES_RANKING_BASIS
    return {"served_basis": served, "gated": gated}


def _ranking_basis(gate: dict, studies: dict | None = None, cfg: dict | None = None) -> dict:
    """The ranking's basis versus the basis of the served studies.

    ``studies_match`` is true only when the served Top-10 % and DEM-noise
    studies were computed for this ranking: same basis and, with the gate,
    the same ``min_fraction``.
    """
    gated_name = _gated_basis(cfg) if cfg is not None else GATED_BASIS
    basis = gated_name if gate["status"] == "applied" else STUDIES_RANKING_BASIS
    studies = studies or {"served_basis": STUDIES_RANKING_BASIS, "gated": {"available": False}}
    served = studies["served_basis"]
    gated = studies["gated"]
    match = basis == served
    if match and basis == gated_name:
        match = math.isclose(float(gate["min_fraction"]), float(gated["min_fraction"]))
    if match:
        note = None
    elif basis == gated_name and served == gated_name:
        note = (
            f"Top-10 % and DEM-noise badges were computed with the 20 m terrain rule at "
            f"{float(gated['min_fraction']):.0%}, not {float(gate['min_fraction']):.0%}"
        )
    elif basis == gated_name:
        note = (
            "Top-10 %, Stage B and Stage D badges were computed for the previous "
            "(slope-only) ranking"
        )
    else:
        note = (
            "Top-10 % and DEM-noise badges were computed for the ranking with the "
            "20 m terrain rule"
        )
    return {
        "basis": basis,
        "studies_basis": served,
        "studies_match": match,
        "studies_available": {
            STUDIES_RANKING_BASIS: True,
            gated_name: bool(gated.get("available")),
        },
        "note": note,
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


# ---- Stage G darkness (display and per-site facts; never scored)


def _darkness_spec(cfg: dict) -> dict:
    return cfg.get("darkness") or {}


def _open_darkness(cfg: dict, root: Path, shape: tuple[int, int] | None = None) -> dict:
    """``{"cube": DarknessCube | None, "available": bool, "reason": str | None}``.

    Missing, unreadable or wrongly shaped output is unavailable, never an error.
    """
    path = _paths(cfg, root)["darkness"]
    if path is None:
        return {"cube": None, "available": False, "reason": "paths.darkness is not configured"}
    key = str(path.resolve())
    cube = _DARKNESS.get(key)
    if cube is None:
        if not (path / DARKNESS_INDEX).is_file():
            return {"cube": None, "available": False, "reason": f"{DARKNESS_MISSING} ({path})"}
        try:
            cube = DarknessCube(path)
            for name in ("dark_hours", "dark_start", "dark_flags", "lit_hours", "lit_flags"):
                cube.period(name, (slice(0, 1), slice(0, 1)))
        except (OSError, KeyError, ValueError) as exc:
            logger.warning("darkness output at %s could not be opened: %s", path, exc)
            return {"cube": None, "available": False, "reason": f"{DARKNESS_MISSING}: {exc}"}
        _DARKNESS[key] = cube
    if shape is not None and tuple(cube.valid.shape) != tuple(shape):
        return {
            "cube": None,
            "available": False,
            "reason": f"darkness shape {tuple(cube.valid.shape)} does not match grid {tuple(shape)}",
        }
    return {"cube": cube, "available": True, "reason": None}


def _monthly_ready(cube: DarknessCube) -> bool:
    return all((cube.root / name).is_file() for name in cube.index["monthly"].values())


def _darkness_meta(cfg: dict, root: Path, info: dict) -> dict:
    spec = _darkness_spec(cfg)
    cube = info["cube"]
    meta = {
        "available": info["available"],
        "reason": info["reason"],
        "job": spec.get("job"),
        "period_label": spec.get("period_label"),
        "note": spec.get("note"),
        "dark_display_max_days": _finite_or_none(spec.get("dark_display_max_days")),
        "lit_display_max_days": _finite_or_none(spec.get("lit_display_max_days")),
        "monthly_available": False,
        "period": None,
    }
    if cube is not None:
        meta["monthly_available"] = _monthly_ready(cube)
        meta["period"] = {
            "start_day_utc": cube.start.isoformat(),
            "end_day_exclusive_utc": cube.end.isoformat(),
            "hours": cube.total_hours,
        }
    return meta


def _hour_utc(cube: DarknessCube, hour: int) -> str:
    start = datetime(cube.start.year, cube.start.month, cube.start.day, tzinfo=timezone.utc)
    return (start + timedelta(hours=int(hour))).strftime("%Y-%m-%dT%H:%MZ")


def _days(hours: int | None) -> float | None:
    return None if hours is None else round(hours / 24.0, 3)


def _cube_months(window: MonthWindow) -> tuple[str, str]:
    """The UI's inclusive month range as the reader's [start, end) months."""
    start, end = window.days()
    return _month_text(start), _month_text(end)


DARKNESS_FIELDS = (
    "longest_dark_hours",
    "longest_dark_days",
    "longest_dark_censored",
    "never_lit",
    "longest_dark_start_utc",
    "longest_dark_end_utc",
    "longest_lit_hours",
    "longest_lit_days",
    "longest_lit_censored",
    "window_longest_dark_hours",
    "window_longest_dark_days",
    "window_longest_dark_censored",
    "window_never_lit",
)


def _site_darkness(cube: DarknessCube | None, row: int, col: int, window: MonthWindow | None) -> dict:
    """Per-site Stage G facts; every field null when the data or pixel is missing.

    ``window_*`` describe the month window (runs clipped to it); they stay
    null without a window or when its month files are not on this machine.
    """
    out: dict[str, Any] = dict.fromkeys(DARKNESS_FIELDS)
    if cube is None or not bool(cube.valid[row, col]):
        return out
    cell = (slice(row, row + 1), slice(col, col + 1))
    dark = int(cube.period("dark_hours", cell)[0, 0])
    lit = int(cube.period("lit_hours", cell)[0, 0])
    out.update(
        longest_dark_hours=dark,
        longest_dark_days=_days(dark),
        longest_dark_censored=bool(cube.period("dark_flags", cell)[0, 0] != 0) and dark > 0,
        never_lit=lit == 0,
        longest_lit_hours=lit,
        longest_lit_days=_days(lit),
        longest_lit_censored=bool(cube.period("lit_flags", cell)[0, 0] != 0) and lit > 0,
    )
    if dark > 0:
        start = int(cube.period("dark_start", cell)[0, 0])
        out["longest_dark_start_utc"] = _hour_utc(cube, start)
        out["longest_dark_end_utc"] = _hour_utc(cube, start + dark)
    if window is not None:
        try:
            summary = cube.window_summary(*_cube_months(window), cell)
        except (FileNotFoundError, ValueError) as exc:
            logger.info("darkness window %s..%s not answered: %s", window.start, window.end, exc)
            return out
        best = int(summary["longest_dark_hours"][0, 0])
        out.update(
            window_longest_dark_hours=best,
            window_longest_dark_days=_days(best),
            window_longest_dark_censored=bool(summary["edge_censored"][0, 0]),
            window_never_lit=best == summary["hours"],
        )
    return out


def _darkness_window_status(cube: DarknessCube | None, window: MonthWindow | None) -> dict | None:
    if window is None:
        return None
    status = {"start": window.start, "end": window.end, "available": False, "reason": None}
    if cube is None:
        status["reason"] = DARKNESS_MISSING
        return status
    try:
        # A one-pixel probe: month files are needed unless the window is whole years.
        cube.window_summary(*_cube_months(window), (slice(0, 1), slice(0, 1)))
    except FileNotFoundError:
        status["reason"] = "monthly darkness files are not on this machine; whole calendar years only"
        return status
    except ValueError as exc:
        status["reason"] = str(exc)
        return status
    status["available"] = True
    return status


def _darkness_png(cfg: dict, root: Path, store: dict, name: str, window: MonthWindow | None) -> Response:
    grid = store["grid"]
    info = _open_darkness(cfg, root, (grid.height, grid.width))
    cube = info["cube"]
    if cube is None:
        raise HTTPException(status_code=404, detail=f"{name} is not available: {info['reason']}")
    spec = _darkness_spec(cfg)
    headers = {"X-Anuma-Display-Only": "true", "X-Anuma-Unit": "days", "X-Anuma-Range-Basis": "fixed"}
    cache = store.setdefault("darkness_png", {})
    key = (name, None if window is None else (window.start, window.end))
    if key in cache:
        png, headers = cache[key]
        return Response(content=png, media_type="image/png", headers=headers)
    if name == "longest_lit":
        if window is not None:
            raise HTTPException(
                status_code=400, detail="start/end months apply only to illumination and longest_dark"
            )
        hours = cube.period("lit_hours").astype(np.float64)
        never = cube.valid & (hours == 0)
        top = float(spec.get("lit_display_max_days", 30.0))
        low_rgb, high_rgb = LIT_RAMP
    else:
        if window is None:
            hours = cube.period("dark_hours").astype(np.float64)
            span = cube.total_hours
        else:
            status = _darkness_window_status(cube, window)
            if not status["available"]:
                raise HTTPException(status_code=400, detail=f"darkness window: {status['reason']}")
            try:
                summary = cube.window_summary(*_cube_months(window))
            except (FileNotFoundError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=f"darkness window: {exc}") from exc
            hours = summary["longest_dark_hours"].astype(np.float64)
            span = summary["hours"]
            headers["X-Anuma-Window"] = f"{window.start}..{window.end}"
        never = cube.valid & (hours == span)
        top = float(spec.get("dark_display_max_days", 45.0))
        low_rgb, high_rgb = DARK_RAMP
    days = np.where(cube.valid, hours / 24.0, np.nan)
    png = render_ramp_png(
        days, never, low_rgb=low_rgb, high_rgb=high_rgb, flag_rgb=NEVER_LIT_RGB, vmin=0.0, vmax=top
    )
    headers.update(
        {
            "X-Anuma-Range-Min": "0",
            "X-Anuma-Range-Max": f"{top:g}",
            "X-Anuma-Never-Lit-Pixels": str(int(never.sum())),
        }
    )
    if len(cache) >= 6:
        cache.pop(next(iter(cache)))
    cache[key] = (png, headers)
    return Response(content=png, media_type="image/png", headers=headers)


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
        **{name: site.get(name) for name in DARKNESS_FIELDS},
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


def _select_basis(cfg: dict, root: Path, basis: str | None) -> tuple[str, dict]:
    """The requested study basis (default: the served one) and the context."""
    studies = _studies_context(cfg, root)
    allowed = (STUDIES_RANKING_BASIS, _gated_basis(cfg))
    if basis is None:
        basis = studies["served_basis"]
    if basis not in allowed:
        raise HTTPException(
            status_code=400, detail=f"basis must be one of {', '.join(allowed)}"
        )
    return basis, studies


def _stability_payload(cfg: dict, root: Path, basis: str | None = None) -> dict:
    """Top-10 membership and winner frequency for the default policy, if on disk.

    ``basis`` picks the study: ``slope_only`` (job 5332991) or the gated
    Stage H study; by default the one matching the default ``/score``.
    """
    basis, studies = _select_basis(cfg, root, basis)
    if basis != STUDIES_RANKING_BASIS:
        return _gated_stability_payload(cfg, studies["gated"], basis)
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


def _gated_stability_payload(cfg: dict, gated: dict, basis: str) -> dict:
    if not gated["available"]:
        return {"available": False, "ranking_basis": basis, "reason": gated["reason"]}
    spec = cfg["stability"]["gated"]
    summary = gated["summary"]
    policy = cfg["stability"]["policy"]
    study = summary["policies"][policy]
    return {
        "available": True,
        "ranking_basis": basis,
        "study_ranking_basis": summary["ranking_basis"],
        "job_id": str(summary.get("source_job")) if summary.get("source_job") else None,
        "policy": policy,
        "draw_count": int(summary["draw_count"]),
        "weight_ranges": summary.get("weight_ranges"),
        "terrain_gate": summary.get("terrain_gate"),
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


ALL_ZERO_WEIGHTS = (
    "All weights are zero. Set at least one of illumination, hydrogen or "
    "Earth visibility above 0."
)


def _normalised_weights(weights: dict[str, float]) -> dict[str, float]:
    """Divide by the sum, so scores stay on 0..1 and 1:1 means the same as 0.5:0.5."""
    total = float(sum(weights.values()))
    if not total > 0.0:
        raise HTTPException(status_code=400, detail=ALL_ZERO_WEIGHTS)
    return {name: float(value) / total for name, value in weights.items()}


def _score_range(combined: np.ndarray) -> list[float] | None:
    """Finite min and max of the score, the colour range of the score PNG."""
    finite = combined[np.isfinite(combined)]
    if finite.size == 0:
        return None
    return [float(finite.min()), float(finite.max())]


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
    weights = _normalised_weights(weights)
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
    separation_km = (
        body.min_separation_km
        if body.min_separation_km is not None
        else float(cfg["scoring"]["min_separation_km"])
    )
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
    sites = rank_sites(result, n=n, min_separation_km=separation_km)
    if terrain["available"]:
        for site in sites:
            site["terrain_1km_fraction"] = float(terrain["array"][site["row"], site["col"]])
    grid = store["grid"]
    darkness = _open_darkness(cfg, root, (grid.height, grid.width))
    dark_window = _darkness_window_status(darkness["cube"], body.illumination_months)
    use_window = body.illumination_months if dark_window and dark_window["available"] else None
    for site in sites:
        site.update(_site_darkness(darkness["cube"], site["row"], site["col"], use_window))
    geojson = _artemis_feature_collection(cfg, root)
    overlap = artemis_overlap(sites, geojson, result=result)
    payload = {
        "bounds": [grid.xmin, grid.ymin, grid.xmax, grid.ymax],
        "sites": [_site_payload(s) for s in overlap["sites"]],
        "artemis": _artemis_payload(overlap, cfg),
        "illumination_window": window,
        "terrain_gate": result.terrain_gate["status"],
        "terrain_gate_detail": result.terrain_gate,
        "ranking_basis": _ranking_basis(
            result.terrain_gate, _studies_context(cfg, root, terrain), cfg
        ),
        "weights_used": weights,
        "hard_used": {"max_slope_deg": float(max_slope), "slope_radius_m": float(slope_radius)},
        "min_separation_km": float(separation_km),
        "score_range": _score_range(result.combined),
        "darkness": {
            "available": darkness["available"],
            "reason": darkness["reason"],
            "job": _darkness_spec(cfg).get("job"),
            "window": dark_window,
        },
    }
    if include_png:
        png = render_png(result.combined, cmap="viridis")
        payload["png"] = base64.b64encode(png).decode("ascii")
    return payload


def _scenario(payload: dict, body: ScoreBody | None) -> dict:
    """The illumination window, keep-out, weights and slope rule behind an exported ranking."""
    keepout = body.keepout.model_dump() if body is not None and body.keepout else None
    detail = payload["terrain_gate_detail"]
    return {
        "illumination_window": payload["illumination_window"],
        "keepout": keepout,
        "terrain_gate": {
            key: detail.get(key)
            for key in ("status", "min_fraction", "radius_m", "layer_max_slope_deg", "message")
        },
        "weights_used": payload["weights_used"],
        "max_slope_deg": payload["hard_used"]["max_slope_deg"],
        "slope_radius_m": payload["hard_used"]["slope_radius_m"],
        "min_separation_km": payload["min_separation_km"],
    }


SITE_LAYERS = ("illumination", "slope", "earth_visibility", "hydrogen", "dem", "dem_error")


def _site_values(row: int, col: int, window: MonthWindow | None) -> dict:
    """Raw layer values in layer units at one grid cell, for the site card."""
    cfg = _cfg()
    root = data_root()
    store = _load_store(cfg, root)
    grid = store["grid"]
    if not (0 <= row < grid.height and 0 <= col < grid.width):
        raise HTTPException(
            status_code=404,
            detail=f"cell {row}:{col} is outside the {grid.height} x {grid.width} grid",
        )
    lon, lat = grid.to_lonlat(row + 0.5, col + 0.5)
    values: dict[str, float | None] = {}
    units: dict[str, str | None] = {}
    for name in SITE_LAYERS:
        array = store["layers"].get(name)
        values[name] = None if array is None else _finite_or_none(array[row, col])
        units[name] = cfg["layers"].get(name, {}).get("unit")
    illumination = _default_window(cfg)
    if window is not None:
        _check_window(window, _window_bounds(cfg))
        cube = _load_cube(cfg, root, (grid.height, grid.width))
        _check_window(window, _window_bounds(cfg, cube))
        start, end = window.days()
        try:
            hours = cube.lit_hours_mean(start, end, (slice(row, row + 1), slice(col, col + 1)))
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"daily sunlight cube: {exc}") from exc
        values["illumination"] = _finite_or_none(float(hours[0, 0]) / 24.0)
        illumination = {"source": "daily_lit", "start": window.start, "end": window.end}
    dark = _open_darkness(cfg, root, (grid.height, grid.width))
    dark_window = _darkness_window_status(dark["cube"], window)
    use_window = window if dark_window and dark_window["available"] else None
    return {
        "row": row,
        "col": col,
        "lon": lon,
        "lat": lat,
        "values": values,
        "units": units,
        "illumination_window": illumination,
        "darkness": _site_darkness(dark["cube"], row, col, use_window),
        "checks": _site_checks(cfg, root, store, row, col),
        "darkness_status": {
            "available": dark["available"],
            "reason": dark["reason"],
            "window": dark_window,
        },
    }


EXCLUDED_LAYER = "excluded"
EXCLUDED_SLOPE = 1
EXCLUDED_TERRAIN = 2
EXCLUDED_COLOURS = {
    EXCLUDED_SLOPE: (215, 48, 39),  # too steep at 240 m
    EXCLUDED_TERRAIN: (255, 214, 0),  # too little flat 20 m ground within 1 km
}


def _default_flat_rule(cfg: dict, terrain: dict) -> float | None:
    """The flat-ground threshold the default /score applies, or None when the
    default ranking does not use the 20 m rule (switched off, layer missing,
    or built for another slope limit)."""
    spec = cfg["hard"].get("terrain_1km") or {}
    layer_slope = (terrain.get("meta") or {}).get("max_slope_deg")
    applies = bool(
        spec.get("enabled", False)
        and terrain["available"]
        and layer_slope is not None
        and math.isclose(float(layer_slope), float(cfg["hard"]["max_slope_deg"]))
    )
    return float(spec.get("min_fraction", 0.5)) if applies else None


def _exclusion_codes(cfg: dict, root: Path, store: dict) -> np.ndarray:
    """Why each cell fails the default hard rules: 0 passes or no data,
    1 fails the slope rule, 2 passes slope but fails the 1 km flat-ground check.

    Uses the config defaults (max slope, slope radius, flat-ground threshold);
    keep-out is per request and is drawn by the app, not here.
    """
    terrain = _terrain_layer(cfg, root, store)
    min_fraction = _default_flat_rule(cfg, terrain)
    key = (EXCLUDED_LAYER, min_fraction)
    if key in store:
        return store[key]
    slope = store["layers"]["slope"]
    slope_ok = hard_mask(slope, cfg=cfg, resolution_m=store["grid"].resolution_m)
    codes = np.zeros(slope.shape, dtype=np.uint8)
    codes[np.isfinite(slope) & ~slope_ok] = EXCLUDED_SLOPE
    if min_fraction is not None:
        flat_ok = terrain_pass(terrain["array"], min_fraction, slope.shape)
        codes[slope_ok & ~flat_ok] = EXCLUDED_TERRAIN
    store[key] = codes
    return codes


def _site_checks(cfg: dict, root: Path, store: dict, row: int, col: int) -> dict:
    """Pass/fail of each default hard rule at one cell, with the value tested."""
    slope = _finite_or_none(store["layers"]["slope"][row, col])
    code = int(_exclusion_codes(cfg, root, store)[row, col])
    checks = {
        "slope": {
            "value": slope,
            "limit": float(cfg["hard"]["max_slope_deg"]),
            "unit": "deg",
            # With a slope radius a cell can fail because of a neighbour.
            "radius_m": float(cfg["hard"].get("slope_radius_m", 0.0)),
            "passes": None if slope is None else code != EXCLUDED_SLOPE,
        }
    }
    terrain = _terrain_layer(cfg, root, store)
    limit = _default_flat_rule(cfg, terrain)
    if limit is not None:
        fraction = _finite_or_none(terrain["array"][row, col])
        checks["flat_ground_1km"] = {
            "value": fraction,
            "limit": limit,
            "unit": "fraction",
            "passes": None if fraction is None else fraction >= limit,
        }
    return checks


def _display_range(cfg: dict, name: str, array: np.ndarray) -> tuple[float, float] | None:
    """Colour range in layer units: a configured fixed range (gate layer), or
    over the pixels the PNG draws (the grid itself, or nanmax blocks for
    oversized arrays), finite minimum to the configured percentile."""
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
        basis = (
            "displayed-pixels" if displayed(array).shape == array.shape else "displayed-block-max"
        )
        headers = {"X-Anuma-Display-Only": "true", "X-Anuma-Range-Basis": basis}
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
        description="Lunar south-pole landing-site screening.",
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
        layers.append(
            {
                "name": EXCLUDED_LAYER,
                "label": "Why excluded (hard rules)",
                "unit": None,
                "mode": None,
                "citation": "ANUMA hard rules at the default settings",
                "present": not missing,
                "display_only": True,
                "excluded": True,
                "scored": False,
                "available": not missing,
                "flat_ground": _default_flat_rule(cfg, terrain),
                "command": None,
            }
        )
        shape = None if missing else (grid.height, grid.width)
        darkness = _darkness_meta(cfg, root, _open_darkness(cfg, root, shape))
        period_label = darkness["period_label"] or _darkness_spec(cfg).get("period_label") or "modelled period"
        citation = "ANUMA Stage G model" + (f", job {darkness['job']}" if darkness["job"] else "")
        for name, label in DARKNESS_LAYERS.items():
            layers.append(
                {
                    "name": name,
                    "label": label.format(period=period_label),
                    "unit": "days",
                    "mode": None,
                    "citation": citation,
                    "present": darkness["available"],
                    "display_only": True,
                    "darkness": True,
                    "scored": False,
                    "available": darkness["available"] and not missing,
                    "windowed": name == "longest_dark",
                    "command": None,
                }
            )
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
                "min_separation_km": float(cfg["scoring"]["min_separation_km"]),
            },
            "illumination_window": _window_meta(cfg, root),
            "terrain_gate": _terrain_summary(cfg, terrain),
            "darkness": darkness,
            "studies_ranking_basis": (
                STUDIES_RANKING_BASIS
                if missing
                else _studies_context(cfg, root, terrain)["served_basis"]
            ),
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
            if name not in ("illumination", "longest_dark"):
                raise HTTPException(
                    status_code=400,
                    detail="start/end months apply only to illumination and longest_dark",
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
        if name == EXCLUDED_LAYER:
            store = _load_store(cfg, root)
            png = render_category_png(_exclusion_codes(cfg, root, store), EXCLUDED_COLOURS)
            return Response(content=png, media_type="image/png")
        if name in DARKNESS_LAYERS:
            store = _load_store(cfg, root)
            if window is not None:
                _check_window(window, _window_bounds(cfg))
            return _darkness_png(cfg, root, store, name, window)
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

    @app.get("/site/{row}/{col}")
    def get_site(
        row: int,
        col: int,
        start: Annotated[str | None, Query()] = None,
        end: Annotated[str | None, Query()] = None,
    ) -> dict:
        window = None
        if start is not None or end is not None:
            if start is None or end is None:
                raise HTTPException(status_code=400, detail="give both start and end months")
            try:
                window = MonthWindow(start=start, end=end)
            except ValidationError as exc:
                detail = "; ".join(item["msg"] for item in exc.errors())
                raise HTTPException(status_code=400, detail=detail) from exc
        return _site_values(row, col, window)

    @app.get("/stability.json")
    def get_stability(basis: Annotated[str | None, Query()] = None) -> dict:
        return _stability_payload(_cfg(), data_root(), basis)

    @app.get("/checks/stage-b-1km.json")
    def get_stage_b_checks() -> JSONResponse:
        path = repo_root() / _cfg()["stability"]["stage_b_checks"]
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Stage B check file is missing")
        return JSONResponse(json.loads(path.read_text()))

    @app.get("/checks/stage-d-noise.json")
    def get_stage_d_checks(basis: Annotated[str | None, Query()] = None) -> JSONResponse:
        """DEM-noise verdicts for the served ranking basis (``?basis=`` overrides)."""
        cfg = _cfg()
        basis, studies = _select_basis(cfg, data_root(), basis)
        if basis != STUDIES_RANKING_BASIS:
            gated = studies["gated"]
            if not gated["available"]:
                raise HTTPException(status_code=404, detail=gated["reason"])
            return JSONResponse(gated["noise"])
        path = repo_root() / cfg["stability"]["stage_d_checks"]
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
