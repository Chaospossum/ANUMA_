"""Phase 2 scoring tests on synthetic layers with known optima."""

from __future__ import annotations

import json
from copy import deepcopy

import numpy as np
import pytest

from anuma.config import load_config
from anuma.grid import Grid
from anuma.ingest import write_artemis_geojson
from anuma.score import apply_keepout, artemis_overlap, hard_mask, normalize, rank_sites, score


@pytest.fixture(scope="module")
def grid() -> Grid:
    return Grid()


@pytest.fixture(scope="module")
def cfg() -> dict:
    return load_config()


def _pixel(grid: Grid, lon: float, lat: float) -> tuple[int, int]:
    row, col = grid.to_pixel(lon, lat)
    return int(np.floor(row)), int(np.floor(col))


def _peak_layers(grid: Grid, placements: list[tuple[int, int, float, float]]):
    """Finite only at listed pixels: (row, col, soft_value, slope_deg)."""
    shape = (grid.height, grid.width)
    slope = np.full(shape, np.nan, dtype=np.float32)
    soft = np.full(shape, np.nan, dtype=np.float32)
    for row, col, value, slope_deg in placements:
        slope[row, col] = slope_deg
        soft[row, col] = value
    return {
        "slope": slope,
        "illumination": soft.copy(),
        "hydrogen": soft.copy(),
        "earth_visibility": soft.copy(),
    }


def test_hard_mask_slope_and_nodata(cfg):
    slope = np.array([[np.nan, 0.0], [8.0, 8.1], [20.0, 5.0]], dtype=np.float32)
    limit = float(cfg["hard"]["max_slope_deg"])
    mask = hard_mask(slope, limit, cfg=cfg)
    assert mask.tolist() == [[False, True], [True, False], [False, True]]
    default = hard_mask(slope, cfg=cfg)
    assert np.array_equal(default, mask)
    zero = hard_mask(slope, limit, cfg=cfg, slope_radius_m=0.0)
    assert np.array_equal(zero, mask)


def test_hard_mask_radius_fails_near_steep_or_missing_cells(cfg):
    slope = np.ones((9, 9), dtype=np.float32)
    slope[4, 4] = 20.0
    slope[2, 6] = np.nan

    rook = hard_mask(slope, 8.0, cfg=cfg, slope_radius_m=240.0, resolution_m=240.0)
    assert not rook[4, 4]
    assert not rook[3, 4] and not rook[5, 4] and not rook[4, 3] and not rook[4, 5]
    assert rook[3, 3] and rook[2, 4]
    assert not rook[2, 6] and not rook[1, 6] and not rook[2, 7]
    # Cell centres one step off the array are unknown, so the edge ring fails.
    assert not rook[0, 3] and not rook[8, 3] and not rook[3, 0] and not rook[3, 8]
    assert rook[1, 2]

    wider = hard_mask(slope, 8.0, cfg=cfg, slope_radius_m=340.0, resolution_m=240.0)
    assert not wider[3, 3]
    assert wider[2, 4]
    assert not hard_mask(slope, 8.0, cfg=cfg, slope_radius_m=480.0, resolution_m=240.0)[2, 4]

    finer = hard_mask(slope, 8.0, cfg=cfg, slope_radius_m=30.0, resolution_m=20.0)
    assert not finer[3, 4]
    assert finer[2, 4]
    assert not hard_mask(slope, 8.0, cfg=cfg, slope_radius_m=40.0, resolution_m=20.0)[2, 4]


