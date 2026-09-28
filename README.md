# ANUMA

Landing-site decision support for the lunar south pole. Demo case: ispace-EUROPE MAGPIE (ESA, 2029), Neospace Challenge 2026.

Phases 1–4 are in this tree: ingest, scoring, FastAPI, and the map UI at `http://localhost:8000`. Static figures are Phase 5.

## Install

Python 3.11 or newer. GDAL comes in via the `rasterio` wheel.

```bash
python3 -m pip install -r requirements.txt
```

## Commands

```bash
python3 -m anuma.ingest
python3 -m uvicorn anuma.api:app --host 127.0.0.1 --port 8000
python3 -m anuma.render_static --weights config/demo_weights.yaml  # Phase 5, not implemented yet
```

Unit tests use constructed arrays and `--synthetic` ingest only:

```bash
python3 -m pytest tests/test_ingest.py tests/test_score.py tests/test_api.py
python3 -m anuma.ingest --synthetic --data-dir /tmp/anuma-test
```

`POST /score` and `GET /layer/{name}.png` return HTTP 412 with the ingest command until real grids exist in `data/grid/`. The API does not score `--synthetic` layers for the demo.

## Ingest download sizes

| Layer | Product | Size |
| --- | --- | --- |
| DEM | `LDEM_80S_20MPP_ADJ.TIF` | 2.51 GiB |
| Illumination | `AVGVISIB_65S_240M_201608.jp2` | 46.4 MiB |
| Hydrogen | `hydrogenhd.dat` (Lunar Prospector, LEND map not on PDS) | 506 KiB |

Open `http://localhost:8000`. The stereographic well stays empty until `data/grid/` holds real warped layers. The UI will not fill the map with `--synthetic` scores.

If ingest has not been run:

```bash
python3 -m anuma.ingest
```

The LOLA DEM is 2.51 GiB and can take a long time. Until it finishes, the UI shows the not-ingested state and the command above.

## Grid

Moon south polar stereographic, `+proj=stere +lat_0=-90 +lon_0=0 +R=1737400 +units=m +no_defs`, 240 m/pixel, square covering 80°S–90°S. Arrays in `data/grid/*.npy`, transform in `data/grid/grid.json`.

Artemis III region names are in `data/artemis3_regions.geojson`. NASA has not published centre coordinates; geometries are null and the file is marked approximate.

Layer URLs, citations, and thresholds live in `config/layers.yaml`. See `SOURCES.md` for the jury list.
