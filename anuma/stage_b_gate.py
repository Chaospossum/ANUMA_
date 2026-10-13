"""Stage A acceptance gate for Stage B.

Kept free of rasterio so the submit wrapper can run it on a login node.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

FROZEN_STOP_RMSE = 0.05


def require_stage_a_acceptance(path: Path, *, stop_rmse: float = FROZEN_STOP_RMSE) -> dict:
    """Refuse Stage B unless Stage A recorded a pass at the frozen RMSE gate."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Stage A acceptance record is missing: {path}")
    report = json.loads(path.read_text())
    if float(stop_rmse) != FROZEN_STOP_RMSE:
        raise ValueError("Stage B must not change stop_rmse from 0.05")
    metrics = report.get("metrics") or {}
    if float(metrics.get("stop_rmse", -1)) != FROZEN_STOP_RMSE:
        raise ValueError("Stage A record stop_rmse must remain 0.05")
    if report.get("status") != "passed" or metrics.get("passed") is not True:
        raise ValueError("Stage A acceptance record has not passed")
    rmse = float(metrics.get("rmse", float("inf")))
    if not math.isfinite(rmse) or rmse > FROZEN_STOP_RMSE:
        raise ValueError(
            f"Stage A RMSE {rmse} exceeds the frozen stop_rmse {FROZEN_STOP_RMSE}"
        )
    return report
