"""Stage E: sunlit hours per UTC day on the 240 m cap, reusing Stage A.

Each UTC day has 24 hourly epochs at HH:00:00Z. A pixel's value for a day is
the number of those epochs in which any fraction of the ±0.27° solar disc is
above the Stage A terrain horizon (the same lit rule as Stage A). Values are
uint8 0-24; out-of-cap pixels (NaN in the Stage A layers) are 255.

A monthly cumulative sum (uint32, fill 4294967295) lets a reader average any
whole-day range from two cumulative slices plus the daily slices of the
partial months at either end. This is a scenario layer, not a measurement and
not a mission-duration claim.

On disk, under one output directory:

- ``daily/lit-hours-YYYY.npy``: (days, height, width) uint8 per calendar year
  (clipped to the period) and ``daily/lit-hours-YYYY.json``, written last, as
  the completion record used to resume.
- ``cumulative-monthly.npy``: (boundaries, height, width) uint32. Slice k is
  the lit-hour total over [period start, boundary k).
- ``valid-mask.npy``, ``mean-lit-fraction.npy``, ``daily-lit-index.json``.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import platform
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Sequence

import numpy as np
import yaml

from anuma.config import repo_root
from anuma.stage_b_gate import FROZEN_STOP_RMSE, require_stage_a_acceptance
from anuma.sun import (
    _advance_sunlight_block,
    _atomic_json,
    _atomic_save,
    _lit_mask,
    _local_frames,
    _log_progress,
    _row_blocks,
    _start_tile_pool,
    configured_kernel_manifest,
    load_sun_config,
    sun_vectors_from_spice,
    verify_kernel_manifest,
)
import anuma.sun as _sun

HOURS_PER_DAY = 24
FILL_UINT8 = 255
FILL_UINT32 = np.iinfo(np.uint32).max
SCHEMA_VERSION = 1
STAGE = "E-daily-lit-240m"
INDEX_NAME = "daily-lit-index.json"
RUN_CONFIG_NAME = "run-config.json"


# --------------------------------------------------------------------------
# Configuration and calendar helpers
# --------------------------------------------------------------------------


def config_path(path: Path | None = None) -> Path:
    return Path(path) if path else repo_root() / "config" / "daily-lit.yaml"


def load_daily_lit_config(path: Path | None = None) -> dict:
    cfg = yaml.safe_load(config_path(path).read_text())
    if not isinstance(cfg, dict) or cfg.get("stage") != STAGE:
        raise ValueError(f"daily-lit config must declare stage {STAGE}")
    if int(cfg["output"]["lit_hours_fill_uint8"]) != FILL_UINT8:
        raise ValueError("lit_hours_fill_uint8 must stay 255")
    if int(cfg["output"]["cumulative_fill_uint32"]) != int(FILL_UINT32):
        raise ValueError("cumulative_fill_uint32 must stay 4294967295")
    if int(cfg["period"]["cadence_hours"]) != 1:
        raise ValueError("daily lit hours require 1 h epochs")
    if float(cfg["stage_a"]["stop_rmse"]) != FROZEN_STOP_RMSE:
        raise ValueError("Stage A stop_rmse must stay 0.05")
    if cfg["acceptance"].get("tune_after_run") is not False:
        raise ValueError("acceptance.tune_after_run must stay false")
    if int(cfg["pilot"]["days"]) < 2 * int(cfg["execution"]["chunk_days"]):
        raise ValueError("pilot must span two dispatches so one is timed warm")
    return cfg


def parse_day(value: object) -> date:
    """A UTC day from a date, 'YYYY-MM-DD', or a midnight 'YYYY-MM-DDT00:00:00Z'."""
    if isinstance(value, datetime):
        raise ValueError("pass a date or a UTC midnight string, not a datetime")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"not a UTC day: {value!r}")
    if "T" in value:
        if not value.endswith("Z"):
            raise ValueError(f"timestamp must be UTC Zulu: {value!r}")
        moment = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        if moment.time() != datetime.min.time():
            raise ValueError(f"day boundaries must be UTC midnight: {value!r}")
        return moment.date()
    return date.fromisoformat(value)


def period_days(cfg: dict, mode: str) -> tuple[date, date]:
    """Return [start, end) days for the pilot or the full production period."""
    if mode == "pilot":
        start = parse_day(cfg["pilot"]["start_utc"])
        days = int(cfg["pilot"]["days"])
        if days <= 0:
            raise ValueError("pilot days must be positive")
        return start, start + timedelta(days=days)
    if mode != "production":
        raise ValueError(f"unknown mode {mode!r}")
    period = cfg["period"]
    start = parse_day(period["start_utc"])
    end = parse_day(period["end_utc"])
    span = Decimal(str(period["minimum_span_years"])) * Decimal(
        str(period["year_length_days"])
    )
    if Decimal((end - start).days) < span:
        raise ValueError("production period is shorter than the declared nodal cycle")
    return start, end


def day_epochs(first_day: date, day_count: int) -> list[str]:
    """Hourly UTC epochs HH:00:00Z for day_count whole days from first_day."""
    base = datetime(first_day.year, first_day.month, first_day.day, tzinfo=timezone.utc)
    return [
        (base + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for hour in range(day_count * HOURS_PER_DAY)
    ]


def year_chunks(start: date, end: date) -> list[dict]:
    """Calendar-year chunks of [start, end), each a resume unit."""
    if end <= start:
        raise ValueError("period end must be after its start")
    chunks = []
    cursor = start
    while cursor < end:
        stop = min(end, date(cursor.year + 1, 1, 1))
        chunks.append(
            {
                "label": f"{cursor.year:04d}",
                "first_day": cursor.isoformat(),
                "day_count": (stop - cursor).days,
                "offset_days": (cursor - start).days,
            }
        )
        cursor = stop
    return chunks


def month_boundaries(start: date, end: date) -> list[date]:
    """Period start, every month start strictly inside, and period end."""
    boundaries = [start]
    year, month = start.year, start.month
    while True:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        boundary = date(year, month, 1)
        if boundary >= end:
            break
        boundaries.append(boundary)
    boundaries.append(end)
    return boundaries


# --------------------------------------------------------------------------
# Daily accumulation on row tiles
# --------------------------------------------------------------------------


def _daily_lit_block(
    horizons: np.ndarray,
    longitude: np.ndarray,
    latitude: np.ndarray,
    vectors: np.ndarray,
    *,
    disk_radius_deg: float,
    azimuth_origin_deg: float,
) -> np.ndarray:
    """Lit epochs per UTC day for one row tile. vectors = whole days × 24."""
    if len(vectors) % HOURS_PER_DAY:
        raise ValueError("daily accumulation needs whole days of hourly epochs")
    frames = _local_frames(longitude, latitude)
    day_count = len(vectors) // HOURS_PER_DAY
    counts = np.zeros((day_count, *longitude.shape), dtype=np.uint8)
    for index, vector in enumerate(vectors):
        counts[index // HOURS_PER_DAY] += _lit_mask(
            vector,
            horizons,
            longitude,
            frames,
            disk_radius_deg=disk_radius_deg,
            azimuth_origin_deg=azimuth_origin_deg,
        )
    return counts


def _daily_lit_tile(task: tuple) -> tuple[int, int, np.ndarray]:
    row0, row1, vectors, disk_radius_deg, azimuth_origin_deg = task
    shared = _sun._TILE_SHARED
    if shared is None:
        raise RuntimeError("row-tile worker started without shared arrays")
    counts = _daily_lit_block(
        np.ascontiguousarray(shared["horizons"][:, row0:row1, :]),
        np.ascontiguousarray(shared["longitude"][row0:row1, :]),
        np.ascontiguousarray(shared["latitude"][row0:row1, :]),
        vectors,
        disk_radius_deg=disk_radius_deg,
        azimuth_origin_deg=azimuth_origin_deg,
    )
    return row0, row1, counts


def daily_lit_hours(
    horizons: np.ndarray,
    longitude: np.ndarray,
    latitude: np.ndarray,
    vectors: np.ndarray,
    *,
    disk_radius_deg: float,
    azimuth_origin_deg: float,
    workers: int = 1,
    pool=None,
) -> np.ndarray:
    """Lit hours per day, shape (days, height, width), uint8 0-24, no fill.

    workers=1 is the serial reference. With a pool, row tiles run in the
    fork workers started by anuma.sun._start_tile_pool on the same arrays.
    """
    vectors = np.ascontiguousarray(vectors, dtype=np.float64)
    if vectors.ndim != 2 or vectors.shape[1] != 3:
        raise ValueError("vectors must have shape (epochs, 3)")
    if workers <= 0 or workers > 128:
        raise ValueError("workers must be between 1 and 128")
    if workers == 1 or longitude.shape[0] == 1:
        return _daily_lit_block(
            horizons,
            longitude,
            latitude,
            vectors,
            disk_radius_deg=disk_radius_deg,
            azimuth_origin_deg=azimuth_origin_deg,
        )
    out = np.zeros((len(vectors) // HOURS_PER_DAY, *longitude.shape), dtype=np.uint8)
    tasks = [
        (row0, row1, vectors, float(disk_radius_deg), float(azimuth_origin_deg))
        for row0, row1 in _row_blocks(longitude.shape[0], workers)
    ]
    owned = pool is None
    if owned:
        pool = _start_tile_pool(horizons, longitude, latitude, workers)
    try:
        for row0, row1, counts in pool.imap_unordered(_daily_lit_tile, tasks):
            out[:, row0:row1, :] = counts
    finally:
        if owned:
            pool.close()
            pool.join()
    return out


def valid_pixels(
    horizons: np.ndarray, longitude: np.ndarray, latitude: np.ndarray
) -> np.ndarray:
    """Same validity rule as anuma.sun.evaluate_sunlight, one azimuth at a time."""
    valid = np.zeros(longitude.shape, dtype=bool)
    for layer in horizons:
        valid |= np.isfinite(layer)
    return valid & np.isfinite(longitude) & np.isfinite(latitude)


# --------------------------------------------------------------------------
# Chunk checkpoints
# --------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def chunk_paths(output_dir: Path, label: str) -> tuple[Path, Path]:
    daily = Path(output_dir) / "daily"
    return daily / f"lit-hours-{label}.npy", daily / f"lit-hours-{label}.json"


def completed_chunk(
    output_dir: Path, chunk: dict, shape: tuple[int, int], run_key: dict
) -> dict | None:
    """Return the completion record if this chunk can be skipped, else None.

    The JSON record is written after the array, so an array without a record
    (a kill mid-write) is recomputed. A record that disagrees with the chunk,
    the grid, or the run key is refused rather than silently reused.
    """
    array_path, record_path = chunk_paths(output_dir, chunk["label"])
    if not record_path.is_file():
        return None
    record = json.loads(record_path.read_text())
    expected = {
        "label": chunk["label"],
        "first_day": chunk["first_day"],
        "day_count": chunk["day_count"],
        "shape": [chunk["day_count"], *shape],
        "dtype": "uint8",
        "run_key": run_key,
    }
    for key, value in expected.items():
        if record.get(key) != value:
            raise ValueError(f"chunk {chunk['label']} record mismatch: {key}")
    if not array_path.is_file():
        raise ValueError(f"chunk {chunk['label']} record exists without its array")
    stored = np.load(array_path, mmap_mode="r", allow_pickle=False)
    if stored.shape != tuple(expected["shape"]) or stored.dtype != np.uint8:
        raise ValueError(f"chunk {chunk['label']} array does not match its record")
    if array_path.stat().st_size != int(record["bytes"]):
        raise ValueError(f"chunk {chunk['label']} array size changed")
    return record


def write_chunk(
    output_dir: Path, chunk: dict, daily: np.ndarray, run_key: dict
) -> dict:
    array_path, record_path = chunk_paths(output_dir, chunk["label"])
    _atomic_save(array_path, daily)
    record = {
        "label": chunk["label"],
        "first_day": chunk["first_day"],
        "day_count": chunk["day_count"],
        "shape": list(daily.shape),
        "dtype": "uint8",
        "file": str(array_path.relative_to(output_dir)),
        "bytes": array_path.stat().st_size,
        "sha256": _sha256(array_path),
        "run_key": run_key,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(record_path, record)
    return record


# --------------------------------------------------------------------------
# Cumulative sums, index, and reader
# --------------------------------------------------------------------------


def build_cumulative(
    output_dir: Path,
    start: date,
    end: date,
    chunks: Sequence[dict],
    valid: np.ndarray,
) -> tuple[list[date], dict]:
    """Write cumulative-monthly.npy and mean-lit-fraction.npy from the chunks.

    The same pass checks every stored day: valid pixels hold 0-24 and
    out-of-cap pixels hold the fill value on every day.
    """
    from numpy.lib.format import open_memmap

    boundaries = month_boundaries(start, end)
    shape = valid.shape
    final = Path(output_dir) / "cumulative-monthly.npy"
    temporary = final.with_suffix(".npy.tmp")
    cumulative = open_memmap(
        temporary, mode="w+", dtype=np.uint32, shape=(len(boundaries), *shape)
    )
    running = np.zeros(shape, dtype=np.uint32)
    day_max = np.zeros(shape, dtype=np.uint8)
    day_min = np.full(shape, FILL_UINT8, dtype=np.uint8)
    boundary_offsets = [(item - start).days for item in boundaries]
    next_boundary = 0
    for chunk in chunks:
        array_path, _ = chunk_paths(output_dir, chunk["label"])
        daily = np.load(array_path, mmap_mode="r", allow_pickle=False)
        first = int(chunk["offset_days"])
        cursor = first
        stop = first + int(chunk["day_count"])
        while cursor < stop:
            while boundary_offsets[next_boundary] <= cursor:
                cumulative[next_boundary] = np.where(valid, running, FILL_UINT32)
                next_boundary += 1
            segment_end = min(stop, boundary_offsets[next_boundary])
            segment = np.asarray(daily[cursor - first : segment_end - first])
            np.maximum(day_max, segment.max(axis=0), out=day_max)
            np.minimum(day_min, segment.min(axis=0), out=day_min)
            running += np.where(
                valid, segment.sum(axis=0, dtype=np.uint32), 0
            ).astype(np.uint32)
            cursor = segment_end
    while next_boundary < len(boundaries):
        cumulative[next_boundary] = np.where(valid, running, FILL_UINT32)
        next_boundary += 1
    cumulative.flush()
    del cumulative
    os.replace(temporary, final)
    total_hours = HOURS_PER_DAY * (end - start).days
    mean = (running / float(total_hours)).astype(np.float32)
    mean[~valid] = np.nan
    _atomic_save(Path(output_dir) / "mean-lit-fraction.npy", mean)
    max_valid = int(day_max[valid].max(initial=0))
    checks = {
        "lit_hours_max_valid": max_valid,
        "lit_hours_within_0_24": max_valid <= HOURS_PER_DAY,
        "fill_matches_valid_mask": bool(np.all(day_min[~valid] == FILL_UINT8)),
    }
    return boundaries, checks


def write_index(
    output_dir: Path,
    start: date,
    end: date,
    shape: tuple[int, int],
    chunks: Sequence[dict],
    boundaries: Sequence[date],
) -> dict:
    index = {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "kind": "modeled scenario layer, not a measurement",
        "period": {
            "start_day_utc": start.isoformat(),
            "end_day_exclusive_utc": end.isoformat(),
            "day_count": (end - start).days,
            "epochs_per_day": HOURS_PER_DAY,
            "epoch_times": "HH:00:00Z for HH = 00..23",
        },
        "shape": list(shape),
        "lit_rule": "any fraction of the ±0.27° solar disc above the horizon",
        "fill": {
            "lit_hours_uint8": FILL_UINT8,
            "cumulative_uint32": int(FILL_UINT32),
            "mean_lit_fraction_float32": "NaN",
            "meaning": "out-of-cap pixel; NaN in the Stage A layers",
        },
        "chunks": [
            {
                "file": f"daily/lit-hours-{chunk['label']}.npy",
                "first_day": chunk["first_day"],
                "day_count": chunk["day_count"],
            }
            for chunk in chunks
        ],
        "cumulative": {
            "file": "cumulative-monthly.npy",
            "boundaries": [item.isoformat() for item in boundaries],
            "meaning": "slice k = lit hours over [start_day_utc, boundaries[k])",
        },
        "valid_mask": "valid-mask.npy",
        "mean_lit_fraction": "mean-lit-fraction.npy",
    }
    _atomic_json(Path(output_dir) / INDEX_NAME, index)
    return index


class DailyLitCube:
    """Read-only access to a finished daily lit-hours output directory."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.index = json.loads((self.root / INDEX_NAME).read_text())
        if self.index.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported daily-lit index schema")
        period = self.index["period"]
        self.start = parse_day(period["start_day_utc"])
        self.end = parse_day(period["end_day_exclusive_utc"])
        self.shape = tuple(self.index["shape"])
        self._chunks = [
            (
                parse_day(item["first_day"]),
                int(item["day_count"]),
                np.load(self.root / item["file"], mmap_mode="r", allow_pickle=False),
            )
            for item in self.index["chunks"]
        ]
        self.boundaries = [parse_day(item) for item in self.index["cumulative"]["boundaries"]]
        self.cumulative = np.load(
            self.root / self.index["cumulative"]["file"], mmap_mode="r", allow_pickle=False
        )
        self.valid = np.load(self.root / self.index["valid_mask"], allow_pickle=False)

    def _check_range(self, start: date, end: date) -> None:
        if not self.start <= start < end <= self.end:
            raise ValueError(
                f"range [{start}, {end}) is outside [{self.start}, {self.end})"
            )

    def daily(
        self, start: object, end: object, window: tuple[slice, slice] | None = None
    ) -> np.ndarray:
        """uint8 lit hours per day for [start, end), fill 255 out of cap."""
        start, end = parse_day(start), parse_day(end)
        self._check_range(start, end)
        rows, cols = window or (slice(None), slice(None))
        parts = []
        for first, count, array in self._chunks:
            lo = max(start, first)
            hi = min(end, first + timedelta(days=count))
            if lo < hi:
                parts.append(
                    np.asarray(array[(lo - first).days : (hi - first).days, rows, cols])
                )
        return np.concatenate(parts, axis=0)

    def lit_hours_sum(
        self, start: object, end: object, window: tuple[slice, slice] | None = None
    ) -> np.ndarray:
        """int64 total lit hours over [start, end); out-of-cap pixels are -1."""
        start, end = parse_day(start), parse_day(end)
        self._check_range(start, end)
        rows, cols = window or (slice(None), slice(None))
        valid = self.valid[rows, cols]
        inner = [item for item in self.boundaries if start <= item <= end]
        if len(inner) >= 2:
            first, last = inner[0], inner[-1]
            k0, k1 = self.boundaries.index(first), self.boundaries.index(last)
            total = self.cumulative[k1, rows, cols].astype(np.int64) - self.cumulative[
                k0, rows, cols
            ].astype(np.int64)
            for lo, hi in ((start, first), (last, end)):
                if lo < hi:
                    total += self.daily(lo, hi, window).sum(axis=0, dtype=np.int64)
        else:
            total = self.daily(start, end, window).sum(axis=0, dtype=np.int64)
        return np.where(valid, total, -1)

    def lit_hours_mean(
        self, start: object, end: object, window: tuple[slice, slice] | None = None
    ) -> np.ndarray:
        """float32 mean lit hours per UTC day over [start, end); NaN out of cap.

        Divide by 24 for the lit fraction of the range.
        """
        start_day, end_day = parse_day(start), parse_day(end)
        total = self.lit_hours_sum(start_day, end_day, window)
        mean = (total / float((end_day - start_day).days)).astype(np.float32)
        mean[total < 0] = np.nan
        return mean


