#!/bin/bash -l
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bootstrap_python="${ANUMA_BOOTSTRAP_PYTHON:-python3}"
venv="${ANUMA_VENV:-${repo_root}/.venv-hpc}"

: "${SLURM_JOB_ID:?run bootstrap inside a MeluXina compute allocation}"
command -v module >/dev/null 2>&1 || {
  echo "Environment Modules is unavailable in this compute allocation" >&2
  exit 2
}
module_record="$(mktemp)"
trap 'rm -f -- "${module_record}"' EXIT
module -t list > "${module_record}" 2>&1
if grep -q 'env/staging/2025\\.1' "${module_record}" \
  && [[ -z "${ANUMA_STAGING_JUSTIFICATION:-}" ]]; then
  echo "Refusing staging modules without ANUMA_STAGING_JUSTIFICATION naming the missing release package" >&2
  exit 2
fi

"${bootstrap_python}" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("ANUMA requires Python 3.11 or newer")
print(f"Bootstrapping with Python {sys.version.split()[0]}")
PY
"${bootstrap_python}" -m ensurepip --version >/dev/null 2>&1 || {
  echo "The selected Python cannot create a venv (ensurepip is unavailable)." >&2
  echo "Choose a cluster-supported Python 3.11+ with venv support; do not install system packages on a login node." >&2
  exit 2
}

"${bootstrap_python}" -m venv "${venv}"
"${venv}/bin/python" -m pip install --disable-pip-version-check \
  --only-binary=:all: --requirement "${repo_root}/hpc/requirements-hpc.lock"
"${venv}/bin/python" -m pip install --disable-pip-version-check \
  --no-deps --editable "${repo_root}"
"${venv}/bin/python" -m pip check

"${venv}/bin/python" - "${repo_root}" "${venv}" "${module_record}" <<'PY'
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

repo = Path(sys.argv[1])
venv = Path(sys.argv[2])
module_record = Path(sys.argv[3])
lock = repo / "hpc" / "requirements-hpc.lock"
record = {
    "schema_version": 1,
    "created_at_utc": datetime.now(timezone.utc).isoformat(),
    "python": platform.python_version(),
    "platform": platform.platform(),
    "slurm_job_id": os.environ["SLURM_JOB_ID"],
    "modules": module_record.read_text().splitlines(),
    "staging_justification": os.environ.get("ANUMA_STAGING_JUSTIFICATION"),
    "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
    "packages": subprocess.check_output(
        [str(venv / "bin" / "python"), "-m", "pip", "freeze", "--all"],
        text=True,
    ).splitlines(),
}
(venv / "anuma-environment.json").write_text(
    json.dumps(record, indent=2, sort_keys=True) + "\n"
)
print(f"Wrote {venv / 'anuma-environment.json'}")
PY
