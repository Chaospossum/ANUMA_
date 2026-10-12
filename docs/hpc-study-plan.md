# ANUMA MeluXina HPC Study Plan

## Scope and non-negotiable limits

This plan covers the already-built 240 m clip-and-weight Monte Carlo precursor
and stages A–D on MeluXina project `p201619`. It authorizes no submission by
itself and records no study result.

- Ceiling across the precursor and stages A–D: **220 CPU node-hours**.
- Run **one CPU node at a time**: 128 cores and 512 GB RAM.
- MeluXina bills the whole node-hour, even when fewer cores are requested.
- No multi-node jobs, GPU, FPGA, or large-memory jobs.
- Capture `myquota` immediately before and after every submitted job.
- Use project storage for immutable inputs/results and
  `/project/scratch/p201619` only for job-local, disposable work.
- Preserve input, environment, Git, config, output, and accounting manifests.
- Do not invent mission dates, study results, or Artemis candidate centres.

The maximum committed spend is 88.5 node-hours. At least **131.5 node-hours**
of the 220-hour ceiling remain held:

| Work | QOS | One-node walltime cap | Maximum node-hours |
| --- | --- | ---: | ---: |
| Precursor pilot | `test` | 00:30 | 0.5 |
| Precursor full | `default` | 04:00 | 4 |
| A — 240 m Sun-model acceptance | `default` | 04:00 | 4 |
| B — full-cap 20 m slope/ranking comparison | `default` | 40:00 | 40 |
| C — 20 m Sun model for ≤20 sites | `default` | 16:00 | 16 |
| D — 20 m Monte Carlo and targeted DEM noise | `default` | 24:00 | 24 |
| **Maximum committed** |  |  | **88.5** |
| **Held remainder** |  |  | **131.5** |

Caps are maxima, not targets. A failed gate or early completion stops spend;
unused hours return to the held remainder.

## Global execution and stop gates

Before any stage:

1. Work from a clean, reviewed Git commit with a checksum-pinned environment.
2. Confirm account `p201619`, CPU partition, selected QOS, current allocation,
   storage quota, project paths, and scratch paths through fail-closed preflight.
3. Validate input manifests and per-file SHA-256. Inputs must be real, not
   synthetic.
4. Re-ingest must have produced
   `illumination_affine_revision: pds3-pixel-center-v2`. Older or missing
   provenance stops the precursor and every later stage.
5. Keep downloads, ingest, checksumming, and computation off login nodes.
6. Repeat fixed resource caps in the submission command and job; reject
   mismatched account, QOS, node count, core count, memory, walltime, receipt,
   manifest, or Git commit.
7. Create a unique output directory; never overwrite prior output.
8. Record `module list`, package versions, input/output checksums, stdout,
   stderr, elapsed time, CPU use, peak memory, and `myquota` before/after.
9. Delete job-local scratch after outputs and manifests are safely preserved.
10. Run only one ANUMA CPU job at a time.

Stages are sequential. A gate failure stops downstream work; it is not a
reason to consume held hours on an alternative run.

## Precursor — 240 m clip-and-weight Monte Carlo

The precursor is already implemented and remains the first study. It must not
be replaced by stages A–D.

### Resources

- Pilot: one node, `test`, walltime ≤00:30, ≤0.5 node-hours.
- Full: one node, `default`, walltime ≤04:00, ≤4 node-hours.
- Use only the cores and memory requested by the implemented guarded path.

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
- Review the pilot before the full draw.
- No 20 m job starts until the full precursor output and accounting are
  reviewed.

## Stage A — 240 m Sun-model acceptance

Stage A is a blocking method-validation stage, capped at four node-hours.

### Method

Compute the terrain horizon once from the 240 m DEM. Reuse that horizon when
comparing Sun elevation against terrain. A fresh whole-cap ray march for every
date is explicitly out of budget.

The first test is reproduction of the published `AVGVISIB` 2016 average-
visibility map under its documented temporal definition. The published map
remains ANUMA's measured illumination layer; the homemade model is a scenario
tool, not a replacement measurement.

### Resources

- One CPU node, `default`, walltime ≤04:00, ≤4 node-hours.
- No downstream job may overlap it.

### Inputs

- Corrected 240 m DEM and affine-fix manifest.
- Published `AVGVISIB` 2016 map and metadata.
- Frozen projection, horizon algorithm, solar geometry inputs, environment,
  and a scientific-owner-approved acceptance protocol/tolerance declared
  before submission.

### Outputs

- Reusable 240 m terrain-horizon artifact and checksum.
- Model-versus-published-map agreement diagnostics and acceptance record.
- Provenance, resource, and accounting manifests.

### Blocking gate

If the predeclared reproduction test fails, stop. Stages B–D do not start.
Do not tune acceptance criteria after viewing the result.

## Stage B — full-cap 20 m slope and ranking comparison

Stage B asks only whether the 240 m ranking survives the full-cap 20 m slope
and score calculation.

### Resources

- One CPU node, `default`, walltime ≤40:00, ≤40 node-hours.
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

- One CPU node, `default`, walltime ≤16:00, ≤16 node-hours.
- At most 20 sites.

### Site set

Use the Stage B shortlist, then add any site that was in the 240 m top 10 but
was dropped by the 20 m list. If the union exceeds 20, stop for an explicit
scientific-owner selection rule before submission; do not silently truncate or
invent Artemis centres.

### Required inputs

- Passed Stage A and reviewed Stage B outputs.
- Frozen union-of-lists site manifest and 20 m terrain data.
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

Stage D uses all 128 cores of one node. It does not scale across nodes.

### Resources

- One CPU node, all 128 CPU cores, `default`, walltime ≤24:00,
  ≤24 node-hours.
- Multiprocessing is bounded to those 128 local cores.

### Inputs and method

- Reviewed Stage B arrays and rank table.
- Frozen weight, clipping-policy, seed, draw-count, and DEM-noise model
  configuration.
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
- Site-local DEM-noise slope/rank sensitivity for unstable sites only.
- Per-draw, environment, resource, and checksum manifests.

### Stop gates

- Stop if Stage B arrays cannot be reused without recomputation.
- Stop before noise reruns if the instability rule or DEM-noise model is not
  frozen and justified.
- Reject more than one node, any worker count other than exactly 128 cores,
  walltime over 24 hours, or any full-cap slope rerun per Monte Carlo draw.

## Review and release discipline

Each stage produces evidence, not a mission claim. Review scientific outputs
and accounting before authorizing the next stage. Keep the published
illumination map as measured evidence, label modeled illumination clearly, and
retain the 131.5-node-hour minimum remainder unless a new reviewed plan
explicitly reallocates it.