def lit_hours_mean(
    root: Path, start: object, end: object, window: tuple[slice, slice] | None = None
) -> np.ndarray:
    """Mean lit hours per UTC day over [start, end) from a finished output dir."""
    return DailyLitCube(root).lit_hours_mean(start, end, window)


# --------------------------------------------------------------------------
# SPICE coverage
# --------------------------------------------------------------------------

# spkpos("SUN", ..., "MOON") with LT+S chains Sun (10) -> SSB (0) and
# Moon (301) -> Earth-Moon barycentre (3) -> SSB.
SPK_BODIES = (10, 3, 301)


def window_inside(intervals: Sequence[tuple[float, float]], lo: float, hi: float) -> bool:
    """True when one coverage interval contains [lo, hi]."""
    return any(left <= lo and hi <= right for left, right in intervals)


def spice_coverage(
    kernel_manifest: Path,
    required_roles: Sequence[str],
    window: tuple[str, str] | None = None,
) -> dict:
    """Read the time coverage of the verified kernels from the kernels themselves.

    Binary SPK coverage is the intersection over SPK_BODIES; binary PCK
    coverage is per frame class. Text kernels (frames, leap seconds) have no
    coverage window. With ``window`` (first and last epoch as UTC strings),
    ``covers_window`` says whether every binary kernel covers it.
    """
    paths = verify_kernel_manifest(kernel_manifest, required_roles)
    try:
        import spiceypy as spice
    except ImportError as exc:  # pragma: no cover - depends on HPC environment
        raise RuntimeError("spiceypy is required to read kernel coverage") from exc

    def windows(cell) -> list[tuple[float, float]]:
        return [tuple(spice.wnfetd(cell, i)) for i in range(spice.wncard(cell))]

    def utc(et: float) -> str:
        return spice.et2utc(et, "ISOC", 0) + "Z"

    spice.kclear()
    try:
        for path in paths:
            spice.furnsh(str(path))
        bounds = None
        if window is not None:
            bounds = (spice.str2et(window[0]), spice.str2et(window[1]))
        report: dict = {"roles": {}, "window": list(window) if window else None}
        covers = True
        for role, path in zip(required_roles, paths):
            architecture, kind = spice.getfat(str(path))
            entry: dict = {"file": path.name, "architecture": architecture, "type": kind}
            intervals: list[tuple[float, float]] | None = None
            if architecture == "DAF" and kind == "SPK":
                per_body = {}
                for body in SPK_BODIES:
                    cell = spice.cell_double(2000)
                    spice.spkcov(str(path), body, cell)
                    per_body[body] = windows(cell)
                entry["bodies"] = {
                    str(body): [[utc(a), utc(b)] for a, b in spans]
                    for body, spans in per_body.items()
                }
                intervals = _intersect([per_body[body] for body in SPK_BODIES])
            elif architecture == "DAF" and kind == "PCK":
                ids = spice.cell_int(100)
                spice.pckfrm(str(path), ids)
                per_frame = {}
                for index in range(spice.card(ids)):
                    frame = int(ids[index])
                    cell = spice.cell_double(2000)
                    spice.pckcov(str(path), frame, cell)
                    per_frame[frame] = windows(cell)
                entry["frames"] = {
                    str(frame): [[utc(a), utc(b)] for a, b in spans]
                    for frame, spans in per_frame.items()
                }
                intervals = _intersect(list(per_frame.values()))
            else:
                entry["coverage"] = "text kernel; no time window"
            if intervals is not None:
                entry["coverage_utc"] = [[utc(a), utc(b)] for a, b in intervals]
                if bounds is not None:
                    entry["covers_window"] = window_inside(intervals, *bounds)
                    covers = covers and entry["covers_window"]
            report["roles"][role] = entry
        if bounds is not None:
            report["covers_window"] = covers
    finally:
        spice.kclear()
    return report


