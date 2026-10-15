"""Stage F terrain gate in scoring and the API (constructed arrays only)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from anuma import stage_f
from anuma.api import STAGE_F_COMMAND, app, clear_cache
from anuma.config import load_config
from anuma.grid import Grid
from anuma.score import hard_mask, rank_sites, score, scored_weights, terrain_gate
from tests.test_api import _pixel

LAYER = "terrain_1km_fraction"


def _small_layers(fraction=None) -> dict[str, np.ndarray]:
    slope = np.array([[1.0, 2.0, 3.0], [4.0, 9.0, np.nan]], dtype=np.float32)
    soft = np.array([[1.0, 0.9, 0.8], [0.7, 0.6, 0.5]], dtype=np.float32)
    layers = {"slope": slope, "illumination": soft, "hydrogen": soft, "earth_visibility": soft}
    if fraction is not None:
        layers[LAYER] = np.asarray(fraction, dtype=np.float32)
    return layers


FRACTION = [[0.49, 0.50, np.nan], [0.9, 0.9, 0.9]]


# -- hard_mask and terrain_gate --------------------------------------------------


def test_hard_mask_applies_fraction_threshold_inclusively_and_fails_nan():
    layers = _small_layers()
    plain = hard_mask(layers["slope"], 8.0)
    gated = hard_mask(
        layers["slope"], 8.0, terrain_fraction=np.asarray(FRACTION, dtype=np.float32),
        terrain_min_fraction=0.5,
    )
    assert plain.tolist() == [[True, True, True], [True, False, False]]
    assert gated.tolist() == [[False, True, False], [True, False, False]]
    with pytest.raises(ValueError, match="terrain_min_fraction"):
        hard_mask(layers["slope"], 8.0, terrain_fraction=np.zeros((2, 3)))
    with pytest.raises(ValueError, match="does not match"):
        hard_mask(layers["slope"], 8.0, terrain_fraction=np.zeros((3, 3)), terrain_min_fraction=0.5)


def test_hard_mask_with_radius_still_applies_the_gate():
    slope = np.full((5, 5), 1.0, dtype=np.float32)
    fraction = np.full((5, 5), 0.9, dtype=np.float32)
    fraction[2, 2] = 0.1
    mask = hard_mask(slope, 8.0, slope_radius_m=240.0, resolution_m=240.0,
                     terrain_fraction=fraction, terrain_min_fraction=0.5)
    assert not mask[2, 2] and mask[2, 1] and not mask[0, 0]


def test_score_applies_gate_when_layer_present():
    cfg = load_config()
    result = score(_small_layers(FRACTION), cfg=cfg, grid=_grid_stub())
    gate = result.terrain_gate
    assert gate["status"] == "applied"
    assert gate["cells_passing_slope"] == 4 and gate["cells_removed"] == 2
    assert result.hard_mask.tolist() == [[False, True, False], [True, False, False]]
    assert np.isnan(result.combined[0, 0]) and np.isfinite(result.combined[0, 1])


def test_score_without_layer_is_unchanged_and_reports_unavailable():
    cfg = load_config()
    base = score(_small_layers(), cfg=cfg, grid=_grid_stub())
    assert base.terrain_gate["status"] == "unavailable"
    assert "not on this machine" in base.terrain_gate["message"]
    assert base.hard_mask.tolist() == [[True, True, True], [True, False, False]]


def test_gate_switch_threshold_and_slope_mismatch():
    cfg = load_config()
    layers = _small_layers(FRACTION)
    off = score(layers, hard={"max_slope_deg": 8.0, "terrain_1km": False}, cfg=cfg, grid=_grid_stub())
    assert off.terrain_gate["status"] == "disabled"
    assert off.hard_mask.sum() == 4
    strict = score(
        layers, hard={"max_slope_deg": 8.0, "terrain_1km_min_fraction": 0.95}, cfg=cfg,
        grid=_grid_stub(),
    )
    assert strict.hard_mask.sum() == 0
    mismatch = score(layers, hard={"max_slope_deg": 10.0}, cfg=cfg, grid=_grid_stub())
    assert mismatch.terrain_gate["status"] == "slope_mismatch"
    assert "uses 8°" in mismatch.terrain_gate["message"]
    assert mismatch.hard_mask.sum() == 5  # slope-only at 10°
    with pytest.raises(ValueError, match="cannot be recomputed"):
        score(layers, hard={"max_slope_deg": 10.0, "terrain_1km": True}, cfg=cfg, grid=_grid_stub())
    with pytest.raises(ValueError, match="requested but not available"):
        score(_small_layers(), hard={"max_slope_deg": 8.0, "terrain_1km": True}, cfg=cfg,
              grid=_grid_stub())
    with pytest.raises(ValueError, match="between 0 and 1"):
        terrain_gate(layers, {"terrain_1km_min_fraction": 1.5}, cfg, 8.0)


def test_config_can_disable_the_gate():
    cfg = load_config()
    cfg["hard"]["terrain_1km"]["enabled"] = False
    result = score(_small_layers(FRACTION), cfg=cfg, grid=_grid_stub())
    assert result.terrain_gate["status"] == "disabled"
    on = score(_small_layers(FRACTION), hard={"max_slope_deg": 8.0, "terrain_1km": True},
               cfg=cfg, grid=_grid_stub())
    assert on.terrain_gate["status"] == "applied"


def test_gate_layer_cannot_be_weighted():
    cfg = load_config()
    assert scored_weights({"illumination": 1.0, LAYER: 0.0}, cfg) == {"illumination": 1.0}
    with pytest.raises(ValueError, match="hard-gate layer"):
        scored_weights({"illumination": 1.0, LAYER: 0.5}, cfg)


def test_ranking_skips_gated_cells():
    cfg = load_config()
    result = score(_small_layers(FRACTION), cfg=cfg, grid=_grid_stub())
    sites = rank_sites(result, n=3, min_separation_km=0.001)
    assert [(s["row"], s["col"]) for s in sites] == [(0, 1), (1, 0)]


def _grid_stub():
    class _G:
        resolution_m = 240.0

        def to_lonlat(self, row, col):
            return 0.0, -89.0

    return _G()


# -- API -------------------------------------------------------------------------


def _write_layer(root: Path, fraction: np.ndarray, **overrides) -> None:
    cfg = load_config()
    grid = Grid(cfg)
    gdir = root / cfg["paths"]["grid"]
    spec = stage_f.require_frozen_config(cfg)
    meta = stage_f.sidecar(
        spec=spec, grid=grid, coverage="full_cap", inputs=_pinned_inputs(), stats={}, job="5400000"
    )
    np.save(gdir / f"{LAYER}.npy", fraction.astype(np.float32))
    meta["fraction_sha256"] = stage_f.sha256_file(gdir / f"{LAYER}.npy")
    meta.update(overrides)
    (gdir / f"{LAYER}.json").write_text(json.dumps(meta))


def _pinned_inputs() -> dict:
    from anuma.stage_f_gate import DEM_SHA256, SLOPE_20M_SHA256

    return {
        "dem": {"path": "/x/LDEM_80S_20MPP_ADJ.TIF", "sha256": DEM_SHA256},
        "stage_b_slope": {"path": "/x/slope-20m.npy", "sha256": SLOPE_20M_SHA256},
    }


@pytest.fixture
def gated_root(ingested_root, tmp_path) -> Path:
    # Link the shared read-only layers instead of copying ~100 MB per test;
    # each test writes its own gate layer into its own grid directory.
    root = tmp_path / "gated"
    for source in ingested_root.rglob("*"):
        if source.is_file():
            target = root / source.relative_to(ingested_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)
    return root


def _client(root: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ANUMA_ROOT", str(root))
    clear_cache()
    return TestClient(app)


def _peak_fraction(root: Path, value_at_best: float) -> np.ndarray:
    """Fraction 0.9 everywhere except the fixture's best cell (0°E, 87°S)."""
    grid = Grid(load_config())
    fraction = np.full((grid.height, grid.width), 0.9, dtype=np.float32)
    row, col = _pixel(grid, 0.0, -87.0)
    fraction[row, col] = value_at_best
    return fraction


