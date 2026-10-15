"""Deterministic 240 m clip-and-weight Monte Carlo study.

The study consumes an affine-fix provenance-pinned input manifest. It never
downloads or ingests data and does not submit itself to Slurm.

With a Stage F ``terrain_1km_fraction`` layer (``terrain_fraction`` /
``--terrain-fraction``) every draw ranks with the 20 m terrain gate applied,
as the default ``/score`` does (Stage H, docs/stage-h-plan.md). Weights,
seed, draws and policies are unchanged; the summary records the
``ranking_basis``.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from anuma.config import load_config
from anuma.grid import Grid
from anuma.hpc_smoke import (
    ARRAY_FILES,
    sha256_file,
    validate_input_manifest,
)
from anuma.score import ScoreResult, rank_sites, score


def draw_weights(cfg: dict, count: int) -> list[dict[str, float]]:
    study = cfg["hpc_studies"]["clip_weight_monte_carlo"]
    rng = np.random.default_rng(int(study["seed"]))
    names = tuple(cfg["scoring"]["weights"])
    ranges = study["weight_ranges"]
    draws: list[dict[str, float]] = []
    for _ in range(count):
        values = np.asarray(
            [rng.uniform(float(ranges[name][0]), float(ranges[name][1])) for name in names],
            dtype=np.float64,
        )
        if study.get("normalize_weights_to_one", False):
            values /= values.sum()
        draws.append({name: float(value) for name, value in zip(names, values)})
    return draws


SLOPE_ONLY_BASIS = "slope_only"


def ranking_basis_label(gate: dict) -> str:
    """Study label for a ranking: ``slope_only`` or the applied gate's rule."""
    if gate.get("status") != "applied":
        return SLOPE_ONLY_BASIS
    return (
        f"terrain_gate_{float(gate['layer_max_slope_deg']):g}deg_"
        f"{float(gate['radius_m']) / 1000.0:g}km_"
        f"{int(round(float(gate['min_fraction']) * 100))}pct"
    )


def gated_hard(cfg: dict, terrain_max_slope_deg: float) -> dict:
    """The default hard rule with the terrain gate forced on (fails if unusable)."""
    spec = cfg["hard"]["terrain_1km"]
    return {
        "max_slope_deg": float(cfg["hard"]["max_slope_deg"]),
        "slope_radius_m": float(cfg["hard"].get("slope_radius_m", 0.0)),
        "terrain_1km": True,
        "terrain_1km_min_fraction": float(spec["min_fraction"]),
        "terrain_1km_max_slope_deg": float(terrain_max_slope_deg),
    }


def _site_id(site: dict) -> str:
    return f"{int(site['row'])}:{int(site['col'])}"


def _rank_with_weights(
    prepared: ScoreResult,
    weights: dict[str, float],
) -> list[dict]:
    combined = np.zeros(prepared.combined.shape, dtype=np.float64)
    for name, weight in weights.items():
        combined += float(weight) * prepared.normalized[name].astype(np.float64)
    combined = np.where(prepared.hard_mask, combined, np.nan).astype(np.float32)
    result = ScoreResult(
        combined=combined,
        normalized=prepared.normalized,
        hard_mask=prepared.hard_mask,
        weights=weights,
        grid=prepared.grid,
        normalization_policy=prepared.normalization_policy,
        cfg=prepared.cfg,
        ranking_inputs=prepared.ranking_inputs,
    )
    return rank_sites(result)


def _winner_class_changed(
    first: dict,
    second: dict,
    resolution_m: float,
    radius_km: float,
) -> bool:
    distance_m = np.hypot(
        float(first["row"] - second["row"]) * resolution_m,
        float(first["col"] - second["col"]) * resolution_m,
    )
    return bool(distance_m > radius_km * 1000.0)


def _evaluate_draw(
    item: tuple[int, dict[str, float]],
    prepared: dict[str, ScoreResult],
    policies: list[str],
) -> tuple[dict[str, Any], dict[str, list[dict]]]:
    index, draw = item
    row: dict[str, Any] = {"draw": index, "weights": draw, "policies": {}}
    ranked: dict[str, list[dict]] = {}
    for policy in policies:
        sites = _rank_with_weights(prepared[policy], draw)
        if not sites:
            raise ValueError(f"policy {policy!r} produced no ranked sites")
        ranked[policy] = sites
        ids = [_site_id(site) for site in sites]
        row["policies"][policy] = {"winner": ids[0], "top10": ids}
    return row, ranked


