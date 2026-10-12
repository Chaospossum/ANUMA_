# HPC Use

## How HPC relates to the submitted paper

The submitted 28 September paper does not mention HPC because MeluXina access came later. The team is adding HPC now. It does not replace the paper. It checks whether the ranking described by the paper still holds at 20 m. The mission case remains open.

## How the hours are used

The confirmed `p201619` allocation is 300 CPU node-hours for the current month, with 0 used when checked. Shared project and scratch storage are 10 TB each; personal home is 100 GB. There is no GPU, FPGA, or large-memory allocation. The planned spend remains about 80 node-hours, leaving about 220 node-hours unused on purpose if that plan is approved and run.

The first execution is the [guarded 240 m scoring smoke test](meluxina-execution.md): one node, four CPU cores, and a 30-minute walltime, or 2 core-hours and at most 0.5 CPU node-hours. It runs only after current allocation, quota, account, partition, QOS, and staged paths pass preflight. A later small-tile slope benchmark and any full-cap 20 m job require separate review. The proposed full-cap job remains bounded at 40 hours and would compare 20 m results with the 240 m ranking. Statistical tests remain capped at 40 hours.

## How the work is implemented

First, prove the code on a laptop tile. Only then run it on MeluXina. Run one one-node CPU/memory job at a time, cap every walltime, and capture `myquota` before and after every submission. CPU nodes provide 128 cores and 512 GB RAM, but requests must follow measured need. QOS percentage limits do not justify multi-node or 100-node scaling.

Keep the rank table and agreement map. Delete scratch data. Do not add a sun model or a new dataset.

On MeluXina, `login.lxp.lu:8822` is for access, Git/file management,
submission, and monitoring—not ingest, checksumming, environment creation, or
other heavy work. Use the official CPU development allocation
`--reservation=cpudev -q dev` for interactive setup and preflight, then the
guarded batch smoke under `-q test` without a reservation. Prefer
`env/release/2025.1`, record module versions, and use `env/staging/2025.1` only
when module discovery proves a required package is missing from release.

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
