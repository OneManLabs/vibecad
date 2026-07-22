# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import pytest

from VibeCADBenchmark import (
    BenchmarkEvidenceError,
    VALIDATION_STAGES,
    aggregate_case_attempts,
    failure_diagnostics,
    finalize_series_report,
    make_case_attempt,
    normalized_usage,
    rated_instruction_adherence,
    series_exit_code,
    unrated_instruction_adherence,
    validation_stage,
)


def _stages(*, failed: str | None = None) -> dict:
    result = {}
    for name in VALIDATION_STAGES:
        if name in {"follow_up", "export"}:
            result[name] = validation_stage(
                applicable=False, reason=f"{name} is not part of this case."
            )
        else:
            passed = name != failed
            result[name] = validation_stage(
                applicable=True,
                passed=passed,
                evidence={"check": f"{name}-check", "observed": passed},
            )
    return result


def _attempt(
    case_id: str,
    attempt: int,
    *,
    failed: str | None = None,
    live: bool = False,
) -> dict:
    stages = _stages(failed=failed)
    return make_case_attempt(
        tier=1,
        case_id=case_id,
        attempt=attempt,
        provider="fixture",
        model="fixture-model",
        executor="deterministic-fixture",
        live_model_score=live,
        stages=stages,
        question_count=0,
        unnecessary_question_count=0,
        retry_count=0,
        usage=normalized_usage(),
        instruction_adherence=(
            rated_instruction_adherence(
                rating=4,
                scale_minimum=1,
                scale_maximum=5,
                reviewer_id="reviewer-01",
                notes="The result follows the requested dimensions.",
            )
            if live
            else unrated_instruction_adherence(
                "A deterministic fixture is not rated by a human."
            )
        ),
        elapsed_seconds=0.25,
        diagnostics=failure_diagnostics(stages),
        artifact_paths=["case.FCStd"],
    )


def test_series_uses_case_attempt_denominator_not_whole_trial_rate() -> None:
    attempts = [
        _attempt("box", 1),
        _attempt("hole", 1, failed="dimensions"),
        _attempt("box", 2),
        _attempt("hole", 2),
    ]

    report = aggregate_case_attempts(
        attempts, created_at="2026-07-22T00:00:00Z"
    )

    assert report["case_attempt_count"] == 4
    assert report["passed_case_attempts"] == 3
    assert report["case_attempt_completion_rate"] == 0.75
    assert report["valid_completion_rate"] == 0.75
    assert report["trial_summary"]["fully_passed_round_rate"] == 0.5
    assert report["trial_summary"]["target_completion_metric"] is False


@pytest.mark.parametrize("live", [False, True])
def test_deterministic_and_live_rates_are_separate(live: bool) -> None:
    report = aggregate_case_attempts([_attempt("box", 1, live=live)])

    selected = "live_model" if live else "deterministic"
    other = "deterministic" if live else "live_model"
    assert report["completion_rates"][selected]["completion_rate"] == 1.0
    assert report["completion_rates"][other] is None
    assert report["target_completion_rate"] == (1.0 if live else None)


def test_incomplete_stage_evidence_fails_closed() -> None:
    record = _attempt("box", 1)
    del record["validation"]["constraints"]

    with pytest.raises(BenchmarkEvidenceError, match="each required stage"):
        aggregate_case_attempts([record])


def test_duplicate_case_attempt_fails_closed() -> None:
    record = _attempt("box", 1)

    with pytest.raises(BenchmarkEvidenceError, match="Duplicate"):
        aggregate_case_attempts([record, deepcopy(record)])


def test_attempt_case_sets_must_be_complete_and_consistent() -> None:
    with pytest.raises(BenchmarkEvidenceError, match="inconsistent cases"):
        aggregate_case_attempts(
            [_attempt("box", 1), _attempt("hole", 1), _attempt("box", 2)]
        )


def test_expected_suite_case_set_cannot_be_silently_truncated() -> None:
    with pytest.raises(BenchmarkEvidenceError, match="case set is incomplete"):
        aggregate_case_attempts(
            [_attempt("box", 1)],
            expected_case_ids=("box", "hole"),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("provider", "", "provider"),
        ("model", "", "model"),
        ("executor", "", "executor"),
        ("live_model_score", "false", "live_model_score"),
        ("question_count", -1, "question_count"),
        ("retry_count", -1, "retry_count"),
        ("elapsed_seconds", float("nan"), "elapsed_seconds"),
    ],
)
def test_identity_and_measurement_errors_fail_closed(
    field: str, value, message: str
) -> None:
    record = _attempt("box", 1)
    record[field] = value

    with pytest.raises(BenchmarkEvidenceError, match=message):
        aggregate_case_attempts([record])


