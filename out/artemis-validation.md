# Artemis validation

NASA does not publish Artemis III region centres on nasa.gov. This note records the search, the approximate points that are cited, and the one ranked site whose coordinates job 5332991 actually gives. Ranks 2–10 are not filled in.

## List distinction

The 19 August 2022 NASA list has 13 candidate regions and includes Connecting Ridge, the Shackleton connecting ridge. The 28 October 2024 list has nine regions and does not include that ridge. de Gerlache Rim 2 is in the 2024 list and lies next to that ridge. NASA does not publish region centres on nasa.gov. NASA SVS 5013 shows the 13 regions and does not publish numeric coordinates. Approximate points for the 13 names are the Mean Earth sites in Goossens et al. (2025) Table 1, not NASA polygons. Peak near Cabeus B, Mons Mouton Plateau, Mons Mouton, and Slater Plain have no citable centre in a peer-reviewed paper or an SVS coordinate file, so their geometries stay empty.

## Search

Used:

- Goossens et al. (2025), *Planet. Sci. J.* 6, 191, Table 1, https://doi.org/10.3847/PSJ/adf05f. Peer-reviewed Mean Earth coordinates for 20 south-polar sites, including the original 13 Artemis names. The table's radius column is the lunar radial distance in kilometres, not a region radius. These rows are point sites the authors investigated. They are stored here as approximate points.

Checked and not used as coordinates:

- NASA, 19 August 2022, https://www.nasa.gov/news-release/nasa-identifies-candidate-regions-for-landing-next-americans-on-moon/. Thirteen names. The release describes regions of about 15 by 15 km. No centres. The box size is not drawn.
- NASA, 28 October 2024, https://www.nasa.gov/news-release/nasa-provides-update-on-artemis-iii-moon-landing-regions/. Nine names. No centres and no repeated box size.
- NASA SVS 5013, https://svs.gsfc.nasa.gov/5013/. The page and its data API list a movie, captions, and stills for the 13 regions. No latitude table, KMZ, GeoJSON, or shapefile.
- LROC NAC mosaic catalogue centres, LPSC and LEAG abstracts, and the USGS navigational grids. Those are not a peer-reviewed paper and not an SVS coordinate file, so they are not used.
- Published illumination points and proposed landing points inside regions (including sites 001 and 004). Those are not region centres.

## Cited points

Longitude is positive east, as printed in Table 1. Distance later uses the ANUMA stereographic plane (`+proj=stere +lat_0=-90 +lon_0=0 +R=1737400 +units=m +no_defs`).

| Region | Lists | Paper name | Lon °E | Lat | Geometry |
| --- | --- | --- | --- | --- | --- |
| Faustini Rim A | 2022 | Faustini Rim A | 90.000 | -87.890 | approximate point |
| Peak Near Shackleton | 2022 | Pt Nr Shackleton | 123.691 | -88.811 | approximate point |
| Connecting Ridge | 2022 | Connecting Ridge | 222.51 | -89.463 | approximate point |
| Connecting Ridge Extension | 2022 | Connecting Ridge Ext | 258.001 | -89.032 | approximate point |
| de Gerlache Rim 1 | 2022 | de Gerlache Rim 1 | 292.068 | -88.684 | approximate point |
| de Gerlache Rim 2 | 2022,2024 | de Gerlache Rim 2 | 295.372 | -88.231 | approximate point |
| de Gerlache-Kocher Massif | 2022 | de Gerlache-Kocher Massif | 243.678 | -85.831 | approximate point |
| Haworth | 2022,2024 | Haworth | 337.223 | -86.765 | approximate point |
| Malapert Massif | 2022,2024 | Malapert | 359.764 | -85.996 | approximate point |
| Leibnitz Beta Plateau | 2022 | Leibnitz Beta Plateau | 31.743 | -85.428 | approximate point |
| Nobile Rim 1 | 2022,2024 | Nobile Rim 1 | 37.367 | -85.440 | approximate point |
| Nobile Rim 2 | 2022,2024 | Nobile Rim 2 | 58.822 | -83.956 | approximate point |
| Amundsen Rim | 2022 | Amundsen Rim | 69.444 | -84.230 | approximate point |
| Peak near Cabeus B | 2024 |  |  |  | null |
| Mons Mouton Plateau | 2024 |  |  |  | null |
| Mons Mouton | 2024 |  |  |  | null |
| Slater Plain | 2024 |  |  |  | null |

Malapert Massif uses the Table 1 name Malapert. Peak Near Shackleton uses Pt Nr Shackleton. Connecting Ridge Extension uses Connecting Ridge Ext. A name that appears on both NASA lists carries the 2022 point; the 2024 boundary is still unpublished.

## Top 10

Job 5332991 reports one winner coordinate. It does not report the other nine. Those coordinates are left blank.

| Rank | Latitude | Longitude | Nearest cited point | Distance km | Record |
| --- | --- | --- | --- | --- | --- |
| 1 | 89.45°S | 137.3°W | Connecting Ridge | 0.40 | job 5332991; distance computed by `artemis_overlap` |
| 2–10 |  |  |  |  | coordinates not reported with job 5332991 |

The winner is labelled Shackleton connecting ridge in that job, with illumination 0.93, in 99.8% of 500 weight draws. Six unnamed sites are top-10 in 100% of draws. Their coordinates are not in this record and are not invented. The old 2/98 clip selects 83.66°S in 100% of draws. Longitude is not in this record and is not invented. Clip versus unclip changes the winner class in 500/500 draws.

137.3°W is longitude −137.3° on the positive-east grid. The distance is in the stereographic plane, not a geodesic, and is rounded to 0.01 km for this table. Nearest cited point: Connecting Ridge. Distance: 0.40 km.

Region best score and rank need a scored raster. This checkout does not contain the ingested grids, so those scores are not computed for this table. `tests/test_score.py` checks best score and rank on constructed polygons, and checks this winner's nearest point.

No radius is assigned to any region.
