#!/usr/bin/env bash
# Guarded Stage B submission. This script is the only sbatch path for the
# 20 m slope/ranking job. Running it is a deliberate submission; do not start
# it from a login node without a fresh preflight receipt.
set -euo pipefail

mode="${1:-}"
case "${mode}" in
  pilot)
    profile="stage-b-pilot"
    qos="test"
    walltime="00:30:00"
    memory="240G"
    node_hours="0.5"
    core_hours="64.0"
    ;;
  production)
    profile="stage-b"
    qos="default"
    # Sized from the laptop tile benchmark in docs/meluxina-execution.md.
    walltime="06:00:00"
    memory="240G"
    node_hours="6.0"
    core_hours="768.0"
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
ANUMA_DEM="${ANUMA_DEM:-${repo_root}/data/raw/LDEM_80S_20MPP_ADJ.TIF}"
ANUMA_STAGE_A_ACCEPTANCE="${ANUMA_STAGE_A_ACCEPTANCE:-${repo_root}/out/sun-validation/validation-report.json}"
ANUMA_PYTHON="${ANUMA_PYTHON:-${repo_root}/.venv-hpc-ingest/bin/python}"
ANUMA_STUDY_MODE="${mode}"
export ANUMA_ACCOUNT ANUMA_PARTITION ANUMA_SCRATCH_DIR ANUMA_PYTHON ANUMA_STUDY_MODE
export ANUMA_DEM ANUMA_STAGE_A_ACCEPTANCE

"${ANUMA_PYTHON}" - \
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
from anuma.stage_b import FROZEN_STOP_RMSE, require_stage_a_acceptance

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
    raise SystemExit("Stage B requires account p201619")
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
dem = Path(os.environ["ANUMA_DEM"])
if not dem.is_file():
    raise SystemExit(f"20 m DEM is missing: {dem}")
if str(dem.resolve()) != str(Path(receipt.get("dem_path", "")).resolve()):
    raise SystemExit("20 m DEM path differs from the preflight receipt")
digest = hashlib.sha256()
with dem.open("rb") as handle:
    for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
        digest.update(chunk)
if digest.hexdigest() != receipt.get("dem_sha256"):
    raise SystemExit("20 m DEM changed after preflight")
if profile == "stage-b":
    try:
        require_stage_a_acceptance(Path(os.environ["ANUMA_STAGE_A_ACCEPTANCE"]))
    except ValueError as exc:
        raise SystemExit(f"Stage A gate refused: {exc}") from exc
    if float(receipt.get("stage_a_stop_rmse", -1)) != FROZEN_STOP_RMSE:
        raise SystemExit("preflight receipt is missing the Stage A RMSE pin")
    if receipt.get("stage_a_status") != "passed":
        raise SystemExit("preflight receipt Stage A status is not passed")
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
ANUMA_MYQUOTA_BEFORE="${ANUMA_OUTPUT_ROOT}/myquota-before-stage-b-${mode}-${quota_stamp}-$$.txt"
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
  --job-name="anuma-stage-b-${mode}" \
  --output="${ANUMA_OUTPUT_ROOT}/slurm-%x-%j.out" \
  --error="${ANUMA_OUTPUT_ROOT}/slurm-%x-%j.err" \
  --chdir="${repo_root}" \
  --export=ANUMA_PREFLIGHT_RECEIPT,ANUMA_INPUT_MANIFEST,ANUMA_OUTPUT_ROOT,ANUMA_SCRATCH_DIR,ANUMA_PYTHON,ANUMA_MYQUOTA_BEFORE,ANUMA_STUDY_MODE,ANUMA_DEM,ANUMA_STAGE_A_ACCEPTANCE \
  "${repo_root}/hpc/slurm/stage-b.sbatch"
