#!/usr/bin/env python3
"""Read-only MeluXina preflight; creates a short-lived receipt on success."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from anuma.hpc_data_check import check_data
from anuma.hpc_smoke import run_smoke, sha256_file

REQUIRED_ENV = (
    "ANUMA_ACCOUNT",
    "ANUMA_PARTITION",
    "ANUMA_QOS",
    "ANUMA_INPUT_MANIFEST",
    "ANUMA_DATA_ROOT",
    "ANUMA_OUTPUT_ROOT",
    "ANUMA_SCRATCH_DIR",
    "ANUMA_ALLOCATION_EVIDENCE_FILE",
    "ANUMA_STORAGE_EVIDENCE_FILE",
)
OUTPUT_HEADROOM_BYTES = 100 * 1024 * 1024
JOB_PROFILES = {
    "smoke": {
        "qos": "test",
        "walltime": "00:30:00",
        "walltime_minutes": 30,
        "cpus": 4,
        "memory": "16G",
        "node_hours": 0.5,
        "core_hours": 2.0,
    },
    "monte-carlo-pilot": {
        "qos": "test",
        "walltime": "00:30:00",
        "walltime_minutes": 30,
        "cpus": 4,
        "memory": "16G",
        "node_hours": 0.5,
        "core_hours": 2.0,
    },
    "monte-carlo-full": {
        "qos": "default",
        "walltime": "04:00:00",
        "walltime_minutes": 4 * 60,
        "cpus": 4,
        "memory": "32G",
        "node_hours": 4.0,
        "core_hours": 16.0,
    },
}
QOS_MAX_WALL_MINUTES = {
    "dev": 6 * 60,
    "test": 30,
    "short": 6 * 60,
    "short-preempt": 6 * 60,
    "default": 48 * 60,
    "long": 144 * 60,
    "large": 24 * 60,
    "urgent": 6 * 60,
}
DEFAULT_ENV = {
    "ANUMA_ACCOUNT": "p201619",
    "ANUMA_PARTITION": "cpu",
    "ANUMA_SCRATCH_DIR": "/project/scratch/p201619",
    "ANUMA_DATA_ROOT": "/project/home/p201619/ANUMA_",
}


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


def _load_fresh_evidence(path: Path, required: tuple[str, ...]) -> dict:
    path = path.resolve()
    stat = path.stat()
    if not path.is_file() or stat.st_uid != os.getuid() or stat.st_mode & 0o022:
        fail(f"evidence must be a user-owned, non-group/world-writable file: {path}")
    evidence = json.loads(path.read_text())
    missing = [key for key in required if key not in evidence]
    if missing:
        fail(f"{path} is missing fields: {', '.join(missing)}")
    captured = datetime.fromisoformat(evidence["captured_at_utc"])
    if captured.tzinfo is None:
        fail(f"evidence timestamp must include a UTC offset: {path}")
    age = (datetime.now(timezone.utc) - captured).total_seconds()
    if not 0 <= age <= 86400:
        fail(f"evidence must be captured within the last 24 hours: {path}")
    if not str(evidence["source"]).strip():
        fail(f"evidence source is empty: {path}")
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "source": evidence["source"],
        "captured_at_utc": evidence["captured_at_utc"],
        **{key: evidence[key] for key in required if key not in ("source", "captured_at_utc")},
    }


def run_preflight(receipt_path: Path) -> dict:
    for name, value in DEFAULT_ENV.items():
        os.environ.setdefault(name, value)
    job_profile = os.environ.get("ANUMA_JOB_PROFILE", "smoke")
    if job_profile not in JOB_PROFILES:
        fail(f"unknown ANUMA_JOB_PROFILE {job_profile!r}")
    profile = JOB_PROFILES[job_profile]
    os.environ.setdefault("ANUMA_QOS", str(profile["qos"]))
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        fail("missing required environment variables: " + ", ".join(missing))
    for command in ("myquota", "sbatch", "sacctmgr", "scontrol"):
        if shutil.which(command) is None:
            fail(f"required Slurm command is unavailable: {command}")

    account = os.environ["ANUMA_ACCOUNT"]
    partition = os.environ["ANUMA_PARTITION"]
    qos = os.environ["ANUMA_QOS"]
    if qos not in QOS_MAX_WALL_MINUTES:
        fail(f"unknown ANUMA_QOS {qos!r}; use a documented MeluXina QOS")
    if qos != profile["qos"]:
        fail(f"{job_profile} requires QOS {profile['qos']!r}")
    if int(profile["walltime_minutes"]) > QOS_MAX_WALL_MINUTES[qos]:
        fail(f"{job_profile} walltime exceeds the {qos} QOS limit")
    association = run_checked(
        [
            "sacctmgr",
            "-nP",
            "show",
            "assoc",
            "where",
            f"user={os.environ['USER']}",
            f"account={account}",
            "format=Cluster,Account,User,Partition,QOS",
        ]
    )
    if not _association_matches(association, account):
        fail(f"no Slurm association found for user={os.environ['USER']} account={account}")
    run_checked(["scontrol", "show", "partition", partition])
    if not run_checked(
        ["sacctmgr", "-nP", "show", "qos", qos, "format=Name,MaxWall,MaxJobsPU"]
    ).strip():
        fail(f"Slurm QOS is unavailable: {qos}")

    allocation = _load_fresh_evidence(
        Path(os.environ["ANUMA_ALLOCATION_EVIDENCE_FILE"]),
        ("source", "captured_at_utc", "account", "remaining_cpu_node_hours"),
    )
    if allocation["account"] != account:
        fail("allocation evidence account does not match ANUMA_ACCOUNT")
    remaining = float(allocation["remaining_cpu_node_hours"])
    if not math.isfinite(remaining) or remaining < float(profile["node_hours"]):
        fail(
            f"remaining allocation {remaining} CPU node-hours is below "
            f"required {profile['node_hours']}"
        )
    storage = _load_fresh_evidence(
        Path(os.environ["ANUMA_STORAGE_EVIDENCE_FILE"]),
        ("source", "captured_at_utc", "quota_bytes", "used_bytes"),
    )
    quota = int(storage["quota_bytes"])
    used = int(storage["used_bytes"])
    if quota < 0 or used < 0 or used > quota:
        fail("storage evidence contains an invalid quota/usage pair")
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
    data_root = Path(os.environ["ANUMA_DATA_ROOT"]).resolve()
    try:
        input_manifest.relative_to(data_root)
    except ValueError:
        fail("ANUMA_INPUT_MANIFEST must be within ANUMA_DATA_ROOT")
    presence = check_data(data_root)
    if not presence["ready"]:
        fail(presence["message"])
    run_smoke(input_manifest, output_root / ".anuma-check-only", check_only=True)
    repo_root = Path(__file__).resolve().parents[1]
    if run_checked(["git", "-C", str(repo_root), "status", "--porcelain"]).strip():
        fail("repository must be clean before a preflight receipt is created")
    git_commit = run_checked(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"]
    ).strip()

    receipt = {
        "schema_version": 1,
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "user": os.environ["USER"],
        "account": account,
        "partition": partition,
        "qos": qos,
        "job_profile": job_profile,
        "input_manifest": str(input_manifest),
        "input_manifest_sha256": sha256_file(input_manifest),
        "data_root": str(data_root),
        "output_root": str(output_root),
        "scratch_dir": str(scratch),
        "remaining_cpu_node_hours": remaining,
        "storage_quota_bytes": quota,
        "storage_used_bytes": used,
        "allocation_evidence": allocation,
        "storage_evidence": storage,
        "git_commit": git_commit,
        "job_node_hours_cap": profile["node_hours"],
        "job_core_hours": profile["core_hours"],
        "job_walltime": profile["walltime"],
        "job_cpus": profile["cpus"],
        "job_memory": profile["memory"],
    }
    canonical = json.dumps(receipt, sort_keys=True).encode()
    receipt["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
        handle.write("\n")
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
