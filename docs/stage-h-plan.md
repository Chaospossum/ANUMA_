# Stage H plan — robustness of the gated ranking

Not affiliated with NASA, ESA or ispace. Screening only; not a landing
recommendation. This plan authorizes no submission and records no result.
Status: **frozen on 2026-10-15, implemented, not run on MeluXina**.

The values below are fixed in `hpc_studies.stage_h` and
`hpc_studies.dem_noise_gated` of `config/layers.yaml`, in `anuma/stage_h.py`
(`FROZEN_STAGE_H`, `FROZEN_MONTE_CARLO`), `anuma/stage_d.py`
(`FROZEN_GATED`) and `anuma/stage_h_gate.py` (digests). The code refuses any
drift. Do not change a value after seeing a Stage H output; a change needs a
new dated plan revision and review before the run.

## Purpose

User decision, 2026-10-15: the ranking requires the Stage F 20 m terrain
gate (a 240 m cell is eligible only if ≥ 50 % of the 20 m pixels within 1 km
have Horn slope ≤ 8°; [stage-f-plan.md](stage-f-plan.md)). Stage F production
job `5345446` built the layer, and the default top 10 changed. The two
robustness studies the app shows were computed for the old slope-only
ranking, so the UI shows them as n/a:

- the 240 m weight Monte Carlo, job `5332991` (500 draws, weights ±0.1, three
  normalisation policies; [hpc-study-plan.md](hpc-study-plan.md));
- the Stage D DEM-noise check, job `5345050`
  ([stage-d-plan.md](stage-d-plan.md)).

Stage H repeats both for the gated ranking with the same designs. It reuses
`anuma/hpc_monte_carlo.py` and `anuma/stage_d.py` (parameterised, not
forked). The slope-only results stay as history.

## Gated default ranking (frozen scope)

Default config (weights 0.5 / 0.3 / 0.2, `decided_per_layer`, 8° mask,
10 km separation) with the gate forced on, `rank_sites(n=15)`, on the laptop
`data/grid` arrays and the job `5345446` layer (sha256 `7902c9b9…`), on
2026-10-15. Ranks 1–10 equal the default `/score` with the gate applied.

| Gated rank | Site id | Site | Score | 1 km ≤ 8° fraction | Old (slope-only) role |
| --- | --- | --- | --- | --- | --- |
| 1 | 1350:1392 | 88.81°S 123.64°E | 0.9052 | 0.6585 | old rank 3; Stage D control |
| 2 | 748:1693 | 84.69°S 39.44°E | 0.8780 | 0.6078 | — |
| 3 | 761:1285 | 86.00°S 2.10°E | 0.8770 | 0.6438 | old rank 6 |
| 4 | 774:1572 | 85.42°S 31.81°E | 0.8511 | 0.5464 | — |
| 5 | 1319:1219 | 89.44°S 137.86°W | 0.8496 | 0.5243 | neighbour of old rank 1 (1318:1219, 0.49) |
| 6 | 1281:1142 | 89.01°S 96.64°W | 0.8408 | 0.5050 | — |
| 7 | 768:1635 | 85.10°S 36.47°E | 0.8360 | 0.5497 | — |
| 8 | 1290:1547 | 87.77°S 94.79°E | 0.7943 | 0.5695 | — |
| 9 | 753:1742 | 84.47°S 42.80°E | 0.7822 | 0.6819 | — |
| 10 | 1484:1402 | 87.97°S 148.08°E | 0.7652 | 0.6299 | — |
| 11 | 915:1109 | 86.95°S 24.14°W | 0.7636 | 0.7499 | — |
| 12 | 728:1081 | 85.49°S 19.01°W | 0.7616 | 0.5419 | — |

Ranks 13–15 (outside scope): 1196:1703, 698:1711, 964:505.

