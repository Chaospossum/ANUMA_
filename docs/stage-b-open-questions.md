# Stage B open questions

These items need an explicit scientific-owner decision. The implementation
does not invent answers for them. Stage B code freezes only what
`config/layers.yaml`, `config/demo_weights.yaml`, and the Stage A/B execution
patterns already fix.

## Still open

1. **Agreement-map semantics beyond hard-mask equality.**  
   The executable writes a 240 m map where each coarse pixel centre samples the
   20 m hard mask by nearest neighbour and records equality with the 240 m
   hard mask (`1` agree, `0` disagree, `NaN` invalid). The study plan names an
   “agreement map” but does not say whether agreement should instead use score
   values, a landing-ellipse neighborhood, or an any/all/majority rule over the
   fine pixels that fall inside each coarse cell. Confirm or replace the frozen
   hard-mask centre rule before treating the map as a scientific product.

2. **Soft-layer sampling method.**  
   Illumination, hydrogen, and Earth visibility stay at 240 m and are sampled
   onto 20 m by nearest 240 m pixel centre so the pipeline does not invent
   fine-scale soft-layer detail. Confirm that nearest-neighbour (rather than
   bilinear or area-weighted) is the intended comparison rule.

3. **20 m versus 240 m grid nesting.**  
   The LDEM 20 m product is 30400² over ±304000 m. The ANUMA 240 m grid is
   2534² over ±304080 m. They are not nested by an integer factor. Edge coarse
   pixels and half-pixel offsets are a geometric fact of the two products.
   Confirm that comparing them by georeferenced centres (not a forced 12×12
   block reshape) is acceptable.

4. **Stage D reuse of Stage B arrays.**  
   Stage B’s declared scientific outputs are only the agreement map and rank
   table. Stage D also requires reuse of Stage B arrays without recomputing
   full-cap slope. The job therefore keeps `slope-20m.npy` as an operational
   artifact. Confirm which normalized/source arrays Stage D must retain beyond
   that slope file.

5. **Stage A gate status.**  
   Production refuses to run unless
   `out/sun-validation/validation-report.json` (or `ANUMA_STAGE_A_ACCEPTANCE`)
   shows `status=passed`, `metrics.passed=true`, and RMSE ≤ 0.05 with
   `stop_rmse` still 0.05. MeluXina job `5333886` is the Stage A run in flight
   as of the Stage B prep; its result is unknown here. Do not assume it passed.

## Not open (frozen)

- Hard slope threshold: 8°.
- Default normalization policy: `decided_per_layer`.
- Demo weights: illumination 0.5, hydrogen 0.3, earth visibility 0.2.
- Stage A `stop_rmse`: 0.05; `tune_threshold_after_run`: false.
- One CPU node, 128 workers, `--mem=240G`, no accelerators.
- Stage B ceiling: 40 node-hours; production walltime sized at 06:00 from the
  laptop tile benchmark documented in `docs/meluxina-execution.md`.