def test_api_without_layer_reports_unavailable(gated_root, monkeypatch):
    client = _client(gated_root, monkeypatch)
    body = client.post("/score", json={}).json()
    assert body["terrain_gate"] == "unavailable"
    assert body["terrain_gate_detail"]["message"].startswith("20 m terrain rule not applied")
    assert body["ranking_basis"] == {
        "basis": "slope_only", "studies_basis": "slope_only", "studies_match": True, "note": None,
    }
    assert all(site["terrain_1km_fraction"] is None for site in body["sites"])
    layers = client.get("/layers").json()
    assert layers["terrain_gate"]["available"] is False
    assert layers["terrain_gate"]["build"] == STAGE_F_COMMAND
    entry = next(layer for layer in layers["layers"] if layer["name"] == LAYER)
    assert entry["gate"] is True and entry["scored"] is False and entry["available"] is False
    assert entry["label"] == "Flat ground within 1 km at 20 m (%)"
    assert client.get(f"/layer/{LAYER}.png").status_code == 404


def test_api_applies_the_gate_and_reports_the_basis(gated_root, monkeypatch):
    _write_layer(gated_root, _peak_fraction(gated_root, 0.49))
    client = _client(gated_root, monkeypatch)
    before = _client_score(client, {"hard": {"terrain_1km": False}})
    after = _client_score(client, {})
    assert after["terrain_gate"] == "applied"
    assert after["terrain_gate_detail"]["cells_removed"] == 1
    assert after["ranking_basis"]["basis"] == "slope_and_terrain_1km"
    assert after["ranking_basis"]["studies_match"] is False
    assert "previous" in after["ranking_basis"]["note"]
    assert before["terrain_gate"] == "disabled"
    best_before = (before["sites"][0]["row"], before["sites"][0]["col"])
    assert best_before not in {(s["row"], s["col"]) for s in after["sites"]}
    assert all(s["terrain_1km_fraction"] >= 0.5 for s in after["sites"])
    assert before["sites"][0]["terrain_1km_fraction"] == pytest.approx(0.49)
    # The threshold is switchable; 0.4 lets the best cell back in.
    loose = _client_score(client, {"hard": {"terrain_1km_min_fraction": 0.4}})
    assert (loose["sites"][0]["row"], loose["sites"][0]["col"]) == best_before
    layers = client.get("/layers").json()
    assert layers["terrain_gate"]["available"] is True
    assert layers["terrain_gate"]["layer_max_slope_deg"] == 8.0
    assert layers["studies_ranking_basis"] == "slope_only"
    png = client.get(f"/layer/{LAYER}.png")
    assert png.status_code == 200 and png.headers["X-Anuma-Gate-Layer"] == "true"
    assert png.headers["X-Anuma-Range-Min"] == "0" and png.headers["X-Anuma-Range-Max"] == "1"
    csv_text = client.post("/score.csv", json={}).text
    assert '# terrain_gate: {"status":"applied"' in csv_text


