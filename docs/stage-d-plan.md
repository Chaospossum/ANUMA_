# Stage D plan — site-local LOLA DEM-noise check

Not affiliated with NASA, ESA or ispace. Screening only; not a landing
recommendation. This plan authorizes no submission and records no result.
Status: **frozen on 2026-10-15 (revised after review the same day, before any
noise run), not implemented, not run**.

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
and for four controls, how much do the 1 km slope statistics and the 20 m
survival of the site's 240 m cell move when the 20 m DEM is perturbed by its
own published height error?

The weight-and-clipping part of Stage D is already answered by the 240 m
Monte Carlo job `5332991` (500 weight draws, 3 policies). It reuses the
ingested arrays and recomputes no slope. Stage D does not repeat it.

## Stability rule (frozen)

The rule answers **"which sites are rank-marginal"**. It does not answer "is
rank 1 robust"; rank 1 is a control, not a rule output.

A site is **rank-unstable** if either holds:

1. it is in the default top 10 and is in the top 10 in **< 80 %** of the 500
   weight draws of job `5332991` under `decided_per_layer`; or
2. its default rank is within **2** of the top-10 cut, i.e. **default ranks
   9–12**.

Inputs: `out/hpc/monte-carlo-full-5332991/study-summary.json` (sha256
`4da2a9c336e0ef2b5242afdb1844a536667e46f17cf6fd047c18b794c864f074` in that
job's `output-manifest.json`), field
`policies.decided_per_layer.top10_membership_frequency`. Default ranks come
from running `anuma.score.score` and `rank_sites(n=15)` locally on the
laptop's `data/grid` arrays with the default config (weights 0.5/0.3/0.2,
`decided_per_layer`, 8° mask, 10 km separation). The first 10 equal the
Stage B rank table in the same order.

| Default rank | Site id | Site | Score | In top 10 (of 500) | Stage B 1 km ≤ 8° | Role |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 1318:1219 | 89.45°S 137.31°W | 0.9293 | 1.000 | 0.49 | control |
| 2 | 1292:1255 | 89.78°S 155.73°W | 0.9154 | 1.000 | 0.10 | control |
| 3 | 1350:1392 | 88.81°S 123.64°E | 0.9052 | 1.000 | 0.66 | control |
| 4 | 753:1681 | 84.78°S 38.91°E | 0.8919 | 1.000 | 0.32 | — |
| 5 | 756:1236 | 85.95°S 3.42°W | 0.8882 | 1.000 | 0.38 | — |
| 6 | 761:1285 | 86.00°S 2.10°E | 0.8770 | 0.988 | 0.64 | — |
| 7 | 619:799 | 83.69°S 35.83°W | 0.8769 | 0.970 | 0.24 | — |
| 8 | 791:1624 | 85.29°S 36.94°E | 0.8659 | 1.000 | 0.50 | control (added) |
| 9 | 779:1564 | 85.48°S 31.39°E | 0.8598 | 0.844 | 0.47 | rule: rank margin |
| 10 | 1252:1287 | 89.80°S 54.73°E | 0.8535 | 0.948 | 0.21 | rule: rank margin |
| 11 | 1277:1142 | 89.01°S 94.82°W | 0.8474 | 0.176 | not tabulated | rule: rank margin |
| 12 | 1206:1114 | 88.70°S 68.36°W | 0.8451 | 0.022 | not tabulated | rule: rank margin |

Result of the rule: **779:1564, 1252:1287, 1277:1142, 1206:1114**.

The 80 % frequency clause selects no top-10 site on its own; the lowest
top-10 frequency is 0.844 (rank 9). The threshold is kept, not lowered to
make it bite: the weight study shows the top 8 are stable to ±0.1 weight
changes, and the rank-margin clause already picks the boundary. Site 972:515
(rank 13, 5.2 % of draws) is outside the rule and is not added.

The code must recompute the rule from the summary and the default ranking
and **assert** that it reproduces `frozen_unstable_ids`; it must not just
read the list from config.

**Controls:** default ranks 1–3, plus default rank 8 (791:1624), added on
2026-10-15 before any noise draw because its zero-noise 1 km fraction is
exactly 0.50. Rank 2 is also the site Stage B flagged (0.10).

**Scope:** 8 sites, in this order (the order fixes the seed streams):
1318:1219, 1292:1255, 1350:1392, 791:1624, 779:1564, 1252:1287, 1277:1142,
1206:1114.

## Noise model (frozen)

