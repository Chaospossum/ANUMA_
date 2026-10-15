# Stage E result — daily sunlit hours per pixel, 2026–2044

Not affiliated with NASA, ESA or ispace. Modeled scenario layer; not a
measurement, a mission claim or a landing recommendation.

| Item | Value | Source |
| --- | --- | --- |
| Job | MeluXina `5342908`, COMPLETED, 00:50:23, peak RSS ≈ 115 GiB | `sacct` |
| Commit | `b393697` | preflight receipt |
| Stage A input | job `5334265`, passed, RMSE 0.0198; horizon cube reused, sha256 `a0cdd18f…` | `daily-lit-report.json` |
| Pilot | job `5342899`, 32 days / 768 epochs in 58 s; identical to the Stage A accumulator on 157,492 px; projection 0.96 node-h | `pilot-measurement.json` |
| Period | 2026-01-01 to 2044-09-01 (exclusive): 6,818 days, 163,632 hourly epochs, 19 year chunks | `daily-lit-report.json` |
| SPICE coverage | DE440 + `moon_pa_de440_200625` cover 1549-12-31 to 2650-01-25; window covered | preflight receipt |
| Built-in checks | all passed: lit hours 0–24, fill matches valid mask and Stage A NaN mask | `daily-lit-report.json` |
| Mean lit fraction vs Stage A | RMSE **0.00232**, bias −0.00188, max abs 0.0156, corr 0.99996 over 5,040,704 valid px | job `5342908`, independent recomputation |
| Cost | ≈ 1 CPU node-h (account 4 → 5 of 300 this month) | `myquota` before/after |
| Storage | 47 GB: 43.8 GB daily uint8 + 5.8 GB monthly uint32 cumulative sums | `du`, `output-manifest.json` |

Outputs are in `/project/home/p201619/ANUMA_/out/hpc/daily-lit-production-5342908/`.
Small summaries (report, index, manifest, mean, valid mask) are copied to the
laptop under `out/hpc/` and are not committed.

## Reading the data

`DailyLitCube(root).lit_hours_mean(start, end)` returns the mean sunlit hours
per day for each pixel over `[start, end)` (UTC days). It reads two monthly
cumulative slices and at most the partial-month daily slices, so a range query
does not touch the full 47 GB. Fill values: 255 (daily), 4294967295
(cumulative), NaN (means).

## Independent check

A second reviewer recomputed the following, read-only, on samples of the
production output and on the local summaries:

- All 19 chunks are present with the right lengths (leap years 366 days,
  2044 = 244 days) and shape `(days, 2534, 2534)` uint8; valid pixels are in
  0–24 and off-cap pixels are exactly 255.
- Monthly cumulative differences equal brute-force sums of the daily files for
  six months, including February 2028 and August 2044.
- The final cumulative slice divided by 6,818 × 24 equals the mean lit fraction
  to 2.6e-8.
- `lit_hours_mean` equals brute force (≤ 5e-7) for a year-crossing range, two
  single days, a 132-day range and the full period.
- Manifest hashes match for the index, valid mask, mean and the 2044 chunk.
- All 314,360 pixels with zero mean lit fraction have zero cumulative hours; the
  seasonal cycle is visible (about 5.1 h/day in May–June, 8.5 h/day in
  November–December); no two adjacent days are identical.

## What it does not show

- The Stage A comparison uses a different 18.6-year window (Stage A
  2000–2018); agreement is expected, not a validation gate.
- 563 pixels that Stage A never lit receive up to 7 lit hours over 18.6 years.
  This follows from the different window.
- Lit means any fraction of the solar disc above the local horizon, the Stage A
  rule. It says nothing about power, thermal load or Earth visibility.
- No mission dates are assumed. The cube is meant for a user-chosen date range
  in the GUI; the GUI date picker is not built yet.
- Daily sidecar JSON files and `run-config.json` are not listed in
  `output-manifest.json` (cosmetic).
