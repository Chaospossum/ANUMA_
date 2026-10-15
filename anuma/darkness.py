"""Stage G: longest continuous dark and lit periods per 240 m pixel.

Stage E stored only lit hours per UTC day, which cannot give the exact
longest dark run (a run can start and end inside days). Stage G replays the
same hourly epochs through the same lit rule, horizon cube and SPICE path and
keeps, per pixel, a streaming run-length state of O(1) size:

- the longest dark run and the longest lit run over the whole period (hours),
  the hour index where each starts, censoring flags, and the longest run that
  touches neither end of the period (the "interior" run);
- per calendar month, three dark-run numbers: ``lead`` (dark hours from the
  month start to the first lit hour), ``trail`` (dark hours from the last lit
  hour to the month end) and ``best`` (longest dark run inside the month).

The month triples form a monoid, so the longest dark run of any month-aligned
window is computed exactly by stitching (``combine``). Calendar-year triples
are written as well, stitched from the months, so a window reads at most 11 +
19 + 11 blocks.

Hour index ``h`` is the epoch ``period start + h hours`` (2026-01-01T00:00Z is
0). A dark run of ``N`` hours is ``N`` consecutive unlit hourly samples; the
physical darkness lasted between N - 1 and N + 1 hours. Ties go to the
earliest run. Runs are clipped to the period (or to the queried window); a
run touching an edge is censored and its true length may be longer.

This is a modeled scenario layer, not a measurement and not a mission claim.
"""

from __future__ import annotations

import argparse
import json
import mmap
import os
import platform
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import yaml

