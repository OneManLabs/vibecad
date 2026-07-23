# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from tools.run_live_tier1_benchmark import _runtime_identity, execute_live_benchmark
from tools.vibecad_secure_process import run_bounded_process
from VibeCADBenchmark import (
    BenchmarkEvidenceError,
    VALIDATION_STAGES,
    failure_diagnostics,
    make_case_attempt,
    normalized_usage,
    unrated_instruction_adherence,
    validation_stage,
)
from VibeCADLiveBenchmark import (
    LIVE_EXECUTOR,
    LIVE_LIMITS,
    LIVE_RUN_SCHEMA,
    TIER1_CASE_IDS,
    all_edge_fillet_evidence,
    centered_hole_evidence,
    changed_constraint_evidence,
    mirrored_link_evidence,
    open_top_aperture_evidence,
    persist_partial_metrics_checkpoint,
    recover_partial_case_metrics,
    require_complete_partial_usage,
    symmetric_through_holes_evidence,
    validate_runtime_identity,
    validate_unrated_live_run,
)


SOURCE_COMMIT = "a" * 40


def _provider_runtime_record() -> dict:
    return {
        "schema": "vibecad-provider-runtime-attestation-v1",
        "version": 1,
        "platform": "darwin",
        "python": {},
        "provider_modules": [],
        "loaded_python_files": [],
        "distributions": [],
        "native_libraries": [],
        "components": [
            {
                "path": ".pixi/envs/default/bin/python3.11",
                "size": 1,
                "sha256": "f" * 64,
            }
        ],
    }


def _runtime_identity_record(source_commit: str = SOURCE_COMMIT) -> dict:
    return {
        "schema": "vibecad-live-runtime-identity-v3",
        "version": 3,
        "source_commit": source_commit,
        "module_file_count": 1,
        "module_manifest_sha256": "1" * 64,
        "gui_entry_sha256": "2" * 64,
        "gui_runner_sha256": "3" * 64,
        "case_catalog_sha256": "4" * 64,
        "runtime_files": [
            {
                "role": role,
                "path": path,
                "size": index + 1,
                "sha256": f"{index + 5:x}" * 64,
            }
            for index, (role, path) in enumerate(
                (
                    (
                        "benchmark_evidence_io_helper",
                        "tools/vibecad_benchmark_evidence_io.py",
                    ),
                    ("benchmark_launcher", "tools/run_live_tier1_benchmark.py"),
                    ("freecad_app_library", "build/release/lib/libFreeCADApp.dylib"),
                    ("freecad_cmd", "FreeCADCmd"),
                    ("freecad_gui", "FreeCAD"),
                    ("freecad_gui_library", "build/release/lib/libFreeCADGui.dylib"),
                    (
                        "provider_runtime_attestation",
                        "tools/provider_runtime_attestation.py",
                    ),
                    ("readiness_child", "tools/provider_readiness_child.py"),
                    ("readiness_probe", "tools/probe_provider_readiness.py"),
                    ("secure_process_helper", "tools/vibecad_secure_process.py"),
                )
            )
        ],
        "provider_runtime": _provider_runtime_record(),
    }


def _readiness(*, verified: bool) -> dict:
    return {
        "schema": "vibecad-provider-readiness-v1",
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "can_call_provider": verified,
        "prompt_sent": False,
        "document_data_sent": False,
        "credential_validation_performed": verified,
        "model_validation_performed": verified,
        "model_available": verified,
        "stage": "complete",
        "provider": "openai",
        "model": "gpt-test",
        "auth_status": "verified" if verified else "configured_unverified",
        "auth_source": "OS keyring",
        "online_by_default": True,
        "endpoint_identity": "https://api.openai.com/v1",
        "endpoint_sha256": hashlib.sha256(
            b"https://api.openai.com/v1"
        ).hexdigest(),
        "credential_binding_nonce": "2" * 64,
        "credential_fingerprint_algorithm": "hmac-sha256-v1",
        "credential_fingerprint": "3" * 64,
        "process_timed_out": False,
        "process_exit_code": 0,
        "ready_for_live_benchmark": verified,
    }


def _attempt(case_id: str, *, geometry_passed: bool = True) -> dict:
    stages = {
        name: (
            validation_stage(applicable=False, reason=f"{name} does not apply.")
            if name in {"follow_up", "export"}
            else validation_stage(
                applicable=True,
                passed=geometry_passed if name == "geometry" else True,
                evidence={"check": name},
            )
        )
        for name in VALIDATION_STAGES
    }
    artifact = f"{case_id}.FCStd"
    return make_case_attempt(
        tier=1,
        case_id=case_id,
        attempt=1,
        provider="openai",
        model="gpt-test",
        executor=LIVE_EXECUTOR,
        live_model_score=True,
        stages=stages,
        question_count=0,
        unnecessary_question_count=0,
        retry_count=0,
        usage=normalized_usage(input_tokens=10, output_tokens=5),
        instruction_adherence=unrated_instruction_adherence(
            "A separate human rating is required."
        ),
        elapsed_seconds=1,
        diagnostics=failure_diagnostics(stages),
        artifact_paths=[artifact],
        source_commit=SOURCE_COMMIT,
        artifact_sha256={artifact: "b" * 64},
        allow_unrated_live=True,
    )


