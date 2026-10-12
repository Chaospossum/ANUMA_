# ANUMA data sources

Verified 28 September 2026. Product names and URLs are in `config/layers.yaml`. USGS Astropedia does not host these three rasters; they come from PGDA and the PDS Geosciences Node.

## DEM / slope

- **Product:** `LDEM_80S_20MPP_ADJ` (Barker et al. 2023; renamed from `LDEM_80S_20M`)
- **URL:** https://pgda.gsfc.nasa.gov/data/LOLA_20mpp/LDEM_80S_20MPP_ADJ.TIF
- **Catalog:** https://pgda.gsfc.nasa.gov/products/90
- **Resolution:** 20 m/pixel, downsampled to the 240 m ANUMA grid
- **Coverage:** 80°S–90°S, polar stereographic, about ±304 km
- **Citation:** Barker, M. K., et al. (2023), A new view of the lunar south pole from the Lunar Orbiter Laser Altimeter (LOLA), *Planet. Sci. J.* 4, 183. https://doi.org/10.3847/PSJ/acf3e1
- **Data DOI:** https://doi.org/10.60903/gsfcpgda-lola-spole
- **Licence:** citation requested on the PGDA page; no other licence text
- **Size:** 2,696,082,051 bytes
- **Slope:** 3×3 Horn gradient in degrees on the 240 m grid (not a separate download)

`LDEM_87S_20M` is not a published filename. `LDEM_875S_20M` exists but only covers 87.5°S–90°S.

## Illumination

- **Product:** `AVGVISIB_65S_240M_201608`
- **URL:** https://pds-geosciences.wustl.edu/lro/lro-l-lola-3-rdr-v1/lrolol_1xxx/extras/illumination/jp2/avgvisib_65s_240m_201608.jp2
- **Catalog:** https://pgda.gsfc.nasa.gov/products/69
- **Resolution:** 240 m/pixel
- **Coverage:** 65°S to the pole (includes 80°S–90°S)
- **Scale:** fraction = DN × 0.00004
- **Citation:** Mazarico, E., et al. (2011), Illumination conditions of the lunar polar regions using LOLA topography, *Icarus* 211, 1066–1081. https://doi.org/10.1016/j.icarus.2010.10.030
- **Licence:** PDS EXTRAS; the 2016 readme says the files are not supported by PDS or the LOLA team
- **Size:** 48,625,783 bytes
- **Note:** the live raster is the 2016 release. No 2018-dated illumination file was found. This is a published map, not a model we wrote.

## Hydrogen (ice proxy)

- **Product:** Lunar Prospector Neutron Spectrometer `hydrogenhd` (half-degree hydrogen abundance)
- **URL:** https://pds-geosciences.wustl.edu/missions/lunarp/reduced/hydrogenhd.dat
- **Catalog:** https://pds-geosciences.wustl.edu/missions/lunarp/reduced_special.html
- **Resolution:** 0.5° × 0.5°, bilinear-resampled to 240 m
- **Coverage:** global cylindrical; south pole included
- **Citation:** Feldman, W. C., et al. (2001), Evidence for water ice near the lunar poles, *J. Geophys. Res.* 106(E10), 23231–23251. https://doi.org/10.1029/2000JE001444
- **Licence:** special product, not a peer-reviewed PDS archive; no licence text
- **Size:** 518,400 bytes
- **Fallback:** LRO LEND on the PDS Geosciences Node is time-series counts (EDR/RDR/ALD). There is no downloadable Sanin et al. (2017) collimated-epithermal hydrogen map.

## Earth visibility

- **Product:** derived from the ingested DEM
- **Method:** 100 km ray toward sub-Earth (0°E, 0°N); clearance = Earth elevation minus horizon elevation. See `config/layers.yaml` `earth_visibility`.

## Artemis III regions (overlay)

- **2022 list (13, includes Connecting Ridge):** https://www.nasa.gov/news-release/nasa-identifies-candidate-regions-for-landing-next-americans-on-moon/
- **2024 list (9, does not include Connecting Ridge; includes de Gerlache Rim 2):** Peak near Cabeus B; Haworth; Malapert Massif; Mons Mouton Plateau; Mons Mouton; Nobile Rim 1; Nobile Rim 2; de Gerlache Rim 2; Slater Plain. https://www.nasa.gov/news-release/nasa-provides-update-on-artemis-iii-moon-landing-regions/
- **Centres:** not published on nasa.gov. NASA SVS 5013 shows the 13 regions and does not publish numeric coordinates. https://svs.gsfc.nasa.gov/5013/
- **Approximate points:** Goossens, S. J., et al. (2025), *Planet. Sci. J.* 6, 191, Table 1. https://doi.org/10.3847/PSJ/adf05f. Mean Earth sites for the original 13 names, not NASA polygons. Peak near Cabeus B, Mons Mouton Plateau, Mons Mouton, and Slater Plain have no cited point; geometry stays null.
