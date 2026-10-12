"""Deterministic tests for the first 240 m HPC study."""

from __future__ import annotations

from copy import deepcopy

import numpy as np

from anuma.config import load_config
from anuma.hpc_monte_carlo import draw_weights, run_study


class TinyGrid:
    resolution_m = 240.0

    @staticmethod
    def to_lonlat(row, col):
        return float(col), float(-90.0 + row / 100.0)


def _layers():
    base = np.arange(16, dtype=np.float32).reshape(4, 4)
    return {
        "dem": base,
        "slope": np.array(
            [
                [1.0, 2.0, 3.0, 4.0],
                [2.0, 3.0, 4.0, 5.0],
                [3.0, 4.0, 5.0, 6.0],
                [4.0, 5.0, 6.0, 9.0],
            ],
            dtype=np.float32,
        ),
        "illumination": base / 16.0,
        "hydrogen": np.flipud(base) * 10.0,
        "earth_visibility": np.fliplr(base),
    }


def test_weight_draws_are_deterministic_and_normalized():
    cfg = load_config()
    first = draw_weights(cfg, 4)
    second = draw_weights(cfg, 4)
    assert first == second
    for draw in first:
        np.testing.assert_allclose(sum(draw.values()), 1.0)


def test_tiny_monte_carlo_is_deterministic():
    cfg = deepcopy(load_config())
    cfg["scoring"]["n_sites"] = 3
    cfg["scoring"]["min_separation_km"] = 0.24
    first, first_draws = run_study(
        _layers(), TinyGrid(), cfg, mode="pilot", draw_count=4
    )
    second, second_draws = run_study(
        _layers(), TinyGrid(), cfg, mode="pilot", draw_count=4
    )
    assert first == second
    assert first_draws == second_draws
    assert first["draw_count"] == 4
    assert set(first["policies"]) == {
        "legacy_global_2_98",
        "decided_per_layer",
        "all_min_max",
    }
    comparison = first["clipped_vs_unclipped_winner_class"]
    assert 0 <= comparison["changed_draws"] <= 4
    assert 0.0 <= comparison["changed_frequency"] <= 1.0
    for result in first["policies"].values():
        assert result["winner_stability"]["most_frequent_winner"]["count"] >= 1