import anuma.sun as _sun
from anuma import daily_lit as dl
from anuma.config import repo_root
from anuma.sun import (
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

STAGE = "G-darkness-240m"
SCHEMA_VERSION = 1
HOURS_PER_DAY = 24
FILL_UINT32 = np.iinfo(np.uint32).max
FILL_UINT16 = np.iinfo(np.uint16).max
FILL_FLAGS = 255
# A start hour for a pixel that never had a run of that kind (hours == 0).
NO_RUN = FILL_UINT32
CENSORED_AT_START = 1
CENSORED_AT_END = 2
INDEX_NAME = "darkness-index.json"
RUN_CONFIG_NAME = "run-config.json"
KINDS = ("dark", "lit")
MONTH_FIELDS = ("lead", "trail", "best")
STATE_FIELDS = (
    ("run_lit", np.bool_),
    ("run_start", np.uint32),
    ("best_dark", np.uint32),
    ("best_dark_start", np.uint32),
    ("best_lit", np.uint32),
    ("best_lit_start", np.uint32),
    ("interior_dark", np.uint32),
    ("interior_lit", np.uint32),
    ("lit_total", np.uint32),
)
PERIOD_FILES = {
    f"{kind}_{field}": f"longest-{kind}-{suffix}.npy"
    for kind in KINDS
    for field, suffix in (
        ("hours", "hours"),
        ("start", "start-hour"),
        ("flags", "censored"),
        ("interior", "interior-hours"),
    )
}


# --------------------------------------------------------------------------
# Streaming run-length kernel (flat arrays, vectorised over pixels)
# --------------------------------------------------------------------------


def new_run_state(shape, alloc=None) -> dict[str, np.ndarray]:
    """Fresh per-pixel state. ``alloc(shape, dtype)`` must return zeros."""
    alloc = alloc or (lambda size, dtype: np.zeros(size, dtype=dtype))
    state = {name: alloc(shape, dtype) for name, dtype in STATE_FIELDS}
    state["best_dark_start"][...] = NO_RUN
    state["best_lit_start"][...] = NO_RUN
    return state


def begin_month(size: int) -> dict[str, np.ndarray]:
    """Month accumulators. ``lead_open``: no lit hour seen yet this month."""
    return {
        "lead": np.zeros(size, dtype=np.uint32),
        "lead_open": np.ones(size, dtype=bool),
        "best": np.zeros(size, dtype=np.uint32),
    }


def _close_runs(
    state: dict,
    month: dict | None,
    idx: np.ndarray,
    hour: int,
    month_start: int,
    *,
    final: bool = False,
) -> None:
    """Close the runs of pixels ``idx`` that end just before ``hour``.

    A closed run is [run_start, hour). With ``final`` the run touches the
    period end, so it may set the longest run but never the interior one.
    """
    start = state["run_start"][idx].astype(np.int64)
    length = np.int64(hour) - start
    was_lit = state["run_lit"][idx]
    for kind, chosen in (("dark", ~was_lit), ("lit", was_lit)):
        if not chosen.any():
            continue
        pixels, lengths, starts = idx[chosen], length[chosen], start[chosen]
        best = state[f"best_{kind}"]
        better = lengths > best[pixels]
        if better.any():
            best[pixels[better]] = lengths[better]
            state[f"best_{kind}_start"][pixels[better]] = starts[better]
        if not final:
            inner = starts > 0
            if inner.any():
                interior = state[f"interior_{kind}"]
                inner_pixels = pixels[inner]
                interior[inner_pixels] = np.maximum(interior[inner_pixels], lengths[inner])
    if month is not None:
        dark = ~was_lit
        if dark.any():
            pixels = idx[dark]
            clipped = np.int64(hour) - np.maximum(start[dark], np.int64(month_start))
            month["best"][pixels] = np.maximum(month["best"][pixels], clipped)
            still_open = month["lead_open"][pixels]
            if still_open.any():
                # lead_open means the dark run began at or before the month
                # start, so its clipped length is the month's leading darkness.
                month["lead"][pixels[still_open]] = clipped[still_open]
                month["lead_open"][pixels[still_open]] = False


def step(state: dict, month: dict | None, lit: np.ndarray, hour: int, month_start: int) -> None:
    """Advance flat state by one hourly sample ``lit`` (bool) at ``hour``."""
    run_lit = state["run_lit"]
    if hour == 0:
        run_lit[:] = lit
        state["run_start"][:] = 0
    else:
        changed = np.flatnonzero(lit != run_lit)
        if changed.size:
            _close_runs(state, month, changed, hour, month_start)
            run_lit[changed] = lit[changed]
            state["run_start"][changed] = hour
    if month is not None and hour == month_start:
        lit_now = lit & month["lead_open"]
        month["lead"][lit_now] = 0
        month["lead_open"][lit_now] = False


def end_month(
    state: dict, month: dict, month_start: int, month_end: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(lead, trail, best) dark hours of the month [month_start, month_end)."""
    hours = month_end - month_start
    if not 0 < hours < FILL_UINT16:
        raise ValueError("month length must fit uint16 below the fill value")
    dark_open = ~state["run_lit"]
    trail = np.zeros(dark_open.shape, dtype=np.int64)
    trail[dark_open] = np.int64(month_end) - np.maximum(
        state["run_start"][dark_open].astype(np.int64), np.int64(month_start)
    )
    best = np.maximum(month["best"].astype(np.int64), trail)
    lead = np.where(month["lead_open"], hours, month["lead"].astype(np.int64))
    return lead.astype(np.uint16), trail.astype(np.uint16), best.astype(np.uint16)


def finalize_runs(state: dict, total_hours: int) -> dict[str, np.ndarray]:
    """Close every open run at the period end; return the period maps (flat).

    Does not modify ``state``. Flags: bit 1 the longest run starts at hour 0,
    bit 2 it ends at the period end. Pixels with no run of a kind have hours
    0, start NO_RUN and flags 0.
    """
    closed = {name: np.array(values, copy=True) for name, values in state.items()}
    size = closed["run_lit"].size
    if size:
        _close_runs(closed, None, np.arange(size), total_hours, 0, final=True)
    out: dict[str, np.ndarray] = {}
    for kind in KINDS:
        hours = closed[f"best_{kind}"]
        start = closed[f"best_{kind}_start"]
        has = hours > 0
        flags = np.zeros(size, dtype=np.uint8)
        flags[has & (start == 0)] |= CENSORED_AT_START
        ends = start.astype(np.int64) + hours.astype(np.int64)
        flags[has & (ends == total_hours)] |= CENSORED_AT_END
        out[f"{kind}_hours"] = hours
        out[f"{kind}_start"] = start
        out[f"{kind}_flags"] = flags
        out[f"{kind}_interior"] = closed[f"interior_{kind}"]
    out["lit_total"] = closed["lit_total"]
    return out


def runs_from_lit(
    lit: np.ndarray, month_hours: Sequence[int]
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Drive the streaming kernel from a (hours, pixels) bool matrix.

    Returns the period maps and the month triples, shape (months, 3, pixels)
    ordered lead, trail, best. The reference for tests and the pilot.
    """
    lit = np.asarray(lit, dtype=bool)
    if lit.ndim != 2 or lit.shape[0] != sum(month_hours):
        raise ValueError("lit must be (sum(month_hours), pixels)")
    size = lit.shape[1]
    state = new_run_state(size)
    months = np.zeros((len(month_hours), 3, size), dtype=np.uint16)
    hour = 0
    for k, hours in enumerate(month_hours):
        month = begin_month(size)
        first = hour
        for _ in range(hours):
            step(state, month, lit[hour], hour, first)
            hour += 1
        months[k] = end_month(state, month, first, hour)
        state["lit_total"] += lit[first:hour].sum(axis=0, dtype=np.uint32)
    return finalize_runs(state, hour), months


# --------------------------------------------------------------------------
# Brute-force reference (a different algorithm: consecutive-count scan)
# --------------------------------------------------------------------------


def brute_force_runs(lit: np.ndarray) -> dict[str, np.ndarray]:
    """Period maps from a (hours, pixels) bool matrix by consecutive counts."""
    lit = np.asarray(lit, dtype=bool)
    hours, size = lit.shape
    out: dict[str, np.ndarray] = {}
    for kind, member in (("dark", ~lit), ("lit", lit)):
        count = np.zeros(size, dtype=np.int64)
        best = np.zeros(size, dtype=np.int64)
        best_end = np.full(size, -1, dtype=np.int64)
        interior = np.zeros(size, dtype=np.int64)
        for t in range(hours):
            count = np.where(member[t], count + 1, 0)
            better = count > best
            best = np.where(better, count, best)
            best_end = np.where(better, t, best_end)
            if t + 1 < hours:
                run_ends = member[t] & ~member[t + 1]
                inner = run_ends & (count != t + 1)
                interior = np.where(inner, np.maximum(interior, count), interior)
        start = np.where(best > 0, best_end - best + 1, int(NO_RUN))
        flags = np.zeros(size, dtype=np.uint8)
        flags[(best > 0) & (start == 0)] |= CENSORED_AT_START
        flags[(best > 0) & (best_end == hours - 1)] |= CENSORED_AT_END
        out[f"{kind}_hours"] = best
        out[f"{kind}_start"] = start
        out[f"{kind}_flags"] = flags
        out[f"{kind}_interior"] = interior
    out["lit_total"] = lit.sum(axis=0, dtype=np.int64)
    return out


def brute_force_longest_dark(lit: np.ndarray) -> np.ndarray:
    """Longest dark run inside a (hours, pixels) window, runs clipped to it."""
    lit = np.asarray(lit, dtype=bool)
    count = np.zeros(lit.shape[1], dtype=np.int64)
    best = np.zeros(lit.shape[1], dtype=np.int64)
    for row in lit:
        count = np.where(row, 0, count + 1)
        np.maximum(best, count, out=best)
    return best


# --------------------------------------------------------------------------
# Month-window stitching
# --------------------------------------------------------------------------


def combine(a: tuple, b: tuple) -> tuple:
    """Concatenate two adjacent blocks (hours, lead, trail, best); a before b.

    ``hours`` is the block length (a scalar); the others are per-pixel dark
    hours. A block is all dark exactly where lead == hours. Associative, so
    any grouping of adjacent blocks gives the same result.
    """
    hours_a, lead_a, trail_a, best_a = a
    hours_b, lead_b, trail_b, best_b = b
    lead = np.where(lead_a == hours_a, hours_a + lead_b, lead_a)
    trail = np.where(trail_b == hours_b, hours_b + trail_a, trail_b)
    best = np.maximum(np.maximum(best_a, best_b), trail_a + lead_b)
    return hours_a + hours_b, lead, trail, best


def stitch(blocks: Iterable[tuple]) -> tuple:
    """Fold ordered blocks (hours, lead, trail, best) with ``combine``."""
    total = None
    for hours, lead, trail, best in blocks:
        block = (
            int(hours),
            np.asarray(lead, dtype=np.int64),
            np.asarray(trail, dtype=np.int64),
            np.asarray(best, dtype=np.int64),
        )
        total = block if total is None else combine(total, block)
    if total is None:
        raise ValueError("cannot stitch an empty window")
    return total


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------


def month_table(start: date, end: date) -> list[dict]:
    """Months of [start, end) with label, first day, day and hour counts."""
    bounds = dl.month_boundaries(start, end)
    table = []
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        table.append(
            {
                "label": f"{lo.year:04d}-{lo.month:02d}",
                "first_day": lo.isoformat(),
                "days": (hi - lo).days,
                "first_hour": (lo - start).days * HOURS_PER_DAY,
                "hours": (hi - lo).days * HOURS_PER_DAY,
            }
        )
    return table


def year_table(months: Sequence[dict]) -> list[dict]:
    """Calendar-year blocks as runs of consecutive months."""
    years: list[dict] = []
    for index, month in enumerate(months):
        label = month["label"][:4]
        if years and years[-1]["label"] == label:
            years[-1]["month_count"] += 1
            years[-1]["hours"] += month["hours"]
        else:
            years.append(
                {"label": label, "first_month": index, "month_count": 1, "hours": month["hours"]}
            )
    return years


def parse_month(value: object) -> date:
    """A month boundary from 'YYYY-MM', a first-of-month day, or a date."""
    if isinstance(value, str) and len(value) == 7:
        value = f"{value}-01"
    day = dl.parse_day(value)
    if day.day != 1:
        raise ValueError(f"month windows start and end on the 1st: {value!r}")
    return day


# --------------------------------------------------------------------------
# Reader
# --------------------------------------------------------------------------


class DarknessCube:
    """Read-only access to a finished Stage G output directory.

    Month files are opened on first use: a copy that holds only the period
    and yearly files still answers year-aligned windows.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.index = json.loads((self.root / INDEX_NAME).read_text())
        if self.index.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported darkness index schema")
        self.start = dl.parse_day(self.index["period"]["start_day_utc"])
        self.end = dl.parse_day(self.index["period"]["end_day_exclusive_utc"])
        self.total_hours = int(self.index["period"]["hours"])
        self.months = self.index["months"]
        self.years = self.index["years"]
        self.valid = np.load(self.root / self.index["valid_mask"], allow_pickle=False)
        self._month_starts = [parse_month(m["first_day"]) for m in self.months] + [self.end]
        self._opened: dict[str, np.ndarray] = {}

    def _array(self, name: str) -> np.ndarray:
        if name not in self._opened:
            path = self.root / name
            if not path.exists():
                raise FileNotFoundError(
                    f"{name} is not in {self.root}; copy it or use a window on "
                    "calendar-year boundaries"
                )
            self._opened[name] = np.load(path, mmap_mode="r", allow_pickle=False)
        return self._opened[name]

    def period(self, name: str, window: tuple[slice, slice] | None = None) -> np.ndarray:
        """A full-period map by key, e.g. 'dark_hours', 'lit_flags'."""
        rows, cols = window or (slice(None), slice(None))
        return np.asarray(self._array(self.index["period_files"][name])[rows, cols])

    def _month_range(self, start: object, end: object) -> tuple[int, int]:
        first, last = parse_month(start), parse_month(end)
        if first not in self._month_starts or last not in self._month_starts or last <= first:
            raise ValueError(
                f"window [{first}, {last}) is not a month range inside [{self.start}, {self.end})"
            )
        return self._month_starts.index(first), self._month_starts.index(last)

    def _blocks(self, a: int, b: int, rows, cols):
        year_at = {year["first_month"]: year for year in self.years}
        k = a
        while k < b:
            year = year_at.get(k)
            if year is not None and k + year["month_count"] <= b:
                j = self.years.index(year)
                files = self.index["yearly"]
                yield (
                    year["hours"],
                    *(np.asarray(self._array(files[f])[j, rows, cols]) for f in MONTH_FIELDS),
                )
                k += year["month_count"]
            else:
                files = self.index["monthly"]
                yield (
                    self.months[k]["hours"],
                    *(np.asarray(self._array(files[f])[k, rows, cols]) for f in MONTH_FIELDS),
                )
                k += 1

    def window_summary(
        self, start: object, end: object, window: tuple[slice, slice] | None = None
    ) -> dict:
        """Stitched dark-run summary of the month window [start, end).

        ``edge_censored`` marks pixels whose longest run touches a window edge
        (it may continue outside the window).
        """
        a, b = self._month_range(start, end)
        rows, cols = window or (slice(None), slice(None))
        valid = self.valid[rows, cols]
        if (a, b) == (0, len(self.months)):
            best = self.period("dark_hours", window).astype(np.int64)
            flags = self.period("dark_flags", window)
            censored = (flags != 0) & (flags != FILL_FLAGS) & (best > 0)
            hours = self.total_hours
            lead = trail = None
        else:
            hours, lead, trail, best = stitch(self._blocks(a, b, rows, cols))
            censored = (best > 0) & ((best == lead) | (best == trail))
        best = np.where(valid, best, -1)
        return {
            "hours": int(hours),
            "longest_dark_hours": best,
            "edge_censored": censored & valid,
            "lead": None if lead is None else np.where(valid, lead, -1),
            "trail": None if trail is None else np.where(valid, trail, -1),
        }

    def longest_dark_hours(
        self, start_month: object, end_month: object, window: tuple[slice, slice] | None = None
    ) -> np.ndarray:
        """int64 longest dark run (hours) in months [start, end); -1 out of cap.

        ``start_month`` and ``end_month`` are 'YYYY-MM' (or the 1st of a
        month); ``end_month`` is exclusive. Runs are clipped to the window.
        """
        return self.window_summary(start_month, end_month, window)["longest_dark_hours"]


# --------------------------------------------------------------------------
# Lit evaluation on row tiles with shared state
# --------------------------------------------------------------------------

_DARK_SHARED: dict | None = None


def _shared_zeros(shape, dtype) -> np.ndarray:
    """Zeroed array in a MAP_SHARED anonymous mapping, inherited by forks."""
    count = int(np.prod(shape))
    nbytes = max(1, count * np.dtype(dtype).itemsize)
    buffer = mmap.mmap(-1, nbytes)
    return np.frombuffer(buffer, dtype=dtype, count=count).reshape(shape)


def _month_block(
    horizons: np.ndarray,
    longitude: np.ndarray,
    latitude: np.ndarray,
    vectors: np.ndarray,
    state: dict,
    month_start: int,
    month_out: np.ndarray,
    daily_out: np.ndarray,
    *,
    disk_radius_deg: float,
    azimuth_origin_deg: float,
) -> None:
    """One month on one tile. state/out arrays are flat views, written in place."""
    if len(vectors) % HOURS_PER_DAY:
        raise ValueError("a month block needs whole days of hourly epochs")
    frames = _local_frames(longitude, latitude)
    size = longitude.size
    month = begin_month(size)
    days = len(vectors) // HOURS_PER_DAY
    daily = daily_out[:days]
    daily[...] = 0
    for index, vector in enumerate(vectors):
        lit = _lit_mask(
            vector,
            horizons,
            longitude,
            frames,
            disk_radius_deg=disk_radius_deg,
            azimuth_origin_deg=azimuth_origin_deg,
        ).reshape(-1)
        step(state, month, lit, month_start + index, month_start)
        daily[index // HOURS_PER_DAY] += lit
    month_out[...] = end_month(state, month, month_start, month_start + len(vectors))
    state["lit_total"] += daily.sum(axis=0, dtype=np.uint32)


def _flat_rows(array: np.ndarray, rows: slice) -> np.ndarray:
    """A flat writable view of rows of a C-contiguous (..., H, W) array."""
    part = array[..., rows, :]
    lead = part.shape[:-2]
    view = part.reshape(*lead, -1)
    if not np.shares_memory(view, array):
        raise RuntimeError("row view is not a view")
    return view


def _darkness_tile(task: tuple) -> tuple[int, int]:
    row0, row1, vectors, month_start, disk_radius_deg, azimuth_origin_deg = task
    shared, dark = _sun._TILE_SHARED, _DARK_SHARED
    if shared is None or dark is None:
        raise RuntimeError("darkness worker started without shared arrays")
    rows = slice(row0, row1)
    _month_block(
        np.ascontiguousarray(shared["horizons"][:, rows, :]),
        np.ascontiguousarray(shared["longitude"][rows, :]),
        np.ascontiguousarray(shared["latitude"][rows, :]),
        vectors,
        {name: _flat_rows(values, rows) for name, values in dark["state"].items()},
        month_start,
        _flat_rows(dark["month_out"], rows),
        _flat_rows(dark["daily_out"], rows),
        disk_radius_deg=disk_radius_deg,
        azimuth_origin_deg=azimuth_origin_deg,
    )
    return row0, row1


class DarknessEngine:
    """Streams whole months of hourly Sun vectors over the grid.

    workers=1 is the serial reference. With more workers the state lives in
    shared anonymous memory, each fork worker updates only its row tile, and
    the result is bit-identical to the serial path.
    """

    def __init__(
        self,
        horizons: np.ndarray,
        longitude: np.ndarray,
        latitude: np.ndarray,
        *,
        workers: int,
        disk_radius_deg: float,
        azimuth_origin_deg: float,
    ):
        if workers <= 0 or workers > 128:
            raise ValueError("workers must be between 1 and 128")
        self.shape = longitude.shape
        self.horizons, self.longitude, self.latitude = horizons, longitude, latitude
        self.disk = float(disk_radius_deg)
        self.origin = float(azimuth_origin_deg)
        self.parallel = workers > 1 and self.shape[0] > 1
        alloc = _shared_zeros if self.parallel else (lambda s, d: np.zeros(s, dtype=d))
        self.state = new_run_state(self.shape, alloc)
        self.month_out = alloc((3, *self.shape), np.uint16)
        self.daily_out = alloc((31, *self.shape), np.uint8)
        self.hour = 0
        self.pool = None
        self.blocks = [(0, self.shape[0])]
        if self.parallel:
            global _DARK_SHARED
            _DARK_SHARED = {
                "state": self.state,
                "month_out": self.month_out,
                "daily_out": self.daily_out,
            }
            self.pool = _start_tile_pool(horizons, longitude, latitude, workers)
            self.blocks = _row_blocks(self.shape[0], workers)

    def advance_month(self, vectors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Evaluate one month. Returns (lead/trail/best (3,H,W), daily (days,H,W))."""
        vectors = np.ascontiguousarray(vectors, dtype=np.float64)
        if vectors.ndim != 2 or vectors.shape[1] != 3 or len(vectors) % HOURS_PER_DAY:
            raise ValueError("vectors must be whole days of (epochs, 3)")
        if self.pool is not None:
            tasks = [
                (row0, row1, vectors, self.hour, self.disk, self.origin)
                for row0, row1 in self.blocks
            ]
            for _ in self.pool.imap_unordered(_darkness_tile, tasks):
                pass
        else:
            everything = slice(None)
            _month_block(
                self.horizons,
                self.longitude,
                self.latitude,
                vectors,
                {name: _flat_rows(values, everything) for name, values in self.state.items()},
                self.hour,
                _flat_rows(self.month_out, everything),
                _flat_rows(self.daily_out, everything),
                disk_radius_deg=self.disk,
                azimuth_origin_deg=self.origin,
            )
        self.hour += len(vectors)
        days = len(vectors) // HOURS_PER_DAY
        return np.array(self.month_out, copy=True), np.array(self.daily_out[:days], copy=True)

    def finish(self) -> dict[str, np.ndarray]:
        flat = {name: values.reshape(-1) for name, values in self.state.items()}
        return {name: values.reshape(self.shape) for name, values in finalize_runs(flat, self.hour).items()}

    def close(self) -> None:
        global _DARK_SHARED
        if self.pool is not None:
            self.pool.close()
            self.pool.join()
            self.pool = None
        _DARK_SHARED = None


def lit_matrix(
    horizons: np.ndarray,
    longitude: np.ndarray,
    latitude: np.ndarray,
    vectors: np.ndarray,
    *,
    disk_radius_deg: float,
    azimuth_origin_deg: float,
) -> np.ndarray:
    """(epochs, pixels) lit samples straight from the lit rule; a reference."""
    frames = _local_frames(longitude, latitude)
    out = np.zeros((len(vectors), longitude.size), dtype=bool)
    for index, vector in enumerate(vectors):
        out[index] = _lit_mask(
            vector,
            horizons,
            longitude,
            frames,
            disk_radius_deg=disk_radius_deg,
            azimuth_origin_deg=azimuth_origin_deg,
        ).reshape(-1)
    return out


# --------------------------------------------------------------------------
# Configuration, Stage E pin, receipts
# --------------------------------------------------------------------------


def config_path(path: Path | None = None) -> Path:
    return Path(path) if path else repo_root() / "config" / "darkness.yaml"


def sun_config_path() -> Path:
    """config/sun.yaml: disc radius, azimuth origin, frame, kernel roles."""
    return repo_root() / "config" / "sun.yaml"


def load_darkness_config(path: Path | None = None) -> dict:
    cfg = yaml.safe_load(config_path(path).read_text())
    if not isinstance(cfg, dict) or cfg.get("stage") != STAGE:
        raise ValueError(f"darkness config must declare stage {STAGE}")
    output = cfg["output"]
    if int(output["hours_fill_uint32"]) != int(FILL_UINT32):
        raise ValueError("hours_fill_uint32 must stay 4294967295")
    if int(output["month_fill_uint16"]) != int(FILL_UINT16):
        raise ValueError("month_fill_uint16 must stay 65535")
    if int(output["flags_fill_uint8"]) != FILL_FLAGS:
        raise ValueError("flags_fill_uint8 must stay 255")
    if cfg["acceptance"].get("tune_after_run") is not False:
        raise ValueError("acceptance.tune_after_run must stay false")
    if int(cfg["pilot"]["months"]) < 2:
        raise ValueError("pilot must span two monthly dispatches so one is timed warm")
    exe = cfg["execution"]
    if float(exe["projection_limit_node_hours"]) > float(exe["production_node_hours"]):
        raise ValueError("projection limit must not exceed the production cap")
    if float(exe["pilot_node_hours"]) + float(exe["production_node_hours"]) > float(
        exe["stage_node_hour_cap"]
    ):
        raise ValueError("pilot plus production exceeds the stage cap")
    return cfg


def period_days(cfg: dict, daily_cfg: dict, mode: str) -> tuple[date, date]:
    """[start, end) days: the Stage E period, or the pilot's whole months."""
    if mode == "production":
        return dl.period_days(daily_cfg, "production")
    if mode != "pilot":
        raise ValueError(f"unknown mode {mode!r}")
    start = dl.parse_day(cfg["pilot"]["start_utc"])
    if start.day != 1:
        raise ValueError("pilot must start on the 1st of a month")
    end = start
    for _ in range(int(cfg["pilot"]["months"])):
        end = date(end.year + (end.month == 12), end.month % 12 + 1, 1)
    full_start, full_end = dl.period_days(daily_cfg, "production")
    if not full_start <= start < end <= full_end:
        raise ValueError("pilot months must lie inside the Stage E period")
    return start, end


def stage_e_pin(stage_e_dir: Path) -> dict:
    """Stage E record: passed report, run key and output digests (no hashing of data)."""
    stage_e_dir = Path(stage_e_dir).resolve()
    report = json.loads((stage_e_dir / "daily-lit-report.json").read_text())
    if report.get("mode") != "production" or report.get("passed") is not True:
        raise ValueError("Stage E production report has not passed")
    run_key = json.loads((stage_e_dir / dl.RUN_CONFIG_NAME).read_text())
    manifest_path = stage_e_dir / "output-manifest.json"
    outputs = json.loads(manifest_path.read_text()).get("outputs") or {}
    index = json.loads((stage_e_dir / dl.INDEX_NAME).read_text())
    files = ["cumulative-monthly.npy", *[item["file"] for item in index["chunks"]]]
    digests = {}
    for name in files:
        entry = outputs.get(name)
        if not isinstance(entry, dict) or len(str(entry.get("sha256", ""))) != 64:
            raise ValueError(f"Stage E output manifest has no digest for {name}")
        digests[name] = {"sha256": str(entry["sha256"]).lower(), "bytes": int(entry["bytes"])}
    return {
        "stage_e_dir": str(stage_e_dir),
        "stage_e_manifest_sha256": dl._sha256(manifest_path),
        "stage_e_run_key": run_key,
        "stage_e_period": [
            index["period"]["start_day_utc"],
            index["period"]["end_day_exclusive_utc"],
        ],
        "stage_e_digests": digests,
    }


def _require_stage_e_matches(
    stage_e: dict, *, horizon_sha256: str, kernel_manifest: Path, daily_cfg_path: Path
) -> None:
    key = stage_e["stage_e_run_key"]
    if key.get("horizon_sha256") != horizon_sha256:
        raise ValueError("Stage E used a different horizon cube")
    if key.get("kernel_manifest_sha256") != dl._sha256(kernel_manifest):
        raise ValueError("Stage E used a different kernel manifest")
    if key.get("daily_lit_config_sha256") != dl._sha256(daily_cfg_path):
        raise ValueError("config/daily-lit.yaml differs from the one Stage E ran")
    period = [key.get("period_start_day"), key.get("period_end_day_exclusive")]
    if period != stage_e["stage_e_period"]:
        raise ValueError("Stage E run key and index disagree on the period")


def receipt_pin(
    *,
    kernel_manifest: Path,
    stage_a_dir: Path,
    stage_e_dir: Path,
    horizon_cube: Path,
    mode: str,
    darkness_config: Path | None = None,
    daily_lit_config: Path | None = None,
) -> dict:
    """Pins a Stage G preflight receipt records. Hashes no large file."""
    cfg_path = config_path(darkness_config).resolve()
    cfg = load_darkness_config(cfg_path)
    daily_path = dl.config_path(daily_lit_config).resolve()
    daily_cfg = dl.load_daily_lit_config(daily_path)
    start, end = period_days(cfg, daily_cfg, mode)
    kernel_path = Path(kernel_manifest)
    if not kernel_path.is_file():
        raise ValueError(f"kernel manifest is missing: {kernel_path}")
    pin = dl.stage_a_horizon_pin(stage_a_dir)
    horizon = Path(horizon_cube).resolve()
    if not horizon.is_file():
        raise ValueError(f"horizon cube is missing: {horizon}")
    if horizon.stat().st_size != pin["horizon_bytes"]:
        raise ValueError("horizon cube size differs from the Stage A manifest")
    stage_e = stage_e_pin(stage_e_dir)
    _require_stage_e_matches(
        stage_e,
        horizon_sha256=pin["horizon_sha256"],
        kernel_manifest=kernel_path,
        daily_cfg_path=daily_path,
    )
    if stage_e["stage_e_period"] != [
        dl.period_days(daily_cfg, "production")[0].isoformat(),
        dl.period_days(daily_cfg, "production")[1].isoformat(),
    ]:
        raise ValueError("Stage E period differs from config/daily-lit.yaml")
    return {
        **pin,
        "horizon_cube": str(horizon),
        "kernel_manifest": str(kernel_path.resolve()),
        "kernel_manifest_sha256": dl._sha256(kernel_path),
        "stage_e_dir": stage_e["stage_e_dir"],
        "stage_e_manifest_sha256": stage_e["stage_e_manifest_sha256"],
        "darkness_config": str(cfg_path),
        "darkness_config_sha256": dl._sha256(cfg_path),
        "daily_lit_config_sha256": dl._sha256(daily_path),
        "sun_config_sha256": dl._sha256(sun_config_path()),
        "darkness_mode": mode,
        "period_start_day": start.isoformat(),
        "period_end_day_exclusive": end.isoformat(),
        "accelerators": False,
    }


def reject_darkness_receipt(
    receipt: dict,
    *,
    kernel_manifest: Path,
    stage_a_dir: Path,
    stage_e_dir: Path,
    horizon_cube: Path,
    mode: str,
    darkness_config: Path | None = None,
) -> None:
    """Refuse a receipt whose Stage A/E, horizon, kernels, configs or period drifted."""
    if receipt.get("account") != "p201619":
        raise ValueError("Stage G requires account p201619")
    if receipt.get("accelerators") is not False:
        raise ValueError("Stage G does not allow accelerators")
    pin = receipt_pin(
        kernel_manifest=kernel_manifest,
        stage_a_dir=stage_a_dir,
        stage_e_dir=stage_e_dir,
        horizon_cube=horizon_cube,
        mode=mode,
        darkness_config=darkness_config,
    )
    for key, value in pin.items():
        if receipt.get(key) != value:
            raise ValueError(f"Stage G receipt mismatch: {key}")


# --------------------------------------------------------------------------
# Job
# --------------------------------------------------------------------------


def _prepare_output_dir(output_dir: Path, run_key: dict) -> None:
    """Stage G never resumes: the streaming state spans the whole period."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(output_dir / RUN_CONFIG_NAME, run_key)


def _verify_stage_e_files(stage_e_dir: Path, digests: dict, names: Sequence[str]) -> dict:
    checked = {}
    for name in names:
        path = Path(stage_e_dir) / name
        expected = digests[name]
        if path.stat().st_size != expected["bytes"]:
            raise ValueError(f"Stage E file size changed: {name}")
        if dl._sha256(path) != expected["sha256"]:
            raise ValueError(f"Stage E file digest differs from its manifest: {name}")
        checked[name] = expected
    return checked


def _with_fill(values: np.ndarray, valid: np.ndarray, fill, dtype) -> np.ndarray:
    return np.where(valid, values, fill).astype(dtype)


def _write_period_maps(output_dir: Path, period: dict, valid: np.ndarray) -> None:
    for kind in KINDS:
        for field, fill, dtype in (
            ("hours", FILL_UINT32, np.uint32),
            ("start", FILL_UINT32, np.uint32),
            ("flags", FILL_FLAGS, np.uint8),
            ("interior", FILL_UINT32, np.uint32),
        ):
            key = f"{kind}_{field}"
            _atomic_save(output_dir / PERIOD_FILES[key], _with_fill(period[key], valid, fill, dtype))


def write_index(output_dir: Path, start: date, end: date, shape, months, years) -> dict:
    index = {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "kind": "modeled scenario layer, not a measurement",
        "period": {
            "start_day_utc": start.isoformat(),
            "end_day_exclusive_utc": end.isoformat(),
            "hours": (end - start).days * HOURS_PER_DAY,
            "hour_zero_utc": f"{start.isoformat()}T00:00:00Z",
            "epoch_times": "HH:00:00Z for HH = 00..23, as Stage E",
        },
        "shape": list(shape),
        "lit_rule": "any fraction of the ±0.27° solar disc above the horizon",
        "definitions": {
            "run": "consecutive hourly samples of one kind; N samples = N hours (±1 h physically)",
            "ties": "the earliest longest run is reported",
            "censoring": "bit 1: run starts at hour 0; bit 2: run ends at the last hour",
            "interior": "longest run that touches neither end of the period",
            "month_triple": "lead/trail/best dark hours inside the block; all dark where lead == hours",
        },
        "fill": {
            "uint32": int(FILL_UINT32),
            "uint16": int(FILL_UINT16),
            "flags_uint8": FILL_FLAGS,
            "no_run_start": int(NO_RUN),
            "meaning": "out-of-cap pixel; NaN in the Stage A layers",
        },
        "period_files": PERIOD_FILES,
        "monthly": {field: f"monthly/dark-{field}.npy" for field in MONTH_FIELDS},
        "yearly": {field: f"yearly/dark-{field}.npy" for field in MONTH_FIELDS},
        "months": list(months),
        "years": list(years),
        "valid_mask": "valid-mask.npy",
    }
    _atomic_json(Path(output_dir) / INDEX_NAME, index)
    return index


def _build_years(
    output_dir: Path, months: Sequence[dict], years: Sequence[dict], valid: np.ndarray
) -> np.ndarray:
    """Stitch monthly files into yearly files; return the whole-period best."""
    from numpy.lib.format import open_memmap

    monthly = {
        field: np.load(output_dir / f"monthly/dark-{field}.npy", mmap_mode="r")
        for field in MONTH_FIELDS
    }
    (output_dir / "yearly").mkdir(exist_ok=True)
    temporary = {
        field: output_dir / f"yearly/dark-{field}.npy.tmp" for field in MONTH_FIELDS
    }
    yearly = {
        field: open_memmap(temporary[field], mode="w+", dtype=np.uint16, shape=(len(years), *valid.shape))
        for field in MONTH_FIELDS
    }
    total = None
    for j, year in enumerate(years):
        first = year["first_month"]
        block = stitch(
            (months[k]["hours"], *(monthly[f][k] for f in MONTH_FIELDS))
            for k in range(first, first + year["month_count"])
        )
        if block[0] != year["hours"]:
            raise RuntimeError("year hours do not add up")
        for field, values in zip(MONTH_FIELDS, block[1:]):
            yearly[field][j] = _with_fill(values, valid, FILL_UINT16, np.uint16)
        total = block if total is None else combine(total, block)
    for field in MONTH_FIELDS:
        yearly[field].flush()
        del yearly[field]
        os.replace(temporary[field], output_dir / f"yearly/dark-{field}.npy")
    return total[3]


def project_node_hours(*, projection_limit_node_hours: float, **measured) -> dict:
    """Stage E's projection rule; the pass limit leaves headroom (no resume)."""
    result = dl.project_node_hours(**measured)
    result["projection_limit_node_hours"] = float(projection_limit_node_hours)
    result["fits_production_cap"] = bool(
        result["projected_node_hours"] <= float(projection_limit_node_hours)
    )
    return result


def run_darkness(
    *,
    mode: str,
    input_manifest: Path,
    kernel_manifest: Path,
    stage_a_dir: Path,
    stage_e_dir: Path,
    output_dir: Path,
    workers: int,
    horizon_cube: Path | None = None,
    darkness_config: Path | None = None,
    daily_lit_config: Path | None = None,
) -> dict:
    """Pilot or production run. Refuses before heavy work on any bad pin."""
    from numpy.lib.format import open_memmap

    from anuma.grid import Grid
    from anuma.hpc_smoke import sha256_file, validate_input_manifest
    from anuma.sun_validation import _require_lon0_zero

    cfg_path = config_path(darkness_config).resolve()
    cfg = load_darkness_config(cfg_path)
    daily_path = dl.config_path(daily_lit_config).resolve()
    daily_cfg = dl.load_daily_lit_config(daily_path)
    sun_cfg = load_sun_config()
    start, end = period_days(cfg, daily_cfg, mode)
    roles = sun_cfg["sun"]["required_kernel_roles"]
    stage_a_dir = Path(stage_a_dir).resolve()
    stage_e_dir = Path(stage_e_dir).resolve()
    horizon_path = Path(horizon_cube or stage_a_dir / "terrain-horizon.npy").resolve()
    pin = dl.stage_a_horizon_pin(stage_a_dir)
    stage_e = stage_e_pin(stage_e_dir)
    _require_stage_e_matches(
        stage_e,
        horizon_sha256=pin["horizon_sha256"],
        kernel_manifest=Path(kernel_manifest),
        daily_cfg_path=daily_path,
    )
    coverage = dl.require_spice_coverage(kernel_manifest, roles, start, end)
    _, files = validate_input_manifest(Path(input_manifest))
    grid = Grid.from_json(files["grid"])
    _require_lon0_zero(grid)

    started_utc = datetime.now(timezone.utc).isoformat()
    clock = time.perf_counter()
    horizon_sha = dl._sha256(horizon_path)
    if horizon_sha != pin["horizon_sha256"]:
        raise ValueError("horizon cube digest differs from the Stage A manifest")
    output_dir = Path(output_dir).resolve()
    run_key = {
        "horizon_sha256": horizon_sha,
        "kernel_manifest_sha256": dl._sha256(Path(kernel_manifest)),
        "darkness_config_sha256": dl._sha256(cfg_path),
        "daily_lit_config_sha256": dl._sha256(daily_path),
        "sun_config_sha256": dl._sha256(sun_config_path()),
        "stage_e_manifest_sha256": stage_e["stage_e_manifest_sha256"],
        "period_start_day": start.isoformat(),
        "period_end_day_exclusive": end.isoformat(),
        "mode": mode,
    }
    _prepare_output_dir(output_dir, run_key)
    log_path = output_dir / "progress.log"
    _log_progress(f"darkness {mode} {start}..{end}", log_path)

    horizons = np.load(horizon_path, allow_pickle=False)
    azimuths = int(sun_cfg["horizon"]["azimuth_count"])
    if horizons.dtype != np.float32 or horizons.shape != (azimuths, grid.height, grid.width):
        raise ValueError(f"horizon cube shape {horizons.shape} does not fit the grid")
    longitude, latitude = grid.lonlat_arrays()
    valid = dl.valid_pixels(horizons, longitude, latitude)
    _atomic_save(output_dir / "valid-mask.npy", valid)
    fixed_seconds = time.perf_counter() - clock

    # Stage E files used as the reference: their digests must match its
    # manifest. The pilot reads the first year file only.
    clock = time.perf_counter()
    stage_e_cube = dl.DailyLitCube(stage_e_dir)
    needed = ["cumulative-monthly.npy"] + [
        item["file"]
        for item in stage_e_cube.index["chunks"]
        if dl.parse_day(item["first_day"]) < end
        and start < dl.parse_day(item["first_day"]) + timedelta(days=int(item["day_count"]))
    ]
    verified = _verify_stage_e_files(stage_e_dir, stage_e["stage_e_digests"], needed)
    stage_e_hash_seconds = time.perf_counter() - clock
    stage_e_hash_bytes = sum(item["bytes"] for item in verified.values())
    valid_matches_stage_e = bool(np.array_equal(valid, stage_e_cube.valid))

    def vectors_for(epochs: list[str]) -> np.ndarray:
        return sun_vectors_from_spice(
            epochs,
            kernel_manifest,
            required_roles=roles,
            frame=str(sun_cfg["sun"]["frame"]),
            aberration_correction=str(sun_cfg["sun"]["aberration_correction"]),
        )

    months = month_table(start, end)
    years = year_table(months)
    total_hours = (end - start).days * HOURS_PER_DAY
    disk = float(sun_cfg["sun"]["disk_radius_deg"])
    origin = float(sun_cfg["horizon"]["azimuth_origin_deg"])
    (output_dir / "monthly").mkdir(exist_ok=True)
    month_tmp = {f: output_dir / f"monthly/dark-{f}.npy.tmp" for f in MONTH_FIELDS}
    monthly = {
        f: open_memmap(month_tmp[f], mode="w+", dtype=np.uint16, shape=(len(months), *valid.shape))
        for f in MONTH_FIELDS
    }
    timings: dict = {
        "fixed_seconds": fixed_seconds,
        "stage_e_hash_seconds": stage_e_hash_seconds,
        "stage_e_hash_bytes": stage_e_hash_bytes,
        "spice_seconds": 0.0,
        "spice_calls": 0,
        "evaluate_seconds": 0.0,
        "compare_write_seconds": 0.0,
        "dispatches": [],
    }
    daily_mismatch = 0
    month_bounds_ok = True
    pilot_vectors: list[np.ndarray] = []
    clock = time.perf_counter()
    engine = DarknessEngine(
        horizons, longitude, latitude, workers=workers, disk_radius_deg=disk, azimuth_origin_deg=origin
    )
    timings["pool_start_seconds"] = time.perf_counter() - clock
    try:
        for chunk in dl.year_chunks(start, end):
            first_day = dl.parse_day(chunk["first_day"])
            clock = time.perf_counter()
            vectors = vectors_for(dl.day_epochs(first_day, int(chunk["day_count"])))
            timings["spice_seconds"] += time.perf_counter() - clock
            timings["spice_calls"] += 1
            if mode == "pilot":
                pilot_vectors.append(vectors)
            chunk_first_hour = int(chunk["offset_days"]) * HOURS_PER_DAY
            for k, month in enumerate(months):
                lo = month["first_hour"] - chunk_first_hour
                if not 0 <= lo < len(vectors):
                    continue
                if engine.hour != month["first_hour"] or lo + month["hours"] > len(vectors):
                    raise RuntimeError(
                        f"month {month['label']} is out of step with the hourly stream"
                    )
                clock = time.perf_counter()
                triple, daily = engine.advance_month(vectors[lo : lo + month["hours"]])
                seconds = time.perf_counter() - clock
                timings["evaluate_seconds"] += seconds
                timings["dispatches"].append({"epochs": month["hours"], "seconds": seconds})
                clock = time.perf_counter()
                for f, values in zip(MONTH_FIELDS, triple):
                    monthly[f][k] = np.where(valid, values, FILL_UINT16)
                lead, trail, best = (values[valid].astype(np.int64) for values in triple)
                hours = month["hours"]
                month_bounds_ok = month_bounds_ok and bool(
                    best.max(initial=0) <= hours
                    and np.all(lead <= best)
                    and np.all(trail <= best)
                    and np.array_equal(lead == hours, best == hours)
                    and np.array_equal(trail == hours, best == hours)
                )
                day0 = dl.parse_day(month["first_day"])
                reference = stage_e_cube.daily(day0, day0 + timedelta(days=month["days"]))
                daily_mismatch += int(np.count_nonzero(daily[:, valid] != reference[:, valid]))
                timings["compare_write_seconds"] += time.perf_counter() - clock
                _log_progress(f"darkness month {month['label']} done", log_path)
        clock = time.perf_counter()
        period = engine.finish()
    finally:
        engine.close()
    for f in MONTH_FIELDS:
        monthly[f].flush()
        del monthly[f]
        os.replace(month_tmp[f], output_dir / f"monthly/dark-{f}.npy")
    _write_period_maps(output_dir, period, valid)
    stitched_best = _build_years(output_dir, months, years, valid)
    write_index(output_dir, start, end, valid.shape, months, years)
    timings["finalize_seconds"] = time.perf_counter() - clock

    # Checks fixed before any run (docs/stage-g-darkness-plan.md).
    boundaries = stage_e_cube.boundaries
    if start not in boundaries or end not in boundaries:
        raise ValueError("period is not on Stage E cumulative boundaries")
    k0, k1 = boundaries.index(start), boundaries.index(end)
    stage_e_total = stage_e_cube.cumulative[k1].astype(np.int64) - stage_e_cube.cumulative[k0].astype(np.int64)
    lit_total = period["lit_total"].astype(np.int64)
    dark_hours = period["dark_hours"].astype(np.int64)
    lit_hours = period["lit_hours"].astype(np.int64)
    never_lit = (lit_total == 0) & valid
    always_lit = (lit_total == total_hours) & valid
    checks = {
        "valid_matches_stage_e": valid_matches_stage_e,
        "daily_lit_hours_equal_stage_e": daily_mismatch == 0,
        "daily_mismatched_pixel_days": daily_mismatch,
        "lit_total_equals_stage_e_cumulative": bool(
            np.array_equal(lit_total[valid], stage_e_total[valid])
        ),
        "month_triples_within_bounds": month_bounds_ok,
        "stitched_months_equal_streaming": bool(
            np.array_equal(stitched_best[valid], dark_hours[valid])
        ),
        "run_invariants": bool(
            np.all(dark_hours[valid] + lit_hours[valid] <= total_hours)
            and np.all(dark_hours[valid] <= total_hours - lit_total[valid])
            and np.all(lit_hours[valid] <= lit_total[valid])
            and np.all((dark_hours[valid] == 0) == (lit_total[valid] == total_hours))
            and np.all(dark_hours[never_lit] == total_hours)
            and np.all(period["dark_flags"][never_lit] == CENSORED_AT_START | CENSORED_AT_END)
            and np.all(lit_hours[always_lit] == total_hours)
        ),
    }
    summary = _summary(period, valid, total_hours)
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
            "hours": total_hours,
            "months": len(months),
        },
        "timings_seconds": timings,
        "checks": checks,
        "summary": summary,
        "spice_coverage": coverage,
        "inputs": {
            **pin,
            "horizon_cube": str(horizon_path),
            "stage_e": {k: v for k, v in stage_e.items() if k != "stage_e_digests"},
            "stage_e_files_verified": verified,
            "input_manifest": {
                "path": str(Path(input_manifest).resolve()),
                "sha256": sha256_file(Path(input_manifest).resolve()),
            },
            "run_key": run_key,
        },
        "software": {"python": platform.python_version(), "numpy": np.__version__},
        "execution": {"workers": workers, "dispatch": "one calendar month"},
    }
    gate = [
        "valid_matches_stage_e",
        "daily_lit_hours_equal_stage_e",
        "lit_total_equals_stage_e_cumulative",
        "month_triples_within_bounds",
        "stitched_months_equal_streaming",
        "run_invariants",
    ]
    if mode == "pilot":
        clock = time.perf_counter()
        report["sample_checks"] = _pilot_sample_checks(
            output_dir,
            horizons,
            longitude,
            latitude,
            np.concatenate(pilot_vectors),
            months,
            valid,
            stride=int(cfg["pilot"]["check_row_stride"]),
            disk_radius_deg=disk,
            azimuth_origin_deg=origin,
        )
        timings["sample_check_seconds"] = time.perf_counter() - clock
        checks["serial_parallel_identical"] = report["sample_checks"]["serial_parallel_identical"]
        checks["brute_force_identical"] = report["sample_checks"]["brute_force_identical"]
        checks["stitching_equals_brute_force"] = report["sample_checks"]["stitching_equals_brute_force"]
        gate += ["serial_parallel_identical", "brute_force_identical", "stitching_equals_brute_force"]
        clock = time.perf_counter()
        verify_kernel_manifest(kernel_manifest, roles)
        timings["spice_verify_seconds"] = time.perf_counter() - clock
        report["projection"] = _pilot_projection(cfg, daily_cfg, timings, start, end, stage_e)
        gate_projection = report["projection"]["fits_production_cap"]
    else:
        gate_projection = True
    report["passed"] = bool(all(checks[name] for name in gate) and gate_projection)
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    name = "pilot-measurement.json" if mode == "pilot" else "darkness-report.json"
    report_path = output_dir / name
    _atomic_json(report_path, report)
    _write_output_manifest(output_dir, report, report_path)
    return report


