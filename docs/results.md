# ANUMA results

One page, current as of 16 October 2026. Every number names the run it comes
from; details are in the linked stage records.

ANUMA is an independent student project. It is not affiliated with or endorsed
by ispace, ESA, NASA or the MAGPIE mission team. It is a screening tool: it
suggests an order of candidate sites and says why. It is not a landing
recommendation.

## Data

All public:

- Terrain and slope: NASA LOLA 20 m south-pole DEM (Barker et al. 2023), slope
  on the 240 m grid and at 20 m.
- Height error: the same product's error layer (display and Stage D/H only).
- Sunlight: ANUMA's sun model on the LOLA terrain (LOLA flies on LRO; the
  challenge paper names LROC, the camera on the same spacecraft), checked against NASA's
  published average-illumination map (Mazarico et al. 2011).
- Hydrogen: Lunar Prospector neutron spectrometer, 0.5° (Feldman et al. 2001).
  A regional preference, not a water claim.
- Earth view: elevation of the sub-Earth point above the local horizon at mean
  libration, from the DEM. Libration (about ±7°) makes contact near the pole
  intermittent; contact windows are not modelled.
- Artemis III: NASA's 2022 and 2024 candidate-region names, with approximate
  points from Goossens et al. (2025), not NASA centres.

Sources and URLs: [SOURCES.md](../SOURCES.md).

## Default result (weights sunlight 50 / hydrogen 30 / Earth view 20, slope ≤ 8°, flat-ground rule on)

Hard rules: slope ≤ 8° on the 240 m grid, and at least 50 % of the 20 m
pixels within 1 km at ≤ 8° (Stage F). Of 5,040,704 cells, 2,365,330 fail the
slope rule and a further 338,434 fail the flat-ground rule. Sites closer than
10 km count as one.

| # | Site | Score | Longest dark | Top 10 in weight draws | Flat within 1 km | DEM noise | Nearest Artemis III point |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 88.81°S 123.6°E | 0.905 | 5.8 d | 100 % (#1 in 95.8 %) | 66 % | passes | Peak Near Shackleton, 0.1 km |
| 2 | 84.69°S 39.4°E | 0.878 | 11.7 d | 100 % | 61 % | passes | Nobile Rim 1, 23.4 km |
| 3 | 86.00°S 2.1°E | 0.877 | 11.1 d | 100 % | 64 % | passes | Malapert Massif, 4.9 km |
| 4 | 85.42°S 31.8°E | 0.851 | 12.5 d | 100 % | 55 % | passes | Leibnitz Beta Plateau, 0.4 km |
| 5 | 89.44°S 137.9°W | 0.850 | 15.6 d | 100 % | 52 % | passes | Connecting Ridge, 0.7 km |
| 6 | 89.01°S 96.6°W | 0.841 | 9.0 d | 100 % | 50 % | on the line | Connecting Ridge Extension, 2.9 km |
| 7 | 85.10°S 36.5°E | 0.836 | 10.8 d | 78.6 % | 55 % | passes | Nobile Rim 1, 10.7 km |
| 8 | 87.77°S 94.8°E | 0.794 | 16.0 d | 99.6 % | 57 % | passes | Faustini Rim A, 6.5 km |
| 9 | 84.47°S 42.8°E | 0.782 | 11.9 d | 99.0 % | 68 % | passes | Nobile Rim 1, 33.0 km |
| 10 | 87.97°S 148.1°E | 0.765 | 19.2 d | 54.2 % | 63 % | passes | Peak Near Shackleton, 32.3 km |

Sources: ranking and flat ground, Stage F (job 5345446); longest darkness,
Stage G (job 5345264, hourly 2026–2044, modelled); weight draws and DEM noise,
Stage H (job 5345720, 500 draws ±0.1; 200 noise draws per site).

In plain words: the #1 site and ranks 1–6 do not depend on ±0.1 weight choices
or on NASA's published height error. Ranks 7 and 10 are weight-sensitive;
rank 6 sits exactly on the 50 % flat-ground line.

## How it was checked

