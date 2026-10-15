#!/usr/bin/env bash
# Guarded Stage D submission (docs/stage-d-plan.md). This script is the only
# sbatch path for the DEM-noise job. Running it is a deliberate submission;
# do not start it without a fresh preflight receipt from a cpudev allocation.
#
# This wrapper runs on a login node, so it never reads or hashes a raster
# (plan gate 6). Preflight hashed every input inside the compute allocation
# and pinned size, mtime and digest; here the inputs must still exist with
# the pinned size and mtime, pgda90-sha256.txt must be unchanged, and the
# job verifies every digest again before reading.
set -euo pipefail

mode="${1:-}"
case "${mode}" in
  pilot)
    profile="stage-d-pilot"
    qos="test"
    walltime="00:30:00"
    memory="240G"
    node_hours="0.5"
    core_hours="64.0"
    sbatch_file="stage-d-pilot.sbatch"
    ;;
  production)
    profile="stage-d"
    qos="default"
    # Pilot 0.5 + production 1.5 = 2.0 node-hours, the plan's production cap.
    walltime="01:30:00"
    memory="240G"
    node_hours="1.5"
    core_hours="192.0"
    sbatch_file="stage-d-production.sbatch"
    ;;
  *)
    echo "usage: $0 pilot|production" >&2
    exit 2
    ;;
esac

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${ANUMA_PREFLIGHT_RECEIPT:?set ANUMA_PREFLIGHT_RECEIPT}"
: "${ANUMA_INPUT_MANIFEST:?set ANUMA_INPUT_MANIFEST}"
: "${ANUMA_OUTPUT_ROOT:?set ANUMA_OUTPUT_ROOT}"
ANUMA_ACCOUNT="${ANUMA_ACCOUNT:-p201619}"
ANUMA_PARTITION="${ANUMA_PARTITION:-cpu}"
ANUMA_SCRATCH_DIR="${ANUMA_SCRATCH_DIR:-/project/scratch/p201619}"
ANUMA_DATA_ROOT="${ANUMA_DATA_ROOT:-/project/home/p201619/ANUMA_}"
ANUMA_STAGE_D_RAW_DIR="${ANUMA_STAGE_D_RAW_DIR:-${ANUMA_DATA_ROOT}/data/raw}"
ANUMA_DEM="${ANUMA_DEM:-${ANUMA_DATA_ROOT}/data/raw/LDEM_80S_20MPP_ADJ.TIF}"
ANUMA_STAGE_B_SLOPE="${ANUMA_STAGE_B_SLOPE:-${ANUMA_DATA_ROOT}/out/hpc/stage-b-production-5334425/slope-20m.npy}"
ANUMA_MC_SUMMARY="${ANUMA_MC_SUMMARY:-${ANUMA_DATA_ROOT}/out/hpc/monte-carlo-full-5332991/study-summary.json}"
# The job needs rasterio and scipy: .venv-hpc-ingest on the release/2024.1
# Python module, as Stage B. Login-node checks use the system-Python env.
ANUMA_PYTHON="${ANUMA_PYTHON:-${repo_root}/.venv-hpc-ingest/bin/python}"
ANUMA_CHECK_PYTHON="${ANUMA_CHECK_PYTHON:-${repo_root}/.venv-hpc-sun/bin/python}"
ANUMA_STUDY_MODE="${mode}"
export ANUMA_ACCOUNT ANUMA_PARTITION ANUMA_SCRATCH_DIR ANUMA_PYTHON ANUMA_STUDY_MODE
export ANUMA_DATA_ROOT ANUMA_STAGE_D_RAW_DIR ANUMA_DEM ANUMA_STAGE_B_SLOPE ANUMA_MC_SUMMARY

"${ANUMA_CHECK_PYTHON}" - \
  "${ANUMA_PREFLIGHT_RECEIPT}" "${repo_root}" "${profile}" "${qos}" \
  "${node_hours}" "${core_hours}" "${walltime}" "${memory}" <<'PY'
import hashlib
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, sys.argv[2])
from anuma.stage_d_gate import reject_stage_d_receipt

path = Path(sys.argv[1]).resolve()
repo = Path(sys.argv[2])
profile, qos = sys.argv[3], sys.argv[4]
node_hours, core_hours = float(sys.argv[5]), float(sys.argv[6])
walltime, memory = sys.argv[7], sys.argv[8]
metadata = path.stat()
if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
    raise SystemExit("preflight receipt must be user-owned and mode 0600")
