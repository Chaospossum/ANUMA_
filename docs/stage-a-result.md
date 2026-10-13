# Stage A result — 240 m Sun model against AVGVISIB

Not affiliated with NASA or ispace. Modeled illumination is ANUMA's model;
`avgvisib_65s_240m_201608` is the measured reference.

## Gate

| Item | Value | Source |
| --- | --- | --- |
| Job | MeluXina `5334265`, COMPLETED, 01:08:35 | `sacct` |
| Commit | `4a633bf` | preflight receipt |
| Window | primary, 2000-01-01T00Z, hourly, 163,048 epochs (ANUMA's choice; no published start) | `validation-report.json` |
| Lit rule | any fraction of the ±0.27° disc above the horizon | `config/sun.yaml` |
| RMSE | **0.0198** (gate 0.05, fixed before the run) | job `5334265` |
| Bias | −0.0035 | job `5334265` |
| Scatter | 0.0195 | job `5334265` |
| Valid pixels | 5,040,704 (the whole 80–90°S cap) | job `5334265` |
| Status | **passed** | job `5334265` |

An independent recomputation from `percent-time-lit.npy` and the measured
layer reproduced these numbers exactly.

## History

- Job `5333886` (commit `5eadb43`) looked up the terrain horizon with local
  azimuth instead of grid azimuth. Its RMSE 0.127 is invalid and is not a
  Stage A result. The bug was found by code review while that job ran; the
  fix is `4a633bf`.
- Job `5334265` reused the 360 horizon azimuths of `5333886`
  (`horizon-seed.txt`). The horizon code is identical between the commits.
- Pilot `5333871` measured the cost; `5333828` stopped on kernel role names.

## Caveats (reported, not used to change the gate)

- **Rim band.** The 240 m DEM is NaN outside 80°S, so rays leaving the cap
  see no terrain and the model is too bright near the edge. RMSE is 0.0169
  without the outer 5 km and 0.060 inside it (bias +0.027; +0.06 in the outer
  1 km).
- **Near the pole.** Within 3 km of the pole (500 pixels) RMSE is 0.118; the
  measured layer shows streaky zeros there.
- **Measured artefacts.** 434 isolated zero pixels among lit neighbours;
  removing them changes RMSE from 0.01982 to 0.01970.
- **Brightest ridges** are slightly under-predicted (bias −0.03 to −0.05 for
  measured > 0.7). Precursor winner (row 1318, col 1219): measured 0.935,
  modeled 0.873.
- **Sensitivity window** (start 2009-04-19) was not computed. The report
  lists it as metadata only.
- Residuals show no structure with longitude (first and second harmonic
  amplitudes ≤ 0.001), and a one-pixel shift raises RMSE to 0.035–0.044.

Residual RMSE by latitude band: 80–82°S 0.025, 82–84°S 0.016, 84–86°S 0.016,
86–88°S 0.015, 88–90°S 0.023.
