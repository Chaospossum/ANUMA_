#!/bin/bash -l
# Stage G longest dark/lit runs. One CPU node, 128 workers, no accelerators.
# 240G is the partition limit of 1920 MB per CPU times 128 CPUs.
# Resource caps are repeated and enforced by submit-darkness.sh.
# Pilot is QOS test and at most 30 minutes. Production is QOS default and
# 03:00; pilot plus production is 3.5 node-hours. Stage G never resumes.
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128

set -euo pipefail
umask 027

: "${ANUMA_PREFLIGHT_RECEIPT:?missing preflight receipt}"
: "${ANUMA_INPUT_MANIFEST:?missing input manifest}"
: "${ANUMA_KERNEL_MANIFEST:?missing kernel manifest}"
: "${ANUMA_STAGE_A_DIR:?missing Stage A output directory}"
: "${ANUMA_HORIZON_CUBE:?missing horizon cube path}"
: "${ANUMA_STAGE_E_DIR:?missing Stage E output directory}"
: "${ANUMA_OUTPUT_ROOT:?missing output root}"
: "${ANUMA_SCRATCH_DIR:?missing scratch directory}"
: "${ANUMA_PYTHON:?missing pinned environment interpreter}"
: "${ANUMA_MYQUOTA_BEFORE:?missing pre-submission myquota snapshot}"
: "${ANUMA_STUDY_MODE:?missing study mode}"
: "${SLURM_JOB_ID:?this script must run under Slurm}"
: "${SLURM_JOB_ACCOUNT:?Slurm did not provide an account}"
: "${SLURM_JOB_PARTITION:?Slurm did not provide a partition}"
: "${SLURM_JOB_QOS:?Slurm did not provide a QOS}"
: "${SLURM_CPUS_PER_TASK:?Slurm did not provide CPUs per task}"
: "${SLURM_JOB_NUM_NODES:?Slurm did not provide node count}"
: "${SLURM_NTASKS:?Slurm did not provide task count}"
: "${SLURM_MEM_PER_NODE:?Slurm did not provide memory per node}"

[[ "${SLURM_JOB_ACCOUNT}" == "p201619" ]] || { echo "account must be p201619" >&2; exit 2; }
[[ "${SLURM_CPUS_PER_TASK}" == "128" ]] || { echo "CPU cap mismatch" >&2; exit 2; }
[[ "${SLURM_JOB_NUM_NODES}" == "1" ]] || { echo "node cap mismatch" >&2; exit 2; }
[[ "${SLURM_NTASKS}" == "1" ]] || { echo "task cap mismatch" >&2; exit 2; }
if [[ -n "${SLURM_JOB_GPUS:-}${SLURM_GPUS:-}${SLURM_GPUS_ON_NODE:-}" ]]; then
  echo "accelerators are not allowed" >&2
  exit 2
fi

case "${ANUMA_STUDY_MODE}" in
  pilot)
    expected_qos="test"
    expected_time="00:30:00"
    expected_memory_mb="245760"
    ;;
  production)
    expected_qos="default"
    expected_time="03:00:00"
    expected_memory_mb="245760"
    ;;
  *)
    echo "invalid study mode" >&2
    exit 2
    ;;
esac
[[ "${SLURM_JOB_QOS}" == "${expected_qos}" ]] || { echo "QOS mismatch" >&2; exit 2; }
[[ "${SLURM_MEM_PER_NODE}" == "${expected_memory_mb}" ]] || {
  echo "memory cap mismatch" >&2
  exit 2
}
job_record="$(scontrol show job "${SLURM_JOB_ID}" --oneliner)"
[[ "${job_record}" == *"TimeLimit=${expected_time}"* ]] || {
  echo "walltime cap mismatch" >&2
  exit 2
}

repo_root="${SLURM_SUBMIT_DIR:?Slurm submit directory is unavailable}"
output_dir="${ANUMA_OUTPUT_ROOT}/darkness-${ANUMA_STUDY_MODE}-${SLURM_JOB_ID}"
job_tmp="${ANUMA_SCRATCH_DIR}/anuma-darkness-${SLURM_JOB_ID}"