def test_hard_mask_radius_rejects_bad_input(cfg):
    slope = np.ones((3, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="non-negative"):
        hard_mask(slope, 8.0, cfg=cfg, slope_radius_m=-1.0, resolution_m=240.0)
    with pytest.raises(ValueError, match="resolution_m"):
        hard_mask(slope, 8.0, cfg=cfg, slope_radius_m=240.0)


def test_score_slope_radius_uses_grid_resolution(grid, cfg):
    row, col = _pixel(grid, 0.0, -87.0)
    shape = (grid.height, grid.width)
    slope = np.full(shape, np.nan, dtype=np.float32)
    soft = np.full(shape, np.nan, dtype=np.float32)
    slope[row - 3 : row + 4, col - 3 : col + 4] = 1.0
    soft[row - 3 : row + 4, col - 3 : col + 4] = 50.0
    slope[row, col + 2] = 20.0
    layers = {
        "slope": slope,
        "illumination": soft.copy(),
        "hydrogen": soft.copy(),
        "earth_visibility": soft.copy(),
    }
    weights = cfg["scoring"]["weights"]
    for radius, passes in ((0.0, True), (240.0, True), (480.0, False)):
        hard = {"max_slope_deg": 8.0, "slope_radius_m": radius}
        result = score(layers, weights, hard, grid=grid, cfg=cfg)
        assert bool(result.hard_mask[row, col]) is passes
        assert bool(np.isfinite(result.combined[row, col])) is passes


def test_normalize_higher_and_lower(cfg):
    layer = np.arange(100, dtype=np.float32).reshape(10, 10)
    high = normalize(
        layer,
        "higher_better",
        layer_name="illumination",
        policy="legacy_global_2_98",
        cfg=cfg,
    )
    low = normalize(
        layer,
        "lower_better",
        layer_name="illumination",
        policy="legacy_global_2_98",
        cfg=cfg,
    )
    assert 0.0 <= float(np.nanmin(high)) <= float(np.nanmax(high)) <= 1.0
    assert high[0, 0] < high[5, 0] < high[-1, -1]
    assert float(high[-1, -1]) == 1.0
    assert float(low[-1, -1]) == 0.0
    assert float(low[0, 0]) == 1.0
    np.testing.assert_allclose(high + low, 1.0, atol=1e-6)


def test_decided_policy_preserves_illumination_bright_tail(cfg):
    illumination = np.array([0.0, 0.1, 0.49, 0.93], dtype=np.float32)
    decided = normalize(
        illumination,
        "higher_better",
        layer_name="illumination",
        policy="decided_per_layer",
        cfg=cfg,
    )
    legacy = normalize(
        illumination,
        "higher_better",
        layer_name="illumination",
        policy="legacy_global_2_98",
        cfg=cfg,
    )
    assert decided[-1] == 1.0
    assert decided[-2] < 1.0
    assert legacy[-2] > decided[-2]


def test_decided_slope_min_max_uses_only_hard_mask(cfg):
    slope = np.array([0.0, 4.0, 8.0, 40.0], dtype=np.float32)
    mask = hard_mask(slope, cfg=cfg)
    normalized = normalize(
        slope,
        "lower_better",
        layer_name="slope",
        policy="decided_per_layer",
        valid_mask=mask,
        cfg=cfg,
    )
    np.testing.assert_allclose(normalized[:3], [1.0, 0.5, 0.0])
    assert np.isnan(normalized[3])


def test_decided_hydrogen_retains_percentile_clipping(cfg):
    hydrogen = np.arange(101, dtype=np.float32)
    hydrogen[-1] = 10_000.0
    normalized = normalize(
        hydrogen,
        "higher_better",
        layer_name="hydrogen",
        policy="decided_per_layer",
        cfg=cfg,
    )
    assert normalized[-1] == 1.0
    assert normalized[-2] == 1.0


def test_rank_sites_finds_known_optima_and_separation(grid, cfg):
    xa, ya = grid.fwd.transform(0.0, -87.0)
    lon_a, lat_a = 0.0, -87.0
    lon_close, lat_close = grid.inv.transform(xa + 5_000.0, ya)
    lon_far, lat_far = grid.inv.transform(xa, ya + 50_000.0)
    ra, ca = _pixel(grid, lon_a, lat_a)
    rc, cc = _pixel(grid, float(lon_close), float(lat_close))
    rf, cf = _pixel(grid, float(lon_far), float(lat_far))
    assert (ra, ca) != (rc, cc)
    assert (ra, ca) != (rf, cf)

    layers = _peak_layers(
        grid,
        [
            (ra, ca, 100.0, 1.0),
            (rc, cc, 95.0, 1.0),
            (rf, cf, 90.0, 1.0),
            (_pixel(grid, 45.0, -87.0)[0], _pixel(grid, 45.0, -87.0)[1], 100.0, 20.0),
        ],
    )
    result = score(layers, cfg["scoring"]["weights"], cfg["hard"], grid=grid, cfg=cfg)
    assert result.hard_mask[ra, ca]
    assert not result.hard_mask[_pixel(grid, 45.0, -87.0)]

    sep = float(cfg["scoring"]["min_separation_km"])
    sites = rank_sites(result, n=3, min_separation_km=sep)
    assert len(sites) == 2
    assert (sites[0]["row"], sites[0]["col"]) == (ra, ca)
    assert (sites[1]["row"], sites[1]["col"]) == (rf, cf)
    x0, y0 = grid.fwd.transform(sites[0]["lon"], sites[0]["lat"])
    x1, y1 = grid.fwd.transform(sites[1]["lon"], sites[1]["lat"])
    dist_km = float(np.hypot(x1 - x0, y1 - y0) / 1000.0)
    assert dist_km >= sep - 1e-6


def test_contributions_sum_to_total(grid, cfg):
    row, col = _pixel(grid, 10.0, -86.0)
    layers = _peak_layers(grid, [(row, col, 50.0, 2.0)])
    result = score(layers, cfg["scoring"]["weights"], cfg["hard"], grid=grid, cfg=cfg)
    sites = rank_sites(result, n=1)
    assert len(sites) == 1
    site = sites[0]
    assert site["contributions"].keys() == result.weights.keys()
    np.testing.assert_allclose(
        sum(site["contributions"].values()), site["total"], atol=1e-5
    )


def test_negative_weight_is_rejected(grid, cfg):
    row, col = _pixel(grid, 10.0, -86.0)
    layers = _peak_layers(grid, [(row, col, 50.0, 2.0)])
    with pytest.raises(ValueError, match="finite and non-negative"):
        score(layers, {"illumination": -0.01}, grid=grid, cfg=cfg)


def test_rank_ties_use_slope_then_raw_illumination(cfg):
    small_cfg = deepcopy(cfg)
    small_cfg["projection"]["resolution_m"] = 2000.0
    small_cfg["projection"]["lat_max"] = -88.0
    small_grid = Grid(small_cfg)
    shape = (small_grid.height, small_grid.width)
    illumination = np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
    slope = np.ones(shape, dtype=np.float32)
    layers = {"slope": slope, "illumination": illumination}

    # Percentile clipping makes many high-valued pixels share score 1. Lower
    # raw slope must win even when another tied pixel has higher illumination.
    lower_slope = np.unravel_index(illumination.size - 2, shape)
    slope[lower_slope] = 0.5
    result = score(
        layers,
        {"illumination": 1.0},
        {"max_slope_deg": 8.0},
        grid=small_grid,
        cfg=small_cfg,
        normalization_policy="legacy_global_2_98",
    )
    sites = rank_sites(result, n=1)
    assert (sites[0]["row"], sites[0]["col"]) == lower_slope

    # With equal slopes, higher un-clipped illumination is the secondary key,
    # so selection is independent of the first tied array index.
    slope[lower_slope] = 1.0
    result = score(
        layers,
        {"illumination": 1.0},
        {"max_slope_deg": 8.0},
        grid=small_grid,
        cfg=small_cfg,
        normalization_policy="legacy_global_2_98",
    )
    sites = rank_sites(result, n=1)
    highest_raw = np.unravel_index(illumination.size - 1, shape)
    assert (sites[0]["row"], sites[0]["col"]) == highest_raw


def _box_around(grid: Grid, x: float, y: float, half_m: float = 7500.0) -> list[list[float]]:
    corners = [
        (x - half_m, y - half_m),
        (x + half_m, y - half_m),
        (x + half_m, y + half_m),
        (x - half_m, y + half_m),
        (x - half_m, y - half_m),
    ]
    ring = []
    for cx, cy in corners:
        lon, lat = grid.inv.transform(cx, cy)
        ring.append([float(lon), float(lat)])
    return ring


def test_artemis_overlap_synthetic_geometries(grid, cfg):
    xa, ya = grid.fwd.transform(0.0, -87.0)
    row, col = _pixel(grid, 0.0, -87.0)
    layers = _peak_layers(grid, [(row, col, 80.0, 1.0)])
    result = score(layers, cfg["scoring"]["weights"], cfg["hard"], grid=grid, cfg=cfg)
    sites = rank_sites(result, n=1)

    features = []
    for i in range(9):
        if i == 0:
            x, y = xa, ya
            name = "synthetic-peak"
        else:
            x, y = xa + 40_000.0 * np.cos(i), ya + 40_000.0 * np.sin(i)
            name = f"synthetic-{i}"
        features.append(
            {
                "type": "Feature",
                "properties": {"name": name},
                "geometry": {"type": "Polygon", "coordinates": [_box_around(grid, x, y)]},
            }
        )
    geojson = {"type": "FeatureCollection", "features": features}
    overlap = artemis_overlap(sites, geojson, result=result)
    assert overlap["sites"][0]["nearest"] == "synthetic-peak"
    assert overlap["sites"][0]["distance_km"] == 0.0
    ranked = {r["name"]: r for r in overlap["regions"]}
    assert ranked["synthetic-peak"]["rank"] == 1
    assert ranked["synthetic-peak"]["mean_score"] > 0.0
    assert ranked["synthetic-peak"]["best_score"] >= ranked["synthetic-peak"]["mean_score"]
    assert len(overlap["regions"]) == 9


def test_artemis_null_geometries_do_not_crash(grid, cfg):
    row, col = _pixel(grid, 0.0, -88.0)
    layers = _peak_layers(grid, [(row, col, 40.0, 1.0)])
    result = score(layers, cfg["scoring"]["weights"], cfg["hard"], grid=grid, cfg=cfg)
    sites = rank_sites(result, n=1)
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "unnamed"},
                "geometry": None,
            }
        ],
    }
    overlap = artemis_overlap(sites, geojson, result=result)
    assert len(overlap["sites"]) == 1
    assert overlap["sites"][0]["nearest"] is None
    assert overlap["sites"][0]["distance_km"] is None
    assert overlap["regions"][0]["rank"] is None
    assert not np.isfinite(overlap["regions"][0]["mean_score"])
    assert not np.isfinite(overlap["regions"][0]["best_score"])


