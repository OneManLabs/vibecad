#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run Tier 1 cases only after a verified, data-free provider check."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
MIN_LIVE_TIMEOUT_SECONDS = 1560.0
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src" / "Mod" / "VibeCAD"))

from tools.probe_provider_readiness import run_probe  # noqa: E402
from VibeCADLiveBenchmark import (  # noqa: E402
    atomic_write_json,
    readiness_digest,
    validate_live_readiness,
    validate_runtime_identity,
    validate_unrated_live_run,
    sha256_file,
)
from tools.verify_vibecad_source_identity import (  # noqa: E402
    source_manifest,
    verify_source_identity,
)


def _source_commit(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )
    commit = str(completed.stdout or "").strip()
    if completed.returncode != 0 or len(commit) != 40 or any(
        character not in "0123456789abcdef" for character in commit.lower()
    ):
        raise RuntimeError("The live benchmark could not resolve the exact source commit.")
    status = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )
    if status.returncode != 0:
        raise RuntimeError("The live benchmark could not inspect its source tree.")
    if str(status.stdout or "").strip():
        raise RuntimeError(
            "Commit the complete worktree before a live benchmark run."
        )
    return commit.lower()


def _runtime_identity(root: Path, source_commit: str) -> dict[str, Any]:
    source = root / "src" / "Mod" / "VibeCAD"
    installed = root / "build" / "release" / "Mod" / "VibeCAD"
    verified = verify_source_identity(source, installed)
    source_entry = source / "TestVibeCADLiveTier1Benchmark.py"
    installed_entry = installed / "TestVibeCADLiveTier1Benchmark.py"
    if not source_entry.is_file() or not installed_entry.is_file():
        raise RuntimeError("The live benchmark GUI entry point is not installed.")
    source_entry_sha = sha256_file(source_entry)
    installed_entry_sha = sha256_file(installed_entry)
    if source_entry_sha != installed_entry_sha:
        raise RuntimeError("The installed live benchmark GUI entry point is stale.")
    source_runner = root / "tests" / "benchmark" / "tier1_live_provider_runner.py"
    source_catalog = root / "tests" / "benchmark" / "tier1_cases.json"
    installed_runner = installed / "live_benchmark" / source_runner.name
    installed_catalog = installed / "live_benchmark" / source_catalog.name
    for source_asset, installed_asset, label in (
        (source_runner, installed_runner, "GUI runner"),
        (source_catalog, installed_catalog, "case catalog"),
    ):
        if not source_asset.is_file() or not installed_asset.is_file():
            raise RuntimeError(f"The live benchmark {label} is not installed.")
        if sha256_file(source_asset) != sha256_file(installed_asset):
            raise RuntimeError(f"The installed live benchmark {label} is stale.")
    manifest = source_manifest(source)
    manifest_digest = hashlib.sha256(
        json.dumps(
            manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema": "vibecad-live-runtime-identity-v1",
        "version": 1,
        "source_commit": source_commit,
        "module_file_count": verified["file_count"],
        "module_manifest_sha256": manifest_digest,
        "gui_entry_sha256": source_entry_sha,
        "gui_runner_sha256": sha256_file(source_runner),
        "case_catalog_sha256": sha256_file(source_catalog),
    }


def prepare_runtime(root: Path, source_commit: str) -> dict[str, Any]:
    """Refresh and verify the copied VibeCAD runtime before provider readiness."""

    completed = subprocess.run(
        [
            "cmake",
            "--build",
            str(root / "build" / "release"),
            "--target",
            "VibeCADScripts",
            "-j2",
        ],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "The VibeCAD resource-copy build failed before provider readiness: "
            + str(completed.stdout or "")[-2000:]
        )
    return _runtime_identity(root, source_commit)


def execute_live_benchmark(
    *,
    freecad: Path,
    freecad_cmd: Path,
    run_directory: Path,
    source_commit: str,
    timeout_seconds: float,
    readiness_timeout_seconds: float,
    credential_validation_timeout: float,
    probe: Callable[..., dict[str, Any]] = run_probe,
    process_runner: Callable[..., Any] = subprocess.run,
    runtime_preparer: Callable[[Path, str], dict[str, Any]] = prepare_runtime,
    source_verifier: Callable[[Path], str] = _source_commit,
    runtime_rechecker: Callable[[Path, str], dict[str, Any]] = _runtime_identity,
) -> int:
    """Run the GUI child only after the stronger readiness contract passes."""

    if timeout_seconds < MIN_LIVE_TIMEOUT_SECONDS:
        raise ValueError(
            "The GUI timeout is shorter than the seven-case execution and validation bound."
        )
    run_directory.mkdir(parents=True, exist_ok=False)
    runtime_identity = validate_runtime_identity(
        runtime_preparer(ROOT, source_commit), source_commit=source_commit
    )
    runtime_identity_path = run_directory / "runtime-identity.json"
    atomic_write_json(runtime_identity_path, runtime_identity)
    runtime_identity_sha256 = sha256_file(runtime_identity_path)
    readiness_path = run_directory / "provider-readiness.json"
    readiness = probe(
        freecad_cmd,
        ROOT / "tools" / "provider_readiness_child.py",
        readiness_path,
        readiness_timeout_seconds,
        validate_credentials=True,
        credential_validation_timeout=credential_validation_timeout,
    )
    atomic_write_json(readiness_path, readiness)
    try:
        checked_readiness = validate_live_readiness(readiness)
    except Exception as exc:
        print(f"Live benchmark blocked before prompt or CAD data: {exc}")
        return 2

    launch_source_commit = source_verifier(ROOT)
    if launch_source_commit != source_commit:
        raise RuntimeError("The source commit changed after provider readiness.")
    launch_runtime_identity = validate_runtime_identity(
        runtime_rechecker(ROOT, source_commit), source_commit=source_commit
    )
    if launch_runtime_identity != runtime_identity:
        raise RuntimeError("The installed runtime changed after provider readiness.")

    raw_path = run_directory / "tier1-live-unrated-run.json"
    environment = dict(os.environ)
    environment.update(
        QT_QPA_PLATFORM="offscreen",
        VIBECAD_LIVE_BENCHMARK_OUTPUT=str(run_directory.resolve()),
        VIBECAD_LIVE_BENCHMARK_READINESS=str(readiness_path.resolve()),
        VIBECAD_LIVE_BENCHMARK_SOURCE_COMMIT=source_commit,
        VIBECAD_LIVE_BENCHMARK_PROVIDER=str(checked_readiness["provider"]),
        VIBECAD_LIVE_BENCHMARK_MODEL=str(checked_readiness["model"]),
        VIBECAD_LIVE_BENCHMARK_READINESS_SHA256=readiness_digest(
            checked_readiness
        ),
        VIBECAD_LIVE_BENCHMARK_RUNTIME_SHA256=runtime_identity_sha256,
        VIBECAD_LIVE_BENCHMARK_RUNTIME_IDENTITY=str(
            runtime_identity_path.resolve()
        ),
    )
    completed = process_runner(
        [str(freecad), "-t", "TestVibeCADLiveTier1Benchmark"],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    post_run_source_commit = source_verifier(ROOT)
    if post_run_source_commit != source_commit:
        raise RuntimeError("The source commit or clean-tree state changed during the live run.")
    post_run_runtime_identity = validate_runtime_identity(
        runtime_rechecker(ROOT, source_commit), source_commit=source_commit
    )
    if post_run_runtime_identity != runtime_identity:
        raise RuntimeError("The installed runtime changed during the live run.")
    if not raw_path.is_file():
        print(str(completed.stdout or "")[-4000:])
        raise RuntimeError("The live GUI benchmark produced no raw evidence file.")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    validate_unrated_live_run(raw)
    expected_readiness_digest = readiness_digest(checked_readiness)
    if (
        raw.get("provider") != checked_readiness["provider"]
        or raw.get("model") != checked_readiness["model"]
        or raw.get("source_commit") != source_commit
        or raw.get("readiness_sha256") != expected_readiness_digest
        or raw.get("runtime_identity_sha256") != runtime_identity_sha256
    ):
        raise RuntimeError(
            "The live GUI evidence does not match the verified provider, model, source, and readiness identity."
        )
    reported_ok = "\nOK\n" in str(completed.stdout or "")
    case_passed = all(
        item.get("passed") is True for item in raw.get("case_attempts", [])
    )
    raw["runner"] = {
        "attempt": 1,
        "case_evidence_passed": (
            case_passed and int(completed.returncode) == 0 and reported_ok
        ),
        "gui_runner_exit_code": int(completed.returncode),
        "gui_runner_reported_ok": reported_ok,
    }
    validate_unrated_live_run(raw, require_runner_result=True)
    atomic_write_json(raw_path, raw)
    print(
        "The live run retained seven unrated case attempts. "
        "Apply a separate human rating set before scoring."
    )
    return 0 if case_passed and reported_ok and completed.returncode == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freecad", default="build/release/bin/FreeCAD")
    parser.add_argument("--freecad-cmd", default="build/release/bin/FreeCADCmd")
    parser.add_argument("--output", default="build/benchmark/tier1-live")
    parser.add_argument(
        "--run-id",
        default=datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ"),
    )
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--readiness-timeout", type=float, default=30.0)
    parser.add_argument("--credential-validation-timeout", type=float, default=5.0)
    parser.add_argument(
        "--execute-live",
        action="store_true",
        help="Permit the seven-case live run after credential verification.",
    )
    args = parser.parse_args()
    if not args.execute_live:
        print("Live execution was not enabled. No provider request was sent.")
        return 2
    if not 0 < args.timeout <= 3600:
        raise ValueError("The GUI timeout must be greater than zero and at most one hour.")
    if args.timeout < MIN_LIVE_TIMEOUT_SECONDS:
        raise ValueError(
            "The GUI timeout must be at least 1560 seconds for seven bounded cases and validation."
        )
    if not 0 < args.readiness_timeout <= 60:
        raise ValueError("The readiness timeout must be greater than zero and at most 60 seconds.")
    if not 0 < args.credential_validation_timeout <= 15:
        raise ValueError(
            "The credential check timeout must be greater than zero and at most 15 seconds."
        )
    run_id = str(args.run_id or "").strip()
    if not run_id or Path(run_id).name != run_id:
        raise ValueError("The run ID must be one local directory name.")
    return execute_live_benchmark(
        freecad=(ROOT / args.freecad).resolve(),
        freecad_cmd=(ROOT / args.freecad_cmd).resolve(),
        run_directory=(ROOT / args.output / run_id).resolve(),
        source_commit=_source_commit(ROOT),
        timeout_seconds=args.timeout,
        readiness_timeout_seconds=args.readiness_timeout,
        credential_validation_timeout=args.credential_validation_timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
