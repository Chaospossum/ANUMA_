# ANUMA MeluXina HPC Study Plan

## Scope and non-negotiable limits

This plan covers stages A–D after the completed 240 m clip-and-weight Monte
Carlo precursor on MeluXina project `p201619`. It authorizes no submission by
itself and records no new study result.

- Ceiling across the precursor and stages A–D: **220 CPU node-hours**.
- Run **one CPU node at a time**: 128 cores and 512 GB RAM.
- MeluXina bills the whole node-hour, even when fewer cores are requested.
- No multi-node jobs, GPU, FPGA, or large-memory jobs.
- Capture `myquota` immediately before and after every submitted job. After
  each stage, preserve `sacct` (elapsed, state, CPU, and peak-memory fields)
  beside the delayed `myquota` capture before authorizing the next stage.
- Use project storage for immutable inputs/results and
  `/project/scratch/p201619` only for job-local, disposable work.
- Preserve input, environment, Git, config, output, and accounting manifests.
- Do not invent mission dates, study results, or Artemis candidate centres.

The A–D caps commit at most **165 node-hours**. The other **55 node-hours** of
the 220-hour study ceiling are held. With about one of the monthly 300 CPU
node-hours used as of 12 October, completing every cap would still leave about
134 node-hours, safely above the required **80-node-hour allocation reserve**.
`myquota` is delayed, so current captures—not that arithmetic—govern every
preflight.

| Work | QOS | One-node walltime cap | Maximum node-hours |
| --- | --- | ---: | ---: |
| Precursor — completed job `5332991`; do not rerun | recorded job | recorded job | already used |
| A — 240 m Sun-model validation | `test` pilot, then `default` | 15:00 | 15 |
| B — full-cap 20 m slope/ranking comparison | `default` | 40:00 | 40 |
| C — 20 m Sun model, ≤20 ±5 km site windows | `default` | 60:00 | 60 |
| D — 20 m Monte Carlo, noise, and ellipse check | `default` | 50:00 | 50 |
| **A–D maximum committed** |  |  | **165** |
| **Held within 220-hour ceiling** |  |  | **55** |

Caps are maxima, not targets. A failed gate or early completion stops spend;
unused hours return to the held remainder.

## Global execution and stop gates

Before any stage:

1. Start every stage with a small `test`-QOS pilot, derive a documented
   one-node cost and memory estimate, review it, then run preflight for the
   bounded production job. A pilot never bypasses a scientific gate.
2. Work from a clean, reviewed Git commit with a checksum-pinned environment.
3. Confirm account `p201619`, CPU partition, selected QOS, current allocation,
   storage quota, project paths, and scratch paths through fail-closed preflight.
4. Validate input manifests and per-file SHA-256. Inputs must be real, not
   synthetic.
5. Re-ingest must have produced
   `illumination_affine_revision: pds3-pixel-center-v2`. Older or missing
   provenance stops the precursor and every later stage.
6. Keep downloads, ingest, checksumming, and computation off login nodes.
7. Repeat fixed resource caps in the submission command and job; reject
   mismatched account, QOS, node count, core count, memory, walltime, receipt,
   manifest, or Git commit.
8. Create a unique output directory; never overwrite prior output.
9. Record `module list`, package versions, input/output checksums, stdout,
   stderr, elapsed time, CPU use, peak memory, and `myquota` before/after.
10. Delete job-local scratch after outputs and manifests are safely preserved.
11. Run only one ANUMA CPU job at a time. Require a hard walltime at or below
    the stage cap and stop when a pilot estimate cannot fit the cap.

Stages are sequential. A gate failure stops downstream work; it is not a
reason to consume held hours on an alternative run.

## Precursor — 240 m clip-and-weight Monte Carlo

The precursor is already implemented and remains the first study. Its
500-draw result is job `5332991`; preserve its outputs and accounting and
**do not rerun it**.

### Recorded execution

Treat the precursor as prior spend, not a new reservation in A–D. Verify its
job manifest, `sacct`, and before/after `myquota` records before Stage A.

### Inputs

- Corrected, re-ingested 240 m arrays and input manifest.
- Affine revision `pds3-pixel-center-v2`.
- Normalization policies, weight ranges, draw counts, and seed from
  `config/layers.yaml`.

### Outputs

- Top-10 membership frequency by policy.
- Winner stability.
- Paired clipped-versus-unclipped winner-class changes.
- Per-draw records, checksummed output manifest, environment/accounting logs.

### Stop gates

- Do not run before corrected re-ingest and manifest validation.
- Stop if job `5332991` lacks the corrected affine stamp or required manifests.
- Do not recreate the pilot or full draw to repair missing evidence.

## Stage A — 240 m Sun-model acceptance

Stage A is a blocking method-validation stage, capped at 15 node-hours.

### Method

