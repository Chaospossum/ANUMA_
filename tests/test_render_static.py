"""Static export tests using small constructed NumPy grids."""

from __future__ import annotations

import numpy as np
import pytest

from anuma.config import load_config
from anuma.render_static import export_figures, load_weights


def _write_layers(grid_dir):
    grid_dir.mkdir()
    base = np.arange(16, dtype=np.float32).reshape(4, 4)
    np.save(grid_dir / "slope.npy", np.where(base == 15, 20.0, 2.0))
    np.save(grid_dir / "illumination.npy", base)
    np.save(grid_dir / "hydrogen.npy", np.flipud(base))
    np.save(grid_dir / "earth_visibility.npy", np.fliplr(base))


def test_export_figures_from_small_npy_files(tmp_path):
    grid_dir = tmp_path / "grid"
    output_dir = tmp_path / "figures"
    weights_path = tmp_path / "weights.yaml"
    _write_layers(grid_dir)
    weights_path.write_text(
        "weights:\n"
        "  illumination: 0.5\n"
        "  hydrogen: 0.3\n"
        "  earth_visibility: 0.2\n"
    )

    written = export_figures(
        weights_path=weights_path,
        grid_dir=grid_dir,
        output_dir=output_dir,
        cfg=load_config(),
    )

    assert {path.name for path in written} == {
        "score.png",
        "illumination.png",
        "hydrogen.png",
        "earth_visibility.png",
    }
    assert all(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for path in written)


def test_load_weights_rejects_unknown_public_layer(tmp_path):
    weights_path = tmp_path / "weights.yaml"
    weights_path.write_text("weights:\n  invented_layer: 1.0\n")
    with pytest.raises(ValueError, match="Unknown weighted layer"):
        load_weights(weights_path, load_config())
