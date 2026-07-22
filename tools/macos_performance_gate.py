#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Measure and verify installed VibeCAD macOS performance budgets."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import plistlib
import re
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Mapping


REPORT_SCHEMA = "vibecad-macos-performance-report-v1"
BUDGET_SCHEMA = "vibecad-macos-performance-budget-input-v1"
SCHEMA_VERSION = 1
PROBE_REVISION = "vibecad-macos-performance-probe-v1"
DETERMINISTIC_PROVIDER_MODEL = "macos-performance-deterministic-local-v1"
MEDIUM_OBJECT_COUNT = 64
LARGE_OBJECT_COUNT = 256

DEFAULT_BUDGETS: dict[str, dict[str, Any]] = {
    "cold_launch_ms": {"maximum": 30_000.0, "unit": "ms"},
    "warm_launch_ms": {"maximum": 15_000.0, "unit": "ms"},
    "medium_document_open_ms": {"maximum": 5_000.0, "unit": "ms"},
    "large_document_open_ms": {"maximum": 15_000.0, "unit": "ms"},
    "first_deterministic_ai_response_ms": {"maximum": 30_000.0, "unit": "ms"},
    "viewport_interaction_ms": {"maximum": 2_000.0, "unit": "ms"},
    "revision_apply_ms": {"maximum": 10_000.0, "unit": "ms"},
    "peak_memory_mib": {"maximum": 4_096.0, "unit": "MiB"},
    "worker_cleanup_ms": {"maximum": 3_000.0, "unit": "ms"},
    "quit_ms": {"maximum": 5_000.0, "unit": "ms"},
}
REQUIRED_METRICS = tuple(DEFAULT_BUDGETS)
REQUIRED_HARDWARE_FIELDS = {
    "architecture",
    "os_version",
    "cpu_logical_count",
    "physical_memory_bytes",
    "application_path",
    "application_version",
    "runner_name",
    "ci",
    "source_sha",
}
REQUIRED_ENVIRONMENT_FIELDS = {
    "probe",
    "display_mode",
    "provider_mode",
    "provider_model",
    "medium_object_count",
    "large_object_count",
    "cold_launch_definition",
    "warm_launch_definition",
    "freecad_version",
}


def default_budget_input() -> dict[str, Any]:
    return {
        "schema": BUDGET_SCHEMA,
        "version": SCHEMA_VERSION,
        "metrics": json.loads(json.dumps(DEFAULT_BUDGETS)),
        "waivers": [],
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _finite_number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number.")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result <= 0):
        qualifier = "positive " if positive else "nonnegative "
        raise ValueError(f"{label} must be a finite {qualifier}number.")
    return result


def validate_budget_input(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("The performance budget input is not a JSON object.")
    if raw.get("schema") != BUDGET_SCHEMA or raw.get("version") != SCHEMA_VERSION:
        raise ValueError("The performance budget input has an unsupported schema.")
    metrics = raw.get("metrics")
    if not isinstance(metrics, dict) or set(metrics) != set(REQUIRED_METRICS):
        raise ValueError("The performance budget input must contain the exact metric set.")
    normalized: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_METRICS:
        entry = metrics.get(name)
        if not isinstance(entry, dict) or set(entry) != {"maximum", "unit"}:
            raise ValueError(f"Performance budget {name} is malformed.")
        if entry.get("unit") != DEFAULT_BUDGETS[name]["unit"]:
            raise ValueError(f"Performance budget {name} has an invalid unit.")
        normalized[name] = {
            "maximum": _finite_number(
                entry.get("maximum"), f"Performance budget {name}", positive=True
            ),
            "unit": str(entry["unit"]),
        }
    waivers = raw.get("waivers")
    if waivers != []:
        raise ValueError(
            "Runtime performance waivers are not allowed. Change the reviewed budget input instead."
        )
    return {
        "schema": BUDGET_SCHEMA,
        "version": SCHEMA_VERSION,
        "metrics": normalized,
        "waivers": [],
    }


def load_budget_input(path: Path | None) -> dict[str, Any]:
    if path is None:
        return validate_budget_input(default_budget_input())
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"The performance budget input could not be read: {path}: {exc}") from exc
    return validate_budget_input(raw)


def _validate_hardware(
    raw: Any, expected_application: Path, expected_source_sha: str
) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != REQUIRED_HARDWARE_FIELDS:
        raise ValueError("The performance report has incomplete hardware metadata.")
    for field in (
        "architecture",
        "os_version",
        "application_version",
        "runner_name",
        "source_sha",
    ):
        if not isinstance(raw.get(field), str) or not raw[field].strip():
            raise ValueError(f"Performance hardware field {field} is invalid.")
    application = Path(str(raw.get("application_path") or ""))
    if application.resolve() != expected_application.resolve():
        raise ValueError("The performance report used a different application.")
    if (
        isinstance(raw.get("cpu_logical_count"), bool)
        or not isinstance(raw.get("cpu_logical_count"), int)
        or raw["cpu_logical_count"] <= 0
    ):
        raise ValueError("Performance hardware CPU count is invalid.")
    if (
        isinstance(raw.get("physical_memory_bytes"), bool)
        or not isinstance(raw.get("physical_memory_bytes"), int)
        or raw["physical_memory_bytes"] <= 0
    ):
        raise ValueError("Performance hardware memory size is invalid.")
    if not isinstance(raw.get("ci"), bool):
        raise ValueError("Performance hardware CI metadata is invalid.")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_source_sha):
        raise ValueError("The expected performance source commit is invalid.")
    if raw.get("source_sha") != expected_source_sha:
        raise ValueError("The performance report used a different source commit.")
    return dict(raw)


