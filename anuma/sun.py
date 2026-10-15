"""Terrain-horizon Sun model with externally supplied DE440 geometry.

The terrain horizon is computed once and reused for every epoch.  Orbit and
frame geometry is delegated to SPICE; this module contains no orbital model.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import yaml

from anuma.config import repo_root


def load_sun_config(path: Path | None = None) -> dict:
    config_path = Path(path) if path else repo_root() / "config" / "sun.yaml"
    with config_path.open() as handle:
        return yaml.safe_load(handle)


def configured_kernel_manifest(cfg: dict | None = None) -> Path:
    """Return the external kernel manifest path. Digests are not in this repo."""
    cfg = cfg if cfg is not None else load_sun_config()
    raw = cfg["sun"].get("kernel_manifest")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("sun.kernel_manifest is required")
    return Path(raw)


def _ray_offsets(
    shape: tuple[int, int], azimuth_deg: float
) -> list[tuple[int, int]]:
    """Return unique pixel offsets along a clockwise-from-north ray."""
    limit = int(np.ceil(np.hypot(*shape)))
    radians = np.deg2rad(float(azimuth_deg))
    offsets: list[tuple[int, int]] = []
    previous = (0, 0)
    for step in range(1, limit + 1):
        offset = (
            int(np.rint(-np.cos(radians) * step)),
            int(np.rint(np.sin(radians) * step)),
        )
        if offset != previous and offset != (0, 0):
            offsets.append(offset)
            previous = offset
    return offsets


def _overlap_slices(
    shape: tuple[int, int], dr: int, dc: int
) -> tuple[tuple[slice, slice], tuple[slice, slice]] | None:
    height, width = shape
    row0, row1 = max(0, -dr), min(height, height - dr)
    col0, col1 = max(0, -dc), min(width, width - dc)
    if row0 >= row1 or col0 >= col1:
        return None
    origin = (slice(row0, row1), slice(col0, col1))
    target = (slice(row0 + dr, row1 + dr), slice(col0 + dc, col1 + dc))
    return origin, target


def horizon_for_azimuth(
    dem: np.ndarray,
    resolution_m: float,
    moon_radius_m: float,
    azimuth_deg: float,
) -> np.ndarray:
    """Maximum terrain elevation angle for one azimuth at every DEM pixel."""
    elevation = np.asarray(dem, dtype=np.float64)
    if elevation.ndim != 2:
        raise ValueError("DEM must be two-dimensional")
    if resolution_m <= 0.0 or moon_radius_m <= 0.0:
        raise ValueError("resolution and lunar radius must be positive")

    horizon = np.full(elevation.shape, -90.0, dtype=np.float64)
    for dr, dc in _ray_offsets(elevation.shape, azimuth_deg):
        overlap = _overlap_slices(elevation.shape, dr, dc)
        if overlap is None:
            continue
        origin_slice, target_slice = overlap
        origin = elevation[origin_slice]
        target = elevation[target_slice]
        distance = np.hypot(dr, dc) * float(resolution_m)
        central_angle = distance / float(moon_radius_m)
        horizontal = (moon_radius_m + target) * np.sin(central_angle)
        vertical = (
            (moon_radius_m + target) * np.cos(central_angle)
            - (moon_radius_m + origin)
        )
        angle = np.rad2deg(np.arctan2(vertical, horizontal))
        valid = np.isfinite(origin) & np.isfinite(target)
        view = horizon[origin_slice]
        np.maximum(view, np.where(valid, angle, -90.0), out=view)
    horizon[~np.isfinite(elevation)] = np.nan
    return horizon.astype(np.float32)


def _log_progress(message: str, log_path: Path | None = None) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} {message}"
    print(line, flush=True)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as handle:
            handle.write(line + "\n")


def _atomic_save(path: Path, array: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        np.save(handle, np.ascontiguousarray(array))
    os.replace(temporary, path)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def compute_horizons(
    dem: np.ndarray,
    resolution_m: float,
    *,
    moon_radius_m: float = 1_737_400.0,
    azimuth_count: int = 360,
    azimuth_origin_deg: float = 0.0,
    workers: int = 1,
    checkpoint_dir: Path | None = None,
    log_path: Path | None = None,
) -> np.ndarray:
    """Compute the reusable horizon cube, deterministically by azimuth."""
    if azimuth_count <= 0:
        raise ValueError("azimuth_count must be positive")
    if workers <= 0 or workers > 128:
        raise ValueError("workers must be between 1 and 128")
    azimuths = (
        float(azimuth_origin_deg)
        + np.arange(azimuth_count, dtype=np.float64) * 360.0 / azimuth_count
    )
    checkpoint = Path(checkpoint_dir) if checkpoint_dir else None
    layers: list[np.ndarray | None] = [None] * azimuth_count
    pending: list[int] = []
    if checkpoint is not None:
        checkpoint.mkdir(parents=True, exist_ok=True)
        for index in range(azimuth_count):
            path = checkpoint / f"azimuth-{index:03d}.npy"
            if path.is_file():
                layers[index] = np.load(path)
            else:
                pending.append(index)
        _log_progress(
            f"horizon resume {azimuth_count - len(pending)}/{azimuth_count} azimuths",
            log_path,
        )
    else:
        pending = list(range(azimuth_count))

    def calculate(index: int) -> np.ndarray:
        return horizon_for_azimuth(
            dem, resolution_m, moon_radius_m, float(azimuths[index])
        )

    def store(index: int, layer: np.ndarray) -> None:
        layers[index] = layer
        if checkpoint is not None:
            _atomic_save(checkpoint / f"azimuth-{index:03d}.npy", layer)
            done = sum(item is not None for item in layers)
            _atomic_json(
                checkpoint / "horizon-progress.json",
                {"completed_azimuths": done, "azimuth_count": azimuth_count},
            )
            _log_progress(
                f"horizon azimuth {index + 1}/{azimuth_count} stored",
                log_path,
            )
        elif log_path is not None:
            _log_progress(
                f"horizon azimuth {index + 1}/{azimuth_count} stored",
                log_path,
            )

    if not pending:
        pass
    elif workers == 1:
        for index in pending:
            store(index, calculate(index))
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(calculate, index): index for index in pending}
            for future in as_completed(futures):
                index = futures[future]
                store(index, future.result())
    if any(layer is None for layer in layers):
        raise ValueError("horizon checkpoint is missing an azimuth")
    return np.stack(layers)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_kernel_manifest(
    kernel_manifest: Path, required_roles: Iterable[str]
) -> list[Path]:
    """Read the manifest and verify every listed kernel before SPICE is called.

    The MeluXina manifest is the pin. This function does not embed digests.
    A missing required role, a repeated role, a missing file, or a SHA-256
    mismatch raises ValueError and does not import spiceypy.
    """
    manifest_path = Path(kernel_manifest).resolve()
    manifest = json.loads(manifest_path.read_text())
    entries = manifest.get("kernels")
    if not isinstance(entries, list) or not entries:
        raise ValueError("kernel manifest must contain a kernels list")

    by_role: dict[str, dict] = {}
    listed: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("kernel manifest entry must be an object")
        role = entry.get("role")
        if not isinstance(role, str) or not role.strip():
            raise ValueError("kernel manifest entry is missing a role")
        if role in by_role:
            raise ValueError(f"kernel manifest repeats role {role}")
        by_role[role] = entry
        listed.append(role)

    required = [str(role) for role in required_roles]
    missing = [role for role in required if role not in by_role]
    if missing:
        raise ValueError(
            "kernel manifest is missing roles: " + ", ".join(missing)
        )

    order = required + [role for role in listed if role not in required]
    verified: dict[str, Path] = {}
    for role in order:
        entry = by_role[role]
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f"kernel path missing for role {role}")
        path = Path(raw_path)
        if not path.is_absolute():
            path = manifest_path.parent / path
        path = path.resolve()
        if not path.is_file():
            raise ValueError(f"kernel file missing for role {role}")
        expected = str(entry.get("sha256", "")).strip().lower()
        actual = _sha256(path)
        if (
            len(expected) != 64
            or any(char not in "0123456789abcdef" for char in expected)
            or actual != expected
        ):
            raise ValueError(f"kernel checksum mismatch for role {role}")
        verified[role] = path
    return [verified[role] for role in required]


def sun_vectors_from_spice(
    utc_epochs: Sequence[str],
    kernel_manifest: Path,
    *,
    required_roles: Iterable[str],
    frame: str = "MOON_ME",
    aberration_correction: str = "LT+S",
) -> np.ndarray:
    """Return unit Moon-to-Sun vectors from checksummed SPICE kernels."""
    paths = verify_kernel_manifest(kernel_manifest, required_roles)
    try:
        import spiceypy as spice
    except ImportError as exc:  # pragma: no cover - depends on HPC environment
        raise RuntimeError("spiceypy is required for DE440 Sun directions") from exc

    spice.kclear()
    try:
        for path in paths:
            spice.furnsh(str(path))
        vectors = []
        for utc in utc_epochs:
            et = spice.str2et(str(utc))
            position, _ = spice.spkpos(
                "SUN", et, frame, aberration_correction, "MOON"
            )
            vector = np.asarray(position, dtype=np.float64)
            vectors.append(vector / np.linalg.norm(vector))
    finally:
        spice.kclear()
    return np.asarray(vectors, dtype=np.float64)


def sun_disk_visible_fraction(
    center_elevation_deg: np.ndarray, horizon_deg: np.ndarray, radius_deg: float
) -> np.ndarray:
    """Visible area fraction of a circular Sun disk above the terrain horizon."""
    if radius_deg <= 0.0:
        raise ValueError("Sun disk radius must be positive")
    relative = (
        np.asarray(center_elevation_deg, dtype=np.float64)
        - np.asarray(horizon_deg, dtype=np.float64)
    ) / float(radius_deg)
    clipped = np.clip(relative, -1.0, 1.0)
    fraction = (
        np.arccos(-clipped) + clipped * np.sqrt(np.maximum(0.0, 1.0 - clipped**2))
    ) / np.pi
    return np.where(relative <= -1.0, 0.0, np.where(relative >= 1.0, 1.0, fraction))


def _row_blocks(row_count: int, workers: int) -> list[tuple[int, int]]:
    workers = max(1, min(int(workers), int(row_count)))
    base, extra = divmod(row_count, workers)
    blocks = []
    start = 0
    for index in range(workers):
        stop = start + base + (1 if index < extra else 0)
        if stop > start:
            blocks.append((start, stop))
        start = stop
    return blocks


def _local_frames(
    longitude: np.ndarray, latitude: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """East, north, and up unit vectors in MOON_ME for every pixel."""
    lon = np.deg2rad(longitude)
    lat = np.deg2rad(latitude)
    east = np.stack((-np.sin(lon), np.cos(lon), np.zeros_like(lon)))
    north = np.stack(
        (-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat))
    )
    up = np.stack(
        (np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat))
    )
    return east, north, up


def _lit_mask(
    vector: np.ndarray,
    horizons: np.ndarray,
    longitude: np.ndarray,
    frames: tuple[np.ndarray, np.ndarray, np.ndarray],
    *,
    disk_radius_deg: float,
    azimuth_origin_deg: float,
) -> np.ndarray:
    """Boolean lit mask for one Sun direction. The single ANUMA lit rule."""
    east, north, up = frames
    azimuth_count = horizons.shape[0]
    east_component = np.einsum("i,ijk->jk", vector, east)
    north_component = np.einsum("i,ijk->jk", vector, north)
    up_component = np.einsum("i,ijk->jk", vector, up)
    azimuth = np.mod(
        np.rad2deg(np.arctan2(east_component, north_component)), 360.0
    )
    # The horizon cube is indexed clockwise from grid +y. On the south
    # polar stereographic grid (lon_0 = 0) local north at longitude L
    # points along grid azimuth L, so grid azimuth = local azimuth + L.
    grid_azimuth = azimuth + longitude
    index = np.rint(
        np.mod(grid_azimuth - azimuth_origin_deg, 360.0)
        * azimuth_count
        / 360.0
    ).astype(np.int64) % azimuth_count
    terrain = np.take_along_axis(horizons, index[None, ...], axis=0)[0]
    elevation = np.rad2deg(np.arcsin(np.clip(up_component, -1.0, 1.0)))
    # PDS AVGVISIB: a timestep is lit when any fraction of the solar disc
    # is above the horizon. Do not average the visible-disc area.
    visible_fraction = sun_disk_visible_fraction(
        elevation, terrain, disk_radius_deg
    )
    return visible_fraction > 0.0


def _advance_sunlight_block(
    horizons: np.ndarray,
    longitude: np.ndarray,
    latitude: np.ndarray,
    vectors: np.ndarray,
    total: np.ndarray,
    current_dark: np.ndarray,
    longest_dark: np.ndarray,
    *,
    disk_radius_deg: float,
    azimuth_origin_deg: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Advance one row tile. Darkness state does not cross rows."""
    frames = _local_frames(longitude, latitude)
    total = np.array(total, dtype=np.float64, copy=True)
    current_dark = np.array(current_dark, dtype=np.int64, copy=True)
    longest_dark = np.array(longest_dark, dtype=np.int64, copy=True)
    for vector in vectors:
        lit = _lit_mask(
            vector,
            horizons,
            longitude,
            frames,
            disk_radius_deg=disk_radius_deg,
            azimuth_origin_deg=azimuth_origin_deg,
        )
        total += lit
        dark = ~lit
        current_dark = np.where(dark, current_dark + 1, 0)
        longest_dark = np.maximum(longest_dark, current_dark)
    return total, current_dark, longest_dark


