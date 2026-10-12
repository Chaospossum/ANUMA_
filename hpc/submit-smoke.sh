#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${ANUMA_PREFLIGHT_RECEIPT:?set ANUMA_PREFLIGHT_RECEIPT to a successful preflight receipt}"
: "${ANUMA_ACCOUNT:?set ANUMA_ACCOUNT}"
: "${ANUMA_PARTITION:?set ANUMA_PARTITION}"
ANUMA_QOS="${ANUMA_QOS:-test}"
: "${ANUMA_QOS:?set ANUMA_QOS}"
export ANUMA_QOS
: "${ANUMA_INPUT_MANIFEST:?set ANUMA_INPUT_MANIFEST}"
: "${ANUMA_OUTPUT_ROOT:?set ANUMA_OUTPUT_ROOT}"
: "${ANUMA_SCRATCH_DIR:?set ANUMA_SCRATCH_DIR}"

ANUMA_PYTHON="${ANUMA_PYTHON:-${repo_root}/.venv-hpc/bin/python}"
export ANUMA_PYTHON

"${ANUMA_PYTHON}" - "${ANUMA_PREFLIGHT_RECEIPT}" "${repo_root}" <<'PY'
import hashlib
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1]).resolve()
metadata = path.stat()
if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
    raise SystemExit("preflight receipt must be user-owned and mode 0600")
receipt = json.loads(path.read_text())
stored = receipt.pop("receipt_sha256", None)
actual = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
if stored != actual:
    raise SystemExit("preflight receipt digest is invalid")
verified = datetime.fromisoformat(receipt["verified_at_utc"])
age = (datetime.now(timezone.utc) - verified).total_seconds()
if age < 0 or age > 24 * 60 * 60:
    raise SystemExit("preflight receipt must be less than 24 hours old")
expected = {
    "account": os.environ["ANUMA_ACCOUNT"],
    "partition": os.environ["ANUMA_PARTITION"],
    "qos": os.environ["ANUMA_QOS"],
    "input_manifest": str(Path(os.environ["ANUMA_INPUT_MANIFEST"]).resolve()),
    "output_root": str(Path(os.environ["ANUMA_OUTPUT_ROOT"]).resolve()),
    "scratch_dir": str(Path(os.environ["ANUMA_SCRATCH_DIR"]).resolve()),
}
for key, value in expected.items():
    if receipt.get(key) != value:
        raise SystemExit(f"preflight receipt {key} does not match current environment")
if float(receipt.get("job_cpu_hours_cap", -1)) != 2.0:
    raise SystemExit("preflight receipt does not authorize the bounded smoke job")
manifest = Path(os.environ["ANUMA_INPUT_MANIFEST"])
if hashlib.sha256(manifest.read_bytes()).hexdigest() != receipt["input_manifest_sha256"]:
    raise SystemExit("input manifest changed after preflight")
repo = Path(sys.argv[2])
if subprocess.check_output(["git", "-C", repo, "status", "--porcelain"], text=True).strip():
    raise SystemExit("repository changed after preflight")
commit = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()
if commit != receipt["git_commit"]:
    raise SystemExit("Git commit changed after preflight")
PY

if [[ ! -x "${ANUMA_PYTHON}" ]]; then
  echo "ANUMA_PYTHON is not executable: ${ANUMA_PYTHON}" >&2
  exit 2
fi

unset "${!SBATCH_@}"
sbatch \
  --parsable \
  --account="${ANUMA_ACCOUNT}" \
  --partition="${ANUMA_PARTITION}" \
  --qos="${ANUMA_QOS}" \
  --nodes=1 \
  --ntasks=1 \
  --cpus-per-task=4 \
  --mem=16G \
  --time=00:30:00 \
  --output="${ANUMA_OUTPUT_ROOT}/slurm-%x-%j.out" \
  --error="${ANUMA_OUTPUT_ROOT}/slurm-%x-%j.err" \
  --chdir="${repo_root}" \
  --export=ANUMA_PREFLIGHT_RECEIPT,ANUMA_INPUT_MANIFEST,ANUMA_OUTPUT_ROOT,ANUMA_SCRATCH_DIR,ANUMA_PYTHON \
  "${repo_root}/hpc/slurm/smoke-240m.sbatch"
