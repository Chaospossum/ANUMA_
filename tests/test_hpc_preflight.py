"""Unit tests for fail-closed MeluXina preflight behavior."""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "hpc" / "preflight.py"
REPO_ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("anuma_hpc_preflight", MODULE_PATH)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def _write_evidence(path, body):
    path.write_text(json.dumps(body))
    path.chmod(0o600)


def _configured_environment(tmp_path, monkeypatch, remaining=10.0):
    output = tmp_path / "output"
    scratch = tmp_path / "scratch"
    output.mkdir()
    scratch.mkdir()
    manifest = tmp_path / "input-manifest.json"
    manifest.write_text("{}")
    captured = datetime.now(timezone.utc).isoformat()
    allocation = tmp_path / "allocation.json"
    storage = tmp_path / "storage.json"
    _write_evidence(
        allocation,
        {
            "source": "test fixture",
            "captured_at_utc": captured,
            "account": "project-account",
            "remaining_cpu_node_hours": remaining,
        },
    )
    _write_evidence(
        storage,
        {
            "source": "test fixture",
            "captured_at_utc": captured,
            "quota_bytes": 1_000_000_000,
            "used_bytes": 1_000_000,
        },
    )
    values = {
        "USER": "tester",
        "ANUMA_ACCOUNT": "project-account",
        "ANUMA_PARTITION": "cpu",
        "ANUMA_QOS": "test",
        "ANUMA_DATA_ROOT": str(tmp_path),
        "ANUMA_INPUT_MANIFEST": str(manifest),
        "ANUMA_OUTPUT_ROOT": str(output),
        "ANUMA_SCRATCH_DIR": str(scratch),
        "ANUMA_ALLOCATION_EVIDENCE_FILE": str(allocation),
        "ANUMA_STORAGE_EVIDENCE_FILE": str(storage),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(preflight.shutil, "which", lambda command: f"/bin/{command}")
    monkeypatch.setattr(
        preflight.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 1_000_000_000})()
    )
    monkeypatch.setattr(preflight, "run_smoke", lambda *args, **kwargs: {"status": "validated"})
    monkeypatch.setattr(
        preflight,
        "check_data",
        lambda root: {"ready": True, "message": "ready"},
    )

    def fake_command(command):
        joined = " ".join(str(item) for item in command)
        if "show assoc" in joined:
            return "cluster|project-account|tester|cpu|test\n"
        if "show qos" in joined:
            return "test|00:30:00|\n"
        if "status --porcelain" in joined:
            return ""
        if "rev-parse HEAD" in joined:
            return "a" * 40 + "\n"
        return "ok\n"

    monkeypatch.setattr(preflight, "run_checked", fake_command)


def test_preflight_writes_private_receipt_with_test_qos(tmp_path, monkeypatch):
    _configured_environment(tmp_path, monkeypatch)
    receipt_path = tmp_path / "receipt.json"
    receipt = preflight.run_preflight(receipt_path)
    assert receipt["qos"] == "test"
    assert receipt["job_walltime"] == "00:30:00"
    assert receipt["job_node_hours_cap"] == 0.5
    assert receipt["input_manifest_sha256"]
    assert receipt_path.stat().st_mode & 0o777 == 0o600


def test_preflight_rejects_nan_allocation(tmp_path, monkeypatch):
    _configured_environment(tmp_path, monkeypatch, remaining=float("nan"))
    with pytest.raises(RuntimeError, match="below required"):
        preflight.run_preflight(tmp_path / "receipt.json")


def test_preflight_rejects_unknown_qos(tmp_path, monkeypatch):
    _configured_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("ANUMA_QOS", "made-up")
    with pytest.raises(RuntimeError, match="unknown ANUMA_QOS"):
        preflight.run_preflight(tmp_path / "receipt.json")


def test_preflight_rejects_dev_qos_for_batch_smoke(tmp_path, monkeypatch):
    _configured_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("ANUMA_QOS", "dev")
    with pytest.raises(RuntimeError, match="requires QOS 'test'"):
        preflight.run_preflight(tmp_path / "receipt.json")


def test_sun_validation_profiles_fit_one_node_and_fifteen_hours():
    pilot = preflight.JOB_PROFILES["sun-validation-pilot"]
    production = preflight.JOB_PROFILES["sun-validation"]
    assert pilot["qos"] == "test"
    assert pilot["walltime"] == "00:30:00"
    assert pilot["walltime_minutes"] <= 30
    assert pilot["cpus"] == 128
    assert production["qos"] == "default"
    assert production["walltime"] == "14:30:00"
    assert production["cpus"] == 128
    assert pilot["memory"] == "240G"
    assert production["memory"] == "240G"
    assert pilot["node_hours"] + production["node_hours"] <= 15
    script = (REPO_ROOT / "hpc" / "slurm" / "sun-validation.sbatch").read_text()
    wrapper = (REPO_ROOT / "hpc" / "submit-sun-validation.sh").read_text()
    assert "--gpu" not in script.lower()
    assert "--gpu" not in wrapper.lower()
    assert "p201619" in wrapper