def test_failed_case_requires_diagnostic_evidence() -> None:
    record = _attempt("box", 1, failed="geometry")
    record["failure_diagnostics"] = []

    with pytest.raises(BenchmarkEvidenceError, match="must contain an error"):
        aggregate_case_attempts([record])


def test_passed_case_cannot_hide_error_diagnostic() -> None:
    record = _attempt("box", 1)
    record["failure_diagnostics"] = [
        {
            "severity": "error",
            "stage": "execution",
            "code": "hidden_failure",
            "message": "A hidden failure occurred.",
            "details": {},
        }
    ]

    with pytest.raises(BenchmarkEvidenceError, match="passed case"):
        aggregate_case_attempts([record])


def test_normalized_usage_rejects_an_impossible_total() -> None:
    with pytest.raises(BenchmarkEvidenceError, match="cannot be less"):
        normalized_usage(input_tokens=8, output_tokens=5, total_tokens=12)


def test_series_rejects_mixed_provider_or_score_identity() -> None:
    other_provider = _attempt("hole", 1)
    other_provider["provider"] = "different"

    with pytest.raises(BenchmarkEvidenceError, match="cannot mix"):
        aggregate_case_attempts([_attempt("box", 1), other_provider])


def test_live_model_attempt_requires_human_instruction_rating() -> None:
    record = _attempt("box", 1)
    record["live_model_score"] = True

    with pytest.raises(BenchmarkEvidenceError, match="requires a human"):
        aggregate_case_attempts([record])


def test_instruction_adherence_field_is_required() -> None:
    record = _attempt("box", 1)
    del record["instruction_adherence"]

    with pytest.raises(BenchmarkEvidenceError, match="instruction_adherence"):
        aggregate_case_attempts([record])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("rating", 6, "outside its scale"),
        ("reviewer_id", "", "reviewer_id"),
        ("notes", "", "notes"),
    ],
)
def test_human_rating_requires_bounded_score_reviewer_and_notes(
    field: str, value, message: str
) -> None:
    record = _attempt("box", 1, live=True)
    record["instruction_adherence"][field] = value

    with pytest.raises(BenchmarkEvidenceError, match=message):
        aggregate_case_attempts([record])


def test_failed_gui_gate_retains_complete_case_evidence() -> None:
    failed = _attempt("box", 1, failed="geometry")

    report = finalize_series_report(
        [failed],
        [
            {
                "attempt": 1,
                "case_evidence_passed": False,
                "gui_runner_exit_code": 1,
                "gui_runner_reported_ok": False,
            }
        ],
        expected_case_ids=("box",),
    )

    assert report["case_attempts"] == [failed]
    assert report["failed_case_attempts"] == 1
    assert report["runner_gate_passed"] is False
    assert report["trial_runs"][0]["gui_runner_reported_ok"] is False
    assert series_exit_code(report) == 1


def test_passed_cases_still_fail_when_gui_gate_does_not_report_ok() -> None:
    report = finalize_series_report(
        [_attempt("box", 1)],
        [
            {
                "attempt": 1,
                "case_evidence_passed": True,
                "gui_runner_exit_code": 134,
                "gui_runner_reported_ok": False,
            }
        ],
        expected_case_ids=("box",),
    )

    assert report["passed_case_attempts"] == 1
    assert report["runner_gate_passed"] is False
    assert series_exit_code(report) == 1


def _module_tree(relative_path: str) -> ast.Module:
    root = Path(__file__).resolve().parents[4]
    return ast.parse(
        (root / relative_path).read_text(encoding="utf-8"),
        filename=relative_path,
    )


def test_tier2_tool_requires_all_ten_functional_part_cases() -> None:
    tree = _module_tree("tools/run_tier2_provider_benchmark.py")
    expected_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "EXPECTED_CASE_IDS"
            for target in node.targets
        )
    )

    assert ast.literal_eval(expected_assignment.value) == (
        "t2_wall_bracket",
        "t2_motor_adapter",
        "t2_battery_tray",
        "t2_camera_mount",
        "t2_pipe_clamp",
        "t2_ventilated_cover",
        "t2_electronics_enclosure_and_lid",
        "t2_flanged_coupling",
        "t2_simple_hinge",
        "t2_bolt_pattern_plate",
    )


def test_tier2_runner_contains_three_added_typed_providers() -> None:
    tree = _module_tree(
        "tests/benchmark/tier2_transactional_provider_runner.py"
    )
    provider_classes = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }

    assert {
        "FlangedCouplingProvider",
        "SimpleHingeProvider",
        "BoltPatternPlateProvider",
    } <= provider_classes
