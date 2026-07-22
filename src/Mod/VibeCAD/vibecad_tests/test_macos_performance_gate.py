# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[4]


def _load_tool():
    path = REPOSITORY / "tools/macos_performance_gate.py"
    spec = importlib.util.spec_from_file_location("macos_performance_gate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


performance = _load_tool()


def _verify_report(report_path: Path, application: Path, **kwargs):
    return performance.verify_report(
        report_path,
        application,
        expected_source_sha="a" * 40,
        **kwargs,
    )


def _valid_report(tmp_path: Path) -> tuple[Path, Path, dict]:
    application = tmp_path / "Applications/VibeCAD.app"
    application.mkdir(parents=True)
    budgets = performance.default_budget_input()
    metrics = {
        name: float(entry["maximum"]) / 2.0
        for name, entry in budgets["metrics"].items()
    }
    report = {
        "schema": performance.REPORT_SCHEMA,
        "version": performance.SCHEMA_VERSION,
        "probe_revision": performance.PROBE_REVISION,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "ok": True,
        "failures": [],
        "hardware": {
            "architecture": "arm64",
            "os_version": "macOS-15.5-arm64",
            "cpu_logical_count": 10,
            "physical_memory_bytes": 32 * 1024 * 1024 * 1024,
            "application_path": str(application),
            "application_version": "26.3.2",
            "runner_name": "test-runner",
            "ci": True,
            "source_sha": "a" * 40,
        },
        "environment": {
            "probe": "installed-vibecad-freecad-gui",
            "display_mode": "offscreen",
            "provider_mode": "deterministic-local-no-network",
            "provider_model": performance.DETERMINISTIC_PROVIDER_MODEL,
            "medium_object_count": performance.MEDIUM_OBJECT_COUNT,
            "large_object_count": performance.LARGE_OBJECT_COUNT,
            "cold_launch_definition": "first process with a fresh dedicated profile",
            "warm_launch_definition": "immediate second process with the same profile",
            "freecad_version": "26.3.2",
        },
        "budget_input": budgets,
        "metrics": metrics,
        "waivers": [],
    }
    report_path = tmp_path / "performance.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report_path, application, report


def _write_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report), encoding="utf-8")


def test_default_budget_input_is_versioned_and_has_exact_metric_set() -> None:
    budgets = performance.validate_budget_input(performance.default_budget_input())
    assert budgets["schema"] == "vibecad-macos-performance-budget-input-v1"
    assert budgets["version"] == 1
    assert tuple(budgets["metrics"]) == performance.REQUIRED_METRICS
    assert budgets["waivers"] == []
    assert all(entry["maximum"] > 0 for entry in budgets["metrics"].values())


def test_complete_report_passes_with_hardware_and_environment_metadata(
    tmp_path: Path,
) -> None:
    report_path, application, report = _valid_report(tmp_path)
    result = _verify_report(report_path, application)
    assert result["hardware"]["architecture"] == "arm64"
    assert result["environment"]["provider_mode"] == "deterministic-local-no-network"
    assert result["metrics"] == report["metrics"]


@pytest.mark.parametrize("missing", performance.REQUIRED_METRICS)
def test_report_fails_closed_when_one_metric_is_missing(
    tmp_path: Path,
    missing: str,
) -> None:
    report_path, application, report = _valid_report(tmp_path)
    del report["metrics"][missing]
    _write_report(report_path, report)
    with pytest.raises(ValueError, match="exact metric set"):
        _verify_report(report_path, application)


@pytest.mark.parametrize(
    "value", [None, "1", True, 0, -1, float("inf"), float("nan")]
)
def test_report_fails_closed_on_malformed_metric(tmp_path: Path, value) -> None:
    report_path, application, report = _valid_report(tmp_path)
    report["metrics"]["warm_launch_ms"] = value
    _write_report(report_path, report)
    with pytest.raises(ValueError, match="warm_launch_ms"):
        _verify_report(report_path, application)


@pytest.mark.parametrize("metric", performance.REQUIRED_METRICS)
def test_report_fails_closed_when_each_metric_exceeds_its_budget(
    tmp_path: Path,
    metric: str,
) -> None:
    report_path, application, report = _valid_report(tmp_path)
    maximum = float(report["budget_input"]["metrics"][metric]["maximum"])
    report["metrics"][metric] = maximum + 0.001
    report["ok"] = False
    report["failures"] = ["over budget"]
    _write_report(report_path, report)
    with pytest.raises(ValueError, match=metric):
        _verify_report(report_path, application)


@pytest.mark.parametrize(
    "field,value",
    [
        ("architecture", ""),
        ("cpu_logical_count", 0),
        ("physical_memory_bytes", 0),
        ("ci", "true"),
        ("source_sha", None),
    ],
)
def test_report_fails_closed_on_malformed_hardware_metadata(
    tmp_path: Path,
    field: str,
    value,
) -> None:
    report_path, application, report = _valid_report(tmp_path)
    report["hardware"][field] = value
    _write_report(report_path, report)
    with pytest.raises(ValueError, match="hardware|Hardware|field|CPU|memory|CI"):
        _verify_report(report_path, application)


