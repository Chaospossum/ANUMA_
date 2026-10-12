"""Stage B acceptance gate, sampling, agreement map, and rank table."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml
from affine import Affine

from anuma.config import load_config
from anuma.grid import Grid
from anuma.stage_b import (
    FROZEN_STOP_RMSE,
    agreement_map,
    build_rank_table,
    load_demo_weights,
    load_stage_b_config,
    require_stage_a_acceptance,
    run_pilot,
    sample_coarse_to_fine,
)

REPO = Path(__file__).resolve().parents[1]
DEM_PATH = REPO / "data" / "raw" / "LDEM_80S_20MPP_ADJ.TIF"


def test_stage_b_config_keeps_frozen_gates_and_budget():
    bcfg = load_stage_b_config()
    layers = load_config()
    weights = load_demo_weights()
    assert bcfg["stage_a_gate"]["stop_rmse"] == FROZEN_STOP_RMSE == 0.05
    assert float(layers["hard"]["max_slope_deg"]) == 8.0
    assert layers["scoring"]["default_normalization_policy"] == "decided_per_layer"
    assert weights == {
        "illumination": 0.5,
        "hydrogen": 0.3,
        "earth_visibility": 0.2,
    }
    assert weights == layers["scoring"]["weights"]
    exe = bcfg["execution"]
    assert exe["memory"] == "240G"
    assert exe["maximum_workers"] == 128
    assert exe["pilot_node_hours"] + exe["production_node_hours"] <= exe["stage_node_hour_cap"]
    assert exe["stage_node_hour_cap"] == 40.0


def test_require_stage_a_acceptance_passes_and_refuses(tmp_path):
    good = {
        "status": "passed",
        "metrics": {"rmse": 0.04, "stop_rmse": 0.05, "passed": True},
    }
    path = tmp_path / "validation-report.json"
    path.write_text(json.dumps(good))
    assert require_stage_a_acceptance(path)["status"] == "passed"

    bad = dict(good)
    bad["status"] = "failed"
    bad["metrics"] = {"rmse": 0.04, "stop_rmse": 0.05, "passed": False}
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="has not passed"):
        require_stage_a_acceptance(path)

    over = {
        "status": "passed",
        "metrics": {"rmse": 0.06, "stop_rmse": 0.05, "passed": True},
    }
    path.write_text(json.dumps(over))
    with pytest.raises(ValueError, match="exceeds"):
        require_stage_a_acceptance(path)

    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="missing"):
        require_stage_a_acceptance(missing)


def test_sample_coarse_to_fine_nearest_and_agreement_map():
    # Nested 3x3 coarse (240 m) and 12x12 fine (60 m) over the same 720 m square.
    class Tiny:
        width = 3
        height = 3
        affine = Affine(240.0, 0.0, 0.0, 0.0, -240.0, 720.0)

        def pixel_centers(self):
            cols = np.arange(self.width, dtype=np.float64) + 0.5
            rows = np.arange(self.height, dtype=np.float64) + 0.5
            x = self.affine.c + cols * self.affine.a
            y = self.affine.f + rows * self.affine.e
            return np.meshgrid(x, y)

    fine_transform = Affine(60.0, 0.0, 0.0, 0.0, -60.0, 720.0)
    coarse = np.arange(9, dtype=np.float32).reshape(3, 3)
    fine = sample_coarse_to_fine(coarse, Tiny(), fine_transform, (12, 12))
    assert fine.shape == (12, 12)
    # Fine (0,0) centre (30, 690) falls in coarse (0,0).
    assert fine[0, 0] == coarse[0, 0]
    # Fine centres in the middle of coarse (1,1) (x=360,y=360) use that value.
    assert fine[4, 4] == coarse[1, 1]
    assert fine[5, 5] == coarse[1, 1]
    # Fine col 2 centre x=150 lies inside coarse col 0 (0-240 m), not col 1.
    assert fine[0, 2] == coarse[0, 0]
    assert fine[0, 3] == coarse[0, 0]
    assert fine[0, 4] == coarse[0, 1]
    # Every fine pixel takes the coarse cell that contains its centre.
    expected = np.repeat(np.repeat(coarse, 4, axis=0), 4, axis=1)
    assert np.array_equal(fine, expected)

    mask_240 = np.array(
        [[True, True, False], [True, False, False], [True, True, True]]
    )
    mask_fine = np.ones((12, 12), dtype=bool)
    # Coarse (1,1) centre (360,360) samples fine row/col 5.5 → (6,6) after rint? 
    # (360-0)/60 = 6.0 → rint 6. Set that fine pixel False so masks agree.
    mask_fine[6, 6] = False
    agree = agreement_map(mask_240, mask_fine, fine_transform, Tiny())
    assert agree.shape == (3, 3)
    assert agree[0, 0] == 1.0  # True vs True
    assert agree[0, 2] == 0.0  # False vs True
    assert agree[1, 1] == 1.0  # False vs False
    # Outside the 240 m slope coverage the map is NaN, not "disagree".
    valid_240 = np.ones((3, 3), dtype=bool)
    valid_240[0, 2] = False
    masked = agreement_map(
        mask_240, mask_fine, fine_transform, Tiny(), valid_240=valid_240
    )
    assert np.isnan(masked[0, 2])
    assert masked[0, 0] == 1.0
    valid_fine = np.ones((12, 12), dtype=bool)
    valid_fine[6, 6] = False
    masked_fine = agreement_map(
        mask_240, mask_fine, fine_transform, Tiny(), valid_20=valid_fine
    )
    assert np.isnan(masked_fine[1, 1])


def test_build_rank_table_pairs_and_membership():
    grid = Grid()
    sites_240 = [
        {"lon": 0.0, "lat": -89.0, "total": 1.0, "row": 1, "col": 1},
        {"lon": 10.0, "lat": -88.0, "total": 0.9, "row": 2, "col": 2},
    ]
    sites_20 = [
        {"lon": 0.01, "lat": -89.0, "total": 1.1, "row": 10, "col": 10},
        {"lon": 40.0, "lat": -85.0, "total": 0.5, "row": 20, "col": 20},
    ]
    table = build_rank_table(sites_240, sites_20, grid)
    assert table["kind"] == "rank_table_20m_vs_240m"
    assert len(table["paired_by_rank"]) == 2
    assert table["paired_by_rank"][0]["distance_km"] is not None
    assert table["membership_240m_in_20m"][0]["nearest_rank_20m"] == 1
    assert "not affiliated" in table["note"].lower()


@pytest.mark.skipif(not DEM_PATH.is_file(), reason="20 m DEM not staged")
def test_pilot_times_tiles_and_writes_measurement(tmp_path, monkeypatch):
    # Shrink the pilot sample for a fast unit test.
    cfg_path = tmp_path / "stage-b.yaml"
    raw = yaml.safe_load((REPO / "config" / "stage-b.yaml").read_text())
    raw["pilot"]["tile_count"] = 2
    raw["slope"]["tile_size"] = 512
    cfg_path.write_text(yaml.safe_dump(raw))
    out = tmp_path / "pilot-out"
    report = run_pilot(
        DEM_PATH,
        out,
        workers=2,
        stage_b_config=cfg_path,
    )
    assert (out / "pilot-measurement.json").is_file()
    assert report["sample"]["tile_count"] == 2
    assert "projection" in report
    assert "projected_node_hours_with_scoring" in report["projection"]
