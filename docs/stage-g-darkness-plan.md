# Stage G plan — longest continuous dark and lit periods (240 m)

Not affiliated with NASA, ESA or ispace. Modeled illumination is ANUMA's
model; `avgvisib_65s_240m_201608` remains the measured layer. This plan
authorizes no submission and records no result. Status: **implemented, not
run**.

## Purpose

At the ispace meeting an engineer said mean sunlight matters less for
battery and heater sizing than **the longest continuous dark period**, and,
for power budgets, **the longest continuous sunlit period**. Stage E stored
sunlit hours per UTC day. Day sums cannot give an exact run length, because
runs start and end inside days. Stage G replays Stage E's hourly epochs and
keeps run lengths per pixel. It also stores enough per month to give the
**exact** longest dark run for any month-aligned window that the GUI month
picker can choose.

The mission case remains open. The period is a precompute span, not a
mission interval.

## Method (reused, not new)

Everything that decides lit or unlit is Stage E's code path, unchanged:

- **Epochs:** hourly `HH:00:00Z`, 2026-01-01T00Z to 2044-09-01T00Z
  (exclusive): 6,818 days, **163,632 epochs**. The period is read from
  `config/daily-lit.yaml`. The job refuses to run unless that file's SHA-256
  equals `daily_lit_config_sha256` in the Stage E run key
  (`29c27f85…6539`, job `5342908`).
- **Horizon:** the Stage A cube `terrain-horizon.npy` (job `5334265`,
  sha256 `a0cdd18f…e5e3b4`). It is verified against the Stage A manifest, and
  it must equal the `horizon_sha256` in Stage E's run key.
- **Sun:** `sun_vectors_from_spice` with the same kernel manifest. The
  manifest digest must equal Stage E's. There is one SPICE call per calendar
  year, as in Stage E, and coverage is checked by `require_spice_coverage`.
  The SHA-256 of `config/sun.yaml` (disc radius, azimuth origin, frame,
  aberration correction, kernel roles) is pinned in the receipt and the run
  key as `sun_config_sha256`.
- **Lit rule:** `anuma.sun._lit_mask`. An epoch is lit when any fraction of
  the ±0.27° disc is above the local horizon.
- **Execution:** the same fork pool over row tiles (`_start_tile_pool`) with
  128 workers. Each dispatch evaluates one calendar month (672–744 epochs).

## Definitions

Hour index `h` is the epoch `2026-01-01T00:00Z + h hours`. The sample at
`h` represents that instant only.

| Term | Definition |
| --- | --- |
| Run | Maximal sequence of consecutive hourly samples of one kind (dark or lit). N samples = **N hours**. The physical spell lasted between N − 1 and N + 1 hours (1 h sampling). |
| Longest dark/lit run | Maximum run length over the period, with runs clipped to the period. |
| Start hour | Hour index of the first sample of that run. **Ties go to the earliest run.** |
| Censored | Bit 1: the run starts at hour 0 (it may have begun before 2026). Bit 2: the run ends at the last hour (it may continue after 2044-08-31T23Z). The value is still reported and is a lower bound for that run. |
| Interior run | The longest run that touches neither end of the period, so it is fully observed. It is 0 if no such run exists. |
| Month triple | For one block (month or year): `lead` = dark hours from the block start to the first lit sample; `trail` = dark hours after the last lit sample; `best` = longest dark run inside the block, clipped to it. The block is all dark exactly where `lead == hours`. |

Degenerate pixels:

- **Never lit** (lit total 0): longest dark = 163,632 h, start 0, flags 3,
  interior 0. Longest lit = 0, start `NO_RUN` (4294967295), flags 0.
- **Never dark:** the same with dark and lit swapped.
- **Out of cap** (NaN in the Stage A layers): fill **4294967295** (uint32),
  **65535** (uint16 month and year files), **255** (flags). The reader
  returns −1.

## Streaming kernel (O(1) state per pixel)

The state per pixel is: current run kind, current run start, best dark and
lit lengths and their starts, interior dark and lit, and lit total. That is
33 B per pixel, 212 MB for the full grid, held in a shared anonymous mapping
that the fork workers update in place, each worker on its own rows. For each
epoch, only pixels whose state changed (`lit != run_lit`, found with
`flatnonzero`) close a run. Closing updates best/start (with a strict `>`,
so the earliest run wins ties), the interior value (when the run start is
> 0), and the month accumulators. At the month end, `trail` is the clipped
open dark run, and `lead` is the month length where no lit sample was seen.
At the period end, the open runs are closed as runs that touch the end.
These can set the longest run but never the interior one.

