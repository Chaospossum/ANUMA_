# ANUMA

Landing-site decision support for the lunar south pole. The mission case is still open.

ANUMA is an independent student project. It is not affiliated with or endorsed
by ispace, ESA, or the MAGPIE mission team. Names are used only to describe the
challenge and mission context.

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
approved-workstation work. On MeluXina, first use module discovery to identify
stable-release Python 3.12, then create the supported hash-pinned Rasterio
environment with `ANUMA_ENVIRONMENT=ingest bash hpc/bootstrap.sh` from a
compute allocation. Compare cross-machine transfer manifests by each file's
SHA-256, not by the whole manifest hash, because absolute paths differ.

## Serve the UI

After ingest completes, start the API and static UI:

```bash
python3 -m uvicorn anuma.api:app --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000`. The page scores automatically with the configured
defaults and displays the combined map and ranked sites. If the real grids are
missing, the page deliberately remains empty and shows the ingest command.
After scoring, **Download CSV** and **Download GeoJSON** export the current
ranked sites. The equivalent API calls are `POST /score.csv` and
`POST /score.geojson`, using the same JSON body as `POST /score`.

## Render static figures

After ingest, render the configured demonstration weights:

```bash
python3 -m anuma.render_static --weights config/demo_weights.yaml
```

The command reads `data/grid/*.npy` and writes `out/static/score_heatmap.png`
with numbered ranked sites, `slope_mask.png`, `sites.csv`,
`artemis_ranking.csv`, the unannotated `score.png`, and normalized criterion
figures. Artemis rows are ranked only where a cited point falls on a
finite score. Four 2024-only names stay unranked because they have no cited
geometry. The exporter does not invent coordinates or a plume radius. It prints every written path. Use
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

Artemis III names are in `config/layers.yaml` and `GET /artemis3.geojson`. NASA has not published centres. Approximate points for the 2022 names come from Goossens et al. (2025) Table 1 and are marked approximate. Peak near Cabeus B, Mons Mouton Plateau, Mons Mouton, and Slater Plain stay null. See `docs/results.md` and `out/artemis-validation.md`.

Optional keep-out on `POST /score`: send `keepout.points`, a required `keepout.radius_km`, and `keepout.mode` of `hard` or `penalty`. Penalty mode also requires `keepout.penalty`. There is no default radius. Orbital debris affects trajectories, not site choice, so it is not modelled.

Layer URLs, citations, and thresholds live in `config/layers.yaml`. See `SOURCES.md` for the jury list.

## Licensing

No repository-wide software licence has been selected because this checkout
does not establish the authors' authority or preferred terms. Third-party
runtime assets retain their own permissive licences under
`static/vendor/LICENSES/`; see `static/vendor/NOTICE.txt` for versions and
sources.
