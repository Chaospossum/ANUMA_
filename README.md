# ANUMA

Landing-site decision support for the lunar south pole. Demo case: ispace-EUROPE MAGPIE (ESA, 2029), Neospace Challenge 2026.

Phases 1–5 are in this tree: ingest, scoring, FastAPI, the map UI at
`http://localhost:8000`, and static figure export.

## Install

Run all commands from the repository root. Python 3.11 or newer is required;
GDAL is provided by the `rasterio` wheel.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Ingest the public source data

```bash
python3 -m anuma.ingest
```

This downloads the products listed in `config/layers.yaml`, warps them to the
common 240 m grid, and writes the arrays to `data/grid/`. The download includes
the 2.51 GiB LOLA DEM. A completed ingest produces `dem.npy`, `slope.npy`,
`illumination.npy`, `hydrogen.npy`, `earth_visibility.npy`, and `grid.json`.
`--synthetic` creates test fixtures only and must not be used for the demo.

## Serve the UI

After ingest completes, start the API and static UI:

```bash
python3 -m uvicorn anuma.api:app --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000`. The page scores automatically with the configured
defaults and displays the combined map and ranked sites. If the real grids are
missing, the page deliberately remains empty and shows the ingest command.

## Render static figures

After ingest, render the configured demonstration weights:

```bash
python3 -m anuma.render_static --weights config/demo_weights.yaml
```

The command reads `data/grid/*.npy` and writes `out/static/score.png` plus
`illumination.png`, `hydrogen.png`, and `earth_visibility.png`, each showing
the normalized criterion used by scoring. It prints every written path. Use
`--grid-dir PATH` and `--output-dir PATH` to override those directories.

## Ten-line UI demo

1. With the server running and real grids ingested, open `http://localhost:8000`.
2. Wait for the automatic default score to finish and the map veil to disappear.
3. Show the current ranked sites by reading the table on the right (up to ten rows).
4. Select the first row to center and zoom the map on the current top-ranked site.
5. Note its score and the Illumination, Hydrogen, and Earth vis. contribution tick bars.
6. Change only **Max slope (deg)** from `8` to the displayed HLS goal of `5`.
7. Select **Score** to apply the stricter slope requirement and calculate the ranking again.
8. Read the refreshed table and select its first row to inspect the newly ranked top site.
9. Read which criterion has the largest contribution tick bar at that site, then choose that criterion under **Show single layer** to inspect its map; the UI shows weighted contributions, not causal attribution.
10. State explicitly: “This result is decision-support screening, not landing clearance.”

Unit tests use constructed arrays and `--synthetic` ingest only:

```bash
python3 -m pytest tests/test_ingest.py tests/test_score.py tests/test_api.py tests/test_render_static.py
python3 -m anuma.ingest --synthetic --data-dir /tmp/anuma-test
```

`POST /score` and `GET /layer/{name}.png` return HTTP 412 with the ingest command until real grids exist in `data/grid/`. The API does not score `--synthetic` layers for the demo.

## Ingest download sizes

| Layer | Product | Size |
| --- | --- | --- |
| DEM | `LDEM_80S_20MPP_ADJ.TIF` | 2.51 GiB |
| Illumination | `AVGVISIB_65S_240M_201608.jp2` | 46.4 MiB |
| Hydrogen | `hydrogenhd.dat` (Lunar Prospector, LEND map not on PDS) | 506 KiB |

## Grid

Moon south polar stereographic, `+proj=stere +lat_0=-90 +lon_0=0 +R=1737400 +units=m +no_defs`, 240 m/pixel, square covering 80°S–90°S. Arrays in `data/grid/*.npy`, transform in `data/grid/grid.json`.

Artemis III region names are in `data/artemis3_regions.geojson`. NASA has not published centre coordinates; geometries are null and the file is marked approximate.

Layer URLs, citations, and thresholds live in `config/layers.yaml`. See `SOURCES.md` for the jury list.
