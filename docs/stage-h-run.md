# Stage H run — implementation notes and commands

Not affiliated with NASA, ESA or ispace. Screening only; not a landing
recommendation. This page records how the frozen
[stage-h-plan.md](stage-h-plan.md) is implemented and how to launch it. It
changes no frozen value and records no MeluXina result. Status:
**implemented, not run on MeluXina**.

## Code

| File | Role |
| --- | --- |
| `anuma/stage_h.py` | Driver (`python -m anuma.stage_h --mode pilot\|production\|checks-file`): pins, reproduction check, gated Monte Carlo, gated noise, labels, projection, check file |
| `anuma/stage_h_gate.py` | Pinned digests, Stage F record check, receipt pin; standard library only |
| `anuma/hpc_monte_carlo.py` | `terrain_fraction` / `--terrain-fraction` + `--terrain-sidecar`: the same study with the gate on; summary `ranking_basis` |
| `anuma/stage_d.py` | `require_gated_config`, `recompute_gated_scope`, `run_gated_noise`; `run_stage_d` and the Stage D config are unchanged in behaviour |
| `hpc/preflight.py` | Profiles `stage-h-pilot`, `stage-h` (compute-only preflight; no `myquota` needed there) |
| `hpc/submit-stage-h.sh`, `hpc/slurm/stage-h-{pilot,production}.sbatch` | The only sbatch path; caps and receipt re-checked in the job |
| `anuma/api.py` | `/stability.json?basis=`, `/checks/stage-d-noise.json?basis=`, `ranking_basis.studies_match` |
| `tests/test_stage_h.py` | Synthetic arrays and GeoTIFFs; pilot and production end to end |

Environment: the job imports `anuma.score` (scipy at call time), rasterio and
pyproj, so it runs on `.venv-hpc-ingest` with `env/release/2024.1` and
`Python/3.12.3-GCCcore-13.3.0`, like Stages B, D and F. The login-node
wrapper imports only `anuma.stage_h_gate` → `anuma.stage_d_gate` (standard
library) on `.venv-hpc-sun`. See the venv lesson in
[stage-d-run.md](stage-d-run.md).

## Commands (MeluXina)

Push the reviewed branch, check it out clean in
`/project/home/p201619/ANUMA_` (preflight and the wrapper refuse a dirty
tree or a different commit). Then on the login node:

```bash
cd /project/home/p201619/ANUMA_
export ANUMA_ACCOUNT=p201619
export ANUMA_DATA_ROOT=/project/home/p201619/ANUMA_
export ANUMA_INPUT_MANIFEST=$ANUMA_DATA_ROOT/data/input-manifest.json
export ANUMA_OUTPUT_ROOT=$ANUMA_DATA_ROOT/out/hpc
export ANUMA_SCRATCH_DIR=/project/scratch/p201619
export ANUMA_ALLOCATION_EVIDENCE_FILE=...   # refreshed from myquota on the login node, < 24 h
export ANUMA_STORAGE_EVIDENCE_FILE=...      # same
# Defaults, shown for clarity:
export ANUMA_STAGE_D_RAW_DIR=$ANUMA_DATA_ROOT/data/raw
export ANUMA_DEM=$ANUMA_STAGE_D_RAW_DIR/LDEM_80S_20MPP_ADJ.TIF
export ANUMA_STAGE_B_SLOPE=$ANUMA_DATA_ROOT/out/hpc/stage-b-production-5334425/slope-20m.npy
export ANUMA_STAGE_F_DIR=$ANUMA_DATA_ROOT/out/hpc/stage-f-production-5345446
export ANUMA_REFERENCE_DRAWS=$ANUMA_DATA_ROOT/out/hpc/monte-carlo-full-5332991/draws.jsonl
myquota   # before; keep the output
```

Pilot (preflight in a short `cpudev` allocation, then submit from the login
node):

