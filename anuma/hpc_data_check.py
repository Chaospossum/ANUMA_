"""Read-only presence checks for MeluXina source and derived raster data."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from anuma.config import load_config, repo_root

GRID_FILES = (
    "grid.json",
    "ingest-manifest.json",
    "dem.npy",
    "slope.npy",
    "illumination.npy",
    "hydrogen.npy",
    "earth_visibility.npy",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_requirements(cfg: dict) -> dict[str, dict[str, Any]]:
    requirements: dict[str, dict[str, Any]] = {}
    for layer in ("dem", "illumination", "hydrogen"):
        metadata = cfg["layers"][layer]
        requirements[metadata["filename"]] = {
            "expected_bytes": metadata.get("expected_bytes"),
            "expected_sha256": metadata.get("sha256"),
        }
        for sidecar in metadata.get("sidecars", []):
            requirements[sidecar["filename"]] = {
                "expected_bytes": sidecar.get("expected_bytes"),
                "expected_sha256": sidecar.get("sha256"),
            }
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
    for name, requirement in _source_requirements(cfg).items():
        path = raw_dir / name
        actual_bytes = path.stat().st_size if path.is_file() else None
        actual_sha256 = _sha256(path) if path.is_file() else None
        expected_bytes = requirement["expected_bytes"]
        expected_sha256 = requirement["expected_sha256"]
        sources[name] = {
            "present": actual_bytes is not None,
            "actual_bytes": actual_bytes,
            "expected_bytes": expected_bytes,
            "actual_sha256": actual_sha256,
            "expected_sha256": expected_sha256,
            "size_matches": (
                actual_bytes == expected_bytes
                if actual_bytes is not None and expected_bytes is not None
                else None
            ),
            "hash_matches": (
                actual_sha256 == expected_sha256
                if actual_sha256 is not None and expected_sha256 is not None
                else None
            ),
        }

    derived = {}
    for name in GRID_FILES:
        path = grid_dir / name
        derived[name] = {
            "present": path.is_file(),
            "bytes": path.stat().st_size if path.is_file() else None,
            "sha256": _sha256(path) if path.is_file() else None,
        }
    tracked = _tracked_data(root)
    source_ready = all(
        item["present"]
        and item["size_matches"] is not False
        and item["hash_matches"] is not False
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
        "raw_ready": source_ready and not tracked,
        "grid_ready": grid_ready and not tracked,
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
        "--require-raw-ready",
        action="store_true",
        help="Exit nonzero unless existing source rasters pass size/hash checks.",
    )
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit nonzero unless all source and derived files are present and untracked.",
    )
    parser.add_argument(
        "--manifest-output",
        type=Path,
        help="Write the checksummed source/derived presence report to this path.",
    )
    args = parser.parse_args(argv)
    try:
        result = check_data(args.root)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f"data check failed: {exc}\n")
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.manifest_output:
        args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_output.write_text(rendered)
    if args.require_raw_ready and not result["raw_ready"]:
        parser.exit(
            2,
            "data check failed: existing raw sources are missing or invalid\n",
        )
    if args.require_ready and not result["ready"]:
        parser.exit(2, "data check failed: ingest and verification are required\n")


if __name__ == "__main__":
    main()
