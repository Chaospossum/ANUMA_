"""Stage H input pins shared by preflight, the submit wrapper and the job.

Standard library only, so the submit wrapper can run it on a login node
(``.venv-hpc-sun`` has no numpy-dependent extras it needs here). Nothing here
hashes a raster on its own: the 20 m GeoTIFFs and the 3.7 GB Stage B slope
are hashed only inside compute allocations (preflight on ``cpudev`` and the
job, ``verify_input_digests``), as for Stages D and F. The login-node pin is
size and mtime of each input.

Stage H (docs/stage-h-plan.md) reruns the two robustness studies for the
ranking with the Stage F 20 m terrain gate applied:

- the 500-draw weight Monte Carlo of job 5332991 on the same 240 m arrays
  (input manifest pinned below), with the gate layer of Stage F job 5345446;
- the Stage D DEM-noise check on the gated default top 12.

The gate layer is not part of the 240 m input manifest. It is pinned by the
sha256 of ``terrain_1km_fraction.npy`` and of its sidecar, both of which the
Stage F output manifest lists, and is read in place from the Stage F output
directory on MeluXina; nothing is copied or restaged.
"""

from __future__ import annotations

import json
from pathlib import Path

from anuma import stage_d_gate
from anuma.stage_d_gate import sha256_file

# Pins fixed in docs/stage-h-plan.md before any Stage H run. Sources:
# - input manifest: output-manifest.json of Monte Carlo job 5332991 (the
#   same digest Stage D job 5345050 recorded);
# - gate layer, sidecar and Stage F manifest: Stage F job 5345446;
# - reference draws: output-manifest.json of job 5332991.
INPUT_MANIFEST_SHA256 = "54ba6d76277808685fac7887cdbcd70728ecb0bad7027909a20d001460cdb0d9"
GATE_JOB = "5345446"
GATE_FRACTION_SHA256 = "7902c9b98106a5952b520966c7058ab0133729871c4912819418351659b446a9"
GATE_SIDECAR_SHA256 = "335a41dd17946fe4daf5693c66110e6cb7960c70829d29e5eee19d9b7c2dbbe8"
STAGE_F_MANIFEST_SHA256 = "03a04bab651432dfa830d08da34e6c78638dbfff37733ebac61ac58de0930084"
REFERENCE_DRAWS_SHA256 = "05784ca639f4ce6d2159da427e45e9e53445e6fef5a46425fc4c3d29bbdcd608"
RANKING_BASIS = "terrain_gate_8deg_1km_50pct"

GATE_LAYER = "terrain_1km_fraction"
GATE_FRACTION_FILENAME = f"{GATE_LAYER}.npy"
GATE_SIDECAR_FILENAME = f"{GATE_LAYER}.json"
STAGE_F_MANIFEST_FILENAME = "output-manifest.json"
DEFAULT_STAGE_F_DIR = Path("out") / "hpc" / f"stage-f-production-{GATE_JOB}"
DEFAULT_REFERENCE_DRAWS = Path("out") / "hpc" / "monte-carlo-full-5332991" / "draws.jsonl"

# Pilot 0.5 + production 1.0 = 1.5 node-hours, the Stage H cap.
PROFILES = {"stage-h-pilot": "pilot", "stage-h": "production"}
WALLTIME = {"pilot": "00:30:00", "production": "01:00:00"}
NODE_HOURS = {"pilot": 0.5, "production": 1.0}
STAGE_CAP_NODE_HOURS = 1.5


def expected_inputs(
    *,
    raw_dir: Path,
    dem: Path,
    stage_b_slope: Path,
    stage_f_dir: Path,
    reference_draws: Path,
    input_manifest: Path,
) -> dict[str, dict]:
    """Every Stage H input with its pinned SHA-256.

    The noise inputs are exactly Stage D's (DEM, ERR, EFFRES, LDSM_ERR and the
    Stage B slope, with the same PGDA digest list), without the slope-only
    Monte Carlo summary.
    """
    inputs = stage_d_gate.expected_inputs(
        raw_dir=raw_dir, dem=dem, stage_b_slope=stage_b_slope, mc_summary=None
    )
    stage_f_dir = Path(stage_f_dir)
    inputs.update(
        {
            "gate_fraction": {
                "path": stage_f_dir / GATE_FRACTION_FILENAME,
                "sha256": GATE_FRACTION_SHA256,
            },
            "gate_sidecar": {
                "path": stage_f_dir / GATE_SIDECAR_FILENAME,
                "sha256": GATE_SIDECAR_SHA256,
            },
            "stage_f_manifest": {
                "path": stage_f_dir / STAGE_F_MANIFEST_FILENAME,
                "sha256": STAGE_F_MANIFEST_SHA256,
            },
            "reference_draws": {"path": Path(reference_draws), "sha256": REFERENCE_DRAWS_SHA256},
            "input_manifest": {"path": Path(input_manifest), "sha256": INPUT_MANIFEST_SHA256},
        }
    )
    for name, entry in inputs.items():
        if not Path(entry["path"]).is_file():
            raise ValueError(f"Stage H input {name} is missing: {entry['path']}")
    return inputs


