#!/usr/bin/env bash
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
: "${SLURM_JOB_ID:?this script must run under Slurm}"
: "${SLURM_JOB_ACCOUNT:?Slurm did not provide an account}"
: "${SLURM_JOB_PARTITION:?Slurm did not provide a partition}"

repo_root="${SLURM_SUBMIT_DIR:?Slurm submit directory is unavailable}"
output_dir="${ANUMA_OUTPUT_ROOT}/smoke-240m-${SLURM_JOB_ID}"
job_tmp="${ANUMA_SCRATCH_DIR}/anuma-smoke-${SLURM_JOB_ID}"

cleanup() {
  rm -rf -- "${job_tmp}"
}
trap cleanup EXIT
mkdir -p -- "${job_tmp}"
export TMPDIR="${job_tmp}"
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-4}"
export OPENBLAS_NUM_THREADS="${SLURM_CPUS_PER_TASK:-4}"
export MKL_NUM_THREADS="${SLURM_CPUS_PER_TASK:-4}"

"${ANUMA_PYTHON}" - "${ANUMA_PREFLIGHT_RECEIPT}" <<'PY'
import hashlib
import json
import os
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
    "input_manifest": str(Path(os.environ["ANUMA_INPUT_MANIFEST"]).resolve()),
    "output_root": str(Path(os.environ["ANUMA_OUTPUT_ROOT"]).resolve()),
    "scratch_dir": str(Path(os.environ["ANUMA_SCRATCH_DIR"]).resolve()),
}
for key, expected in checks.items():
    if receipt.get(key) != expected:
        raise SystemExit(f"preflight receipt mismatch: {key}")
PY

test -x "${ANUMA_PYTHON}"
test -d "${ANUMA_OUTPUT_ROOT}"
test -w "${ANUMA_OUTPUT_ROOT}"
test -d "${ANUMA_SCRATCH_DIR}"
test -w "${ANUMA_SCRATCH_DIR}"
test ! -e "${output_dir}"

cd "${repo_root}"
srun --ntasks=1 --cpus-per-task="${SLURM_CPUS_PER_TASK:-4}" \
  "${ANUMA_PYTHON}" -m anuma.hpc_smoke \
  --input-manifest "${ANUMA_INPUT_MANIFEST}" \
  --output-dir "${output_dir}"
