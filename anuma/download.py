"""HTTP download that stops on failure with the exact URL."""

from __future__ import annotations

import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path


USER_AGENT = "ANUMA/0.1 (Neospace Challenge 2026; local ingest)"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    url: str,
    dest: Path,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
) -> Path:
    """Download `url` to `dest`, enforcing configured size and SHA-256."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (not expected_bytes or dest.stat().st_size == expected_bytes):
        if not expected_sha256 or _sha256(dest) == expected_sha256.lower():
            return dest
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            tmp = dest.with_suffix(dest.suffix + ".part")
            with tmp.open("wb") as fh:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
            tmp.replace(dest)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        print(f"Download failed\nURL: {url}\nError: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if expected_bytes and dest.stat().st_size != expected_bytes:
        msg = (
            f"Download size mismatch\nURL: {url}\n"
            f"Expected {expected_bytes} bytes, got {dest.stat().st_size}"
        )
        print(msg, file=sys.stderr)
        raise SystemExit(1)
    if expected_sha256:
        actual = _sha256(dest)
        if actual != expected_sha256.lower():
            dest.unlink(missing_ok=True)
            print(
                f"Download checksum mismatch\nURL: {url}\n"
                f"Expected SHA-256 {expected_sha256.lower()}, got {actual}",
                file=sys.stderr,
            )
            raise SystemExit(1)
    return dest
