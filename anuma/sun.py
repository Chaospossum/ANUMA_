"""Terrain-horizon Sun model with externally supplied DE440 geometry.

The terrain horizon is computed once and reused for every epoch.  Orbit and
frame geometry is delegated to SPICE; this module contains no orbital model.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
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


def compute_horizons(
    dem: np.ndarray,
    resolution_m: float,
    *,
    moon_radius_m: float = 1_737_400.0,
    azimuth_count: int = 360,
    azimuth_origin_deg: float = 0.0,
    workers: int = 1,
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

    def calculate(azimuth: float) -> np.ndarray:
        return horizon_for_azimuth(
            dem, resolution_m, moon_radius_m, float(azimuth)
        )

    if workers == 1:
        layers = [calculate(float(azimuth)) for azimuth in azimuths]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            # map preserves input order, so worker scheduling cannot alter output.
            layers = list(executor.map(calculate, azimuths))
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
) -> dict[str, np.ndarray]:
    """Evaluate illumination and longest complete darkness over fixed epochs.

    Percent time lit is the share of timesteps in which any positive fraction
    of the solar disc is above the horizon. The disc radius stays ±0.27°.
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

    lon = np.deg2rad(longitude)
    lat = np.deg2rad(latitude)
    east = np.stack((-np.sin(lon), np.cos(lon), np.zeros_like(lon)))
    north = np.stack(
        (-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat))
    )
    up = np.stack(
        (np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat))
    )

    total = np.zeros(longitude.shape, dtype=np.float64)
    current_dark = np.zeros(longitude.shape, dtype=np.int64)
    longest_dark = np.zeros(longitude.shape, dtype=np.int64)
    azimuth_count = horizons.shape[0]
    for vector in vectors:
        east_component = np.einsum("i,ijk->jk", vector, east)
        north_component = np.einsum("i,ijk->jk", vector, north)
        up_component = np.einsum("i,ijk->jk", vector, up)
        azimuth = np.mod(
            np.rad2deg(np.arctan2(east_component, north_component)), 360.0
        )
        index = np.rint(
            np.mod(azimuth - azimuth_origin_deg, 360.0)
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
        lit = visible_fraction > 0.0
        total += lit
        dark = ~lit
        current_dark = np.where(dark, current_dark + 1, 0)
        longest_dark = np.maximum(longest_dark, current_dark)

    percent_time_lit = (100.0 * total / len(vectors)).astype(np.float32)
    invalid = ~np.isfinite(longitude) | ~np.isfinite(latitude) | ~np.isfinite(horizons).any(axis=0)
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