def _runtime(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "provider_class": "OpenAIProvider",
        "provider_turn_count": 1,
        "provider_turns_observed": [1],
        "api_request_count": 1,
        "api_attempt_upper_bound": 3,
        "tool_call_count": 4,
        "retry_count": 0,
        "usage_event_count": 1,
        "fixture": {
            "kind": "benchmark_setup",
            "canonical_sha256": "c" * 64,
            "object_names": [],
        },
        "final_sha256": "d" * 64,
        "isolated_validation_ok": True,
    }


def _raw_run(runtime_digest: str) -> dict:
    attempts = [_attempt(case_id) for case_id in TIER1_CASE_IDS]
    return {
        "schema": LIVE_RUN_SCHEMA,
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tier": 1,
        "provider": "openai",
        "model": "gpt-test",
        "executor": LIVE_EXECUTOR,
        "source_commit": SOURCE_COMMIT,
        "readiness_sha256": "e" * 64,
        "runtime_identity_sha256": runtime_digest,
        "limits": dict(LIVE_LIMITS),
        "usage_summary": {
            "input_tokens": 70,
            "output_tokens": 35,
            "cached_input_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 105,
        },
        "case_runtime": [_runtime(case_id) for case_id in TIER1_CASE_IDS],
        "scored": False,
        "case_attempts": attempts,
    }


def test_unverified_readiness_stops_before_gui_or_prompt(tmp_path: Path) -> None:
    process_called = False

    def process_runner(*args, **kwargs):
        nonlocal process_called
        process_called = True
        raise AssertionError("The GUI runner must not start.")

    result = execute_live_benchmark(
        freecad=Path("FreeCAD"),
        freecad_cmd=Path("FreeCADCmd"),
        run_directory=tmp_path / "run",
        source_commit=SOURCE_COMMIT,
        timeout_seconds=1800,
        readiness_timeout_seconds=30,
        credential_validation_timeout=5,
        probe=lambda *args, **kwargs: _readiness(verified=False),
        process_runner=process_runner,
        runtime_preparer=lambda root, commit, *_paths: _runtime_identity_record(commit),
    )

    assert result == 2
    assert process_called is False


def test_live_child_receives_only_allowlisted_ambient_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hostile_values = {
        "OPENAI_API_KEY": "ambient-openai-secret",
        "ANTHROPIC_API_KEY": "ambient-anthropic-secret",
        "AWS_SECRET_ACCESS_KEY": "ambient-cloud-secret",
        "PYTHONPATH": "/tmp/hostile-python",
        "PYTHONHOME": "/tmp/hostile-home",
        "DYLD_INSERT_LIBRARIES": "/tmp/hostile.dylib",
        "DYLD_LIBRARY_PATH": "/tmp/hostile-loader",
        "LD_PRELOAD": "/tmp/hostile.so",
        "LD_LIBRARY_PATH": "/tmp/hostile-loader",
        "BASH_ENV": "/tmp/hostile-bash",
        "ENV": "/tmp/hostile-shell",
        "ZDOTDIR": "/tmp/hostile-zsh",
        "QT_PLUGIN_PATH": "/tmp/hostile-qt",
        "XDG_CONFIG_HOME": "/tmp/hostile-config",
        "XDG_DATA_HOME": "/tmp/hostile-data",
        "VIBECAD_HOSTILE_AMBIENT": "must-not-pass",
    }
    for name, value in hostile_values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HOME", str(tmp_path / "safe-home"))
    monkeypatch.setenv("PATH", "/tmp/hostile-path")

    def process_runner(command, **kwargs):
        environment = kwargs["env"]
        assert all(name not in environment for name in hostile_values)
        assert environment["HOME"] == str(tmp_path / "safe-home")
        assert environment["PATH"] == os.defpath
        assert environment["QT_QPA_PLATFORM"] == "offscreen"
        assert environment["VIBECAD_LIVE_BENCHMARK_SOURCE_COMMIT"] == SOURCE_COMMIT
        return _completed_process_result(command, **kwargs)

    result = execute_live_benchmark(
        freecad=Path("FreeCAD"),
        freecad_cmd=Path("FreeCADCmd"),
        run_directory=tmp_path / "run",
        source_commit=SOURCE_COMMIT,
        timeout_seconds=1800,
        readiness_timeout_seconds=30,
        credential_validation_timeout=5,
        probe=lambda *args, **kwargs: _readiness(verified=True),
        process_runner=process_runner,
        runtime_preparer=lambda root, commit, *_paths: _runtime_identity_record(commit),
        source_verifier=lambda root: SOURCE_COMMIT,
        runtime_rechecker=lambda root, commit, *_paths: _runtime_identity_record(commit),
    )

    assert result == 0