**Scope: the 12 sites above, in this order** (the order fixes the noise seed
streams):
1350:1392, 748:1693, 761:1285, 774:1572, 1319:1219, 1281:1142, 768:1635,
1290:1547, 753:1742, 1484:1402, 915:1109, 728:1081.

The job recomputes the gated ranking from the pinned 240 m arrays and the
pinned gate layer and **asserts** it reproduces this order
(`stage_d.recompute_gated_scope`); it does not read the list from config.

## Part 1 — weight Monte Carlo with the gate (frozen)

Same design as job `5332991`, value for value
(`hpc_studies.clip_weight_monte_carlo`, unchanged): seed `20261012`, 500
draws (pilot 8), weights uniform in illumination [0.4, 0.6], hydrogen
[0.2, 0.4], Earth visibility [0.1, 0.3] and renormalised to sum 1, policies
`legacy_global_2_98`, `decided_per_layer`, `all_min_max`, clipped versus
unclipped winner-class radius 10 km. Same draws: draw *i* has the same
weights as draw *i* of job `5332991`.

The only change: every draw and policy ranks with the gate **on**
(`terrain_1km: true`, `min_fraction 0.5`, radius 1 km, layer built for 8°).
The gate enters the hard mask before normalisation, so percentile and
min–max ranges are taken over gated cells, as the default `/score` does.
`score` raises instead of silently ranking slope-only if the gate cannot
apply.

