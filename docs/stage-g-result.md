# Stage G result — longest continuous darkness and sunlight (240 m)

Not affiliated with NASA, ESA or ispace. This is ANUMA's model, not a
measurement. The 2026–2044 span is a precompute period, not a mission
interval.

| Item | Value | Source |
| --- | --- | --- |
| Job | MeluXina `5345264`, COMPLETED, 00:55:58, about 0.93 node-h (cap 3.0) | `sacct` |
| Commit | `86f7410` (darkness code identical to `ce0d391`) | preflight receipt |
| Plan | [stage-g-darkness-plan.md](stage-g-darkness-plan.md) | |
| Pilot | job `5345216` | `pilot-measurement.json` |
| Period | 2026-01-01T00Z to 2044-09-01T00Z (exclusive), hourly: 6,818 days, 163,632 epochs | `darkness-index.json` |
| Built-in checks | all true (below) | `darkness-report.json` |

Outputs: `/project/home/p201619/ANUMA_/out/hpc/darkness-production-5345264/`,
copied to `out/hpc/darkness-production-5345264/` on the laptop (period maps
and yearly files first, then `monthly/`, 8.6 GB; the three month files'
SHA-256 match `output-manifest.json`). Not committed.

## Result

A dark run is a stretch of consecutive hourly samples with no part of the
Sun above the modelled horizon.

- **314,360 pixels (6.2 %)** are never lit in the 2026–2044 hourly samples.
- Longest continuous darkness, other pixels: median **21.8 days** (IQR
  19.2–26.8). None lies between 30 and 45 days. About 16 % (763,000 pixels)
  have seasonal darkness from 45 days to more than a year.
- Short longest nights are rare: **6,970 pixels (0.14 %)** are at or below
  15 days, and 123 pixels are at or below 10 days (105 strictly below).
- The longest sunlit stretch is typically 11.8 days (95th percentile
  15.5 days).
- At every site below, the worst calendar year is within a few hours of the
  full-period value. For the old ranking the worst year is mostly 2030 (8 of
  12 sites); otherwise 2026, 2032 or 2033.

### Old default top 12 (slope-only ranking)

| Default rank | Cell | Longest dark | When (UTC; run end exclusive) | Longest sunlit | Lit (2026–2044) |
| --- | --- | --- | --- | --- | --- |
| 1 | 1318:1219 | 198 h = 8.3 d | 2030-03-13 15Z → 03-21 21Z | 260 d (cap maximum) | 86.6 % |
| 2 | 1292:1255 | 65 h = 2.7 d (shortest longest night on the cap) | 2026-05-11 10Z → 05-14 03Z | 176 d | 90.0 % |
| 3 | 1350:1392 (Peak Near Shackleton) | 138 h = 5.8 d | 2032-01-29 16Z → 02-04 10Z | 175 d | 88.0 % |
| 4 | 753:1681 | 237 h = 9.9 d | 2032-02-02 00Z → 02-11 21Z | 24.6 d | 74.5 % |
| 5 | 756:1236 | 275 h = 11.5 d | 2030-03-01 18Z → 03-13 05Z | 86.5 d | 79.9 % |
| 6 | 761:1285 | 266 h = 11.1 d | 2030-03-01 14Z → 03-12 16Z | 147.0 d | 74.5 % |
| 7 | 619:799 | 233 h = 9.7 d | 2030-03-03 04Z → 03-12 21Z | 25.1 d | 74.2 % |
| 8 | 791:1624 | 353 h = 14.7 d | 2030-02-23 05Z → 03-09 22Z | 87.8 d | 75.9 % |
| 9 | 779:1564 | 217 h = 9.0 d | 2026-05-19 04Z → 05-28 05Z | 29.1 d | 68.3 % |
| 10 | 1252:1287 | 247 h = 10.3 d | 2030-02-22 20Z → 03-05 03Z | 117.4 d | 79.1 % |
| 11 | 1277:1142 | 204 h = 8.5 d | 2033-01-10 18Z → 01-19 06Z | 88.2 d | 77.6 % |
| 12 | 1206:1114 | 139 h = 5.8 d | 2030-03-05 15Z → 03-11 10Z | 163.2 d | 77.5 % |