```bash
salloc -A p201619 -p cpu --reservation=cpudev -q dev -N 1 -t 00:30:00
export ANUMA_JOB_PROFILE=stage-h-pilot ANUMA_QOS=test
module load env/release/2024.1 Python/3.12.3-GCCcore-13.3.0
srun --export=ALL -N1 -n1 -c 4 .venv-hpc-ingest/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-stage-h-pilot-$(date -u +%Y%m%dT%H%M%SZ).json"
exit   # leave the cpudev allocation
export ANUMA_PREFLIGHT_RECEIPT="$HOME/<the-new-pilot-receipt>.json"
bash hpc/submit-stage-h.sh pilot
```

Pass: `out/hpc/stage-h-pilot-<job>/pilot-measurement.json` `passed: true`,
i.e. `checks.reference_draws_identical`, `checks.monte_carlo_passed`,
`checks.noise_passed` and `checks.projection_fits`; read
`projection.total_hours`.

The Monte Carlo projection is deliberately conservative (whole pilot time,
scoring preparation included, charged per pilot draw × 500) and stays as
frozen. If the pilot fails **only** on `projection.monte_carlo_fits_share`,
do not rerun or edit the code: write a dated plan revision (for example a
projection that separates the fixed preparation time), have it reviewed,
then run a new pilot.

Production (only after the pilot passes and is reviewed):

```bash
salloc -A p201619 -p cpu --reservation=cpudev -q dev -N 1 -t 00:30:00
export ANUMA_JOB_PROFILE=stage-h ANUMA_QOS=default
module load env/release/2024.1 Python/3.12.3-GCCcore-13.3.0
srun --export=ALL -N1 -n1 -c 4 .venv-hpc-ingest/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-stage-h-$(date -u +%Y%m%dT%H%M%SZ).json"
exit
export ANUMA_PREFLIGHT_RECEIPT="$HOME/<the-new-production-receipt>.json"
bash hpc/submit-stage-h.sh production
```

After each job: `myquota` on the login node and
`sacct -j <job> --format=JobID,Elapsed,State,AllocCPUS,TotalCPU,MaxRSS`.
Exit codes: 2 a refused input or pin, 3 a failed check, 4 a pilot projection
that does not fit.

## Installing the results in the app (after a passed production run)

```bash
job=<production job id>
mkdir -p out/hpc/stage-h-production-$job out/hpc/stage-h-monte-carlo
scp -r -P 8822 <user>@login.lxp.lu:/project/home/p201619/ANUMA_/out/hpc/stage-h-production-$job/. \
  out/hpc/stage-h-production-$job/
sha256sum out/hpc/stage-h-production-$job/monte-carlo/study-summary.json  # compare with output-manifest.json
cp out/hpc/stage-h-production-$job/monte-carlo/study-summary.json out/hpc/stage-h-monte-carlo/
python -m anuma.stage_h --mode checks-file --run-dir out/hpc/stage-h-production-$job \
  --out data/checks/stage-h-noise.json --commit <commit the job ran>
```

Commit `data/checks/stage-h-noise.json` with a `docs/stage-h-result.md`.
Restart the API: with the gate applied by default, `/score` then returns
`ranking_basis.studies_match: true`, `/stability.json` the gated
frequencies and `/checks/stage-d-noise.json` the gated verdicts; `?basis=slope_only`
still serves jobs `5332991` and `5345050`.

## Cost

Caps: pilot 0.5 + production 1.0 = 1.5 node-hours. Expected: pilot a few
minutes (hashing about 18 GB, 8 + 8 weight draws, 16 noise draws, each
twice); production about 0.2–0.35 node-hours (500 × 3 gated rankings on 32
threads, 4,800 noise draws serial then parallel). Laptop, 15 October 2026:
8 gated weight draws on the real arrays took 16 s on 4 threads (conservative
500-draw projection 0.27 h); the gate-off draws 0–7 equalled job
`5332991`'s records exactly.
