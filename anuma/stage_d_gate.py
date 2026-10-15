"""Stage D input pins shared by preflight, the submit wrapper and the job.

Standard library only, so the submit wrapper can run it on a login node.
Nothing here hashes a raster: the plan (docs/stage-d-plan.md) allows
reading and hashing the GeoTIFFs and the 3.7 GB Stage B slope only inside
compute allocations, that is preflight on ``cpudev`` and the job itself
(``verify_input_digests``). The login-node pin is size and mtime of each
input plus the digest of the small ``pgda90-sha256.txt`` list.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# Digests fixed before any noise run. Sources:
# - study summary: output-manifest.json of Monte Carlo job 5332991, also in
#   docs/stage-d-plan.md;
# - DEM and Stage B slope: output-manifest.json of Stage B job 5334425;
# - height error: first PGDA product 90 download on MeluXina (2026-10-15).
STUDY_SUMMARY_SHA256 = "4da2a9c336e0ef2b5242afdb1844a536667e46f17cf6fd047c18b794c864f074"
DEM_SHA256 = "09b7ca80f9e6a146f970225d18af72fc02787669b3ef51b888e347d2b6845649"
SLOPE_20M_SHA256 = "08b2e17b98f6c639068493e1f4007194d5cc41ad2d78cb850e593aef9542bd3a"
ERR_SHA256 = "d64045b8aa58a24010f009ab5667ae02cc06cffa7796784dd5e2d48b3059d8cf"

DEM_FILENAME = "LDEM_80S_20MPP_ADJ.TIF"
ERR_FILENAME = "LDEM_80S_20MPP_ADJ_ERR.TIF"
EFFRES_FILENAME = "LDEM_80S_20MPP_ADJ_EFFRES.TIF"
LDSM_ERR_FILENAME = "LDSM_80S_20MPP_ADJ_ERR.TIF"
SHA_LIST_FILENAME = "pgda90-sha256.txt"

# Pilot 0.5 + production 1.5 node-hours = 2.0, inside the plan's 2.0
# node-hour production cap and far below the 50 node-hour Stage D cap.
PROFILES = {"stage-d-pilot": "pilot", "stage-d": "production"}
WALLTIME = {"pilot": "00:30:00", "production": "01:30:00"}
NODE_HOURS = {"pilot": 0.5, "production": 1.5}

_GNU_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+)$")
_BSD_LINE = re.compile(r"^SHA256 \((.+)\) = ([0-9a-fA-F]{64})$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sha256_list(path: Path) -> dict[str, str]:
    """Read a ``sha256sum`` (GNU or BSD style) list, keyed by file basename."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"PGDA digest list is missing: {path}")
    digests: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        gnu = _GNU_LINE.match(line)
        bsd = _BSD_LINE.match(line)
        if gnu:
            digest, name = gnu.group(1), gnu.group(2)
        elif bsd:
            name, digest = bsd.group(1), bsd.group(2)
        else:
            raise ValueError(f"unreadable line in {path}: {line!r}")
        key = Path(name.strip()).name
        if key in digests and digests[key] != digest.lower():
            raise ValueError(f"{path} lists two digests for {key}")
        digests[key] = digest.lower()
    return digests