def test_stage_b_profiles_fit_one_node_and_forty_hours():
    pilot = preflight.JOB_PROFILES["stage-b-pilot"]
    production = preflight.JOB_PROFILES["stage-b"]
    assert pilot["qos"] == "test"
    assert pilot["walltime"] == "00:30:00"
    assert pilot["walltime_minutes"] <= 30
    assert pilot["cpus"] == 128
    assert production["qos"] == "default"
    assert production["walltime"] == "06:00:00"
    assert production["cpus"] == 128
    assert pilot["memory"] == production["memory"] == "240G"
    assert pilot["node_hours"] + production["node_hours"] <= 40
    script = (REPO_ROOT / "hpc" / "slurm" / "stage-b.sbatch").read_text()
    wrapper = (REPO_ROOT / "hpc" / "submit-stage-b.sh").read_text()
    assert "--gpu" not in script.lower()
    assert "--gpu" not in wrapper.lower()
    assert "p201619" in wrapper
    assert "ANUMA_STAGE_A_ACCEPTANCE" in wrapper
    assert "require_stage_a_acceptance" in wrapper


def test_full_monte_carlo_requires_default_qos_and_four_hour_cap(
    tmp_path, monkeypatch
):
    _configured_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("ANUMA_JOB_PROFILE", "monte-carlo-full")
    monkeypatch.setenv("ANUMA_QOS", "default")
    receipt = preflight.run_preflight(tmp_path / "receipt.json")
    assert receipt["job_profile"] == "monte-carlo-full"
    assert receipt["qos"] == "default"
    assert receipt["job_walltime"] == "04:00:00"
    assert receipt["job_node_hours_cap"] == 4.0


def test_preflight_refuses_to_overwrite_receipt(tmp_path, monkeypatch):
    _configured_environment(tmp_path, monkeypatch)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text("keep")
    with pytest.raises(FileExistsError):
        preflight.run_preflight(receipt_path)


@pytest.mark.parametrize(
    "path",
    (
        "hpc/bootstrap.sh",
        "hpc/discover-software.sh",
        "hpc/slurm/smoke-240m.sbatch",
        "hpc/slurm/monte-carlo.sbatch",
        "hpc/slurm/sun-validation.sbatch",
        "hpc/slurm/stage-b.sbatch",
    ),
)
def test_staging_module_regex_uses_one_literal_dot_escape(path):
    text = (REPO_ROOT / path).read_text()
    assert "'env/staging/2025\\.1'" in text
    assert "'env/staging/2025\\\\.1'" not in text


def test_stage_b_production_preflight_requires_stage_a_pass(tmp_path, monkeypatch):
    _configured_environment(tmp_path, monkeypatch, remaining=10.0)
    monkeypatch.setenv("ANUMA_JOB_PROFILE", "stage-b")
    monkeypatch.setenv("ANUMA_QOS", "default")
    monkeypatch.setenv("ANUMA_ACCOUNT", "p201619")
    captured = datetime.now(timezone.utc).isoformat()
    _write_evidence(
        Path(os.environ["ANUMA_ALLOCATION_EVIDENCE_FILE"]),
        {
            "source": "test fixture",
            "captured_at_utc": captured,
            "account": "p201619",
            "remaining_cpu_node_hours": 10.0,
        },
    )
    dem = tmp_path / "dem.tif"
    dem.write_bytes(b"fake-dem")
    monkeypatch.setenv("ANUMA_DEM", str(dem))
    acceptance = tmp_path / "validation-report.json"
    acceptance.write_text(
        json.dumps(
            {
                "status": "passed",
                "metrics": {"rmse": 0.04, "stop_rmse": 0.05, "passed": True},
            }
        )
    )
    monkeypatch.setenv("ANUMA_STAGE_A_ACCEPTANCE", str(acceptance))

    def fake_command(command):
        joined = " ".join(str(item) for item in command)
        if "show assoc" in joined:
            return "cluster|p201619|tester|cpu|default\n"
        if "show qos" in joined:
            return "default|48:00:00|\n"
        if "status --porcelain" in joined:
            return ""
        if "rev-parse HEAD" in joined:
            return "a" * 40 + "\n"
        return "ok\n"

    monkeypatch.setattr(preflight, "run_checked", fake_command)
    receipt = preflight.run_preflight(tmp_path / "receipt-stage-b.json")
    assert receipt["job_profile"] == "stage-b"
    assert receipt["stage_a_status"] == "passed"
    assert receipt["stage_a_stop_rmse"] == 0.05
    assert receipt["accelerators"] is False

    acceptance.write_text(
        json.dumps(
            {
                "status": "failed",
                "metrics": {"rmse": 0.09, "stop_rmse": 0.05, "passed": False},
            }
        )
    )
    with pytest.raises(RuntimeError, match="has not passed"):
        preflight.run_preflight(tmp_path / "receipt-stage-b-fail.json")
