# ANUMA

Landing-site decision support for the lunar south pole. The mission case is still open.

Phases 1–5 are in this tree: ingest, scoring, FastAPI, the map UI at
`http://localhost:8000`, and static figure export.

Project documentation:

- [Team and delivery facts](docs/TEAM.md)
- [HPC use plan](docs/hpc-use.md)
- [Guarded MeluXina 240 m execution](docs/meluxina-execution.md)
- [Winning strategy](docs/winning-strategy.md)
- [Short ispace meeting guide](docs/ispace-meeting.md)

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

Raw source rasters and derived grids are intentionally excluded from Git. A
clone alone cannot be scored. Check an HPC staging area with:

```bash
python3 -m anuma.hpc_data_check --root "$PWD" --require-ready \
  --manifest-output data/data-presence-manifest.json
```

Ingest either on an approved workstation followed by checksummed transfer, or
in a separately reviewed one-node MeluXina compute job. Ingest, downloads,
hashing, and environment installation are not login-node work. Do not stage a
reference grid until the illumination-registration correction documented by
the HPC audit is merged and verified.

Existing raw sources are confirmed at local `data/raw` and MeluXina
`/project/home/p201619/ANUMA_/data/raw`. After the ingest fixes merge, validate
them before re-ingest:

```bash
python3 -m anuma.hpc_data_check --root "$PWD" --require-raw-ready \
  --manifest-output data/raw-before-reingest.json
```

A passing check means re-ingest must reuse the existing sources. A missing,
wrong-sized, or checksum-invalid source is the only reason to download its
replacement. Re-ingest and post-ingest manifest capture remain compute-node or
approved-workstation work.

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
python3 -m pytest
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
