# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

from tools import score_live_tier1_benchmark as score
from tools import vibecad_benchmark_evidence_io as evidence_io
from VibeCADBenchmark import (
    HUMAN_RATING_SET_SCHEMA,
    VALIDATION_STAGES,
    case_evidence_digest,
    make_case_attempt,
    normalized_usage,
    unrated_instruction_adherence,
    validation_stage,
)
from VibeCADLiveBenchmark import (
    LIVE_EXECUTOR,
    LIVE_LIMITS,
    LIVE_RUN_SCHEMA,
    LIVE_RUNTIME_FILE_ROLES,
    TIER1_CASE_IDS,
    limits_digest,
    readiness_digest,
    runtime_identity_digest,
)


SOURCE_COMMIT = "a" * 40


@dataclass
class LiveEvidenceFixture:
    run_directory: Path
    raw_path: Path
    ratings_path: Path
    readiness_path: Path
    runtime_identity_path: Path
    output_path: Path
    raw: dict
    artifacts: dict[str, Path]


def _write_json(path: Path, value: dict) -> None:
    path.write_bytes(
        (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )


def _attempt(case_id: str, artifact: Path) -> dict:
    stages = {
        name: validation_stage(
            applicable=True,
            passed=True,
            evidence={"check": name, "passed": True},
        )
        for name in VALIDATION_STAGES
    }
    artifact_path = str(artifact)
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
        elapsed_seconds=1.0,
        artifact_paths=[artifact_path],
        source_commit=SOURCE_COMMIT,
        artifact_sha256={
            artifact_path: hashlib.sha256(artifact.read_bytes()).hexdigest()
        },
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
        "tool_call_count": 1,
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


def _readiness() -> dict:
    endpoint = "https://api.openai.com/v1"
    return {
        "schema": "vibecad-provider-readiness-v1",
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "can_call_provider": True,
        "prompt_sent": False,
        "document_data_sent": False,
        "credential_validation_performed": True,
        "model_validation_performed": True,
        "model_available": True,
        "stage": "complete",
        "provider": "openai",
        "model": "gpt-test",
        "auth_status": "verified",
        "auth_source": "keychain",
        "online_by_default": True,
        "endpoint_identity": endpoint,
        "endpoint_sha256": hashlib.sha256(endpoint.encode("utf-8")).hexdigest(),
        "credential_binding_nonce": "1" * 64,
        "credential_fingerprint_algorithm": "hmac-sha256-v1",
        "credential_fingerprint": "2" * 64,
        "process_timed_out": False,
        "process_exit_code": 0,
        "ready_for_live_benchmark": True,
    }


def _runtime_identity() -> dict:
    provider_component = Path(__file__).resolve().parents[4] / "README.md"
    provider_bytes = provider_component.read_bytes()
    return {
        "schema": "vibecad-live-runtime-identity-v3",
        "version": 3,
        "source_commit": SOURCE_COMMIT,
        "module_file_count": 1,
        "module_manifest_sha256": "3" * 64,
        "gui_entry_sha256": "4" * 64,
        "gui_runner_sha256": "5" * 64,
        "case_catalog_sha256": "6" * 64,
        "runtime_files": [
            {
                "role": role,
                "path": f"runtime/{index:02d}-{role}.bin",
                "size": index + 1,
                "sha256": f"{index + 7:064x}",
            }
            for index, role in enumerate(LIVE_RUNTIME_FILE_ROLES)
        ],
        "provider_runtime": {
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
                    "path": "README.md",
                    "size": len(provider_bytes),
                    "sha256": hashlib.sha256(provider_bytes).hexdigest(),
                }
            ],
        },
    }


def _ratings(raw: dict, raw_sha256: str) -> dict:
    return {
        "schema": HUMAN_RATING_SET_SCHEMA,
        "version": 1,
        "created_at": "2026-07-22T12:00:00Z",
        "provider": raw["provider"],
        "model": raw["model"],
        "source_commit": raw["source_commit"],
        "readiness_sha256": raw["readiness_sha256"],
        "runtime_identity_sha256": raw["runtime_identity_sha256"],
        "limits_sha256": limits_digest(raw["limits"]),
        "raw_run_sha256": raw_sha256,
        "ratings": [
            {
                "provider": attempt["provider"],
                "model": attempt["model"],
                "source_commit": attempt["source_commit"],
                "case_id": attempt["case_id"],
                "attempt": attempt["attempt"],
                "evidence_sha256": case_evidence_digest(attempt),
                "rating": 5,
                "scale": {"minimum": 1, "maximum": 5},
                "reviewer_id": "reviewer-01",
                "notes": "The retained result follows the case requirements.",
            }
            for attempt in raw["case_attempts"]
        ],
    }


