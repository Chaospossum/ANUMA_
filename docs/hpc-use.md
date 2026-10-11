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

## Proposed bounded Monte Carlo

The mission case is still open. A bounded Monte Carlo is a possible use of the held node-hours, not a way to spend them for show. The proposed simulation would draw many weight sets and slope limits on the cheap 240 m grid and record how often each site remains in the top ten. It would rerun at 20 m only for sites that are unstable. That is the simulation. It would show ispace which sites survive requirement changes. This is proposed and has not been done; it becomes a useful portfolio line only after jobs actually run.

The mission case is still undecided. The tests wait until the team chooses the requirements.