# Read-only arrays for row-tile workers. Set before the fork pool starts so
# each worker inherits them instead of receiving a pickled copy per chunk.
_TILE_SHARED: dict[str, np.ndarray] | None = None


def _advance_sunlight_tile(task: tuple) -> tuple[int, int, np.ndarray, np.ndarray, np.ndarray]:
    (
        row0,
        row1,
        vectors,
        total,
        current_dark,
        longest_dark,
        disk_radius_deg,
        azimuth_origin_deg,
    ) = task
    shared = _TILE_SHARED
    if shared is None:
        raise RuntimeError("row-tile worker started without shared arrays")
    advanced = _advance_sunlight_block(
        np.ascontiguousarray(shared["horizons"][:, row0:row1, :]),
        np.ascontiguousarray(shared["longitude"][row0:row1, :]),
        np.ascontiguousarray(shared["latitude"][row0:row1, :]),
        vectors,
        total,
        current_dark,
        longest_dark,
        disk_radius_deg=disk_radius_deg,
        azimuth_origin_deg=azimuth_origin_deg,
    )
    return (row0, row1, *advanced)


def _start_tile_pool(
    horizons: np.ndarray,
    longitude: np.ndarray,
    latitude: np.ndarray,
    workers: int,
):
    """Fork one worker pool that shares the read-only arrays for the whole run."""
    global _TILE_SHARED
    _TILE_SHARED = {
        "horizons": horizons,
        "longitude": longitude,
        "latitude": latitude,
    }
    processes = len(_row_blocks(longitude.shape[0], workers))
    return get_context("fork").Pool(processes=processes)


