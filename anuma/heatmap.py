"""PNG heatmaps for the API. Viridis for scores, grey for single layers."""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PIL import Image


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


# Above the 2534 px south-polar grid, so map overlays are the grid pixel for
# pixel: a blank pixel on the map is exactly a cell that fails the slope rule.
# Larger arrays still fall back to nanmax blocks.
PNG_MAX_DIM = 4096
COLOUR_LEVELS = 255
TRANSPARENT_INDEX = 255


def displayed(array: np.ndarray, max_dim: int = PNG_MAX_DIM) -> np.ndarray:
    """The pixels render_png draws: the array itself, or nanmax blocks when
    larger than max_dim."""
    return _downsample(np.asarray(array, dtype=np.float64), max_dim)


def render_png(
    array: np.ndarray,
    cmap: str = "viridis",
    max_dim: int = PNG_MAX_DIM,
    vmin: float | None = None,
    vmax: float | None = None,
) -> bytes:
    """Render a 2-D array as a PNG, one image pixel per array cell.

    NaN / masked pixels are transparent. Without vmin/vmax the colour range is
    the finite min/max after downsampling. Values outside the range take the
    end colours.
    """
    data = _downsample(np.asarray(array, dtype=np.float64), max_dim)
    finite = np.isfinite(data)
    # Palette PNG: 255 colour levels plus one transparent index for NaN.
    # About half the bytes of RGBA and several times faster to encode.
    index = np.full(data.shape, TRANSPARENT_INDEX, dtype=np.uint8)
    if finite.any():
        lo = float(np.min(data[finite])) if vmin is None else float(vmin)
        hi = float(np.max(data[finite])) if vmax is None else float(vmax)
        if lo >= hi:
            hi = lo + 1.0
        scaled = np.clip((data[finite] - lo) / (hi - lo), 0.0, 1.0)
        index[finite] = np.minimum((scaled * COLOUR_LEVELS).astype(np.int64), COLOUR_LEVELS - 1)
    colours = matplotlib.colormaps[cmap](np.linspace(0.0, 1.0, COLOUR_LEVELS), bytes=True)[:, :3]
    palette = np.zeros((256, 3), dtype=np.uint8)
    palette[:COLOUR_LEVELS] = colours
    image = Image.fromarray(index, "P")
    image.putpalette(palette.ravel().tolist())
    alpha = bytes([255] * COLOUR_LEVELS + [0])
    buf = io.BytesIO()
    image.save(buf, format="png", transparency=alpha, compress_level=6)
    return buf.getvalue()


FLAG_INDEX = 254


def render_ramp_png(
    array: np.ndarray,
    flag: np.ndarray,
    *,
    low_rgb: tuple[int, int, int],
    high_rgb: tuple[int, int, int],
    flag_rgb: tuple[int, int, int],
    vmin: float,
    vmax: float,
    max_dim: int = PNG_MAX_DIM,
) -> bytes:
    """A linear two-colour ramp with one extra colour for ``flag`` pixels.

    NaN pixels are transparent; flagged pixels (finite or not) take
    ``flag_rgb``. Values outside [vmin, vmax] take the end colours. One image
    pixel per array cell up to ``max_dim``, like ``render_png``.
    """
    data = _downsample(np.asarray(array, dtype=np.float64), max_dim)
    marks = _downsample(np.where(np.asarray(flag, dtype=bool), 1.0, 0.0), max_dim) > 0.5
    finite = np.isfinite(data)
    levels = FLAG_INDEX
    index = np.full(data.shape, TRANSPARENT_INDEX, dtype=np.uint8)
    if finite.any():
        hi = float(vmax) if float(vmax) > float(vmin) else float(vmin) + 1.0
        scaled = np.clip((data[finite] - float(vmin)) / (hi - float(vmin)), 0.0, 1.0)
        index[finite] = np.minimum((scaled * levels).astype(np.int64), levels - 1)
    index[marks] = FLAG_INDEX
    t = np.linspace(0.0, 1.0, levels)[:, None]
    ramp = np.rint((1.0 - t) * np.array(low_rgb) + t * np.array(high_rgb)).astype(np.uint8)
    palette = np.zeros((256, 3), dtype=np.uint8)
    palette[:levels] = ramp
    palette[FLAG_INDEX] = flag_rgb
    image = Image.fromarray(index, "P")
    image.putpalette(palette.ravel().tolist())
    alpha = bytes([255] * (FLAG_INDEX + 1) + [0])
    buf = io.BytesIO()
    image.save(buf, format="png", transparency=alpha, compress_level=6)
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
