"""Download integrity tests."""

from __future__ import annotations

import hashlib

import pytest

from anuma.download import download


def test_download_enforces_size_and_sha256(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"verified source")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    destination = tmp_path / "raw" / "source.bin"
    result = download(
        source.as_uri(),
        destination,
        expected_bytes=source.stat().st_size,
        expected_sha256=expected,
    )
    assert result.read_bytes() == source.read_bytes()


def test_download_removes_checksum_mismatch(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"unexpected")
    destination = tmp_path / "raw" / "source.bin"
    with pytest.raises(SystemExit):
        download(
            source.as_uri(),
            destination,
            expected_bytes=source.stat().st_size,
            expected_sha256="0" * 64,
        )
    assert not destination.exists()