def test_api_other_max_slope_turns_the_gate_off_or_400(gated_root, monkeypatch):
    _write_layer(gated_root, _peak_fraction(gated_root, 0.49))
    client = _client(gated_root, monkeypatch)
    body = _client_score(client, {"hard": {"max_slope_deg": 10}})
    assert body["terrain_gate"] == "slope_mismatch"
    assert "uses 8°" in body["terrain_gate_detail"]["message"]
    assert body["ranking_basis"]["basis"] == "slope_only"
    forced = client.post("/score", json={"hard": {"max_slope_deg": 10, "terrain_1km": True}})
    assert forced.status_code == 400 and "cannot be recomputed" in forced.json()["detail"]
    bad = client.post("/score", json={"hard": {"terrain_1km_min_fraction": 1.5}})
    assert bad.status_code == 422


def test_api_explicit_gate_without_layer_is_400(gated_root, monkeypatch):
    client = _client(gated_root, monkeypatch)
    forced = client.post("/score", json={"hard": {"terrain_1km": True}})
    assert forced.status_code == 400
    assert "requested but not available" in forced.json()["detail"]
    # Omitted switch degrades with a status instead.
    assert _client_score(client, {})["terrain_gate"] == "unavailable"


@pytest.mark.parametrize(
    "override, reason",
    [
        ({"coverage": "window"}, "full cap"),
        ({"grid_sha256": "0" * 64}, "different 240 m grid"),
        ({"job": None}, "no Slurm job id"),
        ({"inputs": {**_pinned_inputs(), "stage_b_slope": {"sha256": "496f554f" + "0" * 56}}},
         "pinned Stage B slope"),
        ({"inputs": {**_pinned_inputs(), "dem": {"sha256": "0" * 64}}}, "pinned 20 m DEM"),
        ({"fraction_sha256": "0" * 64}, "does not match the sidecar"),
        ({"fraction_sha256": None}, "lacks fraction_sha256"),
    ],
)
def test_api_refuses_a_layer_with_a_bad_sidecar(gated_root, monkeypatch, override, reason):
    _write_layer(gated_root, _peak_fraction(gated_root, 0.49), **override)
    client = _client(gated_root, monkeypatch)
    body = _client_score(client, {})
    assert body["terrain_gate"] == "unavailable"
    assert reason in body["terrain_gate_detail"]["message"]