finish() {
  status=$?
  trap - EXIT
  set +e
  mkdir -p -- "${output_dir}"
  cp -- "${ANUMA_MYQUOTA_BEFORE}" "${output_dir}/myquota-before-${SLURM_JOB_ID}.txt"
  if [[ -f "${job_tmp}/module-list.txt" ]]; then
    cp -- "${job_tmp}/module-list.txt" "${output_dir}/module-list-${SLURM_JOB_ID}.txt"
  fi
  {
    printf 'captured_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'command=myquota\n'
    myquota
    printf 'myquota_exit_code=%s\n' "$?"
  } > "${output_dir}/myquota-after-${SLURM_JOB_ID}.txt"
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
# .venv-hpc-sun ran Stage A job 5334265 on OS Python 3.12 under the default
# release modules, with no Python module loaded. Set ANUMA_LOAD_PYTHON_MODULE=1
# only for an interpreter built on the release/2024.1 Python module
# (as .venv-hpc-ingest was; see job 5334407).
if [[ "${ANUMA_LOAD_PYTHON_MODULE:-0}" == "1" ]]; then
  module load env/release/2024.1
  module load Python/3.12.3-GCCcore-13.3.0
fi
module -t list > "${job_tmp}/module-list.txt" 2>&1
if grep -q 'env/staging/2025\.1' "${job_tmp}/module-list.txt"; then
  echo "Stage G requires stable release modules, not staging" >&2
  exit 2
fi
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

"${ANUMA_PYTHON}" - "${ANUMA_PREFLIGHT_RECEIPT}" "${repo_root}" <<'PY'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, sys.argv[2])
from anuma.darkness import reject_darkness_receipt

path = Path(sys.argv[1])
receipt = json.loads(path.read_text())
stored = receipt.pop("receipt_sha256", None)
actual = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
if stored != actual:
    raise SystemExit("preflight receipt digest is invalid")
verified = datetime.fromisoformat(receipt["verified_at_utc"])
if not 0 <= (datetime.now(timezone.utc) - verified).total_seconds() <= 86400:
    raise SystemExit("preflight receipt is expired")
if receipt.get("account") != os.environ["SLURM_JOB_ACCOUNT"]:
    raise SystemExit("preflight receipt account mismatch")
try:
    reject_darkness_receipt(
        receipt,
        kernel_manifest=Path(os.environ["ANUMA_KERNEL_MANIFEST"]),
        stage_a_dir=Path(os.environ["ANUMA_STAGE_A_DIR"]),
        stage_e_dir=Path(os.environ["ANUMA_STAGE_E_DIR"]),
        horizon_cube=Path(os.environ["ANUMA_HORIZON_CUBE"]),
        mode=os.environ["ANUMA_STUDY_MODE"],
    )
except ValueError as exc:
    raise SystemExit(f"Stage G receipt rejected: {exc}") from exc
PY

test -x "${ANUMA_PYTHON}"
test -d "${ANUMA_OUTPUT_ROOT}"
test -w "${ANUMA_OUTPUT_ROOT}"
test -d "${ANUMA_SCRATCH_DIR}"
test -w "${ANUMA_SCRATCH_DIR}"
test -f "${ANUMA_HORIZON_CUBE}"
test -d "${ANUMA_STAGE_E_DIR}"
test ! -e "${output_dir}"

cd "${repo_root}"
srun --export=ALL --ntasks=1 --cpus-per-task=128 \
  "${ANUMA_PYTHON}" -m anuma.darkness \
  --mode "${ANUMA_STUDY_MODE}" \
  --input-manifest "${ANUMA_INPUT_MANIFEST}" \
  --kernel-manifest "${ANUMA_KERNEL_MANIFEST}" \
  --stage-a-dir "${ANUMA_STAGE_A_DIR}" \
  --stage-e-dir "${ANUMA_STAGE_E_DIR}" \
  --horizon-cube "${ANUMA_HORIZON_CUBE}" \
  --output-dir "${output_dir}" \
  --workers 128
