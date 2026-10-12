#!/bin/bash -l
#SBATCH --job-name=anuma-240m-smoke
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:30:00
#SBATCH --output=slurm-%x-%j.out
#SBATCH --error=slurm-%x-%j.err

set -euo pipefail
umask 027

: "${ANUMA_PREFLIGHT_RECEIPT:?missing preflight receipt}"
: "${ANUMA_INPUT_MANIFEST:?missing input manifest}"
: "${ANUMA_OUTPUT_ROOT:?missing output root}"
: "${ANUMA_SCRATCH_DIR:?missing scratch directory}"
: "${ANUMA_PYTHON:?missing pinned environment interpreter}"
: "${ANUMA_MYQUOTA_BEFORE:?missing pre-submission myquota snapshot}"
: "${SLURM_JOB_ID:?this script must run under Slurm}"
: "${SLURM_JOB_ACCOUNT:?Slurm did not provide an account}"
: "${SLURM_JOB_PARTITION:?Slurm did not provide a partition}"
: "${SLURM_JOB_QOS:?Slurm did not provide a QOS}"
: "${SLURM_CPUS_PER_TASK:?Slurm did not provide CPUs per task}"
: "${SLURM_JOB_NUM_NODES:?Slurm did not provide node count}"
: "${SLURM_NTASKS:?Slurm did not provide task count}"

[[ "${SLURM_CPUS_PER_TASK}" == "4" ]] || { echo "CPU cap mismatch" >&2; exit 2; }
[[ "${SLURM_JOB_NUM_NODES}" == "1" ]] || { echo "node cap mismatch" >&2; exit 2; }
[[ "${SLURM_NTASKS}" == "1" ]] || { echo "task cap mismatch" >&2; exit 2; }
job_record="$(scontrol show job "${SLURM_JOB_ID}" --oneliner)"
[[ "${job_record}" == *"TimeLimit=00:30:00"* ]] || {
  echo "walltime cap mismatch" >&2
  exit 2
}

repo_root="${SLURM_SUBMIT_DIR:?Slurm submit directory is unavailable}"
output_dir="${ANUMA_OUTPUT_ROOT}/smoke-240m-${SLURM_JOB_ID}"
job_tmp="${ANUMA_SCRATCH_DIR}/anuma-smoke-${SLURM_JOB_ID}"

finish() {
  status=$?
  trap - EXIT
  set +e
  mkdir -p -- "${output_dir}"
  cp -- "${ANUMA_MYQUOTA_BEFORE}" "${output_dir}/myquota-before.txt"
  if [[ -f "${job_tmp}/module-list.txt" ]]; then
    cp -- "${job_tmp}/module-list.txt" "${output_dir}/module-list.txt"
  fi
  {
    printf 'captured_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'command=myquota\n'
    myquota
    printf 'myquota_exit_code=%s\n' "$?"
  } > "${output_dir}/myquota-after.txt"
  rm -rf -- "${job_tmp}"
  exit "${status}"
}
trap finish EXIT
mkdir -p -- "${job_tmp}"
export TMPDIR="${job_tmp}"
command -v module >/dev/null 2>&1 || {
  echo "Environment Modules is unavailable on the compute node" >&2
  exit 2
}
module -t list > "${job_tmp}/module-list.txt" 2>&1
if grep -q 'env/staging/2025\.1' "${job_tmp}/module-list.txt"; then
  echo "Smoke job requires stable env/release/2025.1 modules, not staging" >&2
  exit 2
fi
export ANUMA_MODULE_LIST_FILE="${job_tmp}/module-list.txt"
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-4}"
export OPENBLAS_NUM_THREADS="${SLURM_CPUS_PER_TASK:-4}"
export MKL_NUM_THREADS="${SLURM_CPUS_PER_TASK:-4}"

"${ANUMA_PYTHON}" - "${ANUMA_PREFLIGHT_RECEIPT}" <<'PY'
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
receipt = json.loads(path.read_text())
stored = receipt.pop("receipt_sha256", None)
actual = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
if stored != actual:
    raise SystemExit("preflight receipt digest is invalid")
verified = datetime.fromisoformat(receipt["verified_at_utc"])
if not 0 <= (datetime.now(timezone.utc) - verified).total_seconds() <= 86400:
    raise SystemExit("preflight receipt is expired")
checks = {
    "account": os.environ["SLURM_JOB_ACCOUNT"],
    "partition": os.environ["SLURM_JOB_PARTITION"],
    "qos": os.environ["SLURM_JOB_QOS"],
    "input_manifest": str(Path(os.environ["ANUMA_INPUT_MANIFEST"]).resolve()),
    "output_root": str(Path(os.environ["ANUMA_OUTPUT_ROOT"]).resolve()),
    "scratch_dir": str(Path(os.environ["ANUMA_SCRATCH_DIR"]).resolve()),
}
for key, expected in checks.items():
    if receipt.get(key) != expected:
        raise SystemExit(f"preflight receipt mismatch: {key}")
manifest = Path(os.environ["ANUMA_INPUT_MANIFEST"])
if hashlib.sha256(manifest.read_bytes()).hexdigest() != receipt["input_manifest_sha256"]:
    raise SystemExit("input manifest changed after preflight")
repo = Path(os.environ["SLURM_SUBMIT_DIR"])
if subprocess.check_output(["git", "-C", repo, "status", "--porcelain"], text=True).strip():
    raise SystemExit("repository changed after preflight")
commit = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()
if commit != receipt["git_commit"]:
    raise SystemExit("Git commit changed after preflight")
PY

test -x "${ANUMA_PYTHON}"
test -d "${ANUMA_OUTPUT_ROOT}"
test -w "${ANUMA_OUTPUT_ROOT}"
test -d "${ANUMA_SCRATCH_DIR}"
test -w "${ANUMA_SCRATCH_DIR}"
test ! -e "${output_dir}"

cd "${repo_root}"
srun --ntasks=1 --cpus-per-task=4 \
  "${ANUMA_PYTHON}" -m anuma.hpc_smoke \
  --input-manifest "${ANUMA_INPUT_MANIFEST}" \
  --output-dir "${output_dir}"