| Check | Result | Record |
| --- | --- | --- |
| Sun model vs NASA's illumination map | RMSE 0.0198 (limit 0.05, set before the run) | [Stage A](stage-a-result.md), job 5334265 |
| 240 m slope vs 20 m slope | 7.5 % of 240 m "landable" cells fail at 20 m; 2.1 % the other way | [Stage B](stage-b-result.md), job 5334425 |
| Daily sunlight cube vs Stage A | RMSE 0.0023 | [Stage E](stage-e-result.md), job 5342908 |
| Height error on the 1 km flat-ground share | intervals 0.003–0.006 wide, far below 0.10 | [Stage D](stage-d-result.md), [Stage H](stage-h-result.md) |
| Weight robustness of the gated ranking | #1 wins 479 of 500 draws | [Stage H](stage-h-result.md) |
| Cost | about 9 CPU node-hours on MeluXina | myquota |

Normalisation matters more than weights: with the old 2–98 % clip a different
site (84.83°S 38.2°E) wins in 71.6 % of draws. That is why the app shows the
policy it uses.

## History: the ranking before the flat-ground rule

Slurm job 5332991 ranked on the 240 m slope rule only (500 weight draws). Its
winner was 89.45°S, 137.3°W on the Shackleton connecting ridge (illumination
0.93), in 99.8% of draws, 0.40 km from the Connecting Ridge point in Goossens
et al. (2025); the old 2/98 clip selected 83.66°S in 500/500 draws. Stage B
showed that 240 m slope overstates landable ground, so Stage F added the 20 m
flat-ground rule; that cell has 49 % flat ground within 1 km and drops out,
while a cell next to it (the current #5, 89.44°S 137.9°W) passes with 52 %. The
table above is the current ranking.

## What this does not show

- The thresholds (8°, 50 %, 1 km) were not varied; the 1 km circle stands in
  for a landing ellipse.
- Darkness counts any sliver of the Sun above the 240 m horizon; it is not a
  power or thermal result.
- No mission dates: time windows are scenarios.
- Keep-out uses only points with published coordinates (Artemis III); other
  missions' sites are not loaded.
- ispace's Mission 2 sites (Mare Frigoris, about 60.5°N) are outside the
  80–90°S map, so they could not be compared.
- The mission case is still open: no ellipse, mission dates or payload
  requirements from a real mission are used.
- Orbital debris affects trajectories, not site choice, so it is not modelled.

## Planned

- Rover reach from each landing point; the customer's own ellipse as an input.
- Real mission dates and libration-resolved Earth contact windows.
- Geometry for Peak near Cabeus B, Mons Mouton Plateau, Mons Mouton and Slater
  Plain, which have no citable centre.
- Other missions' declared zones in the keep-out list.

## API contracts

### Keep-out

`POST /score` accepts an optional `keepout` object: `points` as `{lon, lat}`
pairs, `radius_km`, and `mode` of `hard` or `penalty`. `radius_km` is required
whenever keep-out is sent; there is no default plume radius. Penalty mode also
requires `penalty`. Hard mode removes the disk from the feasible set; penalty
mode subtracts the value from the weighted score inside the disk. These are
exclusion zones in the Artemis Accords "safety zone" sense; no site is owned or
claimed (Outer Space Treaty, Article II).

### Illumination window

`POST /score` accepts `illumination_months`, `{"start": "YYYY-MM", "end":
"YYYY-MM"}` (end month inclusive, UTC days). The illumination layer is then the
Stage E mean lit hours per day over that window divided by 24. Months outside
2026-01..2044-08 are rejected with 400; a malformed month or a start after the
end gives 422. The window is a modelled scenario, not mission dates.

### Grouping, exclusion layer, scenario link

- `min_separation_km` (0.5–100, default 10) sets how close candidates may be
  before they count as one site; it is recorded in CSV and GeoJSON exports.
- `GET /layer/excluded.png` colours each cell by the first default hard rule it
  fails (red slope, yellow flat ground); `GET /site/{row}/{col}` returns the
  checks with the values tested.
- The app keeps the scored scenario in its URL; *Copy scenario link* copies it.