Compute the terrain horizon once per pixel from the 240 m DEM at 360 azimuths,
including far terrain. Reuse that horizon at every epoch. A timestep is lit
when any fraction of the solar disc is above the horizon. Do not average the
visible-disc area. The disc radius stays ±0.27°. Sun direction must come from the
hash-pinned `spiceypy` DE440 path in `hpc/requirements-sun.lock` and externally
checksummed kernels; there is no hand-written orbital approximation. A fresh
whole-cap ray march for every date is explicitly out of budget.

The first test is reproduction of `avgvisib_65s_240m_201608` under its
documented temporal definition. The published map
remains ANUMA's measured illumination layer; the homemade model is a scenario
tool, not a replacement measurement.

### Resources

- Pilot on one CPU node, QOS `test`, at most 00:30 (0.5 node-hours), 128
  workers, no accelerators. It times 4 horizon azimuths and 256 epochs on the
  full grid, then writes `pilot-measurement.json` with a projected one-node
  cost. It does not apply the RMSE gate.
- Production on one CPU node, QOS `default`, hard walltime 14:30 (14.5
  node-hours), 128 workers, no accelerators. Sunlight epochs run in row-tile
  processes. Horizon azimuths and sunlight accumulators are checkpointed so a
  timeout keeps partial output.
- The production walltime plus the pilot stays at or below 15 node-hours.
- No downstream job may overlap it.

### Inputs

- Corrected 240 m DEM and affine-fix manifest.
- Published `AVGVISIB` 2016 map and metadata.
- Frozen `config/sun.yaml`, `config/sun-epoch.yaml`, projection, horizon
  algorithm, DE440 kernel manifest, and environment.
- The validation gate uses the primary window, which starts at
  2000-01-01T00:00:00Z because that start is ANUMA's choice. NASA publishes
  no start or end for `AVGVISIB_65S_240M_201608`, and the filename is not a
  date. 18.6 years means 18.6 × 365.25 days, sampled hourly. The sensitivity
  window starts 9.3 × 365.25 days later, is reported separately, and cannot
  be used to pick a passing start.

### Outputs

- Reusable 360-azimuth 240 m terrain-horizon artifact and checksum.
- Percent time lit, longest continuous darkness in hours, and a ≥80% layer.
- Model-versus-published RMSE, bias, and residual scatter under
  `out/sun-validation/`.
- Provenance, resource, and accounting manifests.

### Blocking gate

The proposed and fixed stop threshold is RMSE **0.05**. If RMSE exceeds it,
stop: stages B–D do not start. Do not tune the threshold after seeing output.
`stop_rmse` stays 0.05 and `tune_threshold_after_run` stays false. The job
refuses a changed RMSE threshold.

The scenario default is calendar year 2029, but it does not substitute for the
published map's validation interval. The mission case remains open.

### Operator status, 12 October 2026

MeluXina project `p201619` reports that
`/project/home/p201619/spice-kernels/kernel-manifest.json` is the kernel pin.
Roles `DE440`, `moon_pa_de440_200625`, `moon_de440_250416.tf`, and `naif0012`
are present and were SHA-256 verified twice. Digests stay in that manifest.
`.venv-hpc-sun` is built. An operator-reported geometry sanity check says
south-pole Sun elevation over calendar 2029 stays within ±1.53°. No Slurm job
id was supplied for that check. It is not the AVGVISIB validation gate and not
a scored illumination result.

Update, 13 October 2026: the RMSE 0.05 gate against
`avgvisib_65s_240m_201608` passed in job `5334265` (RMSE 0.0198). See
[stage-a-result.md](stage-a-result.md).

## Stage B — full-cap 20 m slope and ranking comparison

Stage B asks only whether the 240 m ranking survives the full-cap 20 m slope
and score calculation.

### Resources

- Pilot on `test` ≤00:30, then one node on `default`, hard walltime ≤40:00,
  ≤40 node-hours.
- Use bounded tiling/checkpointing within that node and its 512 GB RAM.

### Inputs

- Passed Stage A acceptance record.
- Checksum-pinned 20 m DEM and coverage manifest.
- Frozen 8° hard slope mask, decided per-layer normalization, demo score
  weights, and accepted 240 m precursor ranking.
- Verified tile/halo, merge, restart, and equivalence procedure.

### Outputs

- **Agreement map only** for 20 m versus 240 m.
- **Rank table only** for 20 m versus 240 m.
- Operational manifests/logs needed to reproduce those two products.

### Stop gates

- Stop if Stage A did not pass.
- Stop if the 20 m source, full-cap coverage, tile boundaries, memory estimate,
  or small reference-tile equivalence is unverified.
- Review the agreement map and rank table before selecting any Stage C site or
  starting Stage D.

## Stage C — 20 m Sun model for at most 20 sites

Stage C is site-bounded, not a second full-cap illumination run.

### Resources

- Pilot on `test` ≤00:30, then one node on `default`, hard walltime ≤60:00,
  ≤60 node-hours.
- At most 20 sites, each in a ±5 km 20 m window.

### Site set