def _summary(period: dict, valid: np.ndarray, total_hours: int) -> dict:
    dark = period["dark_hours"][valid].astype(np.int64)
    lit = period["lit_hours"][valid].astype(np.int64)
    flags = period["dark_flags"][valid]
    percentiles = (1, 5, 25, 50, 75, 95, 99)
    return {
        "valid_pixels": int(valid.sum()),
        "total_hours": total_hours,
        "never_lit_pixels": int(np.count_nonzero(period["lit_total"][valid] == 0)),
        "never_dark_pixels": int(np.count_nonzero(dark == 0)),
        "longest_dark_censored_pixels": int(np.count_nonzero(flags != 0)),
        "longest_lit_censored_pixels": int(np.count_nonzero(period["lit_flags"][valid] != 0)),
        "longest_dark_hours_percentiles": {
            str(p): float(np.percentile(dark, p)) if dark.size else None for p in percentiles
        },
        "longest_lit_hours_percentiles": {
            str(p): float(np.percentile(lit, p)) if lit.size else None for p in percentiles
        },
    }


def _pilot_projection(cfg, daily_cfg, timings, start, end, stage_e) -> dict:
    dispatches = timings["dispatches"]
    if len(dispatches) < 2:
        raise ValueError("pilot needs two dispatches to time a warm one")
    warm = dispatches[-1]["seconds"] / dispatches[-1]["epochs"]
    cold_excess = max(0.0, dispatches[0]["seconds"] - warm * dispatches[0]["epochs"])
    full_start, full_end = dl.period_days(daily_cfg, "production")
    full_days = (full_end - full_start).days
    pilot_days = (end - start).days
    pilot_epochs = pilot_days * HOURS_PER_DAY
    verify_seconds = float(timings["spice_verify_seconds"])
    spice_per_epoch = (
        max(0.0, timings["spice_seconds"] - verify_seconds * timings["spice_calls"]) / pilot_epochs
    )
    # Stage E reference hashing scales with bytes: the pilot hashed the
    # cumulative file and one year file; production hashes every file.
    production_bytes = sum(item["bytes"] for item in stage_e["stage_e_digests"].values())
    rate = timings["stage_e_hash_seconds"] / max(1, timings["stage_e_hash_bytes"])
    hash_seconds = rate * production_bytes
    exe = cfg["execution"]
    result = project_node_hours(
        projection_limit_node_hours=float(exe["projection_limit_node_hours"]),
        fixed_seconds=timings["fixed_seconds"] + timings["pool_start_seconds"] + hash_seconds,
        cold_excess_seconds=cold_excess,
        warm_seconds_per_epoch=warm,
        spice_verify_seconds=verify_seconds,
        spice_calls=len(dl.year_chunks(full_start, full_end)),
        spice_seconds_per_epoch=spice_per_epoch,
        full_epochs=full_days * HOURS_PER_DAY,
        io_seconds=timings["compare_write_seconds"] + timings["finalize_seconds"],
        pilot_days=pilot_days,
        full_days=full_days,
        production_node_hours=float(exe["production_node_hours"]),
        stage_node_hour_cap=float(exe["stage_node_hour_cap"]),
    )
    result["method"] = (
        "warm last-month s/epoch x full epochs; kernel verification x production "
        "SPICE calls (one per year); remaining SPICE s/epoch x full epochs; start-up, cube hash and first-dispatch excess once; "
        "Stage E reference hashing x production bytes; compare/write/finalize x days"
    )
    result["projected_stage_e_hash_seconds"] = hash_seconds
    result["pilot_warm_seconds_per_epoch"] = warm
    result["pilot_mean_seconds_per_epoch"] = timings["evaluate_seconds"] / pilot_epochs
    result["stage_e_production_wall_seconds"] = 50 * 60 + 23
    return result


