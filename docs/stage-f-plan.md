# Stage F plan — full-cap 20 m terrain gate (flat ground within 1 km)

Not affiliated with NASA, ESA or ispace. Screening only; not a landing
recommendation. This plan authorizes no submission and records no result.
Status: **frozen on 2026-10-15, implemented, not run on MeluXina**.

The values below are fixed in `anuma/stage_f.py` (`FROZEN`) and in
`hard.terrain_1km` of `config/layers.yaml`; `require_frozen_config` refuses
any drift. Do not change a value after seeing a Stage F output; a change
needs a new dated plan revision and review before the run.

## Purpose

User decision, 2026-10-15: the ranking must **require enough flat ground at
20 m**. Today the "1 km fraction" exists only for 10 sites (Stage B table,
[stage-b-result.md](stage-b-result.md)) and 8 sites (Stage D zero noise,
[stage-d-result.md](stage-d-result.md)). It showed that most of the default
top 10 are mostly steeper than 8° within 1 km (ranks 1, 8 and 9 at 0.49,
0.499 and 0.47). Stage F computes the same statistic for every 240 m cell
of the cap, so it can be a hard rule in the score.

## Rule (frozen)

A 240 m cell is **eligible** only if

> at least **50 %** (inclusive, `fraction >= 0.50`) of the 20 m pixels whose
> centres lie within **1000 m** of the 240 m cell centre have a **finite**
> Horn slope **≤ 8°** (`hard.max_slope_deg`, read from config).

The rule is applied in addition to the existing 240 m slope mask, not instead
of it.

## Definition (frozen; identical to Stage D's zero-noise statistic)

| Item | Value |
| --- | --- |
| Slope | Horn 3×3 at 20 m: the Stage B `slope-20m.npy` (job `5334425`), the same array Stage D's zero-noise windows equal to 0.0° |
| Centre | the 240 m pixel centre in the shared polar stereographic projection. The 240 m grid nests 12 × 12 in the 20 m grid with its origin 4 fine pixels outside (`grid_origin_in_fine_pixels = [-4, -4]`, as `anuma/dem_error.py`), so the centre of cell (r, c) is the **fine pixel corner** (12 r + 2, 12 c + 2) |
| Circle | 20 m pixel **centres** with `hypot(dx, dy) <= 1000 m` from that corner: **7,860 pixels** (the 100 × 100 frame around the corner minus its corners); the test is `stage_d.window_masks`' test, and no pixel centre lies exactly on the circle (half-integer offsets cannot satisfy it) |
| Numerator | pixels in the circle with finite slope **≤ 8°** (equality passes) |
| Denominator | pixels in the circle with **finite** slope. Missing DEM, the one-pixel NaN frame of the Horn slope, and pixels off the 20 m raster count as missing, not as failing (Stage D: `circle_finite_pixels`) |
| No data | denominator 0 → fraction **NaN**; NaN **fails** the gate |
| Partial circles | no minimum coverage: a cell near the raster edge uses its finite pixels only, as Stage D would. The count of in-cap cells with a partial circle is reported |
| Outside the cap | NaN (latitude above 80°S, `mask_outside_cap`), as every 240 m layer |
| Threshold | `hard.terrain_1km.min_fraction = 0.5`, inclusive |
| Precision | counts are exact integers; fraction = passing / finite in float64, stored as float32. For the frozen threshold this cannot flip a decision (a fraction below 0.5 is at most 0.5 − 1/15,720) |

## Outputs (frozen)

In `out/hpc/stage-f-production-<job>/`:

| File | Content |
| --- | --- |
| `terrain_1km_fraction.npy` | float32, 2534 × 2534, NaN outside the cap or with no finite pixel — the gate layer |
| `terrain_1km_finite_count.npy`, `terrain_1km_pass_count.npy` | int16 counts behind the fraction (whole grid, not cap-masked) |
| `terrain_1km_fraction.json` | sidecar: slope limit (8°), radius, centre and NaN conventions, grid sha256, input digests, job id, layer statistics, `coverage: "full_cap"` only if every acceptance check passed |
| `stage-f-summary.json`, `output-manifest.json` | report, checks, timings, sha256 and bytes of every output |
| `progress.log`, `module-list.txt`, `myquota-before.txt`, `myquota-after.txt` | run evidence |

Pass/fail is **derived** from the fraction (`>= min_fraction`); no separate
pass array is written, so a changed threshold needs no rerun.

## How the score uses it (frozen behaviour)

- `hard.terrain_1km: {enabled: true, min_fraction: 0.5, radius_m: 1000,
  layer: terrain_1km_fraction, max_slope_deg_from: hard.max_slope_deg}`.
- The API loads `data/grid/terrain_1km_fraction.npy` only with a valid
  sidecar (`coverage: full_cap`, same grid sha256, same radius). Otherwise
  the gate is **unavailable**: the ranking is today's, and `/score` and
  `/layers` say so (`terrain_gate: "unavailable"` with the reason).
- Request switch: `hard.terrain_1km` (true / false / omitted = config) and
  `hard.terrain_1km_min_fraction` (0–1), so the UI can show the ranking
  with and without the rule.
