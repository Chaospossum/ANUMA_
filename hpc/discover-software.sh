#!/bin/bash -l
set -euo pipefail

: "${SLURM_JOB_ID:?run software discovery inside a MeluXina compute allocation}"
command -v module >/dev/null 2>&1 || {
  echo "Environment Modules is unavailable in this compute allocation" >&2
  exit 2
}

output="${1:-/project/home/p201619/ANUMA_/out/hpc/software-discovery.txt}"
mkdir -p -- "$(dirname "${output}")"
umask 077
loaded_modules="$(module -t list 2>&1)"

{
  printf 'captured_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'host=%s\n' "$(hostname)"
  printf 'slurm_job_id=%s\n' "${SLURM_JOB_ID}"
  printf '\n## Loaded stable-release modules\n'
  printf '%s\n' "${loaded_modules}"
  for query in Python GDAL PROJ GEOS rasterio pyproj; do
    printf '\n## module spider %s\n' "${query}"
    module spider "${query}" 2>&1 || true
  done
} > "${output}"

printf '%s\n' "${output}"
if grep -q 'env/staging/2025\\.1' <<<"${loaded_modules}"; then
  echo "staging is loaded; prefer env/release/2025.1 for discovery" >&2
  exit 2
fi