def _advance_sunlight(
    horizons: np.ndarray,
    longitude: np.ndarray,
    latitude: np.ndarray,
    vectors: np.ndarray,
    total: np.ndarray,
    current_dark: np.ndarray,
    longest_dark: np.ndarray,
    *,
    workers: int,
    disk_radius_deg: float,
    azimuth_origin_deg: float,
    pool=None,
) -> None:
    if len(vectors) == 0:
        return
    if workers == 1 or longitude.shape[0] == 1:
        updated = _advance_sunlight_block(
            horizons,
            longitude,
            latitude,
            vectors,
            total,
            current_dark,
            longest_dark,
            disk_radius_deg=disk_radius_deg,
            azimuth_origin_deg=azimuth_origin_deg,
        )
        total[...] = updated[0]
        current_dark[...] = updated[1]
        longest_dark[...] = updated[2]
        return
    tasks = []
    for row0, row1 in _row_blocks(longitude.shape[0], workers):
        tasks.append(
            (
                row0,
                row1,
                np.ascontiguousarray(vectors),
                np.ascontiguousarray(total[row0:row1, :]),
                np.ascontiguousarray(current_dark[row0:row1, :]),
                np.ascontiguousarray(longest_dark[row0:row1, :]),
                float(disk_radius_deg),
                float(azimuth_origin_deg),
            )
        )
    owned = pool is None
    if owned:
        pool = _start_tile_pool(horizons, longitude, latitude, workers)
    try:
        for row0, row1, tile_total, tile_current, tile_longest in pool.imap_unordered(
            _advance_sunlight_tile, tasks
        ):
            total[row0:row1, :] = tile_total
            current_dark[row0:row1, :] = tile_current
            longest_dark[row0:row1, :] = tile_longest
    finally:
        if owned:
            pool.close()
            pool.join()


