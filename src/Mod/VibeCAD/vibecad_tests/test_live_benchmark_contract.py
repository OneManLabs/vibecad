# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from VibeCADBenchmark import (
    BenchmarkEvidenceError,
    HUMAN_RATING_SET_SCHEMA,
    VALIDATION_STAGES,
    aggregate_case_attempts,
    apply_human_rating_set,
    case_evidence_digest,
    make_case_attempt,
    normalized_usage,
    unrated_instruction_adherence,
    validation_stage,
)
from VibeCADLiveBenchmark import validate_live_readiness


SOURCE_COMMIT = "a" * 40
ARTIFACT_DIGEST = "b" * 64
RUN_BINDING = {
    "readiness_sha256": "c" * 64,
    "runtime_identity_sha256": "d" * 64,
    "limits_sha256": "e" * 64,
    "raw_run_sha256": "f" * 64,
}


def _raw_attempt(case_id: str = "t1_exact_box") -> dict:
    stages = {
        name: (
            validation_stage(
                applicable=False,
                reason=f"{name} does not apply to this fixture.",
            )
            if name in {"follow_up", "export"}
            else validation_stage(
                applicable=True,
                passed=True,
                evidence={"check": name, "observed": True},
            )
        )
        for name in VALIDATION_STAGES
    }
    return make_case_attempt(
        tier=1,
        case_id=case_id,
        attempt=1,
        provider="openai",
        model="gpt-test",
        executor="vibecad-normal-session-live-v1",
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
        artifact_paths=["case.FCStd"],
        source_commit=SOURCE_COMMIT,
        artifact_sha256={"case.FCStd": ARTIFACT_DIGEST},
        allow_unrated_live=True,
    )


def _rating_set(attempts: list[dict]) -> dict:
    return {
        "schema": HUMAN_RATING_SET_SCHEMA,
        "version": 1,
        "created_at": "2026-07-22T12:00:00Z",
        "provider": "openai",
        "model": "gpt-test",
        "source_commit": SOURCE_COMMIT,
        **RUN_BINDING,
        "ratings": [
            {
                "provider": attempt["provider"],
                "model": attempt["model"],
                "source_commit": attempt["source_commit"],
                "case_id": attempt["case_id"],
                "attempt": attempt["attempt"],
                "evidence_sha256": case_evidence_digest(attempt),
                "rating": 4,
                "scale": {"minimum": 1, "maximum": 5},
                "reviewer_id": "reviewer-01",
                "notes": "The result follows the stated dimensions.",
            }
            for attempt in attempts
        ],
    }


def test_raw_live_attempt_cannot_be_aggregated_or_scored() -> None:
    with pytest.raises(BenchmarkEvidenceError, match="requires a human"):
        aggregate_case_attempts([_raw_attempt()])


def test_complete_separate_rating_set_enables_live_scoring() -> None:
    attempts = [_raw_attempt("box"), _raw_attempt("hole")]
    rated = apply_human_rating_set(
        attempts, _rating_set(attempts), run_binding=RUN_BINDING
    )
    report = aggregate_case_attempts(rated, expected_case_ids=("box", "hole"))

    assert report["live_model_score"] is True
    assert report["target_completion_rate"] == 1.0
    assert all(
        item["instruction_adherence"]["status"] == "rated"
        for item in report["case_attempts"]
    )


def test_missing_and_duplicate_human_ratings_fail() -> None:
    attempts = [_raw_attempt("box"), _raw_attempt("hole")]
    missing = _rating_set(attempts)
    missing["ratings"].pop()
    with pytest.raises(BenchmarkEvidenceError, match="incomplete"):
        apply_human_rating_set(attempts, missing, run_binding=RUN_BINDING)

    duplicate = _rating_set(attempts)
    duplicate["ratings"].append(deepcopy(duplicate["ratings"][0]))
    with pytest.raises(BenchmarkEvidenceError, match="Duplicate"):
        apply_human_rating_set(attempts, duplicate, run_binding=RUN_BINDING)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "anthropic"),
        ("model", "stale-model"),
        ("source_commit", "c" * 40),
        ("evidence_sha256", "d" * 64),
    ],
)
def test_stale_or_mismatched_rating_binding_fails(field: str, value: str) -> None:
    attempts = [_raw_attempt()]
    ratings = _rating_set(attempts)
    ratings["ratings"][0][field] = value

    with pytest.raises(BenchmarkEvidenceError, match="stale or mismatched"):
        apply_human_rating_set(attempts, ratings, run_binding=RUN_BINDING)


