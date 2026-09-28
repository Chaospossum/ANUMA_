"""3x3 Horn slope in degrees on a projected grid."""

import numpy as np
from scipy.ndimage import convolve


def horn_slope_deg(z: np.ndarray, cellsize_m: float) -> np.ndarray:
    """Return terrain slope in degrees from a 3x3 Horn gradient.

    `z` is height in metres on a regular projected grid. NaN in the 3x3
    neighbourhood yields NaN at that pixel.
    """
    finite = np.isfinite(z)
    filled = np.where(finite, z, 0.0).astype(np.float64)
    kx = np.array([[-1.0, 0.0, 1.0], [-2.0, 0.0, 2.0], [-1.0, 0.0, 1.0]])
    ky = np.array([[1.0, 2.0, 1.0], [0.0, 0.0, 0.0], [-1.0, -2.0, -1.0]])
    dzdx = convolve(filled, kx, mode="nearest") / (8.0 * cellsize_m)
    dzdy = convolve(filled, ky, mode="nearest") / (8.0 * cellsize_m)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    weight = np.ones_like(filled)
    weight[~finite] = 0.0
    n_finite = convolve(weight, np.ones((3, 3)), mode="constant", cval=0.0)
    slope = np.where((n_finite == 9.0) & finite, slope, np.nan)
    return slope.astype(np.float32)
