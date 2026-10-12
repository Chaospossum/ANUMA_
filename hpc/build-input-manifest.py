#!/usr/bin/env python3
"""Build a checksum manifest for an already-staged 240 m grid."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from anuma.config import load_config
from anuma.hpc_smoke import EXPECTED_RESOLUTION_M, REQUIRED_FILES, sha256_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--id", required=True, help="Operator-provided immutable dataset ID")
    args = parser.parse_args()

    grid_dir = args.grid_dir.resolve()
    output = args.output.resolve()
    file_names = {
        "grid": "grid.json",
        "ingest_manifest": "ingest-manifest.json",
        "dem": "dem.npy",
        "slope": "slope.npy",
        "illumination": "illumination.npy",
        "hydrogen": "hydrogen.npy",
        "earth_visibility": "earth_visibility.npy",
    }
    entries = {}
    for name in REQUIRED_FILES:
        path = grid_dir / file_names[name]
        if not path.is_file():
            parser.error(f"missing required file: {path}")
        try:
            relative = path.relative_to(output.parent)
        except ValueError:
            parser.error("--output must be in the grid directory or one of its parents")
        entries[name] = {
            "path": relative.as_posix(),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }

    grid_meta = json.loads((grid_dir / "grid.json").read_text())
    if float(grid_meta.get("resolution_m", 0)) != EXPECTED_RESOLUTION_M:
        parser.error("grid.json does not declare a 240 m grid")
    ingest_meta = json.loads((grid_dir / "ingest-manifest.json").read_text())
    if ingest_meta.get("synthetic") is not False:
        parser.error("synthetic ingest output cannot be used for an HPC study")
    expected_revision = load_config()["provenance"]["illumination_affine_revision"]
    if ingest_meta.get("illumination_affine_revision") != expected_revision:
        parser.error("illumination manifest predates the affine fix; re-ingest first")
    manifest = {
        "schema_version": 1,
        "id": args.id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "resolution_m": EXPECTED_RESOLUTION_M,
        "provenance": {
            "illumination_affine_revision": ingest_meta.get(
                "illumination_affine_revision"
            )
        },
        "files": entries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(output)


if __name__ == "__main__":
    main()
