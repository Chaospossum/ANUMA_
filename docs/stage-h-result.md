# Stage H result — robustness of the gated ranking

Not affiliated with NASA, ESA or ispace. Screening sensitivity only; not a
landing recommendation.

| Item | Value | Source |
| --- | --- | --- |
| Job | MeluXina `5345720`, COMPLETED, 00:05:33, about 0.09 node-h | `sacct` |
| Commit | `1257cbd` | preflight receipt |
| Plan | frozen 2026-10-15 (revised the same day, before any run) | [stage-h-plan.md](stage-h-plan.md) |
| Pilot | job `5345709`, 01:11; all pass conditions met | `pilot-measurement.json` |
| Gate | Stage F layer, job `5345446`, sha256 `7902c9b9…`, read in place | `stage-h-summary.json` |
| Design | 500 weight draws (same weights, draw for draw, as job `5332991`), 3 policies; 12 sites × 2 noise models × 200 draws | `stage-h-summary.json` |
| Summary | `stage-h-summary.json` sha256 `e455db20…` | `output-manifest.json` |

Outputs are in `/project/home/p201619/ANUMA_/out/hpc/stage-h-production-5345720/`.
The app reads `out/hpc/stage-h-monte-carlo/study-summary.json` (laptop copy,
not committed) and `data/checks/stage-h-noise.json` when the 20 m gate is on.

## Result

**Weights.** Under the default normalisation (`decided_per_layer`) the gated
#1, 1350:1392 (88.81°S 123.64°E, at the Peak Near Shackleton site), wins **479 of 500**
random weight sets (95.8 %). The only other winners are 748:1693 (11, 2.2 %)
and 761:1285 (10, 2.0 %). Under `all_min_max` it wins 500 of 500. The
comparison-only clipped policy `legacy_global_2_98` prefers 753:1671 (358 of
500, 71.6 %), the same clipping effect seen in job `5332991`.

Top-10 membership over the 500 draws (`decided_per_layer`):

| Gated rank | Site | In top 10 | DEM-noise (primary, 200 draws) |
| --- | --- | --- | --- |
| 1 | 1350:1392 | 100 % | passes 200/200 |
| 2 | 748:1693 | 100 % | passes 200/200 |
| 3 | 761:1285 | 100 % | passes 200/200 |
| 4 | 774:1572 | 100 % | passes 200/200 |
| 5 | 1319:1219 | 100 % | passes 200/200 |
| 6 | 1281:1142 | 100 % | **straddles 0.50** (0.4999–0.5048; passes in 94.5 %) |
| 7 | 768:1635 | 78.6 % | passes 200/200 |
| 8 | 1290:1547 | 99.6 % | passes 200/200 |
| 9 | 753:1742 | 99.0 % | passes 200/200 |
| 10 | 1484:1402 | 54.2 % | passes 200/200 |
| 11 | 915:1109 | 2.0 % | passes 200/200 |
| 12 | 728:1081 | 35.8 % | passes 200/200 |

- **Rank-unstable** by the Stage D rule analogue (< 80 % in the top 10, or
  ranks 9–12): 768:1635, 753:1742, 1484:1402, 915:1109, 728:1081.
- **DEM noise:** every 5–95 % interval is narrow (width 0.003–0.006, far below
  0.10). Only rank 6 is noise-unstable, because its flat-ground fraction
  (0.5050) sits on the 50 % line; its gate pass/fail is undecidable under the
  published height error (sensitivity run: passes in 84.0 %).

In plain words: with the 20 m flat-ground rule, the #1 site and ranks 1–6 do
not depend on ±0.1 weight choices or on the published LOLA height error. The
lower half of the top 10 (ranks 7 and 10) and ranks 11–12 are weight-sensitive.

## Checks

- Gate-off draws 0–7 are identical to job `5332991`.
- The frozen 12-site order was reproduced from the arrays before any draw.
- Zero-noise 1 km fractions equal the Stage F layer to ≤ 3e-8 at all 12 sites
  (tolerance 1e-6); zero-noise slopes equal Stage B exactly.
- Serial and 128-worker noise runs are bit-identical; noise std / ERR
  0.9996–1.0004; primary slope-noise ratio 0.695–1.027, inside [0.5, 2].
- A second reviewer recomputed every winner, top-10 frequency, percentile,
  clause and pass rate from the draw records: 0 mismatches.

## What it does not show

- The gate threshold (50 %), radius (1 km) and slope limit (8°) were not
  varied.
- DEM noise on the gate was tested only for these 12 cells, with height error
  only, as ANUMA reads the product (see [stage-d-result.md](stage-d-result.md)).
- Weight ranges are ±0.1 around the defaults; normalisation choice still
  matters (the clipped policy picks a different site).
- Nothing here is a landing recommendation; the 1 km circle is a proxy, not a
  mission ellipse.
