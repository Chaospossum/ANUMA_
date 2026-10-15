# Stage E plan — 240 m daily lit hours for date-range averages

Not affiliated with NASA, ESA or ispace. Modeled illumination is ANUMA's
model; `avgvisib_65s_240m_201608` remains the measured layer. This plan
authorizes no submission and records no result. Status: **implemented, not
run**.

## Purpose

The web GUI should show the average lit time over any mission date range a
user picks, without a new HPC job per range. Stage E precomputes, for every
240 m pixel of the 80–90°S cap, the number of sunlit hours in each UTC day.
Any whole-day range is then averaged from two monthly cumulative slices plus
at most the daily slices of the two partial months at its ends.

The mission case remains open. The precomputed span is not a mission
interval, and no mission-duration percentage is quoted from it.

## Method (reused, not new)

- Horizon: the Stage A 360-azimuth cube `terrain-horizon.npy` from job
  `5334265` (passed, RMSE 0.0198 at the frozen 0.05 gate). It is read, not
  recomputed. The job refuses to start unless the cube's SHA-256 equals the
  digest in that job's `output-manifest.json`
  (`a0cdd18f…e5e3b4`, 9,246,464,768 bytes) and the Stage A
  `validation-report.json` still shows a pass at `stop_rmse` 0.05.
- Sun direction: `anuma.sun.sun_vectors_from_spice`, the same DE440 path and
  kernel pin (`/project/home/p201619/spice-kernels/kernel-manifest.json`).
  Every kernel is SHA-256 verified before `spiceypy` is imported; a missing
  role or a digest mismatch refuses the run.
- Lit rule: `anuma.sun._lit_mask`, the one helper Stage A's accumulator now
  also calls. A timestep is lit when any fraction of the ±0.27° solar disc is
  above the horizon.
- Epochs: hourly, `HH:00:00Z` for `HH = 00…23` in each UTC day. Day *d*'s
  value is the count of lit epochs among those 24, so it is 0–24 (`uint8`).
- Execution: the Stage A row-tile fork pool shares the horizon cube with 128
  workers; each dispatch evaluates 16 days (384 epochs).

## Inputs

| Input | Where |
| --- | --- |
| Horizon cube | `$ANUMA_STAGE_A_DIR/terrain-horizon.npy` (override with `ANUMA_HORIZON_CUBE`) |
| Stage A record | `$ANUMA_STAGE_A_DIR/{validation-report,output-manifest}.json`, `percent-time-lit.npy` |
| Grid | `grid.json` via the checksummed `data/input-manifest.json` |
| Kernels | the MeluXina kernel manifest above |
| Config | `config/daily-lit.yaml` (period, pilot, fills, caps, acceptance) |

`ANUMA_STAGE_A_DIR` defaults to
`/project/home/p201619/ANUMA_/out/hpc/sun-validation-production-5334265`.

## Period and SPICE coverage

Default production period: **2026-01-01T00Z to 2044-09-01T00Z (exclusive)**,
6,818 days, 163,632 hourly epochs. 2026-01-01 + 18.6 × 365.25 days is
2044-08-07T15:36Z; the end is rounded up to the next month start so the
monthly sums end cleanly. The config refuses a production span shorter than
18.6 × 365.25 days. The start is ANUMA's choice, not a mission date.

Coverage has **not** been read from the MeluXina kernel files yet; no one
has logged in for this stage. What is known:

- NAIF documents `de440.bsp` and `moon_pa_de440_200625.bpc` as covering
  roughly 1550 to 2650. That figure is quoted from memory of NAIF's kernel
  comments. It was not re-read for this plan and is not a check of the
  staged files.
- Stage A job `5334265` evaluated 2000-01-01 to 2018-08 with these kernels,
  and the operator check covered 2029. Neither reaches 2044.
- `moon_de440_250416.tf` (frames) and `naif0012.tls` (leap seconds) are text
  kernels with no time window. `naif0012.tls` lists leap seconds through
  2017-01-01. A leap second added later would shift UTC epochs by 1 s, which
  is negligible at 1 h cadence.

The job therefore reads coverage from the kernels themselves
(`anuma.daily_lit.spice_coverage`): SPK intervals for bodies 10, 3 and 301
(Sun, Earth–Moon barycentre, Moon), and binary-PCK intervals for every frame
class in the lunar orientation kernel. Preflight, and the job again, refuse
the period unless one interval of every binary kernel contains the first and
last epoch. The decision rule:

1. If coverage contains 2026-01-01T00Z to 2044-08-31T23Z, run the default
   period.