def _checkpoint_paths(checkpoint_dir: Path) -> tuple[Path, Path]:
    return (
        checkpoint_dir / "sunlight-state.npz",
        checkpoint_dir / "sunlight-progress.json",
    )


def _load_sunlight_checkpoint(
    checkpoint_dir: Path | None, shape: tuple[int, int], epoch_count: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    total = np.zeros(shape, dtype=np.float64)
    current_dark = np.zeros(shape, dtype=np.int64)
    longest_dark = np.zeros(shape, dtype=np.int64)
    if checkpoint_dir is None:
        return total, current_dark, longest_dark, 0
    state_path, progress_path = _checkpoint_paths(checkpoint_dir)
    if not state_path.is_file() and not progress_path.is_file():
        return total, current_dark, longest_dark, 0
    if not state_path.is_file() or not progress_path.is_file():
        raise ValueError("sunlight checkpoint is incomplete")
    progress = json.loads(progress_path.read_text())
    if int(progress["epoch_count"]) != epoch_count:
        raise ValueError("sunlight checkpoint epoch count does not match")
    next_epoch = int(progress["next_epoch"])
    if not 0 <= next_epoch <= epoch_count:
        raise ValueError("sunlight checkpoint epoch index is invalid")
    with np.load(state_path) as state:
        total = np.array(state["total"], dtype=np.float64, copy=True)
        current_dark = np.array(state["current_dark"], dtype=np.int64, copy=True)
        longest_dark = np.array(state["longest_dark"], dtype=np.int64, copy=True)
        # The state file carries its own epoch index, so a kill between the
        # state and progress writes cannot replay a chunk on resume.
        if "next_epoch" in state.files:
            if int(state["epoch_count"]) != epoch_count:
                raise ValueError("sunlight checkpoint epoch count does not match")
            next_epoch = int(state["next_epoch"])
    if total.shape != shape or current_dark.shape != shape or longest_dark.shape != shape:
        raise ValueError("sunlight checkpoint shape does not match the grid")
    return total, current_dark, longest_dark, next_epoch


def _save_sunlight_checkpoint(
    checkpoint_dir: Path,
    total: np.ndarray,
    current_dark: np.ndarray,
    longest_dark: np.ndarray,
    next_epoch: int,
    epoch_count: int,
) -> None:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    state_path, progress_path = _checkpoint_paths(checkpoint_dir)
    temporary = checkpoint_dir / "sunlight-state.npz.tmp"
    with temporary.open("wb") as handle:
        np.savez(
            handle,
            total=np.ascontiguousarray(total),
            current_dark=np.ascontiguousarray(current_dark),
            longest_dark=np.ascontiguousarray(longest_dark),
            next_epoch=np.int64(next_epoch),
            epoch_count=np.int64(epoch_count),
        )
    os.replace(temporary, state_path)
    _atomic_json(
        progress_path,
        {"next_epoch": int(next_epoch), "epoch_count": int(epoch_count)},
    )


def evaluate_sunlight(
    horizons_deg: np.ndarray,
    lon_deg: np.ndarray,
    lat_deg: np.ndarray,
    sun_vectors: np.ndarray,
    *,
    cadence_hours: float,
    disk_radius_deg: float = 0.27,
    pass_threshold: float = 0.80,
    azimuth_origin_deg: float = 0.0,
    workers: int = 1,
    checkpoint_dir: Path | None = None,
    checkpoint_every: int = 16,
    log_path: Path | None = None,
) -> dict[str, np.ndarray]:
    """Evaluate illumination and longest complete darkness over fixed epochs.

    Percent time lit is the share of timesteps in which any positive fraction
    of the solar disc is above the horizon. The disc radius stays ±0.27°.
    Row tiles are independent because each pixel's darkness streak stays on
    that pixel. workers=1 is the serial reference.
    """
    horizons = np.asarray(horizons_deg)
    longitude = np.asarray(lon_deg, dtype=np.float64)
    latitude = np.asarray(lat_deg, dtype=np.float64)
    vectors = np.asarray(sun_vectors, dtype=np.float64)
    if horizons.ndim != 3 or horizons.shape[1:] != longitude.shape:
        raise ValueError("horizon cube and coordinate grids do not align")
    if longitude.shape != latitude.shape:
        raise ValueError("longitude and latitude grids do not align")
    if vectors.ndim != 2 or vectors.shape[1] != 3 or len(vectors) == 0:
        raise ValueError("sun_vectors must have shape (epochs, 3)")
    if cadence_hours <= 0.0:
        raise ValueError("cadence_hours must be positive")
    if workers <= 0 or workers > 128:
        raise ValueError("workers must be between 1 and 128")
    if checkpoint_every <= 0:
        raise ValueError("checkpoint_every must be positive")

    epoch_count = len(vectors)
    total, current_dark, longest_dark, next_epoch = _load_sunlight_checkpoint(
        Path(checkpoint_dir) if checkpoint_dir else None,
        longitude.shape,
        epoch_count,
    )
    parallel = workers > 1 and longitude.shape[0] > 1 and next_epoch < epoch_count
    pool = _start_tile_pool(horizons, longitude, latitude, workers) if parallel else None
    try:
        while next_epoch < epoch_count:
            stop = min(epoch_count, next_epoch + int(checkpoint_every))
            _advance_sunlight(
                horizons,
                longitude,
                latitude,
                vectors[next_epoch:stop],
                total,
                current_dark,
                longest_dark,
                workers=workers,
                disk_radius_deg=disk_radius_deg,
                azimuth_origin_deg=azimuth_origin_deg,
                pool=pool,
            )
            next_epoch = stop
            if checkpoint_dir is not None:
                _save_sunlight_checkpoint(
                    Path(checkpoint_dir),
                    total,
                    current_dark,
                    longest_dark,
                    next_epoch,
                    epoch_count,
                )
                _log_progress(
                    f"sunlight epochs {next_epoch}/{epoch_count} checkpointed",
                    log_path,
                )
    finally:
        if pool is not None:
            pool.close()
            pool.join()

    percent_time_lit = (100.0 * total / epoch_count).astype(np.float32)
    invalid = (
        ~np.isfinite(longitude)
        | ~np.isfinite(latitude)
        | ~np.isfinite(horizons).any(axis=0)
    )
    percent_time_lit[invalid] = np.nan
    darkness_hours = (longest_dark * float(cadence_hours)).astype(np.float32)
    darkness_hours[invalid] = np.nan
    return {
        "percent_time_lit": percent_time_lit,
        "longest_continuous_darkness_hours": darkness_hours,
        "passes_80_percent": np.where(
            invalid, False, percent_time_lit >= 100.0 * float(pass_threshold)
        ),
    }


def project_one_node_hours(
    *,
    horizon_seconds: float,
    pilot_azimuths: int,
    full_azimuths: int,
    horizon_workers: int,
    eval_seconds: float,
    pilot_epochs: int,
    full_epochs: int,
    spice_seconds: float,
    production_node_hours: float = 14.5,
    stage_node_hour_cap: float = 15.0,
) -> dict[str, float | bool | int]:
    """Project one-node wall time from a pilot sample.

    Horizon waves use the measured wave cost. Epoch and SPICE times are
    already measured with the production worker count, then scaled by epoch
    count. This is a projection, not an illumination result.
    """
    if min(pilot_azimuths, full_azimuths, pilot_epochs, full_epochs, horizon_workers) <= 0:
        raise ValueError("pilot sample sizes must be positive")
    if min(horizon_seconds, eval_seconds, spice_seconds) < 0.0:
        raise ValueError("measured durations must be non-negative")
    pilot_waves = math.ceil(pilot_azimuths / horizon_workers)
    full_waves = math.ceil(full_azimuths / horizon_workers)
    projected_horizon = float(horizon_seconds) * full_waves / pilot_waves
    projected_eval = float(eval_seconds) * full_epochs / pilot_epochs
    projected_spice = float(spice_seconds) * full_epochs / pilot_epochs
    wall_seconds = projected_horizon + projected_eval + projected_spice
    node_hours = wall_seconds / 3600.0
    return {
        "pilot_horizon_waves": pilot_waves,
        "full_horizon_waves": full_waves,
        "projected_horizon_seconds": projected_horizon,
        "projected_eval_seconds": projected_eval,
        "projected_spice_seconds": projected_spice,
        "projected_wall_seconds": wall_seconds,
        "projected_node_hours": node_hours,
        "production_node_hours": float(production_node_hours),
        "stage_node_hour_cap": float(stage_node_hour_cap),
        "fits_production_cap": bool(node_hours <= float(production_node_hours)),
        "fits_stage_cap": bool(node_hours <= float(stage_node_hour_cap)),
    }


def validation_metrics(
    modeled_fraction: np.ndarray,
    measured_fraction: np.ndarray,
    *,
    stop_rmse: float = 0.05,
) -> dict[str, float | bool | int]:
    """Compute the fixed Stage-A gate metrics without modifying the threshold."""
    modeled = np.asarray(modeled_fraction, dtype=np.float64)
    measured = np.asarray(measured_fraction, dtype=np.float64)
    valid = np.isfinite(modeled) & np.isfinite(measured)
    if not valid.any():
        raise ValueError("validation layers have no common valid pixels")
    residual = modeled[valid] - measured[valid]
    rmse = float(np.sqrt(np.mean(residual**2)))
    return {
        "valid_pixels": int(valid.sum()),
        "rmse": rmse,
        "bias": float(np.mean(residual)),
        "scatter": float(np.std(residual)),
        "stop_rmse": float(stop_rmse),
        "passed": bool(rmse <= float(stop_rmse)),
    }