def _pilot_sample_checks(
    output_dir: Path,
    horizons: np.ndarray,
    longitude: np.ndarray,
    latitude: np.ndarray,
    vectors: np.ndarray,
    months: Sequence[dict],
    valid: np.ndarray,
    *,
    stride: int,
    disk_radius_deg: float,
    azimuth_origin_deg: float,
) -> dict:
    """On every ``stride``-th row: serial path, brute force, and every window."""
    rows = slice(0, longitude.shape[0], stride)
    sample_h = np.ascontiguousarray(horizons[:, rows, :])
    sample_lon = np.ascontiguousarray(longitude[rows])
    sample_lat = np.ascontiguousarray(latitude[rows])
    sample_valid = valid[rows].reshape(-1)
    shape = sample_lon.shape
    total_hours = len(vectors)

    # 1. Serial code path on the sample rows, compared bit for bit.
    serial = DarknessEngine(
        sample_h, sample_lon, sample_lat, workers=1,
        disk_radius_deg=disk_radius_deg, azimuth_origin_deg=azimuth_origin_deg,
    )
    serial_months = []
    for month in months:
        lo = month["first_hour"]
        triple, _ = serial.advance_month(vectors[lo : lo + month["hours"]])
        serial_months.append(triple)
    serial_period = serial.finish()
    serial.close()
    v2 = valid[rows]
    identical = True
    monthly = {f: np.load(output_dir / f"monthly/dark-{f}.npy", mmap_mode="r") for f in MONTH_FIELDS}
    for k, triple in enumerate(serial_months):
        for i, f in enumerate(MONTH_FIELDS):
            expected = np.where(v2, triple[i], FILL_UINT16)
            identical &= bool(np.array_equal(expected, monthly[f][k, rows, :]))
    stored = {
        key: np.load(output_dir / name, allow_pickle=False)[rows, :]
        for key, name in PERIOD_FILES.items()
    }
    for key in PERIOD_FILES:
        fill = FILL_FLAGS if key.endswith("flags") else FILL_UINT32
        expected = np.where(v2, serial_period[key], fill)
        identical &= bool(np.array_equal(expected, stored[key]))

    # 2. Brute force from lit samples evaluated directly with the lit rule.
    lit = lit_matrix(
        sample_h, sample_lon, sample_lat, vectors,
        disk_radius_deg=disk_radius_deg, azimuth_origin_deg=azimuth_origin_deg,
    )
    brute = brute_force_runs(lit)
    brute_ok = True
    for key in PERIOD_FILES:
        brute_ok &= bool(
            np.array_equal(
                stored[key].reshape(-1)[sample_valid].astype(np.int64),
                brute[key][sample_valid].astype(np.int64),
            )
        )

    # 3. Every month-aligned window: stitched files == brute force.
    windows = 0
    stitch_ok = True
    for a in range(len(months)):
        for b in range(a + 1, len(months) + 1):
            _, _, _, best = stitch(
                (months[k]["hours"], *(monthly[f][k, rows, :].reshape(-1) for f in MONTH_FIELDS))
                for k in range(a, b)
            )
            lo, hi = months[a]["first_hour"], months[b - 1]["first_hour"] + months[b - 1]["hours"]
            reference = brute_force_longest_dark(lit[lo:hi])
            stitch_ok &= bool(np.array_equal(best[sample_valid], reference[sample_valid]))
            windows += 1
    return {
        "row_stride": stride,
        "pixels": int(sample_valid.sum()),
        "hours": total_hours,
        "windows": windows,
        "serial_parallel_identical": identical,
        "brute_force_identical": brute_ok,
        "stitching_equals_brute_force": stitch_ok,
        "shape": list(shape),
    }