def _intersect(groups: Sequence[Sequence[tuple[float, float]]]) -> list[tuple[float, float]]:
    """Intersection of interval lists (each sorted, non-overlapping)."""
    if not groups:
        return []
    result = [tuple(item) for item in groups[0]]
    for group in groups[1:]:
        merged = []
        for a0, a1 in result:
            for b0, b1 in group:
                lo, hi = max(a0, b0), min(a1, b1)
                if lo <= hi:
                    merged.append((lo, hi))
        result = merged
    return result


def require_spice_coverage(
    kernel_manifest: Path, required_roles: Sequence[str], start: date, end: date
) -> dict:
    """Refuse a period that the verified kernels do not cover."""
    last_epoch = day_epochs(end - timedelta(days=1), 1)[-1]
    first_epoch = day_epochs(start, 1)[0]
    report = spice_coverage(kernel_manifest, required_roles, (first_epoch, last_epoch))
    if report.get("covers_window") is not True:
        raise ValueError(
            f"kernels do not cover {first_epoch} to {last_epoch}: "
            + json.dumps(report["roles"], sort_keys=True)
        )
    return report


# --------------------------------------------------------------------------
# Stage A inputs and receipt pins
# --------------------------------------------------------------------------


def stage_a_horizon_pin(stage_a_dir: Path) -> dict:
    """Stage A acceptance plus the horizon cube digest it recorded."""
    stage_a_dir = Path(stage_a_dir).resolve()
    report = require_stage_a_acceptance(stage_a_dir / "validation-report.json")
    manifest = json.loads((stage_a_dir / "output-manifest.json").read_text())
    entry = (manifest.get("outputs") or {}).get("terrain-horizon.npy")
    if not isinstance(entry, dict) or len(str(entry.get("sha256", ""))) != 64:
        raise ValueError("Stage A output manifest has no terrain-horizon.npy digest")
    return {
        "stage_a_dir": str(stage_a_dir),
        "stage_a_status": report.get("status"),
        "stage_a_rmse": float(report["metrics"]["rmse"]),
        "stage_a_stop_rmse": FROZEN_STOP_RMSE,
        "stage_a_acceptance_sha256": _sha256(stage_a_dir / "validation-report.json"),
        "horizon_sha256": str(entry["sha256"]).lower(),
        "horizon_bytes": int(entry["bytes"]),
    }


