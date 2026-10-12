#!/bin/bash -l
# One CPU node, 128 workers, no accelerators. 240G is the partition limit
# of 1920 MB per CPU times 128 CPUs; a node has 480 GB usable, not 512.
# Resource caps are repeated and enforced by submit-stage-b.sh.
# Pilot is QOS test and at most 30 minutes. Production is 06:00 so the
# pilot plus production stays well under the 40 node-hour Stage B cap.
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128

set -euo pipefail
umask 027

: "${ANUMA_PREFLIGHT_RECEIPT:?missing preflight receipt}"
: "${ANUMA_INPUT_MANIFEST:?missing input manifest}"
: "${ANUMA_DEM:?missing 20 m DEM path}"
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
    expected_time="06:00:00"
    expected_memory_mb="245760"
    : "${ANUMA_STAGE_A_ACCEPTANCE:?missing Stage A acceptance record}"
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
output_dir="${ANUMA_OUTPUT_ROOT}/stage-b-${ANUMA_STUDY_MODE}-${SLURM_JOB_ID}"
job_tmp="${ANUMA_SCRATCH_DIR}/anuma-stage-b-${SLURM_JOB_ID}"

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
  echo "Stage B requires stable release modules, not staging" >&2
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
from anuma.stage_b import require_stage_a_acceptance

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
if os.environ["ANUMA_STUDY_MODE"] == "production":
    try:
        require_stage_a_acceptance(Path(os.environ["ANUMA_STAGE_A_ACCEPTANCE"]))
    except ValueError as exc:
        raise SystemExit(f"Stage A gate refused: {exc}") from exc
PY

test -x "${ANUMA_PYTHON}"
test -d "${ANUMA_OUTPUT_ROOT}"
test -w "${ANUMA_OUTPUT_ROOT}"
test -d "${ANUMA_SCRATCH_DIR}"
test -w "${ANUMA_SCRATCH_DIR}"
test -f "${ANUMA_DEM}"
test ! -e "${output_dir}"

cd "${repo_root}"
if [[ "${ANUMA_STUDY_MODE}" == "pilot" ]]; then
  srun --ntasks=1 --cpus-per-task=128 \
    "${ANUMA_PYTHON}" -m anuma.stage_b \
    --mode pilot \
    --dem "${ANUMA_DEM}" \
    --output-dir "${output_dir}" \
    --workers 128
else
  srun --ntasks=1 --cpus-per-task=128 \
    "${ANUMA_PYTHON}" -m anuma.stage_b \
    --mode production \
    --dem "${ANUMA_DEM}" \
    --input-manifest "${ANUMA_INPUT_MANIFEST}" \
    --stage-a-acceptance "${ANUMA_STAGE_A_ACCEPTANCE}" \
    --output-dir "${output_dir}" \
    --workers 128
fi
