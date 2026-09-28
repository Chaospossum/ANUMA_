"""PNG heatmaps for the API. Viridis for scores, grey for single layers."""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure


def _downsample(array: np.ndarray, max_dim: int) -> np.ndarray:
    """Block-reduce with nanmax so isolated peaks survive."""
    h, w = array.shape
    if max(h, w) <= max_dim:
        return array
    factor = int(np.ceil(max(h, w) / float(max_dim)))
    nh = (h // factor) * factor
    nw = (w // factor) * factor
    if nh == 0 or nw == 0:
        return array
    blocks = array[:nh, :nw].reshape(nh // factor, factor, nw // factor, factor)
    valid = np.isfinite(blocks)
    filled = np.where(valid, blocks, -np.inf)
    reduced = filled.max(axis=(1, 3))
    reduced = np.where(valid.any(axis=(1, 3)), reduced, np.nan)
    return reduced


def render_png(
    array: np.ndarray,
    cmap: str = "viridis",
    max_dim: int = 1024,
) -> bytes:
    """Render a 2-D array as a tight PNG. NaN / masked pixels are transparent."""
    data = _downsample(np.asarray(array, dtype=np.float64), max_dim)
    finite = np.isfinite(data)
    h, w = data.shape
    dpi = 100.0
    fig = Figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0.0, 0.0, 1.0, 1.0), frameon=False)
    ax.set_axis_off()
    if finite.any():
        vmin = float(np.min(data[finite]))
        vmax = float(np.max(data[finite]))
        if vmin == vmax:
            vmax = vmin + 1.0
        cmap_obj = matplotlib.colormaps[cmap].with_extremes(bad=(0.0, 0.0, 0.0, 0.0))
        masked = np.ma.masked_invalid(data)
        ax.imshow(
            masked,
            cmap=cmap_obj,
            origin="upper",
            interpolation="nearest",
            vmin=vmin,
            vmax=vmax,
            aspect="equal",
        )
    else:
        ax.imshow(
            np.zeros((h, w, 4), dtype=np.float32),
            origin="upper",
            interpolation="nearest",
            aspect="equal",
        )
    ax.set_xlim(-0.5, w - 0.5)
    ax.set_ylim(h - 0.5, -0.5)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True, pad_inches=0)
    return buf.getvalue()