def test_tampered_evidence_invalidates_existing_rating() -> None:
    attempt = _raw_attempt()
    ratings = _rating_set([attempt])
    attempt["elapsed_seconds"] = 2.0

    with pytest.raises(BenchmarkEvidenceError, match="evidence_sha256"):
        apply_human_rating_set([attempt], ratings, run_binding=RUN_BINDING)


def test_out_of_range_rating_fails() -> None:
    attempt = _raw_attempt()
    ratings = _rating_set([attempt])
    ratings["ratings"][0]["rating"] = 6

    with pytest.raises(BenchmarkEvidenceError, match="outside its scale"):
        apply_human_rating_set([attempt], ratings, run_binding=RUN_BINDING)


@pytest.mark.parametrize(
    "field",
    (
        "readiness_sha256",
        "runtime_identity_sha256",
        "limits_sha256",
        "raw_run_sha256",
    ),
)
def test_human_rating_rejects_stale_run_identity(field: str) -> None:
    attempt = _raw_attempt()
    ratings = _rating_set([attempt])
    ratings[field] = "0" * 64

    with pytest.raises(BenchmarkEvidenceError, match="stale or mismatched"):
        apply_human_rating_set([attempt], ratings, run_binding=RUN_BINDING)


def test_human_rating_requires_external_run_binding() -> None:
    attempt = _raw_attempt()
    with pytest.raises(BenchmarkEvidenceError, match="exact readiness"):
        apply_human_rating_set([attempt], _rating_set([attempt]))


@pytest.mark.parametrize("scope", ["top", "rating", "scale"])
def test_unknown_rating_contract_fields_fail(scope: str) -> None:
    attempt = _raw_attempt()
    ratings = _rating_set([attempt])
    target = (
        ratings
        if scope == "top"
        else ratings["ratings"][0]
        if scope == "rating"
        else ratings["ratings"][0]["scale"]
    )
    target["unknown"] = True

    with pytest.raises(BenchmarkEvidenceError, match="unknown|scale"):
        apply_human_rating_set([attempt], ratings, run_binding=RUN_BINDING)


def test_rating_set_timestamp_must_be_utc() -> None:
    attempt = _raw_attempt()
    ratings = _rating_set([attempt])
    ratings["created_at"] = "2026-07-22T12:00:00-06:00"

    with pytest.raises(BenchmarkEvidenceError, match="UTC"):
        apply_human_rating_set([attempt], ratings, run_binding=RUN_BINDING)


def _readiness(created_at: datetime, **changes) -> dict:
    result = {
        "schema": "vibecad-provider-readiness-v1",
        "version": 1,
        "created_at": created_at.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
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
        "auth_source": "OS keyring",
        "online_by_default": True,
        "endpoint_identity": "https://api.openai.com/v1",
        "endpoint_sha256": hashlib.sha256(
            b"https://api.openai.com/v1"
        ).hexdigest(),
        "credential_binding_nonce": "1" * 64,
        "credential_fingerprint_algorithm": "hmac-sha256-v1",
        "credential_fingerprint": "2" * 64,
        "process_timed_out": False,
        "process_exit_code": 0,
        "ready_for_live_benchmark": True,
    }
    result.update(changes)
    return result


def test_live_readiness_rejects_stale_future_and_tampered_results() -> None:
    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    validate_live_readiness(_readiness(now), now=now)

    with pytest.raises(BenchmarkEvidenceError, match="stale"):
        validate_live_readiness(
            _readiness(now - timedelta(seconds=121)), now=now
        )
    with pytest.raises(BenchmarkEvidenceError, match="future"):
        validate_live_readiness(
            _readiness(now + timedelta(seconds=6)), now=now
        )
    tampered = _readiness(now)
    tampered["unknown"] = "value"
    with pytest.raises(BenchmarkEvidenceError, match="unknown"):
        validate_live_readiness(tampered, now=now)


@pytest.mark.parametrize(
    "changes",
    [
        {"model_validation_performed": False},
        {"model_available": False},
        {"model": ""},
    ],
)
def test_live_readiness_rejects_unverified_selected_model(changes: dict) -> None:
    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(BenchmarkEvidenceError, match="model"):
        validate_live_readiness(_readiness(now, **changes), now=now)


def test_live_readiness_rejects_unbounded_provider_adapter() -> None:
    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    report = _readiness(now, provider="anthropic")
    report["endpoint_identity"] = "https://api.anthropic.com"
    report["endpoint_sha256"] = hashlib.sha256(
        b"https://api.anthropic.com"
    ).hexdigest()

    with pytest.raises(BenchmarkEvidenceError, match="only the bounded OpenAI"):
        validate_live_readiness(report, now=now)
