#!/usr/bin/env python3
"""Read-only MeluXina preflight; creates a short-lived receipt on success."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from anuma.hpc_smoke import run_smoke, sha256_file

REQUIRED_ENV = (
    "ANUMA_ACCOUNT",
    "ANUMA_PARTITION",
    "ANUMA_INPUT_MANIFEST",
    "ANUMA_OUTPUT_ROOT",
    "ANUMA_SCRATCH_DIR",
    "ANUMA_PROJECT_REMAINING_CPU_HOURS",
    "ANUMA_PROJECT_STORAGE_QUOTA_BYTES",
    "ANUMA_PROJECT_STORAGE_USED_BYTES",
    "ANUMA_ALLOCATION_EVIDENCE",
    "ANUMA_STORAGE_EVIDENCE",
)
JOB_CPU_HOURS = 2.0  # 4 CPUs * 30 minutes
OUTPUT_HEADROOM_BYTES = 100 * 1024 * 1024


def fail(message: str) -> None:
    raise RuntimeError(message)


def run_checked(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        fail(f"{' '.join(command)} failed: {detail}")
    return result.stdout


def _require_directory(path: Path, name: str) -> None:
    if not path.is_dir():
        fail(f"{name} is not an existing directory: {path}")
    if not os.access(path, os.W_OK | os.X_OK):
        fail(f"{name} is not writable/searchable: {path}")


def _association_matches(output: str, account: str) -> bool:
    for line in output.splitlines():
        fields = line.split("|")
        if len(fields) >= 3 and fields[1] == account and fields[2] == os.environ["USER"]:
            return True
    return False


def run_preflight(receipt_path: Path) -> dict:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        fail("missing required environment variables: " + ", ".join(missing))
    for command in ("sbatch", "sacctmgr", "scontrol"):
        if shutil.which(command) is None:
            fail(f"required Slurm command is unavailable: {command}")

    account = os.environ["ANUMA_ACCOUNT"]
    partition = os.environ["ANUMA_PARTITION"]
    association = run_checked(
        [
            "sacctmgr",
            "-nP",
            "show",
            "assoc",
            f"where user={os.environ['USER']}",
            f"account={account}",
            "format=Cluster,Account,User,Partition,QOS",
        ]
    )
    if not _association_matches(association, account):
        fail(f"no Slurm association found for user={os.environ['USER']} account={account}")
    run_checked(["scontrol", "show", "partition", partition])

    remaining = float(os.environ["ANUMA_PROJECT_REMAINING_CPU_HOURS"])
    if remaining < JOB_CPU_HOURS:
        fail(f"remaining allocation {remaining} CPU-hours is below required {JOB_CPU_HOURS}")
    quota = int(os.environ["ANUMA_PROJECT_STORAGE_QUOTA_BYTES"])
    used = int(os.environ["ANUMA_PROJECT_STORAGE_USED_BYTES"])
    if quota - used < OUTPUT_HEADROOM_BYTES:
        fail("verified project storage has less than 100 MiB free")

    output_root = Path(os.environ["ANUMA_OUTPUT_ROOT"]).resolve()
    scratch = Path(os.environ["ANUMA_SCRATCH_DIR"]).resolve()
    _require_directory(output_root, "ANUMA_OUTPUT_ROOT")
    _require_directory(scratch, "ANUMA_SCRATCH_DIR")
    fs_free = shutil.disk_usage(output_root).free
    if fs_free < OUTPUT_HEADROOM_BYTES:
        fail("output filesystem has less than 100 MiB free")

    input_manifest = Path(os.environ["ANUMA_INPUT_MANIFEST"]).resolve()
    run_smoke(input_manifest, output_root / ".anuma-check-only", check_only=True)

    receipt = {
        "schema_version": 1,
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "user": os.environ["USER"],
        "account": account,
        "partition": partition,
        "input_manifest": str(input_manifest),
        "input_manifest_sha256": sha256_file(input_manifest),
        "output_root": str(output_root),
        "scratch_dir": str(scratch),
        "remaining_cpu_hours": remaining,
        "storage_quota_bytes": quota,
        "storage_used_bytes": used,
        "allocation_evidence": os.environ["ANUMA_ALLOCATION_EVIDENCE"],
        "storage_evidence": os.environ["ANUMA_STORAGE_EVIDENCE"],
        "job_cpu_hours_cap": JOB_CPU_HOURS,
        "job_walltime": "00:30:00",
        "job_cpus": 4,
    }
    canonical = json.dumps(receipt, sort_keys=True).encode()
    receipt["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = run_preflight(args.receipt.resolve())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"preflight failed: {exc}\n")
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