def receipt_pin(
    *,
    kernel_manifest: Path,
    stage_a_dir: Path,
    horizon_cube: Path,
    mode: str,
    daily_lit_config: Path | None = None,
) -> dict:
    """Pins a daily-lit preflight receipt records. Does not hash the 9 GB cube."""
    cfg_path = config_path(daily_lit_config).resolve()
    cfg = load_daily_lit_config(cfg_path)
    start, end = period_days(cfg, mode)
    kernel_path = Path(kernel_manifest)
    if not kernel_path.is_file():
        raise ValueError(f"kernel manifest is missing: {kernel_path}")
    pin = stage_a_horizon_pin(stage_a_dir)
    horizon = Path(horizon_cube).resolve()
    if not horizon.is_file():
        raise ValueError(f"horizon cube is missing: {horizon}")
    if horizon.stat().st_size != pin["horizon_bytes"]:
        raise ValueError("horizon cube size differs from the Stage A manifest")
    return {
        **pin,
        "horizon_cube": str(horizon),
        "kernel_manifest": str(kernel_path.resolve()),
        "kernel_manifest_sha256": _sha256(kernel_path),
        "daily_lit_config": str(cfg_path),
        "daily_lit_config_sha256": _sha256(cfg_path),
        "daily_lit_mode": mode,
        "period_start_day": start.isoformat(),
        "period_end_day_exclusive": end.isoformat(),
        "accelerators": False,
    }


