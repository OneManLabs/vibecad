# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import sys
import threading
import time
from pathlib import Path

import pytest

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
    LIVE_MAX_PROGRESS_EVENT_BYTES,
    LIVE_MAX_RETAINED_PROGRESS_EVENTS,
    LIVE_PARTIAL_METRICS_SCHEMA,
    LIVE_PARTIAL_METRICS_VERSION,
    LIVE_RUN_SCHEMA,
    LIVE_RUN_VERSION,
    TIER1_CASE_IDS,
    empty_partial_case_metrics,
    live_run_unscorable_reasons,
    persist_partial_metrics_checkpoint,
    validate_partial_metrics_checkpoint,
    validate_unrated_live_run,
)


ROOT = Path(__file__).resolve().parents[4]
BENCHMARK_DIR = ROOT / "tests" / "benchmark"
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

import tier1_live_provider_runner as live_runner


SOURCE_COMMIT = "a" * 40
READINESS_SHA256 = "b" * 64
RUNTIME_SHA256 = "c" * 64
RUN_NONCE = "d" * 64


def _request(turn: int, *, retries: int = 2) -> dict:
    return {
        "event": "provider_request_started",
        "turn": turn,
        "sdk_max_retries": retries,
        "api_attempt_ceiling": 1 + retries,
    }


def _usage(turn: int, *, mode: str = "incremental", total: int = 15) -> dict:
    return {
        "event": "provider_usage",
        "turn": turn,
        "mode": mode,
        "usage_available": True,
        "usage_complete": True,
        "usage": {
            "input_tokens": total - 5,
            "output_tokens": 5,
            "cached_input_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": total,
        },
    }


def test_request_monitor_accepts_a_lower_consistent_retry_ceiling() -> None:
    measured = live_runner.Measurements()
    measured.progress(_request(1, retries=0))
    measured.progress(_usage(1, mode="cumulative"))

    assert measured.limit_error is None
    assert measured.snapshot()["api_request_count"] == 1


def test_request_monitor_accepts_nondecreasing_cumulative_turn_updates() -> None:
    measured = live_runner.Measurements()
    measured.progress(_request(1, retries=0))
    measured.progress(_usage(1, mode="cumulative", total=15))
    measured.progress(_usage(1, mode="cumulative", total=30))

    snapshot = measured.snapshot()
    assert measured.limit_error is None
    assert snapshot["usage_event_count"] == 1
    assert snapshot["usage"]["total_tokens"] == 30


def test_live_provider_operation_runs_off_gui_thread_while_events_pump() -> None:
    main_thread = threading.current_thread()
    observed: dict[str, object] = {}

    def operation() -> str:
        observed["worker"] = threading.current_thread()
        time.sleep(0.02)
        return "complete"

    def event_pump() -> None:
        observed["pump"] = threading.current_thread()

    result = live_runner._run_on_gui_worker(operation, event_pump)

    assert result == "complete"
    assert observed["worker"] is not main_thread
    assert observed["pump"] is main_thread


def _partial(
    case_id: str = TIER1_CASE_IDS[0],
    *,
    metrics: dict | None = None,
) -> dict:
    now = time.time()
    return {
        "schema": LIVE_PARTIAL_METRICS_SCHEMA,
        "version": LIVE_PARTIAL_METRICS_VERSION,
        "case_id": case_id,
        "source_commit": SOURCE_COMMIT,
        "readiness_sha256": READINESS_SHA256,
        "runtime_identity_sha256": RUNTIME_SHA256,
        "run_nonce": RUN_NONCE,
        "started_at_epoch": now,
        "updated_at_epoch": now,
        "checkpoint_sequence": 1,
        "provider_class": "OpenAIProvider",
        "fixture": {
            "kind": "benchmark_setup",
            "canonical_sha256": "e" * 64,
            "object_names": [],
        },
        "measurements": metrics or empty_partial_case_metrics(),
    }


