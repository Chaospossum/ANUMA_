"""Tests for source/derived raster presence checks."""

from __future__ import annotations

import hashlib
import subprocess

from anuma.hpc_data_check import GRID_FILES, check_data


def _cfg():
    return {
        "paths": {"raw": "data/raw", "grid": "data/grid"},
        "layers": {
            "dem": {
                "filename": "dem.tif",
                "expected_bytes": 3,
                "sha256": hashlib.sha256(b"dem").hexdigest(),
            },
            "illumination": {
                "filename": "illum.jp2",
                "expected_bytes": 4,
                "sidecars": [{"filename": "illum.lbl"}],
            },
            "hydrogen": {"filename": "hydrogen.dat", "expected_bytes": 2},
        },
    }


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "grid").mkdir(parents=True)
    return tmp_path


def test_missing_sources_and_grids_are_not_ready(tmp_path):
    root = _repo(tmp_path)
    result = check_data(root, cfg=_cfg())
    assert result["ready"] is False
    assert result["raw_ready"] is False
    assert result["git_tracked_data_files"] == []
    assert "absent from Git" in result["message"]


def test_complete_untracked_dataset_is_ready(tmp_path):
    root = _repo(tmp_path)
    raw = root / "data" / "raw"
    grid = root / "data" / "grid"
    for name, contents in {
        "dem.tif": b"dem",
        "illum.jp2": b"moon",
        "illum.lbl": b"label",
        "hydrogen.dat": b"h2",
    }.items():
        (raw / name).write_bytes(contents)
    for name in GRID_FILES:
        (grid / name).write_bytes(b"derived")
    result = check_data(root, cfg=_cfg())
    assert result["ready"] is True
    assert result["raw_ready"] is True
    assert result["grid_ready"] is True


def test_wrong_source_hash_is_not_raw_ready(tmp_path):
    root = _repo(tmp_path)
    raw = root / "data" / "raw"
    (raw / "dem.tif").write_bytes(b"bad")
    (raw / "illum.jp2").write_bytes(b"moon")
    (raw / "illum.lbl").write_bytes(b"label")
    (raw / "hydrogen.dat").write_bytes(b"h2")
    result = check_data(root, cfg=_cfg())
    assert result["sources"]["dem.tif"]["size_matches"] is True
    assert result["sources"]["dem.tif"]["hash_matches"] is False
    assert result["raw_ready"] is False


def test_tracked_source_data_is_rejected(tmp_path):
    root = _repo(tmp_path)
    path = root / "data" / "raw" / "dem.tif"
    path.write_bytes(b"dem")
    subprocess.run(["git", "-C", str(root), "add", "-f", "data/raw/dem.tif"], check=True)
    result = check_data(root, cfg=_cfg())
    assert result["ready"] is False
    assert result["git_tracked_data_files"] == ["data/raw/dem.tif"]