def reject_daily_lit_receipt(
    receipt: dict,
    *,
    kernel_manifest: Path,
    stage_a_dir: Path,
    horizon_cube: Path,
    mode: str,
    daily_lit_config: Path | None = None,
) -> None:
    """Refuse a receipt whose Stage A, horizon, kernel, config, or period drifted."""
    if receipt.get("account") != "p201619":
        raise ValueError("daily lit requires account p201619")
    if receipt.get("accelerators") is not False:
        raise ValueError("daily lit does not allow accelerators")
    pin = receipt_pin(
        kernel_manifest=kernel_manifest,
        stage_a_dir=stage_a_dir,
        horizon_cube=horizon_cube,
        mode=mode,
        daily_lit_config=daily_lit_config,
    )
    for key, value in pin.items():
        if receipt.get(key) != value:
            raise ValueError(f"daily lit receipt mismatch: {key}")


# --------------------------------------------------------------------------
# Job
# --------------------------------------------------------------------------


def project_node_hours(
    *,
    fixed_seconds: float,
    cold_excess_seconds: float,
    warm_seconds_per_epoch: float,
    spice_verify_seconds: float,
    spice_calls: int,
    spice_seconds_per_epoch: float,
    full_epochs: int,
    io_seconds: float,
    pilot_days: int,
    full_days: int,
    production_node_hours: float,
    stage_node_hour_cap: float,
) -> dict:
    """Scale pilot timings to the full period. A projection, not a result.

    Only per-epoch costs scale with epochs: the warm dispatch rate and the
    SPICE time left after kernel verification. Kernel verification is paid
    once per SPICE call (one per year chunk). Start-up and the first
    dispatch's cold excess are paid once. Write and finalize scale by days.
    """
    if min(spice_calls, full_epochs, pilot_days, full_days) <= 0:
        raise ValueError("pilot and full sizes must be positive")
    measured = (
        fixed_seconds,
        cold_excess_seconds,
        warm_seconds_per_epoch,
        spice_verify_seconds,
        spice_seconds_per_epoch,
        io_seconds,
    )
    if min(measured) < 0.0:
        raise ValueError("measured durations must be non-negative")
    projected_epochs = float(warm_seconds_per_epoch) * full_epochs
    projected_spice = (
        float(spice_verify_seconds) * spice_calls
        + float(spice_seconds_per_epoch) * full_epochs
    )
    projected_io = float(io_seconds) * full_days / pilot_days
    once = float(fixed_seconds) + float(cold_excess_seconds)
    wall = once + projected_epochs + projected_spice + projected_io
    hours = wall / 3600.0
    return {
        "projected_fixed_seconds": once,
        "projected_epoch_seconds": projected_epochs,
        "projected_spice_seconds": projected_spice,
        "projected_io_seconds": projected_io,
        "projected_wall_seconds": wall,
        "projected_node_hours": hours,
        "production_node_hours": float(production_node_hours),
        "stage_node_hour_cap": float(stage_node_hour_cap),
        "fits_production_cap": bool(hours <= float(production_node_hours)),
    }


