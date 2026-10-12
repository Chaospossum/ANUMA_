"""Static export tests using small constructed NumPy grids."""

from __future__ import annotations

import csv
import io

import numpy as np
import pytest

from anuma.config import load_config
from anuma.grid import Grid
from anuma.render_static import export_figures, load_weights


def _write_layers(grid_dir, shape=(4, 4)):
    grid_dir.mkdir()
    base = np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
    np.save(grid_dir / "slope.npy", np.where(base == base.max(), 20.0, 2.0))
    np.save(grid_dir / "illumination.npy", base)
    np.save(grid_dir / "hydrogen.npy", np.flipud(base))
    np.save(grid_dir / "earth_visibility.npy", np.fliplr(base))


def test_export_figures_from_small_npy_files(tmp_path):
    grid_dir = tmp_path / "grid"
    output_dir = tmp_path / "figures"
    weights_path = tmp_path / "weights.yaml"
    cfg = load_config()
    cfg["projection"]["resolution_m"] = 160_000.0
    grid = Grid(cfg)
    assert (grid.height, grid.width) == (4, 4)
    _write_layers(grid_dir, (grid.height, grid.width))
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
        cfg=cfg,
    )

    assert {path.name for path in written} == {
        "score.png",
        "score_heatmap.png",
        "slope_mask.png",
        "sites.csv",
        "artemis_ranking.csv",
        "illumination.png",
        "hydrogen.png",
        "earth_visibility.png",
    }
    pngs = [path for path in written if path.suffix == ".png"]
    assert all(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for path in pngs)
    sites = list(csv.DictReader(io.StringIO((output_dir / "sites.csv").read_text())))
    assert sites
    assert sites[0]["rank"] == "1"
    regions = list(
        csv.DictReader(io.StringIO((output_dir / "artemis_ranking.csv").read_text()))
    )
    assert len(regions) == 17
    unpublished = [region for region in regions if region["coordinate_status"] == "not_published"]
    pointed = [region for region in regions if region["coordinate_status"] == "approximate_point"]
    assert len(unpublished) == 4
    assert len(pointed) == 13
    assert all(region["rank"] == "" for region in unpublished)
    assert all(region["rank"] != "" for region in pointed)
    assert "best_score" in regions[0]


def test_load_weights_rejects_unknown_public_layer(tmp_path):
    weights_path = tmp_path / "weights.yaml"
    weights_path.write_text("weights:\n  invented_layer: 1.0\n")
    with pytest.raises(ValueError, match="Unknown weighted layer"):
        load_weights(weights_path, load_config())