"Lit" is the Stage E mean of daily lit hours over 2026–2044 (job `5342908`).

### New default top 10 (with the 20 m flat-ground gate)

Read from the period maps (`longest-dark-hours.npy`,
`longest-dark-start-hour.npy`, `longest-lit-hours.npy`, dates from
`darkness-index.json`) and the Stage E cube (lit share), through
`POST /score` on the laptop. Ranks as in [stage-f-result.md](stage-f-result.md).
No site is edge-censored.

| Rank | Cell | Site | Longest dark | When (UTC; run end exclusive) | Longest sunlit | Lit (2026–2044) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 1350:1392 | Peak Near Shackleton | 138 h = 5.8 d | 2032-01-29 16Z → 02-04 10Z | 175.2 d | 88.0 % |
| 2 | 748:1693 | Nobile Rim 1, 23 km | 280 h = 11.7 d | 2031-02-10 21Z → 02-22 13Z | 20.6 d | 67.1 % |
| 3 | 761:1285 | Malapert Massif | 266 h = 11.1 d | 2030-03-01 14Z → 03-12 16Z | 147.0 d | 74.5 % |
| 4 | 774:1572 | Leibnitz Beta Plateau | 301 h = 12.5 d | 2034-12-02 07Z → 12-14 20Z | 175.8 d | 73.2 % |
| 5 | 1319:1219 | Connecting Ridge | 374 h = 15.6 d | 2033-01-10 04Z → 01-25 18Z | 142.5 d | 74.0 % |
| 6 | 1281:1142 | Connecting Ridge Extension | 215 h = 9.0 d | 2033-01-10 18Z → 01-19 17Z | 88.0 d | 80.5 % |
| 7 | 768:1635 | Nobile Rim 1, 10.7 km | 258 h = 10.8 d | 2030-02-24 00Z → 03-06 18Z | 22.7 d | 68.4 % |
| 8 | 1290:1547 | Faustini Rim A | 383 h = 16.0 d | 2031-02-04 23Z → 02-20 22Z | 26.4 d | 67.6 % |
| 9 | 753:1742 | Nobile Rim 1, 33 km | 285 h = 11.9 d | 2034-11-29 08Z → 12-11 05Z | 22.3 d | 64.2 % |
| 10 | 1484:1402 | Peak Near Shackleton, 32 km | 461 h = 19.2 d | 2033-12-26 07Z → 2034-01-14 12Z | 25.5 d | 68.5 % |

In plain words: the new #1 has the shortest longest night in the new top 10
(5.8 days); the others go 9 to 19 days without any sunlight at least once in
2026–2044. Rank 2 of the old list (1292:1255, 2.7 days) is the best on the
cap for this measure, but it fails the 20 m flat-ground gate (share 0.10).

## Checks

- All built-in checks of the job are true.
- Lit hours per pixel-day equal Stage E: **0 mismatched pixel-days** over
  6,818 days.
- A reviewer recomputed 16 pixels by brute force over the full 18.7 years
  with the lit rule: exact match.
- Month windows from the reader across year boundaries match brute force.

## What it does not show

- It is ANUMA's model on the Stage A 240 m horizon, not a measurement.
- Hourly sampling: a run of N hours lasted between N − 1 and N + 1 hours.
- "Lit" means any sliver of the solar disc above the horizon. It is not
  usable power: no mast height, panel tilt or thermal model.
- Runs that touch the start or end of the period are edge-censored and may be
  longer: 2,811 + 1,846 pixels, besides the never-lit ones.
- The horizon is at 240 m, not 20 m.

## In the app

- Table column **Longest dark** (days), after Sunlight; with a sunlight month
  window it shows that window's longest dark run (runs clipped to the window).
- Site card: "Longest darkness 5.8 days (29 Jan–4 Feb 2032)" and "Longest
  sunlight 175 days". The answer banner names #1's longest dark stretch.
- Map layers `longest_dark` (month windows supported) and `longest_lit`; black
  marks never-lit pixels. Display only, never scored.
- A long multi-year month window can take up to about 10 s the first time
  the map layer is drawn; it is cached after that.
- Without the output on disk the fields are null and the layers are listed as
  not available. Month windows need `monthly/`; without it only whole calendar
  years answer.
