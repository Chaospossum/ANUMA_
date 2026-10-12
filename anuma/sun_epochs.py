"""Frozen AVGVISIB epoch windows and Sun-validation receipt checks.

NASA publishes no start or end for AVGVISIB_65S_240M_201608. The primary
start is ANUMA's choice. The sensitivity window is reported separately and
cannot be used to pick a passing start.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import yaml

from anuma.config import load_config, repo_root
from anuma.sun import load_sun_config

YEAR_LENGTH_DAYS = Decimal("365.25")
SPAN_YEARS = Decimal("18.6")
OFFSET_YEARS = Decimal("9.3")
CADENCE_HOURS = 1
PRIMARY_START = "2000-01-01T00:00:00Z"
PRIMARY_LABEL = "ANUMA's choice because no published start exists"
FROZEN_STOP_RMSE = 0.05
CITATION_PHRASES = (
    "https://pgda.gsfc.nasa.gov/products/69",
    "hourly timesteps spanning 18.6 years",
    "Mazarico et al. 2011",
    "20120010094",
    "6 h",
    "updated after 2011",
    "NASA publishes no start or end for AVGVISIB_65S_240M_201608",
    "Do not infer dates from the filename",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"epoch field {name} must be an exact decimal string")
    return Decimal(str(value))


def duration_seconds(years: Decimal, year_days: Decimal = YEAR_LENGTH_DAYS) -> int:
    """Return years × year_days as an integer number of UTC seconds."""
    seconds = years * year_days * Decimal(86400)
    if seconds != seconds.to_integral_value():
        raise ValueError("epoch window is not an integer number of seconds")
    return int(seconds)


def format_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text: str) -> datetime:
    if not isinstance(text, str) or not text.endswith("Z"):
        raise ValueError(f"epoch timestamp must be UTC Zulu: {text!r}")
    parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    return parsed.replace(tzinfo=timezone.utc)


def hourly_count(span_seconds: int) -> int:
    """Hourly samples with start <= t < start + span."""
    if span_seconds <= 0:
        raise ValueError("epoch window must be positive")
    return (span_seconds - 1) // 3600 + 1


def hourly_timestamps(start: datetime, span_seconds: int) -> list[str]:
    count = hourly_count(span_seconds)
    return [
        format_utc(start + timedelta(seconds=index * 3600)) for index in range(count)
    ]


def load_epoch_manifest(path: Path | None = None) -> dict:
    """Load the frozen epoch file and refuse a window that is not that pin."""
    cfg = load_sun_config()
    relative = str(cfg["validation"]["epoch_manifest"])
    epoch_path = Path(path) if path else repo_root() / relative
    manifest = yaml.safe_load(epoch_path.read_text())
    if not isinstance(manifest, dict):
        raise ValueError("epoch manifest must be a mapping")
    definition = manifest.get("published_temporal_definition")
    if not isinstance(definition, str):
        raise ValueError("published temporal definition must be supplied")
    missing = [phrase for phrase in CITATION_PHRASES if phrase not in definition]
    if missing:
        raise ValueError(
            "epoch manifest is missing published temporal citations: "
            + "; ".join(missing)
        )
    if _decimal(manifest.get("year_length_days"), "year_length_days") != YEAR_LENGTH_DAYS:
        raise ValueError("18.6 years must use 365.25 days")
    if _decimal(manifest.get("span_years"), "span_years") != SPAN_YEARS:
        raise ValueError("epoch span must be 18.6 years")
    if manifest.get("cadence_hours") != CADENCE_HOURS:
        raise ValueError("epoch cadence must be hourly")
    if manifest.get("gate_window") != "primary":
        raise ValueError("validation gate must use the primary window")
    primary = manifest.get("primary")
    sensitivity = manifest.get("sensitivity")
    if not isinstance(primary, dict) or not isinstance(sensitivity, dict):
        raise ValueError("epoch manifest must define primary and sensitivity windows")
    if primary.get("role") != "validation_gate":
        raise ValueError("primary window must be the validation gate")
    if primary.get("start_utc") != PRIMARY_START:
        raise ValueError(
            "primary start must be 2000-01-01T00:00:00Z, ANUMA's choice"
        )
    if primary.get("start_label") != PRIMARY_LABEL:
        raise ValueError("primary start must be labeled as ANUMA's choice")
    if sensitivity.get("role") != "reported_separately":
        raise ValueError("sensitivity window must be reported separately")
    if sensitivity.get("cannot_select_passing_start") is not True:
        raise ValueError(
            "sensitivity window cannot be used to pick a passing start"
        )
    if _decimal(sensitivity.get("offset_years"), "offset_years") != OFFSET_YEARS:
        raise ValueError("sensitivity window must start 9.3 × 365.25 days later")
    manifest["_path"] = str(epoch_path.resolve())
    return manifest


def window_start(manifest: dict, name: str) -> datetime:
    primary = parse_utc(manifest["primary"]["start_utc"])
    if name == "primary":
        return primary
    if name != "sensitivity":
        raise ValueError(f"unknown epoch window: {name}")
    offset = duration_seconds(
        _decimal(manifest["sensitivity"]["offset_years"], "offset_years")
    )
    return primary + timedelta(seconds=offset)


def window_span_seconds(manifest: dict) -> int:
    return duration_seconds(_decimal(manifest["span_years"], "span_years"))


def describe_windows(manifest: dict) -> dict:
    """Metadata for both windows. Sensitivity is not a gate result."""
    span = window_span_seconds(manifest)
    described = {}
    for name in ("primary", "sensitivity"):
        start = window_start(manifest, name)
        described[name] = {
            "role": manifest[name]["role"],
            "start_utc": format_utc(start),
            "end_exclusive_utc": format_utc(start + timedelta(seconds=span)),
            "end_note": (
                "Derived from 18.6 × 365.25 days. Not a NASA-published end."
            ),
            "cadence_hours": CADENCE_HOURS,
            "epoch_count": hourly_count(span),
            "used_for_gate": name == "primary",
        }
    described["sensitivity"]["cannot_select_passing_start"] = True
    described["primary"]["start_label"] = manifest["primary"]["start_label"]
    return described


def gate_epochs(manifest: dict) -> list[str]:
    """Hourly primary-window timestamps. The sensitivity window is not included."""
    if manifest.get("gate_window") != "primary":
        raise ValueError("validation gate must use the primary window")
    if manifest["sensitivity"].get("cannot_select_passing_start") is not True:
        raise ValueError("sensitivity window cannot be used to pick a passing start")
    return hourly_timestamps(window_start(manifest, "primary"), window_span_seconds(manifest))


def assert_frozen_rmse(cfg: dict | None = None) -> None:
    cfg = cfg if cfg is not None else load_sun_config()
    validation = cfg["validation"]
    if validation.get("stop_rmse") != FROZEN_STOP_RMSE:
        raise ValueError("refusing a changed RMSE threshold")
    if validation.get("tune_threshold_after_run") is not False:
        raise ValueError("tune_threshold_after_run must stay false")


def receipt_pin(
    *,
    input_manifest: Path,
    kernel_manifest: Path,
    epoch_manifest: Path,
) -> dict:
    """Checksums and frozen science pins recorded on a Sun-validation receipt."""
    assert_frozen_rmse()
    epoch = load_epoch_manifest(epoch_manifest)
    describe_windows(epoch)
    kernel_path = Path(kernel_manifest)
    if not kernel_path.is_file():
        raise ValueError(f"kernel manifest is missing: {kernel_path}")
    body = json.loads(Path(input_manifest).read_text())
    expected_revision = load_config()["provenance"]["illumination_affine_revision"]
    found = body.get("provenance", {}).get("illumination_affine_revision")
    if found != expected_revision:
        raise ValueError("affine revision does not match the frozen ingest pin")
    return {
        "kernel_manifest": str(kernel_path.resolve()),
        "kernel_manifest_sha256": _sha256(kernel_path),
        "epoch_manifest": str(Path(epoch_manifest).resolve()),
        "epoch_manifest_sha256": _sha256(Path(epoch_manifest)),
        "illumination_affine_revision": expected_revision,
        "stop_rmse": FROZEN_STOP_RMSE,
        "tune_threshold_after_run": False,
        "accelerators": False,
        "gate_window": "primary",
        "input_manifest_sha256": _sha256(Path(input_manifest)),
    }


def reject_sun_receipt(
    receipt: dict,
    *,
    input_manifest: Path,
    kernel_manifest: Path,
    epoch_manifest: Path,
) -> None:
    """Refuse a receipt whose epoch, kernel, affine pin, or RMSE gate drifted."""
    if receipt.get("account") != "p201619":
        raise ValueError("sun validation requires account p201619")
    if receipt.get("accelerators") is not False:
        raise ValueError("sun validation does not allow accelerators")
    if float(receipt.get("stop_rmse", -1)) != FROZEN_STOP_RMSE:
        raise ValueError("refusing a changed RMSE threshold")
    if receipt.get("tune_threshold_after_run") is not False:
        raise ValueError("tune_threshold_after_run must stay false")
    if receipt.get("gate_window") != "primary":
        raise ValueError("validation gate must use the primary window")
    pin = receipt_pin(
        input_manifest=input_manifest,
        kernel_manifest=kernel_manifest,
        epoch_manifest=epoch_manifest,
    )
    compared = {
        "input_manifest": str(Path(input_manifest).resolve()),
        "kernel_manifest": pin["kernel_manifest"],
        "kernel_manifest_sha256": pin["kernel_manifest_sha256"],
        "epoch_manifest": pin["epoch_manifest"],
        "epoch_manifest_sha256": pin["epoch_manifest_sha256"],
        "input_manifest_sha256": pin["input_manifest_sha256"],
        "illumination_affine_revision": pin["illumination_affine_revision"],
        "stop_rmse": pin["stop_rmse"],
        "tune_threshold_after_run": False,
        "gate_window": "primary",
    }
    for key, value in compared.items():
        if receipt.get(key) != value:
            raise ValueError(f"sun validation receipt mismatch: {key}")