def compare_with_stage_a(
    mean_fraction: np.ndarray, stage_a_percent: np.ndarray, *, rmse_max: float
) -> dict:
    """Full-period mean lit fraction against Stage A percent-time-lit / 100."""
    modeled = np.asarray(mean_fraction, dtype=np.float64)
    reference = np.asarray(stage_a_percent, dtype=np.float64) / 100.0
    both = np.isfinite(modeled) & np.isfinite(reference)
    if not both.any():
        raise ValueError("no common valid pixels with Stage A")
    residual = modeled[both] - reference[both]
    rmse = float(np.sqrt(np.mean(residual**2)))
    return {
        "valid_pixels": int(both.sum()),
        "rmse": rmse,
        "bias": float(residual.mean()),
        "max_abs": float(np.abs(residual).max()),
        "rmse_max": float(rmse_max),
        "within_tolerance": bool(rmse <= float(rmse_max)),
        "fill_matches_stage_a_nan": bool(
            np.array_equal(np.isfinite(modeled), np.isfinite(reference))
        ),
        "note": "different 18.6-year windows (Stage A 2000-2018); not a gate",
    }


def _run_key(
    *, horizon_sha256: str, kernel_manifest: Path, cfg_path: Path, start: date, end: date
) -> dict:
    return {
        "horizon_sha256": horizon_sha256,
        "kernel_manifest_sha256": _sha256(kernel_manifest),
        "daily_lit_config_sha256": _sha256(cfg_path),
        "period_start_day": start.isoformat(),
        "period_end_day_exclusive": end.isoformat(),
        "fill_uint8": FILL_UINT8,
    }


def _prepare_output_dir(output_dir: Path, run_key: dict, *, allow_resume: bool = True) -> bool:
    """Create or resume an output directory. Returns True when resuming.

    The pilot never resumes: its timings must come from one fresh run.
    """
    run_config = output_dir / RUN_CONFIG_NAME
    if run_config.is_file():
        if not allow_resume:
            raise ValueError(f"pilot output directory already exists: {output_dir}")
        if json.loads(run_config.read_text()) != run_key:
            raise ValueError(f"output directory belongs to a different run: {output_dir}")
        return True
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(run_config, run_key)
    return False


def compute_chunks(
    output_dir: Path,
    chunks: Sequence[dict],
    *,
    horizons: np.ndarray,
    longitude: np.ndarray,
    latitude: np.ndarray,
    valid: np.ndarray,
    run_key: dict,
    vectors_for,
    workers: int,
    chunk_days: int,
    disk_radius_deg: float,
    azimuth_origin_deg: float,
    log_path: Path | None = None,
) -> dict:
    """Compute every missing chunk; skip finished ones. Returns timings."""
    timings = {
        "spice_seconds": 0.0,
        "evaluate_seconds": 0.0,
        "write_seconds": 0.0,
        "pool_start_seconds": 0.0,
        "spice_calls": 0,
        "dispatches": [],
        "computed": [],
        "skipped": [],
    }
    pending = []
    for chunk in chunks:
        if completed_chunk(output_dir, chunk, longitude.shape, run_key) is not None:
            timings["skipped"].append(chunk["label"])
            _log_progress(f"daily-lit chunk {chunk['label']} already complete", log_path)
        else:
            pending.append(chunk)
    if not pending:
        return timings
    parallel = workers > 1 and longitude.shape[0] > 1
    started = time.perf_counter()
    pool = _start_tile_pool(horizons, longitude, latitude, workers) if parallel else None
    timings["pool_start_seconds"] = time.perf_counter() - started
    try:
        for chunk in pending:
            first = parse_day(chunk["first_day"])
            count = int(chunk["day_count"])
            started = time.perf_counter()
            vectors = vectors_for(day_epochs(first, count))
            timings["spice_seconds"] += time.perf_counter() - started
            timings["spice_calls"] += 1
            daily = np.empty((count, *longitude.shape), dtype=np.uint8)
            started = time.perf_counter()
            for day0 in range(0, count, chunk_days):
                day1 = min(count, day0 + chunk_days)
                dispatch_started = time.perf_counter()
                daily[day0:day1] = daily_lit_hours(
                    horizons,
                    longitude,
                    latitude,
                    vectors[day0 * HOURS_PER_DAY : day1 * HOURS_PER_DAY],
                    disk_radius_deg=disk_radius_deg,
                    azimuth_origin_deg=azimuth_origin_deg,
                    workers=workers,
                    pool=pool,
                )
                timings["dispatches"].append(
                    {
                        "epochs": (day1 - day0) * HOURS_PER_DAY,
                        "seconds": time.perf_counter() - dispatch_started,
                    }
                )
            daily[:, ~valid] = FILL_UINT8
            timings["evaluate_seconds"] += time.perf_counter() - started
            started = time.perf_counter()
            write_chunk(output_dir, chunk, daily, run_key)
            timings["write_seconds"] += time.perf_counter() - started
            timings["computed"].append(chunk["label"])
            _log_progress(
                f"daily-lit chunk {chunk['label']} ({count} days) written", log_path
            )
    finally:
        if pool is not None:
            pool.close()
            pool.join()
    return timings


