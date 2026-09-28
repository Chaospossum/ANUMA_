"""Load `config/layers.yaml`."""

from pathlib import Path

import yaml


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_config(path: Path | None = None) -> dict:
    """Return the layers config as a dict."""
    cfg_path = Path(path) if path else repo_root() / "config" / "layers.yaml"
    with cfg_path.open() as fh:
        return yaml.safe_load(fh)
