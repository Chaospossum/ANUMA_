"""Export static score and criterion figures from ingested grid arrays."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from anuma.config import load_config, repo_root
from anuma.grid import Grid
from anuma.heatmap import render_png
from anuma.quicklook import write_quicklook
from anuma.score import score


def load_weights(path: Path, cfg: dict) -> dict[str, float]:
    """Load a weight mapping and reject layers outside the public config."""
    document = load_config(path)
    if not isinstance(document, dict) or set(document) != {"weights"}:
        raise ValueError("Weights file must contain only a 'weights' mapping.")
    raw = document["weights"]
    if not isinstance(raw, dict) or not raw:
        raise ValueError("'weights' must be a non-empty mapping.")

    allowed = set(cfg["scoring"]["weights"])
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"Unknown weighted layer(s): {', '.join(unknown)}")

    weights = {name: float(value) for name, value in raw.items()}
    if any(not np.isfinite(value) or value < 0.0 for value in weights.values()):
        raise ValueError("Weights must be finite and non-negative.")
    if sum(weights.values()) <= 0.0:
        raise ValueError("At least one weight must be positive.")
    return weights


def export_figures(
    weights_path: Path,
    grid_dir: Path,
    output_dir: Path,
    cfg: dict | None = None,
) -> list[Path]:
    """Score saved arrays and write the combined and normalized PNG figures."""
    cfg = cfg if cfg is not None else load_config()
    weights = load_weights(weights_path, cfg)
    names = ["slope", *weights]
    missing = [name for name in names if not (grid_dir / f"{name}.npy").is_file()]
    if missing:
        joined = ", ".join(f"{name}.npy" for name in missing)
        raise FileNotFoundError(f"Missing grid layer(s) in {grid_dir}: {joined}")

    layers = {name: np.load(grid_dir / f"{name}.npy") for name in names}
    shapes = {array.shape for array in layers.values()}
    if len(shapes) != 1 or any(array.ndim != 2 for array in layers.values()):
        raise ValueError("Grid layers must be same-shaped 2-D arrays.")

    result = score(layers, weights=weights, grid=Grid(cfg), cfg=cfg)
    output_dir.mkdir(parents=True, exist_ok=True)

    written = [output_dir / "score.png"]
    written[0].write_bytes(render_png(result.combined, cmap="viridis"))
    for name, normalized in result.normalized.items():
        path = output_dir / f"{name}.png"
        write_quicklook(normalized, path, cmap="viridis")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    root = repo_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument(
        "--grid-dir", type=Path, default=root / cfg["paths"]["grid"]
    )
    parser.add_argument("--output-dir", type=Path, default=root / "out" / "static")
    args = parser.parse_args(argv)

    for path in export_figures(args.weights, args.grid_dir, args.output_dir, cfg):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