def _write_output_manifest(output_dir: Path, report: dict, report_path: Path) -> None:
    outputs = {}
    names = [
        *PERIOD_FILES.values(),
        *(f"monthly/dark-{f}.npy" for f in MONTH_FIELDS),
        *(f"yearly/dark-{f}.npy" for f in MONTH_FIELDS),
        "valid-mask.npy",
        INDEX_NAME,
        RUN_CONFIG_NAME,
        report_path.name,
    ]
    for name in names:
        path = output_dir / name
        outputs[name] = {"sha256": dl._sha256(path), "bytes": path.stat().st_size}
    _atomic_json(output_dir / "output-manifest.json", {**report, "outputs": outputs})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("pilot", "production"), required=True)
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument("--kernel-manifest", type=Path, default=None)
    parser.add_argument("--stage-a-dir", type=Path, required=True)
    parser.add_argument("--stage-e-dir", type=Path, required=True)
    parser.add_argument("--horizon-cube", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    kernel_manifest = args.kernel_manifest or configured_kernel_manifest()
    try:
        report = run_darkness(
            mode=args.mode,
            input_manifest=args.input_manifest,
            kernel_manifest=kernel_manifest,
            stage_a_dir=args.stage_a_dir,
            stage_e_dir=args.stage_e_dir,
            output_dir=args.output_dir,
            workers=args.workers,
            horizon_cube=args.horizon_cube,
            darkness_config=args.config,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f"darkness precondition failed: {exc}\n")
    except RuntimeError as exc:
        # Internal consistency failure during the run (e.g. the month loop
        # out of step with the hourly stream): treated as a failed check.
        parser.exit(3, f"darkness run failed: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    if args.mode == "pilot" and not report["projection"]["fits_production_cap"]:
        raise SystemExit(4)
    if not report["passed"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