def _attempt(case_id: str, *, passed: bool, usage_total: int) -> dict:
    stages = {
        name: (
            validation_stage(
                applicable=True,
                passed=passed if name == "geometry" else True,
                evidence={"check": name},
            )
            if name not in {"follow_up", "export"}
            else validation_stage(applicable=False, reason=f"{name} does not apply.")
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
        usage=normalized_usage(
            input_tokens=max(0, usage_total - 5),
            output_tokens=5 if usage_total else 0,
            total_tokens=usage_total,
        ),
        instruction_adherence=unrated_instruction_adherence(
            "A separate human rating is required."
        ),
        elapsed_seconds=1.0,
        diagnostics=failure_diagnostics(stages),
        artifact_paths=[artifact],
        source_commit=SOURCE_COMMIT,
        artifact_sha256={artifact: "f" * 64},
        allow_unrated_live=True,
    )


def _runtime(case_id: str, *, usage_complete: bool) -> dict:
    return {
        "case_id": case_id,
        "provider_class": "OpenAIProvider",
        "provider_turn_count": 1,
        "provider_turns_observed": [1],
        "api_request_count": 1,
        "api_attempt_upper_bound": 3,
        "tool_call_count": 1,
        "retry_count": 0,
        "usage_event_count": 1 if usage_complete else 0,
        "usage_mode": "incremental" if usage_complete else None,
        "usage_complete": usage_complete,
        "missing_usage_turns": [] if usage_complete else [1],
        "usage_integrity_error_count": 0,
        "event_evidence_complete": True,
        "partial_evidence_valid": True,
        "fixture": {
            "kind": "benchmark_setup",
            "canonical_sha256": "1" * 64,
            "object_names": [],
        },
        "final_sha256": "2" * 64,
        "isolated_validation_ok": usage_complete,
    }


def _unscorable_run() -> dict:
    attempts = [
        _attempt(
            case_id,
            passed=index != 0,
            usage_total=0 if index == 0 else 15,
        )
        for index, case_id in enumerate(TIER1_CASE_IDS)
    ]
    runtimes = [
        _runtime(case_id, usage_complete=index != 0)
        for index, case_id in enumerate(TIER1_CASE_IDS)
    ]
    reasons = live_run_unscorable_reasons(runtimes)
    return {
        "schema": LIVE_RUN_SCHEMA,
        "version": LIVE_RUN_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tier": 1,
        "provider": "openai",
        "model": "gpt-test",
        "executor": LIVE_EXECUTOR,
        "source_commit": SOURCE_COMMIT,
        "readiness_sha256": READINESS_SHA256,
        "runtime_identity_sha256": RUNTIME_SHA256,
        "run_nonce": RUN_NONCE,
        "limits": dict(LIVE_LIMITS),
        "usage_summary": {
            "input_tokens": 60,
            "output_tokens": 30,
            "cached_input_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 90,
        },
        "case_runtime": runtimes,
        "scorable": False,
        "unscorable_reasons": reasons,
        "scored": False,
        "case_attempts": attempts,
    }


@pytest.mark.parametrize("fault", ("duplicate", "mixed", "decreasing", "missing"))
def test_usage_events_are_bound_to_one_request_turn(fault: str) -> None:
    measured = live_runner.Measurements()
    measured.progress(_request(1))
    if fault == "missing":
        pass
    elif fault == "duplicate":
        measured.progress(_usage(1))
        measured.progress(_usage(1))
    elif fault == "mixed":
        measured.progress(_usage(1))
        measured.progress(_request(2))
        measured.progress(_usage(2, mode="cumulative", total=30))
    else:
        measured.progress(_usage(1, mode="cumulative", total=15))
        measured.progress(_request(2))
        measured.progress(_usage(2, mode="cumulative", total=14))

    snapshot = measured.snapshot()
    assert snapshot["usage_complete"] is False
    if fault == "missing":
        assert snapshot["missing_usage_turns"] == [1]
    else:
        assert snapshot["usage_integrity_errors"]


def test_progress_ledger_redacts_deltas_and_enforces_fixed_bounds() -> None:
    measured = live_runner.Measurements()
    measured.progress(
        {"event": "provider_reasoning_delta", "turn": 1, "text": "secret reasoning"}
    )
    retained = measured.events[0]
    assert "text" not in retained
    assert retained["text_length"] == len("secret reasoning")
    assert len(retained["text_sha256"]) == 64

    measured.progress({"event": "oversized", "payload": "x" * (LIVE_MAX_PROGRESS_EVENT_BYTES * 2)})
    for index in range(LIVE_MAX_RETAINED_PROGRESS_EVENTS + 5):
        measured.progress({"event": "tick", "index": index})
    snapshot = measured.snapshot()
    assert len(snapshot["events"]) <= LIVE_MAX_RETAINED_PROGRESS_EVENTS
    assert snapshot["dropped_event_count"] > 0
    assert snapshot["event_evidence_complete"] is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("case_id", TIER1_CASE_IDS[1], "case binding"),
        ("run_nonce", "9" * 64, "run nonce"),
        ("started_at_epoch", float("nan"), "finite"),
    ),
)
def test_partial_v2_rejects_replay_and_nonfinite_values(
    field: str, value: object, message: str
) -> None:
    partial = _partial()
    partial[field] = value
    with pytest.raises(BenchmarkEvidenceError, match=message):
        validate_partial_metrics_checkpoint(
            partial,
            expected_case_id=TIER1_CASE_IDS[0],
            expected_source_commit=SOURCE_COMMIT,
            expected_readiness_sha256=READINESS_SHA256,
            expected_runtime_identity_sha256=RUNTIME_SHA256,
            expected_run_nonce=RUN_NONCE,
        )


