"""Portable ranked-site and Artemis-region exports."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import numpy as np


CONTRIBUTION_NAMES = ("illumination", "hydrogen", "earth_visibility")


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if np.isfinite(number) else None


def site_records(sites: list[dict]) -> list[dict]:
    """Flatten ranked sites into stable, tabular records."""
    records = []
    for rank, site in enumerate(sites, start=1):
        contributions = site.get("contributions") or {}
        record = {
            "rank": rank,
            "longitude_deg": _finite(site.get("lon")),
            "latitude_deg": _finite(site.get("lat")),
            "row": site.get("row"),
            "col": site.get("col"),
            "score": _finite(site.get("total")),
        }
        for name in CONTRIBUTION_NAMES:
            record[f"{name}_contribution"] = _finite(contributions.get(name))
        record["nearest_artemis_region"] = site.get("nearest")
        record["distance_to_artemis_km"] = _finite(site.get("distance_km"))
        records.append(record)
    return records


def _csv(records: list[dict], fieldnames: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue().encode("utf-8")


def _scenario_lines(scenario: dict | None) -> bytes:
    """Leading ``# key: json`` lines that record the request behind a ranking."""
    if not scenario:
        return b""
    lines = [
        f"# {key}: {json.dumps(value, separators=(',', ':'), allow_nan=False)}\n"
        for key, value in scenario.items()
    ]
    return "".join(lines).encode("utf-8")


def sites_csv(sites: list[dict], scenario: dict | None = None) -> bytes:
    """Ranked sites as CSV. ``scenario`` adds ``#`` comment lines before the header."""
    records = site_records(sites)
    fields = [
        "rank",
        "longitude_deg",
        "latitude_deg",
        "row",
        "col",
        "score",
        *(f"{name}_contribution" for name in CONTRIBUTION_NAMES),
        "nearest_artemis_region",
        "distance_to_artemis_km",
    ]
    return _scenario_lines(scenario) + _csv(records, fields)


def sites_geojson(sites: list[dict], scenario: dict | None = None) -> bytes:
    """Return WGS84-like lunar lon/lat points without inventing region geometry."""
    features = []
    for record in site_records(sites):
        lon = record.pop("longitude_deg")
        lat = record.pop("latitude_deg")
        if lon is None or lat is None:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": record,
            }
        )
    collection = {
        "type": "FeatureCollection",
        "name": "anuma_ranked_sites",
        "coordinate_reference": (
            "Lunar longitude/latitude degrees on a spherical Moon (R=1737400 m); "
            "not terrestrial EPSG:4326."
        ),
        "features": features,
    }
    if scenario:
        collection["properties"] = dict(scenario)
    return (json.dumps(collection, indent=2, allow_nan=False) + "\n").encode("utf-8")


def artemis_ranking_csv(artemis: dict) -> bytes:
    records = []
    for region in artemis.get("regions") or []:
        records.append(
            {
                "rank": region.get("rank"),
                "region": region.get("name"),
                "best_score": _finite(region.get("best_score")),
                "mean_score": _finite(region.get("mean_score")),
                "coordinate_status": region.get("coordinate_status")
                or artemis.get("coordinate_status"),
                "source": artemis.get("source"),
                "note": artemis.get("note"),
            }
        )
    return _csv(
        records,
        [
            "rank",
            "region",
            "best_score",
            "mean_score",
            "coordinate_status",
            "source",
            "note",
        ],
    )
