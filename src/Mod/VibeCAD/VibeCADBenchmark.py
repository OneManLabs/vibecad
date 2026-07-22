# SPDX-License-Identifier: LGPL-2.1-or-later
"""Versioned evidence and scoring contracts for conversational CAD benchmarks."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping


CASE_ATTEMPT_SCHEMA = "vibecad-benchmark-case-attempt-v2"
SERIES_SCHEMA = "vibecad-benchmark-series-v2"
BENCHMARK_VERSION = 2
VALIDATION_STAGES = (
    "geometry",
    "dimensions",
    "constraints",
    "editability",
    "follow_up",
    "reopen",
    "export",
)
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
    "total_tokens",
)


class BenchmarkEvidenceError(ValueError):
    """Raised when benchmark evidence is incomplete or inconsistent."""


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkEvidenceError(f"{field} must be a nonempty string.")
    return value.strip()


def _nonnegative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BenchmarkEvidenceError(f"{field} must be a nonnegative integer.")
    return value


def _positive_integer(value: Any, field: str) -> int:
    result = _nonnegative_integer(value, field)
    if result == 0:
        raise BenchmarkEvidenceError(f"{field} must be greater than zero.")
    return result


def validation_stage(
    *,
    applicable: bool,
    passed: bool | None = None,
    evidence: Mapping[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Create one explicit validation-stage result."""

    if not isinstance(applicable, bool):
        raise BenchmarkEvidenceError("Stage applicability must be Boolean.")
    details = dict(evidence or {})
    if applicable:
        if not isinstance(passed, bool):
            raise BenchmarkEvidenceError(
                "An applicable validation stage must have a Boolean result."
            )
        if not details:
            raise BenchmarkEvidenceError(
                "An applicable validation stage must contain evidence."
            )
    else:
        if passed is not None:
            raise BenchmarkEvidenceError(
                "A validation stage that does not apply must have a null result."
            )
        details = {"reason": _required_string(reason, "stage reason")}
    return {"applicable": applicable, "passed": passed, "evidence": details}


def normalized_usage(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    reasoning_tokens: int = 0,
    total_tokens: int | None = None,
    estimated_cost_usd: float | None = None,
) -> dict[str, Any]:
    """Create provider-neutral token and cost evidence."""

    values = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "reasoning_tokens": reasoning_tokens,
    }
    for field, value in values.items():
        _nonnegative_integer(value, field)
    minimum_total = input_tokens + output_tokens
    resolved_total = minimum_total if total_tokens is None else total_tokens
    _nonnegative_integer(resolved_total, "total_tokens")
    if resolved_total < minimum_total:
        raise BenchmarkEvidenceError(
            "total_tokens cannot be less than input_tokens plus output_tokens."
        )
    if estimated_cost_usd is not None and (
        isinstance(estimated_cost_usd, bool)
        or not isinstance(estimated_cost_usd, (int, float))
        or not math.isfinite(float(estimated_cost_usd))
        or float(estimated_cost_usd) < 0
    ):
        raise BenchmarkEvidenceError(
            "estimated_cost_usd must be null or a nonnegative finite number."
        )
    return {
        **values,
        "total_tokens": resolved_total,
        "estimated_cost_usd": (
            None if estimated_cost_usd is None else float(estimated_cost_usd)
        ),
    }


def unrated_instruction_adherence(reason: str) -> dict[str, Any]:
    """Record why a case has no human instruction-adherence rating."""

    return {
        "status": "not_rated",
        "reason": _required_string(reason, "instruction adherence reason"),
        "rating": None,
        "scale": None,
        "reviewer_id": None,
        "notes": None,
    }


def rated_instruction_adherence(
    *,
    rating: float,
    scale_minimum: float,
    scale_maximum: float,
    reviewer_id: str,
    notes: str,
) -> dict[str, Any]:
    """Create one bounded human instruction-adherence rating."""

    return {
        "status": "rated",
        "reason": None,
        "rating": rating,
        "scale": {
            "minimum": scale_minimum,
            "maximum": scale_maximum,
        },
        "reviewer_id": reviewer_id,
        "notes": notes,
    }