- **Max slope other than 8°.** The fraction exists for 8° only and cannot be
  recomputed in the app. Decision: the gate is **not applied** and the
  response says `terrain_gate: "slope_mismatch"`, "20 m terrain rule uses
  8° and cannot be recomputed for X°". An explicit `terrain_1km: true` with
  another max slope is a 400. Reason: applying an 8° flat-ground rule under
  a different slope limit is a third rule nobody chose; silently keeping 8°
  would make a looser slope setting stricter than it says.
- **Studies basis.** The Monte Carlo top-10 % (job `5332991`) and the Stage
  B and Stage D badges were computed for the slope-only ranking.
  `/score` returns `ranking_basis = {basis, studies_basis: "slope_only",
  studies_match, note}`; when the gate applies, `studies_match` is false and
  the UI should mark those columns "computed for the previous ranking".
  `/stability.json` carries `ranking_basis: "slope_only"`.
- The layer is a **gate layer** (`gate: true` in `layers.yaml`): it is
  never weighted (a positive weight is a 400), it is shown as "Flat ground
  within 1 km at 20 m (%)" on a fixed 0–1 grey range.

## Algorithm

Exact integer counting, no floating-point convolution:

1. Per row band of 32 240 m rows (80 bands), read the fine rows the band's
   disks touch (band ± 50 fine rows; 100-pixel column pads, NaN outside the
   raster) from `slope-20m.npy` (memory-mapped).
2. Masks `finite` and `passing = finite & slope <= 8°`.
3. Per fine row, a cumulative sum of each mask (int32) gives the count of any
   horizontal run in O(1). The disk is 100 horizontal chords; the count at
   a centre is the sum of its 100 chord counts.
4. Sample at the 240 m centres only (12 r + 2, 12 c + 2).

FFT or `oaconvolve` would give the same counts after rounding, but needs a
float error argument and a rounding step; the prefix-sum chords are exact by
construction and cheaper (about 0.4 s per band on a laptop core). Bands
overlap only in what they read, never in what they write, so the output does
not depend on worker count or order.

## Inputs

| Input | Where | SHA-256 |
| --- | --- | --- |
| Stage B 20 m slope | `/project/home/p201619/ANUMA_/out/hpc/stage-b-production-5334425/slope-20m.npy` | `08b2e17b98f6c639068493e1f4007194d5cc41ad2d78cb850e593aef9542bd3a` |
| 20 m DEM | `/project/home/p201619/ANUMA_/data/raw/LDEM_80S_20MPP_ADJ.TIF` | `09b7ca80f9e6a146f970225d18af72fc02787669b3ef51b888e347d2b6845649` |
| Stage B table | `data/checks/stage-b-1km.json` (repo) | recorded |
| Stage D table | `data/checks/stage-d-noise.json` (repo); exact counts frozen in code | recorded |

The DEM gives the 20 m transform and CRS (checked against the grid proj4 to
1 mm) and an independent check: each acceptance site is recomputed from DEM
windows through Stage D's own code. Digests are verified in preflight (on
`cpudev`) and again in the job, never on a login node.

## Acceptance checks (fixed before any run)

1. **Stage B:** ranks 1–10 of `data/checks/stage-b-1km.json` reproduced to
   two decimals (|Δ| ≤ 0.005, Stage D's rule).
2. **Stage D:** for all 8 sites, finite = 7,860 and passing equal to the
   frozen zero-noise counts of job `5345050` (`stage-d-summary.json`
   sha256 `bee287c7…`): 1318:1219 3863, 1292:1255 821, 1350:1392 5176,
   791:1624 3920, 779:1564 3701, 1252:1287 1638, 1277:1142 3073,
   1206:1114 2509 (fractions to 1e-6; the committed JSON's four-decimal
   values to 5e-5).
3. **Stage D code path:** the 18 acceptance cells recomputed from DEM
   windows with `stage_d.read_window`, `slope_tile_from_array`,
   `window_masks` and `slope_statistics` give identical counts.
4. **Brute force:** 512 (pilot) / 4,096 (production) cells sampled with seed
   20261017, counted with Stage D's `window_masks` circle directly on the
   slope, equal the band counts exactly.
5. **Serial = parallel:** the 8 pilot bands (band indices of the 32-row bands holding every acceptance
   site: 19, 23, 24, 37, 39, 40, 41, 42) computed serially and with the
   worker pool are bit-identical. Production repeats this.
6. **Pilot projection:** projected production wall time fits 1.0 node-hour
   and the 01:00 walltime.

Any failed check: exit 3, sidecar `coverage: failed_acceptance`, and the
API refuses the layer.

## Resources

| Item | Pilot | Production |
| --- | --- | --- |
| QOS | `test` | `default` |
| Walltime cap | 00:30 | 01:00 |
| Node-hours cap | 0.5 | 1.0 |
| Node | 1 CPU node, 128 workers | same |

Pilot + production caps = **1.5 node-hours ≤ 2.0** node-hours. Expected use
is minutes: hashing about 6.4 GB, 80 bands of about 1 s each across the
pool, and the checks. Memory is about 0.3 GB per worker.

## What it does not show

- It uses the 20 m DEM as published; DEM noise sensitivity is Stage D's
  (eight sites; the fraction moved by < 0.006 between the 5th and 95th
  percentiles). Cells within about ±0.005 of 0.50 can flip under that noise.
- The 1 km circle is a screening proxy, not a mission landing ellipse.
- It recomputes no soft layer and no Monte Carlo; the studies stay on the
  slope-only basis until rerun.
- It holds for 8° only.