def check_gate_records(sidecar_path: Path, stage_f_manifest_path: Path) -> dict:
    """The Stage F records must name the pinned layer. Reads two small JSONs.

    Refuses a sidecar that is not the full-cap, 8°, 1 km layer of job
    5345446 with the pinned fraction digest, and a Stage F manifest that did
    not pass or does not list the pinned layer and sidecar digests.
    """
    meta = json.loads(Path(sidecar_path).read_text())
    manifest = json.loads(Path(stage_f_manifest_path).read_text())
    problems = []
    if meta.get("layer") != GATE_LAYER:
        problems.append("sidecar is not for terrain_1km_fraction")
    if meta.get("coverage") != "full_cap":
        problems.append("gate layer is not full_cap")
    if str(meta.get("job")) != GATE_JOB:
        problems.append(f"gate layer job is {meta.get('job')!r}, not {GATE_JOB}")
    if meta.get("fraction_sha256") != GATE_FRACTION_SHA256:
        problems.append("sidecar fraction_sha256 is not the pinned layer")
    if meta.get("max_slope_deg") != 8.0 or meta.get("radius_m") != 1000.0:
        problems.append("gate layer is not the 8 deg / 1 km layer")
    if manifest.get("passed") is not True:
        problems.append("Stage F output manifest does not record passed=true")
    outputs = manifest.get("outputs") or {}
    if (outputs.get(GATE_FRACTION_FILENAME) or {}).get("sha256") != GATE_FRACTION_SHA256:
        problems.append("Stage F output manifest does not list the pinned layer digest")
    if (outputs.get(GATE_SIDECAR_FILENAME) or {}).get("sha256") != GATE_SIDECAR_SHA256:
        problems.append("Stage F output manifest does not list the pinned sidecar digest")
    if problems:
        raise ValueError("; ".join(problems))
    return {
        "job": GATE_JOB,
        "fraction_sha256": GATE_FRACTION_SHA256,
        "sidecar_sha256": GATE_SIDECAR_SHA256,
        "max_slope_deg": float(meta["max_slope_deg"]),
        "radius_m": float(meta["radius_m"]),
        "min_fraction_at_build": meta.get("gate_min_fraction_at_build"),
    }


def receipt_pin(
    *,
    raw_dir: Path,
    dem: Path,
    stage_b_slope: Path,
    stage_f_dir: Path,
    reference_draws: Path,
    input_manifest: Path,
    mode: str,
) -> dict:
    """What a Stage H receipt records. Stats files; hashes only small text files."""
    if mode not in WALLTIME:
        raise ValueError(f"unknown Stage H mode {mode!r}")
    inputs = expected_inputs(
        raw_dir=raw_dir,
        dem=dem,
        stage_b_slope=stage_b_slope,
        stage_f_dir=stage_f_dir,
        reference_draws=reference_draws,
        input_manifest=input_manifest,
    )
    files = {}
    for name, entry in sorted(inputs.items()):
        path = Path(entry["path"]).resolve()
        stat = path.stat()
        files[name] = {
            "path": str(path),
            "sha256": entry["sha256"],
            "bytes": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }
    sha_list = (Path(raw_dir) / stage_d_gate.SHA_LIST_FILENAME).resolve()
    return {
        "stage_h_mode": mode,
        "stage_h_inputs": files,
        "stage_h_effres_staged": "effres" in inputs,
        "stage_h_sha_list": str(sha_list),
        "stage_h_sha_list_sha256": sha256_file(sha_list),
        "stage_h_ranking_basis": RANKING_BASIS,
        "accelerators": False,
    }


def reject_stage_h_receipt(receipt: dict, *, mode: str, **paths) -> None:
    """Refuse a receipt whose inputs, digests or mode drifted since preflight."""
    if receipt.get("account") != "p201619":
        raise ValueError("Stage H requires account p201619")
    if receipt.get("accelerators") is not False:
        raise ValueError("Stage H does not allow accelerators")
    if receipt.get("stage_h_inputs_verified") is not True:
        raise ValueError("preflight did not verify the Stage H input digests")
    pin = receipt_pin(**paths, mode=mode)
    for key, value in pin.items():
        if receipt.get(key) != value:
            raise ValueError(f"Stage H receipt mismatch: {key}")


def verify_input_digests(inputs: dict[str, dict]) -> dict[str, str]:
    """Hash every input and compare. Compute allocations only."""
    actual: dict[str, str] = {}
    for name, entry in sorted(inputs.items()):
        digest = sha256_file(Path(entry["path"]))
        if digest != entry["sha256"]:
            raise ValueError(
                f"checksum mismatch for Stage H input {name}: expected "
                f"{entry['sha256']}, got {digest}"
            )
        actual[name] = digest
    return actual