2. If not, do not submit. Shorten `period.end_utc` to the last whole month
   inside coverage, lower `minimum_span_years` to match, record the coverage
   output here, and get the change reviewed before a new preflight.

Read coverage on a `cpudev` compute allocation (it checksums the kernels, so
not on a login node):

```bash
.venv-hpc-sun/bin/python -m anuma.daily_lit --mode coverage \
  --kernel-manifest /project/home/p201619/spice-kernels/kernel-manifest.json \
  | tee out/hpc/daily-lit-spice-coverage.json
```

The command exits with status 5 when the default period is not covered.

## Outputs

One directory per job, `out/hpc/daily-lit-{pilot,production}-<job>/`:

| File | Content |
| --- | --- |
| `daily/lit-hours-YYYY.npy` | (days in that year of the period, 2534, 2534) `uint8`, lit hours per UTC day |
| `daily/lit-hours-YYYY.json` | completion record (shape, bytes, SHA-256, run key); written last |
| `cumulative-monthly.npy` | (225, 2534, 2534) `uint32`; slice *k* = lit hours over [start, boundary *k*) |
| `mean-lit-fraction.npy` | `float32` full-period mean lit fraction |
| `valid-mask.npy` | `bool`, same rule as Stage A |
| `daily-lit-index.json` | period, chunk files, month boundaries, fill values |
| `pilot-measurement.json` / `daily-lit-report.json` | timings, checks, coverage, inputs |
| `output-manifest.json` | the report plus SHA-256 and bytes of every output |
| `run-config.json`, `progress.log`, `myquota-*-<job>.txt`, `module-list-<job>.txt` | resume key and accounting |

