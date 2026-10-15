# Stage F result — full-cap 20 m terrain gate (flat ground within 1 km)

Not affiliated with NASA, ESA or ispace. Screening only; not a landing
recommendation. The 1 km circle is a proxy, not a mission ellipse.

| Item | Value | Source |
| --- | --- | --- |
| Job | MeluXina `5345446`, COMPLETED, 00:02:21, about 0.04 node-h | `sacct` |
| Commit | `c26ebd2` (Stage F compute code identical since `86f7410`) | preflight receipt |
| Plan | frozen 2026-10-15 before any run | [stage-f-plan.md](stage-f-plan.md) |
| Pilot | job `5345262`, 1:05 | `sacct` |
| Inputs | LOLA 20 m DEM `09b7ca80…`; Stage B 20 m slope `08b2e17b…` (job `5334425`) | `terrain_1km_fraction.json` |
| Output | `terrain_1km_fraction.npy`, SHA-256 `7902c9b9…` | sidecar, `sha256sum` |
| Built-in checks | all passed (below) | job report |

The fraction layer and its sidecar are copied to `data/grid/` on the laptop;
they are not committed. Run steps: [stage-f-run.md](stage-f-run.md).

## Result

For each 240 m cell: the share of 20 m pixels within 1 km of the cell centre
whose Horn slope is at most 8°. The gate keeps a cell if the share is at
least 0.50.

- **2,453,272 of 5,040,704** cap cells (48.7 %) pass. The median share is
  0.478. 2,752 cells near the raster edge have a partial circle.
- Of the **2,675,198** cells that pass the 240 m slope rule, the gate removes
  **338,434** (12.7 %).
- Without the gate, the old default top 10 is reproduced exactly. Coupling the
  slope normalisation to the gate has no effect at the default weights.

### New default top 10 (gate on)

| Rank | Cell | Site | Score | Share ≤ 8° within 1 km | Nearest Artemis III point |
| --- | --- | --- | --- | --- | --- |
| 1 | 1350:1392 | 88.81°S 123.64°E | 0.905 | 0.66 | Peak Near Shackleton, 0.1 km |
| 2 | 748:1693 | 84.69°S 39.44°E | 0.878 | 0.61 | Nobile Rim 1, 23 km |
| 3 | 761:1285 | 86.00°S 2.10°E | 0.877 | 0.64 | Malapert Massif, 4.9 km |
| 4 | 774:1572 | 85.42°S 31.81°E | 0.851 | 0.55 | Leibnitz Beta Plateau, 0.4 km |
| 5 | 1319:1219 | 89.44°S 137.86°W | 0.850 | 0.52 | Connecting Ridge, 0.7 km |
| 6 | 1281:1142 | 89.01°S 96.64°W | 0.841 | 0.50 (0.505, inside the DEM-noise band of about ±0.005) | Connecting Ridge Extension, 2.9 km |
| 7 | 768:1635 | 85.10°S 36.47°E | 0.836 | 0.55 | Nobile Rim 1, 10.7 km |
| 8 | 1290:1547 | 87.77°S 94.79°E | 0.794 | 0.57 | Faustini Rim A, 6.5 km |
| 9 | 753:1742 | 84.47°S 42.80°E | 0.782 | 0.68 | Nobile Rim 1, 33 km |
| 10 | 1484:1402 | 87.97°S 148.08°E | 0.765 | 0.63 | Peak Near Shackleton, 32 km |

Read from `POST /score` at the defaults on the laptop (2026-10-15). The old
rank 3 (Peak Near Shackleton) is the new rank 1; the old ranks 1 and 2
(Connecting Ridge, shares 0.49 and 0.10) drop out. Artemis points are
approximate, not NASA centres.

In plain words: half of the cap has enough flat 20 m ground within 1 km. The
gate removes about one in eight cells that the 240 m slope rule allowed, and
it changes the top of the ranking.

## Checks

- **Stage D:** at the 8 Stage D sites the counts of flat 20 m pixels are
  exact: 3863, 821, 5176, 3920, 3701, 1638, 3073 and 2509 of 7860.
- **Stage B:** the shares of default ranks 1–10 match the Stage B table to
  two decimals.
- **Acceptance cells:** 12 cells recomputed through the Stage D code path
  match.
- **Brute force:** 4096 of 4096 sampled cells match a direct count.
- Serial and parallel runs are identical.

## What it does not show

- The 1 km circle is a proxy, not a landing ellipse.
- Only the 8° limit was computed. With another maximum slope the gate is not
  applied (the app says so).
- The Monte Carlo weight study and the DEM-noise study were run for the
  slope-only ranking. With the gate on they do not describe the new list
  (`studies_match` is false in `/score`), and the app shows n/a for them.
- Slope is Stage B's Horn 3×3 at 20 m. Nothing below 20 m (boulders, small
  craters) is modelled.