def _rewrite_bound_inputs(fixture: LiveEvidenceFixture) -> None:
    _write_json(fixture.raw_path, fixture.raw)
    raw_sha256 = hashlib.sha256(fixture.raw_path.read_bytes()).hexdigest()
    _write_json(fixture.ratings_path, _ratings(fixture.raw, raw_sha256))


def _fixture(tmp_path: Path) -> LiveEvidenceFixture:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    readiness = _readiness()
    runtime_identity = _runtime_identity()
    readiness_path = run_directory / "provider-readiness.json"
    runtime_identity_path = run_directory / "runtime-identity.json"
    _write_json(readiness_path, readiness)
    _write_json(runtime_identity_path, runtime_identity)
    artifacts: dict[str, Path] = {}
    attempts = []
    for index, case_id in enumerate(TIER1_CASE_IDS):
        case_directory = run_directory / case_id
        case_directory.mkdir()
        artifact = case_directory / f"{case_id}.FCStd"
        artifact.write_bytes(f"fixture-{index}-{case_id}\n".encode("utf-8"))
        artifacts[case_id] = artifact
        attempts.append(_attempt(case_id, artifact))
    raw = {
        "schema": LIVE_RUN_SCHEMA,
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tier": 1,
        "provider": "openai",
        "model": "gpt-test",
        "executor": LIVE_EXECUTOR,
        "source_commit": SOURCE_COMMIT,
        "readiness_sha256": readiness_digest(readiness),
        "runtime_identity_sha256": runtime_identity_digest(runtime_identity),
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
        "runner": {
            "attempt": 1,
            "case_evidence_passed": True,
            "gui_runner_exit_code": 0,
            "gui_runner_reported_ok": True,
        },
    }
    fixture = LiveEvidenceFixture(
        run_directory=run_directory,
        raw_path=run_directory / "tier1-live-unrated-run.json",
        ratings_path=run_directory / "tier1-live-human-ratings.json",
        readiness_path=readiness_path,
        runtime_identity_path=runtime_identity_path,
        output_path=run_directory / "tier1-live-scored-series.json",
        raw=raw,
        artifacts=artifacts,
    )
    _rewrite_bound_inputs(fixture)
    return fixture


def test_score_binds_exact_input_bytes_and_publishes_private_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    fsync_modes: list[int] = []
    real_fsync = evidence_io.os.fsync

    def recording_fsync(descriptor: int) -> None:
        fsync_modes.append(os.fstat(descriptor).st_mode)
        real_fsync(descriptor)

    monkeypatch.setattr(evidence_io.os, "fsync", recording_fsync)
    result = score.score_run(
        fixture.raw_path, fixture.ratings_path, fixture.output_path
    )

    assert result["scored"] is True
    assert result["raw_live_run_sha256"] == hashlib.sha256(
        fixture.raw_path.read_bytes()
    ).hexdigest()
    assert result["human_rating_set_sha256"] == hashlib.sha256(
        fixture.ratings_path.read_bytes()
    ).hexdigest()
    assert result["readiness_evidence_file_sha256"] == hashlib.sha256(
        fixture.readiness_path.read_bytes()
    ).hexdigest()
    assert result["runtime_identity_evidence_file_sha256"] == hashlib.sha256(
        fixture.runtime_identity_path.read_bytes()
    ).hexdigest()
    assert stat.S_IMODE(fixture.output_path.stat().st_mode) == 0o600
    assert any(stat.S_ISREG(mode) for mode in fsync_modes)
    assert any(stat.S_ISDIR(mode) for mode in fsync_modes)


