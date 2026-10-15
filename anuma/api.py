"""FastAPI surface for scoring and layer display.

If `data/grid/*.npy` is missing, endpoints that need rasters return HTTP 412
with the ingest command. They do not invent or score `--synthetic` layers.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from anuma.artemis import distinction, feature_collection
from anuma.config import load_config, repo_root
from anuma.export import sites_csv, sites_geojson
from anuma.grid import Grid
from anuma.heatmap import render_png
from anuma.score import apply_keepout, artemis_overlap, hard_mask, normalize, rank_sites, score

INGEST_COMMAND = "python3 -m anuma.ingest"
REQUIRED_FOR_SCORE = ("slope", "illumination", "hydrogen", "earth_visibility")
DISPLAY_LAYERS = ("dem", "slope", "illumination", "hydrogen", "earth_visibility")

_STORE: dict[str, Any] = {}


class HardBody(BaseModel):
    max_slope_deg: float | None = None
    slope_radius_m: float | None = Field(default=None, ge=0.0)


class KeepOutPoint(BaseModel):
    lon: float
    lat: float


class KeepOutBody(BaseModel):
    """User landing points. Radius is required; there is no default."""

    points: list[KeepOutPoint] = Field(default_factory=list)
    radius_km: float | None = None
    mode: str = "hard"
    penalty: float | None = None


class ScoreBody(BaseModel):
    weights: dict[str, Annotated[float, Field(ge=0.0)]] | None = None
    hard: HardBody | None = None
    n: int | None = Field(default=None, ge=1, le=50)
    keepout: KeepOutBody | None = None


def data_root() -> Path:
    override = os.environ.get("ANUMA_ROOT")
    return Path(override) if override else repo_root()


def clear_cache() -> None:
    _STORE.clear()


def _cfg() -> dict:
    return load_config()


def _paths(cfg: dict, root: Path) -> dict[str, Path]:
    p = cfg["paths"]
    return {
        "grid": root / p["grid"],
        "grid_json": root / p["grid_json"],
        "artemis_geojson": root / p["artemis_geojson"],
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
    needed = ["slope", *weights.keys()]
    absent = [name for name in needed if name not in layers]
    if absent:
        raise _not_ingested(absent)
    try:
        result = score(
            layers,
            weights=weights,
            hard={"max_slope_deg": max_slope, "slope_radius_m": slope_radius},
            grid=store["grid"],
            cfg=cfg,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = _with_keepout(result, body)
    sites = rank_sites(result, n=n)
    geojson = _artemis_feature_collection(cfg, root)
    overlap = artemis_overlap(sites, geojson, result=result)
    grid = store["grid"]
    payload = {
        "bounds": [grid.xmin, grid.ymin, grid.xmax, grid.ymax],
        "sites": [_site_payload(s) for s in overlap["sites"]],
        "artemis": _artemis_payload(overlap, cfg),
    }
    if include_png:
        png = render_png(result.combined, cmap="viridis")
        payload["png"] = base64.b64encode(png).decode("ascii")
    return payload


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
        for name, meta in cfg["layers"].items():
            npy = gdir / f"{name}.npy"
            layers.append(
                {
                    "name": meta.get("name", name),
                    "unit": meta.get("unit"),
                    "mode": meta.get("mode"),
                    "citation": meta.get("citation"),
                    "present": npy.is_file(),
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
            },
            "layers": layers,
        }

    @app.post("/score")
    def post_score(body: ScoreBody | None = None) -> dict:
        return _run_score(body)

    @app.post("/score.csv")
    def post_score_csv(body: ScoreBody | None = None) -> Response:
        payload = _run_score(body, include_png=False)
        return Response(
            content=sites_csv(payload["sites"]),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="anuma-ranked-sites.csv"'},
        )

    @app.post("/score.geojson")
    def post_score_geojson(body: ScoreBody | None = None) -> Response:
        payload = _run_score(body, include_png=False)
        return Response(
            content=sites_geojson(payload["sites"]),
            media_type="application/geo+json",
            headers={
                "Content-Disposition": 'attachment; filename="anuma-ranked-sites.geojson"'
            },
        )

    @app.get("/layer/{name}.png")
    def get_layer_png(name: str) -> Response:
        if name == "score":
            # Combined score at the config defaults, in the same viridis as /score.
            png = base64.b64decode(_run_score()["png"])
            return Response(content=png, media_type="image/png")
        cfg = _cfg()
        root = data_root()
        if name not in cfg["layers"]:
            raise HTTPException(status_code=404, detail=f"Unknown layer {name!r}")
        store = _load_store(cfg, root)
        array = store["layers"].get(name)
        if array is None:
            raise _not_ingested([name])
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
