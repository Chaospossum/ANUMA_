"""Read-only presence checks for MeluXina source and derived raster data."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from anuma.config import load_config, repo_root

GRID_FILES = (
    "grid.json",
    "dem.npy",
    "slope.npy",
    "illumination.npy",
    "hydrogen.npy",
    "earth_visibility.npy",
)


def _source_requirements(cfg: dict) -> dict[str, int | None]:
    requirements: dict[str, int | None] = {}
    for layer in ("dem", "illumination", "hydrogen"):
        metadata = cfg["layers"][layer]
        requirements[metadata["filename"]] = metadata.get("expected_bytes")
        for sidecar in metadata.get("sidecars", []):
            requirements[sidecar["filename"]] = sidecar.get("expected_bytes")
    return requirements


def _tracked_data(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--", "data/raw", "data/grid"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"data root is not a readable Git clone: {root}")
    return [line for line in result.stdout.splitlines() if line]


def check_data(root: Path, cfg: dict | None = None) -> dict[str, Any]:
    root = root.resolve()
    cfg = cfg if cfg is not None else load_config(root / "config" / "layers.yaml")
    raw_dir = root / cfg["paths"]["raw"]
    grid_dir = root / cfg["paths"]["grid"]

    sources = {}
    for name, expected_bytes in _source_requirements(cfg).items():
        path = raw_dir / name
        actual_bytes = path.stat().st_size if path.is_file() else None
        sources[name] = {
            "present": actual_bytes is not None,
            "actual_bytes": actual_bytes,
            "expected_bytes": expected_bytes,
            "size_matches": (
                actual_bytes == expected_bytes
                if actual_bytes is not None and expected_bytes is not None
                else None
            ),
        }

    derived = {
        name: {
            "present": (grid_dir / name).is_file(),
            "bytes": (grid_dir / name).stat().st_size
            if (grid_dir / name).is_file()
            else None,
        }
        for name in GRID_FILES
    }
    tracked = _tracked_data(root)
    source_ready = all(
        item["present"] and item["size_matches"] is not False
        for item in sources.values()
    )
    grid_ready = all(item["present"] for item in derived.values())
    return {
        "schema_version": 1,
        "root": str(root),
        "raw_directory": str(raw_dir),
        "grid_directory": str(grid_dir),
        "git_tracked_data_files": tracked,
        "sources": sources,
        "derived": derived,
        "ready": source_ready and grid_ready and not tracked,
        "message": (
            "Source rasters are intentionally absent from Git. Ingest and verify "
            "them on a compute node before scoring."
        ),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repo_root())
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit nonzero unless all source and derived files are present and untracked.",
    )
    args = parser.parse_args(argv)
    try:
        result = check_data(args.root)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f"data check failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.require_ready and not result["ready"]:
        parser.exit(2, "data check failed: ingest and verification are required\n")


if __name__ == "__main__":
    main()