def run_study(
    layers: dict[str, np.ndarray],
    grid: Grid,
    cfg: dict,
    *,
    mode: str,
    draw_count: int | None = None,
    workers: int = 1,
    terrain_fraction: np.ndarray | None = None,
    terrain_max_slope_deg: float | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the weight draws for every policy.

    Without ``terrain_fraction`` the hard rule is ``cfg["hard"]`` exactly as
    in job 5332991 (slope only). With it, the gate is forced on for every
    policy (``score`` raises if it cannot apply), and normalisation and
    ranking see the gated mask, as the default ``/score`` does.
    """
    study_cfg = cfg["hpc_studies"]["clip_weight_monte_carlo"]
    if mode not in ("pilot", "full"):
        raise ValueError("mode must be pilot or full")
    count = int(draw_count if draw_count is not None else study_cfg["draws"][mode])
    if count <= 0:
        raise ValueError("draw count must be positive")
    if workers <= 0 or workers > 128:
        raise ValueError("workers must be between 1 and 128")
    weights = draw_weights(cfg, count)
    policies = list(study_cfg["policies"])
    if terrain_fraction is None:
        score_layers, hard = layers, cfg["hard"]
    else:
        if terrain_max_slope_deg is None:
            raise ValueError("terrain_max_slope_deg is required with terrain_fraction")
        name = str(cfg["hard"]["terrain_1km"]["layer"])
        score_layers = {**layers, name: terrain_fraction}
        hard = gated_hard(cfg, terrain_max_slope_deg)
    prepared = {
        policy: score(
            score_layers,
            cfg["scoring"]["weights"],
            hard,
            grid=grid,
            cfg=cfg,
            normalization_policy=policy,
        )
        for policy in policies
    }
    gates = {policy: prepared[policy].terrain_gate for policy in policies}
    bases = {ranking_basis_label(gate) for gate in gates.values()}
    if len(bases) != 1:
        raise ValueError(f"policies ranked on different bases: {sorted(bases)}")
    ranking_basis = bases.pop()
    if terrain_fraction is not None and ranking_basis == SLOPE_ONLY_BASIS:
        raise ValueError("terrain gate was given but not applied")
    if terrain_fraction is None and ranking_basis != SLOPE_ONLY_BASIS:
        raise ValueError(f"gate-off study ranked on {ranking_basis}, not slope only")

    membership = {policy: Counter() for policy in policies}
    winners = {policy: Counter() for policy in policies}
    site_metadata: dict[str, dict[str, Any]] = {}
    draw_rows: list[dict[str, Any]] = []
    winner_sites: dict[str, list[dict]] = {policy: [] for policy in policies}

    items = list(enumerate(weights))
    if workers == 1:
        evaluated = [_evaluate_draw(item, prepared, policies) for item in items]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            # executor.map preserves draw order. Aggregation below is serial, so
            # worker scheduling cannot alter floating-point operations or output.
            evaluated = list(
                executor.map(
                    lambda item: _evaluate_draw(item, prepared, policies), items
                )
            )

    for row, ranked in evaluated:
        index = int(row["draw"])
        for policy in policies:
            sites = ranked[policy]
            ids = [_site_id(site) for site in sites]
            membership[policy].update(ids)
            winners[policy].update([ids[0]])
            winner_sites[policy].append(sites[0])
            for site_id, site in zip(ids, sites):
                site_metadata.setdefault(
                    site_id,
                    {
                        "id": site_id,
                        "row": int(site["row"]),
                        "col": int(site["col"]),
                        "lon": float(site["lon"]),
                        "lat": float(site["lat"]),
                    },
                )
        draw_rows.append(row)

    clipped = str(study_cfg["clipped_policy"])
    unclipped = str(study_cfg["unclipped_policy"])
    radius_km = float(study_cfg["winner_class_radius_km"])
    changed = sum(
        _winner_class_changed(
            winner_sites[clipped][index],
            winner_sites[unclipped][index],
            grid.resolution_m,
            radius_km,
        )
        for index in range(count)
    )

    policy_results = {}
    for policy in policies:
        membership_rows = [
            {
                **site_metadata[site_id],
                "count": int(site_count),
                "frequency": float(site_count / count),
            }
            for site_id, site_count in membership[policy].items()
        ]
        membership_rows.sort(key=lambda item: (-item["count"], item["id"]))
        winner_rows = [
            {
                **site_metadata[site_id],
                "count": int(site_count),
                "frequency": float(site_count / count),
            }
            for site_id, site_count in winners[policy].items()
        ]
        winner_rows.sort(key=lambda item: (-item["count"], item["id"]))
        policy_results[policy] = {
            "top10_membership_frequency": membership_rows,
            "winner_stability": {
                "unique_winners": len(winner_rows),
                "winners": winner_rows,
                "most_frequent_winner": winner_rows[0],
            },
        }

    summary = {
        "schema_version": 1,
        "study": "clip_weight_monte_carlo_240m",
        "ranking_basis": ranking_basis,
        "mode": mode,
        "draw_count": count,
        "seed": int(study_cfg["seed"]),
        "weight_ranges": study_cfg["weight_ranges"],
        "normalize_weights_to_one": bool(study_cfg["normalize_weights_to_one"]),
        "policies": policy_results,
        "clipped_vs_unclipped_winner_class": {
            "clipped_policy": clipped,
            "unclipped_policy": unclipped,
            "class_radius_km": radius_km,
            "changed_draws": int(changed),
            "changed_frequency": float(changed / count),
        },
    }
    if ranking_basis != SLOPE_ONLY_BASIS:
        gate = gates[str(cfg["scoring"]["default_normalization_policy"])]
        summary["terrain_gate"] = {
            key: gate.get(key)
            for key in (
                "status",
                "layer",
                "min_fraction",
                "radius_m",
                "layer_max_slope_deg",
                "cells_passing_slope",
                "cells_removed",
            )
        }
    return summary, draw_rows


def run_from_manifest(
    input_manifest: Path,
    output_dir: Path,
    *,
    mode: str,
    workers: int = 1,
    terrain_fraction: Path | None = None,
    terrain_sidecar: Path | None = None,
    draw_count: int | None = None,
) -> dict[str, Any]:
    """Run the study and write summary, draws and an output manifest.

    ``terrain_fraction`` and ``terrain_sidecar`` (both or neither) apply the
    Stage F gate. The sidecar must pass ``anuma.stage_f.validate_sidecar``
    for this grid, including the layer's sha256; callers that need a specific
    layer (Stage H) check its pinned digest before calling.
    """
    manifest, files = validate_input_manifest(input_manifest)
    cfg = load_config()
    grid = Grid.from_json(files["grid"], cfg=cfg)
    layers = {
        name: np.load(files[name], mmap_mode="r", allow_pickle=False)
        for name in ARRAY_FILES
    }
    if (terrain_fraction is None) != (terrain_sidecar is None):
        raise ValueError("give both terrain_fraction and terrain_sidecar, or neither")
    gate_meta: dict | None = None
    gate_array = None
    if terrain_fraction is not None:
        from anuma.stage_f import validate_sidecar

        gate_meta = json.loads(Path(terrain_sidecar).read_text())
        problem = validate_sidecar(gate_meta, grid, cfg, npy_path=Path(terrain_fraction))
        if problem is not None:
            raise ValueError(f"terrain gate layer refused: {problem}")
        gate_array = np.load(terrain_fraction, allow_pickle=False)
        if gate_array.shape != (grid.height, grid.width):
            raise ValueError("terrain gate layer does not match the 240 m grid")
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc).isoformat()
    summary, draws = run_study(
        layers,
        grid,
        cfg,
        mode=mode,
        workers=workers,
        draw_count=draw_count,
        terrain_fraction=gate_array,
        terrain_max_slope_deg=None if gate_meta is None else float(gate_meta["max_slope_deg"]),
    )
    if gate_meta is not None:
        summary["terrain_gate"].update(
            {
                "fraction_sha256": gate_meta["fraction_sha256"],
                "source_job": str(gate_meta["job"]),
            }
        )
        if os.environ.get("SLURM_JOB_ID"):
            summary["source_job"] = os.environ["SLURM_JOB_ID"]
    summary_path = output_dir / "study-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    draws_path = output_dir / "draws.jsonl"
    with draws_path.open("w") as handle:
        for row in draws:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    output_manifest = {
        "schema_version": 1,
        "status": "completed",
        "started_at_utc": started,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_manifest": {
            "path": str(input_manifest.resolve()),
            "sha256": sha256_file(input_manifest.resolve()),
            "declared_id": manifest.get("id"),
            "illumination_affine_revision": manifest.get("provenance", {}).get(
                "illumination_affine_revision"
            ),
        },
        "ranking_basis": summary["ranking_basis"],
        **(
            {}
            if gate_meta is None
            else {
                "terrain_gate": {
                    "fraction": str(Path(terrain_fraction).resolve()),
                    "fraction_sha256": gate_meta["fraction_sha256"],
                    "sidecar": str(Path(terrain_sidecar).resolve()),
                    "sidecar_sha256": sha256_file(Path(terrain_sidecar).resolve()),
                    "job": str(gate_meta["job"]),
                }
            }
        ),
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "execution": {
            "workers": workers,
            **{
                key: value
                for key, value in os.environ.items()
                if key
                in {
                    "SLURM_JOB_ID",
                    "SLURM_JOB_ACCOUNT",
                    "SLURM_JOB_PARTITION",
                    "SLURM_JOB_QOS",
                    "SLURM_CPUS_PER_TASK",
                    "SLURM_JOB_NUM_NODES",
                }
            },
        },
        "outputs": {
            path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in (summary_path, draws_path)
        },
    }
    manifest_path = output_dir / "output-manifest.json"
    manifest_path.write_text(
        json.dumps(output_manifest, indent=2, sort_keys=True) + "\n"
    )
    return output_manifest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("pilot", "full"), required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--terrain-fraction", type=Path, default=None)
    parser.add_argument("--terrain-sidecar", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        result = run_from_manifest(
            args.input_manifest,
            args.output_dir,
            mode=args.mode,
            workers=args.workers,
            terrain_fraction=args.terrain_fraction,
            terrain_sidecar=args.terrain_sidecar,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f"study precondition failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