Use the Stage B shortlist, then add any site that was in the 240 m top 10 but
was dropped by the 20 m list. If the union exceeds 20, stop for an explicit
scientific-owner selection rule before submission; do not silently truncate or
invent Artemis centres.

### Required inputs

- Passed Stage A and reviewed Stage B outputs.
- Frozen union-of-lists site manifest and complete ±5 km 20 m terrain windows.
- Reusable Sun/horizon method accepted in Stage A.
- Actual mission interval and sampling definition: **[DATES]**.

The executable must refuse to run while `[DATES]` is unset, literal, empty, or
otherwise unvalidated.

### Outputs

- Illumination fraction for each selected site over `[DATES]`.
- Longest darkness interval for each selected site over `[DATES]`.
- Site/input/method/environment/accounting manifests.

### Stop gates

- Missing real dates is a hard stop.
- Do not quote a mission-duration illumination percentage before dates exist.
- Stop if more than 20 sites are requested or a site lacks adequate 20 m data.

## Stage D — 20 m Monte Carlo and targeted DEM-noise reruns

The stability rule, DEM-noise model and ellipse check were frozen in
[stage-d-plan.md](stage-d-plan.md) on 2026-10-15, before any noise run
(config `hpc_studies.dem_noise`). Implementation notes and launch commands:
[stage-d-run.md](stage-d-run.md).

Stage D may use up to all 128 cores of one node after the pilot demonstrates
the need. It does not scale across nodes.

### Resources

- Pilot on `test` ≤00:30, then one CPU node on `default`, hard walltime
  ≤50:00, ≤50 node-hours.
- `concurrent.futures`/multiprocessing is bounded to measured local workers,
  at most 128. Same-seed serial and parallel outputs must be bit-identical.

### Inputs and method

- Reviewed Stage B arrays and rank table.
- Frozen weight, normalization-policy, seed, draw-count, LOLA elevation-noise
  model, and landing-ellipse check configuration.
- Reuse Stage B normalized/source arrays for weight and clipping draws; these
  draws must not recompute full-cap slope.
- Use the initial draws to identify unstable sites under a predeclared
  stability rule.
- Apply DEM-noise slope reruns only to those unstable sites and their declared
  local neighborhoods.
- Never express DEM noise as hundreds of full-cap 20 m slope jobs.

### Outputs

- Top-10 membership and winner/rank stability under weights and clipping.
- Declared unstable-site list and the rule that produced it.
- Site-local LOLA elevation-noise slope/rank sensitivity for unstable sites
  only, plus the declared ellipse check.
- Per-draw, environment, resource, and checksum manifests.

### Stop gates

- Stop if Stage B arrays cannot be reused without recomputation.
- Stop before noise reruns if the instability rule or DEM-noise model is not
  frozen and justified.
- Reject more than one node, more than 128 workers, walltime over 50 hours, or
  any full-cap slope rerun per Monte Carlo draw.

## Stage E — 240 m daily lit hours

Done. Stage E reuses the Stage A horizon cube and lit rule to store sunlit
hours per UTC day for 2026-01-01 to 2044-09-01, so the web GUI can average any
date range. Reallocated from the 55 held hours on 15 October 2026 (cap 3.5
node-hours). Pilot job `5342899` passed in 58 s; production job `5342908`
completed in 00:50:23 for about 1 node-hour. See
[stage-e-daily-lit-plan.md](stage-e-daily-lit-plan.md) and
[stage-e-result.md](stage-e-result.md).

## Stage G — 240 m longest continuous dark and lit periods

Planned, not run. Stage G replays Stage E's hourly epochs, using the same lit
rule, horizon cube and kernels. It stores the longest dark and lit run per
pixel, and per-month dark-run summaries that give the exact longest dark run
of any month window. It needs a reviewed draw of at most 3.5 node-hours from
the held hours: a pilot of 0.5 at QOS `test`, then a production of 3.0 at
`default`. Production goes ahead only if the pilot projects at most 2.4. See
[stage-g-darkness-plan.md](stage-g-darkness-plan.md).

## Stage F — full-cap 20 m terrain gate

Planned, not run. Per user decision of 15 October 2026 a 240 m cell is
eligible only if at least 50 % of the 20 m pixels within 1 km of its centre
have Horn slope ≤ 8°. Stage F computes that fraction for every cell from the
Stage B `slope-20m.npy` (job `5334425`), with Stage D's zero-noise geometry.
Proposed from the held hours: cap 2.0 node-hours (pilot 0.5 on `test`,
production 1.0 on `default`). See [stage-f-plan.md](stage-f-plan.md) and
[stage-f-run.md](stage-f-run.md).

## Review and release discipline

Each stage produces evidence, not a mission claim. Review scientific outputs
and accounting before authorizing the next stage. Keep the published
illumination map as measured evidence, label modeled illumination clearly, and
retain the 55-node-hour study hold and at least 80 node-hours of the allocation
unless a new reviewed plan explicitly reallocates them.
