# Stage B result — 20 m slope versus the 240 m ranking

Not affiliated with NASA, ESA or ispace. Comparison only; not a landing
recommendation.

| Item | Value | Source |
| --- | --- | --- |
| Job | MeluXina `5334425`, COMPLETED, 00:08:09, peak RSS 45 GB | `sacct` |
| Commit | `5f01305` | preflight receipt |
| Stage A gate | job `5334265`, passed, RMSE 0.0198 | `docs/stage-a-result.md` |
| Pilot | job `5334423`, 16 tiles in 8.1 s | `pilot-measurement.json` |
| Hard-mask agreement | **0.9043** of 5,040,528 valid 240 m cells | job `5334425` |
| 240 m pass, 20 m fail | 377,887 cells (7.5 %) | independent recomputation |
| 240 m fail, 20 m pass | 104,657 cells (2.1 %) | independent recomputation |
| Landable fraction | 53.1 % at 240 m, 47.7 % at 20 m | independent recomputation |
| Top 10 | same 10 sites, same order, 0.04–0.13 km apart | `rank-table-20m-vs-240m.json` |

## Reading the rank table

The top 10 surviving is **guaranteed by construction** and is not evidence of
ranking robustness. At 20 m every pixel keeps its 240 m cell's illumination,
hydrogen and Earth-visibility score; slope enters only as the 8° hard mask. A
site can only drop out if none of its 144 fine pixels passes. The 0.04–0.13
km offsets are the position of the lowest-slope passing pixel inside the same
240 m cell.

What the run does show:

- 240 m slope overstates the landable area: 7.5 % of cells pass at 240 m but
  fail at 20 m, against 2.1 % the other way. Agreement is 0.89–0.91 in every
  1° latitude band.
- Fraction of 20 m pixels at or below 8°, around each top-10 cell centre
  (recomputed locally from the DEM with the same Horn slope):

| Rank (240 m) | Site | In cell (144 px) | Within 500 m | Within 1 km |
| --- | --- | --- | --- | --- |
| 1 | 89.45°S 137.31°W | 1.00 | 0.80 | 0.49 |
| 2 | 89.78°S 155.73°W | 0.85 | 0.25 | **0.10** |
| 3 | 88.81°S 123.64°E | 1.00 | 0.80 | 0.66 |
| 4 | 84.78°S 38.91°E | 1.00 | 0.55 | 0.32 |
| 5 | 85.95°S 3.42°W | 0.92 | 0.52 | 0.38 |
| 6 | 86.00°S 2.10°E | 0.78 | 0.63 | 0.64 |
| 7 | 83.69°S 35.83°W | 0.98 | 0.42 | 0.24 |
| 8 | 85.29°S 36.94°E | 0.99 | 0.87 | 0.50 |
| 9 | 85.48°S 31.39°E | 0.68 | 0.53 | 0.47 |
| 10 | 89.80°S 54.73°E | **0.59** | 0.39 | **0.21** |

Ranks 2 and 10 are mostly steeper than 8° at 20 m within 1 km.

## Caveats

- The agreement map compares the 240 m Horn slope (720 m footprint) with the
  single 20 m pixel at each cell centre, not an any/all/majority rule over the
  144 fine pixels (open question 1).
- Soft layers are nearest 240 m cell values (open question 2).
- The grids are nested by exactly 12 with a 4-fine-pixel (80 m) origin
  offset; cell centres fall on fine-pixel corners.
