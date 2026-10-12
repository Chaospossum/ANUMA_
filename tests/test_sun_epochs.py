"""Epoch windows and Sun-validation receipt checks."""

from __future__ import annotations

import json
from datetime import timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from anuma.config import load_config, repo_root
from anuma.sun_epochs import (
    describe_windows,
    duration_seconds,
    load_epoch_manifest,
    parse_utc,
    reject_sun_receipt,
    window_start,
)


def test_epoch_windows_use_labeled_starts():
    manifest = load_epoch_manifest(repo_root() / "config" / "sun-epoch.yaml")
    primary = window_start(manifest, "primary")
    sensitivity = window_start(manifest, "sensitivity")
    assert primary == parse_utc("2000-01-01T00:00:00Z")
    assert manifest["primary"]["start_label"] == (
        "ANUMA's choice because no published start exists"
    )
    offset = timedelta(seconds=duration_seconds(Decimal("9.3")))
    assert sensitivity == primary + offset
    assert sensitivity.tzinfo == timezone.utc
    assert manifest["gate_window"] == "primary"
    assert manifest["sensitivity"]["cannot_select_passing_start"] is True
    windows = describe_windows(manifest)
    assert windows["primary"]["used_for_gate"] is True
    assert windows["sensitivity"]["used_for_gate"] is False
    assert windows["sensitivity"]["cannot_select_passing_start"] is True
    definition = manifest["published_temporal_definition"]
    for phrase in (
        "https://pgda.gsfc.nasa.gov/products/69",
        "hourly timesteps spanning 18.6 years",
        "Mazarico et al. 2011",
        "20120010094",
        "6 h",
        "updated after 2011",
        "NASA publishes no start or end for AVGVISIB_65S_240M_201608",
        "Do not infer dates from the filename",
    ):
        assert phrase in definition
    span = duration_seconds(Decimal("18.6"))
    assert windows["primary"]["epoch_count"] == (span - 1) // 3600 + 1
    assert windows["sensitivity"]["epoch_count"] == windows["primary"]["epoch_count"]


def _pin_receipt(tmp_path: Path, epoch_path: Path) -> tuple[dict, Path, Path]:
    kernel = tmp_path / "kernel-manifest.json"
    kernel.write_text('{"kernels":[]}\n')
    revision = load_config()["provenance"]["illumination_affine_revision"]
    manifest = tmp_path / "input-manifest.json"
    manifest.write_text(
        json.dumps({"provenance": {"illumination_affine_revision": revision}})
    )
    from anuma.sun_epochs import receipt_pin

    pin = receipt_pin(
        input_manifest=manifest,
        kernel_manifest=kernel,
        epoch_manifest=epoch_path,
    )
    receipt = {
        "account": "p201619",
        "accelerators": False,
        "gate_window": "primary",
        "input_manifest": str(manifest.resolve()),
        **pin,
    }
    return receipt, kernel, manifest


def test_receipt_rejects_bad_epoch_or_raised_rmse(tmp_path):
    good = repo_root() / "config" / "sun-epoch.yaml"
    bad = tmp_path / "sun-epoch.yaml"
    bad.write_text(good.read_text().replace("2000-01-01T00:00:00Z", "2016-08-01T00:00:00Z"))
    with pytest.raises(ValueError, match="primary start"):
        reject_sun_receipt(
            {
                "account": "p201619",
                "accelerators": False,
                "stop_rmse": 0.05,
                "tune_threshold_after_run": False,
                "gate_window": "primary",
            },
            input_manifest=tmp_path / "unused.json",
            kernel_manifest=tmp_path / "unused-kernel.json",
            epoch_manifest=bad,
        )

    receipt, kernel, manifest = _pin_receipt(tmp_path, good)
    receipt["stop_rmse"] = 0.2
    with pytest.raises(ValueError, match="RMSE"):
        reject_sun_receipt(
            receipt,
            input_manifest=manifest,
            kernel_manifest=kernel,
            epoch_manifest=good,
        )