def failure_diagnostics(
    stages: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Create stable diagnostics for failed applicable validation stages."""

    result = []
    for stage in VALIDATION_STAGES:
        item = stages.get(stage)
        if isinstance(item, Mapping) and item.get("applicable") is True and item.get(
            "passed"
        ) is False:
            result.append(
                {
                    "severity": "error",
                    "stage": stage,
                    "code": "validation_failed",
                    "message": f"The {stage} validation stage failed.",
                    "details": dict(item.get("evidence") or {}),
                }
            )
    return result


def make_case_attempt(
    *,
    tier: int,
    case_id: str,
    attempt: int,
    provider: str,
    model: str,
    executor: str,
    live_model_score: bool,
    stages: Mapping[str, Mapping[str, Any]],
    question_count: int,
    unnecessary_question_count: int,
    retry_count: int,
    usage: Mapping[str, Any],
    instruction_adherence: Mapping[str, Any],
    elapsed_seconds: float,
    diagnostics: Iterable[Mapping[str, Any]] | None = None,
    artifact_paths: Iterable[str] = (),
) -> dict[str, Any]:
    """Build and validate one case-attempt record."""

    stage_copy = {name: dict(value) for name, value in stages.items()}
    passed = all(
        item.get("passed") is True
        for item in stage_copy.values()
        if item.get("applicable") is True
    )
    record = {
        "schema": CASE_ATTEMPT_SCHEMA,
        "version": BENCHMARK_VERSION,
        "tier": tier,
        "case_id": case_id,
        "attempt": attempt,
        "provider": provider,
        "model": model,
        "executor": executor,
        "live_model_score": live_model_score,
        "passed": passed,
        "validation": stage_copy,
        "question_count": question_count,
        "unnecessary_question_count": unnecessary_question_count,
        "retry_count": retry_count,
        "normalized_usage": dict(usage),
        "instruction_adherence": dict(instruction_adherence),
        "elapsed_seconds": elapsed_seconds,
        "failure_diagnostics": [dict(item) for item in (diagnostics or [])],
        "artifact_paths": [str(path) for path in artifact_paths],
    }
    validate_case_attempt(record)
    return record


def validate_case_attempt(record: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed unless one case attempt has complete, consistent evidence."""

    if not isinstance(record, Mapping):
        raise BenchmarkEvidenceError("Case-attempt evidence must be an object.")
    if record.get("schema") != CASE_ATTEMPT_SCHEMA:
        raise BenchmarkEvidenceError("The case-attempt schema is invalid.")
    if record.get("version") != BENCHMARK_VERSION:
        raise BenchmarkEvidenceError("The case-attempt version is invalid.")
    tier = _positive_integer(record.get("tier"), "tier")
    if tier not in {1, 2, 3}:
        raise BenchmarkEvidenceError("tier must be 1, 2, or 3.")
    _required_string(record.get("case_id"), "case_id")
    _positive_integer(record.get("attempt"), "attempt")
    _required_string(record.get("provider"), "provider")
    _required_string(record.get("model"), "model")
    _required_string(record.get("executor"), "executor")
    if not isinstance(record.get("live_model_score"), bool):
        raise BenchmarkEvidenceError("live_model_score must be Boolean.")
    if not isinstance(record.get("passed"), bool):
        raise BenchmarkEvidenceError("passed must be Boolean.")

    stages = record.get("validation")
    if not isinstance(stages, Mapping) or set(stages) != set(VALIDATION_STAGES):
        raise BenchmarkEvidenceError(
            "validation must contain each required stage exactly once."
        )
    applicable_count = 0
    stage_passed = True
    for name in VALIDATION_STAGES:
        item = stages[name]
        if not isinstance(item, Mapping):
            raise BenchmarkEvidenceError(f"Validation stage {name} must be an object.")
        applicable = item.get("applicable")
        result = item.get("passed")
        evidence = item.get("evidence")
        if not isinstance(applicable, bool):
            raise BenchmarkEvidenceError(
                f"Validation stage {name} must declare Boolean applicability."
            )
        if not isinstance(evidence, Mapping) or not evidence:
            raise BenchmarkEvidenceError(
                f"Validation stage {name} must contain evidence."
            )
        if applicable:
            applicable_count += 1
            if not isinstance(result, bool):
                raise BenchmarkEvidenceError(
                    f"Applicable validation stage {name} must have a Boolean result."
                )
            stage_passed = stage_passed and result
        else:
            if result is not None:
                raise BenchmarkEvidenceError(
                    f"Validation stage {name} that does not apply must have a null result."
                )
            _required_string(
                evidence.get("reason"), f"validation.{name}.evidence.reason"
            )
    if applicable_count == 0:
        raise BenchmarkEvidenceError("At least one validation stage must apply.")
    if record.get("passed") is not stage_passed:
        raise BenchmarkEvidenceError(
            "The case result does not match its applicable validation stages."
        )

    question_count = _nonnegative_integer(
        record.get("question_count"), "question_count"
    )
    unnecessary = _nonnegative_integer(
        record.get("unnecessary_question_count"), "unnecessary_question_count"
    )
    if unnecessary > question_count:
        raise BenchmarkEvidenceError(
            "unnecessary_question_count cannot exceed question_count."
        )
    _nonnegative_integer(record.get("retry_count"), "retry_count")

    usage = record.get("normalized_usage")
    if not isinstance(usage, Mapping):
        raise BenchmarkEvidenceError("normalized_usage must be an object.")
    for field in USAGE_FIELDS:
        _nonnegative_integer(usage.get(field), f"normalized_usage.{field}")
    if usage["total_tokens"] < usage["input_tokens"] + usage["output_tokens"]:
        raise BenchmarkEvidenceError(
            "Normalized total token use is smaller than input plus output use."
        )
    cost = usage.get("estimated_cost_usd")
    if cost is not None and (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or not math.isfinite(float(cost))
        or float(cost) < 0
    ):
        raise BenchmarkEvidenceError(
            "normalized_usage.estimated_cost_usd is invalid."
        )

    adherence = record.get("instruction_adherence")
    if not isinstance(adherence, Mapping):
        raise BenchmarkEvidenceError("instruction_adherence must be an object.")
    status = _required_string(
        adherence.get("status"), "instruction_adherence.status"
    )
    if status == "not_rated":
        _required_string(
            adherence.get("reason"), "instruction_adherence.reason"
        )
        if any(
            adherence.get(field) is not None
            for field in ("rating", "scale", "reviewer_id", "notes")
        ):
            raise BenchmarkEvidenceError(
                "An unrated instruction-adherence result cannot contain rating data."
            )
        if record["live_model_score"]:
            raise BenchmarkEvidenceError(
                "A live-model case requires a human instruction-adherence rating."
            )
    elif status == "rated":
        rating = adherence.get("rating")
        scale = adherence.get("scale")
        if (
            isinstance(rating, bool)
            or not isinstance(rating, (int, float))
            or not math.isfinite(float(rating))
        ):
            raise BenchmarkEvidenceError(
                "instruction_adherence.rating must be finite."
            )
        if not isinstance(scale, Mapping):
            raise BenchmarkEvidenceError(
                "instruction_adherence.scale must be an object."
            )
        minimum = scale.get("minimum")
        maximum = scale.get("maximum")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in (minimum, maximum)
        ):
            raise BenchmarkEvidenceError(
                "The instruction-adherence scale must be finite."
            )
        if float(minimum) >= float(maximum):
            raise BenchmarkEvidenceError(
                "The instruction-adherence scale minimum must be less than its maximum."
            )
        if not float(minimum) <= float(rating) <= float(maximum):
            raise BenchmarkEvidenceError(
                "The instruction-adherence rating is outside its scale."
            )
        _required_string(
            adherence.get("reviewer_id"), "instruction_adherence.reviewer_id"
        )
        _required_string(
            adherence.get("notes"), "instruction_adherence.notes"
        )
        if adherence.get("reason") is not None:
            raise BenchmarkEvidenceError(
                "A rated instruction-adherence result cannot contain an unrated reason."
            )
    else:
        raise BenchmarkEvidenceError(
            "instruction_adherence.status must be rated or not_rated."
        )

    elapsed = record.get("elapsed_seconds")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(float(elapsed))
        or float(elapsed) < 0
    ):
        raise BenchmarkEvidenceError(
            "elapsed_seconds must be nonnegative and finite."
        )

    diagnostics = record.get("failure_diagnostics")
    if not isinstance(diagnostics, list):
        raise BenchmarkEvidenceError("failure_diagnostics must be a list.")
    error_count = 0
    for index, item in enumerate(diagnostics):
        if not isinstance(item, Mapping):
            raise BenchmarkEvidenceError(
                f"failure_diagnostics[{index}] must be an object."
            )
        severity = _required_string(item.get("severity"), "diagnostic severity")
        if severity not in {"warning", "error"}:
            raise BenchmarkEvidenceError("Diagnostic severity is invalid.")
        stage = _required_string(item.get("stage"), "diagnostic stage")
        if stage not in {*VALIDATION_STAGES, "execution"}:
            raise BenchmarkEvidenceError("Diagnostic stage is invalid.")
        _required_string(item.get("code"), "diagnostic code")
        _required_string(item.get("message"), "diagnostic message")
        if not isinstance(item.get("details", {}), Mapping):
            raise BenchmarkEvidenceError("Diagnostic details must be an object.")
        error_count += severity == "error"
    if record["passed"] and error_count:
        raise BenchmarkEvidenceError(
            "A passed case cannot contain an error diagnostic."
        )
    if not record["passed"] and error_count == 0:
        raise BenchmarkEvidenceError(
            "A failed case must contain an error diagnostic."
        )

    artifacts = record.get("artifact_paths")
    if not isinstance(artifacts, list) or any(
        not isinstance(path, str) or not path.strip() for path in artifacts
    ):
        raise BenchmarkEvidenceError(
            "artifact_paths must contain nonempty strings."
        )
    return dict(record)


