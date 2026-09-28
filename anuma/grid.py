"""Common Moon south-polar stereographic grid."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from affine import Affine
from pyproj import CRS, Transformer

from anuma.config import load_config


class Grid:
    """240 m south-polar stereographic grid covering 80°S–90°S."""

    def __init__(self, cfg: dict | None = None):
        cfg = cfg if cfg is not None else load_config()
        proj = cfg["projection"]
        self.proj4 = proj["proj4"]
        self.geodetic_proj4 = proj["geodetic_proj4"]
        self.radius_m = float(proj["radius_m"])
        self.resolution_m = float(proj["resolution_m"])
        self.lat_min = float(proj["lat_min"])
        self.lat_max = float(proj["lat_max"])
        self.crs = CRS.from_proj4(self.proj4)
        self.geodetic_crs = CRS.from_proj4(self.geodetic_proj4)
        self.fwd = Transformer.from_crs(
            self.geodetic_crs, self.crs, always_xy=True
        )
        self.inv = Transformer.from_crs(
            self.crs, self.geodetic_crs, always_xy=True
        )
        rho = self._circle_radius_m(self.lat_max)
        n = int(np.ceil(2.0 * rho / self.resolution_m))
        self.width = n
        self.height = n
        half = n * self.resolution_m / 2.0
        self.xmin = -half
        self.ymax = half
        self.xmax = half
        self.ymin = -half
        self.affine = Affine(
            self.resolution_m, 0.0, self.xmin, 0.0, -self.resolution_m, self.ymax
        )

    def _circle_radius_m(self, lat: float) -> float:
        x, y = self.fwd.transform(0.0, lat)
        return float(np.hypot(x, y))

    def to_pixel(self, lon: float, lat: float) -> tuple[float, float]:
        """Return (row, col) in pixel coordinates from the UL corner."""
        x, y = self.fwd.transform(lon, lat)
        col, row = ~self.affine * (x, y)
        return float(row), float(col)

    def to_lonlat(self, row: float, col: float) -> tuple[float, float]:
        """Return (lon, lat) in degrees for a pixel coordinate."""
        x, y = self.affine * (col, row)
        lon, lat = self.inv.transform(x, y)
        return float(lon), float(lat)

    def pixel_centers(self) -> tuple[np.ndarray, np.ndarray]:
        """Projected (x, y) of every pixel centre, shape (height, width)."""
        cols = np.arange(self.width, dtype=np.float64) + 0.5
        rows = np.arange(self.height, dtype=np.float64) + 0.5
        # x = xmin + col * res; y = ymax + row * (-res)
        x = self.affine.c + cols * self.affine.a
        y = self.affine.f + rows * self.affine.e
        return np.meshgrid(x, y)

    def lonlat_arrays(self) -> tuple[np.ndarray, np.ndarray]:
        x, y = self.pixel_centers()
        lon, lat = self.inv.transform(x, y)
        return np.asarray(lon), np.asarray(lat)

    def inside_latitude(self, lat_cutoff: float) -> np.ndarray:
        """True where geodetic latitude (deg) is at or poleward of lat_cutoff."""
        _, lat = self.lonlat_arrays()
        return lat <= lat_cutoff

    def to_dict(self) -> dict:
        a = self.affine
        return {
            "proj4": self.proj4,
            "radius_m": self.radius_m,
            "resolution_m": self.resolution_m,
            "width": self.width,
            "height": self.height,
            "xmin": self.xmin,
            "ymin": self.ymin,
            "xmax": self.xmax,
            "ymax": self.ymax,
            "transform": [a.a, a.b, a.c, a.d, a.e, a.f],
        }

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def from_json(cls, path: Path, cfg: dict | None = None) -> "Grid":
        """Rebuild a Grid and check it matches the saved transform."""
        meta = json.loads(Path(path).read_text())
        grid = cls(cfg)
        if [grid.width, grid.height] != [meta["width"], meta["height"]]:
            raise ValueError("Saved grid.json does not match current config.")
        return grid
