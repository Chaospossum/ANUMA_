# Stage D plan — site-local LOLA DEM-noise check

Not affiliated with NASA, ESA or ispace. Screening only; not a landing
recommendation. This plan authorizes no submission and records no result.
Status: **frozen on 2026-10-15, not implemented, not run**.

The study plan ([hpc-study-plan.md](hpc-study-plan.md), Stage D) requires the
stability rule, the LOLA elevation-noise model and the landing-ellipse check
to be frozen and justified before any noise rerun. They are frozen here and
in `hpc_studies.dem_noise` in `config/layers.yaml`. No noise draw has been
made. Do not change any value below after seeing a noise result; a change
needs a new dated plan revision and review before the run.

## Purpose

Stage B showed that the 240 m top 10 survives at 20 m by construction, and
that two sites (ranks 2 and 10) are mostly steeper than 8° within 1 km. It
did not show how much of that 20 m slope picture is LOLA height error. Stage D
asks one narrow question: for the sites whose ranking is already marginal,
and for three controls, does the 1 km slope check keep its pass/fail answer
when the 20 m DEM is perturbed by its own published height error?

The weight-and-clipping part of Stage D is already answered by the 240 m
Monte Carlo job `5332991` (500 weight draws, 3 policies). It reuses the
ingested arrays and recomputes no slope. Stage D does not repeat it.

## Stability rule (frozen)

A site is **rank-unstable** if either holds:

1. it is in the default top 10 and is in the top 10 in **< 80 %** of the 500
   weight draws of job `5332991` under `decided_per_layer`; or
2. its default rank is within **2** of the top-10 cut, i.e. ranks **9–12**.

Inputs: `out/hpc/monte-carlo-full-5332991/study-summary.json` (sha256
`4da2a9c336e0ef2b5242afdb1844a536667e46f17cf6fd047c18b794c864f074` in that
job's `output-manifest.json`), field
`policies.decided_per_layer.top10_membership_frequency`. Default ranks come
from running `anuma.score.score` and `rank_sites(n=15)` locally on the
laptop's `data/grid` arrays with the default config (weights 0.5/0.3/0.2,
`decided_per_layer`, 8° mask, 10 km separation). The first 10 equal the
Stage B rank table in the same order.

| Default rank | Site id | Site | Score | In top 10 (of 500) | Selected by |
| --- | --- | --- | --- | --- | --- |
| 1 | 1318:1219 | 89.45°S 137.31°W | 0.9293 | 1.000 | control |
| 2 | 1292:1255 | 89.78°S 155.73°W | 0.9154 | 1.000 | control |
| 3 | 1350:1392 | 88.81°S 123.64°E | 0.9052 | 1.000 | control |
| 4 | 753:1681 | 84.78°S 38.91°E | 0.8919 | 1.000 | — |
| 5 | 756:1236 | 85.95°S 3.42°W | 0.8882 | 1.000 | — |
| 6 | 761:1285 | 86.00°S 2.10°E | 0.8770 | 0.988 | — |
| 7 | 619:799 | 83.69°S 35.83°W | 0.8769 | 0.970 | — |
| 8 | 791:1624 | 85.29°S 36.94°E | 0.8659 | 1.000 | — |
| 9 | 779:1564 | 85.48°S 31.39°E | 0.8598 | 0.844 | rank margin |
| 10 | 1252:1287 | 89.80°S 54.73°E | 0.8535 | 0.948 | rank margin |
| 11 | 1277:1142 | 89.01°S 94.82°W | 0.8474 | 0.176 | rank margin |
| 12 | 1206:1114 | 88.70°S 68.36°W | 0.8451 | 0.022 | rank margin |

Result of the rule: **779:1564, 1252:1287, 1277:1142, 1206:1114**.

The 80 % frequency clause selects no top-10 site on its own; the lowest
top-10 frequency is 0.844 (rank 9). The threshold is kept, not lowered to
make it bite: the weight study simply shows the top 8 are stable to ±0.1
weight changes, and the rank-margin clause already picks the boundary. The
rule therefore selects 4 sites, neither none nor all, and is left as
proposed. Site 972:515 (rank 13, 5.2 % of draws) is outside the rule and is
not added.

**Controls:** default ranks 1–3. Rank 2 is also the site Stage B flagged
(0.10 of 20 m cells ≤ 8° within 1 km).

**Scope:** 7 sites in total, in this order (the order fixes the seed streams):
1318:1219, 1292:1255, 1350:1392, 779:1564, 1252:1287, 1277:1142, 1206:1114.

## Noise model (frozen)