def test_partial_v2_rejects_an_oversized_event() -> None:
    metrics = empty_partial_case_metrics()
    event = {"event": "oversized", "payload": "x" * LIVE_MAX_PROGRESS_EVENT_BYTES}
    metrics["events"] = [event]
    metrics["retained_event_bytes"] = len(str(event).encode("utf-8"))
    with pytest.raises(BenchmarkEvidenceError, match="byte bound|byte count"):
        validate_partial_metrics_checkpoint(_partial(metrics=metrics))


def test_checkpoint_throttle_keeps_memory_current() -> None:
    state: dict = {}
    writes: list[int] = []

    def writer(path: Path, payload: dict) -> None:
        writes.append(payload["sequence"])

    assert persist_partial_metrics_checkpoint(
        state,
        {"sequence": 1},
        Path("unused.json"),
        writer=writer,
        now_monotonic=10.0,
    )
    assert not persist_partial_metrics_checkpoint(
        state,
        {"sequence": 2},
        Path("unused.json"),
        writer=writer,
        now_monotonic=10.1,
    )
    assert state["partial"] == {"sequence": 2}
    assert persist_partial_metrics_checkpoint(
        state,
        {"sequence": 3},
        Path("unused.json"),
        writer=writer,
        now_monotonic=10.3,
    )
    assert writes == [1, 3]


def test_missing_usage_keeps_seven_records_but_blocks_scoring() -> None:
    run = _unscorable_run()
    validated = validate_unrated_live_run(run, require_scorable=False)
    assert len(validated["case_attempts"]) == 7
    assert validated["case_runtime"][0]["usage_complete"] is False
    assert validated["scorable"] is False

    with pytest.raises(BenchmarkEvidenceError, match="not scorable"):
        validate_unrated_live_run(run, require_scorable=True)


def test_failed_case_record_retains_missing_usage_without_claiming_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    measured = live_runner.Measurements()
    measured.progress(_request(1))
    partial = _partial(metrics=measured.snapshot())
    monkeypatch.setattr(live_runner, "_close_all", lambda: None)

    record, runtime = live_runner._failed_case_attempt(
        {"id": TIER1_CASE_IDS[0], "prompt": "Create a box."},
        tmp_path,
        {"provider": "openai", "model": "gpt-test"},
        SOURCE_COMMIT,
        READINESS_SHA256,
        RUNTIME_SHA256,
        RUN_NONCE,
        "TimeoutError: provider timed out",
        in_memory_partial=partial,
    )

    assert record["passed"] is False
    assert runtime["api_request_count"] == 1
    assert runtime["usage_event_count"] == 0
    assert runtime["usage_complete"] is False
    assert runtime["missing_usage_turns"] == [1]