Per-day lit counts are accumulated in the same pass, and they are the
bridge to Stage E (see the acceptance checks).

Local benchmark (20 × 2534 tile, 360 azimuths, 11 % of pixels changing per
epoch): `_lit_mask` takes 6.6 ms per epoch and the streaming update 0.53 ms.
The overhead is about **8 %**.

## Stitching month windows exactly

A block summary is `S = (n, lead, trail, best)`. Two adjacent blocks combine
as

```
lead  = lead_A            if lead_A < n_A  else n_A + lead_B
trail = trail_B           if trail_B < n_B else n_B + trail_A
best  = max(best_A, best_B, trail_A + lead_B)
n     = n_A + n_B
```

`combine` is associative, so blocks may be grouped any way. This includes
months grouped into calendar years. It is exact because every dark run in
`A·B` is either inside A, inside B, or the one run that crosses the seam,
whose length is `trail_A + lead_B`. When A (or B) is entirely dark, that
seam run continues into the neighbouring block's lead or trail, which is
what the `lead`/`trail` update carries. The reader stitches the window
`[start, end)` from months up to the first year boundary, then whole
calendar years, then months. That is at most 11 + 19 + 11 blocks. A window
equal to the whole period reads the full-period map directly.

Window runs are clipped to the window. `edge_censored` marks pixels whose
longest run equals the window's lead or trail: that run may continue
outside the window.

## Outputs

`out/hpc/darkness-{pilot,production}-<job>/`:

| File | Type | Production size |
| --- | --- | ---: |
| `longest-{dark,lit}-hours.npy` | (2534, 2534) uint32 | 2 × 25.7 MB |
| `longest-{dark,lit}-start-hour.npy` | uint32 hour index | 2 × 25.7 MB |
| `longest-{dark,lit}-censored.npy` | uint8 bit flags | 2 × 6.4 MB |
| `longest-{dark,lit}-interior-hours.npy` | uint32 | 2 × 25.7 MB |
| `monthly/dark-{lead,trail,best}.npy` | (224, 2534, 2534) uint16 | 3 × 2.88 GB = **8.63 GB** |
| `yearly/dark-{lead,trail,best}.npy` | (19, 2534, 2534) uint16, stitched from months | 3 × 244 MB = **0.73 GB** |
| `valid-mask.npy`, `darkness-index.json` | mask; period, month/year tables, fills, definitions | 6.4 MB |
| `pilot-measurement.json` / `darkness-report.json`, `output-manifest.json`, `run-config.json`, `progress.log`, `myquota-*`, `module-list-*` | records | small |

Production total ≈ **9.5 GB** on project storage (pilot ≈ 0.3 GB). Month
values fit uint16 (≤ 744 h, year ≤ 8,784 h); full-period values need uint32
(up to 163,632 h).

Storing only the 5,040,704 valid pixels would save about 21 % (1.8 GB of the
monthly files). It was rejected, because the reader would lose direct `(row,
col)` memory-mapped slicing. No compression library (`zarr`/`numcodecs`) is
in the locks.

The lit-run monthly triples are **not** stored. The longest lit run is
available for the full period only. Adding it would cost another 8.6 GB.

### Laptop copy

The laptop has 310 GB free and already holds Stage E's 5.8 GB cumulative
file. Copy the **period maps, yearly files, index, valid mask, report and
manifest (≈ 0.9 GB)** in every case. Copy the **monthly files (8.63 GB)**
as well, so that every month window is exact. Without them the reader
answers only windows on calendar-year boundaries (`FileNotFoundError`
otherwise). The reader is `DarknessCube`:

```python
from anuma.darkness import DarknessCube

cube = DarknessCube("out/hpc/darkness-production-<job>")
hours = cube.longest_dark_hours("2031-03", "2031-06")    # Mar–May 2031, int64, -1 out of cap
info = cube.window_summary("2031-03", "2031-06")          # + lead, trail, edge_censored
dark = cube.period("dark_hours"); flags = cube.period("dark_flags")
lit = cube.period("lit_hours")
```

