# MeluXina 240 m execution slice

This is a guarded first batch slice, not a completed HPC result. It scores the
already-ingested 240 m ANUMA layers and writes a ranked-site JSON file. It does
not download data, build 20 m products, run optional studies, or submit itself.

The job is capped at one node, four CPU cores, 16 GiB, and 30 minutes: a
2 core-hour request and a conservative upper bound of 0.5 CPU node-hours.
Account, partition, and QOS are deliberately absent from the SBATCH file.
They are supplied only after a read-only preflight verifies the current user
association, scheduler policy, remaining project allocation, storage quota,
and staged data.

## Confirmed project resources

The current `myquota` facts for project/account `p201619` are:

- 300 CPU node-hours for the current month, with 0 used when confirmed;
- no GPU, FPGA, or large-memory allocation;
- CPU nodes have 128 cores and 512 GB RAM;
- `/project/home/p201619` and `/project/scratch/p201619` each have 10 TB;
- the repository is at `/project/home/p201619/ANUMA_`;
- personal home has 100 GB.

The scripts therefore default to account `p201619`, partition `cpu`, QOS
`test`, and scratch `/project/scratch/p201619`. Every value can still be
overridden for a verified account-policy change. Input manifests and output
roots remain explicit because their final staged paths must be verified.

Keep immutable inputs, the pinned environment, receipts, and final outputs
under the shared project path. Use project scratch only for job-local temporary
files; the job removes its `$SLURM_JOB_ID` scratch directory on every exit.
Do not stage large data or job outputs in the 100 GB personal home.

This allocation is optimized as one-node CPU/memory work. Request only measured
cores and RAM within the 128-core/512-GB node; do not request accelerators,
large-memory nodes, or multiple nodes. QOS percentage limits are scheduler
ceilings, not evidence that ANUMA benefits from or is allowed to use a
100-node-scale job.

## MeluXina QOS policy

| QOS | Maximum walltime | Node limit | Scheduling note |
| --- | ---: | ---: | --- |
| `dev` | 06:00 | 1 node | development |
| `test` | 00:30 | 5% | high priority |
| `short` | 06:00 | 5% | short jobs |
| `short-preempt` | 06:00 | 5% | preemptible |
| `default` | 48:00 | 25% | standard production |
| `long` | 144:00 | 5% | low priority; maximum 1 job/user |
| `large` | 24:00 | 70% | special arrangement; maximum 1 job/user |
| `urgent` | 06:00 | 5% | special arrangement |

Use `test` for this first 30-minute smoke job unless the verified account
policy requires another eligible QOS. Use `default` for later bounded
production unless account policy requires otherwise. `large` and `urgent`
require a special arrangement; this slice does not request one.

MeluXina syntax is of the form
`salloc -A p201619 -p cpu --qos default -t 00:30:00`.
`ANUMA_ACCOUNT` defaults to `p201619` and remains configurable; do not copy an
account from unrelated example commands.

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
2. Slurm account, eligible CPU partition, and account-eligible QOS;
3. current remaining CPU node-hours (not only the original award);
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
  --grid-dir /project/home/p201619/ANUMA_/data/grid \
  --output /project/home/p201619/ANUMA_/data/input-manifest.json \
  --id anuma-240m-YYYYMMDD

.venv-hpc/bin/python -m anuma.hpc_smoke \
  --input-manifest /project/home/p201619/ANUMA_/data/input-manifest.json \
  --output-dir /tmp/not-used \
  --check-only
```

The ID must identify the actual staged dataset; do not copy the example
literally. `--check-only` reads metadata, hashes every file, and checks every
array shape. It does not create the output directory or compute a ranking.

## Read-only preflight

Obtain the quota and allocation numbers from the portal or a documented
MeluXina command immediately before preflight. Save each result as a
user-owned, mode-0600 JSON file. Evidence must be less than 24 hours old and
identify its non-secret source.

```json
{"source":"myquota capture path","captured_at_utc":"<ISO-8601 UTC>","account":"p201619","remaining_cpu_node_hours":300}
```

```json
{"source":"myquota capture path","captured_at_utc":"<ISO-8601 UTC>","quota_bytes":10000000000000,"used_bytes":0}
```

The numbers above illustrate field types only. Replace every value with the
current account evidence; do not treat the original award as remaining quota.

```bash
export ANUMA_ACCOUNT='p201619'
export ANUMA_PARTITION='cpu'
export ANUMA_QOS='test'
export ANUMA_INPUT_MANIFEST='/project/home/p201619/ANUMA_/data/input-manifest.json'
export ANUMA_OUTPUT_ROOT='/project/home/p201619/ANUMA_/out/hpc'
export ANUMA_SCRATCH_DIR='/project/scratch/p201619'
export ANUMA_ALLOCATION_EVIDENCE_FILE="$HOME/anuma-allocation-evidence.json"
export ANUMA_STORAGE_EVIDENCE_FILE="$HOME/anuma-storage-evidence.json"
chmod 600 "$ANUMA_ALLOCATION_EVIDENCE_FILE" "$ANUMA_STORAGE_EVIDENCE_FILE"

.venv-hpc/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-preflight-$(date -u +%Y%m%dT%H%M%SZ).json"
```

Preflight fails closed if Slurm commands are missing, the account association,
partition, or selected QOS cannot be queried, evidence is stale or unsafe,
fewer than 0.5 CPU node-hours remain, project quota or filesystem free space
leaves less than 100 MiB, `myquota` is unavailable, paths are not writable, the
repository is dirty, or an input checksum/shape is wrong. It does not call
`sbatch`.

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
directory. Resource limits are repeated as `sbatch` command-line arguments,
and only the six required `ANUMA_*` values are exported to the job; unrelated
login-shell secrets are not propagated. The wrapper saves `myquota` immediately
before `sbatch`, and the job saves it again after the workload even when the
workload fails. Its output directory is
`$ANUMA_OUTPUT_ROOT/smoke-240m-$SLURM_JOB_ID`.

## Outputs and review

Successful computation writes:

- `ranking.json`: the 240 m ranked sites from the existing ANUMA scoring code;
- `output-manifest.json`: timestamps, input-manifest hash and ID, Git commit,
  Python/NumPy/config identity, safe Slurm metadata, and output checksum.
- `myquota-before.txt` and `myquota-after.txt`: raw allocation snapshots around
  the submitted workload.

Preserve the Slurm stdout/stderr files, preflight receipt, and
`.venv-hpc/anuma-environment.json` beside those outputs before any scientific
review. A completed smoke test proves only that the pinned 240 m pipeline ran
under the declared resources. It does not validate the scientific model, prove
20 m agreement, or authorize another job.

After the job exits, use documented MeluXina accounting commands to record
elapsed time, CPU time, and peak memory. Do not schedule a 20 m run until the
240 m outputs and accounting record have been reviewed.
