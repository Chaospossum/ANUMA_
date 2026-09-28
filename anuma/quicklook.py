"""Greyscale quicklook PNGs."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def write_quicklook(array: np.ndarray, path: Path, cmap: str = "gray") -> None:
    """Write a tight greyscale PNG with no axes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(4.5, 4.5), dpi=120)
    finite = array[np.isfinite(array)]
    vmin = float(np.percentile(finite, 2)) if finite.size else 0.0
    vmax = float(np.percentile(finite, 98)) if finite.size else 1.0
    if vmin == vmax:
        vmax = vmin + 1.0
    ax.imshow(array, cmap=cmap, origin="upper", vmin=vmin, vmax=vmax)
    ax.set_axis_off()
    fig.savefig(path, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
