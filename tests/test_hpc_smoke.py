"""Tests for the guarded HPC smoke-test contracts."""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from anuma import hpc_smoke
from anuma.config import load_config
from anuma.grid import Grid


@pytest.fixture
def _accept_tiny_test_grid(monkeypatch):
    monkeypatch.setattr(
        hpc_smoke.Grid, "from_json", classmethod(lambda cls, path, cfg=None: object())
    )


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture_manifest(tmp_path):
    grid = tmp_path / "grid.json"
    grid.write_text(
        json.dumps({"resolution_m": 240.0, "height": 2, "width": 3}) + "\n"
    )
    ingest_manifest = tmp_path / "ingest-manifest.json"
    ingest_manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "synthetic": False,
                "illumination_affine_revision": "pds3-pixel-center-v2",
                "resolution_m": 240.0,
            }
        )
        + "\n"
    )
    names = ("dem", "slope", "illumination", "hydrogen", "earth_visibility")
    for index, name in enumerate(names):
        np.save(tmp_path / f"{name}.npy", np.full((2, 3), index, dtype=np.float32))
    files = {
        "grid": {"path": "grid.json", "sha256": _sha(grid)},
        "ingest_manifest": {
            "path": ingest_manifest.name,
            "sha256": _sha(ingest_manifest),
        },
    }
    for name in names:
        path = tmp_path / f"{name}.npy"
        files[name] = {"path": path.name, "sha256": _sha(path)}
    manifest = tmp_path / "input-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "test-grid",
                "resolution_m": 240.0,
                "files": files,
            }
        )
        + "\n"
    )
    return manifest


def test_check_only_validates_pinned_inputs(tmp_path, _accept_tiny_test_grid):
    manifest = _fixture_manifest(tmp_path)
    result = hpc_smoke.run_smoke(manifest, tmp_path / "unused", check_only=True)
    assert result == {"status": "validated", "shape": [2, 3]}
    assert not (tmp_path / "unused").exists()


def test_checksum_mismatch_stops_before_compute(tmp_path):
    manifest = _fixture_manifest(tmp_path)
    with (tmp_path / "slope.npy").open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch for slope"):
        hpc_smoke.run_smoke(manifest, tmp_path / "out", check_only=True)


def test_manifest_path_cannot_escape_manifest_directory(tmp_path):
    manifest = _fixture_manifest(tmp_path)
    body = json.loads(manifest.read_text())
    body["files"]["slope"]["path"] = "../slope.npy"
    manifest.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="escapes its directory"):
        hpc_smoke.validate_input_manifest(manifest)


def test_manifest_rejects_pre_affine_fix_grid(tmp_path):
    manifest = _fixture_manifest(tmp_path)
    ingest = tmp_path / "ingest-manifest.json"
    body = json.loads(ingest.read_text())
    body["illumination_affine_revision"] = "legacy-offset"
    ingest.write_text(json.dumps(body))
    manifest_body = json.loads(manifest.read_text())
    manifest_body["files"]["ingest_manifest"]["sha256"] = _sha(ingest)
    manifest.write_text(json.dumps(manifest_body))
    with pytest.raises(ValueError, match="predates the affine fix"):
        hpc_smoke.validate_input_manifest(manifest)


def test_run_writes_ranking_and_output_manifest(
    tmp_path, monkeypatch, _accept_tiny_test_grid
):
    manifest = _fixture_manifest(tmp_path)
    module_list = tmp_path / "module-list.txt"
    module_list.write_text("env/release/2025.1\nPython/3.12.3\n")
    monkeypatch.setenv("ANUMA_MODULE_LIST_FILE", str(module_list))
    monkeypatch.setattr(hpc_smoke, "score", lambda layers, grid, cfg: object())
    monkeypatch.setattr(
        hpc_smoke,
        "rank_sites",
        lambda result: [{"row": 1, "col": 2, "total": 0.75}],
    )
    output = tmp_path / "out"
    result = hpc_smoke.run_smoke(manifest, output)
    assert result["status"] == "completed"
    assert result["input_manifest"]["declared_id"] == "test-grid"
    assert result["software"]["modules"]["entries"] == [
        "env/release/2025.1",
        "Python/3.12.3",
    ]
    ranking = json.loads((output / "ranking.json").read_text())
    assert ranking["resolution_m"] == 240.0
    assert result["outputs"]["ranking"]["sha256"] == _sha(output / "ranking.json")
    assert json.loads((output / "output-manifest.json").read_text()) == result


def test_nonempty_output_directory_is_rejected(
    tmp_path, monkeypatch, _accept_tiny_test_grid
):
    manifest = _fixture_manifest(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    (output / "existing").write_text("keep")
    with pytest.raises(ValueError, match="not empty"):
        hpc_smoke.run_smoke(manifest, output)


def test_grid_metadata_rejects_changed_transform(tmp_path):
    grid = Grid(load_config())
    path = tmp_path / "grid.json"
    grid.save_json(path)
    body = json.loads(path.read_text())
    body["transform"][2] += 240.0
    path.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="does not match"):
        Grid.from_json(path, cfg=load_config())