def test_live_run_directory_is_private(tmp_path: Path) -> None:
    run_directory = tmp_path / "private" / "run"
    result = execute_live_benchmark(
        freecad=Path("FreeCAD"),
        freecad_cmd=Path("FreeCADCmd"),
        run_directory=run_directory,
        source_commit=SOURCE_COMMIT,
        timeout_seconds=1800,
        readiness_timeout_seconds=30,
        credential_validation_timeout=5,
        probe=lambda *args, **kwargs: _readiness(verified=False),
        runtime_preparer=lambda root, commit, *_paths: _runtime_identity_record(commit),
    )

    assert result == 2
    assert stat.S_IMODE(run_directory.stat().st_mode) == 0o700


def test_live_run_directory_rejects_a_symlink_parent(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    prepared = False

    def prepare(root, commit, *_paths):
        nonlocal prepared
        prepared = True
        return _runtime_identity_record(commit)

    with pytest.raises(ValueError, match="symlink|unsafe"):
        execute_live_benchmark(
            freecad=Path("FreeCAD"),
            freecad_cmd=Path("FreeCADCmd"),
            run_directory=linked / "run",
            source_commit=SOURCE_COMMIT,
            timeout_seconds=1800,
            readiness_timeout_seconds=30,
            credential_validation_timeout=5,
            probe=lambda *args, **kwargs: _readiness(verified=False),
            runtime_preparer=prepare,
        )

    assert prepared is False
    assert not (actual / "run").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", float("inf")),
        ("readiness_timeout_seconds", float("nan")),
        ("readiness_timeout_seconds", float("-inf")),
        ("credential_validation_timeout", float("nan")),
        ("credential_validation_timeout", float("inf")),
    ),
)
def test_live_runner_rejects_non_finite_timeouts_before_side_effects(
    tmp_path: Path, field: str, value: float
) -> None:
    values = {
        "timeout_seconds": 1800.0,
        "readiness_timeout_seconds": 30.0,
        "credential_validation_timeout": 5.0,
    }
    values[field] = value
    run_directory = tmp_path / field

    with pytest.raises(ValueError, match="finite"):
        execute_live_benchmark(
            freecad=Path("FreeCAD"),
            freecad_cmd=Path("FreeCADCmd"),
            run_directory=run_directory,
            source_commit=SOURCE_COMMIT,
            probe=lambda *args, **kwargs: _readiness(verified=False),
            runtime_preparer=lambda root, commit, *_paths: _runtime_identity_record(commit),
            **values,
        )

    assert not run_directory.exists()


def test_bounded_process_retains_only_the_output_tail() -> None:
    payload_size = 1024 * 1024
    result = run_bounded_process(
        [
            sys.executable,
            "-c",
            f"import sys; sys.stdout.write('x' * {payload_size} + 'END'); sys.stdout.flush()",
        ],
        timeout=10,
        output_limit_bytes=4096,
    )

    assert result.returncode == 0
    assert result.output_truncated is True
    assert result.output_bytes_seen == payload_size + 3
    assert len(result.stdout.encode("utf-8")) <= 4096
    assert result.stdout.endswith("END")


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups are required.")
def test_bounded_process_timeout_kills_its_descendant_group(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child.pid"
    child_code = (
        "import signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(30)"
    )
    parent_code = (
        "import os,pathlib,signal,subprocess,sys,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"child=subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(f'{{os.getpid()}}:{{child.pid}}'); "
        "time.sleep(30)"
    )

    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded_process(
            [sys.executable, "-c", parent_code],
            timeout=0.4,
            termination_grace_seconds=0.1,
            output_limit_bytes=4096,
        )

    assert child_pid_path.is_file()
    parent_pid, child_pid = (
        int(value)
        for value in child_pid_path.read_text(encoding="utf-8").split(":")
    )

    def child_is_active() -> bool:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            return False
        status = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(child_pid)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        ).stdout.strip()
        return bool(status) and not status.startswith("Z")

    deadline = time.monotonic() + 3
    while child_is_active() and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        assert child_is_active() is False
    finally:
        try:
            if os.getpgid(child_pid) == parent_pid:
                os.killpg(parent_pid, signal.SIGKILL)
        except (PermissionError, ProcessLookupError):
            pass


