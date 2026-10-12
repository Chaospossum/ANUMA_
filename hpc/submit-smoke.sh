#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${ANUMA_PREFLIGHT_RECEIPT:?set ANUMA_PREFLIGHT_RECEIPT to a successful preflight receipt}"
: "${ANUMA_ACCOUNT:?set ANUMA_ACCOUNT}"
: "${ANUMA_PARTITION:?set ANUMA_PARTITION}"
: "${ANUMA_INPUT_MANIFEST:?set ANUMA_INPUT_MANIFEST}"
: "${ANUMA_OUTPUT_ROOT:?set ANUMA_OUTPUT_ROOT}"
: "${ANUMA_SCRATCH_DIR:?set ANUMA_SCRATCH_DIR}"

ANUMA_PYTHON="${ANUMA_PYTHON:-${repo_root}/.venv-hpc/bin/python}"
export ANUMA_PYTHON

"${ANUMA_PYTHON}" - "${ANUMA_PREFLIGHT_RECEIPT}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

receipt = json.loads(Path(sys.argv[1]).read_text())
verified = datetime.fromisoformat(receipt["verified_at_utc"])
age = (datetime.now(timezone.utc) - verified).total_seconds()
if age < 0 or age > 24 * 60 * 60:
    raise SystemExit("preflight receipt must be less than 24 hours old")
expected = {
    "account": os.environ["ANUMA_ACCOUNT"],
    "partition": os.environ["ANUMA_PARTITION"],
    "input_manifest": str(Path(os.environ["ANUMA_INPUT_MANIFEST"]).resolve()),
    "output_root": str(Path(os.environ["ANUMA_OUTPUT_ROOT"]).resolve()),
    "scratch_dir": str(Path(os.environ["ANUMA_SCRATCH_DIR"]).resolve()),
}
for key, value in expected.items():
    if receipt.get(key) != value:
        raise SystemExit(f"preflight receipt {key} does not match current environment")
if float(receipt.get("job_cpu_hours_cap", -1)) != 2.0:
    raise SystemExit("preflight receipt does not authorize the bounded smoke job")
PY

if [[ ! -x "${ANUMA_PYTHON}" ]]; then
  echo "ANUMA_PYTHON is not executable: ${ANUMA_PYTHON}" >&2
  exit 2
fi

sbatch \
  --parsable \
  --account="${ANUMA_ACCOUNT}" \
  --partition="${ANUMA_PARTITION}" \
  --chdir="${repo_root}" \
  --export=ALL \
  "${repo_root}/hpc/slurm/smoke-240m.sbatch"
