"""Deterministic tests for terrain horizons and Sun-disk metrics."""

import numpy as np

from anuma.sun import (
    compute_horizons,
    evaluate_sunlight,
    horizon_for_azimuth,
    sun_disk_visible_fraction,
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
