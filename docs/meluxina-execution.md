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

The 12 October `myquota` facts for project/account `p201619` are:

- 300 CPU node-hours for the current month, with about 1 used when confirmed;
- no GPU, FPGA, or large-memory allocation;
- CPU nodes have 128 cores and 512 GB RAM (480 GB usable; the `cpu` partition allows 1920 MB per CPU, so 128 CPUs can request at most 240G);
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

## Confirmed connection and clone

- Host: `login.lxp.lu`
- Port: `8822`
- Repository: `/project/home/p201619/ANUMA_`
- Study-plan baseline `main`: `7546943cfb6e5e80d1b234b0524b7c8715d1dc58`

Use site-managed SSH configuration for `login.lxp.lu` on port `8822`; public
documentation intentionally contains no personal username. Authentication uses
an approved SSH credential provisioned outside this repository. Never copy,
commit, print, or request private-key material. Verify the host fingerprint out
of band before accepting it.

The login nodes are for SSH access, Git/file management, `myquota`, job
submission, and monitoring only. Official policy forbids computationally
intensive, long-running, and data-intensive work there:

- [Platform usage policy](https://docs.lxp.lu/access/PoliciesSummary/)
- [Quick start](https://docs.lxp.lu/first-steps/quick_start/)
- [Handling jobs](https://docs.lxp.lu/first-steps/handling_jobs/)
- [Managing data](https://docs.lxp.lu/first-steps/managing_data/)

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
policy changes. The guarded batch script enforces `test`. Use `default` for
later bounded production unless account policy requires otherwise. `large`
and `urgent` require a special arrangement; this slice does not request one.

The official `dev` path is different from the batch smoke path. Interactive
development must use the CPU development reservation:

```bash
salloc -A p201619 -p cpu --reservation=cpudev -q dev -N 1 -t 02:00:00
```

Run environment creation, package discovery, checksumming, and preflight
inside that compute allocation. Exit it before deliberately submitting the
guarded batch smoke under `-q test`; the smoke has no reservation flag.

MeluXina syntax is of the form
`salloc -A p201619 -p cpu --qos default -t 00:30:00`.
`ANUMA_ACCOUNT` defaults to `p201619` and remains configurable; do not copy an
account from unrelated example commands.

## Files

- `hpc/bootstrap.sh`: creates the minimal pinned Python environment.
- `hpc/discover-software.sh`: records stable-release module discovery.
- `hpc/requirements-hpc.lock`: exact core versions and Python 3.12 Linux-wheel
  hashes; bootstrap installs it with `--require-hashes`.
- `hpc/requirements-ingest.lock`: exact Python 3.12 Linux-wheel hashes for the
  compute-node ingest runtime, including Rasterio/GDAL, SciPy, and Matplotlib.
- `hpc/requirements-sun.lock`: exact Python 3.12 Linux-wheel hashes for the
  scoring core and `spiceypy`; kernels remain checksummed scientific inputs.
- `hpc/build-input-manifest.py`: checksums an existing 240 m grid.
- `hpc/preflight.py`: read-only Slurm, allocation, quota, path, and data checks.
- `hpc/submit-smoke.sh`: accepts only a preflight receipt less than 24 hours old.
- `hpc/slurm/smoke-240m.sbatch`: bounded job with a second receipt check.
- `hpc/submit-monte-carlo.sh`: guarded pilot/full study submission wrapper.
- `hpc/slurm/monte-carlo.sbatch`: one-node study job with mode-specific caps.
- `hpc/submit-sun-validation.sh`: guarded pilot/production wrapper. It does
  not submit itself.
- `hpc/slurm/sun-validation.sbatch`: one CPU node, 128 workers, no
  accelerators. Pilot is QOS `test` and at most 30 minutes. Production is
  14:30 so the two modes stay within 15 node-hours.
- `hpc/submit-stage-b.sh`: guarded Stage B pilot/production wrapper. It does
  not submit itself.
- `hpc/slurm/stage-b.sbatch`: one CPU node, 128 workers, `--mem=240G`, no
  accelerators. Pilot is QOS `test` and at most 30 minutes. Production is
  QOS `default` and 06:00 so pilot plus production stays under 40 node-hours.
- `config/sun-epoch.yaml`: frozen hourly windows for the 2016 comparison.
- `config/stage-b.yaml`: Stage B tile size, pilot sample, walltime, and the
  Stage A acceptance path. Weights, 8° mask, and normalization stay in
  `config/layers.yaml`.
- `anuma/hpc_smoke.py`: validates inputs, scores, and writes output lineage.
- `anuma/hpc_monte_carlo.py`: deterministic 240 m clip-and-weight study.
- `config/sun.yaml`: separate horizon, DE440, date, output, and fixed
  validation-gate settings; it does not modify `config/layers.yaml`.
  The kernel pin is `/project/home/p201619/spice-kernels/kernel-manifest.json`.
  Digests stay in that file.
- `anuma/sun.py` and `anuma/sun_validation.py`: reusable terrain horizons,
  SPICE-only Sun directions, disk visibility, darkness outputs, and Stage-A
  RMSE/bias/scatter validation.
- `anuma/slope20.py` and `anuma/stage_b.py`: tiled 20 m Horn slope with halo
  checkpoints, 20 m score/rank using sampled 240 m soft layers, agreement map,
  and rank table.

## Stop conditions

Do not run `submit-smoke.sh` until all of the following are known from the
current MeluXina account or project portal:

1. login hostname, port, and approved site-managed SSH access;
2. Slurm account, eligible CPU partition, and account-eligible QOS;
3. current remaining CPU node-hours (not only the original award);
4. project storage quota and current bytes used;
5. absolute, project-approved input, output, and scratch paths;
6. the provenance and redistribution status of every staged input;
7. a checksum manifest generated after transfer.

Never put a private key, token, password, or secret in this repository, an
environment variable shown in a log, or a preflight evidence field.

## Stable software stack and reproducible setup

MeluXina's current default is `env/release/2025.1`, which provides Python
3.13.1 only. Python 3.12.3 is available only under `env/release/2024.1`.
Updates are exposed under `env/staging/2025.1`, but staging must not be used
unless a required package is absent from release and that exception is
documented. Login nodes have no Lmod; run module discovery inside `srun` on a
compute allocation. Never run `module purge`, because it removes the
environment that supplies `myquota`. Never guess module names. From the
`cpudev` compute allocation, discover Python, GDAL, PROJ, and geospatial
dependencies:

```bash
module list
bash hpc/discover-software.sh \
  /project/home/p201619/ANUMA_/out/hpc/software-discovery.txt
```

The discovery script uses `module spider` and does not load staging or install
anything. The scoring environment uses OS `/usr/bin/python3.12`, then creates
the project-local fallback on project storage:

```bash
export ANUMA_BOOTSTRAP_PYTHON=/usr/bin/python3.12
export ANUMA_VENV=/project/home/p201619/ANUMA_/.venv-hpc
bash hpc/bootstrap.sh
cat "$ANUMA_VENV/anuma-environment.json"
```

The lock fixes package versions. The generated environment record captures the
actual interpreter, platform, stable module list/versions, lock checksum, and
installed distributions. Bootstrap refuses to run without a SLURM compute
allocation. It does not install system packages. The package's base dependency
set is the minimal scoring core; ingest and web dependencies are explicit
optional groups. The committed HPC lock pins the complete core dependency
closure plus the build backend by version and wheel SHA-256, uses
`--require-hashes`, and performs the editable install offline without build
isolation before running `pip check`.

If and only if discovery proves a required package is absent from release,
document that package in `ANUMA_STAGING_JUSTIFICATION` before using staging.
The environment record preserves that justification. The guarded smoke job
itself rejects staging modules.

### Supported compute-node ingest environment

The ingest environment switches to `env/release/2024.1` inside `srun` to use
Python 3.12.3, then applies the committed hash-pinned profile. Do not switch on
a login node, run `module purge`, or guess GDAL/PROJ module names:

```bash
module load env/release/2024.1
export ANUMA_ENVIRONMENT=ingest
export ANUMA_BOOTSTRAP_PYTHON="$(command -v python3)"
export ANUMA_VENV=/project/home/p201619/ANUMA_/.venv-hpc-ingest
bash hpc/bootstrap.sh
```

This runs only inside a compute allocation, installs pinned Linux wheels for
Rasterio (including its GDAL runtime), PyProj/PROJ, SciPy, and Matplotlib,
runs `pip check`, imports the ingest stack, prints the Rasterio/GDAL versions,
and records modules/packages in `anuma-environment.json`. If release/2024.1
Python 3.12.3 or a compatible pinned wheel is unavailable, stop: the supported
fallback is approved-workstation ingest until the lock is deliberately
regenerated and reviewed. Do not switch to staging or guess package names.

### Supported Sun-model environment

Inside a reviewed compute allocation, build the separate hash-pinned SPICE
environment with OS Python 3.12. Kernel files are not downloaded by bootstrap.
The job reads `/project/home/p201619/spice-kernels/kernel-manifest.json` and
verifies each listed file's SHA-256 before it imports or calls `spiceypy`.
It refuses to run if any required role is missing. The required roles are
`DE440`, `moon_pa_de440_200625`, `moon_de440_250416.tf`, and `naif0012`.
The manifest on MeluXina is the pin; this repository does not copy the digests.

```bash
export ANUMA_ENVIRONMENT=sun
export ANUMA_BOOTSTRAP_PYTHON=/usr/bin/python3.12
export ANUMA_VENV=/project/home/p201619/ANUMA_/.venv-hpc-sun
bash hpc/bootstrap.sh
```

The frozen epoch file is `config/sun-epoch.yaml`. PGDA product 69
(https://pgda.gsfc.nasa.gov/products/69) averages hourly timesteps spanning
18.6 years, defined here as 18.6 × 365.25 days. Mazarico et al. 2011, NTRS
20120010094, used several 18.6-year cycles at 6 h, and PGDA says the products
were updated after 2011. NASA publishes no start or end for
`AVGVISIB_65S_240M_201608`. The filename is not a date. The validation gate
uses the primary window, starting 2000-01-01T00:00:00Z, labeled as ANUMA's
choice because no published start exists. The sensitivity window starts
9.3 × 365.25 days later. It is reported separately and cannot be used to pick
a passing start.

A timestep in that comparison is lit when any fraction of the solar disc is
above the horizon. The disc radius stays ±0.27°. The visible-disc area is not
averaged. `stop_rmse` stays 0.05 and `tune_threshold_after_run` stays false.
The receipt rejects a bad epoch file or a raised RMSE limit.

Both modes go through preflight and the receipt: account `p201619`, input and
kernel manifest checksums, affine revision `pds3-pixel-center-v2`, and the
frozen epoch file. One CPU node, 128 workers, no accelerators. Pilot mode
uses QOS `test` and at most 30 minutes. It is a measurement, not the full
window: 4 horizon azimuths and 256 epochs on the full grid. It writes timings
and a projected node-hour cost to `pilot-measurement.json` and does not apply
the RMSE gate. Production uses QOS `default` and 14:30. Sunlight evaluation
is split across row-tile processes. The job logs progress and checkpoints
finished horizon azimuths and sunlight accumulators. A timeout leaves those
files in the job directory. Those two walltimes add up to 15 node-hours.
`stop_rmse` stays 0.05. This document does not submit the job.

A laptop timing scaled to a 2534² grid was about 0.9 s per epoch in the
serial loop and about 195 s per horizon azimuth. That serial loop is why the
pilot is a sample and why production evaluates row tiles instead of one core.
Those figures are not a MeluXina result and not the RMSE gate.

```bash
export ANUMA_JOB_PROFILE=sun-validation-pilot
export ANUMA_QOS=test
export ANUMA_ACCOUNT=p201619
export ANUMA_KERNEL_MANIFEST=/project/home/p201619/spice-kernels/kernel-manifest.json
export ANUMA_EPOCH_MANIFEST="$PWD/config/sun-epoch.yaml"
.venv-hpc-sun/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-sun-pilot-$(date -u +%Y%m%dT%H%M%SZ).json"
export ANUMA_PREFLIGHT_RECEIPT="$HOME/<the-new-pilot-receipt>.json"
bash hpc/submit-sun-validation.sh pilot
```

Production may set `ANUMA_HORIZON_SEED_DIR` to the `checkpoint/horizons`
directory of an earlier Stage A job whose horizon code is unchanged. The job
copies the finished `azimuth-NNN.npy` files, writes their checksums to
`horizon-seed.txt`, and computes only the missing azimuths. Job `5333886`
(commit `5eadb43`) looked up the horizon with local azimuth instead of grid
azimuth (local + longitude on this `lon_0 = 0` grid). The horizon cube itself
does not depend on that lookup, so the plan is to stop that job once its
horizon azimuths are complete and seed the rerun with them.

Production repeats that sequence with `ANUMA_JOB_PROFILE=sun-validation`,
`ANUMA_QOS=default`, and `bash hpc/submit-sun-validation.sh production`.

#### Operator report, 12 October 2026

Project `p201619`:

- `.venv-hpc-sun` is built.
- Roles `DE440`, `moon_pa_de440_200625`, `moon_de440_250416.tf`, and
  `naif0012` are present in the manifest above and were SHA-256 verified twice.
- Operator-reported geometry sanity check: south-pole Sun elevation over
  calendar 2029 stays within ±1.53°. No Slurm job id was supplied. This is
  not the AVGVISIB validation gate and not a scored illumination result.

Stage A writes `out/sun-validation/`; no report is committed here. The fixed
gate is RMSE 0.05, accompanied by bias and residual scatter. That gate against
`avgvisib_65s_240m_201608` remains unrun and blocking. The calendar-year-2029
scenario default remains configurable and is not a mission interval.

## Stage B — full-cap 20 m slope and ranking comparison

Stage B asks whether the 240 m ranking survives a full-cap 20 m slope and the
same frozen score. It does not submit itself. Scientific products are only:

- `agreement-map-240m.npy` — hard-mask equality at 240 m centres after sampling
  the 20 m mask;
- `rank-table-20m-vs-240m.json` — top-N sites at both resolutions.

Operational manifests, logs, tile checkpoints, and `slope-20m.npy` exist so
those two products can be reproduced and so Stage D can reuse the 20 m slope.
Open scientific choices are listed in [stage-b-open-questions.md](stage-b-open-questions.md).

Frozen inputs (do not retune to make a run pass):

- 8° hard slope mask from `config/layers.yaml`;
- `decided_per_layer` normalization;
- demo weights 0.5 / 0.3 / 0.2;
- Stage A acceptance with RMSE ≤ 0.05 and `stop_rmse` still 0.05.

Illumination, hydrogen, and Earth visibility stay on the 240 m grid and are
sampled onto 20 m by nearest 240 m pixel centre. Only slope is recomputed from
`data/raw/LDEM_80S_20MPP_ADJ.TIF` with windowed reads (never a whole-file load).
Tiles use a one-pixel halo so Horn edges match the untiled serial reference;
workers reopen the DEM for their window; finished tiles are checkpointed.

### Laptop benchmark (not a MeluXina result)

On 12 October 2026, a laptop timed 16 tiles of size 2048 on the full-cap 20 m
DEM (225 tiles total) at about 0.96 s per tile wall time in a serial sample.
Scaled to 128 workers that is about two waves ≈ 31 s of slope work, plus a
0.25 node-hour scoring/I/O allowance → about **0.26 projected node-hours**.
Production is therefore capped at **06:00** (6 node-hours) with `--mem=240G`.
Pilot 0.5 + production 6.0 = 6.5 node-hours, under the Stage B 40-hour cap.
Those figures are a laptop projection, not a MeluXina measurement and not a
ranking result.

### Guarded pilot

```bash
export ANUMA_JOB_PROFILE=stage-b-pilot
export ANUMA_QOS=test
export ANUMA_ACCOUNT=p201619
export ANUMA_DEM=/project/home/p201619/ANUMA_/data/raw/LDEM_80S_20MPP_ADJ.TIF
.venv-hpc-sun/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-stage-b-pilot-$(date -u +%Y%m%dT%H%M%SZ).json"
export ANUMA_PREFLIGHT_RECEIPT="$HOME/<the-new-pilot-receipt>.json"
bash hpc/submit-stage-b.sh pilot
```

The pilot times a sample of full-cap tiles, writes `pilot-measurement.json`
with the projected one-node cost, and exits non-zero if that projection does
not fit the configured production cap. It does not apply the Stage A RMSE gate
and does not write the agreement map or rank table.

### Guarded production

Production requires a Stage A acceptance record. Do not assume job `5333886`
passed; point `ANUMA_STAGE_A_ACCEPTANCE` at the real
`validation-report.json` only after it shows RMSE ≤ 0.05.

```bash
export ANUMA_JOB_PROFILE=stage-b
export ANUMA_QOS=default
export ANUMA_STAGE_A_ACCEPTANCE=/project/home/p201619/ANUMA_/out/hpc/sun-validation-production-5334265/validation-report.json
.venv-hpc-sun/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-stage-b-$(date -u +%Y%m%dT%H%M%SZ).json"
export ANUMA_PREFLIGHT_RECEIPT="$HOME/<the-new-production-receipt>.json"
bash hpc/submit-stage-b.sh production
```

Preflight, the submit wrapper, and the batch script all refuse production
without that passed record. This document does not submit the job.

## Ingest, transfer, and pin the 240 m inputs

The required derived files are `grid.json`, `dem.npy`, `slope.npy`,
`illumination.npy`, `hydrogen.npy`, and `earth_visibility.npy`. The manifest
must live in the grid directory or one of its parents; paths outside the
manifest tree are rejected.

Source rasters and derived arrays are deliberately ignored by Git. A fresh
clone therefore is not score-ready. The source rasters are confirmed at
`data/raw` on the approved local machine and at
`/project/home/p201619/ANUMA_/data/raw` on MeluXina. Their presence does not
make the old derived grids valid: after the relevant PRs merge, validate the
existing raw files first, then re-ingest. On a compute allocation, capture the
pre-ingest raw manifest:

```bash
cd /project/home/p201619/ANUMA_
python -m anuma.hpc_data_check --root "$PWD" --require-raw-ready \
  --manifest-output data/raw-before-reingest.json
```

This hashes every existing source. It fails on a missing file, wrong configured
size, wrong known publisher-file hash, or tracked raw/grid data. The DEM
currently has no configured upstream digest, so its recorded digest is a
transfer-integrity pin, not publisher authentication.

If raw validation passes, do not redownload. `anuma.ingest` reuses each existing
file only when its configured size and available SHA-256 pass. If validation
fails, record the failing filename and allow re-ingest to replace only the
missing or invalid source via its `.part`/rename path.

There are two approved production paths:

1. **Approved workstation:** after all ingest corrections merge, run
   `--require-raw-ready` against local `data/raw`, preserve the resulting raw
   manifest, and re-ingest on that approved machine. Generate both post-ingest
   manifests, transfer `data/raw/`, `data/grid/`, and the manifests to
   `/project/home/p201619/ANUMA_/data/`, then rerun the full check inside a
   `cpudev` compute allocation. Compare per-file digests:
   `sources.<name>.actual_sha256` and `derived.<name>.sha256` must match across
   machines. Do not compare the whole manifest file hash because absolute root
   paths and capture metadata legitimately differ.
2. **MeluXina compute job:** use a separately reviewed, bounded one-node CPU
   job (normally `default` QOS) to validate the existing project raw directory
   and re-ingest directly into project storage. It downloads only a source that
   failed validation. Do not perform downloads, raster processing, hashing, or
   environment installation on a login node.

Do not create the reference manifest from the current misregistered
illumination ingest identified by the audit. The data chain remains blocked
until that correction is merged and its ingest test passes. The guarded smoke
job remains download-free and accepts only the verified staged outputs.

From the approved workstation environment or the supported
`.venv-hpc-ingest` inside the separately reviewed one-node compute job, run:

```bash
"${ANUMA_VENV}/bin/python" -m anuma.ingest
"${ANUMA_VENV}/bin/python" -m anuma.hpc_data_check --root "$PWD" --require-ready \
  --manifest-output data/data-after-reingest.json

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

The numbers above illustrate field types only. Replace every value with a
fresh capture; `myquota` reporting is delayed, and the original award is not
the current remaining quota.

```bash
export ANUMA_ACCOUNT='p201619'
export ANUMA_PARTITION='cpu'
export ANUMA_QOS='test'
export ANUMA_DATA_ROOT='/project/home/p201619/ANUMA_'
export ANUMA_INPUT_MANIFEST='/project/home/p201619/ANUMA_/data/input-manifest.json'
export ANUMA_OUTPUT_ROOT='/project/home/p201619/ANUMA_/out/hpc'
export ANUMA_SCRATCH_DIR='/project/scratch/p201619'
export ANUMA_ALLOCATION_EVIDENCE_FILE="$HOME/anuma-allocation-evidence.json"
export ANUMA_STORAGE_EVIDENCE_FILE="$HOME/anuma-storage-evidence.json"
chmod 600 "$ANUMA_ALLOCATION_EVIDENCE_FILE" "$ANUMA_STORAGE_EVIDENCE_FILE"

.venv-hpc/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-preflight-$(date -u +%Y%m%dT%H%M%SZ).json"
```

Run preflight inside the `cpudev` compute allocation because it hashes the
staged arrays. Preflight fails closed if Slurm commands are missing, the account association,
partition, or selected QOS cannot be queried, evidence is stale or unsafe,
fewer than 0.5 CPU node-hours remain, project quota or filesystem free space
leaves less than 100 MiB, `myquota` is unavailable (except Stage D inside an allocation), paths are not writable, the
repository is dirty, source/derived data is absent, or an input checksum/shape
is wrong. It does not call `sbatch`.

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
  Python/NumPy/config identity, loaded stable-release module versions, safe
  Slurm metadata, and output checksum.
- `myquota-before.txt` and `myquota-after.txt`: raw allocation snapshots around
  the submitted workload.
- `module-list.txt`: modules and versions active on the compute node.

Preserve the Slurm stdout/stderr files, preflight receipt, and
`.venv-hpc/anuma-environment.json` beside those outputs before any scientific
review. A completed smoke test proves only that the pinned 240 m pipeline ran
under the declared resources. It does not validate the scientific model, prove
20 m agreement, or authorize another job.

After the job exits, use documented MeluXina accounting commands to record
elapsed time, CPU time, and peak memory. Do not schedule a 20 m run until the
240 m outputs and accounting record have been reviewed.

## Completed precursor: 240 m clip-and-weight Monte Carlo

The 500-draw precursor exists as job `5332991`. Do not rerun it. Preserve and
review its manifests, `sacct`, and delayed before/after `myquota` captures. The
implemented study compares three declared policies from `config/layers.yaml`:

1. `legacy_global_2_98`: the former global 2nd/98th percentile policy,
   retained only as a comparison;
2. `decided_per_layer`: illumination valid-pixel min–max, slope min–max only
   after the 8° hard mask, hydrogen 2nd/98th clipping, and Earth visibility
   2nd/98th clipping;
3. `all_min_max`: valid-pixel min–max for every layer.

The same seeded weight draws are paired across policies. Configuration fixes
the seed (`20261012`), pilot/full counts (8/500), weight ranges around the
documented 0.5/0.3/0.2 demo weights, and normalization-to-one. Outputs are:

- top-ten membership count and frequency per site and policy;
- exact-pixel winner frequencies and most-frequent-winner stability;
- paired legacy-clipped versus decided-unclipped winner-class changes, where
  a class is defined by the configured 10 km radius;
- per-draw JSON Lines and a checksum output manifest.

No latitude or site is hard-coded as the expected winner. The study refuses
synthetic input, missing ingest provenance, and any illumination ingest whose
`illumination_affine_revision` is not `pds3-pixel-center-v2`.

### Guarded path retained for provenance only

The commands below document the precursor guardrails; they do not authorize or
request a rerun of job `5332991`.

### Tiny pilot

Create a fresh preflight receipt from the compute allocation:

```bash
export ANUMA_JOB_PROFILE=monte-carlo-pilot
export ANUMA_QOS=test
.venv-hpc/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-mc-pilot-$(date -u +%Y%m%dT%H%M%SZ).json"
export ANUMA_PREFLIGHT_RECEIPT="$HOME/<the-new-pilot-receipt>.json"
bash hpc/submit-monte-carlo.sh pilot
```

The wrapper repeats one node, four CPU cores, 16 GiB, `test`, and 30 minutes on
the `sbatch` command line. It captures `myquota` before submission; the job
captures it after every started workload and deletes job-local scratch.

### Bounded full draw

Only after reviewing a successful pilot, create a new receipt:

```bash
export ANUMA_JOB_PROFILE=monte-carlo-full
export ANUMA_QOS=default
.venv-hpc/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-mc-full-$(date -u +%Y%m%dT%H%M%SZ).json"
export ANUMA_PREFLIGHT_RECEIPT="$HOME/<the-new-full-receipt>.json"
bash hpc/submit-monte-carlo.sh full
```

The full job is still one node and four CPU cores, with 32 GiB and a four-hour
walltime (at most four node-hours), far below the held allocation. Both wrapper
and job reject a mismatched QOS, mode, walltime, memory, receipt, manifest,
commit, or affine provenance. Neither path downloads data or submits itself.