def run_daily_lit(
    *,
    mode: str,
    input_manifest: Path,
    kernel_manifest: Path,
    stage_a_dir: Path,
    output_dir: Path,
    workers: int,
    horizon_cube: Path | None = None,
    daily_lit_config: Path | None = None,
) -> dict:
    """Pilot or production run. Refuses before heavy work on any bad pin."""
    from anuma.grid import Grid
    from anuma.hpc_smoke import sha256_file, validate_input_manifest
    from anuma.sun_validation import _require_lon0_zero

    cfg_path = config_path(daily_lit_config).resolve()
    cfg = load_daily_lit_config(cfg_path)
    sun_cfg = load_sun_config()
    start, end = period_days(cfg, mode)
    roles = sun_cfg["sun"]["required_kernel_roles"]
    stage_a_dir = Path(stage_a_dir).resolve()
    horizon_path = Path(horizon_cube or stage_a_dir / "terrain-horizon.npy").resolve()
    pin = stage_a_horizon_pin(stage_a_dir)
    coverage = require_spice_coverage(kernel_manifest, roles, start, end)
    _, files = validate_input_manifest(Path(input_manifest))
    grid = Grid.from_json(files["grid"])
    _require_lon0_zero(grid)

    started_utc = datetime.now(timezone.utc).isoformat()
    clock = time.perf_counter()
    horizon_sha = _sha256(horizon_path)
    if horizon_sha != pin["horizon_sha256"]:
        raise ValueError("horizon cube digest differs from the Stage A manifest")
    output_dir = Path(output_dir).resolve()
    run_key = _run_key(
        horizon_sha256=horizon_sha,
        kernel_manifest=Path(kernel_manifest),
        cfg_path=cfg_path,
        start=start,
        end=end,
    )
    resumed = _prepare_output_dir(output_dir, run_key, allow_resume=mode == "production")
    log_path = output_dir / "progress.log"
    _log_progress(f"daily-lit {mode} {start}..{end} resumed={resumed}", log_path)

    horizons = np.load(horizon_path, allow_pickle=False)
    azimuths = int(sun_cfg["horizon"]["azimuth_count"])
    if horizons.dtype != np.float32 or horizons.shape != (azimuths, grid.height, grid.width):
        raise ValueError(f"horizon cube shape {horizons.shape} does not fit the grid")
    longitude, latitude = grid.lonlat_arrays()
    valid = valid_pixels(horizons, longitude, latitude)
    _atomic_save(output_dir / "valid-mask.npy", valid)
    fixed_seconds = time.perf_counter() - clock

    def vectors_for(epochs: list[str]) -> np.ndarray:
        return sun_vectors_from_spice(
            epochs,
            kernel_manifest,
            required_roles=roles,
            frame=str(sun_cfg["sun"]["frame"]),
            aberration_correction=str(sun_cfg["sun"]["aberration_correction"]),
        )

    chunks = year_chunks(start, end)
    disk = float(sun_cfg["sun"]["disk_radius_deg"])
    origin = float(sun_cfg["horizon"]["azimuth_origin_deg"])
    timings = compute_chunks(
        output_dir,
        chunks,
        horizons=horizons,
        longitude=longitude,
        latitude=latitude,
        valid=valid,
        run_key=run_key,
        vectors_for=vectors_for,
        workers=workers,
        chunk_days=int(cfg["execution"]["chunk_days"]),
        disk_radius_deg=disk,
        azimuth_origin_deg=origin,
        log_path=log_path,
    )
    clock = time.perf_counter()
    boundaries, checks = build_cumulative(output_dir, start, end, chunks, valid)
    write_index(output_dir, start, end, valid.shape, chunks, boundaries)
    timings["finalize_seconds"] = time.perf_counter() - clock
    timings["fixed_seconds"] = fixed_seconds

    cube = DailyLitCube(output_dir)
    stage_a_percent = np.load(stage_a_dir / "percent-time-lit.npy", mmap_mode="r")
    checks["fill_matches_stage_a_nan"] = bool(
        np.array_equal(~valid, ~np.isfinite(np.asarray(stage_a_percent)))
    )

    report: dict = {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "mode": mode,
        "kind": "modeled scenario layer, not a measurement or mission claim",
        "started_at_utc": started_utc,
        "completed_at_utc": None,
        "period": {
            "start_day_utc": start.isoformat(),
            "end_day_exclusive_utc": end.isoformat(),
            "day_count": (end - start).days,
            "epoch_count": (end - start).days * HOURS_PER_DAY,
            "label": cfg["period"]["label"] if mode == "production" else "pilot sample",
        },
        "resumed": resumed,
        "timings_seconds": timings,
        "checks": checks,
        "spice_coverage": coverage,
        "inputs": {
            **pin,
            "horizon_cube": str(horizon_path),
            "input_manifest": {
                "path": str(Path(input_manifest).resolve()),
                "sha256": sha256_file(Path(input_manifest).resolve()),
            },
            "kernel_manifest": {
                "path": str(Path(kernel_manifest).resolve()),
                "sha256": run_key["kernel_manifest_sha256"],
            },
            "daily_lit_config": {"path": str(cfg_path), "sha256": run_key["daily_lit_config_sha256"]},
        },
        "software": {"python": platform.python_version(), "numpy": np.__version__},
        "execution": {"workers": workers, "chunk_days": int(cfg["execution"]["chunk_days"])},
    }
    if mode == "pilot":
        report["equivalence"] = _pilot_equivalence(
            cube,
            start,
            end,
            horizons,
            longitude,
            latitude,
            vectors_for(day_epochs(start, (end - start).days)),
            valid,
            stride=int(cfg["pilot"]["check_row_stride"]),
            disk_radius_deg=disk,
            azimuth_origin_deg=origin,
        )
        pilot_epochs = (end - start).days * HOURS_PER_DAY
        full_start, full_end = period_days(cfg, "production")
        full_days = (full_end - full_start).days
        dispatches = timings["dispatches"]
        if len(dispatches) < 2:
            raise ValueError("pilot needs two dispatches to time a warm one")
        warm = dispatches[-1]["seconds"] / dispatches[-1]["epochs"]
        cold_excess = max(0.0, dispatches[0]["seconds"] - warm * dispatches[0]["epochs"])
        clock = time.perf_counter()
        verify_kernel_manifest(kernel_manifest, roles)
        verify_seconds = time.perf_counter() - clock
        spice_per_epoch = (
            max(0.0, timings["spice_seconds"] - verify_seconds * timings["spice_calls"])
            / pilot_epochs
        )
        report["projection"] = project_node_hours(
            fixed_seconds=fixed_seconds + timings["pool_start_seconds"],
            cold_excess_seconds=cold_excess,
            warm_seconds_per_epoch=warm,
            spice_verify_seconds=verify_seconds,
            spice_calls=len(year_chunks(full_start, full_end)),
            spice_seconds_per_epoch=spice_per_epoch,
            full_epochs=full_days * HOURS_PER_DAY,
            io_seconds=timings["write_seconds"] + timings["finalize_seconds"],
            pilot_days=(end - start).days,
            full_days=full_days,
            production_node_hours=float(cfg["execution"]["production_node_hours"]),
            stage_node_hour_cap=float(cfg["execution"]["stage_node_hour_cap"]),
        )
        report["projection"]["method"] = (
            "warm second-dispatch s/epoch x full epochs; kernel verification "
            "x production SPICE calls; remaining SPICE s/epoch x full epochs; "
            "start-up and first-dispatch excess once; I/O x days"
        )
        report["stage_a_reference"] = {
            "job": cfg["stage_a"]["job_id"],
            "seconds_per_epoch": float(cfg["stage_a"]["seconds_per_epoch"]),
            "pilot_warm_seconds_per_epoch": warm,
            "pilot_mean_seconds_per_epoch": timings["evaluate_seconds"] / pilot_epochs,
        }
        report["passed"] = bool(
            checks["lit_hours_within_0_24"]
            and checks["fill_matches_valid_mask"]
            and checks["fill_matches_stage_a_nan"]
            and report["equivalence"]["identical"]
            and report["projection"]["fits_production_cap"]
        )
    else:
        mean = np.load(output_dir / "mean-lit-fraction.npy", allow_pickle=False)
        report["stage_a_comparison"] = compare_with_stage_a(
            mean,
            np.asarray(stage_a_percent),
            rmse_max=float(cfg["acceptance"]["stage_a_mean_rmse_max"]),
        )
        report["passed"] = bool(
            checks["lit_hours_within_0_24"]
            and checks["fill_matches_valid_mask"]
            and checks["fill_matches_stage_a_nan"]
            and report["stage_a_comparison"]["within_tolerance"]
        )
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    name = "pilot-measurement.json" if mode == "pilot" else "daily-lit-report.json"
    report_path = output_dir / name
    _atomic_json(report_path, report)
    _write_output_manifest(output_dir, report, report_path)
    return report


