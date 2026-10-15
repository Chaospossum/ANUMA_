# Stage F run — implementation notes and commands

Not affiliated with NASA, ESA or ispace. Screening only; not a landing
recommendation. This page records how the frozen
[stage-f-plan.md](stage-f-plan.md) is implemented and how to launch it. It
changes no frozen value and records no MeluXina result. Status:
**implemented, not run on MeluXina**.

## Code

| File | Role |
| --- | --- |
| `anuma/stage_f.py` | Disk geometry, exact counts, bands, acceptance, pilot/production driver and laptop modes (`python -m anuma.stage_f`) |
| `anuma/stage_f_gate.py` | Pinned digests (from `stage_d_gate`), receipt pin and checks; standard library only |
| `hpc/preflight.py` | Profiles `stage-f-pilot` and `stage-f`; hashes the DEM and slope (run it on `cpudev`; no `myquota` needed there) |
| `hpc/submit-stage-f.sh` | The only sbatch path; checks the receipt on the login node without reading rasters |
| `hpc/slurm/stage-f-pilot.sbatch`, `hpc/slurm/stage-f-production.sbatch` | Re-check caps and receipt, then `srun` the module |
| `anuma/score.py`, `anuma/api.py` | The gate (`terrain_gate`, `hard_mask(..., terrain_fraction=)`), `/score` and `/layers` fields |
| `tests/test_stage_f.py`, `tests/test_terrain_gate.py` | Synthetic arrays; one test uses the laptop DEM when present |

## Laptop modes (testing only)

```bash
# Acceptance values (Stage B ranks 1-10, Stage D 8 sites) from the local DEM:
python -m anuma.stage_f --mode local-check --dem data/raw/LDEM_80S_20MPP_ADJ.TIF
# Any 240 m window, Horn slope from the DEM via anuma.slope20:
python -m anuma.stage_f --mode local --dem data/raw/LDEM_80S_20MPP_ADJ.TIF \
  --rows 1300:1340 --cols 1200:1240 --output-dir /tmp/stage-f-window
```

`local` writes `*-window.npy` files with no sidecar, so the app never uses
them as the gate. On 15 October 2026 `local-check` passed on the laptop DEM
(sha256 `09b7ca80…`, the pinned one): all eight Stage D counts equal, all
ten Stage B values within two decimals, Stage D's own window path equal.

The pilot and production drivers were also run end to end on the laptop
against a `slope-20m.npy` recomputed locally with `anuma.slope20` from the
pinned DEM (4 workers; the slope digest pin was overridden for that dry run
only). Both passed every check (production: 4,096 brute-force cells, all
equal). The local slope's sha256 was `496f554f…`, not the pinned
`08b2e17b…`; the difference is libm 1-ULP rounding in the float64 Horn
slope before the cast to float32, and every count was identical. That slope
and its outputs were deleted on 15 October 2026 and must never be installed:
the sidecar check now refuses any layer whose inputs are not the pinned
digests or that has no Slurm job id. The official run uses the pinned
`08b2e17b…` slope on MeluXina.

## Commands (MeluXina)

The branch must be on MeluXina at a clean, reviewed commit first.
Set, as for Stage D ([stage-d-run.md](stage-d-run.md),
[meluxina-execution.md](meluxina-execution.md)):

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
export ANUMA_DEM=$ANUMA_DATA_ROOT/data/raw/LDEM_80S_20MPP_ADJ.TIF
export ANUMA_STAGE_B_SLOPE=$ANUMA_DATA_ROOT/out/hpc/stage-b-production-5334425/slope-20m.npy
myquota   # before; keep the output
```

Pilot (preflight in a short `cpudev` allocation, then submit from the login
node):

```bash
salloc -A p201619 -p cpu --reservation=cpudev -q dev -N 1 -t 00:30:00
export ANUMA_JOB_PROFILE=stage-f-pilot ANUMA_QOS=test
module load env/release/2024.1 Python/3.12.3-GCCcore-13.3.0
srun --export=ALL -N1 -n1 -c 4 .venv-hpc-ingest/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-stage-f-pilot-$(date -u +%Y%m%dT%H%M%SZ).json"
exit   # leave the cpudev allocation
export ANUMA_PREFLIGHT_RECEIPT="$HOME/<the-new-pilot-receipt>.json"
bash hpc/submit-stage-f.sh pilot
```

Pass conditions in `out/hpc/stage-f-pilot-<job>/pilot-measurement.json`:
`passed` true, i.e. `checks.stage_b_reproduced`, `checks.stage_d_reproduced`,
`checks.stage_d_path.all_equal`, `checks.brute_force.all_equal`,
`checks.serial_parallel_identical` and `checks.projection_fits`.

Production (only after the pilot passes and is reviewed):

```bash
salloc -A p201619 -p cpu --reservation=cpudev -q dev -N 1 -t 00:30:00
export ANUMA_JOB_PROFILE=stage-f ANUMA_QOS=default
module load env/release/2024.1 Python/3.12.3-GCCcore-13.3.0
srun --export=ALL -N1 -n1 -c 4 .venv-hpc-ingest/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-stage-f-$(date -u +%Y%m%dT%H%M%SZ).json"
exit
export ANUMA_PREFLIGHT_RECEIPT="$HOME/<the-new-production-receipt>.json"
bash hpc/submit-stage-f.sh production
```

After each job: `myquota` on the login node (the compute node has none; the
job's `myquota-after.txt` then says `myquota_unavailable_on_compute_node`)
and `sacct -j <job> --format=JobID,Elapsed,State,AllocCPUS,TotalCPU,MaxRSS`.

Exit codes: 2 a refused input or pin, 3 a failed acceptance check, 4 a
pilot projection that does not fit.

## Installing the layer in the app

Copy only after production passed (`stage-f-summary.json` `passed: true`,
sidecar `coverage: "full_cap"`):

```bash
scp -P 8822 <user>@login.lxp.lu:/project/home/p201619/ANUMA_/out/hpc/stage-f-production-<job>/terrain_1km_fraction.{npy,json} data/grid/
sha256sum data/grid/terrain_1km_fraction.npy   # compare with output-manifest.json
```

Restart the API. `/layers` then shows `terrain_gate.available: true` and the
default `/score` returns `terrain_gate: "applied"`.

## Cost

Laptop, 15 October 2026: the whole cap from the local DEM (Horn slope
recomputed per band, 4 workers) took 90 s; one 32-row band of counts takes
about 0.4 s on one core. On MeluXina the slope is read, not recomputed, and
hashing about 6.4 GB dominates: expect a few minutes, well under 0.1
node-hours per job, against caps of 0.5 (pilot) and 1.0 (production).
