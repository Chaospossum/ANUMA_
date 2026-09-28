"""HTTP download that stops on failure with the exact URL."""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path


USER_AGENT = "ANUMA/0.1 (Neospace Challenge 2026; local ingest)"


def download(url: str, dest: Path, expected_bytes: int | None = None) -> Path:
    """Download `url` to `dest`. Reuse a file that already has the expected size."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if expected_bytes and dest.exists() and dest.stat().st_size == expected_bytes:
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
    return dest
