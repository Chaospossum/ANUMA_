"""Stage F input pins shared by preflight, the submit wrapper and the job.

Standard library only, so the submit wrapper can run it on a login node.
Nothing here hashes a raster on its own: reading and hashing the 20 m DEM and
the 3.7 GB Stage B slope happen only inside compute allocations (preflight
on ``cpudev`` and the job itself, ``verify_input_digests``), as for Stage D.
The login-node pin is size and mtime of each input.
"""

from __future__ import annotations

from pathlib import Path

from anuma.stage_d_gate import DEM_SHA256, SLOPE_20M_SHA256, sha256_file

__all__ = [
    "DEM_SHA256",
    "SLOPE_20M_SHA256",
    "PROFILES",
    "WALLTIME",
    "NODE_HOURS",
    "expected_inputs",
    "receipt_pin",
    "reject_stage_f_receipt",
    "sha256_file",
    "verify_input_digests",
]

# Pilot 0.5 + production 1.0 = 1.5 node-hours, inside the 2.0 node-hour
# Stage F cap of docs/stage-f-plan.md.
PROFILES = {"stage-f-pilot": "pilot", "stage-f": "production"}
WALLTIME = {"pilot": "00:30:00", "production": "01:00:00"}
NODE_HOURS = {"pilot": 0.5, "production": 1.0}


def expected_inputs(*, dem: Path, stage_b_slope: Path) -> dict[str, dict]:
    """Every Stage F input with its pinned SHA-256 (Stage B job 5334425)."""
    inputs = {
        "dem": {"path": Path(dem), "sha256": DEM_SHA256},
        "stage_b_slope": {"path": Path(stage_b_slope), "sha256": SLOPE_20M_SHA256},
    }
    for name, entry in inputs.items():
        if not Path(entry["path"]).is_file():
            raise ValueError(f"Stage F input {name} is missing: {entry['path']}")
    return inputs


def receipt_pin(*, dem: Path, stage_b_slope: Path, mode: str) -> dict:
    """What a Stage F receipt records. Stats files only; hashes nothing."""
    if mode not in WALLTIME:
        raise ValueError(f"unknown Stage F mode {mode!r}")
    files = {}
    for name, entry in sorted(expected_inputs(dem=dem, stage_b_slope=stage_b_slope).items()):
        path = Path(entry["path"]).resolve()
        stat = path.stat()
        files[name] = {
            "path": str(path),
            "sha256": entry["sha256"],
            "bytes": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }
    return {"stage_f_mode": mode, "stage_f_inputs": files, "accelerators": False}


def reject_stage_f_receipt(receipt: dict, *, dem: Path, stage_b_slope: Path, mode: str) -> None:
    """Refuse a receipt whose inputs or mode drifted since preflight."""
    if receipt.get("account") != "p201619":
        raise ValueError("Stage F requires account p201619")
    if receipt.get("accelerators") is not False:
        raise ValueError("Stage F does not allow accelerators")
    if receipt.get("stage_f_inputs_verified") is not True:
        raise ValueError("preflight did not verify the Stage F input digests")
    pin = receipt_pin(dem=dem, stage_b_slope=stage_b_slope, mode=mode)
    for key, value in pin.items():
        if receipt.get(key) != value:
            raise ValueError(f"Stage F receipt mismatch: {key}")


def verify_input_digests(inputs: dict[str, dict]) -> dict[str, str]:
    """Hash every input and compare. Compute allocations only."""
    actual: dict[str, str] = {}
    for name, entry in sorted(inputs.items()):
        digest = sha256_file(Path(entry["path"]))
        if digest != entry["sha256"]:
            raise ValueError(
                f"checksum mismatch for Stage F input {name}: expected "
                f"{entry['sha256']}, got {digest}"
            )
        actual[name] = digest
    return actual
