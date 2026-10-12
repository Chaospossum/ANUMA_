"""Guarded 240 m scoring smoke test for batch execution.

This command never downloads or ingests data. It accepts only a checksum-pinned
input manifest and writes a ranking plus a machine-readable output manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from anuma.config import load_config, repo_root
from anuma.grid import Grid
from anuma.score import rank_sites, score

SCHEMA_VERSION = 1
EXPECTED_RESOLUTION_M = 240.0
REQUIRED_FILES = (
    "grid",
    "dem",
    "slope",
    "illumination",
    "hydrogen",
    "earth_visibility",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(base: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError(f"manifest path must be relative: {value}")
    resolved = (base / candidate).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"manifest path escapes its directory: {value}") from exc
    return resolved


def validate_input_manifest(path: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    path = path.resolve()
    manifest = json.loads(path.read_text())
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    if float(manifest.get("resolution_m", 0.0)) != EXPECTED_RESOLUTION_M:
        raise ValueError("input manifest must declare resolution_m=240")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ValueError("input manifest files must be an object")

    resolved: dict[str, Path] = {}
    for name in REQUIRED_FILES:
        entry = files.get(name)
        if not isinstance(entry, dict):
            raise ValueError(f"input manifest is missing files.{name}")
        expected = entry.get("sha256")
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"files.{name}.sha256 must be a SHA-256 hex digest")
        file_path = _safe_relative(path.parent, str(entry.get("path", "")))
        if not file_path.is_file():
            raise ValueError(f"input file does not exist: {file_path}")
        actual = sha256_file(file_path)
        if actual.lower() != expected.lower():
            raise ValueError(f"checksum mismatch for {name}: expected {expected}, got {actual}")
        resolved[name] = file_path

    grid_meta = json.loads(resolved["grid"].read_text())
    if float(grid_meta.get("resolution_m", 0.0)) != EXPECTED_RESOLUTION_M:
        raise ValueError("grid.json is not a 240 m grid")
    return manifest, resolved


def _git_commit() -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_root()), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slurm_metadata() -> dict[str, str]:
    names = (
        "SLURM_JOB_ID",
        "SLURM_JOB_NAME",
        "SLURM_JOB_ACCOUNT",
        "SLURM_JOB_PARTITION",
        "SLURM_JOB_QOS",
        "SLURM_CPUS_PER_TASK",
        "SLURM_JOB_NUM_NODES",
        "SLURM_NTASKS",
    )
    return {name: os.environ[name] for name in names if name in os.environ}


def _module_metadata() -> dict[str, Any]:
    record = os.environ.get("ANUMA_MODULE_LIST_FILE")
    if record and Path(record).is_file():
        return {
            "source": str(Path(record).resolve()),
            "entries": Path(record).read_text().splitlines(),
        }
    return {
        "source": "LOADEDMODULES",
        "entries": [
            item for item in os.environ.get("LOADEDMODULES", "").split(":") if item
        ],
    }


def run_smoke(input_manifest: Path, output_dir: Path, check_only: bool = False) -> dict:
    manifest, files = validate_input_manifest(input_manifest)
    grid_meta = json.loads(files["grid"].read_text())
    cfg = load_config()
    grid = Grid.from_json(files["grid"], cfg=cfg)
    expected_shape = (int(grid_meta["height"]), int(grid_meta["width"]))

    layers: dict[str, np.ndarray] = {}
    for name in REQUIRED_FILES[1:]:
        array = np.load(files[name], mmap_mode="r", allow_pickle=False)
        if array.shape != expected_shape:
            raise ValueError(
                f"{name} shape {array.shape} does not match grid {expected_shape}"
            )
        if not np.issubdtype(array.dtype, np.number):
            raise ValueError(f"{name} must have a numeric dtype")
        layers[name] = array

    if check_only:
        return {"status": "validated", "shape": list(expected_shape)}

    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    started = _utc_now()
    result = score(layers, grid=grid, cfg=cfg)
    ranking = {
        "schema_version": SCHEMA_VERSION,
        "resolution_m": EXPECTED_RESOLUTION_M,
        "sites": rank_sites(result),
    }
    ranking_path = output_dir / "ranking.json"
    ranking_path.write_text(json.dumps(ranking, indent=2, sort_keys=True) + "\n")

    output_manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "completed",
        "started_at_utc": started,
        "completed_at_utc": _utc_now(),
        "input_manifest": {
            "path": str(input_manifest.resolve()),
            "sha256": sha256_file(input_manifest.resolve()),
            "declared_id": manifest.get("id"),
        },
        "software": {
            "git_commit": _git_commit(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "config_sha256": sha256_file(repo_root() / "config" / "layers.yaml"),
            "modules": _module_metadata(),
        },
        "execution": {
            "hostname": socket.gethostname(),
            "slurm": _slurm_metadata(),
        },
        "outputs": {
            "ranking": {
                "path": ranking_path.name,
                "sha256": sha256_file(ranking_path),
                "bytes": ranking_path.stat().st_size,
            }
        },
    }
    manifest_path = output_dir / "output-manifest.json"
    manifest_path.write_text(
        json.dumps(output_manifest, indent=2, sort_keys=True) + "\n"
    )
    return output_manifest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run guarded ANUMA 240 m smoke test")
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate checksums, grid metadata, and array shapes without scoring.",
    )
    args = parser.parse_args(argv)
    try:
        result = run_smoke(args.input_manifest, args.output_dir, args.check_only)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f"precondition failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