Source: NASA PGDA product 90 (https://pgda.gsfc.nasa.gov/products/90), on the
same 20 m grid as `LDEM_80S_20MPP_ADJ.TIF`:

- `LDEM_80S_20MPP_ADJ_ERR.TIF`, "LDEM surface height error (in meters)";
- `LDEM_80S_20MPP_ADJ_EFFRES.TIF`, "LDEM effective resolution (in meters)";
- `LDSM_80S_20MPP_ADJ_ERR.TIF`, slope error in degrees, used only for an
  acceptance check, not to draw noise.

Barker et al. (2023), Planet. Sci. J. 4, 183,
https://doi.org/10.3847/PSJ/acf3e1, derive the height error from an ensemble
of solutions, so it is a random-error estimate, not a bias.

For each site and draw, on the ±5 km window plus a pad of ⌈4σ/20 m⌉ pixels:

1. `w` = standard normal white noise, one value per 20 m pixel.
2. `g` = `w` convolved with a normalized Gaussian kernel `K` (sum 1,
   truncated at 4σ).
3. `u` = `g / sqrt(sum K²)`, so `u` has unit standard deviation per pixel.
4. noise = `ERR × u`; perturbed DEM = DEM + noise; crop the pad.
5. Horn 3×3 slope at 20 m (`anuma.slope20.slope_tile_from_array`, the
   Stage B code path).

Kernel width, frozen:

| Run | σ | Role |
| --- | --- | --- |
| Primary | **median EFFRES in the window / 2.355** (EFFRES read as a FWHM) | decisive |
| Sensitivity | median EFFRES in the window | reported only; smoother noise |
| Fallback (EFFRES not staged) | **40 m FWHM, σ = 40 / 2.355 ≈ 17.0 m** | replaces the primary; the report says so |

Why this form:

- **ERR as 1σ is an assumption.** Neither the product page nor the paper
  states the confidence level. 1σ is the usual reading of an ensemble
  spread. If it is really 2σ, the test overstates noise (conservative).
- **EFFRES as FWHM is an assumption.** The product page gives only "effective
  resolution (in meters)". A resolution length is most often a full width,
  so σ = FWHM / 2.355. Reading it as σ instead gives smoother, gentler noise;
  that reading is the sensitivity run, so the choice is visible.
- **Correlated, not white.** Below the effective resolution the DEM is
  interpolated, so its errors are not independent from pixel to pixel.
  White noise at 20 m would put most of its variance into the 3×3 Horn
  gradient and inflate slopes far beyond what the error map means.
- **Rescaled after smoothing** so the per-pixel standard deviation equals the
  published error exactly; smoothing alone would shrink it.
- **One σ per site** (window median) instead of a spatially varying kernel.
  The error amplitude still varies per pixel through `ERR`. The report
  records min/median/max EFFRES in each window so this can be checked.

## Landing-ellipse check (frozen)

- Centre: the 240 m pixel centre of the site, in the shared polar
  stereographic projection (same centre as the Stage B 1 km table).
- Ellipse: a circle of radius **1 km**, the 20 m pixels whose centres lie
  within 1000 m. This matches Stage B's "within 1 km" statistic.
- Slope limit: **8°** (`hard.max_slope_deg`, read from config, not copied).

**Decision (decisive, primary σ):** a site is **noise-unstable** if the 5th–95th
percentile interval of its ≤ 8° fraction within 1 km over the 200 draws

- is wider than **0.10**, or
- straddles **0.50** (5th percentile < 0.50 ≤ 95th percentile).

Why distributional: ranks 1 (0.49), 8 (0.50) and 9 (0.47) sit on the 50 %
line at zero noise, and zero-mean height noise tends to roughen terrain and
push the ≤ 8° fraction down. A pass/fail flip rate around that line would be
an artefact of where the line is, not a measure of noise sensitivity.

**Reported per site, per run (primary and sensitivity):**

| Quantity | Decisive? |
| --- | --- |
| ≤ 8° fraction within 1 km: zero-noise, mean, 5th, 95th percentile | yes (interval rule) |
| Δ = draw fraction − zero-noise fraction: mean, 5th, 95th percentile | no; makes the bias explicit |
| Pass (≥ 0.50) at zero noise; pass rate over draws; flip rate vs zero noise | no |
| In-cell survival: number of the 144 fine 20 m pixels in the site's 240 m cell with slope ≤ 8°, zero-noise and per draw (min, 5th, median, 95th); fraction of draws with count > 0 | no |
| Largest 8-connected ≤ 8° patch within 1 km, in m²: zero-noise, mean, 5th, 95th percentile | no |
| min/median/max EFFRES and median ERR in the window; σ used | no |

In-cell survival is the rank-relevant quantity: Stage B keeps a 240 m site
at 20 m exactly when at least one of its 144 fine pixels passes the 8° mask.
A draw with count 0 would remove the site from the 20 m ranking.

## Draws, seed and windows

| Item | Value |
| --- | --- |
| Draws per site and run | N = 200 |
| Seed | 20261016 (the date the run is planned for; fixed here) |
| Stream | `default_rng(SeedSequence(20261016, spawn_key=(run_index, site_index, draw)))`, run 0 = primary, 1 = sensitivity |
| Window | ±5 km around the centre at 20 m (501 × 501 pixels) plus noise pad |
| Slope | Horn 3×3, 20 m cell size |

One stream per (run, site, draw) makes the output independent of worker
count and scheduling.

## Inputs

| Input | Where |
| --- | --- |
| 20 m DEM | `/project/home/p201619/ANUMA_/data/raw/LDEM_80S_20MPP_ADJ.TIF` (Stage B, already staged and checksummed) |
| Height error | `LDEM_80S_20MPP_ADJ_ERR.TIF` (3.7 GB) |
| Effective resolution | `LDEM_80S_20MPP_ADJ_EFFRES.TIF` (3.8 GB); 40 m FWHM fallback |
| Slope error | `LDSM_80S_20MPP_ADJ_ERR.TIF` (3.7 GB), acceptance check only |
| Stage B slope | `slope-20m.npy` from job `5334425`, for the zero-noise check |
| Monte Carlo summary | `study-summary.json` from job `5332991`, to recompute the rule |
| Site manifest | `hpc_studies.dem_noise` in `config/layers.yaml` |

The three GeoTIFFs are being downloaded on MeluXina into
`/project/home/p201619/ANUMA_/data/raw/`, next to the DEM, with their
sha256 in `pgda90-sha256.txt` there. The job verifies those digests before
reading. Reading the rasters, including hashing them, happens only inside
compute jobs, never on a login node (global gate 6). Record `myquota`
before and after the download and before and after each job.

Only windows are read; no full-cap array is loaded and no full-cap slope is
recomputed.

## Resources

| Item | Pilot | Production |
| --- | --- | --- |
| QOS | `test` | `default` |
| Walltime cap | 00:30 | 02:00 |
| Node-hours cap | 0.5 | 2.0 |
| Node | 1 CPU node | 1 CPU node |

8 sites × 200 draws × 2 runs = 3,200 window slope computations of about
0.25 M pixels each, plus one zero-noise pass per site. That is minutes of
CPU, so 2 node-hours is a hard ceiling, not an estimate, and is far below
the study plan's 50 node-hour Stage D cap. Workers are bounded as in Stage E
(≤ 128).

## Acceptance checks

Fixed before any run.

Pilot (2 sites × 8 draws, primary run, plus their zero-noise passes):

1. The recomputed stability rule reproduces `frozen_unstable_ids` exactly.
2. Serial and parallel runs with the same seed give bit-identical per-draw
   outputs.
3. Zero-noise window slope equals the matching slice of Stage B
   `slope-20m.npy` within 1e-4°, and its 1 km fraction equals the Stage B
   table to two decimals.
4. The noise field's sample standard deviation divided by `ERR`, pooled over
   the window and draws, is within 1 ± 0.05.
5. Slope-noise scale: the standard deviation of (noisy − zero-noise) Horn
   slope over the window, pooled over draws, is compared with the median of
   `LDSM_80S_20MPP_ADJ_ERR` in the same window. A ratio above 2 or below 0.5
   rejects the run until the noise model is reviewed.
6. Projected production time fits 2.0 node-hours.

Production: checks 1–3 and 5 for all 8 sites and both runs, then the
per-site table and the per-draw records with environment, resource and
checksum manifests.

## What it does not show

- It tests only height error, as ANUMA reads it (1σ, Gaussian, one length
  scale per site, EFFRES as FWHM). Systematic error, track misregistration,
  and other readings of ERR or EFFRES are outside it, except the declared
  σ = EFFRES sensitivity run.
- Adding zero-mean noise roughens terrain on average, so it tends to raise
  slopes. The Δ columns show this one-sided effect; a fall in the ≤ 8°
  fraction is not evidence that the true surface is steeper.
- It does not recompute the score ranking. What it does for rank: it reports
  whether each site's 240 m cell keeps at least one 20 m pixel ≤ 8° under
  noise, which is the only way slope can change the Stage B 20 m ranking.
  Soft layers stay at 240 m.
- The stability rule finds rank-marginal sites; it says nothing about
  whether rank 1 is robust beyond its role as a control.
- A 1 km circle at a 240 m cell centre is a screening proxy, not a mission
  landing ellipse, which is larger and oriented.
- Sites outside the 8 are not tested; a stable rank says nothing about their
  DEM-noise sensitivity.