def expected_inputs(
    *,
    raw_dir: Path,
    dem: Path,
    stage_b_slope: Path,
    mc_summary: Path | None,
) -> dict[str, dict]:
    """Every Stage D input with its expected SHA-256.

    ``mc_summary=None`` leaves the job 5332991 summary out; Stage H
    (``anuma.stage_h_gate``) uses that for its gated noise check, which does
    not read the slope-only summary. Stage D always passes it.

    ERR, the DEM, the Stage B slope and the study summary have fixed digests.
    EFFRES and LDSM_ERR digests come from ``pgda90-sha256.txt`` as written by
    the download job. EFFRES may be absent only if it is also absent from the
    list; the run then uses the plan's 40 m FWHM fallback. A listed but
    missing file, or a present but unlisted one, is refused.
    """
    raw_dir = Path(raw_dir)
    listed = parse_sha256_list(raw_dir / SHA_LIST_FILENAME)
    if ERR_FILENAME in listed and listed[ERR_FILENAME] != ERR_SHA256:
        raise ValueError(f"{SHA_LIST_FILENAME} disagrees with the pinned ERR digest")
    if LDSM_ERR_FILENAME not in listed:
        raise ValueError(f"{SHA_LIST_FILENAME} does not list {LDSM_ERR_FILENAME}")
    inputs = {
        "dem": {"path": Path(dem), "sha256": DEM_SHA256},
        "err": {"path": raw_dir / ERR_FILENAME, "sha256": ERR_SHA256},
        "ldsm_err": {"path": raw_dir / LDSM_ERR_FILENAME, "sha256": listed[LDSM_ERR_FILENAME]},
        "stage_b_slope": {"path": Path(stage_b_slope), "sha256": SLOPE_20M_SHA256},
    }
    if mc_summary is not None:
        inputs["mc_summary"] = {"path": Path(mc_summary), "sha256": STUDY_SUMMARY_SHA256}
    effres = raw_dir / EFFRES_FILENAME
    if EFFRES_FILENAME in listed:
        inputs["effres"] = {"path": effres, "sha256": listed[EFFRES_FILENAME]}
    elif effres.exists():
        raise ValueError(f"{EFFRES_FILENAME} is present but not in {SHA_LIST_FILENAME}")
    for name, entry in inputs.items():
        if not Path(entry["path"]).is_file():
            raise ValueError(f"Stage D input {name} is missing: {entry['path']}")
    return inputs


def receipt_pin(
    *,
    raw_dir: Path,
    dem: Path,
    stage_b_slope: Path,
    mc_summary: Path,
    mode: str,
) -> dict:
    """What a Stage D receipt records. Stats files; hashes only the text list."""
    if mode not in WALLTIME:
        raise ValueError(f"unknown Stage D mode {mode!r}")
    inputs = expected_inputs(
        raw_dir=raw_dir, dem=dem, stage_b_slope=stage_b_slope, mc_summary=mc_summary
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
    sha_list = (Path(raw_dir) / SHA_LIST_FILENAME).resolve()
    return {
        "stage_d_mode": mode,
        "stage_d_inputs": files,
        "stage_d_effres_staged": "effres" in inputs,
        "stage_d_sha_list": str(sha_list),
        "stage_d_sha_list_sha256": sha256_file(sha_list),
        "accelerators": False,
    }


def reject_stage_d_receipt(
    receipt: dict,
    *,
    raw_dir: Path,
    dem: Path,
    stage_b_slope: Path,
    mc_summary: Path,
    mode: str,
) -> None:
    """Refuse a receipt whose inputs, digests or mode drifted since preflight."""
    if receipt.get("account") != "p201619":
        raise ValueError("Stage D requires account p201619")
    if receipt.get("accelerators") is not False:
        raise ValueError("Stage D does not allow accelerators")
    if receipt.get("stage_d_inputs_verified") is not True:
        raise ValueError("preflight did not verify the Stage D input digests")
    pin = receipt_pin(
        raw_dir=raw_dir,
        dem=dem,
        stage_b_slope=stage_b_slope,
        mc_summary=mc_summary,
        mode=mode,
    )
    for key, value in pin.items():
        if receipt.get(key) != value:
            raise ValueError(f"Stage D receipt mismatch: {key}")


def verify_input_digests(inputs: dict[str, dict]) -> dict[str, str]:
    """Hash every input and compare. Compute allocations only (plan gate 6)."""
    actual: dict[str, str] = {}
    for name, entry in sorted(inputs.items()):
        digest = sha256_file(Path(entry["path"]))
        if digest != entry["sha256"]:
            raise ValueError(
                f"checksum mismatch for Stage D input {name}: expected "
                f"{entry['sha256']}, got {digest}"
            )
        actual[name] = digest
    return actual
