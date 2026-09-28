# ANUMA

Landing-site decision support for the lunar south pole. Demo case: ispace-EUROPE MAGPIE (ESA, 2029), Neospace Challenge 2026.

Phases 2–6 (scoring, API, map UI, static figures) are not in this tree yet. Phase 1 builds the common 240 m grid and the ingested layers.

## Install

Python 3.11 or newer. GDAL comes in via the `rasterio` wheel.

```bash
python3 -m pip install -r requirements.txt
```

## Commands

```bash
python3 -m anuma.ingest
python3 -m uvicorn anuma.api:app --host 127.0.0.1 --port 43127   # Phase 3, not implemented yet
python3 -m anuma.render_static --weights config/demo_weights.yaml  # Phase 5, not implemented yet
```

Unit tests use fake layers only:

```bash
python3 -m pytest tests/test_ingest.py
python3 -m anuma.ingest --synthetic --data-dir /tmp/anuma-test
```

## Ingest download sizes

| Layer | Product | Size |
| --- | --- | --- |
| DEM | `LDEM_80S_20MPP_ADJ.TIF` | 2.51 GiB |
| Illumination | `AVGVISIB_65S_240M_201608.jp2` | 46.4 MiB |
| Hydrogen | `hydrogenhd.dat` (Lunar Prospector, LEND map not on PDS) | 506 KiB |

If a URL fails, ingest prints the URL and the error and stops. It does not invent rasters.

## Grid

Moon south polar stereographic, `+proj=stere +lat_0=-90 +lon_0=0 +R=1737400 +units=m +no_defs`, 240 m/pixel, square covering 80°S–90°S. Arrays in `data/grid/*.npy`, transform in `data/grid/grid.json`.

Artemis III region names are in `data/artemis3_regions.geojson`. NASA has not published centre coordinates; geometries are null and the file is marked approximate.

Layer URLs, citations, and thresholds live in `config/layers.yaml`. See `SOURCES.md` for the jury list.
