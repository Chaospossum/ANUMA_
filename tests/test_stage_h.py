"""Stage H (gated-ranking robustness) on synthetic arrays and GeoTIFFs only."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from anuma import hpc_monte_carlo, stage_d, stage_d_gate, stage_h, stage_h_gate
from anuma.config import load_config
from anuma.grid import Grid
from anuma.slope20 import compute_slope_untiled
from tests.test_hpc_monte_carlo import TinyGrid, _layers
from tests.test_stage_d import COARSE, _terrain, _write_tif
from tests.test_terrain_gate import _client, _peak_fraction, _write_layer, gated_root  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
GATED = "terrain_gate_8deg_1km_50pct"
NEW_TOP12 = [
    "1350:1392", "748:1693", "761:1285", "774:1572", "1319:1219", "1281:1142",
    "768:1635", "1290:1547", "753:1742", "1484:1402", "915:1109", "728:1081",
]


def _tiny_cfg() -> dict:
    cfg = copy.deepcopy(load_config())
    cfg["scoring"]["n_sites"] = 3
    cfg["scoring"]["min_separation_km"] = 0.24
    return cfg


def _tiny_gate(value: float = 0.9) -> np.ndarray:
    gate = np.full((4, 4), value, dtype=np.float32)
    gate[3, 2] = 0.3  # the usual winner fails the 1 km rule
    return gate


# -- Monte Carlo with the gate ------------------------------------------------


def test_ranking_basis_label_names_the_rule():
    gate = {"status": "applied", "layer_max_slope_deg": 8.0, "radius_m": 1000.0, "min_fraction": 0.5}
    assert hpc_monte_carlo.ranking_basis_label(gate) == GATED
    assert hpc_monte_carlo.ranking_basis_label({**gate, "status": "unavailable"}) == "slope_only"
    assert stage_h_gate.RANKING_BASIS == GATED


def test_gated_monte_carlo_removes_failing_cells_and_records_the_basis():
    cfg = _tiny_cfg()
    plain, plain_draws = hpc_monte_carlo.run_study(_layers(), TinyGrid(), cfg, mode="pilot", draw_count=4)
    gated, gated_draws = hpc_monte_carlo.run_study(
        _layers(), TinyGrid(), cfg, mode="pilot", draw_count=4,
        terrain_fraction=_tiny_gate(), terrain_max_slope_deg=8.0,
    )
    assert plain["ranking_basis"] == "slope_only" and "terrain_gate" not in plain
    assert gated["ranking_basis"] == GATED
    assert gated["terrain_gate"]["status"] == "applied"
    assert gated["terrain_gate"]["cells_removed"] == 1
    # Same weight draws, as in job 5332991; only the mask differs.
    assert [row["weights"] for row in plain_draws] == [row["weights"] for row in gated_draws]
    for row in gated_draws:
        for policy in row["policies"].values():
            assert "3:2" not in policy["top10"]
    assert any("3:2" in p["top10"] for row in plain_draws for p in row["policies"].values())
    parallel, _ = hpc_monte_carlo.run_study(
        _layers(), TinyGrid(), cfg, mode="pilot", draw_count=4, workers=3,
        terrain_fraction=_tiny_gate(), terrain_max_slope_deg=8.0,
    )
    assert parallel == gated


def test_gated_monte_carlo_refuses_an_unusable_gate():
    cfg = _tiny_cfg()
    with pytest.raises(ValueError, match="cannot be recomputed"):
        hpc_monte_carlo.run_study(
            _layers(), TinyGrid(), cfg, mode="pilot", draw_count=2,
            terrain_fraction=_tiny_gate(), terrain_max_slope_deg=10.0,
        )
    with pytest.raises(ValueError, match="does not match the slope grid"):
        hpc_monte_carlo.run_study(
            _layers(), TinyGrid(), cfg, mode="pilot", draw_count=2,
            terrain_fraction=np.ones((3, 3), dtype=np.float32), terrain_max_slope_deg=8.0,
        )
    with pytest.raises(ValueError, match="terrain_max_slope_deg"):
        hpc_monte_carlo.run_study(
            _layers(), TinyGrid(), cfg, mode="pilot", draw_count=2, terrain_fraction=_tiny_gate()
        )


def test_gate_off_study_must_rank_slope_only():
    cfg = _tiny_cfg()
    layers = {**_layers(), "terrain_1km_fraction": _tiny_gate()}
    # The layer is present but no gate was requested: cfg["hard"] enables the
    # gate, so the study would silently rank gated. It must refuse.
    with pytest.raises(ValueError, match="not slope only"):
        hpc_monte_carlo.run_study(layers, TinyGrid(), cfg, mode="pilot", draw_count=2)


def test_zero_noise_fraction_tolerance_is_a_parameter(tmp_path):
    from tests.test_stage_d import _synthetic_site

    full = compute_slope_untiled(_write_tif(tmp_path / "dem.tif", _terrain()))
    site = _synthetic_site()
    fraction = site.window_stats["zero_noise"]["fraction_1km"]
    near = {"8:8": fraction + 0.003}
    # Stage D default: two decimals.
    assert stage_d.zero_noise_check(site, full, near, atol_deg=1e-4, fraction_ranks=[1, 10])["passed"]
    strict = stage_d.zero_noise_check(
        site, full, near, atol_deg=1e-4, fraction_ranks=[1, 10], fraction_atol=1e-6
    )
    assert not strict["passed"] and strict["fraction_1km"]["fraction_atol"] == 1e-6
    exact = {"8:8": float(np.float32(fraction))}
    assert stage_d.zero_noise_check(
        site, full, exact, atol_deg=1e-4, fraction_ranks=[1, 10], fraction_atol=1e-6
    )["passed"]
    assert stage_d.require_gated_config(load_config())["acceptance"]["zero_noise_fraction_atol"] == 1e-6


def test_reference_draw_check_compares_records_exactly(tmp_path):
    cfg = _tiny_cfg()
    _, rows = hpc_monte_carlo.run_study(_layers(), TinyGrid(), cfg, mode="pilot", draw_count=3)
    path = tmp_path / "draws.jsonl"
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows) + "{}\n")
    assert stage_h.reference_draw_check(path, rows, 3)["identical"]
    changed = copy.deepcopy(rows)
    changed[1]["policies"]["decided_per_layer"]["top10"].reverse()
    changed[1]["policies"]["decided_per_layer"]["top10"].append("9:9")
    check = stage_h.reference_draw_check(path, changed, 3)
    assert not check["identical"] and check["mismatched_draws"] == [1]


# -- frozen config ----------------------------------------------------------------


def test_stage_h_config_is_frozen_and_stage_d_is_untouched():
    cfg = load_config()
    stage_d.require_frozen_config(cfg)  # Stage D's own assertion still holds.
    block = stage_h.require_stage_h_config(cfg)
    assert block["terrain_gate"]["fraction_sha256"] == stage_h_gate.GATE_FRACTION_SHA256
    dn = stage_d.require_gated_config(cfg)
    assert dn["site_order"] == NEW_TOP12
    base = cfg["hpc_studies"]["dem_noise"]
    for key in ("noise_model", "ellipse", "draws", "seed", "window_half_size_m", "resolution_m"):
        assert dn[key] == base[key]
    assert dn["acceptance"]["zero_noise_fraction_check_ranks"] == [1, 12]
    assert dn["acceptance"]["zero_noise_slope_atol_deg"] == base["acceptance"]["zero_noise_slope_atol_deg"]
    assert "controls" not in dn


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("hpc_studies", "dem_noise_gated", "draws"), 50, "may not set"),
        (("hpc_studies", "dem_noise_gated", "scope", "site_order"), NEW_TOP12[::-1], "site_order"),
        (("hpc_studies", "dem_noise", "seed"), 1, "seed"),
        (("hpc_studies", "stage_h", "terrain_gate", "fraction_sha256"), "0" * 64, "fraction_sha256"),
        (("hpc_studies", "clip_weight_monte_carlo", "seed"), 1, "5332991 design"),
        (("hard", "terrain_1km", "min_fraction"), 0.4, "min_fraction"),
    ],
)
def test_frozen_stage_h_values_refuse_drift(path, value, match):
    cfg = load_config()
    node = cfg
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValueError, match=match):
        stage_h.require_stage_h_config(cfg)


def _gated_ranked(ids=NEW_TOP12, extra=("1196:1703", "698:1711", "964:505")) -> list[dict]:
    return [
        {"row": int(s.split(":")[0]), "col": int(s.split(":")[1]), "lon": 1.0, "lat": -88.0,
         "total": 0.9 - 0.01 * i}
        for i, s in enumerate([*ids, *extra])
    ]


def test_gated_scope_is_recomputed_and_asserted():
    gate = np.full((2534, 2534), 0.6, dtype=np.float32)
    rule = stage_d.recompute_gated_scope(_gated_ranked(), load_config(), gate)
    assert rule["site_order"] == NEW_TOP12 and rule["reproduces_frozen_scope"]
    assert [row["in_scope"] for row in rule["ranking"]] == [True] * 12 + [False] * 3
    assert rule["ranking"][0]["terrain_1km_fraction"] == pytest.approx(0.6)
    swapped = _gated_ranked()
    swapped[11], swapped[12] = swapped[12], swapped[11]
    with pytest.raises(ValueError, match="differs from the frozen site order"):
        stage_d.recompute_gated_scope(swapped, load_config(), gate)
    with pytest.raises(ValueError, match="need 12"):
        stage_d.recompute_gated_scope(_gated_ranked()[:11], load_config(), gate)


def _gated_summary(frequencies: dict[str, float], draw_count: int = 500) -> dict:
    return {
        "ranking_basis": GATED,
        "draw_count": draw_count,
        "policies": {
            "decided_per_layer": {
                "top10_membership_frequency": [
                    {"id": sid, "frequency": f} for sid, f in frequencies.items()
                ]
            }
        },
    }


def test_rank_unstable_labels_follow_the_stage_d_rule():
    gate = np.full((2534, 2534), 0.6, dtype=np.float32)
    rule = stage_d.recompute_gated_scope(_gated_ranked(), load_config(), gate)
    freq = {sid: 1.0 for sid in NEW_TOP12[:10]}
    freq["774:1572"] = 0.79  # rank 4 below 80 %
    freq["915:1109"] = 0.30
    labels = stage_h.gated_stability_labels(_gated_summary(freq), rule, load_config())
    assert labels["unstable_ids"] == ["774:1572", "753:1742", "1484:1402", "915:1109", "728:1081"]
    rows = {row["id"]: row for row in labels["ranking"]}
    assert rows["774:1572"]["clause_frequency"] and not rows["774:1572"]["clause_rank_margin"]
    assert labels["rule"]["rank_margin_ranks"] == [9, 12]
    with pytest.raises(ValueError, match="gated Monte Carlo"):
        stage_h.gated_stability_labels({**_gated_summary(freq), "ranking_basis": "slope_only"}, rule, load_config())
    with pytest.raises(ValueError, match="500-draw"):
        stage_h.gated_stability_labels(_gated_summary(freq, 8), rule, load_config())


def test_projection_charges_each_share():
    projection = stage_h.project_production(
        fixed_seconds=120.0,
        mc_pilot_seconds=20.0,
        mc_pilot_draws=8,
        mc_production_draws=500,
        noise_projection={"projected_node_hours": 0.1},
        mc_share_hours=0.5,
        noise_share_hours=0.5,
        production_hours=1.0,
    )
    assert projection["monte_carlo_hours"] == pytest.approx(20.0 * 500 / 8 / 3600)
    assert projection["monte_carlo_fits_share"] and projection["noise_fits_share"]
    assert projection["total_fits"]
    slow = stage_h.project_production(
        fixed_seconds=0.0, mc_pilot_seconds=60.0, mc_pilot_draws=8, mc_production_draws=500,
        noise_projection={"projected_node_hours": 0.1}, mc_share_hours=0.5,
        noise_share_hours=0.5, production_hours=1.0,
    )
    assert not slow["monte_carlo_fits_share"]


# -- pins, receipts, preflight and scripts ---------------------------------------


def _stage_f_dir(root: Path, fraction: bytes = b"fraction") -> Path:
    stage_f_dir = root / "stage-f-production-5345446"
    stage_f_dir.mkdir(parents=True)
    (stage_f_dir / "terrain_1km_fraction.npy").write_bytes(fraction)
    digest = hashlib.sha256(fraction).hexdigest()
    sidecar = {
        "layer": "terrain_1km_fraction", "coverage": "full_cap", "job": "5345446",
        "fraction_sha256": digest, "max_slope_deg": 8.0, "radius_m": 1000.0,
        "gate_min_fraction_at_build": 0.5,
    }
    (stage_f_dir / "terrain_1km_fraction.json").write_text(json.dumps(sidecar))
    side_digest = hashlib.sha256((stage_f_dir / "terrain_1km_fraction.json").read_bytes()).hexdigest()
    manifest = {
        "passed": True,
        "outputs": {
            "terrain_1km_fraction.npy": {"sha256": digest},
            "terrain_1km_fraction.json": {"sha256": side_digest},
        },
    }
    (stage_f_dir / "output-manifest.json").write_text(json.dumps(manifest))
    return stage_f_dir


def _pin_stage_h(monkeypatch, paths: dict) -> None:
    from tests.test_stage_d import _pin_digests

    _pin_digests(monkeypatch)
    for name, attr in (
        ("terrain_1km_fraction.npy", "GATE_FRACTION_SHA256"),
        ("terrain_1km_fraction.json", "GATE_SIDECAR_SHA256"),
        ("output-manifest.json", "STAGE_F_MANIFEST_SHA256"),
    ):
        digest = hashlib.sha256((paths["stage_f_dir"] / name).read_bytes()).hexdigest()
        monkeypatch.setattr(stage_h_gate, attr, digest)
    monkeypatch.setattr(
        stage_h_gate, "REFERENCE_DRAWS_SHA256",
        hashlib.sha256(paths["reference_draws"].read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        stage_h_gate, "INPUT_MANIFEST_SHA256",
        hashlib.sha256(paths["input_manifest"].read_bytes()).hexdigest(),
    )


def _h_paths(tmp_path: Path, manifest: Path | None = None) -> dict:
    from tests.test_stage_d import _staged_inputs

    paths = _staged_inputs(tmp_path)
    del paths["mc_summary"]
    paths["stage_f_dir"] = _stage_f_dir(tmp_path)
    paths["reference_draws"] = tmp_path / "draws.jsonl"
    paths["reference_draws"].write_text("{}\n")
    if manifest is None:
        manifest = tmp_path / "input-manifest.json"
        manifest.write_text("{}")
    paths["input_manifest"] = manifest
    return paths


def test_gate_pins_every_input_and_the_stage_f_records(tmp_path, monkeypatch):
    paths = _h_paths(tmp_path)
    _pin_stage_h(monkeypatch, paths)
    inputs = stage_h_gate.expected_inputs(**paths)
    assert "mc_summary" not in inputs
    assert {"gate_fraction", "gate_sidecar", "stage_f_manifest", "reference_draws",
            "input_manifest", "dem", "err", "effres", "ldsm_err", "stage_b_slope"} == set(inputs)
    assert stage_h_gate.verify_input_digests(inputs)["gate_fraction"] == stage_h_gate.GATE_FRACTION_SHA256
    record = stage_h_gate.check_gate_records(
        inputs["gate_sidecar"]["path"], inputs["stage_f_manifest"]["path"]
    )
    assert record["job"] == "5345446" and record["max_slope_deg"] == 8.0
    receipt = {
        "account": "p201619",
        "stage_h_inputs_verified": True,
        **stage_h_gate.receipt_pin(**paths, mode="pilot"),
    }
    stage_h_gate.reject_stage_h_receipt(receipt, **paths, mode="pilot")
    with pytest.raises(ValueError, match="stage_h_mode"):
        stage_h_gate.reject_stage_h_receipt(receipt, **paths, mode="production")
    (paths["stage_f_dir"] / "terrain_1km_fraction.npy").write_bytes(b"another layer")
    with pytest.raises(ValueError, match="stage_h_inputs"):
        stage_h_gate.reject_stage_h_receipt(receipt, **paths, mode="pilot")
    with pytest.raises(ValueError, match="checksum mismatch for Stage H input gate_fraction"):
        stage_h_gate.verify_input_digests(stage_h_gate.expected_inputs(**paths))


def test_gate_records_refuse_another_layer(tmp_path):
    stage_f_dir = _stage_f_dir(tmp_path)
    side = stage_f_dir / "terrain_1km_fraction.json"
    manifest = stage_f_dir / "output-manifest.json"
    # The real pins differ from this synthetic layer.
    with pytest.raises(ValueError, match="fraction_sha256 is not the pinned layer"):
        stage_h_gate.check_gate_records(side, manifest)
    meta = json.loads(side.read_text())
    meta.update(job="5400000", fraction_sha256=stage_h_gate.GATE_FRACTION_SHA256)
    side.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="not 5345446"):
        stage_h_gate.check_gate_records(side, manifest)


def test_real_stage_f_records_match_the_pins():
    sidecar = REPO / "data" / "grid" / "terrain_1km_fraction.json"
    manifest = REPO / "out" / "hpc" / "stage-f-production-5345446" / "output-manifest.json"
    if not (sidecar.is_file() and manifest.is_file()):
        pytest.skip("Stage F job 5345446 records are not on this machine")
    assert stage_d_gate.sha256_file(sidecar) == stage_h_gate.GATE_SIDECAR_SHA256
    assert stage_d_gate.sha256_file(manifest) == stage_h_gate.STAGE_F_MANIFEST_SHA256
    stage_h_gate.check_gate_records(sidecar, manifest)


def test_stage_h_preflight_hashes_inputs_and_pins_them(tmp_path, monkeypatch):
    from tests import test_hpc_preflight as base

    preflight = base.preflight
    base._configured_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("ANUMA_JOB_PROFILE", "stage-h-pilot")
    monkeypatch.setenv("ANUMA_ACCOUNT", "p201619")
    base._write_evidence(
        Path(os.environ["ANUMA_ALLOCATION_EVIDENCE_FILE"]),
        {
            "source": "test fixture",
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "account": "p201619",
            "remaining_cpu_node_hours": 10.0,
        },
    )
    paths = _h_paths(tmp_path, manifest=Path(os.environ["ANUMA_INPUT_MANIFEST"]))
    _pin_stage_h(monkeypatch, paths)
    monkeypatch.setenv("ANUMA_STAGE_D_RAW_DIR", str(paths["raw_dir"]))
    monkeypatch.setenv("ANUMA_DEM", str(paths["dem"]))
    monkeypatch.setenv("ANUMA_STAGE_B_SLOPE", str(paths["stage_b_slope"]))
    monkeypatch.setenv("ANUMA_STAGE_F_DIR", str(paths["stage_f_dir"]))
    monkeypatch.setenv("ANUMA_REFERENCE_DRAWS", str(paths["reference_draws"]))
    original = preflight.run_checked

    def fake_command(command):
        if "show assoc" in " ".join(str(item) for item in command):
            return "cluster|p201619|tester|cpu|test\n"
        return original(command)

    monkeypatch.setattr(preflight, "run_checked", fake_command)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    with pytest.raises(RuntimeError, match="inside a compute allocation"):
        preflight.run_preflight(tmp_path / "receipt-stage-h-login.json")
    monkeypatch.setenv("SLURM_JOB_ID", "123456")
    monkeypatch.setattr(
        preflight.shutil, "which",
        lambda command: None if command == "myquota" else f"/bin/{command}",
    )
    receipt = preflight.run_preflight(tmp_path / "receipt-stage-h.json")
    assert receipt["job_profile"] == "stage-h-pilot"
    assert receipt["stage_h_inputs_verified"] is True
    assert receipt["stage_h_preflight_slurm_job_id"] == "123456"
    assert receipt["stage_h_ranking_basis"] == GATED
    assert receipt["job_walltime"] == "00:30:00"
    stage_h_gate.reject_stage_h_receipt(receipt, **paths, mode="pilot")
    (paths["stage_f_dir"] / "terrain_1km_fraction.npy").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        preflight.run_preflight(tmp_path / "receipt-stage-h-bad.json")


def test_stage_h_profiles_and_scripts_fit_one_and_a_half_node_hours():
    from tests.test_stage_d import preflight

    pilot = preflight.JOB_PROFILES["stage-h-pilot"]
    production = preflight.JOB_PROFILES["stage-h"]
    assert pilot["qos"] == "test" and pilot["walltime"] == "00:30:00"
    assert production["qos"] == "default" and production["walltime_minutes"] <= 60
    assert pilot["node_hours"] + production["node_hours"] <= stage_h_gate.STAGE_CAP_NODE_HOURS == 1.5
    assert pilot["cpus"] == production["cpus"] == 128
    assert stage_h_gate.WALLTIME == {"pilot": pilot["walltime"], "production": production["walltime"]}
    assert stage_h_gate.NODE_HOURS == {"pilot": pilot["node_hours"], "production": production["node_hours"]}
    assert {"stage-h-pilot", "stage-h"} <= preflight.COMPUTE_PREFLIGHT_PROFILES
    wrapper = (REPO / "hpc" / "submit-stage-h.sh").read_text()
    assert "reject_stage_h_receipt" in wrapper and "p201619" in wrapper
    assert "sha256sum" not in wrapper and "--gpu" not in wrapper.lower()
    assert "stage-f-production-5345446" in wrapper and "monte-carlo-full-5332991/draws.jsonl" in wrapper
    for mode, profile in (("pilot", pilot), ("production", production)):
        script = (REPO / "hpc" / "slurm" / f"stage-h-{mode}.sbatch").read_text()
        assert f'stage_h_mode="{mode}"' in script
        assert f'expected_qos="{profile["qos"]}"' in script
        assert f'expected_time="{profile["walltime"]}"' in script
        assert 'expected_memory_mb="245760"' in script
        assert "reject_stage_h_receipt" in script and "-m anuma.stage_h" in script
        assert "module load Python/3.12.3-GCCcore-13.3.0" in script
        assert "srun --export=ALL" in script and "--workers 128" in script
        assert "--gpu" not in script.lower()
        assert f'walltime="{profile["walltime"]}"' in wrapper


# -- end to end on synthetic inputs ----------------------------------------------


class _NestedGrid:
    """17 x 17 cells of 240 m nested on the synthetic 20 m DEM (tests.test_stage_d)."""

    resolution_m = 240.0
    affine = COARSE
    proj4 = "+proj=stere +lat_0=-90 +lon_0=0 +R=1737400 +units=m +no_defs"
    height = width = 17

    @staticmethod
    def to_lonlat(row, col):
        return float(col), float(-89.0 + row / 100.0)


def _synthetic_run(tmp_path: Path, monkeypatch) -> dict:
    """Every Stage H input on disk, pins and config narrowed to two sites."""
    raw = tmp_path / "raw"
    raw.mkdir()
    # Gentler than tests.test_stage_d's terrain, so both sites pass the gate.
    terrain = 1000.0 + 0.3 * (_terrain() - 1000.0)
    terrain = terrain.astype(np.float32).astype(np.float64)
    shape = terrain.shape
    names = {
        "LDEM_80S_20MPP_ADJ.TIF": (terrain, np.nan),
        "LDEM_80S_20MPP_ADJ_ERR.TIF": (np.full(shape, 2.0), np.nan),
        "LDEM_80S_20MPP_ADJ_EFFRES.TIF": (np.full(shape, 60.0), -1.0),
        "LDSM_80S_20MPP_ADJ_ERR.TIF": (np.full(shape, 2.0), np.nan),  # ~ the slope-noise std
    }
    lines = []
    for name, (array, nodata) in names.items():
        path = _write_tif(raw / name, array, nodata=nodata)
        if name != "LDEM_80S_20MPP_ADJ.TIF":
            lines.append(f"{stage_d_gate.sha256_file(path)}  ./{name}")
    (raw / "pgda90-sha256.txt").write_text("\n".join(lines) + "\n")
    dem = raw / "LDEM_80S_20MPP_ADJ.TIF"
    slope = tmp_path / "slope-20m.npy"
    np.save(slope, compute_slope_untiled(dem))

    # 240 m arrays on the nested grid; two ranked sites, (8, 8) then (5, 9).
    grid_dir = tmp_path / "grid"
    grid_dir.mkdir()
    base = np.full((17, 17), np.nan, dtype=np.float32)
    arrays = {name: base.copy() for name in ("dem", "slope", "illumination", "hydrogen", "earth_visibility")}
    for (row, col), value in (((8, 8), 1.0), ((5, 9), 0.8), ((12, 3), 0.6)):
        arrays["slope"][row, col] = 2.0
        arrays["dem"][row, col] = 0.0
        for name in ("illumination", "hydrogen", "earth_visibility"):
            arrays[name][row, col] = value
    files = {"grid": grid_dir / "grid.json"}
    files["grid"].write_text("{}")
    for name, array in arrays.items():
        files[name] = grid_dir / f"{name}.npy"
        np.save(files[name], array)
    manifest = tmp_path / "input-manifest.json"
    manifest.write_text(json.dumps({"id": "synthetic"}))

    # Gate layer: the zero-noise 1 km fraction at the two scope sites.
    dn = copy.deepcopy(stage_d.require_frozen_config(load_config()))
    dn["window_half_size_m"] = 1200.0
    rule = {
        "site_order": ["8:8", "5:9"],
        "ranking": [
            {"id": "8:8", "default_rank": 1, "row": 8, "col": 8},
            {"id": "5:9", "default_rank": 2, "row": 5, "col": 9},
        ],
    }
    prepared, _ = stage_d.prepare_sites_from_rasters(
        rule, dem_path=dem, err_path=raw / "LDEM_80S_20MPP_ADJ_ERR.TIF",
        effres_path=raw / "LDEM_80S_20MPP_ADJ_EFFRES.TIF",
        ldsm_path=raw / "LDSM_80S_20MPP_ADJ_ERR.TIF", coarse_transform=COARSE, dn=dn,
        max_slope_deg=8.0,
    )
    fractions = {
        prepared[i].window.site_id: prepared[i].window_stats["zero_noise"]["fraction_1km"]
        for i in (0, 1)
    }
    assert min(fractions.values()) >= 0.5
    gate = np.full((17, 17), 0.9, dtype=np.float32)
    gate[8, 8], gate[5, 9] = fractions["8:8"], fractions["5:9"]
    gate[12, 3] = 0.2  # ranked without the gate, removed with it
    stage_f_dir = tmp_path / "stage-f"
    stage_f_dir.mkdir()
    np.save(stage_f_dir / "terrain_1km_fraction.npy", gate)
    gate_digest = stage_d_gate.sha256_file(stage_f_dir / "terrain_1km_fraction.npy")
    sidecar = {
        "layer": "terrain_1km_fraction", "coverage": "full_cap", "job": "5345446",
        "fraction_sha256": gate_digest, "max_slope_deg": 8.0, "radius_m": 1000.0,
        "gate_min_fraction_at_build": 0.5,
    }
    (stage_f_dir / "terrain_1km_fraction.json").write_text(json.dumps(sidecar))
    (stage_f_dir / "output-manifest.json").write_text(json.dumps({
        "passed": True,
        "outputs": {
            "terrain_1km_fraction.npy": {"sha256": gate_digest},
            "terrain_1km_fraction.json": {
                "sha256": stage_d_gate.sha256_file(stage_f_dir / "terrain_1km_fraction.json")
            },
        },
    }))

    cfg = copy.deepcopy(load_config())
    cfg["hpc_studies"]["dem_noise"]["window_half_size_m"] = 1200.0
    cfg["scoring"]["min_separation_km"] = 0.24  # several sites on a 4 km grid
    monkeypatch.setitem(stage_d.FROZEN_SCORING, "scoring.min_separation_km", 0.24)
    gated = cfg["hpc_studies"]["dem_noise_gated"]
    gated["scope"] = {"default_ranks": [1, 2], "site_order": ["8:8", "5:9"]}
    gated["acceptance"]["zero_noise_fraction_check_ranks"] = [1, 2]
    monkeypatch.setitem(stage_d.FROZEN, "window_half_size_m", 1200.0)
    monkeypatch.setitem(stage_d.FROZEN_GATED, "scope.default_ranks", [1, 2])
    monkeypatch.setitem(stage_d.FROZEN_GATED, "scope.site_order", ["8:8", "5:9"])
    monkeypatch.setitem(stage_d.FROZEN_GATED, "acceptance.zero_noise_fraction_check_ranks", [1, 2])
    for module in (stage_d, stage_h, hpc_monte_carlo):
        monkeypatch.setattr(module, "load_config", lambda: cfg)
    monkeypatch.setattr(Grid, "from_json", classmethod(lambda cls, path, cfg=None: _NestedGrid()))
    validate = lambda path: ({"id": "synthetic"}, files)  # noqa: E731
    monkeypatch.setattr("anuma.hpc_smoke.validate_input_manifest", validate)
    monkeypatch.setattr(hpc_monte_carlo, "validate_input_manifest", validate)
    monkeypatch.setattr("anuma.stage_f.validate_sidecar", lambda *args, **kwargs: None)

    # Reference draws: the gate-off pipeline on these arrays (job 5332991's role).
    layers = {name: np.load(path) for name, path in files.items() if name != "grid"}
    _, rows = hpc_monte_carlo.run_study(layers, _NestedGrid(), cfg, mode="pilot", draw_count=8)
    reference = tmp_path / "draws.jsonl"
    reference.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))

    paths = {
        "raw_dir": raw, "dem": dem, "stage_b_slope": slope, "stage_f_dir": stage_f_dir,
        "reference_draws": reference, "input_manifest": manifest,
    }
    _pin_stage_h(monkeypatch, paths)
    monkeypatch.setattr(stage_d_gate, "DEM_SHA256", stage_d_gate.sha256_file(dem))
    monkeypatch.setattr(stage_d_gate, "ERR_SHA256", stage_d_gate.sha256_file(raw / "LDEM_80S_20MPP_ADJ_ERR.TIF"))
    monkeypatch.setattr(stage_d_gate, "SLOPE_20M_SHA256", stage_d_gate.sha256_file(slope))
    return paths


def test_stage_h_pilot_end_to_end_on_synthetic_inputs(tmp_path, monkeypatch):
    paths = _synthetic_run(tmp_path, monkeypatch)
    out = tmp_path / "out" / "stage-h-pilot-1"
    report = stage_h.run_stage_h(mode="pilot", output_dir=out, workers=2, **paths)
    checks = report["checks"]
    assert checks["reference_draws_identical"], report["reference_reproduction"]
    assert checks["monte_carlo_passed"] and checks["noise_passed"], checks
    assert checks["projection_fits"] and report["passed"]
    summary = json.loads((out / "monte-carlo" / "study-summary.json").read_text())
    assert summary["ranking_basis"] == GATED and summary["draw_count"] == 8
    assert summary["terrain_gate"]["source_job"] == "5345446"
    ids = {row["id"] for row in summary["policies"]["decided_per_layer"]["top10_membership_frequency"]}
    assert "12:3" not in ids and "8:8" in ids
    noise = json.loads((out / "dem-noise" / "pilot-measurement.json").read_text())
    assert noise["stage"] == "H-dem-noise-20m-gated"
    assert noise["scope"]["site_order"] == ["8:8", "5:9"]
    zero = noise["sites"]["8:8"]["zero_noise_check"]
    assert zero["fraction_1km"]["applies"] and zero["fraction_1km"]["matches_two_decimals"]
    assert zero["fraction_1km"]["fraction_atol"] == 1.0e-6
    assert zero["fraction_1km"]["abs_diff"] <= 1.0e-6
    assert "stage_f_layer" in zero["fraction_1km"]
    assert noise["projection"]["production_node_hours"] == 0.5
    manifest = json.loads((out / "output-manifest.json").read_text())
    assert {"pilot-measurement.json", "monte-carlo/study-summary.json",
            "dem-noise/pilot-measurement.json"} <= set(manifest["outputs"])
    assert manifest["passed"] is True
    with pytest.raises(ValueError, match="not empty"):
        stage_h.run_stage_h(mode="pilot", output_dir=out, workers=2, **paths)


def test_stage_h_production_end_to_end_writes_labels_and_a_checks_file(tmp_path, monkeypatch):
    paths = _synthetic_run(tmp_path, monkeypatch)
    out = tmp_path / "out" / "stage-h-production-2"
    monkeypatch.setenv("SLURM_JOB_ID", "5400002")
    report = stage_h.run_stage_h(mode="production", output_dir=out, workers=2, **paths)
    assert report["passed"], report["checks"]
    summary = json.loads((out / "monte-carlo" / "study-summary.json").read_text())
    assert summary["draw_count"] == 500 and summary["source_job"] == "5400002"
    labels = report["rank_unstable_labels"]
    # Two scope sites at ranks 1-2: no rank-margin clause; both always in the top 10.
    assert labels["unstable_ids"] == []
    noise = json.loads((out / "dem-noise" / "dem-noise-summary.json").read_text())
    assert set(noise["verdict"]) == {"8:8", "5:9"}
    summary_sha = stage_d_gate.sha256_file(out / "stage-h-summary.json")
    payload = stage_h.checks_file(report, noise, summary_sha256=summary_sha, commit="abc1234")
    assert payload["source"]["summary_sha256"] == summary_sha
    assert [site["site_id"] for site in payload["sites"]] == ["8:8", "5:9"]
    assert payload["source"]["job"] == "5400002" and payload["draws"] == 200
    assert payload["terrain_gate"]["fraction_sha256"] == stage_h_gate.GATE_FRACTION_SHA256
    target = tmp_path / "stage-h-noise.json"
    stage_h.main(["--mode", "checks-file", "--run-dir", str(out), "--out", str(target)])
    assert json.loads(target.read_text())["sites"] == payload["sites"]


def test_stage_h_refuses_a_changed_gate_layer_before_any_draw(tmp_path, monkeypatch):
    paths = _synthetic_run(tmp_path, monkeypatch)
    gate = np.load(paths["stage_f_dir"] / "terrain_1km_fraction.npy")
    gate[0, 0] = 0.1
    np.save(paths["stage_f_dir"] / "terrain_1km_fraction.npy", gate)
    out = tmp_path / "out" / "refused"
    with pytest.raises(ValueError, match="checksum mismatch for Stage H input gate_fraction"):
        stage_h.run_stage_h(mode="pilot", output_dir=out, workers=1, **paths)
    assert not (out / "monte-carlo").exists()


def test_reference_mismatch_fails_the_pilot(tmp_path, monkeypatch):
    paths = _synthetic_run(tmp_path, monkeypatch)
    rows = [json.loads(line) for line in paths["reference_draws"].read_text().splitlines()]
    rows[3]["weights"]["hydrogen"] += 1e-12
    paths["reference_draws"].write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    monkeypatch.setattr(stage_h_gate, "REFERENCE_DRAWS_SHA256", stage_d_gate.sha256_file(paths["reference_draws"]))
    report = stage_h.run_stage_h(mode="pilot", output_dir=tmp_path / "o", workers=1, **paths)
    assert report["reference_reproduction"]["mismatched_draws"] == [3]
    assert report["passed"] is False


# -- check file and API ------------------------------------------------------------


def _noise_production(site_order=("8:8", "5:9")) -> dict:
    def run(rate, unstable):
        return {
            "fraction_1km": {"zero_noise": 0.61234},
            "decision": {"p_low": 0.6, "p_high": 0.62, "width": 0.02, "noise_unstable": unstable,
                         "clause_fired": "neither" if not unstable else "straddle"},
            "pass_fail": {"threshold": 0.5, "pass_rate": rate},
        }

    return {
        "mode": "production",
        "passed": True,
        "scope": {"site_order": list(site_order), "draws_per_site_and_run": 200},
        "stability_rule": {
            "ranking": [
                {"id": sid, "row": int(sid.split(":")[0]), "col": int(sid.split(":")[1]),
                 "lat": -88.0, "lon": 10.0}
                for sid in site_order
            ]
        },
        "sites": {
            sid: {"default_rank": i + 1, "runs": {"primary": run(1.0, False), "sensitivity": run(1.0, False)}}
            for i, sid in enumerate(site_order)
        },
    }


def _h_summary(fraction_sha256: str) -> dict:
    return {
        "stage": stage_h.STAGE,
        "mode": "production",
        "passed": True,
        "ranking_basis": GATED,
        "gate_layer": {"fraction_sha256": fraction_sha256, "job": "5345446", "radius_m": 1000.0,
                        "max_slope_deg": 8.0},
        "execution": {"SLURM_JOB_ID": "5400001"},
        "rank_unstable_labels": {
            "unstable_ids": ["5:9"],
            "ranking": [{"id": "8:8", "top10_frequency": 1.0}, {"id": "5:9", "top10_frequency": 0.7}],
        },
    }


def test_checks_file_keeps_the_stage_d_fields_and_adds_the_basis():
    payload = stage_h.checks_file(
        _h_summary("a" * 64), _noise_production(), summary_sha256="b" * 64, commit="abc1234"
    )
    stage_d_file = json.loads((REPO / "data" / "checks" / "stage-d-noise.json").read_text())
    assert set(stage_d_file) <= set(payload)
    assert set(stage_d_file["sites"][0]) <= set(payload["sites"][0])
    assert payload["kind"] == stage_h.CHECKS_KIND and payload["ranking_basis"] == GATED
    assert payload["terrain_gate"]["fraction_sha256"] == "a" * 64
    assert payload["source"]["job"] == "5400001" and payload["draws"] == 200
    assert [site["rank_unstable"] for site in payload["sites"]] == [False, True]
    assert payload["sites"][0]["zero_noise_within_1km"] == 0.6123
    with pytest.raises(ValueError, match="passed"):
        stage_h.checks_file(
            {**_h_summary("a" * 64), "passed": False}, _noise_production(), summary_sha256="b" * 64
        )
    with pytest.raises(ValueError, match="Slurm job id"):
        stage_h.checks_file(
            {**_h_summary("a" * 64), "execution": {}}, _noise_production(), summary_sha256="b" * 64
        )
    with pytest.raises(ValueError, match="sha256 of stage-h-summary"):
        stage_h.checks_file(_h_summary("a" * 64), _noise_production(), summary_sha256="")
    assert payload["terrain_gate"]["min_fraction"] == payload["threshold"] == 0.5
    assert payload["source"]["summary_sha256"] == "b" * 64


def _install_gated_studies(root: Path, digest: str, cfg: dict, min_fraction: float = 0.5) -> None:
    spec = cfg["stability"]["gated"]
    path = root / spec["monte_carlo_summary"]
    path.parent.mkdir(parents=True, exist_ok=True)
    site = {"id": "1350:1392", "row": 1350, "col": 1392, "lat": -88.8, "lon": 123.6}
    summary = {
        "ranking_basis": GATED,
        "source_job": "5400001",
        "draw_count": 500,
        "weight_ranges": {"illumination": [0.4, 0.6]},
        "terrain_gate": {"status": "applied", "fraction_sha256": digest, "min_fraction": min_fraction},
        "policies": {
            "decided_per_layer": {
                "top10_membership_frequency": [{**site, "count": 500, "frequency": 1.0}],
                "winner_stability": {"winners": [{**site, "count": 431, "frequency": 0.862}]},
            }
        },
    }
    path.write_text(json.dumps(summary))
    noise = stage_h.checks_file(_h_summary(digest), _noise_production(), summary_sha256="b" * 64)
    noise_path = root / "stage-h-noise.json"
    noise_path.write_text(json.dumps(noise))
    cfg["stability"]["gated"]["dem_noise_checks"] = str(noise_path)


@pytest.fixture
def gated_cfg(monkeypatch):
    import anuma.api as api

    cfg = load_config()
    monkeypatch.setattr(api, "_cfg", lambda: cfg)
    return cfg


def test_api_serves_gated_studies_when_the_gate_applies(gated_root, gated_cfg, monkeypatch):  # noqa: F811
    from tests.test_api import _write_stability_summary

    _write_layer(gated_root, _peak_fraction(gated_root, 0.49))
    digest = json.loads((gated_root / "data" / "grid" / "terrain_1km_fraction.json").read_text())[
        "fraction_sha256"
    ]
    _install_gated_studies(gated_root, digest, gated_cfg)
    _write_stability_summary(gated_root)
    client = _client(gated_root, monkeypatch)

    body = client.post("/score", json={}).json()
    assert body["terrain_gate"] == "applied"
    assert body["ranking_basis"]["basis"] == "slope_and_terrain_1km"
    assert body["ranking_basis"]["studies_basis"] == "slope_and_terrain_1km"
    assert body["ranking_basis"]["studies_match"] is True and body["ranking_basis"]["note"] is None
    assert body["ranking_basis"]["studies_available"] == {
        "slope_only": True, "slope_and_terrain_1km": True,
    }
    assert client.get("/layers").json()["studies_ranking_basis"] == "slope_and_terrain_1km"

    stability = client.get("/stability.json").json()
    assert stability["available"] and stability["ranking_basis"] == "slope_and_terrain_1km"
    assert stability["study_ranking_basis"] == GATED and stability["job_id"] == "5400001"
    assert stability["winners"][0]["frequency"] == 0.862
    old = client.get("/stability.json", params={"basis": "slope_only"}).json()
    assert old["job_id"] == "5332991" and old["ranking_basis"] == "slope_only"
    assert client.get("/stability.json", params={"basis": "nonsense"}).status_code == 400

    noise = client.get("/checks/stage-d-noise.json").json()
    assert noise["ranking_basis"] == GATED and noise["sites"][0]["site_id"] == "8:8"
    old_noise = client.get("/checks/stage-d-noise.json", params={"basis": "slope_only"}).json()
    assert old_noise["source"]["job"] == "5345050"

    # Gate off or another threshold: the served (gated) studies do not match.
    off = client.post("/score", json={"hard": {"terrain_1km": False}}).json()["ranking_basis"]
    assert off["basis"] == "slope_only" and off["studies_match"] is False
    assert "20 m terrain rule" in off["note"]
    loose = client.post("/score", json={"hard": {"terrain_1km_min_fraction": 0.4}}).json()
    assert loose["ranking_basis"]["studies_match"] is False
    assert "50%" in loose["ranking_basis"]["note"] and "40%" in loose["ranking_basis"]["note"]


def test_api_ignores_gated_studies_for_another_layer(gated_root, gated_cfg, monkeypatch):  # noqa: F811
    _write_layer(gated_root, _peak_fraction(gated_root, 0.49))
    _install_gated_studies(gated_root, "f" * 64, gated_cfg)
    client = _client(gated_root, monkeypatch)
    basis = client.post("/score", json={}).json()["ranking_basis"]
    assert basis["studies_basis"] == "slope_only" and basis["studies_match"] is False
    assert basis["studies_available"]["slope_and_terrain_1km"] is False
    gated = client.get("/stability.json", params={"basis": "slope_and_terrain_1km"}).json()
    assert gated["available"] is False and "different terrain layer" in gated["reason"]
    missing = client.get("/checks/stage-d-noise.json", params={"basis": "slope_and_terrain_1km"})
    assert missing.status_code == 404


def test_api_without_gated_files_keeps_the_slope_only_studies(gated_root, gated_cfg, monkeypatch):  # noqa: F811
    _write_layer(gated_root, _peak_fraction(gated_root, 0.49))
    client = _client(gated_root, monkeypatch)
    basis = client.post("/score", json={}).json()["ranking_basis"]
    assert basis["studies_basis"] == "slope_only" and basis["studies_match"] is False
    assert "previous" in basis["note"]
    assert client.get("/checks/stage-d-noise.json").json()["source"]["job"] == "5345050"
    gated = client.get("/stability.json", params={"basis": "slope_and_terrain_1km"}).json()
    assert gated["available"] is False and "not on this machine" in gated["reason"]


def test_api_basis_query_works_without_ingested_layers(client_empty: TestClient):
    assert client_empty.get("/stability.json").json() == {"available": False}
    gated = client_empty.get("/stability.json", params={"basis": "slope_and_terrain_1km"}).json()
    assert gated == {
        "available": False,
        "ranking_basis": "slope_and_terrain_1km",
        "reason": "20 m terrain layer is not loaded",
    }


@pytest.fixture
def client_empty(tmp_path, monkeypatch) -> TestClient:
    from anuma.api import app, clear_cache

    monkeypatch.setenv("ANUMA_ROOT", str(tmp_path))
    clear_cache()
    return TestClient(app)


LOCAL_GRID = REPO / "data" / "grid"


@pytest.mark.skipif(
    not (LOCAL_GRID / "grid.json").is_file()
    or not (LOCAL_GRID / "terrain_1km_fraction.npy").is_file(),
    reason="ingested 240 m arrays and the Stage F gate layer are not staged",
)
def test_gated_scope_from_real_arrays_reproduces_the_frozen_order():
    cfg = load_config()
    if stage_d_gate.sha256_file(LOCAL_GRID / "terrain_1km_fraction.npy") != stage_h_gate.GATE_FRACTION_SHA256:
        pytest.skip("local gate layer is not the pinned job 5345446 layer")
    grid = Grid.from_json(LOCAL_GRID / "grid.json", cfg=cfg)
    layers = {
        name: np.load(LOCAL_GRID / f"{name}.npy", mmap_mode="r")
        for name in ("slope", "illumination", "hydrogen", "earth_visibility")
    }
    gate = np.load(LOCAL_GRID / "terrain_1km_fraction.npy")
    ranked = stage_d.default_ranking(layers, grid, cfg, terrain_fraction=gate, terrain_max_slope_deg=8.0)
    assert stage_d.recompute_gated_scope(ranked, cfg, gate)["site_order"] == NEW_TOP12
