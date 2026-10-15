# Stage D run — implementation notes and commands

Not affiliated with NASA, ESA or ispace. Screening only; not a landing
recommendation. This page records how the frozen
[stage-d-plan.md](stage-d-plan.md) is implemented and how to launch it. It
changes no frozen value and records no result. Status: **implemented, not
run**.

## Code

| File | Role |
| --- | --- |
| `anuma/stage_d.py` | Rule recomputation, noise model, per-draw statistics, decision, checks, pilot and production driver (`python -m anuma.stage_d`) |
| `anuma/stage_d_gate.py` | Pinned digests and receipt checks; standard library only, never hashes a raster |
| `hpc/preflight.py` | Profiles `stage-d-pilot` and `stage-d`; hashes every Stage D input (run it on `cpudev`) |
| `hpc/submit-stage-d.sh` | The only sbatch path; checks the receipt on the login node without reading rasters |
| `hpc/slurm/stage-d-pilot.sbatch`, `hpc/slurm/stage-d-production.sbatch` | Re-check caps and receipt, then `srun` the module |
| `tests/test_stage_d.py` | Synthetic arrays and GeoTIFFs only |

`require_frozen_config` compares every value in `hpc_studies.dem_noise`
(and the default weights, policy, 8° mask, `n_sites` and separation) with
the plan and refuses any drift.

## Implementation notes

These are points the plan leaves open, resolved here before any noise
draw, so a reviewer can check them:

1. **Window placement.** The 240 m centre lies on a 20 m pixel corner, so a
   501 × 501 window cannot be symmetric. The window starts 250 pixels before
   the pixel whose upper-left corner is the centre (−5000 m to +5020 m). The
   1 km circle (7,860 pixel centres within 1000 m) and the 144-pixel cell are
   measured from the exact centre, so this choice does not move them. This
   geometry reproduces every Stage B 1 km value to two decimals locally.
2. **Horn halo.** Noise is drawn on the window plus the one-pixel Horn halo
   plus the pad, and the slope is taken with `slope_tile_from_array(...,
   halo=1)`, so the zero-noise window equals the Stage B `slope-20m.npy`
   slice exactly, edges included.
3. **Kernel truncation.** `K = outer(k, k)` with a 1-D Gaussian `k` of radius
   ⌈4σ / 20 m⌉ pixels, normalized so `sum K = 1`; the truncation is a square,
   as in `scipy.ndimage.gaussian_filter`. The pad equals that radius, so
   every kept pixel has full kernel support. `sqrt(sum K²) = sum k²`.
4. **EFFRES and LDSM_ERR statistics.** Window medians use finite, positive
   values on the bare 501 × 501 window; nodata is NaN. The valid pixel
   counts are reported.
5. **EFFRES fallback.** If EFFRES is not staged (absent from the disk *and*
   from `pgda90-sha256.txt`), 40 m stands in for the window median: primary
   σ = 40 / 2.355 ≈ 17.0 m as the plan says, and the sensitivity run uses
   σ = 40 m (the same value read as σ), labelled as fallback. A window with
   no valid EFFRES uses the same fallback for that site only. Reports name
   the source per site.
6. **Fractions.** The ≤ 8° fraction divides by the finite-slope pixels in
   the circle. Pass is fraction ≥ 0.50. In-cell counts treat NaN slope as
   failing. The largest patch is the largest 8-connected set of ≤ 8° pixels
   inside the circle, times 400 m².
7. **Two-decimal match (check 3).** |zero-noise fraction − Stage B value| ≤
   0.005, against the committed `data/checks/stage-b-1km.json`.
8. **Pooled standard deviations (checks 4, 5).** Sample standard deviation
   (ddof = 1) over all window pixels with finite, positive ERR (check 4) or
   finite slopes (check 5), across all draws of one site and run. Check 4
   uses noise / ERR = u. Check 5 is per site; the primary run fails if any
   site's ratio is outside [0.5, 2].
9. **Pilot sites.** The plan's pilot is 2 sites × 8 draws; they are the first
   two sites in scope order (1318:1219, 1292:1255) with draws 0–7, the same
   streams production uses.
10. **Production serial reference.** Production runs every draw twice, serial
    and with 128 workers, so check 2 covers all 8 sites and both runs. The
    stored records are the parallel ones; the two must be bit-identical.
11. **Check 4 in production** is computed and reported, not gating (the
    plan lists checks 1–3 and 5 for production).
12. **Default ranking source.** The job recomputes the default ranking from
    the 240 m arrays in the checksummed input manifest, the arrays job
    `5332991` used. `--mode rule` does the same from the laptop's
    `data/grid`; both reproduce `frozen_unstable_ids`.
13. **Walltime.** Production is capped at 01:30 (1.5 node-hours), below the
    plan's 02:00, so pilot plus production stays within 2.0 node-hours. The
    pilot passes only if its projection fits both 2.0 node-hours (check 6)
    and the 01:30 walltime.
14. **Login node.** The submit wrapper never reads or hashes a raster.
    Preflight (on `cpudev`) hashes every input and pins its digest, size and
    mtime; the wrapper checks size, mtime and the digest of
    `pgda90-sha256.txt`; the job hashes every input again before reading.
    Pinned digests: DEM `09b7ca80…` and `slope-20m.npy` `08b2e17b…` (Stage B
    job `5334425` manifest), ERR `d64045b8…`, study summary `4da2a9c3…`.
    EFFRES and LDSM_ERR digests are read from `pgda90-sha256.txt`.
