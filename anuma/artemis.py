"""Artemis III region points and the validation note.

NASA has not published region centres. Point coordinates exist only where
Goossens et al. (2025) Table 1 gives a Mean Earth site for one of the
original 13 names. Those points are approximate. They are not polygons.
"""

from __future__ import annotations

from anuma.config import load_config
from anuma.grid import Grid
from anuma.score import artemis_overlap


# Job 5332991 is the only measured stability result this tree may display.
# Longitude is 137.3°W. The other top-10 coordinates were not reported.
MEASURED_WINNER = {
    "job_id": "5332991",
    "draws": 500,
    "lat": -89.45,
    "lon": -137.3,
    "lat_text": "89.45°S",
    "lon_text": "137.3°W",
    "place": "Shackleton connecting ridge",
    "illumination": 0.93,
    "winner_fraction": "99.8%",
    "top10_note": (
        "Six unnamed sites are top-10 in 100% of draws. "
        "Their coordinates are not in this record and are not invented."
    ),
    "clip_note": (
        "The old 2/98 clip selects 83.66°S in 100% of draws. "
        "Longitude is not in this record and is not invented."
    ),
    "clip_contrast": "Clip versus unclip changes the winner class in 500/500 draws.",
}


def distinction(cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else load_config()
    return " ".join(str(cfg["artemis3"]["distinction"]).split())


def wrap_lon(lon_east: float) -> float:
    """Fold a positive-east longitude into (-180, 180]."""
    return ((float(lon_east) + 180.0) % 360.0) - 180.0


def feature_collection(cfg: dict | None = None) -> dict:
    """Build the region collection from config. Null where no cited point exists."""
    cfg = cfg if cfg is not None else load_config()
    art = cfg["artemis3"]
    citation = art["point_source"]["citation"]
    features = []
    for region in art["regions"]:
        lists = [str(item) for item in region["lists"]]
        props = {
            "name": region["name"],
            "lists": lists,
            "approximate": True,
        }
        lon_text = region.get("lon_east")
        lat_text = region.get("lat")
        if lon_text is None or lat_text is None:
            props["coordinate_status"] = "not_published"
            geometry = None
        else:
            props.update(
                {
                    "coordinate_status": "approximate_point",
                    "paper_name": region.get("paper_name", region["name"]),
                    "lon_east": str(lon_text),
                    "lat": str(lat_text),
                    "citation": citation,
                    "doi": art["point_source"]["doi"],
                    "frame": art["point_source"]["frame"],
                    "note": (
                        "Approximate Mean Earth point from Goossens et al. (2025) "
                        "Table 1 for this name. Not a NASA-published region centre "
                        "or polygon. A shared 2022/2024 name uses the 2022 site; "
                        "the 2024 boundary is not published."
                    ),
                }
            )
            geometry = {
                "type": "Point",
                "coordinates": [wrap_lon(float(lon_text)), float(lat_text)],
            }
        features.append(
            {"type": "Feature", "properties": props, "geometry": geometry}
        )
    return {
        "type": "FeatureCollection",
        "properties": {
            "approximate": True,
            "coordinate_status": art["coordinate_status"],
            "distinction": distinction(cfg),
            "point_source": citation,
            "point_source_doi": art["point_source"]["doi"],
            "lists": art["lists"],
            "svs": art["svs"],
        },
        "features": features,
    }


def _fmt_km(distance: float | None) -> str:
    if distance is None:
        return ""
    return f"{distance:.2f}"


def validation_markdown(cfg: dict | None = None) -> str:
    """Write the coordinate search and the one measured site that can be placed."""
    cfg = cfg if cfg is not None else load_config()
    art = cfg["artemis3"]
    collection = feature_collection(cfg)
    winner = {
        "lon": MEASURED_WINNER["lon"],
        "lat": MEASURED_WINNER["lat"],
        "total": None,
    }
    overlap = artemis_overlap([winner], collection)
    placed = overlap["sites"][0]
    nearest = placed["nearest"] or ""
    distance = _fmt_km(placed["distance_km"])
    grid = Grid(cfg)

    lines = [
        "# Artemis validation",
        "",
        "NASA does not publish Artemis III region centres on nasa.gov. "
        "This note records the search, the approximate points that are cited, "
        "and the one ranked site whose coordinates job 5332991 actually gives. "
        "Ranks 2–10 are not filled in.",
        "",
        "## List distinction",
        "",
        distinction(cfg),
        "",
        "## Search",
        "",
        "Used:",
        "",
        f"- Goossens et al. (2025), *Planet. Sci. J.* 6, 191, Table 1, "
        f"{art['point_source']['doi']}. Peer-reviewed Mean Earth coordinates "
        "for 20 south-polar sites, including the original 13 Artemis names. "
        "The table's radius column is the lunar radial distance in kilometres, "
        "not a region radius. These rows are point sites the authors "
        "investigated. They are stored here as approximate points.",
        "",
        "Checked and not used as coordinates:",
        "",
        f"- NASA, 19 August 2022, {art['lists']['2022']['source_url']}. "
        "Thirteen names. The release describes regions of about 15 by 15 km. "
        "No centres. The box size is not drawn.",
        f"- NASA, 28 October 2024, {art['lists']['2024']['source_url']}. "
        "Nine names. No centres and no repeated box size.",
        f"- NASA SVS {art['svs']['id']}, {art['svs']['url']}. "
        "The page and its data API list a movie, captions, and stills for the "
        "13 regions. No latitude table, KMZ, GeoJSON, or shapefile.",
        "- LROC NAC mosaic catalogue centres, LPSC and LEAG abstracts, and the "
        "USGS navigational grids. Those are not a peer-reviewed paper and not "
        "an SVS coordinate file, so they are not used.",
        "- Published illumination points and proposed landing points inside "
        "regions (including sites 001 and 004). Those are not region centres.",
        "",
        "## Cited points",
        "",
        "Longitude is positive east, as printed in Table 1. "
        f"Distance later uses the ANUMA stereographic plane "
        f"(`{grid.proj4}`).",
        "",
        "| Region | Lists | Paper name | Lon °E | Lat | Geometry |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for feature in collection["features"]:
        props = feature["properties"]
        lists = ",".join(props["lists"])
        if feature["geometry"] is None:
            lines.append(
                f"| {props['name']} | {lists} |  |  |  | null |"
            )
        else:
            lines.append(
                f"| {props['name']} | {lists} | {props['paper_name']} | "
                f"{props['lon_east']} | {props['lat']} | approximate point |"
            )
    lines.extend(
        [
            "",
            "Malapert Massif uses the Table 1 name Malapert. Peak Near "
            "Shackleton uses Pt Nr Shackleton. Connecting Ridge Extension uses "
            "Connecting Ridge Ext. A name that appears on both NASA lists "
            "carries the 2022 point; the 2024 boundary is still unpublished.",
            "",
            "## Top 10",
            "",
            "Job 5332991 reports one winner coordinate. It does not report the "
            "other nine. Those coordinates are left blank.",
            "",
            "| Rank | Latitude | Longitude | Nearest cited point | Distance km | Record |",
            "| --- | --- | --- | --- | --- | --- |",
            f"| 1 | {MEASURED_WINNER['lat_text']} | {MEASURED_WINNER['lon_text']} | "
            f"{nearest} | {distance} | job {MEASURED_WINNER['job_id']}; "
            f"distance computed by `artemis_overlap` |",
            "| 2–10 |  |  |  |  | coordinates not reported with job 5332991 |",
            "",
            f"The winner is labelled {MEASURED_WINNER['place']} in that job, "
            f"with illumination {MEASURED_WINNER['illumination']}, in "
            f"{MEASURED_WINNER['winner_fraction']} of "
            f"{MEASURED_WINNER['draws']} weight draws. "
            f"{MEASURED_WINNER['top10_note']} {MEASURED_WINNER['clip_note']} "
            f"{MEASURED_WINNER['clip_contrast']}",
            "",
            "137.3°W is longitude −137.3° on the positive-east grid. "
            f"The distance is in the stereographic plane, not a geodesic, "
            f"and is rounded to 0.01 km for this table. "
            f"Nearest cited point: {nearest}. Distance: {distance} km.",
            "",
            "Region best score and rank need a scored raster. This checkout "
            "does not contain the ingested grids, so those scores are not "
            "computed for this table. `tests/test_score.py` checks best score "
            "and rank on constructed polygons, and checks this winner's "
            "nearest point.",
            "",
            "No radius is assigned to any region.",
            "",
        ]
    )
    return "\n".join(lines)
