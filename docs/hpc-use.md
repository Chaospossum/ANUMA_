# HPC Use

## How HPC relates to the submitted paper

The submitted 28 September paper does not mention HPC because MeluXina access came later. The team is adding HPC now. It does not replace the paper. It checks whether the ranking described by the paper still holds at 20 m. The mission case remains open.

## How the hours are used

The allocation is 300 CPU node-hours and 1 TB. The planned spend is about 80 node-hours. About 220 node-hours stay unused on purpose.

A 2-hour smoke job reads the DEM and writes slope for a small tile. One full-cap job, with walltime capped at 40 hours, computes 20 m slope and the same score as the demo. It then compares that result with the 240 m ranking. Statistical tests are capped at 40 hours.

## How the work is implemented

First, prove the code on a laptop tile. Only then run it on MeluXina. Run one full-cap job at a time, and cap the walltime of every job.

Keep the rank table and agreement map. Delete scratch data. Do not add a sun model or a new dataset.

## Why the statistical tests exist

The tests answer two questions:

- Does the ranking survive a change in the slope limit or weights?
- Does the 240 m ranking hold at 20 m?

The tests are not a way to consume 300 hours. Most tests run on the 240 m grid. A 20 m rerun happens only for a case that already changed the ranking.

## Possible studies

The mission case remains open, and none of these possible studies has been done. They are optional uses of the held node-hours, not work to run for show or describe as a portfolio result before jobs actually run:

- Possible, not done: Weight Monte Carlo on the 240 m grid, measuring how often a site stays in the top ten.
- Possible, not done: Slope-limit sweep, including the paper's 5° goal and 8° threshold.
- Possible, not done: 20 m versus 240 m agreement map.
- Possible, not done: Ellipse check, where a site counts only if a small neighborhood around it also passes.
- Possible, not done: A few 20 m reruns with elevation noise, only for sites that look unstable.
- Possible, not done: Bounded homemade sun model. It can fit the allocation if the horizon is computed once from the DEM and later dates only compare the sun's elevation to that horizon; a fresh ray-march for every pixel and every date does not fit. Prove it on the 240 m grid against the published 2016 average-illumination map before any 20 m run. Do not claim a mission-duration percentage until the team chooses dates. The case stays open. The published map remains the measured layer; the model is a scenario tool.

The 300 CPU node-hours cover these studies if every full-cap job keeps a walltime cap.