def test_stale_runtime_stops_before_provider_readiness(tmp_path: Path) -> None:
    probe_called = False

    def probe(*args, **kwargs):
        nonlocal probe_called
        probe_called = True
        return _readiness(verified=True)

    with pytest.raises(RuntimeError, match="stale"):
        execute_live_benchmark(
            freecad=Path("FreeCAD"),
            freecad_cmd=Path("FreeCADCmd"),
            run_directory=tmp_path / "run",
            source_commit=SOURCE_COMMIT,
            timeout_seconds=1800,
            readiness_timeout_seconds=30,
            credential_validation_timeout=5,
            probe=probe,
            runtime_preparer=lambda root, commit, *_paths: (_ for _ in ()).throw(
                RuntimeError("The copied runtime is stale.")
            ),
        )

    assert probe_called is False


def test_runtime_identity_rejects_stale_installed_gui_entry(tmp_path: Path) -> None:
    source = tmp_path / "src" / "Mod" / "VibeCAD"
    installed = tmp_path / "build" / "release" / "Mod" / "VibeCAD"
    source.mkdir(parents=True)
    installed.mkdir(parents=True)
    module_names = (
        "VibeCADAcceptance.py",
        "VibeCADSession.py",
        "VibeCADGui.py",
        "VibeCADProject.py",
        "VibeCADAddonManagerPolicy.py",
        "VibeCADLiveBenchmark.py",
        "VibeCADImportAssets.py",
        "VibeCADStepValidator.py",
    )
    for name in module_names:
        (source / name).write_text(f"# {name}\n", encoding="utf-8")
        (installed / name).write_text(f"# {name}\n", encoding="utf-8")
    entry = "TestVibeCADLiveTier1Benchmark.py"
    (source / entry).write_text("# live entry\n", encoding="utf-8")
    (installed / entry).write_text("# live entry\n", encoding="utf-8")
    source_assets = tmp_path / "tests" / "benchmark"
    installed_assets = installed / "live_benchmark"
    source_assets.mkdir(parents=True)
    installed_assets.mkdir(parents=True)
    for name in ("tier1_live_provider_runner.py", "tier1_cases.json"):
        (source_assets / name).write_text(f"# {name}\n", encoding="utf-8")
        (installed_assets / name).write_text(f"# {name}\n", encoding="utf-8")
    runtime_bin = tmp_path / "build" / "release" / "bin"
    runtime_lib = tmp_path / "build" / "release" / "lib"
    runtime_tools = tmp_path / "tools"
    runtime_bin.mkdir(parents=True)
    runtime_lib.mkdir(parents=True)
    runtime_tools.mkdir(parents=True)
    for name in ("FreeCAD", "FreeCADCmd"):
        (runtime_bin / name).write_bytes(f"{name} runtime".encode("utf-8"))
    for name in ("libFreeCADApp.dylib", "libFreeCADGui.dylib"):
        (runtime_lib / name).write_bytes(f"{name} runtime".encode("utf-8"))
    for name in (
        "run_live_tier1_benchmark.py",
        "probe_provider_readiness.py",
        "provider_readiness_child.py",
        "vibecad_secure_process.py",
        "vibecad_benchmark_evidence_io.py",
        "provider_runtime_attestation.py",
    ):
        (runtime_tools / name).write_text(f"# {name}\n", encoding="utf-8")

    exact_identity = _runtime_identity(
        tmp_path,
        SOURCE_COMMIT,
        provider_attester=lambda *_args, **_kwargs: _provider_runtime_record(),
    )
    assert exact_identity["module_file_count"] == 8
    runtime_by_role = {
        item["role"]: item for item in exact_identity["runtime_files"]
    }
    freecad_path = runtime_bin / "FreeCAD"
    original_freecad = freecad_path.read_bytes()
    freecad_path.write_bytes(b"substituted FreeCAD runtime")
    substituted_identity = _runtime_identity(
        tmp_path,
        SOURCE_COMMIT,
        provider_attester=lambda *_args, **_kwargs: _provider_runtime_record(),
    )
    substituted_by_role = {
        item["role"]: item for item in substituted_identity["runtime_files"]
    }
    assert (
        substituted_by_role["freecad_gui"]["sha256"]
        != runtime_by_role["freecad_gui"]["sha256"]
    )
    freecad_path.write_bytes(original_freecad)

    substitute = tmp_path / "substitute-FreeCAD"
    substitute.write_bytes(original_freecad)
    freecad_path.unlink()
    freecad_path.symlink_to(substitute)
    with pytest.raises(RuntimeError, match="missing, unsafe, or unstable"):
        _runtime_identity(
            tmp_path,
            SOURCE_COMMIT,
            provider_attester=lambda *_args, **_kwargs: _provider_runtime_record(),
        )
    freecad_path.unlink()
    freecad_path.write_bytes(original_freecad)

    (installed / entry).write_text("# stale live entry\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="stale"):
        _runtime_identity(tmp_path, SOURCE_COMMIT)

    (installed / entry).write_text("# live entry\n", encoding="utf-8")
    (installed_assets / "tier1_live_provider_runner.py").write_text(
        "# stale runner\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="GUI runner.*stale"):
        _runtime_identity(tmp_path, SOURCE_COMMIT)


def test_live_runner_rejects_source_or_readiness_identity_drift(tmp_path: Path) -> None:
    readiness = _readiness(verified=True)

    def process_runner(command, **kwargs):
        environment = kwargs["env"]
        output = Path(environment["VIBECAD_LIVE_BENCHMARK_OUTPUT"])
        raw = _raw_run(environment["VIBECAD_LIVE_BENCHMARK_RUNTIME_SHA256"])
        raw["readiness_sha256"] = environment[
            "VIBECAD_LIVE_BENCHMARK_READINESS_SHA256"
        ]
        raw["source_commit"] = "f" * 40
        for attempt in raw["case_attempts"]:
            attempt["source_commit"] = "f" * 40
        (output / "tier1-live-unrated-run.json").write_text(
            json.dumps(raw), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0, stdout="\nOK\n")

    with pytest.raises(RuntimeError, match="does not match"):
        execute_live_benchmark(
            freecad=Path("FreeCAD"),
            freecad_cmd=Path("FreeCADCmd"),
            run_directory=tmp_path / "run",
            source_commit=SOURCE_COMMIT,
            timeout_seconds=1800,
            readiness_timeout_seconds=30,
            credential_validation_timeout=5,
            probe=lambda *args, **kwargs: readiness,
            process_runner=process_runner,
            runtime_preparer=lambda root, commit, *_paths: _runtime_identity_record(commit),
            source_verifier=lambda root: SOURCE_COMMIT,
            runtime_rechecker=lambda root, commit, *_paths: _runtime_identity_record(commit),
        )


def _completed_process_result(command, **kwargs):
    environment = kwargs["env"]
    output = Path(environment["VIBECAD_LIVE_BENCHMARK_OUTPUT"])
    raw = _raw_run(environment["VIBECAD_LIVE_BENCHMARK_RUNTIME_SHA256"])
    raw["readiness_sha256"] = environment[
        "VIBECAD_LIVE_BENCHMARK_READINESS_SHA256"
    ]
    (output / "tier1-live-unrated-run.json").write_text(
        json.dumps(raw), encoding="utf-8"
    )
    return SimpleNamespace(returncode=0, stdout="\nOK\n")


def test_live_runner_does_not_trust_or_require_a_stdout_ok_sentinel(
    tmp_path: Path,
) -> None:
    def process_runner(command, **kwargs):
        result = _completed_process_result(command, **kwargs)
        result.stdout = "Provider output without a runner sentinel."
        return result

    result = execute_live_benchmark(
        freecad=Path("FreeCAD"),
        freecad_cmd=Path("FreeCADCmd"),
        run_directory=tmp_path / "run",
        source_commit=SOURCE_COMMIT,
        timeout_seconds=1800,
        readiness_timeout_seconds=30,
        credential_validation_timeout=5,
        probe=lambda *args, **kwargs: _readiness(verified=True),
        process_runner=process_runner,
        runtime_preparer=lambda root, commit, *_paths: _runtime_identity_record(commit),
        source_verifier=lambda root: SOURCE_COMMIT,
        runtime_rechecker=lambda root, commit, *_paths: _runtime_identity_record(commit),
    )

    assert result == 0


def test_live_runner_rechecks_installed_runtime_after_gui_exit(tmp_path: Path) -> None:
    recheck_count = 0

    def runtime_rechecker(root, commit, *_paths):
        nonlocal recheck_count
        recheck_count += 1
        identity = _runtime_identity_record(commit)
        if recheck_count == 2:
            identity["gui_runner_sha256"] = "9" * 64
        return identity

    with pytest.raises(RuntimeError, match="changed during"):
        execute_live_benchmark(
            freecad=Path("FreeCAD"),
            freecad_cmd=Path("FreeCADCmd"),
            run_directory=tmp_path / "run",
            source_commit=SOURCE_COMMIT,
            timeout_seconds=1800,
            readiness_timeout_seconds=30,
            credential_validation_timeout=5,
            probe=lambda *args, **kwargs: _readiness(verified=True),
            process_runner=_completed_process_result,
            runtime_preparer=lambda root, commit, *_paths: _runtime_identity_record(commit),
            source_verifier=lambda root: SOURCE_COMMIT,
            runtime_rechecker=runtime_rechecker,
        )


def test_nonzero_gui_exit_cannot_pass_live_evidence(tmp_path: Path) -> None:
    def process_runner(command, **kwargs):
        result = _completed_process_result(command, **kwargs)
        result.returncode = 9
        return result

    result = execute_live_benchmark(
        freecad=Path("FreeCAD"),
        freecad_cmd=Path("FreeCADCmd"),
        run_directory=tmp_path / "run",
        source_commit=SOURCE_COMMIT,
        timeout_seconds=1800,
        readiness_timeout_seconds=30,
        credential_validation_timeout=5,
        probe=lambda *args, **kwargs: _readiness(verified=True),
        process_runner=process_runner,
        runtime_preparer=lambda root, commit, *_paths: _runtime_identity_record(commit),
        source_verifier=lambda root: SOURCE_COMMIT,
        runtime_rechecker=lambda root, commit, *_paths: _runtime_identity_record(commit),
    )

    assert result == 1
    raw = json.loads(
        (tmp_path / "run" / "tier1-live-unrated-run.json").read_text(
            encoding="utf-8"
        )
    )
    assert raw["runner"]["gui_runner_exit_code"] == 9
    assert raw["runner"]["case_evidence_passed"] is False


def test_runner_case_gate_must_match_cases_and_clean_exit() -> None:
    raw = _raw_run("8" * 64)
    raw["runner"] = {
        "attempt": 1,
        "case_evidence_passed": False,
        "gui_runner_exit_code": 0,
        "gui_runner_reported_ok": True,
    }

    with pytest.raises(Exception, match="does not match"):
        validate_unrated_live_run(raw, require_runner_result=True)


def test_live_run_rejects_modified_limit_values() -> None:
    raw = _raw_run("8" * 64)
    raw["limits"]["provider_turns_per_case"] = 13

    with pytest.raises(Exception, match="fixed contract"):
        validate_unrated_live_run(raw)


def test_passed_live_case_cannot_exceed_total_time_limit() -> None:
    raw = _raw_run("8" * 64)
    raw["case_attempts"][0]["elapsed_seconds"] = (
        raw["limits"]["case_total_timeout_seconds"] + 0.001
    )

    with pytest.raises(Exception, match="elapsed-time"):
        validate_unrated_live_run(raw)


@pytest.mark.parametrize("fault", ("missing_usage", "zero_usage", "missing_turn"))
def test_passed_live_case_requires_complete_request_usage_and_turn_evidence(
    fault: str,
) -> None:
    raw = _raw_run("8" * 64)
    runtime = raw["case_runtime"][0]
    if fault == "missing_usage":
        runtime["usage_event_count"] = 0
    elif fault == "zero_usage":
        usage = raw["case_attempts"][0]["normalized_usage"]
        for field in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "reasoning_tokens",
            "total_tokens",
        ):
            raw["usage_summary"][field] -= usage[field]
            usage[field] = 0
    else:
        runtime.update(
            provider_turn_count=2,
            provider_turns_observed=[2],
            api_request_count=2,
            api_attempt_upper_bound=6,
            usage_event_count=2,
        )

    with pytest.raises(Exception, match="provider-turn, usage|request-to-usage"):
        validate_unrated_live_run(raw)


def test_failed_live_case_cannot_hide_a_started_request_without_usage() -> None:
    raw = _raw_run("8" * 64)
    raw["case_attempts"][0] = _attempt(
        TIER1_CASE_IDS[0], geometry_passed=False
    )
    raw["case_runtime"][0]["usage_event_count"] = 0

    with pytest.raises(Exception, match="request-to-usage"):
        validate_unrated_live_run(raw)


def test_runtime_identity_contract_rejects_source_drift() -> None:
    identity = _runtime_identity_record()
    assert validate_runtime_identity(
        identity, source_commit=SOURCE_COMMIT
    ) == identity

    identity["source_commit"] = "f" * 40
    with pytest.raises(Exception, match="does not match"):
        validate_runtime_identity(identity, source_commit=SOURCE_COMMIT)


@pytest.mark.parametrize("fault", ("missing", "reordered", "escape", "digest"))
def test_runtime_identity_contract_rejects_unbound_runtime_files(fault: str) -> None:
    identity = _runtime_identity_record()
    if fault == "missing":
        identity["runtime_files"].pop()
    elif fault == "reordered":
        identity["runtime_files"][0], identity["runtime_files"][1] = (
            identity["runtime_files"][1],
            identity["runtime_files"][0],
        )
    elif fault == "escape":
        identity["runtime_files"][0]["path"] = "../unbound-FreeCAD"
    else:
        identity["runtime_files"][0]["sha256"] = "not-a-digest"

    with pytest.raises(Exception, match="runtime file|file roles"):
        validate_runtime_identity(identity, source_commit=SOURCE_COMMIT)


@pytest.mark.parametrize("fault", ("missing", "reordered", "escape", "digest"))
def test_runtime_identity_contract_rejects_unbound_provider_components(
    fault: str,
) -> None:
    identity = _runtime_identity_record()
    components = identity["provider_runtime"]["components"]
    components.append(
        {
            "path": ".pixi/envs/default/lib/provider-module.py",
            "size": 2,
            "sha256": "e" * 64,
        }
    )
    if fault == "missing":
        components.clear()
    elif fault == "reordered":
        components[0], components[1] = components[1], components[0]
    elif fault == "escape":
        components[0]["path"] = "../provider-python"
    else:
        components[0]["sha256"] = "not-a-digest"

    with pytest.raises(Exception, match="provider runtime|Provider runtime"):
        validate_runtime_identity(identity, source_commit=SOURCE_COMMIT)


def test_failed_case_recovery_preserves_real_partial_metrics() -> None:
    partial = {
        "schema": "vibecad-live-partial-metrics-v1",
        "version": 1,
        "started_at_epoch": 100.0,
        "measurements": {
            "elapsed_seconds": 8.0,
            "events": [{"event": "provider_usage"}],
            "question_count": 1,
            "unnecessary_question_count": 1,
            "retry_count": 2,
            "tool_call_count": 3,
            "api_request_count": 4,
            "provider_turns_observed": [1, 2, 3, 4],
            "usage_event_count": 4,
            "usage": normalized_usage(input_tokens=90, output_tokens=10),
        },
    }
    recovered = recover_partial_case_metrics(partial, now_epoch=112.0)

    assert recovered["elapsed_seconds"] == 12.0
    assert recovered["api_request_count"] == 4
    assert recovered["tool_call_count"] == 3
    assert recovered["retry_count"] == 2
    assert recovered["question_count"] == 1
    assert recovered["usage"]["total_tokens"] == 100


def test_checkpoint_failure_after_request_invalidates_unknown_usage(
    tmp_path: Path,
) -> None:
    state: dict = {}
    payload = {
        "schema": "vibecad-live-partial-metrics-v1",
        "version": 1,
        "started_at_epoch": 100.0,
        "measurements": {
            "elapsed_seconds": 1.0,
            "events": [{"event": "provider_request_started", "turn": 1}],
            "question_count": 0,
            "unnecessary_question_count": 0,
            "retry_count": 0,
            "tool_call_count": 0,
            "api_request_count": 1,
            "provider_turns_observed": [1],
            "usage_event_count": 0,
            "usage": normalized_usage(),
        },
    }

    def failing_writer(path, value):
        raise OSError("injected checkpoint failure")

    with pytest.raises(OSError, match="injected"):
        persist_partial_metrics_checkpoint(
            state,
            payload,
            tmp_path / "partial.json",
            writer=failing_writer,
        )

    recovered = recover_partial_case_metrics(
        state["partial"], now_epoch=102.0
    )
    assert recovered["api_request_count"] == 1
    with pytest.raises(BenchmarkEvidenceError, match="incomplete usage"):
        require_complete_partial_usage(recovered)


def test_live_freecad_runner_declares_exact_follow_up_validators() -> None:
    root = Path(__file__).resolve().parents[4]
    source = (
        root / "tests" / "benchmark" / "tier1_live_provider_runner.py"
    ).read_text(encoding="utf-8")

    assert "changed_constraint_evidence" in source
    assert "mirrored_link_evidence" in source
    assert "all_edge_fillet_evidence" in source
    assert "centered_hole_evidence" in source
    assert "open_top_aperture_evidence" in source
    assert "validate_saved_document" in source
    assert "provider_turn_count" in source


def test_centered_hole_validator_checks_axis_and_center() -> None:
    vector = lambda x, y, z: SimpleNamespace(x=x, y=y, z=z)
    curve = lambda x, y, z, axis_z=1: SimpleNamespace(
        Radius=3, Center=vector(x, y, z), Axis=vector(0, 0, axis_z)
    )
    shape = SimpleNamespace(
        BoundBox=SimpleNamespace(
            XMin=-20, XMax=20, YMin=-15, YMax=15, ZMin=0, ZMax=10
        ),
        Edges=[
            SimpleNamespace(Curve=curve(0, 0, 0)),
            SimpleNamespace(Curve=curve(0, 0, 10)),
        ],
    )
    assert centered_hole_evidence(shape, 3)["passed"] is True

    shape.Edges[1].Curve = curve(1, 0, 10)
    assert centered_hole_evidence(shape, 3)["passed"] is False


def test_open_top_validator_requires_centered_inner_rim_boundary() -> None:
    def bounds(width: float, depth: float, *, center_x: float = 0) -> SimpleNamespace:
        return SimpleNamespace(
            XMin=center_x - width / 2,
            XMax=center_x + width / 2,
            XLength=width,
            YMin=-depth / 2,
            YMax=depth / 2,
            YLength=depth,
        )

    vertex = lambda z: SimpleNamespace(Point=SimpleNamespace(z=z))
    def face_bounds(
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
        z_min: float,
        z_max: float,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            XMin=x_min,
            XMax=x_max,
            XLength=x_max - x_min,
            YMin=y_min,
            YMax=y_max,
            YLength=y_max - y_min,
            ZMin=z_min,
            ZMax=z_max,
        )

    outer = SimpleNamespace(BoundBox=bounds(60, 40))
    inner = SimpleNamespace(BoundBox=bounds(56, 36))
    rim = SimpleNamespace(
        Vertexes=[vertex(25)] * 8,
        Wires=[outer, inner],
        Area=60 * 40 - 56 * 36,
        BoundBox=face_bounds(-30, 30, -20, 20, 25, 25),
    )
    bottom = SimpleNamespace(
        Vertexes=[vertex(2)] * 4,
        Wires=[],
        Area=56 * 36,
        BoundBox=face_bounds(-28, 28, -18, 18, 2, 2),
    )
    walls = [
        SimpleNamespace(
            Vertexes=[vertex(2), vertex(25)], Wires=[], Area=36 * 23,
            BoundBox=face_bounds(x, x, -18, 18, 2, 25),
        )
        for x in (-28, 28)
    ] + [
        SimpleNamespace(
            Vertexes=[vertex(2), vertex(25)], Wires=[], Area=56 * 23,
            BoundBox=face_bounds(-28, 28, y, y, 2, 25),
        )
        for y in (-18, 18)
    ]
    shape = SimpleNamespace(
        BoundBox=SimpleNamespace(
            XMin=-30,
            XMax=30,
            XLength=60,
            YMin=-20,
            YMax=20,
            YLength=40,
            ZMin=0,
            ZMax=25,
            ZLength=25,
        ),
        Faces=[rim, bottom, *walls],
    )
    assert open_top_aperture_evidence(shape, 2)["passed"] is True

    inner.BoundBox = bounds(56, 36, center_x=1)
    assert open_top_aperture_evidence(shape, 2)["passed"] is False

    inner.BoundBox = bounds(56, 36)
    shape.Faces.pop()
    assert open_top_aperture_evidence(shape, 2)["passed"] is False


def test_all_edge_fillet_validator_requires_all_twelve_native_links() -> None:
    base = SimpleNamespace(Name="Cube", Shape=SimpleNamespace(Edges=[object()] * 12))
    feature = SimpleNamespace(
        TypeId="PartDesign::Fillet",
        Base=(base, [f"Edge{index}" for index in range(1, 13)]),
    )
    assert all_edge_fillet_evidence(feature)["passed"] is True

    feature.Base = (base, [f"Edge{index}" for index in range(1, 12)])
    assert all_edge_fillet_evidence(feature)["passed"] is False


def test_mirror_validator_requires_native_link_to_selected_source() -> None:
    source = SimpleNamespace(Name="SourcePocket")
    plane = SimpleNamespace(Name="YZ_Plane")
    feature = SimpleNamespace(
        TypeId="PartDesign::Mirrored",
        Originals=[source],
        MirrorPlane=(plane, []),
    )
    assert mirrored_link_evidence(
        feature, "SourcePocket", "YZ_Plane"
    )["passed"] is True
    assert mirrored_link_evidence(
        feature, "OtherPocket", "YZ_Plane"
    )["passed"] is False


def test_mirror_geometry_requires_both_symmetric_through_hole_centers() -> None:
    vector = lambda x, y, z: SimpleNamespace(x=x, y=y, z=z)
    edges = [
        SimpleNamespace(
            Curve=SimpleNamespace(
                Radius=2.5,
                Center=vector(x, 0, z),
                Axis=vector(0, 0, 1),
            )
        )
        for x in (-10, 10)
        for z in (0, 5)
    ]
    shape = SimpleNamespace(
        BoundBox=SimpleNamespace(ZMin=0, ZMax=5), Edges=edges
    )
    assert symmetric_through_holes_evidence(
        shape, 2.5, ((-10, 0), (10, 0))
    )["passed"] is True

    shape.Edges[-1].Curve.Center = vector(9, 0, 5)
    assert symmetric_through_holes_evidence(
        shape, 2.5, ((-10, 0), (10, 0))
    )["passed"] is False


def test_dimension_validator_requires_original_constraint_identity() -> None:
    sketch = SimpleNamespace(
        Name="Profile",
        Constraints=[
            SimpleNamespace(
                Type="DistanceX", Value=55.0, Name="BenchmarkWidth"
            )
        ],
        GeometryCount=4,
        ConstraintCount=11,
    )
    document = SimpleNamespace(getObject=lambda name: sketch if name == "Profile" else None)
    fixture = {
        "sketch": "Profile",
        "width_constraint_index": 0,
        "width_constraint_name": "BenchmarkWidth",
        "sketch_geometry_count": 4,
        "sketch_constraint_count": 11,
    }
    assert changed_constraint_evidence(document, fixture)["passed"] is True

    sketch.Constraints[0].Value = 54.0
    assert changed_constraint_evidence(document, fixture)["passed"] is False
