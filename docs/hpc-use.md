# HPC Use

## How HPC relates to the submitted paper

The submitted 28 September paper does not mention HPC because MeluXina access came later. The team is adding HPC now. It does not replace the paper. It checks whether the ranking described by the paper still holds at 20 m. The mission case remains open.

## How the hours are used

The confirmed `p201619` allocation is 300 CPU node-hours for the current
month, with about 1 used on 12 October. Shared project and scratch storage are
10 TB each; personal home is 100 GB. There is no GPU, FPGA, or large-memory
allocation. The [revised bounded study plan](hpc-study-plan.md) caps stages
A–D at 165 node-hours within a 220-node-hour ceiling, holds 55 hours, and
keeps at least 80 allocation hours in reserve.

The 240 m 500-draw precursor already exists as job `5332991` and must not be
rerun. The [execution guide](meluxina-execution.md) and
[study plan](hpc-study-plan.md) define the blocking Sun-model validation and
later 20 m gates.

## How the work is implemented

First, prove the code on a laptop tile. Only then run it on MeluXina. Run one one-node CPU/memory job at a time, cap every walltime, and capture `myquota` before and after every submission. CPU nodes provide 128 cores and 512 GB RAM, but requests must follow measured need. QOS percentage limits do not justify multi-node or 100-node scaling.

Keep declared outputs and accounting evidence, then delete job-local scratch.
The Sun model uses the existing DEM and measured illumination product plus
externally staged, checksummed DE440/frame kernels; it downloads no dataset.

On MeluXina, `login.lxp.lu:8822` is for access, Git/file management,
submission, and monitoring—not ingest, checksumming, environment creation, or
other heavy work. Use the official CPU development allocation
`--reservation=cpudev -q dev` for interactive setup and preflight, then the
guarded batch smoke under `-q test` without a reservation. Record module
versions; release/2025.1 has Python 3.13.1, while Python 3.12.3 is available
only under release/2024.1. Never use staging without a documented gap.

## Why the statistical tests exist

The tests answer two questions:

- Does the ranking survive a change in the slope limit or weights?
- Does the 240 m ranking hold at 20 m?

The tests are not a way to consume 300 hours. Most tests run on the 240 m grid. A 20 m rerun happens only for a case that already changed the ranking.

The normalization decision is fixed in `config/layers.yaml`: illumination uses
valid-pixel min–max; slope keeps the 8° hard mask and uses min–max only inside
that mask; hydrogen retains 2nd/98th percentile clipping; Earth visibility
retains 2nd/98th clipping. The former global 2/98 policy and an all-min–max
policy remain comparison cases, not defaults.

## Possible studies

The mission case remains open, and none of these possible studies has been done. They are optional uses of the held node-hours, not work to run for show or describe as a portfolio result before jobs actually run:

- Completed precursor: job `5332991`, the 500-draw clip-and-weight Monte Carlo.
- Possible, not done: Slope-limit sweep, including the paper's 5° goal and 8° threshold.
- Possible, not done: 20 m versus 240 m agreement map.
- Possible, not done: Ellipse check, where a site counts only if a small neighborhood around it also passes.
- Possible, not done: A few 20 m reruns with elevation noise, only for sites that look unstable.
- Possible, not done: Bounded homemade sun model. It can fit the allocation if the horizon is computed once from the DEM and later dates only compare the sun's elevation to that horizon; a fresh ray-march for every pixel and every date does not fit. Prove it on the 240 m grid against the published 2016 average-illumination map before any 20 m run. Do not claim a mission-duration percentage until the team chooses dates. The case stays open. The published map remains the measured layer; the model is a scenario tool.

No statement here guarantees that every study will fit. Each stage requires a
pilot, measured cost estimate, preflight, hard walltime, and passing prior gate.