`end` is exclusive: `"2031-06"` means up to 2031-06-01T00Z.

## Planned API (not wired yet)

Another agent is editing `static/` and `anuma/api.py`, so Stage G does not
touch them. The proposed API:

- `GET /layer/longest_dark.png?start=YYYY-MM&end=YYYY-MM`: colour map of
  `longest_dark_hours` in days (hours / 24) for the month window. With no
  parameters it shows the full period. Censored pixels are hatched or listed
  in the legend. Out-of-cap pixels are transparent.
- `GET /layer/longest_lit.png`: full period only.
- `/score` per site: `longest_dark_hours` (and days) for the selected
  window, plus its `edge_censored` flag. For the full period it also returns
  `longest_dark_start_utc` (from the start hour), `longest_lit_hours` and the
  censoring flags.
- Use one `DarknessCube` per process. Month memmaps are opened lazily. A
  12-month window reads 3 × 12 slices (≈ 0.46 GB) or one year slice set.

## Inputs

| Input | Where |
| --- | --- |
| Horizon cube, Stage A record | `$ANUMA_STAGE_A_DIR` (default `…/sun-validation-production-5334265`) |
| Stage E record and reference | `$ANUMA_STAGE_E_DIR` (default `/project/home/p201619/ANUMA_/out/hpc/daily-lit-production-5342908`): `daily-lit-report.json` (production, passed), `run-config.json`, `output-manifest.json`, `daily-lit-index.json`, `cumulative-monthly.npy`, `daily/lit-hours-YYYY.npy` |
| Kernels | `/project/home/p201619/spice-kernels/kernel-manifest.json` |
| Grid | `grid.json` via `data/input-manifest.json` |
| Config | `config/darkness.yaml` (pilot, fills, caps); period from `config/daily-lit.yaml` |

Preflight hashes only the kernels (coverage) and small JSON files. The job
hashes the horizon cube, and every Stage E file it reads against Stage E's
manifest: pilot 8.1 GB (cumulative and the 2026 file), production 49.6 GB.

## Acceptance checks

These are fixed before any run and are not tuned afterwards. All are exact
equalities. The pilot exits 4 when its projection does not fit, otherwise
3 when any check fails. Production exits 3 when any check fails. An
internal consistency error during the run (a `RuntimeError`, for example the
month loop out of step with the hourly stream) also exits 3. A refused input
or pin exits 2.

Both modes:

1. `valid_matches_stage_e`: the valid mask equals Stage E's
   `valid-mask.npy`.
2. `daily_lit_hours_equal_stage_e`: for **every** day of the run, the lit
   count per valid pixel accumulated from the hourly stream equals Stage E's
   daily file. The number of mismatched pixel-days must be 0.
3. `lit_total_equals_stage_e_cumulative`: the total lit hours per valid
   pixel equal Stage E's `cumulative[end] − cumulative[start]`. In
   production that is the final slice; in the pilot, slice 3 (2026-04-01).
4. `month_triples_within_bounds`: lead, trail ≤ best ≤ month hours, and
   `lead == hours ⇔ trail == hours ⇔ best == hours`.
5. `stitched_months_equal_streaming`: stitching all months (through the
   yearly blocks) reproduces `longest-dark-hours` exactly.
6. `run_invariants`: longest dark + longest lit ≤ period hours; longest
   dark ≤ period hours − lit total; longest lit ≤ lit total; longest dark = 0
   ⇔ always lit; never-lit pixels have longest dark = period hours and flags
   3; always-lit pixels have longest lit = period hours.

Pilot only (3 months, 2026-01-01 to 2026-04-01, 2,160 epochs, full grid,
three monthly dispatches):

7. `serial_parallel_identical`: on every 32nd row, the serial engine
   (`workers=1`) reproduces every stored month triple and period map **bit
   for bit**.
8. `brute_force_identical`: on the same rows, lit samples are evaluated
   directly with `_lit_mask` and run lengths are recomputed with a different
   algorithm (a consecutive-count scan). Hours, start, flags and interior,
   dark and lit, must all be equal.
9. `stitching_equals_brute_force`: all 6 month windows of the pilot
   (stitched from the stored month files) must equal the brute-force longest
   dark run of that window.