receipt = json.loads(path.read_text())
stored = receipt.pop("receipt_sha256", None)
actual = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
if stored != actual:
    raise SystemExit("preflight receipt digest is invalid")
verified = datetime.fromisoformat(receipt["verified_at_utc"])
if not 0 <= (datetime.now(timezone.utc) - verified).total_seconds() <= 86400:
    raise SystemExit("preflight receipt is expired")
if receipt.get("account") != "p201619":
    raise SystemExit("Stage D requires account p201619")
expected = {
    "account": os.environ["ANUMA_ACCOUNT"],
    "partition": os.environ["ANUMA_PARTITION"],
    "qos": qos,
    "job_profile": profile,
    "input_manifest": str(Path(os.environ["ANUMA_INPUT_MANIFEST"]).resolve()),
    "output_root": str(Path(os.environ["ANUMA_OUTPUT_ROOT"]).resolve()),
    "scratch_dir": str(Path(os.environ["ANUMA_SCRATCH_DIR"]).resolve()),
    "job_walltime": walltime,
    "job_memory": memory,
}
for key, value in expected.items():
    if receipt.get(key) != value:
        raise SystemExit(f"preflight receipt mismatch: {key}")
if (
    float(receipt.get("job_node_hours_cap", -1)) != node_hours
    or float(receipt.get("job_core_hours", -1)) != core_hours
    or int(receipt.get("job_cpus", -1)) != 128
    or receipt.get("accelerators") is not False
):
    raise SystemExit("preflight receipt resource cap mismatch")
manifest = Path(os.environ["ANUMA_INPUT_MANIFEST"])
if hashlib.sha256(manifest.read_bytes()).hexdigest() != receipt["input_manifest_sha256"]:
    raise SystemExit("input manifest changed after preflight")
try:
    reject_stage_d_receipt(
        receipt,
        raw_dir=Path(os.environ["ANUMA_STAGE_D_RAW_DIR"]),
        dem=Path(os.environ["ANUMA_DEM"]),
        stage_b_slope=Path(os.environ["ANUMA_STAGE_B_SLOPE"]),
        mc_summary=Path(os.environ["ANUMA_MC_SUMMARY"]),
        mode=os.environ["ANUMA_STUDY_MODE"],
    )
except ValueError as exc:
    raise SystemExit(f"Stage D receipt rejected: {exc}") from exc
if subprocess.check_output(["git", "-C", repo, "status", "--porcelain"], text=True).strip():
    raise SystemExit("repository changed after preflight")
commit = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()
if commit != receipt["git_commit"]:
    raise SystemExit("Git commit changed after preflight")
PY

test -x "${ANUMA_PYTHON}"
command -v myquota >/dev/null || {
  echo "myquota is required for before/after accounting snapshots" >&2
  exit 2
}

umask 077
quota_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
ANUMA_MYQUOTA_BEFORE="${ANUMA_OUTPUT_ROOT}/myquota-before-stage-d-${mode}-${quota_stamp}-$$.txt"
quota_tmp="${ANUMA_MYQUOTA_BEFORE}.tmp"
{
  printf 'captured_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'command=myquota\n'
  myquota
} > "${quota_tmp}"
mv -- "${quota_tmp}" "${ANUMA_MYQUOTA_BEFORE}"
export ANUMA_MYQUOTA_BEFORE

unset "${!SBATCH_@}"
sbatch \
  --parsable \
  --account="${ANUMA_ACCOUNT}" \
  --partition="${ANUMA_PARTITION}" \
  --qos="${qos}" \
  --nodes=1 \
  --ntasks=1 \
  --cpus-per-task=128 \
  --mem="${memory}" \
  --time="${walltime}" \
  --job-name="anuma-stage-d-${mode}" \
  --output="${ANUMA_OUTPUT_ROOT}/slurm-%x-%j.out" \
  --error="${ANUMA_OUTPUT_ROOT}/slurm-%x-%j.err" \
  --chdir="${repo_root}" \
  --export=ANUMA_PREFLIGHT_RECEIPT,ANUMA_INPUT_MANIFEST,ANUMA_OUTPUT_ROOT,ANUMA_SCRATCH_DIR,ANUMA_PYTHON,ANUMA_MYQUOTA_BEFORE,ANUMA_STUDY_MODE,ANUMA_STAGE_D_RAW_DIR,ANUMA_DEM,ANUMA_STAGE_B_SLOPE,ANUMA_MC_SUMMARY \
  "${repo_root}/hpc/slurm/${sbatch_file}"