Outputs: `study-summary.json` with the job `5332991` schema plus
`ranking_basis: "terrain_gate_8deg_1km_50pct"`, `terrain_gate` (status,
layer, min fraction, radius, slope limit, cells removed, `fraction_sha256`,
source job `5345446`) and `source_job` (this job's id); `draws.jsonl`;
`output-manifest.json` (now also naming the gate layer and sidecar digests).
Per-site top-10 membership frequency and winner frequency are reported per
policy as before.

### How the gate layer is pinned and staged

`hpc_monte_carlo` loads the 240 m arrays through the checksummed input
manifest (`data/input-manifest.json`, sha256 `54ba6d76…`, the manifest job
`5332991` and Stage D used; `hpc_smoke.validate_input_manifest` checks
every array's digest). The gate layer is **not** in that manifest, and Stage H
does not add it: rebuilding the manifest would change the digest that pins
the reproduction of job `5332991`.

Instead the layer is read **in place** from the Stage F output directory on
MeluXina, `/project/home/p201619/ANUMA_/out/hpc/stage-f-production-5345446/`
(nothing is copied or restaged), and pinned by three digests:

| File | sha256 | Source |
| --- | --- | --- |
| `terrain_1km_fraction.npy` | `7902c9b98106a5952b520966c7058ab0133729871c4912819418351659b446a9` | Stage F output manifest |
| `terrain_1km_fraction.json` (sidecar) | `335a41dd17946fe4daf5693c66110e6cb7960c70829d29e5eee19d9b7c2dbbe8` | Stage F output manifest |
| `output-manifest.json` (Stage F) | `03a04bab651432dfa830d08da34e6c78638dbfff37733ebac61ac58de0930084` | laptop copy of the job record |

Preflight (on `cpudev`) and the job hash all three and refuse a mismatch; the
login-node wrapper checks their size and mtime. The job also requires the
sidecar to name job `5345446`, `coverage: "full_cap"`, 8° and 1000 m, and the
Stage F manifest to record `passed: true` and list both digests
(`stage_h_gate.check_gate_records`), and runs the app's own sidecar check
(`stage_f.validate_sidecar`, including the grid digest).

### Reproduction check (decisive)

Before the gated draws, the job runs draws 0–7 **with the gate off** on the
same arrays and requires every record (weights, every policy's winner and
top 10) to equal the first 8 lines of job `5332991`'s `draws.jsonl` (sha256
`05784ca6…`). This shows the pipeline Stage H runs is the one that made the
slope-only study, so any difference in the gated study comes from the gate.
On the laptop arrays the 8 records were identical on 2026-10-15.

## Part 2 — DEM-noise check of the gated top 12 (frozen)

The Stage D model is inherited, not restated: `dem_noise_gated` in the
config may hold only scope, labels and the zero-noise reference
(`require_gated_config` refuses any other key), and the frozen `dem_noise`
block (still asserted unchanged by Stage D's own `require_frozen_config`)
supplies everything else:

- noise: PGDA 90 `ERR` as 1σ Gaussian, smoothed with a Gaussian kernel of
  σ = window-median EFFRES / 2.355 (primary) or σ = EFFRES (sensitivity),
  rescaled to unit standard deviation, 40 m FWHM fallback;
- ±5 km window at 20 m, Horn 3×3 slope, 1 km circle, 8° limit, 144-pixel cell;
- N = 200 draws per site and run, seed `20261016`, stream
  `default_rng(SeedSequence(20261016, spawn_key=(run_index, site_index, draw)))`
  with `site_index` the position in the Stage H scope order above;
- decision (primary run only): **noise-unstable** if the 5–95 % interval of
  the ≤ 8° fraction within 1 km is wider than **0.10** or straddles **0.50**;
  borderline width band 0.085–0.115; clause reported per site.

The seed and stream rule are Stage D's, but site indices differ, so no site
reuses a Stage D stream (1350:1392 was Stage D index 2 and is index 0 here).
Its Stage H result is an independent repeat, not a copy.

Every gated site passes the gate at zero noise (≥ 0.50) by construction.
Zero-mean noise tends to lower the fraction (Stage D Δ columns), so sites
close to 0.50 — 1281:1142 at 0.505 — are the ones expected to straddle;
straddling means "pass/fail undecidable under the published DEM error", as
in Stage D.

### Rank-unstable labels (Stage D rule analogue)

Stage D's rule: rank-unstable if in the default top 10 with < 80 % top-10
membership in the weight study (`decided_per_layer`), or default rank within
2 of the cut (ranks 9–12). Stage H applies the same rule to the gated
ranking with the **Stage H** Monte Carlo frequencies.

That rule can only ever name sites of gated ranks 1–12, so Stage H
noise-checks **all 12** instead of only the rule's output. The noise run
therefore does not depend on the Monte Carlo result, and the two parts need
no sequencing; the rule is applied after both, in the same job, as labels
(`rank_unstable_labels` in `stage-h-summary.json`, `rank_unstable` per site
in the check file). A label never changes the scope.

### Zero-noise check (analogue of Stage D check 3)

- the zero-noise window slope equals the Stage B `slope-20m.npy` slice
  within 1e-4° (unchanged), for all 12 sites;
- the zero-noise 1 km fraction equals the **Stage F layer value** at the
  site's 240 m cell within 0.005 (two decimals), for all 12 sites. Stage F
  was built with Stage D's window path, so this ties the noise windows to
  the gate itself. The Stage B table covers only the old top 10 and is not
  used.

## Acceptance checks

Pilot (`stage-h-pilot`):

1. every pinned digest and the Stage F records verified (refused otherwise);
2. reproduction check identical (draws 0–7);
3. 8 gated weight draws: summary `ranking_basis` is
   `terrain_gate_8deg_1km_50pct`, gate digest pinned, draw count 8;
4. gated noise pilot (first 2 scope sites × 8 draws, primary run): scope
   reproduced, serial and parallel bit-identical, zero-noise check, noise
   std / ERR within 1 ± 0.05, primary slope-noise ratio in [0.5, 2],
   noise projection ≤ 0.5 node-hours;
5. projection: Monte Carlo (whole pilot time per pilot draw × 500)
   ≤ 0.5 node-hours, noise ≤ 0.5, fixed + both ≤ 1.0.

Production (`stage-h`): checks 1–3 with 500 draws; the noise check for all
12 sites and both runs with Stage D's production checks (scope, serial =
parallel, zero-noise, primary slope-noise ratio); then the labels.

## Resources

| Item | Pilot | Production |
| --- | --- | --- |
| Profile | `stage-h-pilot` | `stage-h` |
| QOS | `test` | `default` |
| Walltime cap | 00:30 | 01:00 |
| Node-hours cap | 0.5 | 1.0 |
| Node | 1 CPU node, 128 CPUs, 240G, no accelerators | same |

Stage H cap: **1.5 node-hours**, from the held hours of
[hpc-study-plan.md](hpc-study-plan.md). Monte Carlo threads: 32 (output does
not depend on the count); noise workers: 128 (process pool). Expected use is
far below the caps: job `5332991` took 11 minutes on 4 threads for the
same 500 × 3 rankings, the gated 8-draw run takes 16 s on a 4-thread laptop
(conservative projection 0.27 node-hours), and Stage D's 3,200 noise draws
took 91 s on MeluXina (12 sites: about 140 s). Production should use about
0.2–0.35 node-hours. The `cpudev` preflight (hashing about 18 GB) is outside
these caps, as for Stage D.

## Outputs

`out/hpc/stage-h-<mode>-<job>/`: `monte-carlo/` (study summary, draws,
manifest), `dem-noise/` (`draws.jsonl`, `sites/`, `pilot-measurement.json` or
`dem-noise-summary.json`, manifest, log), `pilot-measurement.json` or
`stage-h-summary.json`, `output-manifest.json` (report plus sha256 and bytes
of every file), `progress.log`, `module-list.txt`, `myquota-*`.

## Serving the results

After a passed production run (see [stage-h-run.md](stage-h-run.md)):

- the gated `study-summary.json` is installed at
  `out/hpc/stage-h-monte-carlo/study-summary.json` (data root;
  `stability.gated.monte_carlo_summary`);
- `python -m anuma.stage_h --mode checks-file` writes
  `data/checks/stage-h-noise.json` (committed;
  `stability.gated.dem_noise_checks`): the per-site fields of
  `stage-d-noise.json` plus `ranking_basis`, `terrain_gate.fraction_sha256`,
  `rank_unstable` and `top10_frequency`.

`/stability.json` and `/checks/stage-d-noise.json` serve these (instead of
the slope-only files) when the default `/score` applies the gate and both
files name the loaded layer's `fraction_sha256` and the same threshold;
`?basis=slope_only` or `?basis=slope_and_terrain_1km` selects explicitly.
`/score` then reports `ranking_basis.studies_match: true` for the default
gated ranking, and false for the gate switched off or another threshold.

## What it does not show

- The same limits as Stage D for noise (height error only, as ANUMA reads
  it) and as job `5332991` for weights (±0.1 around the defaults, three
  policies). The gate threshold (50 %), radius (1 km) and slope limit (8°)
  are not varied.
- The gate layer is held fixed in the weight study; DEM noise on the gate
  itself is tested only for the 12 scope sites. A site of rank > 12 whose
  fraction is near 0.50 is not tested.
- The Stage B 20 m check (`stage-b-1km.json`) stays a slope-only result for
  the old top 10; the gate (the "Flat ground 1 km" column) carries that
  information for the gated ranking.

## Ambiguities resolved here

1. **Gate in the input manifest?** No; pinned by digest and read in place
   (above), so the 5332991 manifest digest still pins the 240 m arrays.
2. **Which normalisation ranges?** Over gated cells, as `/score`.
3. **Rule before or after the noise run?** All 12 rule-eligible sites are
   checked; the rule labels them afterwards, in the same job.
4. **Seed.** Stage D's seed and stream rule, keyed by the Stage H order.
5. **Zero-noise reference.** The Stage F layer, all 12 sites, ±0.005.
6. **Basis label.** Studies record `terrain_gate_8deg_1km_50pct`; the API's
   `/score` basis stays `slope_and_terrain_1km`, mapped in
   `stability.gated`.
