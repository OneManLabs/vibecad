# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from VibeCADImportAssets import register_import_asset
import VibeCADStepValidator as validator


PROJECT_ID = "step-validator-test"
ASSET_ID = "d" * 32
BREP_A = b"DBRep_DrawableShape\nvalidated-solid-A\n"
BREP_B = b"DBRep_DrawableShape\nvalidated-solid-B\n"


def _registered(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "project"
    source = tmp_path / "part.step"
    source.write_bytes(b"deterministic STEP fixture bytes")
    asset = register_import_asset(
        root,
        PROJECT_ID,
        source,
        policy_check=lambda: None,
        permission_check=lambda _permission: None,
        asset_id_factory=lambda: ASSET_ID,
        now=lambda: "2026-07-22T12:00:00Z",
    )
    return root, asset


def _shape() -> dict:
    return {
        "shape_type": "Solid",
        "null": False,
        "valid": True,
        "topology": {
            "solids": 1,
            "shells": 1,
            "faces": 6,
            "edges": 12,
            "vertices": 8,
        },
        "bounds_mm": {
            "min_x": 0.0,
            "min_y": 0.0,
            "min_z": 0.0,
            "max_x": 40.0,
            "max_y": 30.0,
            "max_z": 10.0,
            "size_x": 40.0,
            "size_y": 30.0,
            "size_z": 10.0,
        },
        "volume_mm3": 12000.0,
    }


def _result(request: dict, **changes) -> dict:
    content = {
        "schema": validator.STEP_VALIDATION_RESULT_SCHEMA,
        "version": validator.STEP_VALIDATION_VERSION,
        "ok": True,
        "project_id": request["project_id"],
        "asset_id": request["asset_id"],
        "asset_sha256": request["asset_sha256"],
        "size_bytes": request["size_bytes"],
        "format": "step",
        "shape": _shape(),
        "brep_sha256": hashlib.sha256(BREP_A).hexdigest(),
        "brep_size_bytes": len(BREP_A),
        "errors": [],
    }
    content.update(changes)
    return {
        **content,
        "evidence_sha256": validator._content_digest(content, "evidence_sha256"),
    }


def _successful_runner(mutator=None):
    def run(_command, **kwargs):
        environment = kwargs["environment"]
        request = json.loads(
            Path(environment["VIBECAD_STEP_VALIDATION_REQUEST"]).read_text(
                encoding="utf-8"
            )
        )
        artifact = Path(environment["VIBECAD_STEP_VALIDATION_BREP"])
        artifact.write_bytes(BREP_A)
        artifact.chmod(0o600)
        result = _result(request)
        if mutator is not None:
            mutator(result, request)
        Path(environment["VIBECAD_STEP_VALIDATION_RESULT"]).write_text(
            json.dumps(result), encoding="utf-8"
        )
        return {
            "started": True,
            "returncode": 0,
            "cancelled": False,
            "timed_out": False,
            "memory_exceeded": False,
        }

    return run


def test_isolated_result_is_content_bound_and_contains_geometry_evidence(
    tmp_path: Path,
) -> None:
    root, asset = _registered(tmp_path)
    candidate = validator.validate_registered_step(
        root,
        PROJECT_ID,
        ASSET_ID,
        freecadcmd=sys.executable,
        process_runner=_successful_runner(),
    )
    try:
        result = candidate.provider_evidence()
        assert result["ok"] is True
        assert result["asset_sha256"] == asset["sha256"]
        assert result["shape"]["bounds_mm"]["size_x"] == 40.0
        assert result["shape"]["topology"]["solids"] == 1
        assert result["shape"]["volume_mm3"] == 12000.0
        assert result["brep_sha256"] == hashlib.sha256(BREP_A).hexdigest()
        assert "path" not in json.dumps(result)
        assert str(tmp_path) not in json.dumps(result)
    finally:
        candidate.cleanup()
    assert candidate.artifact_available() is False


@pytest.mark.parametrize(
    ("process", "code"),
    [
        ({"started": False}, "STEP_VALIDATOR_START_FAILED"),
        (
            {
                "started": True,
                "returncode": -9,
                "cancelled": False,
                "timed_out": True,
                "memory_exceeded": False,
            },
            "STEP_VALIDATION_TIMEOUT",
        ),
        (
            {
                "started": True,
                "returncode": -9,
                "cancelled": False,
                "timed_out": False,
                "memory_exceeded": True,
            },
            "STEP_VALIDATION_MEMORY_LIMIT",
        ),
        (
            {
                "started": True,
                "returncode": -11,
                "cancelled": False,
                "timed_out": False,
                "memory_exceeded": False,
                "output_exceeded": True,
            },
            "STEP_VALIDATION_OUTPUT_LIMIT",
        ),
        (
            {
                "started": True,
                "returncode": -11,
                "cancelled": False,
                "timed_out": False,
                "memory_exceeded": False,
            },
            "STEP_VALIDATOR_CRASHED",
        ),
        (
            {
                "started": True,
                "returncode": 0,
                "cancelled": False,
                "timed_out": False,
                "memory_exceeded": False,
            },
            "STEP_VALIDATION_RESULT_MISSING",
        ),
    ],
)
def test_worker_process_failures_are_stable_and_fail_closed(
    tmp_path: Path, process: dict, code: str
) -> None:
    root, _asset = _registered(tmp_path)

    with pytest.raises(validator.StepValidationError) as caught:
        validator.validate_registered_step(
            root,
            PROJECT_ID,
            ASSET_ID,
            freecadcmd=sys.executable,
            process_runner=lambda *_args, **_kwargs: dict(process),
        )

    assert caught.value.code == code


def test_truncated_worker_result_is_rejected(tmp_path: Path) -> None:
    root, _asset = _registered(tmp_path)

    def run(_command, **kwargs):
        Path(kwargs["environment"]["VIBECAD_STEP_VALIDATION_RESULT"]).write_text(
            "{", encoding="utf-8"
        )
        return {
            "started": True,
            "returncode": 0,
            "cancelled": False,
            "timed_out": False,
            "memory_exceeded": False,
        }

    with pytest.raises(validator.StepValidationError) as caught:
        validator.validate_registered_step(
            root,
            PROJECT_ID,
            ASSET_ID,
            freecadcmd=sys.executable,
            process_runner=run,
        )
    assert caught.value.code == "STEP_VALIDATION_RESULT_INVALID"


def test_tampered_or_wrong_identity_worker_evidence_is_rejected(tmp_path: Path) -> None:
    root, _asset = _registered(tmp_path)

    def tamper(result, _request):
        result["shape"]["volume_mm3"] = 1.0

    with pytest.raises(validator.StepValidationError) as caught:
        validator.validate_registered_step(
            root,
            PROJECT_ID,
            ASSET_ID,
            freecadcmd=sys.executable,
            process_runner=_successful_runner(tamper),
        )
    assert caught.value.code == "STEP_VALIDATION_RESULT_TAMPERED"

    def wrong_identity(result, _request):
        result["asset_id"] = "e" * 32
        result["evidence_sha256"] = validator._content_digest(
            result, "evidence_sha256"
        )

    with pytest.raises(validator.StepValidationError) as caught:
        validator.validate_registered_step(
            root,
            PROJECT_ID,
            ASSET_ID,
            freecadcmd=sys.executable,
            process_runner=_successful_runner(wrong_identity),
        )
    assert caught.value.code == "STEP_VALIDATION_IDENTITY_MISMATCH"


def test_invalid_or_non_solid_shape_is_rejected(tmp_path: Path) -> None:
    root, _asset = _registered(tmp_path)

    def invalid(result, _request):
        result["ok"] = False
        result["shape"] = None
        result["errors"] = ["ValueError: invalid STEP"]
        result["evidence_sha256"] = validator._content_digest(
            result, "evidence_sha256"
        )

    with pytest.raises(validator.StepValidationError) as caught:
        validator.validate_registered_step(
            root,
            PROJECT_ID,
            ASSET_ID,
            freecadcmd=sys.executable,
            process_runner=_successful_runner(invalid),
        )
    assert caught.value.code == "STEP_CONTENT_INVALID"

    def no_solid(result, _request):
        result["shape"]["topology"]["solids"] = 0
        result["evidence_sha256"] = validator._content_digest(
            result, "evidence_sha256"
        )

    with pytest.raises(validator.StepValidationError) as caught:
        validator.validate_registered_step(
            root,
            PROJECT_ID,
            ASSET_ID,
            freecadcmd=sys.executable,
            process_runner=_successful_runner(no_solid),
        )
    assert caught.value.code == "STEP_CONTENT_INVALID"

    def multiple_solids(result, _request):
        result["shape"]["topology"]["solids"] = 2
        result["evidence_sha256"] = validator._content_digest(
            result, "evidence_sha256"
        )

    with pytest.raises(validator.StepValidationError) as caught:
        validator.validate_registered_step(
            root,
            PROJECT_ID,
            ASSET_ID,
            freecadcmd=sys.executable,
            process_runner=_successful_runner(multiple_solids),
        )
    assert caught.value.code == "STEP_CONTENT_INVALID"


def test_registered_asset_tampering_stops_before_worker_start(tmp_path: Path) -> None:
    root, asset = _registered(tmp_path)
    target = root / "import-assets" / asset["stored_name"]
    target.write_bytes(b"x" * asset["size_bytes"])
    called = False

    def run(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    with pytest.raises(ValueError, match="changed content"):
        validator.validate_registered_step(
            root,
            PROJECT_ID,
            ASSET_ID,
            freecadcmd=sys.executable,
            process_runner=run,
        )
    assert called is False


def test_asset_tampering_after_worker_success_is_rejected(tmp_path: Path) -> None:
    root, asset = _registered(tmp_path)
    target = root / "import-assets" / asset["stored_name"]

    def tamper_after_result(result, _request):
        target.write_bytes(b"x" * asset["size_bytes"])

    with pytest.raises(validator.StepValidationError) as caught:
        validator.validate_registered_step(
            root,
            PROJECT_ID,
            ASSET_ID,
            freecadcmd=sys.executable,
            process_runner=_successful_runner(tamper_after_result),
        )
    assert caught.value.code == "STEP_ASSET_TAMPERED"


def test_shape_comparison_detects_bound_and_volume_changes() -> None:
    expected = _shape()
    observed = json.loads(json.dumps(expected))
    observed["bounds_mm"]["size_x"] = 39.0
    observed["volume_mm3"] = 11999.0
    comparison = validator.compare_shape_evidence(expected, observed)
    assert comparison["ok"] is False
    failed = {item["name"] for item in comparison["checks"] if not item["ok"]}
    assert failed == {"bounds_mm.size_x", "volume_mm3"}


def test_verified_worker_copy_is_exact_and_private(tmp_path: Path) -> None:
    source = tmp_path / "source.step"
    destination = tmp_path / "staging" / "registered.step"
    destination.parent.mkdir()
    content = b"content-bound STEP bytes"
    source.write_bytes(content)

    digest = validator._copy_verified_source(
        source,
        destination,
        expected_sha256=hashlib.sha256(content).hexdigest(),
        expected_size=len(content),
    )

    assert digest == hashlib.sha256(content).hexdigest()
    assert destination.read_bytes() == content
    assert destination.stat().st_mode & 0o077 == 0


def test_worker_hash_and_copy_reject_source_links(tmp_path: Path) -> None:
    source = tmp_path / "source.step"
    source.write_bytes(b"registered")
    linked = tmp_path / "linked.step"
    linked.symlink_to(source)

    with pytest.raises(ValueError, match="missing or unsafe"):
        validator._file_sha256(linked)

    with pytest.raises(ValueError, match="missing or unsafe"):
        validator._copy_verified_source(
            linked,
            tmp_path / "copy.step",
            expected_sha256=hashlib.sha256(b"registered").hexdigest(),
            expected_size=len(b"registered"),
        )


def test_verified_worker_copy_does_not_replace_or_remove_a_target(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.step"
    destination = tmp_path / "registered.step"
    source.write_bytes(b"registered")
    destination.write_bytes(b"keep-existing")

    with pytest.raises(FileExistsError):
        validator._copy_verified_source(
            source,
            destination,
            expected_sha256=hashlib.sha256(b"registered").hexdigest(),
            expected_size=len(b"registered"),
        )

    assert destination.read_bytes() == b"keep-existing"


def _write_worker_fixture(tmp_path: Path, monkeypatch, part_module) -> tuple[Path, Path]:
    content = b"content-bound parser input"
    source = tmp_path / "registered.step"
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    brep_path = tmp_path / "validated.brep"
    source.write_bytes(content)
    request = validator._request_payload(
        {
            "asset_id": ASSET_ID,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        },
        PROJECT_ID,
        source,
    )
    request_path.write_text(json.dumps(request), encoding="utf-8")
    monkeypatch.setenv("VIBECAD_STEP_VALIDATION_REQUEST", str(request_path))
    monkeypatch.setenv("VIBECAD_STEP_VALIDATION_RESULT", str(result_path))
    monkeypatch.setenv("VIBECAD_STEP_VALIDATION_BREP", str(brep_path))
    monkeypatch.setitem(sys.modules, "Part", part_module)
    return result_path, brep_path


def test_worker_rejects_parser_input_path_swap(
    tmp_path: Path, monkeypatch
) -> None:
    calls = []

    def read(path):
        parser_input = Path(path)
        calls.append(parser_input.name)
        original = parser_input.read_bytes()
        parser_input.replace(parser_input.with_name("displaced.step"))
        parser_input.write_bytes(original)
        parser_input.chmod(0o600)
        return SimpleNamespace(Solids=[object()])

    result_path, brep_path = _write_worker_fixture(
        tmp_path,
        monkeypatch,
        SimpleNamespace(read=read),
    )

    validator.worker_main()

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert calls == ["parser-input.step"]
    assert result["ok"] is False
    assert result["shape"] is None
    assert brep_path.exists() is False


def test_worker_rejects_coarse_equal_brep_with_different_native_identity(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeShape:
        def __init__(self, identity: str):
            self.identity = identity

        def exportBrepToString(self):
            return f"DBRep_DrawableShape\n{self.identity}\n"

        def exportBrep(self, path):
            Path(path).write_text(
                "DBRep_DrawableShape\nserialized-worker-solid\n",
                encoding="utf-8",
            )

    source_shape = FakeShape("source-native-identity")
    reopened_shape = FakeShape("different-native-identity")
    calls = []

    def read(path):
        calls.append(Path(path).name)
        if len(calls) == 1:
            return SimpleNamespace(Solids=[source_shape])
        return reopened_shape

    result_path, brep_path = _write_worker_fixture(
        tmp_path,
        monkeypatch,
        SimpleNamespace(read=read),
    )
    monkeypatch.setattr(validator, "shape_evidence", lambda _shape_value: _shape())

    validator.worker_main()

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert calls == ["parser-input.step", "validated.brep"]
    assert result["ok"] is False
    assert result["shape"] == _shape()
    assert brep_path.exists() is False


def test_worker_environment_does_not_inherit_pythonpath(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/untrusted/provider/path")
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/untrusted/dynamic/libraries")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/untrusted/elf/libraries")

    environment = validator._worker_environment(
        tmp_path,
        tmp_path / "request.json",
        tmp_path / "result.json",
        tmp_path / "validated.brep",
    )

    assert "PYTHONPATH" not in environment
    assert "DYLD_LIBRARY_PATH" not in environment
    assert "LD_LIBRARY_PATH" not in environment


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt test")
def test_validator_uses_the_sandbox_command_and_environment(
    tmp_path: Path, monkeypatch
) -> None:
    root, _asset = _registered(tmp_path)
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/untrusted/injected.dylib")
    captured = {}
    successful = _successful_runner()

    def run(command, **kwargs):
        captured["command"] = list(command)
        captured["environment"] = dict(kwargs["environment"])
        return successful(command, **kwargs)

    candidate = validator.validate_registered_step(
        root,
        PROJECT_ID,
        ASSET_ID,
        freecadcmd=sys.executable,
        process_runner=run,
    )
    try:
        command = captured["command"]
        assert command[0] == "/usr/bin/sandbox-exec"
        assert command[1] == "-p"
        assert "(deny network*)" in command[2]
        assert "(deny file-write*" in command[2]
        assert command[3] == str(Path(sys.executable).resolve())
        assert command[4:6] == ["--safe-mode", "-c"]
        assert "DYLD_INSERT_LIBRARIES" not in captured["environment"]
        home = Path(captured["environment"]["HOME"])
        assert home.name.startswith("vibecad-step-validation-")
        assert captured["environment"]["TMPDIR"] == str(home)
        assert str(home.resolve()) in command[2]
    finally:
        candidate.cleanup()


def test_validator_fails_closed_when_the_os_sandbox_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    root, _asset = _registered(tmp_path)
    called = False

    def unavailable(*_args, **_kwargs):
        raise validator.StepWorkerSandboxUnavailable("unsupported_platform")

    def run(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(validator, "prepare_step_worker_sandbox", unavailable)
    with pytest.raises(validator.StepValidationError) as caught:
        validator.validate_registered_step(
            root,
            PROJECT_ID,
            ASSET_ID,
            freecadcmd=sys.executable,
            process_runner=run,
        )

    assert caught.value.code == "STEP_VALIDATOR_SANDBOX_UNAVAILABLE"
    assert caught.value.evidence["reason"] == "unsupported_platform"
    assert str(tmp_path) not in str(caught.value)
    assert str(tmp_path) not in json.dumps(caught.value.evidence)
    assert called is False


def test_exact_brep_binding_rejects_same_size_different_bytes(tmp_path: Path) -> None:
    root, _asset = _registered(tmp_path)
    candidate = validator.validate_registered_step(
        root,
        PROJECT_ID,
        ASSET_ID,
        freecadcmd=sys.executable,
        process_runner=_successful_runner(),
    )
    try:
        assert len(BREP_A) == len(BREP_B)
        candidate._artifact_path.write_bytes(BREP_B)
        candidate._artifact_path.chmod(0o600)
        with pytest.raises(validator.StepValidationError) as caught:
            with candidate.verified_brep_copy():
                pass
        assert caught.value.code == "STEP_VALIDATION_ARTIFACT_TAMPERED"
        assert str(tmp_path) not in str(caught.value)
    finally:
        candidate.cleanup()


def test_worker_artifacts_are_removed_on_failure(tmp_path: Path) -> None:
    root, _asset = _registered(tmp_path)
    staging_roots: list[Path] = []

    def crash(_command, **kwargs):
        staging = Path(kwargs["cwd"])
        staging_roots.append(staging)
        (staging / "untrusted.step").write_bytes(b"private")
        return {
            "started": True,
            "returncode": -11,
            "cancelled": False,
            "timed_out": False,
            "memory_exceeded": False,
        }

    with pytest.raises(validator.StepValidationError):
        validator.validate_registered_step(
            root,
            PROJECT_ID,
            ASSET_ID,
            freecadcmd=sys.executable,
            process_runner=crash,
        )
    assert staging_roots
    assert all(not path.exists() for path in staging_roots)


@pytest.mark.parametrize(
    "timeout",
    [float("nan"), float("inf"), float("-inf"), True, "30", 0, 301.0],
)
def test_timeout_bounds_fail_before_worker_start(tmp_path: Path, timeout) -> None:
    root, _asset = _registered(tmp_path)
    called = False

    def run(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    with pytest.raises(ValueError, match="timeout"):
        validator.validate_registered_step(
            root,
            PROJECT_ID,
            ASSET_ID,
            freecadcmd=sys.executable,
            timeout_seconds=timeout,
            process_runner=run,
        )
    assert called is False


@pytest.mark.parametrize(
    "memory_limit",
    [True, 1024.0, "1024", 0, -1, validator.MAX_STEP_VALIDATION_MEMORY_BYTES + 1],
)
def test_memory_bounds_fail_before_worker_start(
    tmp_path: Path, memory_limit
) -> None:
    root, _asset = _registered(tmp_path)
    called = False

    def run(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    with pytest.raises(ValueError, match="memory limit"):
        validator.validate_registered_step(
            root,
            PROJECT_ID,
            ASSET_ID,
            freecadcmd=sys.executable,
            memory_limit_bytes=memory_limit,
            process_runner=run,
        )
    assert called is False


def test_worker_result_reader_rejects_over_limit_and_links(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b"x" * validator.MAX_STEP_VALIDATION_RESULT_BYTES)
    with pytest.raises(validator.StepValidationError) as caught:
        validator._read_worker_result(oversized)
    assert caught.value.code == "STEP_VALIDATION_RESULT_INVALID"

    safe = tmp_path / "safe.json"
    safe.write_text("{}", encoding="utf-8")
    linked = tmp_path / "linked.json"
    linked.symlink_to(safe)
    with pytest.raises(validator.StepValidationError) as caught:
        validator._read_worker_result(linked)
    assert caught.value.code == "STEP_VALIDATION_RESULT_INVALID"


def test_worker_result_reader_detects_identity_change(
    tmp_path: Path, monkeypatch
) -> None:
    result = tmp_path / "result.json"
    result.write_text("{}", encoding="utf-8")
    original_fstat = validator.os.fstat
    calls = 0

    def changed_fstat(descriptor):
        nonlocal calls
        observed = original_fstat(descriptor)
        calls += 1
        return SimpleNamespace(
            st_mode=observed.st_mode,
            st_size=observed.st_size,
            st_dev=observed.st_dev,
            st_ino=observed.st_ino,
            st_mtime_ns=observed.st_mtime_ns,
            st_ctime_ns=observed.st_ctime_ns + (1 if calls > 1 else 0),
        )

    monkeypatch.setattr(validator.os, "fstat", changed_fstat)
    with pytest.raises(validator.StepValidationError) as caught:
        validator._read_worker_result(result)
    assert caught.value.code == "STEP_VALIDATION_RESULT_INVALID"


def test_candidate_seals_one_exact_brep_descriptor_without_byte_buffer(
    tmp_path: Path,
) -> None:
    root, _asset = _registered(tmp_path)
    candidate = validator.validate_registered_step(
        root,
        PROJECT_ID,
        ASSET_ID,
        freecadcmd=sys.executable,
        process_runner=_successful_runner(),
    )
    try:
        staging = candidate._artifact_path.parent
        assert candidate.artifact_available() is True
        candidate.seal_for_publication()
        assert candidate.artifact_available() is False
        assert staging.exists() is True
        assert not hasattr(candidate, "_sealed_brep")
        with candidate.consume_verified_brep_descriptor() as (
            descriptor,
            metadata,
        ):
            validator._verify_open_private_descriptor(
                descriptor,
                metadata,
                expected_sha256=hashlib.sha256(BREP_A).hexdigest(),
                expected_size=len(BREP_A),
            )
            observed, size = validator._descriptor_sha256(descriptor)
            assert observed == hashlib.sha256(BREP_A).hexdigest()
            assert size == len(BREP_A)
        with pytest.raises(validator.StepValidationError) as caught:
            with candidate.consume_verified_brep_descriptor():
                pass
        assert caught.value.code == "STEP_VALIDATION_ARTIFACT_CONSUMED"
    finally:
        candidate.cleanup()
    assert candidate.artifact_available() is False
    assert staging.exists() is False


def test_candidate_cleanup_closes_a_sealed_descriptor(tmp_path: Path) -> None:
    root, _asset = _registered(tmp_path)
    candidate = validator.validate_registered_step(
        root,
        PROJECT_ID,
        ASSET_ID,
        freecadcmd=sys.executable,
        process_runner=_successful_runner(),
    )
    candidate.seal_for_publication()
    descriptor = candidate._sealed_descriptor
    assert isinstance(descriptor, int)

    candidate.cleanup()

    with pytest.raises(OSError):
        validator.os.fstat(descriptor)
    assert candidate.artifact_available() is False


def test_detached_brep_parse_rejects_private_path_swap(
    tmp_path: Path, monkeypatch
) -> None:
    root, _asset = _registered(tmp_path)
    candidate = validator.validate_registered_step(
        root,
        PROJECT_ID,
        ASSET_ID,
        freecadcmd=sys.executable,
        process_runner=_successful_runner(),
    )
    staging = candidate._artifact_path.parent

    class SwappingShape:
        @staticmethod
        def importBrep(path):
            target = Path(path)
            content = target.read_bytes()
            target.replace(target.with_name("displaced.brep"))
            target.write_bytes(content)
            target.chmod(0o600)

    monkeypatch.setitem(sys.modules, "Part", SimpleNamespace(Shape=SwappingShape))

    with pytest.raises(validator.StepValidationError) as caught:
        candidate.prepare_detached_shape()

    assert caught.value.code == "STEP_VALIDATION_ARTIFACT_TAMPERED"
    assert str(tmp_path) not in str(caught.value)
    assert candidate.artifact_available() is False
    assert candidate._sealed_descriptor is None
    assert staging.exists() is False