Source: NASA PGDA product 90 (https://pgda.gsfc.nasa.gov/products/90),
`LDEM_80S_20MPP_ADJ_ERR.TIF`, "LDEM surface height error (in meters)", and
`LDEM_80S_20MPP_ADJ_EFFRES.TIF`, "LDEM effective resolution (in meters)",
both on the same 20 m grid as `LDEM_80S_20MPP_ADJ.TIF`. Barker et al. (2023),
Planet. Sci. J. 4, 183, https://doi.org/10.3847/PSJ/acf3e1, derive the error
from an ensemble of solutions, so it is a random-error estimate, not a bias.

For each site and draw, on the ±5 km window plus a pad of ⌈4σ/20 m⌉ pixels:

1. `w` = standard normal white noise, one value per 20 m pixel.
2. `g` = `w` convolved with a normalized Gaussian kernel `K` (sum 1,
   truncated at 4σ), σ = **median of EFFRES within the window**, in metres.
   If the EFFRES file cannot be staged, σ = **40 m** fixed, and the report
   says so.
3. `u` = `g / sqrt(sum K²)`, so `u` has unit standard deviation per pixel.
4. noise = `ERR × u`; perturbed DEM = DEM + noise; crop the pad.
5. Horn 3×3 slope at 20 m (`anuma.slope20.slope_tile_from_array`, the
   Stage B code path).

Why this form:

- **ERR as 1σ is an assumption.** Neither the product page nor the paper
  states the confidence level. 1σ is the usual reading of an ensemble
  spread. If it is really 2σ, the test overstates noise (conservative).
- **Correlated, not white.** Below the effective resolution the DEM is
  interpolated, so its errors are not independent from pixel to pixel.
  White noise at 20 m would put most of its variance into the 3×3 Horn
  gradient and inflate slopes far beyond what the error map means.
  Smoothing at the effective resolution keeps the error's length scale.
- **Rescaled after smoothing** so the per-pixel standard deviation equals the
  published error exactly; smoothing alone would shrink it.
- **One σ per site** (window median) instead of a spatially varying kernel.
  The error amplitude still varies per pixel through `ERR`. The report
  records min/median/max EFFRES in each window so this can be checked.
- Product 90 also ships `LDSM_80S_20MPP_ADJ_ERR.TIF` (slope error). It is not
  used: it is not tied to ANUMA's Horn slope, and drawing heights keeps the
  ellipse check on the same path as Stage B.

## Landing-ellipse check (frozen)

- Centre: the 240 m pixel centre of the site, in the shared polar
  stereographic projection (same centre as the Stage B 1 km table).
- Ellipse: a circle of radius **1 km**, the 20 m pixels whose centres lie
  within 1000 m. This matches Stage B's "within 1 km" statistic.
- Pass: **≥ 50 %** of those pixels have slope **≤ 8°**
  (`hard.max_slope_deg`, read from config, not copied).
- Per site: zero-noise fraction and pass; pass rate over the N draws;
  mean, 5th and 95th percentile of the ≤ 8° fraction.
- **Noise-unstable:** the pass/fail answer differs from the zero-noise
  answer in **> 10 %** of draws (more than 20 of 200).

Known from Stage B before this freeze (zero-noise 1 km fractions): rank 1
0.49, rank 2 0.10, rank 3 0.66, rank 9 0.47, rank 10 0.21; ranks 11–12 were
not tabulated. The 50 % threshold and 1 km radius were set with these
numbers in view; only the noise results are unseen. Ranks 1 and 9 sit close
to the threshold, so they are the likeliest to flip.

## Draws, seed and windows

| Item | Value |
| --- | --- |
| Draws per site | N = 200 |
| Seed | 20261016 |
| Stream | `default_rng(SeedSequence(20261016, spawn_key=(site_index, draw)))` |
| Window | ±5 km around the centre at 20 m (501 × 501 pixels) plus noise pad |
| Slope | Horn 3×3, 20 m cell size |

One stream per (site, draw) makes the output independent of worker count and
scheduling.

## Inputs

| Input | Where |
| --- | --- |
| 20 m DEM | `/project/home/p201619/ANUMA_/data/raw/LDEM_80S_20MPP_ADJ.TIF` (Stage B, already staged and checksummed) |
| Height error | `LDEM_80S_20MPP_ADJ_ERR.TIF` (3.7 GB), to be staged next to the DEM; sha256 recorded in the input manifest before the pilot |
| Effective resolution | `LDEM_80S_20MPP_ADJ_EFFRES.TIF` (3.8 GB), same; optional (40 m fallback) |
| Stage B slope | `slope-20m.npy` from job `5334425`, for the zero-noise check |
| Site manifest | the 7 ids above, `hpc_studies.dem_noise` in `config/layers.yaml` |

Only windows are read; no full-cap array is loaded and no full-cap slope is
recomputed.

## Resources

| Item | Pilot | Production |
| --- | --- | --- |
| QOS | `test` | `default` |
| Walltime cap | 00:30 | 02:00 |
| Node-hours cap | 0.5 | 2.0 |
| Node | 1 CPU node | 1 CPU node |

7 sites × 200 draws = 1,400 window slope computations of about 0.25 M
pixels each, plus one zero-noise pass per site. That is minutes of CPU, so
2 node-hours is a hard ceiling, not an estimate, and is far below the study
plan's 50 node-hour Stage D cap. Workers are bounded as in Stage E (≤ 128).

## Acceptance checks

Fixed before any run.

Pilot (2 sites × 8 draws, plus their zero-noise passes):

1. Serial and parallel runs with the same seed give bit-identical per-draw
   ≤ 8° fractions and pass flags.
2. Zero-noise window slope equals the matching slice of Stage B
   `slope-20m.npy` within 1e-4°, and its 1 km fraction equals the Stage B
   table to two decimals.
3. The noise field's sample standard deviation divided by `ERR`, pooled over
   the window and draws, is within 1 ± 0.05.
4. Projected production time fits 2.0 node-hours.

Production: checks 1–2 for all 7 sites, then the per-site table and the
per-draw records with environment, resource and checksum manifests.

## What it does not show

- It tests only height error, as ANUMA reads it (1σ, Gaussian, one length
  scale per site). Systematic error, track misregistration and the
  possibility that ERR is not 1σ are outside it.
- Adding zero-mean noise roughens terrain on average, so it tends to raise
  slopes. A fall in the ≤ 8° fraction is partly this one-sided effect, not
  evidence that the true surface is steeper.
- It does not re-rank sites. Soft layers stay at 240 m; only the 1 km slope
  check is perturbed.
- A 1 km circle at a 240 m cell centre is a screening proxy, not a mission
  landing ellipse, which is larger and oriented.
- Sites outside the 7 are not tested; a stable rank says nothing about their
  DEM-noise sensitivity.
