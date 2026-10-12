"""Deterministic tests for terrain horizons and Sun-disk metrics."""

import hashlib
import json
import sys

import numpy as np
import pytest

from anuma.config import repo_root
from anuma.sun import (
    compute_horizons,
    evaluate_sunlight,
    horizon_for_azimuth,
    load_sun_config,
    sun_disk_visible_fraction,
    sun_vectors_from_spice,
    validation_metrics,
)


def test_known_north_ridge_has_analytic_horizon():
    dem = np.zeros((5, 5), dtype=np.float64)
    dem[1, :] = 100.0
    radius = 1.0e12
    horizon = horizon_for_azimuth(dem, 10.0, radius, 0.0)
    central_angle = 10.0 / radius
    expected = np.rad2deg(
        np.arctan2(
            (radius + 100.0) * np.cos(central_angle) - radius,
            (radius + 100.0) * np.sin(central_angle),
        )
    )
    np.testing.assert_allclose(horizon[2, 2], expected, atol=1.0e-5)


def test_parallel_horizon_is_bit_identical_to_serial():
    dem = np.arange(49, dtype=np.float32).reshape(7, 7)
    serial = compute_horizons(dem, 240.0, azimuth_count=16, workers=1)
    parallel = compute_horizons(dem, 240.0, azimuth_count=16, workers=4)
    assert np.array_equal(serial, parallel)


def test_sun_disk_fraction_handles_full_partial_and_dark():
    visible = sun_disk_visible_fraction(
        np.array([-0.28, 0.0, 0.28]), np.zeros(3), 0.27
    )
    np.testing.assert_allclose(visible, [0.0, 0.5, 1.0])


def test_synthetic_flat_horizon_outputs_expected_metrics():
    horizon = np.zeros((4, 1, 1), dtype=np.float32)
    lon = np.array([[0.0]])
    lat = np.array([[-90.0]])
    # At the south pole +X is local north on the horizon; +Z is fully below it.
    vectors = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    result = evaluate_sunlight(
        horizon,
        lon,
        lat,
        vectors,
        cadence_hours=2.0,
        disk_radius_deg=0.27,
    )
    np.testing.assert_allclose(result["percent_time_lit"], [[25.0]])
    np.testing.assert_allclose(
        result["longest_continuous_darkness_hours"], [[2.0]]
    )
    assert not result["passes_80_percent"][0, 0]


def test_validation_gate_reports_fixed_metrics():
    measured = np.array([0.0, 0.5, 1.0])
    modeled = np.array([0.0, 0.4, 0.9])
    metrics = validation_metrics(modeled, measured, stop_rmse=0.05)
    assert metrics["valid_pixels"] == 3
    assert metrics["rmse"] > 0.05
    assert metrics["passed"] is False


def test_sun_config_pins_manifest_and_keeps_rmse_gate_unrun():
    cfg = load_sun_config()
    text = (repo_root() / "config" / "sun.yaml").read_text()
    assert "sha256" not in text.lower()
    assert (
        cfg["sun"]["kernel_manifest"]
        == "/project/home/p201619/spice-kernels/kernel-manifest.json"
    )
    assert cfg["sun"]["required_kernel_roles"] == [
        "DE440",
        "moon_pa_de440_200625",
        "moon_de440_250416.tf",
        "naif0012",
    ]
    sanity = cfg["geometry_sanity"]
    assert sanity["kind"] == "operator_reported_geometry_sanity_check"
    assert sanity["result"] == "within ±1.53°"
    assert sanity["slurm_job_id"] is None
    assert sanity["environment"] == ".venv-hpc-sun"
    assert sanity["environment_built"] is True
    assert cfg["validation"]["measured_layer"] == "avgvisib_65s_240m_201608"
    assert cfg["validation"]["stop_rmse"] == 0.05
    assert cfg["validation"]["status"] == "unrun"
    assert cfg["validation"]["blocking"] is True


def _fake_manifest(tmp_path, roles, *, drop_role=None, bad_role=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    entries = []
    for role in roles:
        if role == drop_role:
            continue
        path = tmp_path / role
        path.write_bytes(f"fake-kernel-{role}".encode())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if role == bad_role:
            digest = "0" * 64
        entries.append({"role": role, "path": path.name, "sha256": digest})
    manifest = tmp_path / "kernel-manifest.json"
    manifest.write_text(json.dumps({"kernels": entries}))
    return manifest


def test_missing_role_or_checksum_mismatch_refuses_before_spiceypy(
    tmp_path, monkeypatch
):
    class _SpiceGuard:
        def __getattr__(self, name):
            raise AssertionError(f"spiceypy.{name} was called")

    monkeypatch.setitem(sys.modules, "spiceypy", _SpiceGuard())
    roles = load_sun_config()["sun"]["required_kernel_roles"]

    missing = _fake_manifest(tmp_path / "missing", roles, drop_role=roles[0])
    with pytest.raises(ValueError, match="missing roles"):
        sun_vectors_from_spice(
            ["2029-01-01T00:00:00"],
            missing,
            required_roles=roles,
        )

    mismatched = _fake_manifest(tmp_path / "mismatch", roles, bad_role=roles[-1])
    with pytest.raises(ValueError, match="checksum mismatch"):
        sun_vectors_from_spice(
            ["2029-01-01T00:00:00"],
            mismatched,
            required_roles=roles,
        )