def test_cited_artemis_points_and_measured_winner(grid, cfg, tmp_path):
    dest = tmp_path / "artemis3_regions.geojson"
    write_artemis_geojson(cfg, dest)
    data = json.loads(dest.read_text())
    names = [region["name"] for region in cfg["artemis3"]["regions"]]
    assert [feat["properties"]["name"] for feat in data["features"]] == names
    pointed = [feat for feat in data["features"] if feat["geometry"] is not None]
    empty = [feat for feat in data["features"] if feat["geometry"] is None]
    assert len(pointed) == 13
    assert {feat["properties"]["name"] for feat in empty} == {
        "Peak near Cabeus B",
        "Mons Mouton Plateau",
        "Mons Mouton",
        "Slater Plain",
    }
    assert sum("2022" in feat["properties"]["lists"] for feat in data["features"]) == 13
    assert sum("2024" in feat["properties"]["lists"] for feat in data["features"]) == 9
    ridge = next(feat for feat in pointed if feat["properties"]["name"] == "Connecting Ridge")
    assert ridge["properties"]["lon_east"] == "222.51"
    assert ridge["properties"]["lat"] == "-89.463"
    assert ridge["properties"]["approximate"] is True
    assert "2024" not in ridge["properties"]["lists"]
    rim = next(feat for feat in pointed if feat["properties"]["name"] == "de Gerlache Rim 2")
    assert "2024" in rim["properties"]["lists"]

    winner = [{"lon": -137.3, "lat": -89.45, "total": None}]
    overlap = artemis_overlap(winner, data)
    assert overlap["sites"][0]["nearest"] == "Connecting Ridge"
    distance = overlap["sites"][0]["distance_km"]
    assert distance is not None and 0.0 < distance < 5.0
    by_name = {row["name"]: row for row in overlap["regions"]}
    assert by_name["Slater Plain"]["rank"] is None
    assert not np.isfinite(by_name["Connecting Ridge"]["best_score"])