10. `projection.fits_production_cap`: the projected production wall time is
    ≤ **2.4 node-hours**. Stage G cannot resume, because its state spans the
    whole period, so 20 % of the 3:00 walltime is kept as headroom.

The pilot cannot check runs longer than three months or the 18.6-year
statistics. Production checks 2, 3, 5 and 6 cover those.

Tests (`tests/test_darkness.py`, synthetic only) cover:

- the kernel against brute force on random sequences (12 seeds, 1–6 months
  of 1–4 days, all-dark, all-lit, single-flip and both-ends columns);
- every month window against brute force, with lead and trail checked
  against first and last lit;
- associativity of `combine`;
- hand-checked censoring, interior and tie cases;
- engine serial == 3 workers;
- the engine against `lit_matrix` and against Stage E `daily_lit_hours`;
- a full pilot and a 14-month production (2026-01 to 2027-02, crossing a
  year) against a real short Stage E run, with all 105 month windows from
  the reader equal to brute force, and the reader without month files;
- refusal of a Stage E run with another horizon, an unpassed report or a
  tampered daily file;
- receipt drift;
- the projection limit;
- preflight profiles and pins.

## Resources and cost

| Item | Pilot | Production |
| --- | --- | --- |
| QOS | `test` | `default` |
| Walltime cap | 00:30 | 03:00 |
| Node-hours cap | 0.5 | 3.0 (pass limit 2.4 projected) |
| Node | 1 × 128 CPUs, `--mem=240G` | same |

Pilot plus production is at most **3.5 node-hours**. Like Stage E, this is
outside the 165-hour A–D caps. It needs an explicit, reviewed draw from the
held node-hours (55 held, of which Stage E was allotted 3.5 and used about
1). The study plan keeps the hold unless a reviewed plan reallocates it.

Expected cost. This is scaled from Stage E, not measured for this stage:

- Lit evaluation: Stage E production took 50 min in all, at a pilot warm
  rate of 0.0191 s/epoch. The streaming update adds about 8 %, so
  163,632 × 0.0207 s ≈ **56 min**.
- Stage E reference: hashing 49.6 GB plus reading 43.8 GB of daily files for
  check 2. At Lustre rates of about 1 GB/s, about **2–3 min**.
- Writing 9.5 GB, stitching years, the final checks and hashing the outputs
  for the manifest: about **3–5 min**.

Expect **≈ 1.1 node-hours**. The pilot measures this and refuses production
above 2.4. The projection uses the warm (last) monthly dispatch's s/epoch ×
163,632; kernel verification × 19 SPICE calls; the remaining SPICE time per
epoch; start-up, cube hash and the first dispatch's excess once; Stage E
hashing scaled by bytes (8.1 → 49.6 GB); and compare, write and finalize
scaled by days.

Pilot wall time is expected to be about 6–10 min: about 1 min of loading
and hashing, about 1 min of evaluation, and about 3–6 min for the
serial and brute-force passes on 80 sampled rows.

Peak RAM is expected to be close to Stage E's 115 GiB and below 240 GB:
the cube (9.25 GB, shared copy-on-write), the 0.45 GB of shared state and
month buffers, tile copies in the workers, and the page cache of the
memory-mapped outputs.

## Commands (MeluXina, not run)

Prerequisite: this branch is merged and checked out in
`/project/home/p201619/ANUMA_` at a clean commit. Preflight and job use
`.venv-hpc-sun` (OS Python 3.12, no module loaded), which has `spiceypy`,
`numpy` and `pyyaml` but **no scipy**; `.venv-hpc-ingest` has scipy but
**no spiceypy**, so neither venv has both. Stage G needs spiceypy, so it
uses `.venv-hpc-sun`. This works only because the scipy import is lazy:
`hpc/preflight.py` and `anuma.darkness` import `anuma.hpc_smoke`, which
imported `anuma.score`, which imported `scipy.ndimage` at module level.
Since this commit, `anuma.score` imports scipy inside `hard_mask` (only
when a slope radius is used) and `run_smoke` imports `anuma.score` only
after its `check_only` return. `tests/test_hpc_smoke.py::test_check_only_paths_import_without_scipy`
blocks scipy in a subprocess and imports preflight, `anuma.daily_lit` and
`anuma.darkness` and runs the check-only smoke, so a regression fails
locally before it reaches MeluXina.

