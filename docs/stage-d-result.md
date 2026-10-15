# Stage D result — LOLA height error versus the 1 km slope check

Not affiliated with NASA, ESA or ispace. Screening sensitivity only; not a
landing recommendation. The 1 km circle is a proxy, not a mission ellipse.

| Item | Value | Source |
| --- | --- | --- |
| Job | MeluXina `5345050`, COMPLETED, 00:02:31, about 0.04 node-h | `sacct` |
| Commit | `5a84444` | preflight receipt |
| Plan | frozen 2026-10-15 before any noise draw | [stage-d-plan.md](stage-d-plan.md) |
| Pilot | job `5345021`, 2 sites × 8 draws in 50 s; all pass conditions met | `pilot-measurement.json` |
| Scope | 8 sites × 2 noise models × 200 draws = 3,200 perturbed 20 m windows, seed 20261016 | `stage-d-summary.json` |
| Noise source | NASA PGDA product 90 height error (Barker et al. 2023, PSJ 4, 183); window medians 0.18–0.21 m | job `5345050` |
| Built-in checks | all passed (below) | `stage-d-summary.json` |
| Cost | CPU 7 of 300 node-h before and after (integer resolution) | `myquota` before/after |

Outputs are in `/project/home/p201619/ANUMA_/out/hpc/stage-d-production-5345050/`.
The summary, site files and manifest are copied to the laptop under `out/hpc/`
and are not committed. `data/checks/stage-d-noise.json` holds the per-site
numbers the app shows.

## Result

Share of 20 m pixels at or below 8° within 1 km of each 240 m cell centre.
The decision uses the primary noise model; the rule is ≥ 0.50.

| Default rank | Site | No noise | 5th–95th percentile | Passes in | Verdict |
| --- | --- | --- | --- | --- | --- |
| 1 | 89.45°S 137.31°W | 0.4915 | 0.4878–0.4920 | 0 of 200 | fails, stable |
| 2 | 89.78°S 155.73°W | 0.1045 | 0.1033–0.1057 | 0 of 200 | fails, stable |
| 3 | 88.81°S 123.64°E | 0.6585 | 0.6536–0.6584 | 200 of 200 | passes, stable |
| 8 | 85.29°S 36.94°E | 0.4987 | 0.4968–0.5012 | 38 of 200 | **noise-unstable** (straddles 0.50) |
| 9 | 85.48°S 31.39°E | 0.4709 | 0.4696–0.4732 | 0 of 200 | fails, stable |
| 10 | 89.80°S 54.73°E | 0.2084 | 0.2066–0.2098 | 0 of 200 | fails, stable |
| 11 | 89.01°S 94.82°W | 0.3910 | 0.3880–0.3929 | 0 of 200 | fails, stable |
| 12 | 88.70°S 68.36°W | 0.3192 | 0.3183–0.3224 | 0 of 200 | fails, stable |

In plain words: the published LOLA height error does not change ANUMA's 1 km
slope screening, except at one site. Each site's share moves by less than
0.006 between the 5th and 95th percentiles, about twenty times below the 0.10
instability threshold, and every site's 240 m cell keeps at least 82 of its
144 fine pixels at or below 8° in all 3,200 draws, so no site drops out of
the 20 m ranking.

- **Rank 1** falls just short of 50 % in every draw (95th percentile 0.492).
  Its sub-50 % figure from Stage B is robust to height noise, not an artefact
  of it.
- **Rank 3** passes in every draw.
- **Rank 8** is the only noise-unstable site, and only because it sits on the
  line: 0.499 without noise (Stage B's "0.50" was rounded), passing in 19 % of
  draws (26 % in the sensitivity model). Its terrain figure is stable; its
  pass/fail label is undecidable under the published error.
- The plan expected ranks 1, 8 and 9 might straddle 0.50; only rank 8 does.

The sensitivity model (σ = EFFRES instead of EFFRES/2.355) gives the same
verdict at every site; all its widths are ≤ 0.0062.

## Checks

- Stability rule recomputed from the weight Monte Carlo (job `5332991`):
  unstable sites 779:1564, 1252:1287, 1277:1142, 1206:1114, as frozen.
- Zero-noise slopes reproduce Stage B (job `5334425`) exactly (max difference
  0.0°); 1 km fractions match the Stage B table to two decimals.
- Serial and 128-worker runs are bit-identical; the pilot's 16 draws are
  byte-identical to the same draws in production, on a different node.
- Injected noise has the published standard deviation to 0.1 % (pooled
  0.9994–1.0004 × ERR).
- Induced slope noise is 0.63–0.88 of the published slope-error median
  (`LDSM_80S_20MPP_ADJ_ERR`) in the primary model, inside the frozen [0.5, 2].
- Output hashes match `output-manifest.json`; input hashes match the receipt
  and the pinned values.

A second reviewer recomputed every decision, percentile and rate from
`draws.jsonl` with zero mismatches.

## What it does not show

- It tests **random** height error only, as ANUMA reads the product: 1σ,
  Gaussian, one length scale per site, EFFRES as a full width. It does not
  cover systematic error, track misregistration or errors in the error map.
- The primary noise is nearly white at 20 m: window-median EFFRES is
  14.8–19.9 m, so σ is 6.3–8.5 m.
- It injects less slope uncertainty than the published slope-error map
  (ratio 0.63–0.88), so it is modestly non-conservative.
- Only the 40 m Horn slope baseline, the 8° limit and the 1 km circle are
  tested, for these 8 sites; no scores are recomputed.

### Correction to the plan's wording

The plan says reading EFFRES as σ "gives smoother, gentler noise". For slope
the opposite holds here: the smoother sensitivity noise gives 1.11–1.24×
larger Horn slope noise (larger at all 8 sites), because the Horn stencil sums
correlated neighbours. So the sensitivity run is the more conservative one.
It gives the same verdicts. The frozen plan is left unchanged; this note
records the correction.

### Run note

Preflight ran with `.venv-hpc-ingest` (env/release/2024.1,
Python/3.12.3-GCCcore-13.3.0) on cpudev jobs `5345018` (pilot) and `5345047`
(production), because `.venv-hpc-sun` has no scipy. See
[stage-d-run.md](stage-d-run.md).