def verify_report(
    report_path: Path,
    expected_application: Path,
    *,
    expected_source_sha: str,
    budget_input: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed unless a complete installed-app report is within budget."""
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"The performance report could not be read: {report_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("The performance report is not a JSON object.")
    if payload.get("schema") != REPORT_SCHEMA or payload.get("version") != SCHEMA_VERSION:
        raise ValueError("The performance report has an unsupported schema.")
    if payload.get("probe_revision") != PROBE_REVISION:
        raise ValueError("The performance report has an unsupported probe revision.")
    timestamp = payload.get("timestamp_utc")
    try:
        parsed_timestamp = datetime.fromisoformat(str(timestamp))
    except ValueError as exc:
        raise ValueError("The performance report has no valid UTC timestamp.") from exc
    if parsed_timestamp.utcoffset() != timezone.utc.utcoffset(parsed_timestamp):
        raise ValueError("The performance report has no UTC timestamp.")
    _validate_hardware(
        payload.get("hardware"), expected_application, expected_source_sha
    )
    environment = payload.get("environment")
    if not isinstance(environment, dict) or set(environment) != REQUIRED_ENVIRONMENT_FIELDS:
        raise ValueError("The performance report has incomplete environment metadata.")
    for field in (
        "display_mode",
        "cold_launch_definition",
        "warm_launch_definition",
        "freecad_version",
    ):
        if not isinstance(environment.get(field), str) or not environment[field].strip():
            raise ValueError(f"Performance environment field {field} is invalid.")
    if environment.get("probe") != "installed-vibecad-freecad-gui":
        raise ValueError("The performance report did not use the installed GUI probe.")
    if environment.get("provider_mode") != "deterministic-local-no-network":
        raise ValueError("The AI latency metric is not marked as deterministic and local.")
    if environment.get("provider_model") != DETERMINISTIC_PROVIDER_MODEL:
        raise ValueError("The deterministic provider identity is invalid.")
    if int(environment.get("medium_object_count") or 0) != MEDIUM_OBJECT_COUNT:
        raise ValueError("The medium document fixture identity is invalid.")
    if int(environment.get("large_object_count") or 0) != LARGE_OBJECT_COUNT:
        raise ValueError("The large document fixture identity is invalid.")

    expected_budgets = validate_budget_input(
        dict(budget_input) if budget_input is not None else default_budget_input()
    )
    embedded_budgets = validate_budget_input(payload.get("budget_input"))
    if embedded_budgets != expected_budgets:
        raise ValueError("The performance report used an unexpected budget input.")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict) or set(metrics) != set(REQUIRED_METRICS):
        raise ValueError("The performance report must contain the exact metric set.")
    normalized_metrics: dict[str, float] = {}
    failures: list[str] = []
    for name in REQUIRED_METRICS:
        value = _finite_number(
            metrics.get(name), f"Performance metric {name}", positive=True
        )
        maximum = float(expected_budgets["metrics"][name]["maximum"])
        normalized_metrics[name] = value
        if value > maximum:
            unit = expected_budgets["metrics"][name]["unit"]
            failures.append(f"{name}={value:.3f} {unit} exceeds {maximum:.3f} {unit}")
    if payload.get("waivers") != []:
        raise ValueError("The performance report contains an unapproved runtime waiver.")
    if failures:
        raise ValueError("Performance budget failed: " + "; ".join(failures))
    if payload.get("ok") is not True or payload.get("failures") != []:
        raise ValueError("The performance report does not record a passing result.")
    result = dict(payload)
    result["metrics"] = normalized_metrics
    return result


def _physical_memory_bytes() -> int:
    try:
        completed = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "hw.memsize"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
            check=False,
        )
        value = int(completed.stdout.strip())
        if completed.returncode == 0 and value > 0:
            return value
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    page_size = int(os.sysconf("SC_PAGE_SIZE"))
    pages = int(os.sysconf("SC_PHYS_PAGES"))
    return page_size * pages


def _application_version(application: Path) -> str:
    path = application / "Contents/Info.plist"
    try:
        value = plistlib.loads(path.read_bytes()).get("CFBundleShortVersionString")
    except (OSError, plistlib.InvalidFileException) as exc:
        raise RuntimeError(f"The installed application version could not be read: {path}") from exc
    text = str(value or "").strip()
    if not text:
        raise RuntimeError("The installed application has no release version.")
    return text


def _hardware_metadata(application: Path, source_sha: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", str(source_sha or "")):
        raise ValueError("The performance source commit is invalid.")
    return {
        "architecture": platform.machine() or "unknown",
        "os_version": platform.platform(),
        "cpu_logical_count": int(os.cpu_count() or 1),
        "physical_memory_bytes": _physical_memory_bytes(),
        "application_path": str(application.resolve()),
        "application_version": _application_version(application),
        "runner_name": str(os.environ.get("RUNNER_NAME") or "local"),
        "ci": str(os.environ.get("CI") or "").lower() == "true",
        "source_sha": source_sha,
    }


def _rss_bytes(pid: int) -> int | None:
    try:
        completed = subprocess.run(
            ["/bin/ps", "-o", "rss=", "-p", str(int(pid))],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return int(completed.stdout.strip()) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def _read_output_tail(stream: Any, *, limit: int = 32_000) -> str:
    stream.flush()
    stream.seek(0, os.SEEK_END)
    size = int(stream.tell())
    stream.seek(max(0, size - limit), os.SEEK_SET)
    return stream.read().decode("utf-8", errors="replace")[-limit:]


def _run_application(
    executable: Path,
    script: Path,
    root: Path,
    action: str,
    output_path: Path,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    environment = dict(os.environ)
    environment.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "VIBECAD_HOME": str(root / "vibecad-home"),
            "VIBECAD_PERFORMANCE_ACTION": action,
            "VIBECAD_PERFORMANCE_OUTPUT": str(output_path),
            "VIBECAD_INSTALLED_APP_PATH": str(executable.parents[2]),
        }
    )
    command = [
        str(executable),
        "--hidden",
        "--user-cfg",
        str(root / "user.cfg"),
        "--system-cfg",
        str(root / "system.cfg"),
        str(script),
    ]
    started_ns = time.monotonic_ns()
    peak_rss = 0
    with tempfile.TemporaryFile(mode="w+b") as stdout_stream, tempfile.TemporaryFile(
        mode="w+b"
    ) as stderr_stream:
        process = subprocess.Popen(
            command,
            cwd=str(root),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout_stream,
            stderr=stderr_stream,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None:
            usage = _rss_bytes(process.pid)
            if usage is not None:
                peak_rss = max(peak_rss, usage)
            if time.monotonic() >= deadline:
                process.terminate()
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5.0)
                raise RuntimeError(f"Installed performance probe timed out during {action}.")
            time.sleep(0.025)
        ended_ns = time.monotonic_ns()
        stdout = _read_output_tail(stdout_stream)
        stderr = _read_output_tail(stderr_stream)
    if process.returncode != 0:
        raise RuntimeError(
            f"Installed performance probe failed during {action} with code "
            f"{process.returncode}. stdout={stdout!r} stderr={stderr!r}"
        )
    if not output_path.is_file():
        raise RuntimeError(f"Installed performance probe produced no {action} report.")
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Installed performance probe produced invalid {action} JSON.") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(
            f"Installed performance probe failed during {action}: "
            f"{payload.get('error') if isinstance(payload, dict) else payload}"
        )
    return {
        "payload": payload,
        "started_ns": started_ns,
        "ended_ns": ended_ns,
        "peak_rss_bytes": peak_rss,
    }


def measure_installed_application(
    application: Path,
    root: Path,
    report_path: Path,
    *,
    source_sha: str,
    budget_input: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run fresh-profile, warm-profile, and real installed GUI probes."""
    application = application.resolve()
    root = root.resolve()
    report_path = report_path.resolve()
    executable = application / "Contents/MacOS/FreeCAD"
    if sys.platform != "darwin":
        raise RuntimeError("The installed macOS performance probe requires macOS.")
    if not executable.is_file():
        raise RuntimeError(f"The installed VibeCAD executable is missing: {executable}")
    root.mkdir(parents=True, exist_ok=False)
    budgets = validate_budget_input(
        dict(budget_input) if budget_input is not None else default_budget_input()
    )
    script = Path(__file__).resolve()

    cold_path = root / "cold-launch.json"
    cold = _run_application(
        executable, script, root, "launch", cold_path, timeout_seconds=60.0
    )
    cold_reached_ns = int(cold["payload"].get("reached_monotonic_ns") or 0)
    cold_ms = (cold_reached_ns - int(cold["started_ns"])) / 1_000_000.0

    warm_path = root / "warm-launch.json"
    warm = _run_application(
        executable, script, root, "launch", warm_path, timeout_seconds=60.0
    )
    warm_reached_ns = int(warm["payload"].get("reached_monotonic_ns") or 0)
    warm_ms = (warm_reached_ns - int(warm["started_ns"])) / 1_000_000.0

    workload_path = root / "workload.json"
    workload = _run_application(
        executable, script, root, "workload", workload_path, timeout_seconds=240.0
    )
    inner = dict(workload["payload"])
    quit_started_ns = int(inner.get("quit_started_monotonic_ns") or 0)
    metrics = dict(inner.get("metrics") or {})
    metrics.update(
        {
            "cold_launch_ms": cold_ms,
            "warm_launch_ms": warm_ms,
            "peak_memory_mib": float(workload["peak_rss_bytes"]) / (1024.0 * 1024.0),
            "quit_ms": (int(workload["ended_ns"]) - quit_started_ns) / 1_000_000.0,
        }
    )
    failures: list[str] = []
    for name in REQUIRED_METRICS:
        try:
            value = _finite_number(
                metrics.get(name), f"Performance metric {name}", positive=True
            )
        except ValueError as exc:
            failures.append(str(exc))
            continue
        maximum = float(budgets["metrics"][name]["maximum"])
        if value > maximum:
            unit = budgets["metrics"][name]["unit"]
            failures.append(
                f"{name}={value:.3f} {unit} exceeds {maximum:.3f} {unit}"
            )
    report = {
        "schema": REPORT_SCHEMA,
        "version": SCHEMA_VERSION,
        "probe_revision": PROBE_REVISION,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "ok": not failures,
        "failures": failures,
        "hardware": _hardware_metadata(application, source_sha),
        "environment": inner.get("environment"),
        "budget_input": budgets,
        "metrics": metrics,
        "waivers": [],
    }
    _atomic_json(report_path, report)
    return report


def _make_fixture(root: Path, name: str, count: int) -> Path:
    import FreeCAD as App
    import Part

    path = root / f"{name}.FCStd"
    document = App.newDocument(name)
    base = Part.makeBox(10.0, 10.0, 10.0)
    for index in range(count):
        feature = document.addObject("Part::Feature", f"Feature{index:04d}")
        feature.Label = f"Performance feature {index + 1}"
        feature.Shape = base
        feature.Placement.Base = App.Vector(
            float((index % 16) * 12),
            float(((index // 16) % 16) * 12),
            float((index // 256) * 12),
        )
    document.recompute()
    document.saveAs(str(path))
    App.closeDocument(document.Name)
    return path


def _measure_document_open(path: Path, expected_count: int) -> tuple[float, Any]:
    import FreeCAD as App
    import FreeCADGui as Gui

    started = time.perf_counter()
    document = App.openDocument(str(path))
    App.setActiveDocument(document.Name)
    document.recompute()
    Gui.updateGui()
    elapsed_ms = (time.perf_counter() - started) * 1_000.0
    if len(document.Objects) != expected_count:
        raise RuntimeError(
            f"The {path.name} fixture reopened {len(document.Objects)} objects, not {expected_count}."
        )
    return elapsed_ms, document


def _measure_viewport(document: Any) -> float:
    import FreeCAD as App
    import FreeCADGui as Gui

    App.setActiveDocument(document.Name)
    view = Gui.activeDocument().activeView()
    operations = (view.viewTop, view.viewFront, view.viewRight, view.viewAxonometric)
    samples: list[float] = []
    for _ in range(3):
        for operation in operations:
            started = time.perf_counter()
            operation()
            view.fitAll()
            Gui.updateGui()
            samples.append((time.perf_counter() - started) * 1_000.0)
    if not samples:
        raise RuntimeError("The viewport probe produced no interaction samples.")
    return max(samples)


def _measure_deterministic_response(root: Path) -> tuple[float, Any, Path]:
    import FreeCAD as App

    from VibeCADCore import VibeCADService
    from VibeCADProvider import BaseProvider, ProviderResult
    import VibeCADSession

    class DeterministicLocalProvider(BaseProvider):
        model = DETERMINISTIC_PROVIDER_MODEL

        def run(
            self,
            prompt,
            context,
            tool_runner=None,
            cancellation_check=None,
            progress_callback=None,
        ):
            del prompt, context, tool_runner, cancellation_check, progress_callback
            return ProviderResult("Deterministic local performance response.")

    document_path = root / "deterministic-response.FCStd"
    document = App.newDocument("VibeCADPerformanceResponse")
    document.saveAs(str(document_path))
    App.setActiveDocument(document.Name)
    service = VibeCADService()
    started = time.perf_counter()
    response = VibeCADSession.run_prompt(
        "Return one deterministic local performance response.",
        service=service,
        prefer_online=False,
        provider=DeterministicLocalProvider(),
    )
    elapsed_ms = (time.perf_counter() - started) * 1_000.0
    if response.error or response.final_output != "Deterministic local performance response.":
        raise RuntimeError(f"The deterministic local response failed: {response.error}")
    return elapsed_ms, service, document_path


def _measure_revision_apply(service: Any, document_path: Path) -> float:
    import FreeCAD as App
    import Part

    from VibeCADAcceptance import VibeCADAcceptanceCoordinator
    from VibeCADDocumentValidator import validate_saved_document
    from VibeCADProject import VibeCADProjectStore, now_iso
    from VibeCADRevision import create_revision_record

    document = service._active_document()
    if document is None:
        raise RuntimeError("The performance revision document is not active.")
    scope = service.project_scope_snapshot()
    coordinator = VibeCADAcceptanceCoordinator(scope["root"], str(scope["project_id"]))

    def save_copy(path: Path) -> None:
        from VibeCADSaveBoundary import internal_document_save

        canonical_name = str(document.FileName or "")
        with internal_document_save():
            document.saveCopy(str(path))
        if canonical_name and str(document.FileName or "") != canonical_name:
            document.FileName = canonical_name

    def restore_live(_path: Path) -> None:
        document.restore()
        document.recompute()

    def write_metadata(revision_id: str | None) -> None:
        VibeCADProjectStore.write_accepted_revision_metadata(
            scope["manifest_path"], str(scope["project_id"]), revision_id
        )

    prepared = coordinator.prepare(document_path, save_copy)
    feature = document.addObject("Part::Feature", "PerformanceCandidate")
    feature.Shape = Part.makeBox(24.0, 16.0, 8.0)
    document.recompute()

    def record(validation: Mapping[str, Any]) -> dict[str, Any]:
        return create_revision_record(
            project_id=str(scope["project_id"]),
            parent_revision=prepared.prior_head,
            user_request="Apply one deterministic performance revision.",
            interpreted_intent="Create one 24 by 16 by 8 mm native box.",
            assumptions=[],
            plan=[{"tool": "performance.native_box"}],
            tool_operations=[{"tool": "performance.native_box", "ok": True}],
            changed_objects=[{"name": feature.Name, "change": "created"}],
            validation_results=[dict(validation)],
            provider="deterministic-local-performance",
            model=DETERMINISTIC_PROVIDER_MODEL,
            timestamp=now_iso(),
            generated_source=None,
            preview_image=None,
            rollback={"available": True},
            transaction_id=prepared.acceptance_id,
            document_revision="performance-candidate",
            design_brief_revision=None,
        )

    coordinator.validate_candidate(
        prepared,
        record,
        save_copy=save_copy,
        validate_document=validate_saved_document,
        restore_live=restore_live,
        write_metadata=write_metadata,
    )
    started = time.perf_counter()
    result = coordinator.accept_validated_candidate(
        prepared,
        restore_live=restore_live,
        write_metadata=write_metadata,
        acceptance_mode="automatic",
    )
    elapsed_ms = (time.perf_counter() - started) * 1_000.0
    if result.get("ok") is not True or coordinator.revisions.head() is None:
        raise RuntimeError("The deterministic performance revision was not accepted.")
    return elapsed_ms


def _measure_worker_cleanup(root: Path) -> float:
    from VibeCADScriptedProcess import run_process

    started = time.perf_counter()
    result = run_process(
        ["/usr/bin/python3", "-c", "pass"],
        cwd=root,
        environment=dict(os.environ),
        cancellation_check=None,
        timeout_seconds=5.0,
        memory_limit_bytes=256 * 1024 * 1024,
    )
    elapsed_ms = (time.perf_counter() - started) * 1_000.0
    if (
        result.get("started") is not True
        or result.get("returncode") != 0
        or result.get("timed_out") is not False
    ):
        raise RuntimeError(f"The isolated worker did not exit cleanly: {result}")
    return elapsed_ms


def _schedule_application_quit() -> None:
    from PySide import QtCore, QtWidgets

    application = QtWidgets.QApplication.instance()
    if application is not None:
        QtCore.QTimer.singleShot(0, application.quit)


def _freecad_launch_entry(output: Path) -> None:
    import FreeCAD as App

    payload = {
        "schema": REPORT_SCHEMA,
        "version": SCHEMA_VERSION,
        "ok": True,
        "reached_monotonic_ns": time.monotonic_ns(),
        "freecad_version": ".".join(str(value) for value in App.Version()[:3]),
    }
    _atomic_json(output, payload)
    _schedule_application_quit()


def _freecad_workload_entry(output: Path) -> None:
    import FreeCAD as App

    root = output.parent
    try:
        medium_path = _make_fixture(root, "PerformanceMedium", MEDIUM_OBJECT_COUNT)
        large_path = _make_fixture(root, "PerformanceLarge", LARGE_OBJECT_COUNT)
        medium_ms, medium_document = _measure_document_open(
            medium_path, MEDIUM_OBJECT_COUNT
        )
        App.closeDocument(medium_document.Name)
        large_ms, large_document = _measure_document_open(
            large_path, LARGE_OBJECT_COUNT
        )
        viewport_ms = _measure_viewport(large_document)
        App.closeDocument(large_document.Name)
        response_ms, service, document_path = _measure_deterministic_response(root)
        revision_ms = _measure_revision_apply(service, document_path)
        worker_ms = _measure_worker_cleanup(root)
        active = service._active_document()
        if active is not None:
            App.closeDocument(active.Name)
        payload = {
            "schema": REPORT_SCHEMA,
            "version": SCHEMA_VERSION,
            "ok": True,
            "environment": {
                "probe": "installed-vibecad-freecad-gui",
                "display_mode": str(os.environ.get("QT_QPA_PLATFORM") or "native"),
                "provider_mode": "deterministic-local-no-network",
                "provider_model": DETERMINISTIC_PROVIDER_MODEL,
                "medium_object_count": MEDIUM_OBJECT_COUNT,
                "large_object_count": LARGE_OBJECT_COUNT,
                "cold_launch_definition": "first process with a fresh dedicated profile",
                "warm_launch_definition": "immediate second process with the same profile",
                "freecad_version": ".".join(str(value) for value in App.Version()[:3]),
            },
            "metrics": {
                "medium_document_open_ms": medium_ms,
                "large_document_open_ms": large_ms,
                "first_deterministic_ai_response_ms": response_ms,
                "viewport_interaction_ms": viewport_ms,
                "revision_apply_ms": revision_ms,
                "worker_cleanup_ms": worker_ms,
            },
            "quit_started_monotonic_ns": time.monotonic_ns(),
        }
    except BaseException as exc:
        payload = {
            "schema": REPORT_SCHEMA,
            "version": SCHEMA_VERSION,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
            "quit_started_monotonic_ns": time.monotonic_ns(),
        }
    _atomic_json(output, payload)
    _schedule_application_quit()


def _freecad_entry() -> None:
    action = str(os.environ.get("VIBECAD_PERFORMANCE_ACTION") or "")
    output = Path(os.environ["VIBECAD_PERFORMANCE_OUTPUT"]).resolve()
    if action == "launch":
        _freecad_launch_entry(output)
    elif action == "workload":
        _freecad_workload_entry(output)
    else:
        raise RuntimeError(f"Unsupported performance probe action: {action}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--measure", action="store_true")
    mode.add_argument("--verify-report", type=Path)
    parser.add_argument(
        "--application", type=Path, default=Path("/Applications/VibeCAD.app")
    )
    parser.add_argument("--root", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--budget-input", type=Path)
    parser.add_argument(
        "--source-sha", default=os.environ.get("VIBECAD_SOURCE_SHA", "")
    )
    args = parser.parse_args(argv)
    try:
        budgets = load_budget_input(args.budget_input)
        if args.measure:
            if args.root is None or args.report is None:
                raise ValueError("--measure requires --root and --report.")
            measure_installed_application(
                args.application,
                args.root,
                args.report,
                source_sha=args.source_sha,
                budget_input=budgets,
            )
            payload = verify_report(
                args.report,
                args.application,
                expected_source_sha=args.source_sha,
                budget_input=budgets,
            )
        else:
            payload = verify_report(
                args.verify_report,
                args.application,
                expected_source_sha=args.source_sha,
                budget_input=budgets,
            )
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        "Installed VibeCAD macOS performance gate passed: "
        + ", ".join(
            f"{name}={float(payload['metrics'][name]):.1f}"
            for name in REQUIRED_METRICS
        )
    )
    return 0


if os.environ.get("VIBECAD_PERFORMANCE_ACTION"):
    _freecad_entry()
elif __name__ == "__main__":
    raise SystemExit(main())