Fill values for out-of-cap pixels (NaN in Stage A's `percent-time-lit.npy`):
**255** in the daily files, **4294967295** in the cumulative file, **NaN**
in `mean-lit-fraction.npy` and in reader results.

`.npy` memory-mapped files are used because `zarr`/`numcodecs` are not
installed locally or in the HPC locks.

### Reader

```python
from anuma.daily_lit import DailyLitCube, lit_hours_mean

cube = DailyLitCube("out/hpc/daily-lit-production-<job>")
hours = cube.lit_hours_mean("2031-03-01", "2031-05-15")  # NaN out of cap
fraction = hours / 24.0
window = cube.lit_hours_mean("2031-03-01", "2031-05-15", (slice(1200, 1400), slice(1100, 1300)))
```

Ranges are whole UTC days, `[start, end)`, inside the computed period.
Tests check the result against a brute-force mean of the daily slices.

## Resources and cost

| Item | Pilot | Production |
| --- | --- | --- |
| QOS | `test` | `default` |
| Walltime cap | 00:30 | 03:00 |
| Node-hours cap | 0.5 | 3.0 |
| Node | 1 × 128 CPUs, `--mem=240G` | same |

Pilot plus production is at most **3.5 node-hours**. This is outside the
165-hour A–D caps. It must be drawn from the 55 held node-hours by an
explicit, reviewed decision before submission. The study plan says the hold
stays unless a reviewed plan reallocates it.

Expected cost, scaled from Stage A rather than measured for this stage: Stage A's
`progress.log` shows 162,792 epochs in 3,956 s (0.0243 s/epoch, 128
workers). 163,632 epochs × 0.0243 s ≈ **1.1 h** of evaluation. Add about
0.1–0.2 h to load and hash the 9.2 GB cube and to write, hash and re-read the
daily files. Expect **≈1.3 node-hours**. The pilot measures this and refuses
production (exit 4) if its projection exceeds 3.0.

Storage (production): daily files 6,818 × 6,421,156 B = **43.8 GB**;
cumulative 225 × 25.7 MB = **5.8 GB**; total ≈ **49.6 GB** on project
storage. Pilot ≈ 0.1 GB. Peak RAM is expected below 30 GB: the cube
(9.25 GB, shared copy-on-write with the forked workers), one year of daily
counts (≤ 2.35 GB), and 128 tile slices of about 72 MB.

Checkpointing: each calendar year is a resume unit. Its array is written
atomically, then its JSON record. A timeout loses at most the year in
progress, about 4 minutes of evaluation. A resumed job skips years whose
record matches the run key: horizon digest, kernel-manifest digest, config
digest, and period. Any mismatch refuses the run. The pilot never resumes.

## Commands (MeluXina, not run)

Preflight on a `cpudev` allocation, as for Stages A and B. Evidence files,
`ANUMA_DATA_ROOT`, `ANUMA_INPUT_MANIFEST`, `ANUMA_OUTPUT_ROOT=/project/home/p201619/ANUMA_/out/hpc`
and `ANUMA_SCRATCH_DIR` are set as in
[meluxina-execution.md](meluxina-execution.md).

Pilot:

```bash
export ANUMA_JOB_PROFILE=daily-lit-pilot
export ANUMA_QOS=test
export ANUMA_ACCOUNT=p201619
export ANUMA_KERNEL_MANIFEST=/project/home/p201619/spice-kernels/kernel-manifest.json
export ANUMA_STAGE_A_DIR=/project/home/p201619/ANUMA_/out/hpc/sun-validation-production-5334265
.venv-hpc-sun/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-daily-lit-pilot-$(date -u +%Y%m%dT%H%M%SZ).json"
export ANUMA_PREFLIGHT_RECEIPT="$HOME/<the-new-pilot-receipt>.json"
bash hpc/submit-daily-lit.sh pilot
```

Production (only after the pilot checks below pass and the 3.5 node-hours
are approved from the hold):

```bash
export ANUMA_JOB_PROFILE=daily-lit
export ANUMA_QOS=default
.venv-hpc-sun/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-daily-lit-$(date -u +%Y%m%dT%H%M%SZ).json"
export ANUMA_PREFLIGHT_RECEIPT="$HOME/<the-new-production-receipt>.json"
bash hpc/submit-daily-lit.sh production
```

After a production timeout, run a new preflight and set
`ANUMA_DAILY_LIT_RESUME_DIR=/project/home/p201619/ANUMA_/out/hpc/daily-lit-production-<old job>`
before `submit-daily-lit.sh production`. The job appends to
`resume-history.txt` and skips finished years.

The job uses `.venv-hpc-sun` as Stage A did: OS Python 3.12, default release
modules, no Python module loaded. `ANUMA_LOAD_PYTHON_MODULE=1` loads
`env/release/2024.1` and `Python/3.12.3-GCCcore-13.3.0`. Use it only with an
interpreter built on that module. `srun --export=ALL` is always used.

## Acceptance checks

These are fixed before any run and are not tuned afterwards.

Pilot (7 days from 2026-01-01, 168 epochs, full grid). All must hold. The
job exits 4 when the projection does not fit, otherwise 3 when any other
check fails:

1. Every valid pixel holds 0–24 on every day (`checks.lit_hours_within_0_24`).
2. Out-of-cap pixels hold 255 on every day (`checks.fill_matches_valid_mask`),
   and that mask equals the NaN mask of Stage A `percent-time-lit.npy`
   (`checks.fill_matches_stage_a_nan`).
3. On every 32nd row, the 7-day sum from the reader equals, bit for bit, the
   Stage A accumulator `_advance_sunlight_block` run on the same epochs
   (`equivalence.identical`).
4. The projected production wall time is ≤ 3.0 node-hours
   (`projection.fits_production_cap`). The report also gives the pilot's
   s/epoch next to Stage A's 0.0243 s/epoch.

A 7-day pilot cannot test the long-term mean, because south-polar
illumination is strongly seasonal. That is the production check below.

Production:

1. Checks 1–2 above, over all 6,818 days.
2. The full-period `mean-lit-fraction.npy` compared with Stage A
   `percent-time-lit.npy / 100` over all valid pixels: RMSE ≤ **0.02**
   (`stage_a_comparison.within_tolerance`). Bias and max |Δ| are reported too.
   The two are different 18.6-year windows (Stage A: 2000-01-01 onward). A
   miss flags the run for review (exit 3); it is not a gate on other stages.
   It does not overwrite anything.
3. Review: spot-check `lit_hours_mean` for a few ranges against
   `cube.daily(...).mean(axis=0)` and record `sacct` with before/after
   `myquota`.

## What it does not show

- It is not a measurement. It inherits every Stage A caveat. The rim band
  is too bright because rays leaving the 80°S DEM see no terrain. Near the
  pole and on the brightest ridges the residuals are larger.
- It is not a mission result. No mission dates exist; the span is a
  precompute range, and a GUI average over a user's range is a scenario.
- Hourly instantaneous samples only. Lit spells shorter than an hour, and
  partial-hour sunrise or sunset, are not resolved. A day value says nothing
  about which hours were lit or about continuous darkness. Longest-darkness
  layers need their own run.
- 240 m only. It does not represent the 20 m terrain of Stage B or the
  site-window Stage C.
- Earth visibility, temperature, and solar-panel geometry (mast height,
  panel tilt) are not modeled. The lit rule is "any part of the disc", not
  usable power.
