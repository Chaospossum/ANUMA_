#!/bin/bash -l
# Stage D DEM-noise production (docs/stage-d-plan.md): 8 sites x 200 draws x
# 2 runs (primary, sensitivity), serial reference plus parallel run, with
# per-site and per-draw records and checksum manifests. One CPU node, 128
# workers, no accelerators. QOS default, hard walltime 01:30 (1.5
# node-hours), below the plan's 02:00 cap, so pilot plus production stays
# within 2.0 node-hours. 240G is the partition limit of 1920 MB per CPU
# times 128 CPUs. Caps are repeated and enforced by submit-stage-d.sh.
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128

set -euo pipefail
umask 027

stage_d_mode="production"
expected_qos="default"
expected_time="01:30:00"
expected_memory_mb="245760"

: "${ANUMA_PREFLIGHT_RECEIPT:?missing preflight receipt}"
: "${ANUMA_INPUT_MANIFEST:?missing input manifest}"
: "${ANUMA_STAGE_D_RAW_DIR:?missing PGDA product 90 directory}"
: "${ANUMA_DEM:?missing 20 m DEM path}"
: "${ANUMA_STAGE_B_SLOPE:?missing Stage B slope-20m.npy}"
: "${ANUMA_MC_SUMMARY:?missing Monte Carlo study summary}"
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

[[ "${ANUMA_STUDY_MODE}" == "${stage_d_mode}" ]] || { echo "study mode mismatch" >&2; exit 2; }
[[ "${SLURM_JOB_ACCOUNT}" == "p201619" ]] || { echo "account must be p201619" >&2; exit 2; }
[[ "${SLURM_CPUS_PER_TASK}" == "128" ]] || { echo "CPU cap mismatch" >&2; exit 2; }
[[ "${SLURM_JOB_NUM_NODES}" == "1" ]] || { echo "node cap mismatch" >&2; exit 2; }
[[ "${SLURM_NTASKS}" == "1" ]] || { echo "task cap mismatch" >&2; exit 2; }
if [[ -n "${SLURM_JOB_GPUS:-}${SLURM_GPUS:-}${SLURM_GPUS_ON_NODE:-}" ]]; then
  echo "accelerators are not allowed" >&2
  exit 2
fi
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
output_dir="${ANUMA_OUTPUT_ROOT}/stage-d-${stage_d_mode}-${SLURM_JOB_ID}"
job_tmp="${ANUMA_SCRATCH_DIR}/anuma-stage-d-${SLURM_JOB_ID}"

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
# .venv-hpc-ingest (rasterio, scipy) was built on this Python module; without
# it the interpreter loads the system libpython and numpy crashes (job 5334407).
module load env/release/2024.1
module load Python/3.12.3-GCCcore-13.3.0
module -t list > "${job_tmp}/module-list.txt" 2>&1
if grep -q 'env/staging/2025\.1' "${job_tmp}/module-list.txt"; then
  echo "Stage D requires stable release modules, not staging" >&2
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
from anuma.stage_d_gate import reject_stage_d_receipt

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
PY

test -x "${ANUMA_PYTHON}"
test -d "${ANUMA_OUTPUT_ROOT}"
test -w "${ANUMA_OUTPUT_ROOT}"
test -d "${ANUMA_SCRATCH_DIR}"
test -w "${ANUMA_SCRATCH_DIR}"
test ! -e "${output_dir}"

# The job re-hashes every input against the pinned digests before reading.
cd "${repo_root}"
srun --export=ALL --ntasks=1 --cpus-per-task=128 \
  "${ANUMA_PYTHON}" -m anuma.stage_d \
  --mode "${stage_d_mode}" \
  --input-manifest "${ANUMA_INPUT_MANIFEST}" \
  --dem "${ANUMA_DEM}" \
  --raw-dir "${ANUMA_STAGE_D_RAW_DIR}" \
  --stage-b-slope "${ANUMA_STAGE_B_SLOPE}" \
  --mc-summary "${ANUMA_MC_SUMMARY}" \
  --output-dir "${output_dir}" \
  --workers 128