def test_keepout_requires_radius_and_can_mask_or_penalise(grid, cfg):
    row, col = _pixel(grid, 0.0, -87.0)
    row_b, col_b = _pixel(grid, 0.0, -86.0)
    layers = _peak_layers(
        grid,
        [(row, col, 100.0, 1.0), (row_b, col_b, 40.0, 1.0)],
    )
    result = score(layers, cfg["scoring"]["weights"], cfg["hard"], grid=grid, cfg=cfg)
    with pytest.raises(ValueError, match="radius_km"):
        apply_keepout(result, [(0.0, -87.0)], None, "hard")
    with pytest.raises(ValueError, match="penalty"):
        apply_keepout(result, [(0.0, -87.0)], 1.0, "penalty")
    masked = apply_keepout(result, [(0.0, -87.0)], 1.0, "hard")
    assert not np.isfinite(masked.combined[row, col])
    sites = rank_sites(masked, n=1)
    assert (sites[0]["row"], sites[0]["col"]) == (row_b, col_b)
    penalised = apply_keepout(result, [(0.0, -87.0)], 1.0, "penalty", penalty=0.25)
    assert np.isfinite(penalised.combined[row, col])
    assert float(penalised.combined[row, col]) < float(result.combined[row, col])
    assert float(penalised.combined[row_b, col_b]) == float(result.combined[row_b, col_b])


def test_rank_sites_small_separation_keeps_close_sites(grid, cfg):
    # Two passing peaks 3 pixels (720 m) apart: one site at 1 km, two at 0.5 km.
    r, c = _pixel(grid, 0.0, -87.0)
    layers = _peak_layers(grid, [(r, c, 100.0, 1.0), (r, c + 3, 95.0, 1.0)])
    result = score(layers, cfg["scoring"]["weights"], cfg["hard"], grid=grid, cfg=cfg)
    assert len(rank_sites(result, n=5, min_separation_km=1.0)) == 1
    assert len(rank_sites(result, n=5, min_separation_km=0.5)) == 2