def test_api_layer_without_sidecar_is_unavailable(gated_root, monkeypatch):
    cfg = load_config()
    grid = Grid(cfg)
    np.save(gated_root / cfg["paths"]["grid"] / f"{LAYER}.npy",
            np.zeros((grid.height, grid.width), dtype=np.float32))
    client = _client(gated_root, monkeypatch)
    body = _client_score(client, {})
    assert body["terrain_gate"] == "unavailable"
    assert "sidecar" in body["terrain_gate_detail"]["message"]


def test_stability_payload_names_its_ranking_basis(tmp_path, monkeypatch):
    cfg = load_config()
    path = tmp_path / cfg["stability"]["monte_carlo_summary"]
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "draw_count": 1,
        "policies": {cfg["stability"]["policy"]: {
            "winner_stability": {"winners": []}, "top10_membership_frequency": {},
        }},
    }))
    client = _client(tmp_path, monkeypatch)
    assert client.get("/stability.json").json()["ranking_basis"] == "slope_only"


def _client_score(client: TestClient, body: dict) -> dict:
    response = client.post("/score", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# -- Demo UI fields alongside the gate -------------------------------------------


def test_gate_fields_coexist_with_ui_fields(gated_root, monkeypatch):
    _write_layer(gated_root, _peak_fraction(gated_root, 0.49))
    client = _client(gated_root, monkeypatch)
    body = _client_score(client, {"weights": {"illumination": 1, "hydrogen": 1, "earth_visibility": 0}})
    assert body["terrain_gate"] == "applied"
    assert body["ranking_basis"]["studies_match"] is False
    assert body["weights_used"] == pytest.approx({"illumination": 0.5, "hydrogen": 0.5, "earth_visibility": 0.0})
    assert body["hard_used"]["max_slope_deg"] == 8.0
    low, high = body["score_range"]
    assert low <= high
    # Every ranked site carries its gate-layer fraction for the table and card.
    assert all(site["terrain_1km_fraction"] is not None for site in body["sites"])
    lines = client.post("/score.csv", json={}).text.splitlines()
    keys = [line.split(":", 1)[0] for line in lines if line.startswith("#")]
    assert keys == [
        "# illumination_window", "# keepout", "# terrain_gate",
        "# weights_used", "# max_slope_deg", "# slope_radius_m",
    ]
    # The gate layer keeps its fixed 0..1 range basis.
    png = client.get(f"/layer/{LAYER}.png")
    assert png.headers["X-Anuma-Range-Basis"] == "fixed"


def test_ui_reads_the_gate_fields():
    from anuma.config import repo_root

    app_js = (repo_root() / "static" / "app.js").read_text()
    html = (repo_root() / "static" / "index.html").read_text()
    assert "studies_match === false" in app_js
    assert "site.terrain_1km_fraction" in app_js
    assert "detail.layer_max_slope_deg" in app_js
    assert "request.hard.terrain_1km = els.terrain.checked" in app_js
    assert 'id="terrain-toggle"' in html
