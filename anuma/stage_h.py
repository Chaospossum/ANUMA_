"""Stage H: robustness of the ranking with the 20 m terrain gate.

Implements ``docs/stage-h-plan.md`` (frozen 2026-10-15). One Slurm job per
mode runs, in order:

1. pins: every input hashed against ``anuma.stage_h_gate`` (240 m input
   manifest of job 5332991, Stage F gate layer, sidecar and output manifest
   of job 5345446, the reference draws of job 5332991, and Stage D's 20 m
   DEM, ERR, EFFRES, LDSM_ERR and Stage B slope);
2. reproduction: draws 0-7 of the weight study with the gate off must equal
   job 5332991's ``draws.jsonl`` (weights and every policy's top 10);
3. the weight Monte Carlo with the gate on (``anuma.hpc_monte_carlo``, same
   seed, ranges, 500 draws and 3 policies; 8 draws in the pilot);
4. the frozen Stage D DEM-noise model on the gated default top 12
   (``anuma.stage_d.run_gated_noise``; 2 sites x 8 draws in the pilot);
5. production only: the Stage D rule analogue as labels (gated top 10 with
   < 80 % membership, or gated ranks 9-12), from step 3's summary.

``--mode checks-file`` turns a production summary into the committed
``data/checks/stage-h-noise.json`` the API serves for the gated ranking.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np

from anuma import stage_h_gate
from anuma.config import load_config

SCHEMA_VERSION = 1
STAGE = "H-gated-ranking-robustness"
CHECKS_KIND = "stage_h_dem_noise_1km_gated"
MC_MODES = {"pilot": "pilot", "production": "full"}

# Values frozen in docs/stage-h-plan.md on 2026-10-15, before any Stage H run.
FROZEN_STAGE_H = {
    "frozen_on": "2026-10-15",
    "plan": "docs/stage-h-plan.md",
    "ranking_basis": stage_h_gate.RANKING_BASIS,
    "monte_carlo_study": "clip_weight_monte_carlo",
    "input_manifest_sha256": stage_h_gate.INPUT_MANIFEST_SHA256,
    "terrain_gate.layer": stage_h_gate.GATE_LAYER,
    "terrain_gate.source_job": stage_h_gate.GATE_JOB,
    "terrain_gate.fraction_sha256": stage_h_gate.GATE_FRACTION_SHA256,
    "terrain_gate.sidecar_sha256": stage_h_gate.GATE_SIDECAR_SHA256,
    "terrain_gate.stage_f_output_manifest_sha256": stage_h_gate.STAGE_F_MANIFEST_SHA256,
    "terrain_gate.min_fraction": 0.5,
    "terrain_gate.radius_m": 1000.0,
    "terrain_gate.max_slope_deg": 8.0,
    "reference_draws_sha256": stage_h_gate.REFERENCE_DRAWS_SHA256,
    "reference_draws_checked": 8,
    "execution.nodes": 1,
    "execution.pilot_qos": "test",
    "execution.pilot_walltime": stage_h_gate.WALLTIME["pilot"],
    "execution.production_walltime": stage_h_gate.WALLTIME["production"],
    "execution.pilot_node_hours": stage_h_gate.NODE_HOURS["pilot"],
    "execution.production_node_hours": stage_h_gate.NODE_HOURS["production"],
    "execution.monte_carlo_projection_node_hours": 0.5,
    "execution.noise_projection_node_hours": 0.5,
    "execution.monte_carlo_workers": 32,
}
# The job 5332991 design; Stage H changes none of it.
FROZEN_MONTE_CARLO = {
    "seed": 20261012,
    "draws.pilot": 8,
    "draws.full": 500,
    "weight_ranges.illumination": [0.4, 0.6],
    "weight_ranges.hydrogen": [0.2, 0.4],
    "weight_ranges.earth_visibility": [0.1, 0.3],
    "normalize_weights_to_one": True,
    "policies": ["legacy_global_2_98", "decided_per_layer", "all_min_max"],
    "clipped_policy": "legacy_global_2_98",
    "unclipped_policy": "decided_per_layer",
    "winner_class_radius_km": 10.0,
}


def _lookup(tree: dict, dotted: str):
    node = tree
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"config is missing {dotted}")
        node = node[part]
    return node


def require_stage_h_config(cfg: dict) -> dict:
    """Return ``hpc_studies.stage_h`` after checking every frozen value."""
    from anuma.stage_d import require_gated_config

    block = cfg["hpc_studies"].get("stage_h")
    if not isinstance(block, dict):
        raise ValueError("config is missing hpc_studies.stage_h")
    for key, value in FROZEN_STAGE_H.items():
        if _lookup(block, key) != value:
            raise ValueError(f"hpc_studies.stage_h.{key} differs from the frozen plan")
    study = cfg["hpc_studies"]["clip_weight_monte_carlo"]
    for key, value in FROZEN_MONTE_CARLO.items():
        if _lookup(study, key) != value:
            raise ValueError(
                f"hpc_studies.clip_weight_monte_carlo.{key} differs from the job 5332991 design"
            )
    spec = cfg["hard"]["terrain_1km"]
    for key in ("min_fraction", "radius_m"):
        if float(spec[key]) != float(block["terrain_gate"][key]):
            raise ValueError(f"hard.terrain_1km.{key} differs from the Stage H gate")
    if float(cfg["hard"]["max_slope_deg"]) != float(block["terrain_gate"]["max_slope_deg"]):
        raise ValueError("hard.max_slope_deg differs from the Stage H gate")
    require_gated_config(cfg)
    return block


def reference_draw_check(reference_path: Path, rows: Sequence[dict], count: int) -> dict:
    """Draws 0..count-1 with the gate off must equal job 5332991's records.

    Compares the weights and, for every policy, the winner and top 10 of each
    ``draws.jsonl`` row exactly.
    """
    reference = []
    with Path(reference_path).open() as handle:
        for line in handle:
            if len(reference) == count:
                break
            reference.append(json.loads(line))
    if len(reference) != count or len(rows) != count:
        raise ValueError(f"reference check needs {count} draws")
    mismatched = [
        int(ours["draw"])
        for ours, theirs in zip(rows, reference)
        if json.dumps(ours, sort_keys=True) != json.dumps(theirs, sort_keys=True)
    ]
    return {"draws_checked": count, "mismatched_draws": mismatched, "identical": not mismatched}


def gated_stability_labels(summary: dict, rule: dict, cfg: dict) -> dict:
    """Stage D rule analogue on the gated ranking, as labels only.

    Rank-unstable: gated top 10 with top-10 membership < 0.80 in the Stage H
    Monte Carlo (``decided_per_layer``), or gated default rank within 2 of the
    cut (ranks 9-12). Every such site is inside the frozen top-12 scope.
    """
    from anuma.stage_d import require_gated_config

    dn = require_gated_config(cfg)
    spec = dn["stability_rule"]
    if summary.get("ranking_basis") != dn["ranking_basis"]:
        raise ValueError("labels need the gated Monte Carlo summary")
    if int(summary.get("draw_count", -1)) != int(FROZEN_MONTE_CARLO["draws.full"]):
        raise ValueError("labels need the 500-draw summary")
    n_sites = int(cfg["scoring"]["n_sites"])
    margin = int(spec["rank_margin"])
    min_freq = float(spec["min_top10_frequency"])
    entries = summary["policies"][spec["policy"]]["top10_membership_frequency"]
    frequency = {str(entry["id"]): float(entry["frequency"]) for entry in entries}
    rows = []
    unstable = []
    for row in rule["ranking"]:
        rank = int(row["default_rank"])
        freq = frequency.get(row["id"], 0.0)
        by_frequency = rank <= n_sites and freq < min_freq
        by_margin = n_sites - margin + 1 <= rank <= n_sites + margin
        if by_frequency or by_margin:
            unstable.append(row["id"])
        rows.append(
            {
                "default_rank": rank,
                "id": row["id"],
                "top10_frequency": freq,
                "clause_frequency": bool(by_frequency),
                "clause_rank_margin": bool(by_margin),
            }
        )
    outside = [sid for sid in unstable if sid not in rule["site_order"]]
    if outside:
        raise ValueError(f"rank-unstable sites outside the frozen scope: {outside}")
    return {
        "unstable_ids": unstable,
        "rule": {
            "min_top10_frequency": min_freq,
            "rank_margin": margin,
            "rank_margin_ranks": [n_sites - margin + 1, n_sites + margin],
            "policy": spec["policy"],
        },
        "ranking": rows,
    }


def _log(log_path: Path, message: str) -> None:
    with Path(log_path).open("a") as handle:
        handle.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=True) + "\n")
    os.replace(temporary, path)


def project_production(
    *,
    fixed_seconds: float,
    mc_pilot_seconds: float,
    mc_pilot_draws: int,
    mc_production_draws: int,
    noise_projection: dict,
    mc_share_hours: float,
    noise_share_hours: float,
    production_hours: float,
) -> dict:
    """Bound the production job from the pilot.

    The Monte Carlo is charged its whole pilot time (scoring preparation
    included) per pilot draw, so the bound is conservative. Fixed time
    (hashing, reproduction check) is the same in both modes.
    """
    mc_hours = float(mc_pilot_seconds) * mc_production_draws / mc_pilot_draws / 3600.0
    noise_hours = float(noise_projection["projected_node_hours"])
    total = float(fixed_seconds) / 3600.0 + mc_hours + noise_hours
    return {
        "fixed_hours": float(fixed_seconds) / 3600.0,
        "monte_carlo_hours": mc_hours,
        "noise_hours": noise_hours,
        "total_hours": total,
        "monte_carlo_fits_share": bool(mc_hours <= mc_share_hours),
        "noise_fits_share": bool(noise_hours <= noise_share_hours),
        "total_fits": bool(total <= production_hours),
        "shares_hours": {"monte_carlo": mc_share_hours, "noise": noise_share_hours},
        "production_hours": production_hours,
    }


def run_stage_h(
    *,
    mode: str,
    input_manifest: Path,
    dem: Path,
    raw_dir: Path,
    stage_b_slope: Path,
    stage_f_dir: Path,
    reference_draws: Path,
    output_dir: Path,
    workers: int,
) -> dict:
    """Pilot or production on real inputs. Refuses before any draw on a bad pin."""
    from anuma import hpc_monte_carlo, stage_d
    from anuma.grid import Grid
    from anuma.hpc_smoke import ARRAY_FILES, sha256_file, validate_input_manifest

    if mode not in MC_MODES:
        raise ValueError("mode must be pilot or production")
    if workers <= 0 or workers > 128:
        raise ValueError("workers must be between 1 and 128")
    started = datetime.now(timezone.utc).isoformat()
    clock = time.perf_counter()
    cfg = load_config()
    block = require_stage_h_config(cfg)
    execution = block["execution"]
    mc_workers = min(int(execution["monte_carlo_workers"]), workers)

    output_dir = Path(output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "progress.log"

    inputs = stage_h_gate.expected_inputs(
        raw_dir=raw_dir,
        dem=dem,
        stage_b_slope=stage_b_slope,
        stage_f_dir=stage_f_dir,
        reference_draws=reference_draws,
        input_manifest=input_manifest,
    )
    digests = stage_h_gate.verify_input_digests(inputs)
    gate_record = stage_h_gate.check_gate_records(
        inputs["gate_sidecar"]["path"], inputs["stage_f_manifest"]["path"]
    )
    _log(log_path, f"verified {len(digests)} input digests; gate layer job {gate_record['job']}")

    # Reproduction: the gate-off pipeline on these arrays is job 5332991's.
    _, files = validate_input_manifest(Path(inputs["input_manifest"]["path"]))
    grid = Grid.from_json(files["grid"], cfg=cfg)
    layers = {
        name: np.load(files[name], mmap_mode="r", allow_pickle=False) for name in ARRAY_FILES
    }
    count = int(block["reference_draws_checked"])
    _, rows = hpc_monte_carlo.run_study(
        layers, grid, cfg, mode="pilot", draw_count=count, workers=mc_workers
    )
    reproduction = reference_draw_check(inputs["reference_draws"]["path"], rows, count)
    fixed_seconds = time.perf_counter() - clock
    _log(log_path, f"reference draws 0-{count - 1} identical: {reproduction['identical']}")

    mc_clock = time.perf_counter()
    mc_dir = output_dir / "monte-carlo"
    mc_manifest = hpc_monte_carlo.run_from_manifest(
        Path(inputs["input_manifest"]["path"]),
        mc_dir,
        mode=MC_MODES[mode],
        workers=mc_workers,
        terrain_fraction=Path(inputs["gate_fraction"]["path"]),
        terrain_sidecar=Path(inputs["gate_sidecar"]["path"]),
    )
    mc_seconds = time.perf_counter() - mc_clock
    if mc_manifest.get("input_manifest", {}).get("sha256") != stage_h_gate.INPUT_MANIFEST_SHA256:
        raise ValueError("gated Monte Carlo did not read the pinned input manifest")
    summary =json.loads((mc_dir / "study-summary.json").read_text())
    mc_checks = {
        "ranking_basis": summary.get("ranking_basis") == block["ranking_basis"],
        "gate_layer_pinned": (summary.get("terrain_gate") or {}).get("fraction_sha256")
        == stage_h_gate.GATE_FRACTION_SHA256,
        "draw_count": int(summary["draw_count"])
        == int(FROZEN_MONTE_CARLO[f"draws.{MC_MODES[mode]}"]),
    }
    _log(log_path, f"gated Monte Carlo: {summary['draw_count']} draws in {mc_seconds:.1f} s")

    noise = stage_d.run_gated_noise(
        mode=mode,
        inputs=inputs,
        digests=digests,
        output_dir=output_dir / "dem-noise",
        workers=workers,
        production_node_hours=float(execution["noise_projection_node_hours"]),
    )
    _log(log_path, f"gated DEM-noise {mode}: passed={noise['passed']}")

    checks = {
        "reference_draws_identical": bool(reproduction["identical"]),
        "monte_carlo": mc_checks,
        "monte_carlo_passed": bool(all(mc_checks.values())),
        "noise_passed": bool(noise["passed"]),
        "noise_checks": noise["checks"],
    }
    report: dict = {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "mode": mode,
        "kind": "screening sensitivity, not a landing recommendation",
        "note": (
            "Not affiliated with NASA, ESA or ispace. Robustness of the default "
            "ranking with the Stage F 20 m terrain gate; see docs/stage-h-plan.md."
        ),
        "plan": block["plan"],
        "frozen_on": block["frozen_on"],
        "ranking_basis": block["ranking_basis"],
        "started_at_utc": started,
        "completed_at_utc": None,
        "gate_layer": gate_record,
        "reference_reproduction": reproduction,
        "monte_carlo": {
            "dir": "monte-carlo",
            "draw_count": int(summary["draw_count"]),
            "seconds": mc_seconds,
            "workers": mc_workers,
            "output_manifest": mc_manifest["outputs"],
        },
        "dem_noise": {
            "dir": "dem-noise",
            "site_order": noise["scope"]["site_order"],
            "passed": bool(noise["passed"]),
            "verdict": noise.get("verdict"),
        },
        "checks": checks,
        "timings_seconds": {"fixed": fixed_seconds, "monte_carlo": mc_seconds},
        "inputs": {
            name: {"path": str(Path(entry["path"]).resolve()), "sha256": digests[name]}
            for name, entry in inputs.items()
        },
        "execution": {
            "workers": workers,
            "monte_carlo_workers": mc_workers,
            **{
                key: os.environ[key]
                for key in (
                    "SLURM_JOB_ID",
                    "SLURM_JOB_ACCOUNT",
                    "SLURM_JOB_PARTITION",
                    "SLURM_JOB_QOS",
                    "SLURM_CPUS_PER_TASK",
                    "SLURM_JOB_NUM_NODES",
                    "SLURM_MEM_PER_NODE",
                )
                if key in os.environ
            },
        },
    }
    if mode == "pilot":
        report["projection"] = project_production(
            fixed_seconds=fixed_seconds,
            mc_pilot_seconds=mc_seconds,
            mc_pilot_draws=int(summary["draw_count"]),
            mc_production_draws=int(FROZEN_MONTE_CARLO["draws.full"]),
            noise_projection=noise["projection"],
            mc_share_hours=float(execution["monte_carlo_projection_node_hours"]),
            noise_share_hours=float(execution["noise_projection_node_hours"]),
            production_hours=float(stage_h_gate.NODE_HOURS["production"]),
        )
        projection = report["projection"]
        checks["projection_fits"] = bool(
            projection["monte_carlo_fits_share"]
            and projection["noise_fits_share"]
            and projection["total_fits"]
        )
    else:
        report["rank_unstable_labels"] = gated_stability_labels(
            summary, noise["stability_rule"], cfg
        )
        checks["projection_fits"] = True
    report["passed"] = bool(
        checks["reference_draws_identical"]
        and checks["monte_carlo_passed"]
        and checks["noise_passed"]
        and checks["projection_fits"]
    )
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    name = "pilot-measurement.json" if mode == "pilot" else "stage-h-summary.json"
    report_path = output_dir / name
    _atomic_json(report_path, report)
    outputs = {}
    top_manifest = output_dir / "output-manifest.json"
    for path in sorted(output_dir.rglob("*")):
        if path.is_file() and path != top_manifest and path.name != "progress.log":
            outputs[str(path.relative_to(output_dir))] = {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
    _atomic_json(output_dir / "output-manifest.json", {**report, "outputs": outputs})
    _log(log_path, f"{mode} written; passed={report['passed']}")
    return report


# --------------------------------------------------------------------------
# Committed check file for the API
# --------------------------------------------------------------------------


def checks_file(summary: dict, noise_summary: dict, *, commit: str | None = None) -> dict:
    """``data/checks/stage-h-noise.json`` from a passed production run.

    Same per-site fields as ``data/checks/stage-d-noise.json`` (the UI reads
    them by row/col), plus the ranking basis and the gate layer digest the
    API matches against the loaded layer before serving it.
    """
    if summary.get("stage") != STAGE or summary.get("mode") != "production":
        raise ValueError("checks file needs a Stage H production summary")
    if summary.get("passed") is not True or noise_summary.get("passed") is not True:
        raise ValueError("checks file needs a passed Stage H production run")
    if noise_summary.get("mode") != "production":
        raise ValueError("checks file needs the production DEM-noise summary")
    threshold = float(noise_summary["sites"][noise_summary["scope"]["site_order"][0]]["runs"][
        "primary"
    ]["pass_fail"]["threshold"])
    labels = {
        row["id"]: row for row in (summary.get("rank_unstable_labels") or {}).get("ranking", [])
    }
    unstable = set((summary.get("rank_unstable_labels") or {}).get("unstable_ids") or [])
    ranking = {row["id"]: row for row in noise_summary["stability_rule"]["ranking"]}
    sites = []
    for sid in noise_summary["scope"]["site_order"]:
        entry = noise_summary["sites"][sid]
        primary = entry["runs"]["primary"]
        sensitivity = entry["runs"]["sensitivity"]
        decision = primary["decision"]
        row = ranking[sid]
        sites.append(
            {
                "site_id": sid,
                "row": int(row["row"]),
                "col": int(row["col"]),
                "default_rank": int(entry["default_rank"]),
                "zero_noise_within_1km": round(float(primary["fraction_1km"]["zero_noise"]), 4),
                "p5": round(float(decision["p_low"]), 4),
                "p95": round(float(decision["p_high"]), 4),
                "width": round(float(decision["width"]), 4),
                "noise_unstable": bool(decision["noise_unstable"]),
                "clause": decision["clause_fired"],
                "pass_rate": float(primary["pass_fail"]["pass_rate"]),
                "sensitivity_pass_rate": float(sensitivity["pass_fail"]["pass_rate"]),
                "sensitivity_noise_unstable": bool(sensitivity["decision"]["noise_unstable"]),
                "rank_unstable": sid in unstable,
                "top10_frequency": (labels.get(sid) or {}).get("top10_frequency"),
                "lat": round(float(row["lat"]), 6),
                "lon": round(float(row["lon"]), 6),
            }
        )
    gate = summary["gate_layer"]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": CHECKS_KIND,
        "ranking_basis": summary["ranking_basis"],
        "terrain_gate": {
            "fraction_sha256": gate["fraction_sha256"],
            "job": gate["job"],
            "min_fraction": 0.5,
            "radius_m": gate["radius_m"],
            "max_slope_deg": gate["max_slope_deg"],
        },
        "source": {
            "job": str(summary["execution"].get("SLURM_JOB_ID")),
            "commit": commit,
            "summary": "stage-h-summary.json",
            "plan": "docs/stage-h-plan.md",
        },
        "threshold": threshold,
        "max_slope_deg": gate["max_slope_deg"],
        "draws": int(noise_summary["scope"]["draws_per_site_and_run"]),
        "text": {
            "stable_pass": "passes the 50 % / 1 km rule in every DEM-noise draw",
            "stable_fail": "fails the 50 % / 1 km rule in every DEM-noise draw",
            "unstable": "on the 50 % line: pass/fail undecidable under published DEM error",
        },
        "note": (
            "Gated default ranks 1-12 (20 m terrain gate applied). Primary noise model "
            "only decides; LOLA PGDA 90 height error as 1 sigma Gaussian. Sites outside "
            "this list were not tested."
        ),
        "sites": sites,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("pilot", "production", "checks-file"), required=True)
    parser.add_argument("--input-manifest", type=Path, default=None)
    parser.add_argument("--dem", type=Path, default=None)
    parser.add_argument("--raw-dir", type=Path, default=None)
    parser.add_argument("--stage-b-slope", type=Path, default=None)
    parser.add_argument("--stage-f-dir", type=Path, default=None)
    parser.add_argument("--reference-draws", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--run-dir", type=Path, default=None, help="checks-file: production dir")
    parser.add_argument("--out", type=Path, default=None, help="checks-file: output path")
    parser.add_argument("--commit", default=None, help="checks-file: commit the run used")
    args = parser.parse_args(argv)
    try:
        if args.mode == "checks-file":
            if args.run_dir is None or args.out is None:
                parser.error("--run-dir and --out are required for checks-file")
            summary = json.loads((args.run_dir / "stage-h-summary.json").read_text())
            noise = json.loads(
                (args.run_dir / "dem-noise" / "dem-noise-summary.json").read_text()
            )
            payload = checks_file(summary, noise, commit=args.commit)
            args.out.write_text(json.dumps(payload, indent=2) + "\n")
            print(json.dumps({"written": str(args.out), "sites": len(payload["sites"])}))
            return
        for name in (
            "input_manifest", "dem", "raw_dir", "stage_b_slope", "stage_f_dir",
            "reference_draws", "output_dir",
        ):
            if getattr(args, name) is None:
                parser.error(f"--{name.replace('_', '-')} is required for {args.mode}")
        report = run_stage_h(
            mode=args.mode,
            input_manifest=args.input_manifest,
            dem=args.dem,
            raw_dir=args.raw_dir,
            stage_b_slope=args.stage_b_slope,
            stage_f_dir=args.stage_f_dir,
            reference_draws=args.reference_draws,
            output_dir=args.output_dir,
            workers=args.workers,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Stage H precondition failed: {exc}\n")
    print(json.dumps({"passed": report["passed"], "checks": report["checks"]}, indent=2))
    if args.mode == "pilot" and not report["checks"]["projection_fits"]:
        raise SystemExit(4)
    if not report["passed"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
