"""Run the blocking 240 m Sun-model validation without submitting a job."""

from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from anuma.grid import Grid
from anuma.hpc_smoke import sha256_file, validate_input_manifest
from anuma.sun import (
    compute_horizons,
    configured_kernel_manifest,
    evaluate_sunlight,
    load_sun_config,
    sun_vectors_from_spice,
    validation_metrics,
    verify_kernel_manifest,
)
from anuma.sun_epochs import (
    assert_frozen_rmse,
    describe_windows,
    gate_epochs,
    load_epoch_manifest,
)


def run_validation(
    input_manifest: Path,
    kernel_manifest: Path,
    epoch_manifest: Path,
    output_dir: Path,
    *,
    workers: int,
) -> dict:
    """Compute horizons, model the declared epochs, and apply the fixed gate."""
    input_meta, files = validate_input_manifest(input_manifest)
    cfg = load_sun_config()
    assert_frozen_rmse(cfg)
    epoch_meta = load_epoch_manifest(epoch_manifest)
    epochs = gate_epochs(epoch_meta)
    windows = describe_windows(epoch_meta)
    sun_cfg = cfg["sun"]
    # Refuse before horizon work. sun_vectors_from_spice verifies again
    # immediately before importing spiceypy.
    verify_kernel_manifest(kernel_manifest, sun_cfg["required_kernel_roles"])
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    grid = Grid.from_json(files["grid"])
    dem = np.load(files["dem"], mmap_mode="r", allow_pickle=False)
    measured = np.load(files["illumination"], mmap_mode="r", allow_pickle=False)
    horizon_cfg = cfg["horizon"]
    scenario_cfg = cfg["scenario"]
    validation_cfg = cfg["validation"]
    started = datetime.now(timezone.utc).isoformat()

    horizons = compute_horizons(
        dem,
        grid.resolution_m,
        moon_radius_m=float(horizon_cfg["moon_radius_m"]),
        azimuth_count=int(horizon_cfg["azimuth_count"]),
        azimuth_origin_deg=float(horizon_cfg["azimuth_origin_deg"]),
        workers=workers,
    )
    lon, lat = grid.lonlat_arrays()
    vectors = sun_vectors_from_spice(
        epochs,
        kernel_manifest,
        required_roles=sun_cfg["required_kernel_roles"],
        frame=str(sun_cfg["frame"]),
        aberration_correction=str(sun_cfg["aberration_correction"]),
    )
    outputs = evaluate_sunlight(
        horizons,
        lon,
        lat,
        vectors,
        cadence_hours=float(epoch_meta["cadence_hours"]),
        disk_radius_deg=float(sun_cfg["disk_radius_deg"]),
        pass_threshold=float(scenario_cfg["lit_threshold_fraction"]),
        azimuth_origin_deg=float(horizon_cfg["azimuth_origin_deg"]),
    )
    metrics = validation_metrics(
        outputs["percent_time_lit"] / 100.0,
        measured,
        stop_rmse=float(validation_cfg["stop_rmse"]),
    )

    paths = {
        "terrain-horizon.npy": horizons,
        "percent-time-lit.npy": outputs["percent_time_lit"],
        "longest-darkness-hours.npy": outputs[
            "longest_continuous_darkness_hours"
        ],
        "passes-80-percent.npy": outputs["passes_80_percent"],
    }
    for name, array in paths.items():
        np.save(output_dir / name, array, allow_pickle=False)

    report = {
        "schema_version": 1,
        "stage": "A-sun-validation-240m",
        "status": "passed" if metrics["passed"] else "failed",
        "started_at_utc": started,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "measured_layer": validation_cfg["measured_layer"],
        "published_temporal_definition": epoch_meta[
            "published_temporal_definition"
        ],
        "epoch_count": len(epochs),
        "gate_window": "primary",
        "windows": windows,
        "metrics": metrics,
        "gate": "stages B-D stop unless passed is true",
        "inputs": {
            "input_manifest": {
                "path": str(input_manifest.resolve()),
                "sha256": sha256_file(input_manifest.resolve()),
                "illumination_affine_revision": input_meta["provenance"][
                    "illumination_affine_revision"
                ],
            },
            "kernel_manifest": {
                "path": str(kernel_manifest.resolve()),
                "sha256": sha256_file(kernel_manifest.resolve()),
            },
            "epoch_manifest": {
                "path": str(epoch_manifest.resolve()),
                "sha256": sha256_file(epoch_manifest.resolve()),
            },
        },
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "execution": {"workers": workers},
    }
    report_path = output_dir / "validation-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    report["outputs"] = {
        path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
        for path in [*(output_dir / name for name in paths), report_path]
    }
    manifest_path = output_dir / "output-manifest.json"
    manifest_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument(
        "--kernel-manifest",
        type=Path,
        default=None,
        help="Defaults to sun.kernel_manifest in config/sun.yaml.",
    )
    parser.add_argument("--epoch-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, required=True)
    args = parser.parse_args(argv)
    kernel_manifest = args.kernel_manifest or configured_kernel_manifest()
    try:
        report = run_validation(
            args.input_manifest,
            kernel_manifest,
            args.epoch_manifest,
            args.output_dir,
            workers=args.workers,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Sun validation precondition failed: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["metrics"]["passed"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
