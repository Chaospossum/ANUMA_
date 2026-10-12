#!/bin/bash -l
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bootstrap_python="${ANUMA_BOOTSTRAP_PYTHON:-python3}"
profile="${ANUMA_ENVIRONMENT:-smoke}"
case "${profile}" in
  smoke)
    lock="${repo_root}/hpc/requirements-hpc.lock"
    default_venv="${repo_root}/.venv-hpc"
    ;;
  ingest)
    lock="${repo_root}/hpc/requirements-ingest.lock"
    default_venv="${repo_root}/.venv-hpc-ingest"
    ;;
  sun)
    lock="${repo_root}/hpc/requirements-sun.lock"
    default_venv="${repo_root}/.venv-hpc-sun"
    ;;
  *)
    echo "ANUMA_ENVIRONMENT must be smoke, ingest, or sun" >&2
    exit 2
    ;;
esac
venv="${ANUMA_VENV:-${default_venv}}"

: "${SLURM_JOB_ID:?run bootstrap inside a MeluXina compute allocation}"
command -v module >/dev/null 2>&1 || {
  echo "Environment Modules is unavailable in this compute allocation" >&2
  exit 2
}
module_record="$(mktemp)"
trap 'rm -f -- "${module_record}"' EXIT
module -t list > "${module_record}" 2>&1
if grep -q 'env/staging/2025\.1' "${module_record}" \
  && [[ -z "${ANUMA_STAGING_JUSTIFICATION:-}" ]]; then
  echo "Refusing staging modules without ANUMA_STAGING_JUSTIFICATION naming the missing release package" >&2
  exit 2
fi

"${bootstrap_python}" - <<'PY'
import sys
if sys.version_info[:2] != (3, 12):
    raise SystemExit("The hash-pinned ANUMA HPC environment requires Python 3.12.x")
print(f"Bootstrapping with Python {sys.version.split()[0]}")
PY
"${bootstrap_python}" -m ensurepip --version >/dev/null 2>&1 || {
  echo "The selected Python cannot create a venv (ensurepip is unavailable)." >&2
  echo "Choose the discovered stable-release Python 3.12 with venv support; do not install system packages on a login node." >&2
  exit 2
}

"${bootstrap_python}" -m venv "${venv}"
"${venv}/bin/python" -m pip install --disable-pip-version-check \
  --only-binary=:all: --require-hashes \
  --requirement "${lock}"
"${venv}/bin/python" -m pip install --disable-pip-version-check \
  --no-index --no-build-isolation --no-deps --editable "${repo_root}"
"${venv}/bin/python" -m pip check
if [[ "${profile}" == "ingest" ]]; then
  "${venv}/bin/python" - <<'PY'
import matplotlib
import rasterio
import scipy

print(
    "Verified ingest runtime: "
    f"rasterio={rasterio.__version__} GDAL={rasterio.__gdal_version__} "
    f"scipy={scipy.__version__} matplotlib={matplotlib.__version__}"
)
PY
elif [[ "${profile}" == "sun" ]]; then
  "${venv}/bin/python" - <<'PY'
import spiceypy

print(f"Verified Sun runtime: spiceypy={spiceypy.__version__}")
PY
fi

"${venv}/bin/python" - \
  "${repo_root}" "${venv}" "${module_record}" "${lock}" "${profile}" <<'PY'
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
lock = Path(sys.argv[4])
profile = sys.argv[5]
record = {
    "schema_version": 1,
    "created_at_utc": datetime.now(timezone.utc).isoformat(),
    "python": platform.python_version(),
    "platform": platform.platform(),
    "slurm_job_id": os.environ["SLURM_JOB_ID"],
    "profile": profile,
    "modules": module_record.read_text().splitlines(),
    "staging_justification": os.environ.get("ANUMA_STAGING_JUSTIFICATION"),
    "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
    "packages": subprocess.check_output(
        [
            str(venv / "bin" / "python"),
            "-m",
            "pip",
            "list",
            "--format=freeze",
        ],
        text=True,
    ).splitlines(),
}
(venv / "anuma-environment.json").write_text(
    json.dumps(record, indent=2, sort_keys=True) + "\n"
)
print(f"Wrote {venv / 'anuma-environment.json'}")
PY
