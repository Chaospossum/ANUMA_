"""FastAPI surface for scoring and layer display.

If `data/grid/*.npy` is missing, endpoints that need rasters return HTTP 412
with the ingest command. They do not invent or score `--synthetic` layers.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from anuma.config import load_config, repo_root
from anuma.export import sites_csv, sites_geojson
from anuma.grid import Grid
from anuma.heatmap import render_png
from anuma.ingest import write_artemis_geojson
from anuma.score import artemis_overlap, normalize, rank_sites, score

INGEST_COMMAND = "python3 -m anuma.ingest"
REQUIRED_FOR_SCORE = ("slope", "illumination", "hydrogen", "earth_visibility")
DISPLAY_LAYERS = ("dem", "slope", "illumination", "hydrogen", "earth_visibility")

_STORE: dict[str, Any] = {}


class HardBody(BaseModel):
    max_slope_deg: float | None = None


class ScoreBody(BaseModel):
    weights: dict[str, float] | None = None
    hard: HardBody | None = None
    n: int | None = Field(default=None, ge=1, le=50)


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
                "rank": row.get("rank"),
            }
        )
    art = cfg["artemis3"]
    return {
        "regions": regions,
        "approximate": art.get("approximate"),
        "coordinate_status": art.get("coordinate_status"),
        "source": art.get("source_url"),
        "note": (art.get("note") or "").strip(),
    }


def _artemis_feature_collection(cfg: dict, root: Path) -> dict:
    path = _paths(cfg, root)["artemis_geojson"]
    if path.is_file():
        return json.loads(path.read_text())
    tmp = root / cfg["paths"]["artemis_geojson"]
    write_artemis_geojson(cfg, tmp)
    return json.loads(tmp.read_text())


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
            hard={"max_slope_deg": max_slope},
            grid=store["grid"],
            cfg=cfg,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
            painted = normalize(array, mode, cfg=cfg)
        else:
            painted = array
        png = render_png(painted, cmap="gray")
        return Response(content=png, media_type="image/png")

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