def test_report_rejects_a_different_source_commit(tmp_path: Path) -> None:
    report_path, application, _report = _valid_report(tmp_path)
    with pytest.raises(ValueError, match="different source commit"):
        performance.verify_report(
            report_path,
            application,
            expected_source_sha="b" * 40,
        )


def test_measurement_binds_the_explicit_source_commit() -> None:
    source = (REPOSITORY / "tools/macos_performance_gate.py").read_text(
        encoding="utf-8"
    )
    assert "_hardware_metadata(application, source_sha)" in source
    assert "source_sha=args.source_sha" in source
    hardware_block = source[
        source.index("def _hardware_metadata"):source.index("def _rss_bytes")
    ]
    assert "VIBECAD_SOURCE_SHA" not in hardware_block


def test_report_requires_deterministic_local_provider_label(tmp_path: Path) -> None:
    report_path, application, report = _valid_report(tmp_path)
    report["environment"]["provider_mode"] = "live-provider"
    _write_report(report_path, report)
    with pytest.raises(ValueError, match="deterministic and local"):
        _verify_report(report_path, application)


def test_report_fails_closed_when_environment_metadata_is_missing(
    tmp_path: Path,
) -> None:
    report_path, application, report = _valid_report(tmp_path)
    del report["environment"]["freecad_version"]
    _write_report(report_path, report)
    with pytest.raises(ValueError, match="environment metadata"):
        _verify_report(report_path, application)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "wrong"),
        ("version", 2),
        ("probe_revision", "old-probe"),
    ],
)
def test_report_rejects_unknown_schema_version_or_probe(
    tmp_path: Path,
    field: str,
    value,
) -> None:
    report_path, application, report = _valid_report(tmp_path)
    report[field] = value
    _write_report(report_path, report)
    with pytest.raises(ValueError, match="schema|probe"):
        _verify_report(report_path, application)


def test_report_rejects_failure_flag_even_when_values_are_under_budget(
    tmp_path: Path,
) -> None:
    report_path, application, report = _valid_report(tmp_path)
    report["ok"] = False
    report["failures"] = ["probe failed"]
    _write_report(report_path, report)
    with pytest.raises(ValueError, match="passing result"):
        _verify_report(report_path, application)


def test_report_rejects_runtime_waiver(tmp_path: Path) -> None:
    report_path, application, report = _valid_report(tmp_path)
    report["waivers"] = [{"metric": "cold_launch_ms", "reason": "ignore"}]
    _write_report(report_path, report)
    with pytest.raises(ValueError, match="runtime waiver"):
        _verify_report(report_path, application)


def test_custom_versioned_budget_input_is_bound_to_report(tmp_path: Path) -> None:
    report_path, application, report = _valid_report(tmp_path)
    budgets = copy.deepcopy(report["budget_input"])
    budgets["metrics"]["cold_launch_ms"]["maximum"] = 40_000.0
    budget_path = tmp_path / "budgets.json"
    budget_path.write_text(json.dumps(budgets), encoding="utf-8")
    loaded = performance.load_budget_input(budget_path)
    report["budget_input"] = loaded
    _write_report(report_path, report)
    assert _verify_report(
        report_path,
        application,
        budget_input=loaded,
    )["ok"] is True
    with pytest.raises(ValueError, match="unexpected budget input"):
        _verify_report(report_path, application)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(schema="wrong"),
        lambda value: value["metrics"].pop("quit_ms"),
        lambda value: value["metrics"]["quit_ms"].update(unit="seconds"),
        lambda value: value.update(waivers=[{"metric": "quit_ms"}]),
    ],
)
def test_budget_input_fails_closed_on_schema_metric_unit_or_waiver(
    mutation,
) -> None:
    budgets = performance.default_budget_input()
    mutation(budgets)
    with pytest.raises(ValueError):
        performance.validate_budget_input(budgets)


def test_probe_source_uses_real_installed_gui_and_local_deterministic_paths() -> None:
    source = (REPOSITORY / "tools/macos_performance_gate.py").read_text(
        encoding="utf-8"
    )
    assert 'application / "Contents/MacOS/FreeCAD"' in source
    assert '"--hidden"' in source
    assert "VibeCADSession.run_prompt" in source
    assert "VibeCADAcceptanceCoordinator" in source
    assert "validate_saved_document" in source
    assert '"deterministic-local-no-network"' in source
    assert "OpenAI" not in source
    assert "Anthropic" not in source


def test_workflow_runs_performance_gate_after_installed_package_smoke() -> None:
    source = (REPOSITORY / ".github/workflows/vibecad-macos.yml").read_text(
        encoding="utf-8"
    )
    package = source.index("- name: Install and test generated macOS package")
    performance_gate = source.index("- name: Measure installed macOS performance")
    cleanup = source.index("- name: Remove clean-machine test installation")
    assert package < performance_gate < cleanup
    block = source[performance_gate:cleanup]
    assert "tools/macos_performance_gate.py" in block
    assert 'application="/Applications/VibeCAD.app"' in block
    assert 'VIBECAD_SOURCE_SHA: ${{ needs.prepare.outputs.source_sha }}' in block
    assert "MACOS_NOTARY" not in block
    assert "MACOS_SIGNING" not in block
    assert "OPENAI" not in block
    assert "ANTHROPIC" not in block