def aggregate_case_attempts(
    attempts: Iterable[Mapping[str, Any]],
    *,
    created_at: str | None = None,
    expected_case_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Aggregate complete case attempts with a case-attempt denominator."""

    records = [validate_case_attempt(item) for item in attempts]
    if not records:
        raise BenchmarkEvidenceError("At least one case attempt is required.")
    identity_fields = (
        "tier",
        "provider",
        "model",
        "executor",
        "live_model_score",
    )
    identity = tuple(records[0][field] for field in identity_fields)
    for record in records[1:]:
        if tuple(record[field] for field in identity_fields) != identity:
            raise BenchmarkEvidenceError(
                "One benchmark series cannot mix tier, provider, model, executor, or score type."
            )

    seen: set[tuple[str, int]] = set()
    by_attempt: dict[int, set[str]] = defaultdict(set)
    for record in records:
        key = (record["case_id"], record["attempt"])
        if key in seen:
            raise BenchmarkEvidenceError(
                f"Duplicate benchmark evidence for case {key[0]} attempt {key[1]}."
            )
        seen.add(key)
        by_attempt[record["attempt"]].add(record["case_id"])
    rounds = sorted(by_attempt)
    if rounds != list(range(1, len(rounds) + 1)):
        raise BenchmarkEvidenceError(
            "Benchmark attempt numbers must be contiguous from one."
        )
    expected_cases = by_attempt[rounds[0]]
    for attempt, case_ids in by_attempt.items():
        if case_ids != expected_cases:
            missing = sorted(expected_cases - case_ids)
            extra = sorted(case_ids - expected_cases)
            raise BenchmarkEvidenceError(
                f"Attempt {attempt} has inconsistent cases; missing={missing}, extra={extra}."
            )
    if expected_case_ids is not None:
        required_cases = {
            _required_string(case_id, "expected case ID")
            for case_id in expected_case_ids
        }
        if not required_cases:
            raise BenchmarkEvidenceError(
                "The expected benchmark case set cannot be empty."
            )
        if expected_cases != required_cases:
            missing = sorted(required_cases - expected_cases)
            extra = sorted(expected_cases - required_cases)
            raise BenchmarkEvidenceError(
                f"The benchmark case set is incomplete; missing={missing}, extra={extra}."
            )

    passed = sum(record["passed"] is True for record in records)
    failed = len(records) - passed
    case_rate = passed / len(records)
    fully_passed_rounds = sum(
        all(
            record["passed"]
            for record in records
            if record["attempt"] == attempt
        )
        for attempt in rounds
    )
    live_model = bool(records[0]["live_model_score"])
    partition = {
        "case_attempt_count": len(records),
        "passed_case_attempts": passed,
        "failed_case_attempts": failed,
        "completion_rate": case_rate,
    }
    return {
        "schema": SERIES_SCHEMA,
        "version": BENCHMARK_VERSION,
        "created_at": created_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tier": records[0]["tier"],
        "provider": records[0]["provider"],
        "model": records[0]["model"],
        "executor": records[0]["executor"],
        "live_model_score": live_model,
        "case_ids": sorted(expected_cases),
        "case_count": len(expected_cases),
        "attempt_round_count": len(rounds),
        "case_attempt_count": len(records),
        "passed_case_attempts": passed,
        "failed_case_attempts": failed,
        "case_attempt_completion_rate": case_rate,
        # This alias preserves practical consumers of the v1 report. In v2 it
        # uses the case-attempt denominator, never the whole-trial rate.
        "valid_completion_rate": case_rate,
        "completion_rates": {
            "deterministic": None if live_model else partition,
            "live_model": partition if live_model else None,
        },
        "target_completion_rate": case_rate if live_model else None,
        "trial_summary": {
            "round_count": len(rounds),
            "fully_passed_rounds": fully_passed_rounds,
            "fully_passed_round_rate": fully_passed_rounds / len(rounds),
            "target_completion_metric": False,
        },
        "case_attempts": sorted(
            records, key=lambda item: (item["attempt"], item["case_id"])
        ),
    }


def finalize_series_report(
    attempts: Iterable[Mapping[str, Any]],
    runner_results: Iterable[Mapping[str, Any]],
    *,
    expected_case_ids: Iterable[str],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Attach GUI runner evidence without discarding valid failed cases."""

    report = aggregate_case_attempts(
        attempts,
        created_at=created_at,
        expected_case_ids=expected_case_ids,
    )
    runs = []
    seen_attempts: set[int] = set()
    for item in runner_results:
        if not isinstance(item, Mapping):
            raise BenchmarkEvidenceError("GUI runner evidence must be an object.")
        attempt = _positive_integer(item.get("attempt"), "runner attempt")
        if attempt in seen_attempts:
            raise BenchmarkEvidenceError(
                f"Duplicate GUI runner evidence for attempt {attempt}."
            )
        seen_attempts.add(attempt)
        reported_ok = item.get("gui_runner_reported_ok")
        if not isinstance(reported_ok, bool):
            raise BenchmarkEvidenceError(
                "gui_runner_reported_ok must be Boolean."
            )
        exit_code = item.get("gui_runner_exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise BenchmarkEvidenceError(
                "gui_runner_exit_code must be an integer."
            )
        runs.append(
            {
                "attempt": attempt,
                "case_evidence_passed": bool(item.get("case_evidence_passed")),
                "gui_runner_exit_code": exit_code,
                "gui_runner_reported_ok": reported_ok,
            }
        )
    expected_attempts = set(range(1, report["attempt_round_count"] + 1))
    if seen_attempts != expected_attempts:
        raise BenchmarkEvidenceError(
            "GUI runner evidence does not match the case-attempt rounds."
        )
    report["trial_runs"] = sorted(runs, key=lambda item: item["attempt"])
    report["runner_gate_passed"] = all(
        item["gui_runner_reported_ok"] for item in runs
    )
    return report


def series_exit_code(report: Mapping[str, Any]) -> int:
    """Return success only when case evidence and the GUI runner gate pass."""

    failed = _nonnegative_integer(
        report.get("failed_case_attempts"), "failed_case_attempts"
    )
    runner_gate = report.get("runner_gate_passed")
    if not isinstance(runner_gate, bool):
        raise BenchmarkEvidenceError("runner_gate_passed must be Boolean.")
    return 0 if failed == 0 and runner_gate else 1
