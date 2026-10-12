# ANUMA results

The mission case is still open.

ANUMA is an independent student project. It is not affiliated with or endorsed by ispace, ESA, or the MAGPIE mission team.

The challenge paper names LROC. The layers in this tree are LOLA on LRO — the DEM, the slope derived from it, and the 2016 average-illumination map — and Lunar Prospector hydrogen. Earth visibility is derived from the LOLA DEM. A sun layer is planned until a real `out/sun-validation` product exists. It is not scored here.

## Measured

### Weight stability

Slurm job 5332991:

- 500 weight draws.
- Winner 89.45°S, 137.3°W, Shackleton connecting ridge, illumination 0.93, in 99.8% of draws.
- Six unnamed sites are top-10 in 100% of draws. Their coordinates are not in this record.
- The old 2/98 clip selects 83.66°S in 100% of draws. Its longitude is not in this record.
- Clip versus unclip changes the winner class in 500/500 draws.

### Distance from that winner

`artemis_overlap` places 89.45°S, 137.3°W (longitude −137.3°) 0.40 km from the Connecting Ridge point in Goossens et al. (2025) Table 1, in the south-polar stereographic plane (`R=1737400` m). The figure is rounded to 0.01 km in `out/artemis-validation.md`. `test_cited_artemis_points_and_measured_winner` checks the nearest name. `test_artemis_validation_note` checks the written 0.40 km. This is not a separate survey and not a NASA region radius.

Ranks 2–10 of that job are blank here. Their coordinates were not reported.

## Cited points, not measurements

NASA does not publish region centres on nasa.gov. NASA SVS 5013 shows the 13 regions and does not publish numeric coordinates.

The 19 August 2022 NASA list has 13 candidate regions and includes Connecting Ridge, the Shackleton connecting ridge. The 28 October 2024 list has nine regions and does not include that ridge. de Gerlache Rim 2 is in the 2024 list and lies next to that ridge.

Approximate points for the 13 names are the Mean Earth sites in Goossens et al. (2025), *Planet. Sci. J.* 6, 191, Table 1, https://doi.org/10.3847/PSJ/adf05f. They are not NASA polygons. The table's radius column is lunar radial distance, not a keep-out or a region size, and it is not used. Peak near Cabeus B, Mons Mouton Plateau, Mons Mouton, and Slater Plain have no citable centre, so their geometries stay empty. The coordinate table is in `out/artemis-validation.md`.

## Keep-out

`POST /score` accepts an optional `keepout` object: `points` as `{lon, lat}` pairs, `radius_km`, and `mode` of `hard` or `penalty`. `radius_km` is required whenever keep-out is sent. There is no default plume radius. Penalty mode also requires `penalty`. There is no default penalty. Hard mode removes the disk from the feasible set. Penalty mode subtracts the supplied value from the weighted score inside the disk. Distance is in the same stereographic plane as the grid.

`test_keepout_requires_radius_and_can_mask_or_penalise` and `test_keepout_radius_is_required` lock that contract. `test_keepout_hard_mask_removes_a_site` checks a constructed grid.

Orbital debris affects trajectories, not site choice, so it is not modelled.

## Planned

- The mission case, including ellipse, weights, and the comparison the demo will show.
- A ranked shortlist from ingested LOLA and Lunar Prospector grids. Those arrays are not in this checkout, so no site score from them is listed here.
- Best score and rank inside each Artemis region on that raster.
- Coordinates for the six unnamed top-10 sites and the longitude of the 83.66°S clip selection.
- Geometry for Peak near Cabeus B, Mons Mouton Plateau, Mons Mouton, and Slater Plain.
- A sun layer, until `out/sun-validation` exists.
- Any plume radius other than one the user supplies.
