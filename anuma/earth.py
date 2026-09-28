"""Direct-to-Earth visibility from a south-polar DEM.

Approximation, not a full SPICE/libration model:

* Sub-Earth is taken as (lon 0°, lat 0°) in the ME frame.
* Earth elevation at a pixel is asin(cos(lat) * cos(lon)), i.e. a distant
  Earth along +X, no angular diameter.
* Horizon elevation is the maximum terrain elevation angle along a ray of
  `ray_length_m` toward that sub-Earth point, stepped in the stereographic
  plane. Lunar curvature is included by converting each sample to 3D.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import map_coordinates

from anuma.grid import Grid


def _stereo_to_lonlat(x: np.ndarray, y: np.ndarray, radius: float):
    """South-polar stereographic (lon_0=0) to lon/lat. Matches PROJ within 1e-12 deg."""
    rho = np.hypot(x, y)
    angular = 2.0 * np.arctan(rho / (2.0 * radius))
    lat = np.degrees(-np.pi / 2.0 + angular)
    lon = np.degrees(np.arctan2(x, y))
    lon = np.where(rho == 0.0, 0.0, lon)
    return lon, lat


def _xyz(lon: np.ndarray, lat: np.ndarray, height: np.ndarray, radius: float):
    lonr = np.radians(lon)
    latr = np.radians(lat)
    r = radius + height
    clat = np.cos(latr)
    return r * clat * np.cos(lonr), r * clat * np.sin(lonr), r * np.sin(latr)


def earth_visibility(
    dem: np.ndarray, grid: Grid, vis_cfg: dict
) -> np.ndarray:
    """Return Earth clearance in degrees (Earth elevation minus horizon)."""
    x, y = grid.pixel_centers()
    lon, lat = _stereo_to_lonlat(x, y, grid.radius_m)
    height = np.where(np.isfinite(dem), dem, 0.0).astype(np.float64)

    target_x, target_y = grid.fwd.transform(
        float(vis_cfg["sub_earth_lon"]), float(vis_cfg["sub_earth_lat"])
    )
    dx = target_x - x
    dy = target_y - y
    horiz = np.hypot(dx, dy)
    horiz = np.maximum(horiz, 1.0)
    ux, uy = dx / horiz, dy / horiz

    ox, oy, oz = _xyz(lon, lat, height, grid.radius_m)
    zenith = np.stack([ox, oy, oz], axis=-1)
    zenith /= np.maximum(np.linalg.norm(zenith, axis=-1, keepdims=True), 1e-9)

    step = float(vis_cfg["ray_step_m"])
    length = float(vis_cfg["ray_length_m"])
    nsteps = max(1, int(round(length / step)))
    max_elev = np.full(dem.shape, -90.0, dtype=np.float64)

    for k in range(1, nsteps + 1):
        dist = k * step
        xs = x + ux * dist
        ys = y + uy * dist
        cols = (xs - grid.affine.c) / grid.affine.a
        rows = (ys - grid.affine.f) / grid.affine.e
        zs = map_coordinates(height, [rows, cols], order=1, mode="nearest")
        slon, slat = _stereo_to_lonlat(xs, ys, grid.radius_m)
        sx, sy, sz = _xyz(slon, slat, zs, grid.radius_m)
        look = np.stack([sx - ox, sy - oy, sz - oz], axis=-1)
        look /= np.maximum(np.linalg.norm(look, axis=-1, keepdims=True), 1e-9)
        cosz = np.clip(np.sum(look * zenith, axis=-1), -1.0, 1.0)
        elev = 90.0 - np.degrees(np.arccos(cosz))
        max_elev = np.maximum(max_elev, elev)

    earth_el = np.degrees(
        np.arcsin(np.clip(np.cos(np.radians(lat)) * np.cos(np.radians(lon)), -1.0, 1.0))
    )
    vis = earth_el - max_elev
    return np.where(np.isfinite(dem), vis, np.nan).astype(np.float32)
