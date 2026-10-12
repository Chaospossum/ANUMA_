"""Run the blocking 240 m Sun-model validation without submitting a job."""

from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from anuma.grid import Grid
from anuma.hpc_smoke import sha256_file, validate_input_manifest
from anuma.sun import (
    compute_horizons,
    configured_kernel_manifest,
    evaluate_sunlight,
    load_sun_config,
    project_one_node_hours,
    sun_vectors_from_spice,
    validation_metrics,
    verify_kernel_manifest,
)
from anuma.sun_epochs import (
    assert_frozen_rmse,
    describe_windows,
    format_utc,
    gate_epochs,
    hourly_count,
    load_epoch_manifest,
    window_span_seconds,
    window_start,
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
    checkpoint_dir = output_dir / "checkpoint"
    resuming = checkpoint_dir.is_dir() and any(checkpoint_dir.iterdir())
    if output_dir.exists() and any(output_dir.iterdir()) and not resuming:
        raise ValueError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "progress.log"

    grid = Grid.from_json(files["grid"])
    dem = np.load(files["dem"], mmap_mode="r", allow_pickle=False)
    measured = np.load(files["illumination"], mmap_mode="r", allow_pickle=False)
    horizon_cfg = cfg["horizon"]
    scenario_cfg = cfg["scenario"]
    validation_cfg = cfg["validation"]
    execution_cfg = cfg["execution"]
    started = datetime.now(timezone.utc).isoformat()

    horizons = compute_horizons(
        dem,
        grid.resolution_m,
        moon_radius_m=float(horizon_cfg["moon_radius_m"]),
        azimuth_count=int(horizon_cfg["azimuth_count"]),
        azimuth_origin_deg=float(horizon_cfg["azimuth_origin_deg"]),
        workers=workers,
        checkpoint_dir=checkpoint_dir / "horizons",
        log_path=log_path,
    )
    _require_lon0_zero(grid)
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
        workers=workers,
        checkpoint_dir=checkpoint_dir / "sunlight",
        checkpoint_every=int(execution_cfg["checkpoint_every_epochs"]),
        log_path=log_path,
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
        "execution": {
            "workers": workers,
            "checkpoint_every_epochs": int(execution_cfg["checkpoint_every_epochs"]),
        },
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


def _require_lon0_zero(grid: Grid) -> None:
    """The Sun lookup rotates local azimuth by longitude, valid for lon_0 = 0."""
    from pyproj import CRS

    params = CRS.from_proj4(grid.proj4).to_dict()
    if params.get("proj") != "stere" or float(params.get("lat_0", 0.0)) != -90.0:
        raise ValueError("sun validation needs a south polar stereographic grid")
    if float(params.get("lon_0", 0.0)) != 0.0:
        raise ValueError("sun validation assumes lon_0 = 0 for the azimuth rotation")


def _sample_epochs(epoch_meta: dict, count: int) -> tuple[list[str], int]:
    span = window_span_seconds(epoch_meta)
    full_count = hourly_count(span)
    if count <= 0 or count >= full_count:
        raise ValueError("pilot epoch count must be a positive sample of the primary window")
    start = window_start(epoch_meta, "primary")
    epochs = [
        format_utc(start + timedelta(seconds=index * 3600)) for index in range(count)
    ]
    return epochs, full_count


def run_pilot(
    input_manifest: Path,
    kernel_manifest: Path,
    epoch_manifest: Path,
    output_dir: Path,
    *,
    workers: int,
) -> dict:
    """Time a few azimuths and a few full-grid epochs, then project node-hours.

    The pilot does not apply the RMSE gate and does not score illumination.
    """
    input_meta, files = validate_input_manifest(input_manifest)
    cfg = load_sun_config()
    assert_frozen_rmse(cfg)
    epoch_meta = load_epoch_manifest(epoch_manifest)
    sun_cfg = cfg["sun"]
    horizon_cfg = cfg["horizon"]
    pilot_cfg = cfg["pilot"]
    execution_cfg = cfg["execution"]
    verify_kernel_manifest(kernel_manifest, sun_cfg["required_kernel_roles"])
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "progress.log"

    full_azimuths = int(horizon_cfg["azimuth_count"])
    pilot_azimuths = int(pilot_cfg["azimuth_count"])
    pilot_epochs = int(pilot_cfg["epoch_count"])
    if pilot_azimuths <= 0 or pilot_azimuths >= full_azimuths:
        raise ValueError("pilot azimuth count must be a positive sample below 360")
    epochs, full_epochs = _sample_epochs(epoch_meta, pilot_epochs)
    grid = Grid.from_json(files["grid"])
    dem = np.load(files["dem"], mmap_mode="r", allow_pickle=False)
    started = time.perf_counter()
    horizons = compute_horizons(
        dem,
        grid.resolution_m,
        moon_radius_m=float(horizon_cfg["moon_radius_m"]),
        azimuth_count=pilot_azimuths,
        azimuth_origin_deg=float(horizon_cfg["azimuth_origin_deg"]),
        workers=workers,
        checkpoint_dir=output_dir / "checkpoint" / "horizons",
        log_path=log_path,
    )
    horizon_seconds = time.perf_counter() - started
    spice_started = time.perf_counter()
    vectors = sun_vectors_from_spice(
        epochs,
        kernel_manifest,
        required_roles=sun_cfg["required_kernel_roles"],
        frame=str(sun_cfg["frame"]),
        aberration_correction=str(sun_cfg["aberration_correction"]),
    )
    spice_seconds = time.perf_counter() - spice_started
    _require_lon0_zero(grid)
    lon, lat = grid.lonlat_arrays()
    eval_started = time.perf_counter()
    evaluate_sunlight(
        horizons,
        lon,
        lat,
        vectors,
        cadence_hours=float(epoch_meta["cadence_hours"]),
        disk_radius_deg=float(sun_cfg["disk_radius_deg"]),
        azimuth_origin_deg=float(horizon_cfg["azimuth_origin_deg"]),
        workers=workers,
        checkpoint_dir=output_dir / "checkpoint" / "sunlight",
        checkpoint_every=int(execution_cfg["checkpoint_every_epochs"]),
        log_path=log_path,
    )
    eval_seconds = time.perf_counter() - eval_started
    projection = project_one_node_hours(
        horizon_seconds=horizon_seconds,
        pilot_azimuths=pilot_azimuths,
        full_azimuths=full_azimuths,
        horizon_workers=workers,
        eval_seconds=eval_seconds,
        pilot_epochs=pilot_epochs,
        full_epochs=full_epochs,
        spice_seconds=spice_seconds,
        production_node_hours=float(execution_cfg["production_node_hours"]),
        stage_node_hour_cap=float(execution_cfg["stage_node_hour_cap"]),
    )
    report = {
        "schema_version": 1,
        "stage": "A-sun-pilot-240m",
        "status": "measured",
        "gate": "RMSE 0.05 is not applied to this sample and remains unrun",
        "stop_rmse": float(cfg["validation"]["stop_rmse"]),
        "measured_layer": cfg["validation"]["measured_layer"],
        "sample": {
            "azimuth_count": pilot_azimuths,
            "full_azimuth_count": full_azimuths,
            "epoch_count": pilot_epochs,
            "full_epoch_count": full_epochs,
            "grid_shape": list(dem.shape),
            "workers": workers,
        },
        "timings_seconds": {
            "horizon_sample": horizon_seconds,
            "spice_sample": spice_seconds,
            "evaluate_sample": eval_seconds,
        },
        "projection": projection,
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
        "note": (
            "Projected from one measured horizon wave and the sampled epochs. "
            "Not a scored illumination result."
        ),
    }
    path = output_dir / "pilot-measurement.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
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
    parser.add_argument("--mode", choices=("pilot", "production"), default="production")
    args = parser.parse_args(argv)
    kernel_manifest = args.kernel_manifest or configured_kernel_manifest()
    runner = run_pilot if args.mode == "pilot" else run_validation
    try:
        report = runner(
            args.input_manifest,
            kernel_manifest,
            args.epoch_manifest,
            args.output_dir,
            workers=args.workers,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Sun validation precondition failed: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.mode == "pilot":
        if not report["projection"]["fits_production_cap"]:
            raise SystemExit(4)
        return
    if not report["metrics"]["passed"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