15. **Fail-closed geometry and error map.** A site is refused if ERR is
    missing (NaN or nodata) at any pixel of the window plus halo where the
    DEM is finite (the count is recorded as `err_nan_under_finite_dem`), and
    a draw is refused if its number of finite slope pixels in the circle
    differs from the zero-noise pass. The job also refuses a 240 m grid that
    is not exactly 12 fine pixels per cell, a 240 m centre that is not on a
    fine-pixel corner to 1e-6 pixel, and a DEM CRS that places test points
    more than 1 mm from the grid's proj4.
16. **Environment.** The job needs rasterio and scipy, so it runs on
    `.venv-hpc-ingest` with `env/release/2024.1` and
    `Python/3.12.3-GCCcore-13.3.0`, as Stage B. Login-node checks use
    `.venv-hpc-sun`.

## Laptop check of the rule

```bash
python -m anuma.stage_d --mode rule \
  --grid-dir data/grid \
  --mc-summary out/hpc/monte-carlo-full-5332991/study-summary.json
```

It refuses a summary whose sha256 is not `4da2a9c3…` and exits 2 unless the
recomputed lists equal the frozen ones.

## Commands (MeluXina, not run)

Wait until `pgda90-download.log` shows all three PGDA files done and
`pgda90-sha256.txt` lists them. Capture `myquota`. Then, on a `cpudev`
allocation, with evidence files, `ANUMA_DATA_ROOT=/project/home/p201619/ANUMA_`,
`ANUMA_INPUT_MANIFEST=/project/home/p201619/ANUMA_/data/input-manifest.json`,
`ANUMA_OUTPUT_ROOT=/project/home/p201619/ANUMA_/out/hpc` and
`ANUMA_SCRATCH_DIR` set as in [meluxina-execution.md](meluxina-execution.md).
Defaults for the other inputs: `ANUMA_STAGE_D_RAW_DIR=$ANUMA_DATA_ROOT/data/raw`,
`ANUMA_DEM=$ANUMA_STAGE_D_RAW_DIR/LDEM_80S_20MPP_ADJ.TIF`,
`ANUMA_STAGE_B_SLOPE=$ANUMA_DATA_ROOT/out/hpc/stage-b-production-5334425/slope-20m.npy`,
`ANUMA_MC_SUMMARY=$ANUMA_DATA_ROOT/out/hpc/monte-carlo-full-5332991/study-summary.json`.

Stage D preflight refuses to run without `SLURM_JOB_ID`, because it hashes
the rasters, and records the job id and host in the receipt. `myquota` exists only on
login nodes, so Stage D preflight does not require it inside an allocation:
refresh the allocation and storage evidence files from `myquota` on the login
node first (they must be under 24 hours old). The `cpudev`
allocation (QOS `dev`) is node time outside the 2.0 node-hour Stage D
accounting above; keep it short and record it with the job's `myquota`
captures.

Pilot:

```bash
salloc -A p201619 -p cpu --reservation=cpudev -q dev -N 1 -t 01:00:00
export ANUMA_JOB_PROFILE=stage-d-pilot
export ANUMA_QOS=test
export ANUMA_ACCOUNT=p201619
.venv-hpc-sun/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-stage-d-pilot-$(date -u +%Y%m%dT%H%M%SZ).json"
exit   # leave the cpudev allocation
export ANUMA_PREFLIGHT_RECEIPT="$HOME/<the-new-pilot-receipt>.json"
bash hpc/submit-stage-d.sh pilot
```

Production (only after the pilot passes and is reviewed):

```bash
salloc -A p201619 -p cpu --reservation=cpudev -q dev -N 1 -t 01:00:00
export ANUMA_JOB_PROFILE=stage-d
export ANUMA_QOS=default
.venv-hpc-sun/bin/python hpc/preflight.py \
  --receipt "$HOME/anuma-stage-d-$(date -u +%Y%m%dT%H%M%SZ).json"
exit
export ANUMA_PREFLIGHT_RECEIPT="$HOME/<the-new-production-receipt>.json"
bash hpc/submit-stage-d.sh production
```

The job writes `out/hpc/stage-d-<mode>-<job>/`: `draws.jsonl` (one record per
run, site and draw), `sites/site-<row>-<col>.json`, `pilot-measurement.json`
or `stage-d-summary.json`, `output-manifest.json` (report plus sha256 and
bytes of every output), `progress.log`, `module-list.txt` and `myquota`
before and after. Exit codes: 2 a refused input or pin, 3 a failed
acceptance check, 4 a pilot projection that does not fit.

## Pilot pass conditions

`checks.rule_reproduced`, `checks.serial_parallel_identical`,
`checks.zero_noise_reproduced`, `checks.noise_std_within_tolerance`,
`checks.slope_noise_primary_within_bounds` and `checks.projection_fits` must
all be true (`passed`). `window_preview` lists EFFRES and σ for all 8 sites.

## Cost

Laptop smoke on 15 October 2026 (real 20 m DEM, real 240 m arrays and
summary; synthetic constant ERR, EFFRES and LDSM_ERR, so no result): the
pilot path took 11 s and projected 0.05 node-hours; the full production path
(3,200 draws, serial then 8 workers) took 2 min 28 s, below that
projection. On MeluXina, add hashing of about 18 GB of inputs. Production
should need well under 0.25 node-hours of its 1.5 node-hour walltime.
