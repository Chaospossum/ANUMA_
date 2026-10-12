# MeluXina 240 m execution slice

This is a guarded first batch slice, not a completed HPC result. It scores the
already-ingested 240 m ANUMA layers and writes a ranked-site JSON file. It does
not download data, build 20 m products, run optional studies, or submit itself.

The job is capped at one node, four CPUs, 16 GiB, and 30 minutes: at most
2 CPU-hours. Account and partition are deliberately absent from the SBATCH
file. They must be supplied after a read-only preflight verifies the current
user association, partition, remaining project allocation, storage quota, and
staged data.

## Files

- `hpc/bootstrap.sh`: creates the minimal pinned Python environment.
- `hpc/requirements-hpc.lock`: exact direct and transitive versions.
- `hpc/build-input-manifest.py`: checksums an existing 240 m grid.
- `hpc/preflight.py`: read-only Slurm, allocation, quota, path, and data checks.
- `hpc/submit-smoke.sh`: accepts only a preflight receipt less than 24 hours old.
- `hpc/slurm/smoke-240m.sbatch`: bounded job with a second receipt check.
- `anuma/hpc_smoke.py`: validates inputs, scores, and writes output lineage.

## Stop conditions

Do not run `submit-smoke.sh` until all of the following are known from the
current MeluXina account or project portal:

1. login hostname and SSH username;
2. Slurm account and eligible CPU partition;
3. current remaining CPU-hours (not only the original award);
4. project storage quota and current bytes used;
5. absolute, project-approved input, output, and scratch paths;
6. the provenance and redistribution status of every staged input;
7. a checksum manifest generated after transfer.

Never put a private key, token, password, or secret in this repository, an
environment variable shown in a log, or a preflight evidence field.

## Reproducible setup on the login node

Use the cluster-supported Python 3.11+ interpreter explicitly. Do not guess a
module name in a script.

```bash
export ANUMA_BOOTSTRAP_PYTHON=/absolute/path/to/python3
bash hpc/bootstrap.sh
cat .venv-hpc/anuma-environment.json
```

The lock fixes package versions. The generated environment record captures the
actual interpreter, platform, lock checksum, and installed distributions.
Keep that record with the job outputs. Wheels are selected for the current
platform, so this is version-reproducible rather than bit-identical across
different operating systems or CPU architectures.

## Stage and pin the 240 m inputs

The required files are `grid.json`, `slope.npy`, `illumination.npy`,
`hydrogen.npy`, and `earth_visibility.npy`. The manifest must live in the grid
directory or one of its parents; paths outside the manifest tree are rejected.

```bash
.venv-hpc/bin/python hpc/build-input-manifest.py \
  --grid-dir /verified/project/path/anuma-240m/grid \
  --output /verified/project/path/anuma-240m/input-manifest.json \
  --id anuma-240m-YYYYMMDD

.venv-hpc/bin/python -m anuma.hpc_smoke \
  --input-manifest /verified/project/path/anuma-240m/input-manifest.json \
  --output-dir /tmp/not-used \
  --check-only
```

The ID must identify the actual staged dataset; do not copy the example
literally. `--check-only` reads metadata, hashes every file, and checks every
array shape. It does not create the output directory or compute a ranking.

## Read-only preflight

Obtain the quota and allocation numbers from the portal or a documented
MeluXina command immediately before preflight. The two evidence values are
non-secret references such as a portal page name and timestamp or saved command
output path.

```bash
export ANUMA_ACCOUNT='<verified Slurm account>'
export ANUMA_PARTITION='<verified CPU partition>'
export ANUMA_INPUT_MANIFEST='/verified/project/path/anuma-240m/input-manifest.json'
export ANUMA_OUTPUT_ROOT='/verified/project/path/outputs'
export ANUMA_SCRATCH_DIR='/verified/scratch/path'
export ANUMA_PROJECT_REMAINING_CPU_HOURS='<current numeric value>'
export ANUMA_PROJECT_STORAGE_QUOTA_BYTES='<current integer byte value>'
export ANUMA_PROJECT_STORAGE_USED_BYTES='<current integer byte value>'
export ANUMA_ALLOCATION_EVIDENCE='<non-secret source and timestamp>'
export ANUMA_STORAGE_EVIDENCE='<non-secret source and timestamp>'

.venv-hpc/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-preflight-$(date -u +%Y%m%dT%H%M%SZ).json"
```

Preflight fails closed if Slurm commands are missing, the account association
or partition cannot be queried, fewer than 2 CPU-hours remain, project quota or
filesystem free space leaves less than 100 MiB, paths are not writable, or an
input checksum/shape is wrong. It does not call `sbatch`.

## Deliberate submission

Review the receipt and environment record. Set the receipt path, then submit
through the wrapper only:

```bash
export ANUMA_PREFLIGHT_RECEIPT="$HOME/anuma-preflight-YYYYMMDDTHHMMSSZ.json"
export ANUMA_PYTHON="$PWD/.venv-hpc/bin/python"
bash hpc/submit-smoke.sh
```

The wrapper is the only command here that calls `sbatch`. The job rejects an
expired, altered, or mismatched receipt and refuses to overwrite an output
directory. Its output directory is
`$ANUMA_OUTPUT_ROOT/smoke-240m-$SLURM_JOB_ID`.

## Outputs and review

Successful computation writes:

- `ranking.json`: the 240 m ranked sites from the existing ANUMA scoring code;
- `output-manifest.json`: timestamps, input-manifest hash and ID, Git commit,
  Python/NumPy/config identity, safe Slurm metadata, and output checksum.

Preserve the Slurm stdout/stderr files, preflight receipt, and
`.venv-hpc/anuma-environment.json` beside those outputs before any scientific
review. A completed smoke test proves only that the pinned 240 m pipeline ran
under the declared resources. It does not validate the scientific model, prove
20 m agreement, or authorize another job.

After the job exits, use documented MeluXina accounting commands to record
elapsed time, CPU time, and peak memory. Do not schedule a 20 m run until the
240 m outputs and accounting record have been reviewed.