def _pilot_equivalence(
    cube: DailyLitCube,
    start: date,
    end: date,
    horizons: np.ndarray,
    longitude: np.ndarray,
    latitude: np.ndarray,
    vectors: np.ndarray,
    valid: np.ndarray,
    *,
    stride: int,
    disk_radius_deg: float,
    azimuth_origin_deg: float,
) -> dict:
    """Sampled rows: summed daily hours must equal the Stage A accumulator."""
    rows = slice(0, longitude.shape[0], stride)
    shape = longitude[rows].shape
    total, _, _ = _advance_sunlight_block(
        np.ascontiguousarray(horizons[:, rows, :]),
        np.ascontiguousarray(longitude[rows]),
        np.ascontiguousarray(latitude[rows]),
        vectors,
        np.zeros(shape),
        np.zeros(shape, dtype=np.int64),
        np.zeros(shape, dtype=np.int64),
        disk_radius_deg=disk_radius_deg,
        azimuth_origin_deg=azimuth_origin_deg,
    )
    summed = cube.lit_hours_sum(start, end, (rows, slice(None)))
    sample_valid = valid[rows]
    identical = bool(np.array_equal(summed[sample_valid], total[sample_valid].astype(np.int64)))
    return {
        "row_stride": stride,
        "pixels": int(sample_valid.sum()),
        "identical": identical,
        "reference": "anuma.sun._advance_sunlight_block (Stage A accumulator)",
    }


def _write_output_manifest(output_dir: Path, report: dict, report_path: Path) -> None:
    outputs = {}
    for record_path in sorted((output_dir / "daily").glob("lit-hours-*.json")):
        record = json.loads(record_path.read_text())
        outputs[record["file"]] = {"sha256": record["sha256"], "bytes": record["bytes"]}
    for name in (
        "cumulative-monthly.npy",
        "mean-lit-fraction.npy",
        "valid-mask.npy",
        INDEX_NAME,
        report_path.name,
    ):
        path = output_dir / name
        outputs[name] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    _atomic_json(output_dir / "output-manifest.json", {**report, "outputs": outputs})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("pilot", "production", "coverage"), required=True)
    parser.add_argument("--input-manifest", type=Path)
    parser.add_argument("--kernel-manifest", type=Path, default=None)
    parser.add_argument("--stage-a-dir", type=Path)
    parser.add_argument("--horizon-cube", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    kernel_manifest = args.kernel_manifest or configured_kernel_manifest()
    try:
        if args.mode == "coverage":
            cfg = load_daily_lit_config(args.config)
            start, end = period_days(cfg, "production")
            report = spice_coverage(
                kernel_manifest,
                load_sun_config()["sun"]["required_kernel_roles"],
                (day_epochs(start, 1)[0], day_epochs(end - timedelta(days=1), 1)[-1]),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            if not report.get("covers_window"):
                raise SystemExit(5)
            return
        for name in ("input_manifest", "stage_a_dir", "output_dir"):
            if getattr(args, name) is None:
                parser.error(f"--{name.replace('_', '-')} is required for {args.mode}")
        report = run_daily_lit(
            mode=args.mode,
            input_manifest=args.input_manifest,
            kernel_manifest=kernel_manifest,
            stage_a_dir=args.stage_a_dir,
            output_dir=args.output_dir,
            workers=args.workers,
            horizon_cube=args.horizon_cube,
            daily_lit_config=args.config,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f"daily lit precondition failed: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.mode == "pilot" and not report["projection"]["fits_production_cap"]:
        raise SystemExit(4)
    if not report["passed"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
