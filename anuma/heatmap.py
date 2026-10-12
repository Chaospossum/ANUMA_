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


def render_ranked_png(
    array: np.ndarray,
    sites: list[dict],
    cmap: str = "viridis",
    max_dim: int = 1600,
) -> bytes:
    """Render a score heatmap with numbered ranked-site markers."""
    original = np.asarray(array, dtype=np.float64)
    data = _downsample(original, max_dim)
    finite = np.isfinite(data)
    h, w = data.shape
    fig = Figure(figsize=(10, 10), dpi=160)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(1, 1, 1)
    cmap_obj = matplotlib.colormaps[cmap].with_extremes(bad="#f7f7f7")
    if finite.any():
        image = ax.imshow(
            np.ma.masked_invalid(data),
            cmap=cmap_obj,
            origin="upper",
            interpolation="nearest",
            vmin=float(np.min(data[finite])),
            vmax=float(np.max(data[finite])),
        )
        fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04, label="weighted score")
    row_scale = h / original.shape[0]
    col_scale = w / original.shape[1]
    for rank, site in enumerate(sites, start=1):
        x = (float(site["col"]) + 0.5) * col_scale - 0.5
        y = (float(site["row"]) + 0.5) * row_scale - 0.5
        ax.annotate(
            str(rank),
            (x, y),
            ha="center",
            va="center",
            fontsize=7,
            fontweight="bold",
            color="#161921",
            bbox={"boxstyle": "circle,pad=0.25", "fc": "white", "ec": "#161921", "lw": 0.8},
        )
    ax.set_title("ANUMA ranked landing-site screening")
    ax.set_xlabel("grid column")
    ax.set_ylabel("grid row")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return buf.getvalue()