def test_score_rejects_artifact_content_tamper(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.artifacts[TIER1_CASE_IDS[0]].write_bytes(b"tampered")

    with pytest.raises(evidence_io.EvidenceIOError, match="SHA-256"):
        score.score_run(fixture.raw_path, fixture.ratings_path, fixture.output_path)
    assert not fixture.output_path.exists()


@pytest.mark.parametrize("evidence_name", ("readiness", "runtime_identity"))
def test_score_rejects_execution_identity_tamper(
    tmp_path: Path, evidence_name: str
) -> None:
    fixture = _fixture(tmp_path)
    if evidence_name == "readiness":
        payload = json.loads(fixture.readiness_path.read_text(encoding="utf-8"))
        payload["model"] = "gpt-tampered"
        _write_json(fixture.readiness_path, payload)
    else:
        payload = json.loads(
            fixture.runtime_identity_path.read_text(encoding="utf-8")
        )
        payload["module_manifest_sha256"] = "9" * 64
        _write_json(fixture.runtime_identity_path, payload)

    with pytest.raises(evidence_io.EvidenceIOError, match="does not match"):
        score.score_run(fixture.raw_path, fixture.ratings_path, fixture.output_path)
    assert not fixture.output_path.exists()


@pytest.mark.parametrize("evidence_name", ("readiness", "runtime_identity"))
def test_score_rejects_symlink_execution_identity(
    tmp_path: Path, evidence_name: str
) -> None:
    fixture = _fixture(tmp_path)
    evidence_path = (
        fixture.readiness_path
        if evidence_name == "readiness"
        else fixture.runtime_identity_path
    )
    target = evidence_path.with_name(f"{evidence_name}-target.json")
    target.write_bytes(evidence_path.read_bytes())
    evidence_path.unlink()
    evidence_path.symlink_to(target.name)

    with pytest.raises(evidence_io.EvidenceIOError, match="symbolic link"):
        score.score_run(fixture.raw_path, fixture.ratings_path, fixture.output_path)
    assert not fixture.output_path.exists()


def test_score_rejects_artifact_swap_after_hash_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    artifact = fixture.artifacts[TIER1_CASE_IDS[0]]
    original_apply = score.apply_human_rating_set

    def swapping_apply(*args, **kwargs):
        result = original_apply(*args, **kwargs)
        replacement = artifact.with_name("replacement.FCStd")
        replacement.write_bytes(artifact.read_bytes())
        os.replace(replacement, artifact)
        return result

    monkeypatch.setattr(score, "apply_human_rating_set", swapping_apply)
    with pytest.raises(evidence_io.EvidenceIOError, match="changed during scoring"):
        score.score_run(fixture.raw_path, fixture.ratings_path, fixture.output_path)
    assert not fixture.output_path.exists()


@pytest.mark.parametrize("input_name", ("raw", "ratings"))
def test_score_rejects_symlink_json_input(tmp_path: Path, input_name: str) -> None:
    fixture = _fixture(tmp_path)
    original = fixture.raw_path if input_name == "raw" else fixture.ratings_path
    link = original.with_name(f"{input_name}-link.json")
    link.symlink_to(original.name)
    raw_path = link if input_name == "raw" else fixture.raw_path
    ratings_path = link if input_name == "ratings" else fixture.ratings_path

    with pytest.raises(evidence_io.EvidenceIOError, match="symbolic link"):
        score.score_run(raw_path, ratings_path, fixture.output_path)
    assert not fixture.output_path.exists()


def test_score_rejects_symlink_artifact(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    case_id = TIER1_CASE_IDS[0]
    artifact = fixture.artifacts[case_id]
    target = artifact.with_name("target.FCStd")
    target.write_bytes(artifact.read_bytes())
    artifact.unlink()
    artifact.symlink_to(target.name)

    with pytest.raises(evidence_io.EvidenceIOError, match="symbolic link"):
        score.score_run(fixture.raw_path, fixture.ratings_path, fixture.output_path)
    assert not fixture.output_path.exists()


def test_score_rejects_artifact_with_external_hard_link(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    artifact = fixture.artifacts[TIER1_CASE_IDS[0]]
    external = tmp_path / "external.FCStd"
    external.write_bytes(artifact.read_bytes())
    artifact.unlink()
    os.link(external, artifact)

    with pytest.raises(evidence_io.EvidenceIOError, match="hard link"):
        score.score_run(fixture.raw_path, fixture.ratings_path, fixture.output_path)
    assert not fixture.output_path.exists()


def test_score_rejects_oversized_json_before_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(score, "MAX_RAW_JSON_BYTES", 128)

    with pytest.raises(evidence_io.EvidenceIOError, match="128-byte limit"):
        score.score_run(fixture.raw_path, fixture.ratings_path, fixture.output_path)
    assert not fixture.output_path.exists()


def test_score_rejects_oversized_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(score, "MAX_ARTIFACT_BYTES", 4)

    with pytest.raises(evidence_io.EvidenceIOError, match="4-byte limit"):
        score.score_run(fixture.raw_path, fixture.ratings_path, fixture.output_path)
    assert not fixture.output_path.exists()


def test_raw_and_rating_inputs_cannot_alias(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.ratings_path.unlink()
    os.link(fixture.raw_path, fixture.ratings_path)

    with pytest.raises(evidence_io.EvidenceIOError, match="aliases"):
        score.score_run(
            fixture.raw_path, fixture.ratings_path, fixture.output_path
        )
    assert not fixture.output_path.exists()


@pytest.mark.parametrize("alias_kind", ("same_path", "hard_link"))
def test_score_output_cannot_alias_an_input(
    tmp_path: Path, alias_kind: str
) -> None:
    fixture = _fixture(tmp_path)
    raw_before = fixture.raw_path.read_bytes()
    ratings_before = fixture.ratings_path.read_bytes()
    if alias_kind == "same_path":
        output = fixture.raw_path
    else:
        output = fixture.output_path
        os.link(fixture.ratings_path, output)

    with pytest.raises(evidence_io.EvidenceIOError, match="aliases"):
        score.score_run(fixture.raw_path, fixture.ratings_path, output)
    assert fixture.raw_path.read_bytes() == raw_before
    assert fixture.ratings_path.read_bytes() == ratings_before


def test_score_never_overwrites_an_existing_output(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.output_path.write_bytes(b"keep-this-output")

    with pytest.raises(evidence_io.EvidenceIOError, match="never overwritten"):
        score.score_run(fixture.raw_path, fixture.ratings_path, fixture.output_path)
    assert fixture.output_path.read_bytes() == b"keep-this-output"


def test_score_rejects_nonregular_json_input(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    directory = fixture.run_directory / "not-a-file.json"
    directory.mkdir()

    with pytest.raises(evidence_io.EvidenceIOError, match="not a regular file"):
        score.score_run(directory, fixture.ratings_path, fixture.output_path)
    assert not fixture.output_path.exists()


def test_score_rejects_duplicate_artifact_paths(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    attempt = fixture.raw["case_attempts"][0]
    attempt["artifact_paths"].append(attempt["artifact_paths"][0])
    _rewrite_bound_inputs(fixture)

    with pytest.raises(evidence_io.EvidenceIOError, match="duplicate artifact"):
        score.score_run(fixture.raw_path, fixture.ratings_path, fixture.output_path)
    assert not fixture.output_path.exists()


def test_score_rejects_escaped_artifact_path(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    case_id = TIER1_CASE_IDS[0]
    outside = fixture.run_directory / "outside.FCStd"
    outside.write_bytes(b"outside")
    escaped = str(fixture.run_directory / case_id / ".." / outside.name)
    attempt = fixture.raw["case_attempts"][0]
    old_path = attempt["artifact_paths"][0]
    attempt["artifact_paths"] = [escaped]
    attempt["artifact_sha256"] = {
        escaped: hashlib.sha256(outside.read_bytes()).hexdigest()
    }
    assert old_path != escaped
    _rewrite_bound_inputs(fixture)

    with pytest.raises(evidence_io.EvidenceIOError, match="escaped artifact"):
        score.score_run(fixture.raw_path, fixture.ratings_path, fixture.output_path)
    assert not fixture.output_path.exists()


def test_score_reopens_and_rejects_changed_provider_runtime_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    component = tmp_path / "provider-python"
    component.write_bytes(b"attested provider runtime")
    fixture.raw["runtime_identity_sha256"] = "0" * 64
    runtime_identity = _runtime_identity()
    runtime_identity["provider_runtime"]["components"] = [
        {
            "path": component.name,
            "size": component.stat().st_size,
            "sha256": hashlib.sha256(component.read_bytes()).hexdigest(),
        }
    ]
    _write_json(fixture.runtime_identity_path, runtime_identity)
    fixture.raw["runtime_identity_sha256"] = runtime_identity_digest(
        runtime_identity
    )
    _rewrite_bound_inputs(fixture)
    component.write_bytes(b"changed provider runtime")
    monkeypatch.setattr(score, "ROOT", tmp_path)

    with pytest.raises(
        evidence_io.EvidenceIOError, match="does not match its attestation"
    ):
        score.score_run(fixture.raw_path, fixture.ratings_path, fixture.output_path)
    assert not fixture.output_path.exists()


def test_score_rejects_same_digest_input_path_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    original_finalize = score.finalize_series_report

    def swapping_finalize(*args, **kwargs):
        result = original_finalize(*args, **kwargs)
        replacement = fixture.raw_path.with_name("replacement-raw.json")
        replacement.write_bytes(fixture.raw_path.read_bytes())
        os.replace(replacement, fixture.raw_path)
        return result

    monkeypatch.setattr(score, "finalize_series_report", swapping_finalize)
    with pytest.raises(evidence_io.EvidenceIOError, match="changed during scoring"):
        score.score_run(fixture.raw_path, fixture.ratings_path, fixture.output_path)
    assert not fixture.output_path.exists()
