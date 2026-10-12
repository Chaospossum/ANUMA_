"""Deterministic 240 m clip-and-weight Monte Carlo study.

The study consumes an affine-fix provenance-pinned input manifest. It never
downloads or ingests data and does not submit itself to Slurm.
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
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
    prepared = {
        policy: score(
            layers,
            cfg["scoring"]["weights"],
            cfg["hard"],
            grid=grid,
            cfg=cfg,
            normalization_policy=policy,
        )
        for policy in policies
    }

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
    return summary, draw_rows


def run_from_manifest(
    input_manifest: Path,
    output_dir: Path,
    *,
    mode: str,
    workers: int = 1,
) -> dict[str, Any]:
    manifest, files = validate_input_manifest(input_manifest)
    cfg = load_config()
    grid = Grid.from_json(files["grid"], cfg=cfg)
    layers = {
        name: np.load(files[name], mmap_mode="r", allow_pickle=False)
        for name in ARRAY_FILES
    }
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc).isoformat()
    summary, draws = run_study(layers, grid, cfg, mode=mode, workers=workers)
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
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "execution": {
            "workers": workers,
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
    args = parser.parse_args(argv)
    try:
        result = run_from_manifest(
            args.input_manifest,
            args.output_dir,
            mode=args.mode,
            workers=args.workers,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f"study precondition failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