Login node: capture `myquota` and refresh the allocation and storage
evidence files (they must be under 24 hours old). `myquota` exists only on
login nodes. Then set the environment as in
[meluxina-execution.md](meluxina-execution.md):

```bash
cd /project/home/p201619/ANUMA_
export ANUMA_ACCOUNT=p201619
export ANUMA_DATA_ROOT=/project/home/p201619/ANUMA_
export ANUMA_INPUT_MANIFEST=/project/home/p201619/ANUMA_/data/input-manifest.json
export ANUMA_OUTPUT_ROOT=/project/home/p201619/ANUMA_/out/hpc
export ANUMA_SCRATCH_DIR=/project/scratch/p201619
export ANUMA_ALLOCATION_EVIDENCE_FILE=... ANUMA_STORAGE_EVIDENCE_FILE=...
export ANUMA_KERNEL_MANIFEST=/project/home/p201619/spice-kernels/kernel-manifest.json
export ANUMA_STAGE_A_DIR=/project/home/p201619/ANUMA_/out/hpc/sun-validation-production-5334265
export ANUMA_STAGE_E_DIR=/project/home/p201619/ANUMA_/out/hpc/daily-lit-production-5342908
```

Pilot preflight runs inside a short `cpudev` allocation. It hashes the
kernels and refuses to run without `SLURM_JOB_ID`. Then submit from the
login node:

```bash
export ANUMA_JOB_PROFILE=stage-g-pilot ANUMA_QOS=test
salloc -A p201619 -p cpu --reservation=cpudev -q dev -N 1 -t 00:30:00 \
  srun --export=ALL -N1 -n1 -c 4 \
  .venv-hpc-sun/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-stage-g-pilot-$(date -u +%Y%m%dT%H%M%SZ).json"
export ANUMA_PREFLIGHT_RECEIPT="$HOME/<the-new-pilot-receipt>.json"
bash hpc/submit-darkness.sh pilot
```

Production runs only after the pilot passes, its projection is reviewed,
and the 3.5 node-hours are approved from the hold:

```bash
export ANUMA_JOB_PROFILE=stage-g ANUMA_QOS=default
salloc -A p201619 -p cpu --reservation=cpudev -q dev -N 1 -t 00:30:00 \
  srun --export=ALL -N1 -n1 -c 4 \
  .venv-hpc-sun/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-stage-g-$(date -u +%Y%m%dT%H%M%SZ).json"
export ANUMA_PREFLIGHT_RECEIPT="$HOME/<the-new-production-receipt>.json"
bash hpc/submit-darkness.sh production
```

`submit-darkness.sh` checks the receipt on the login node (it reads only
small JSON files and never hashes data), captures `myquota`, and submits
`hpc/slurm/darkness.sbatch`. The sbatch repeats every cap and the receipt
check, then runs `srun --export=ALL … python -m anuma.darkness --workers
128`. A failed or timed-out production run is not resumed. Fix the cause,
run a new preflight and submit again into a new directory.

After production, record `sacct -j <job> --format=JobID,Elapsed,State,CPUTime,MaxRSS`
with the `myquota` before and after captures. Then copy to the laptop:

```bash
rsync -av --exclude 'monthly/' meluxina:/project/home/p201619/ANUMA_/out/hpc/darkness-production-<job>/ out/hpc/darkness-production-<job>/
rsync -av meluxina:/project/home/p201619/ANUMA_/out/hpc/darkness-production-<job>/monthly/ out/hpc/darkness-production-<job>/monthly/   # 8.6 GB
```

## What it does not show

- It is not a measurement. It inherits every Stage A caveat: the rim band is
  too bright, and residuals are larger near the pole and on ridges.
- Hourly samples only. Gaps shorter than an hour, such as a brief lit
  glimpse inside a long night, are invisible, so the true longest dark spell
  may be split or merged differently. Run lengths are ±1 h.
- "Lit" is any part of the disc. It is not usable power: a lit run with
  the Sun grazing the horizon may give little energy. Mast height, panel
  tilt, Earth visibility and thermal response are not modeled.
- Censored runs at 2026-01-01 and 2044-08-31 are lower bounds. The interior
  maps give the fully observed alternative.
- 240 m only. It does not represent the 20 m terrain of Stage B or the
  site-window Stage C.
