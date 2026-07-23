# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run all Tier 1 prompts through the configured VibeCAD provider and session."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import sys
import time
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui

from VibeCADBenchmark import (
    failure_diagnostics,
    make_case_attempt,
    normalized_usage,
    unrated_instruction_adherence,
    validate_case_attempt,
    validation_stage,
)
from VibeCADCore import VibeCADService
from VibeCADLiveBenchmark import (
    LIVE_CASE_TOTAL_TIMEOUT_SECONDS,
    LIVE_EXECUTOR,
    LIVE_LIMITS,
    LIVE_MAX_PROGRESS_EVENT_BYTES,
    LIVE_MAX_PROGRESS_EVENTS_TOTAL_BYTES,
    LIVE_MAX_PROGRESS_INTEGRITY_ERRORS,
    LIVE_MAX_QUESTIONS_PER_CASE,
    LIVE_MAX_RETAINED_PROGRESS_EVENTS,
    LIVE_MAX_OUTPUT_TOKENS_PER_REQUEST,
    LIVE_MAX_REQUEST_BYTES,
    LIVE_PARTIAL_METRICS_SCHEMA,
    LIVE_PARTIAL_METRICS_VERSION,
    LIVE_PROVIDER_TIMEOUT_SECONDS,
    LIVE_PROVIDER_TURNS_PER_CASE,
    LIVE_RUN_SCHEMA,
    LIVE_RUN_VERSION,
    LIVE_SDK_RETRIES_PER_REQUEST,
    LIVE_TOOL_CALLS_PER_CASE,
    LIVE_TOTAL_TOKENS_PER_CASE,
    LIVE_VISIBLE_RETRY_EVENTS_PER_CASE,
    LIVE_WORST_CASE_API_ATTEMPTS,
    TIER1_CASE_IDS,
    atomic_write_json,
    all_edge_fillet_evidence,
    centered_hole_evidence,
    changed_constraint_evidence,
    empty_partial_case_metrics,
    exact_volume_evidence,
    live_run_unscorable_reasons,
    mirrored_link_evidence,
    open_top_aperture_evidence,
    persist_partial_metrics_checkpoint,
    readiness_digest,
    runtime_identity_digest,
    recover_partial_case_metrics,
    require_complete_partial_usage,
    sha256_file,
    stl_export_evidence,
    symmetric_through_holes_evidence,
    validate_live_readiness,
    validate_partial_metrics_checkpoint,
    validate_runtime_identity,
    validate_unrated_live_run,
    visible_solid_target_evidence,
)
from VibeCADProvider import (
    OPENAI_SDK_MAX_RETRIES,
    ChatGPTSubscriptionProvider,
    OfflineProvider,
    OpenAIProvider,
)
from VibeCADSession import choose_provider, run_prompt
from VibeCADDocumentValidator import validate_saved_document
from tools.probe_provider_readiness import readiness_execution_identity_matches
from tools.vibecad_benchmark_evidence_io import load_bounded_json


MAX_PROVIDER_TURNS = LIVE_PROVIDER_TURNS_PER_CASE
MAX_RETRY_EVENTS = LIVE_VISIBLE_RETRY_EVENTS_PER_CASE
MAX_TOOL_CALLS = LIVE_TOOL_CALLS_PER_CASE
PROVIDER_TIMEOUT_SECONDS = LIVE_PROVIDER_TIMEOUT_SECONDS
CASE_TOTAL_TIMEOUT_SECONDS = LIVE_CASE_TOTAL_TIMEOUT_SECONDS
MAX_TOTAL_TOKENS_PER_CASE = LIVE_TOTAL_TOKENS_PER_CASE
WORST_CASE_API_ATTEMPTS = LIVE_WORST_CASE_API_ATTEMPTS
LIMITS = dict(LIVE_LIMITS)
PROVIDER_CLASSES = {
    "chatgpt": ChatGPTSubscriptionProvider,
    "openai": OpenAIProvider,
}
FOLLOW_UP_CASES = {
    "t1_change_dimension",
    "t1_mirror_feature",
    "t1_export_stl",
}


class Measurements:
    """Collect bounded run metrics without adding a second provider path."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.events: list[dict[str, Any]] = []
        self.question_count = 0
        self.unnecessary_question_count = 0
        self.retry_count = 0
        self.tool_count = 0
        self.api_request_count = 0
        self.provider_turns: set[int] = set()
        self.usage_event_count = 0
        self.retained_event_bytes = 0
        self.dropped_event_count = 0
        self.usage_integrity_errors: list[str] = []
        self._request_turns: set[int] = set()
        self._usage_turns: set[int] = set()
        self._usage_mode: str | None = None
        self._incremental_usage = {name: 0 for name in (
            "input_tokens", "output_tokens", "cached_input_tokens",
            "reasoning_tokens", "total_tokens",
        )}
        self._cumulative_usage = dict(self._incremental_usage)
        self.limit_error: str | None = None

    def _fail(self, message: str, *, usage_integrity: bool = False) -> None:
        if self.limit_error is None:
            self.limit_error = message[:512]
        if (
            usage_integrity
            and message not in self.usage_integrity_errors
            and len(self.usage_integrity_errors)
            < LIVE_MAX_PROGRESS_INTEGRITY_ERRORS
        ):
            self.usage_integrity_errors.append(message[:512])

    def _retain_event(self, event: dict[str, Any]) -> None:
        name = str(event.get("event") or "invalid_progress_event")
        try:
            item = json.loads(
                json.dumps(
                    event,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                    allow_nan=False,
                )
            )
        except (TypeError, ValueError):
            item = {"event": name, "invalid_json": True}
            self.dropped_event_count += 1
            self._fail("A provider progress event was not finite JSON.")
        if name in {"provider_text_delta", "provider_reasoning_delta"}:
            text = str(event.get("text") or event.get("delta") or "")
            item.pop("text", None)
            item.pop("delta", None)
            item["text_length"] = len(text)
            item["text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        encoded = json.dumps(
            item,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > LIVE_MAX_PROGRESS_EVENT_BYTES:
            item = {
                "event": name,
                "oversized_event_bytes": len(encoded),
                "oversized_event_sha256": hashlib.sha256(encoded).hexdigest(),
            }
            encoded = json.dumps(
                item, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            self.dropped_event_count += 1
            self._fail("A provider progress event exceeded its byte limit.")
        if len(self.events) >= LIVE_MAX_RETAINED_PROGRESS_EVENTS:
            self.dropped_event_count += 1
            self._fail("The provider exceeded the retained progress-event limit.")
            return
        if (
            self.retained_event_bytes + len(encoded)
            > LIVE_MAX_PROGRESS_EVENTS_TOTAL_BYTES
        ):
            self.dropped_event_count += 1
            self._fail("The provider exceeded the progress-event byte limit.")
            return
        self.events.append(item)
        self.retained_event_bytes += len(encoded)

    def _record_request(self, turn: Any, item: dict[str, Any]) -> None:
        if (
            isinstance(turn, bool)
            or not isinstance(turn, int)
            or turn <= 0
            or turn != len(self._request_turns) + 1
            or turn in self._request_turns
        ):
            self._fail(
                "Provider request turns are duplicate, missing, or reordered.",
                usage_integrity=True,
            )
            return
        if len(self._request_turns) >= MAX_PROVIDER_TURNS:
            self._fail("The provider exceeded the API-request limit.")
            return
        self._request_turns.add(turn)
        self.provider_turns.add(turn)
        self.api_request_count = len(self._request_turns)
        if (
            item.get("sdk_max_retries") != LIVE_SDK_RETRIES_PER_REQUEST
            or item.get("api_attempt_ceiling")
            != 1 + LIVE_SDK_RETRIES_PER_REQUEST
        ):
            self._fail("The provider request retry contract changed.")

    def _record_usage(self, turn: Any, item: dict[str, Any]) -> None:
        mode = item.get("mode")
        if mode not in {"incremental", "cumulative"}:
            self._fail("The provider usage mode is invalid.", usage_integrity=True)
            return
        if self._usage_mode is not None and mode != self._usage_mode:
            self._fail(
                "The provider mixed incremental and cumulative usage modes.",
                usage_integrity=True,
            )
            return
        if (
            isinstance(turn, bool)
            or not isinstance(turn, int)
            or turn not in self._request_turns
            or turn in self._usage_turns
        ):
            self._fail(
                "Provider usage is not bound to one unique request turn.",
                usage_integrity=True,
            )
            return
        if (
            item.get("usage_available") is not True
            or item.get("usage_complete") is not True
        ):
            self._fail(
                "The provider returned incomplete token usage for a bounded request.",
                usage_integrity=True,
            )
            return
        usage = item.get("usage")
        if not isinstance(usage, dict):
            self._fail("The provider usage payload is invalid.", usage_integrity=True)
            return
        observed: dict[str, int] = {}
        for field in self._incremental_usage:
            value = usage.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                self._fail(
                    "The provider usage payload has an invalid count.",
                    usage_integrity=True,
                )
                return
            observed[field] = value
        if observed["total_tokens"] < (
            observed["input_tokens"] + observed["output_tokens"]
        ):
            self._fail(
                "The provider usage payload has an inconsistent total.",
                usage_integrity=True,
            )
            return
        if mode == "cumulative" and any(
            observed[field] < self._cumulative_usage[field]
            for field in observed
        ):
            self._fail(
                "The provider cumulative usage decreased.",
                usage_integrity=True,
            )
            return
        self._usage_mode = mode
        self._usage_turns.add(turn)
        if mode == "cumulative":
            self._cumulative_usage.update(observed)
        else:
            for field, value in observed.items():
                self._incremental_usage[field] += value
        self.usage_event_count = len(self._usage_turns)
        if self.usage()["total_tokens"] > MAX_TOTAL_TOKENS_PER_CASE:
            self._fail("The provider exceeded the per-case token limit.")

    def progress(self, event: dict[str, Any]) -> None:
        item = dict(event) if isinstance(event, dict) else {}
        self._retain_event(item)
        name = str(item.get("event") or "")
        turn = item.get("turn")
        if name == "provider_tool_requested":
            self.tool_count = min(self.tool_count + 1, MAX_TOOL_CALLS + 1)
            if self.tool_count > MAX_TOOL_CALLS:
                self._fail("The provider exceeded the CAD tool-call limit.")
        if name == "provider_request_started":
            self._record_request(turn, item)
        if name.endswith("retrying") or (
            name == "provider_tool_result_sent" and item.get("ok") is False
        ):
            self.retry_count = min(self.retry_count + 1, MAX_RETRY_EVENTS + 1)
            if self.retry_count > MAX_RETRY_EVENTS:
                self._fail("The provider exceeded the visible retry limit.")
        if name == "provider_usage":
            self._record_usage(turn, item)

    def questions(self, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        count = len(questions)
        self.question_count = min(
            self.question_count + count, LIVE_MAX_QUESTIONS_PER_CASE
        )
        # Each Tier 1 case supplies all critical dimensions. A question is
        # unnecessary for this fixed benchmark and ends the case safely.
        self.unnecessary_question_count = self.question_count
        if count and self.question_count >= LIVE_MAX_QUESTIONS_PER_CASE:
            self._fail("The provider exceeded the question-count limit.")
        return []

    def cancelled(self) -> bool:
        if time.monotonic() - self.started > CASE_TOTAL_TIMEOUT_SECONDS:
            self.limit_error = "The provider exceeded the case timeout."
        return self.limit_error is not None

    def usage(self) -> dict[str, Any]:
        source = (
            self._cumulative_usage
            if self._usage_mode == "cumulative"
            else self._incremental_usage
        )
        return normalized_usage(
            input_tokens=source["input_tokens"],
            output_tokens=source["output_tokens"],
            cached_input_tokens=source["cached_input_tokens"],
            reasoning_tokens=source["reasoning_tokens"],
            total_tokens=source["total_tokens"],
        )

    def snapshot(self) -> dict[str, Any]:
        """Return recoverable partial metrics after any later case failure."""

        missing_usage_turns = sorted(self._request_turns - self._usage_turns)
        return {
            "elapsed_seconds": max(0.0, time.monotonic() - self.started),
            "events": list(self.events),
            "retained_event_bytes": self.retained_event_bytes,
            "dropped_event_count": self.dropped_event_count,
            "event_evidence_complete": self.dropped_event_count == 0,
            "question_count": self.question_count,
            "unnecessary_question_count": self.unnecessary_question_count,
            "retry_count": self.retry_count,
            "tool_call_count": self.tool_count,
            "api_request_count": self.api_request_count,
            "provider_turns_observed": sorted(self.provider_turns),
            "usage_event_count": self.usage_event_count,
            "usage_mode": self._usage_mode,
            "usage_complete": (
                not missing_usage_turns and not self.usage_integrity_errors
            ),
            "missing_usage_turns": missing_usage_turns,
            "usage_integrity_errors": list(self.usage_integrity_errors),
            "usage": self.usage(),
            "limit_error": self.limit_error,
        }


def _tool(service: VibeCADService, name: str, **arguments: Any) -> dict[str, Any]:
    result = service.registry.call(name, **arguments)
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"Fixture operation {name} failed: {result}")
    return result


def _seed_box(
    service: VibeCADService,
    *,
    label: str,
    width: float,
    depth: float,
    height: float,
) -> dict[str, Any]:
    body = _tool(service, "partdesign.create_body", label=label)
    body_name = body["mutation"]["body"]
    sketch = _tool(
        service,
        "partdesign.create_sketch",
        body_name=body_name,
        label=f"{label} Profile",
        support={"type": "origin_plane", "plane": "XY_Plane"},
    )
    sketch_name = sketch["mutation"]["sketch"]
    _tool(service, "partdesign.edit_sketch", sketch_name=sketch_name)
    _tool(
        service,
        "sketcher.draw_rectangle",
        width=width,
        height=depth,
        center_x=0,
        center_y=0,
        construction=False,
    )
    _tool(service, "sketcher.close_sketch")
    pad = _tool(
        service,
        "partdesign.pad",
        profile_name=sketch_name,
        label=label,
        extent={"type": "length", "length": height},
        side="one_side",
        reversed=False,
        taper_angle_degrees=0,
        second_taper_angle_degrees=0,
        refine=True,
    )
    sketch_object = App.ActiveDocument.getObject(sketch_name)
    width_constraint_index = None
    for index, constraint in enumerate(list(sketch_object.Constraints)):
        value = getattr(constraint, "Value", None)
        if (
            str(getattr(constraint, "Type", "")) == "DistanceX"
            and isinstance(value, (int, float))
            and abs(abs(float(value)) - width) <= 1e-6
        ):
            width_constraint_index = index
            break
    width_constraint_name = ""
    if width_constraint_index is not None:
        width_constraint_name = "BenchmarkWidth"
        sketch_object.renameConstraint(width_constraint_index, width_constraint_name)
        App.ActiveDocument.recompute()
    return {
        "body": body_name,
        "sketch": sketch_name,
        "feature": pad["mutation"]["feature"],
        "width_constraint_index": width_constraint_index,
        "width_constraint_name": width_constraint_name,
        "sketch_geometry_count": int(sketch_object.GeometryCount),
        "sketch_constraint_count": int(sketch_object.ConstraintCount),
    }


def _seed_source_hole(service: VibeCADService) -> dict[str, Any]:
    fixture = _seed_box(
        service, label="Mirror Plate", width=40, depth=30, height=5
    )
    sketch = _tool(
        service,
        "partdesign.create_sketch",
        body_name=fixture["body"],
        label="Source Hole",
        support={
            "type": "planar_face",
            "object_name": fixture["feature"],
            "selection": {
                "type": "query",
                "normal": {"x": 0, "y": 0, "z": 1},
                "near_point": {"x": -10, "y": 0, "z": 5},
                "normal_tolerance_degrees": 1,
                "max_distance": 1,
            },
        },
    )
    sketch_name = sketch["mutation"]["sketch"]
    _tool(service, "partdesign.edit_sketch", sketch_name=sketch_name)
    _tool(
        service,
        "sketcher.add_circle",
        center=[-10, 0],
        radius=2.5,
        construction=False,
    )
    _tool(
        service,
        "sketcher.constrain",
        constraints=[
            {
                "type": "Lock",
                "point": {"geometry": 0, "point": "center"},
                "position_mm": [-10, 0],
            },
            {"type": "Diameter", "geometry": 0, "size_mm": 5},
        ],
    )
    _tool(service, "sketcher.close_sketch")
    pocket = _tool(
        service,
        "partdesign.pocket",
        profile_name=sketch_name,
        label="Source Through Hole",
        extent={"type": "through_all"},
        side="one_side",
        reversed=False,
        taper_angle_degrees=0,
        second_taper_angle_degrees=0,
        refine=True,
    )
    fixture.update(
        hole_sketch=sketch_name,
        feature=pocket["mutation"]["feature"],
    )
    body = App.ActiveDocument.getObject(fixture["body"])
    mirror_plane = service._partdesign_origin_feature(body, "YZ_Plane")
    if mirror_plane is None:
        raise RuntimeError("The mirror fixture has no YZ center plane.")
    fixture["mirror_plane"] = mirror_plane.Name
    return fixture


def _prepare_case(case_id: str, service: VibeCADService) -> dict[str, Any]:
    Gui.Selection.clearSelection()
    fixture: dict[str, Any] = {
        "kind": "benchmark_setup",
        "object_names": [],
    }
    if case_id == "t1_change_dimension":
        seeded = _seed_box(
            service, label="Resizable Box", width=40, depth=30, height=20
        )
        if seeded["width_constraint_index"] is None:
            raise RuntimeError("The change-dimension fixture has no width constraint.")
        fixture.update(seeded)
        Gui.Selection.addSelection(App.ActiveDocument.getObject(seeded["sketch"]))
    elif case_id == "t1_mirror_feature":
        seeded = _seed_source_hole(service)
        fixture.update(seeded)
        Gui.Selection.addSelection(App.ActiveDocument.getObject(seeded["feature"]))
    elif case_id == "t1_export_stl":
        seeded = _seed_box(
            service, label="Printable Cube", width=20, depth=20, height=20
        )
        fixture.update(seeded)
        Gui.Selection.addSelection(App.ActiveDocument.getObject(seeded["feature"]))
    App.ActiveDocument.recompute()
    App.ActiveDocument.save()
    fixture["object_names"] = [str(obj.Name) for obj in App.ActiveDocument.Objects]
    fixture["canonical_sha256"] = sha256_file(Path(App.ActiveDocument.FileName))
    return fixture


def _selected_provider(
    service: VibeCADService, readiness: dict[str, Any]
) -> Any:
    if service.provider_name() != readiness["provider"]:
        raise RuntimeError("The selected provider changed after readiness validation.")
    configured_model = service.provider_model()
    if (
        configured_model != readiness["model"]
        and not (
            readiness["provider"] == "chatgpt"
            and not configured_model
        )
    ):
        raise RuntimeError("The selected model changed after readiness validation.")
    auth = service.auth_state()
    if auth.source != readiness["auth_source"] or auth.source == "environment":
        raise RuntimeError("The configured credential source changed after readiness validation.")
    chatgpt_account = None
    if readiness["provider"] == "chatgpt":
        from VibeCADCodex import account_binding_secret, read_account

        account_result = read_account(refresh_token=False)
        chatgpt_account = account_result.get("account")
        chatgpt_binding_key = account_binding_secret()
    else:
        chatgpt_binding_key = None
    if not readiness_execution_identity_matches(
        readiness,
        provider=service.provider_name(),
        base_url=service.provider_base_url(),
        auth_source=auth.source,
        credential=service.provider_api_key(),
        chatgpt_account=chatgpt_account,
        chatgpt_binding_secret=chatgpt_binding_key,
    ):
        raise RuntimeError(
            "The provider endpoint or credential changed after readiness validation."
        )
    selected = choose_provider(service, prefer_online=True)
    expected_class = PROVIDER_CLASSES.get(readiness["provider"])
    if expected_class is None or not isinstance(selected, expected_class):
        raise RuntimeError("The normal provider selector returned the wrong adapter.")
    if isinstance(selected, OfflineProvider):
        raise RuntimeError("The normal provider selector returned offline mode.")
    if (
        isinstance(selected, ChatGPTSubscriptionProvider)
        and not str(getattr(selected, "model", "") or "").strip()
    ):
        selected.model = str(readiness["model"])
    if str(getattr(selected, "model", "")) != readiness["model"]:
        raise RuntimeError("The selected provider adapter has the wrong model.")
    if hasattr(selected, "max_turns"):
        selected.max_turns = MAX_PROVIDER_TURNS
    if hasattr(selected, "timeout_seconds"):
        selected.timeout_seconds = PROVIDER_TIMEOUT_SECONDS
    if type(selected) is OpenAIProvider:
        if not readiness_execution_identity_matches(
            readiness,
            provider="openai",
            base_url=selected.base_url,
            auth_source=auth.source,
            credential=selected.api_key,
        ):
            raise RuntimeError(
                "The selected adapter endpoint or credential does not match readiness."
            )
        if OPENAI_SDK_MAX_RETRIES != LIVE_SDK_RETRIES_PER_REQUEST:
            raise RuntimeError("The OpenAI SDK retry contract changed.")
        selected.max_request_bytes = LIVE_MAX_REQUEST_BYTES
        selected.max_output_tokens_per_request = LIVE_MAX_OUTPUT_TOKENS_PER_REQUEST
        selected.max_total_tokens = LIVE_TOTAL_TOKENS_PER_CASE
    elif type(selected) is ChatGPTSubscriptionProvider:
        if not readiness_execution_identity_matches(
            readiness,
            provider="chatgpt",
            base_url=None,
            auth_source=auth.source,
            chatgpt_account=chatgpt_account,
            chatgpt_binding_secret=chatgpt_binding_key,
        ):
            raise RuntimeError(
                "The selected ChatGPT account does not match readiness."
            )
        selected.skills_enabled = False
    else:
        raise RuntimeError("The live benchmark selected an unsupported adapter.")
    selected.web_search_enabled = False
    return selected


def _last_shape(document: Any) -> Any | None:
    for obj in reversed(list(document.Objects)):
        shape = getattr(obj, "Shape", None)
        if shape is None:
            continue
        try:
            if not shape.isNull() and shape.Solids:
                return obj
        except Exception:
            continue
    return None


def _close_all() -> None:
    Gui.Selection.clearSelection()
    for name in list(App.listDocuments()):
        App.closeDocument(name)


def _near_dimensions(shape: Any, expected: list[float], tolerance: float = 1e-5) -> bool:
    observed = sorted(
        [shape.BoundBox.XLength, shape.BoundBox.YLength, shape.BoundBox.ZLength]
    )
    return all(abs(left - right) <= tolerance for left, right in zip(observed, sorted(expected)))


def _radius_count(shape: Any, radius: float, tolerance: float = 1e-5) -> int:
    count = 0
    for edge in list(shape.Edges):
        value = getattr(getattr(edge, "Curve", None), "Radius", None)
        if isinstance(value, (int, float)) and abs(float(value) - radius) <= tolerance:
            count += 1
    return count


def _constraint_stage(document: Any) -> dict[str, Any]:
    sketches = [
        obj for obj in document.Objects
        if getattr(obj, "TypeId", "") == "Sketcher::SketchObject"
    ]
    if not sketches:
        return validation_stage(
            applicable=False,
            reason="The accepted native feature path contains no sketch.",
        )
    evidence = []
    for sketch in sketches:
        try:
            degrees = int(sketch.DoF)
        except Exception:
            degrees = None
        evidence.append(
            {
                "sketch": str(sketch.Name),
                "degrees_of_freedom": degrees,
                "conflicting_constraints": list(
                    getattr(sketch, "ConflictingConstraints", []) or []
                ),
            }
        )
    passed = all(
        item["degrees_of_freedom"] == 0
        and not item["conflicting_constraints"]
        for item in evidence
    )
    return validation_stage(
        applicable=True,
        passed=passed,
        evidence={"sketches": evidence, "fully_constrained": passed},
    )


def _case_validation(
    case_id: str,
    document: Any,
    *,
    execution_error: str | None,
    reopened: bool,
    revision_count: int,
    export_path: Path | None,
    fixture: dict[str, Any],
    final_sha256: str,
    isolated_validation: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    feature = _last_shape(document)
    shape = getattr(feature, "Shape", None)
    target_scope = visible_solid_target_evidence(document, feature)
    base_geometry = bool(
        feature is not None
        and shape is not None
        and not shape.isNull()
        and shape.isValid()
        and len(shape.Solids) == 1
        and target_scope["passed"]
    )
    geometry_ok = base_geometry and execution_error is None
    dimensions_ok = False
    dimension_evidence: dict[str, Any] = {"feature": getattr(feature, "Name", None)}
    if base_geometry:
        observed = sorted(
            [shape.BoundBox.XLength, shape.BoundBox.YLength, shape.BoundBox.ZLength]
        )
        dimension_evidence["observed_bounds_mm"] = observed
        if case_id == "t1_exact_box":
            expected_volume = 40 * 30 * 20
            volume = exact_volume_evidence(shape, expected_volume)
            dimensions_ok = (
                _near_dimensions(shape, [40, 30, 20])
                and volume["passed"]
            )
            dimension_evidence.update(
                expected_bounds_mm=sorted([40, 30, 20]),
                exact_volume=volume,
            )
        elif case_id == "t1_centered_hole":
            centered_hole = centered_hole_evidence(shape, 3)
            expected_volume = 40 * 30 * 10 - math.pi * 3 * 3 * 10
            dimensions_ok = (
                _near_dimensions(shape, [40, 30, 10])
                and centered_hole["passed"]
                and abs(shape.Volume - expected_volume) <= 1e-3
            )
            dimension_evidence.update(
                expected_bounds_mm=sorted([40, 30, 10]),
                hole_radius_mm=3,
                expected_volume_mm3=expected_volume,
                observed_volume_mm3=shape.Volume,
                centered_hole=centered_hole,
            )
        elif case_id == "t1_round_edges":
            fillet = all_edge_fillet_evidence(feature)
            dimensions_ok = (
                _near_dimensions(shape, [30, 30, 30])
                and _radius_count(shape, 2) >= 12
                and fillet["passed"]
            )
            dimension_evidence.update(
                expected_bounds_mm=[30, 30, 30],
                fillet_radius_mm=2,
                all_outer_edges=fillet,
            )
        elif case_id == "t1_hollow_enclosure":
            expected_volume = 60 * 40 * 25 - 56 * 36 * 23
            open_top = open_top_aperture_evidence(shape, 2)
            volume = exact_volume_evidence(shape, expected_volume)
            dimensions_ok = (
                _near_dimensions(shape, [60, 40, 25])
                and volume["passed"]
                and open_top["passed"]
            )
            dimension_evidence.update(
                expected_bounds_mm=sorted([60, 40, 25]),
                wall_thickness_mm=2,
                exact_volume=volume,
                open_top_aperture=open_top,
            )
        elif case_id == "t1_change_dimension":
            changed_constraint = changed_constraint_evidence(document, fixture)
            volume = exact_volume_evidence(shape, 55 * 30 * 20)
            dimensions_ok = (
                _near_dimensions(shape, [55, 30, 20])
                and changed_constraint["passed"]
                and volume["passed"]
            )
            dimension_evidence.update(
                expected_bounds_mm=sorted([55, 30, 20]),
                original_constraint_edit=changed_constraint,
                exact_volume=volume,
            )
        elif case_id == "t1_mirror_feature":
            expected_volume = 40 * 30 * 5 - 2 * math.pi * 2.5 * 2.5 * 5
            mirrored_link = mirrored_link_evidence(
                feature,
                str(fixture.get("feature") or ""),
                str(fixture.get("mirror_plane") or ""),
            )
            mirrored_holes = symmetric_through_holes_evidence(
                shape, 2.5, ((-10, 0), (10, 0))
            )
            dimensions_ok = (
                _near_dimensions(shape, [40, 30, 5])
                and abs(shape.Volume - expected_volume) <= 1e-3
                and mirrored_link["passed"]
                and mirrored_holes["passed"]
            )
            dimension_evidence.update(
                expected_bounds_mm=sorted([40, 30, 5]),
                hole_radius_mm=2.5,
                expected_volume_mm3=expected_volume,
                observed_volume_mm3=shape.Volume,
                native_mirrored_link=mirrored_link,
                symmetric_through_holes=mirrored_holes,
            )
        elif case_id == "t1_export_stl":
            expected_volume = 20 * 20 * 20
            volume = exact_volume_evidence(shape, expected_volume)
            dimensions_ok = (
                _near_dimensions(shape, [20, 20, 20])
                and volume["passed"]
            )
            dimension_evidence.update(
                expected_bounds_mm=[20, 20, 20],
                exact_volume=volume,
            )

    object_types = [str(getattr(obj, "TypeId", "")) for obj in document.Objects]
    expected_revisions = 0 if case_id == "t1_export_stl" else 1
    fixture_names = set(fixture.get("object_names") or [])
    current_names = {str(obj.Name) for obj in document.Objects}
    baseline_sha256 = str(fixture.get("canonical_sha256") or "")
    file_change_ok = (
        final_sha256 == baseline_sha256
        if case_id == "t1_export_stl"
        else final_sha256 != baseline_sha256
    )
    editable = (
        base_geometry
        and revision_count == expected_revisions
        and file_change_ok
        and fixture_names <= current_names
        and any(
            type_id.startswith("PartDesign::")
            or type_id in {"Part::Box", "Sketcher::SketchObject"}
            for type_id in object_types
        )
    )
    follow_up = case_id in FOLLOW_UP_CASES
    export_validation = (
        stl_export_evidence(export_path, [20, 20, 20])
        if case_id == "t1_export_stl"
        else None
    )
    export_ok = bool(export_validation and export_validation["passed"])
    return {
        "geometry": validation_stage(
            applicable=True,
            passed=geometry_ok,
            evidence={
                "shape_valid": base_geometry,
                "execution_error": execution_error,
                "feature": getattr(feature, "Name", None),
                "target_scope": target_scope,
            },
        ),
        "dimensions": validation_stage(
            applicable=True,
            passed=dimensions_ok and execution_error is None,
            evidence=dimension_evidence,
        ),
        "constraints": _constraint_stage(document),
        "editability": validation_stage(
            applicable=True,
            passed=editable and execution_error is None,
            evidence={
                "object_types": object_types,
                "expected_revision_count": expected_revisions,
                "observed_revision_count": revision_count,
                "fixture_kind": fixture.get("kind"),
                "fixture_objects_preserved": sorted(fixture_names & current_names),
                "baseline_sha256": baseline_sha256,
                "final_sha256": final_sha256,
                "file_change_matches_case": file_change_ok,
            },
        ),
        "follow_up": (
            validation_stage(
                applicable=True,
                passed=dimensions_ok and editable and execution_error is None,
                evidence={
                    "explicit_selection_fixture": True,
                    "fixture_kind": fixture.get("kind"),
                    "fixture_objects": sorted(fixture_names),
                },
            )
            if follow_up
            else validation_stage(
                applicable=False,
                reason="This case starts a new design.",
            )
        ),
        "reopen": validation_stage(
            applicable=True,
            passed=(
                reopened
                and base_geometry
                and isolated_validation.get("ok") is True
            ),
            evidence={
                "document_reopened": reopened,
                "shape_valid": base_geometry,
                "isolated_saved_document_validation": isolated_validation,
            },
        ),
        "export": (
            validation_stage(
                applicable=True,
                passed=export_ok and execution_error is None,
                evidence={"format": "stl", "reopened_mesh": export_validation},
            )
            if case_id == "t1_export_stl"
            else validation_stage(
                applicable=False,
                reason="This case does not request an export.",
            )
        ),
    }


def _export_path(tool_trace: list[dict[str, Any]]) -> Path | None:
    for trace in reversed(tool_trace):
        result = trace.get("result") if isinstance(trace, dict) else None
        export = result.get("export") if isinstance(result, dict) else None
        if isinstance(export, dict):
            value = export.get("path") or export.get("file_path")
            if isinstance(value, str) and value:
                return Path(value).resolve()
    return None


def _failed_case_attempt(
    case: dict[str, Any],
    output: Path,
    readiness: dict[str, Any],
    source_commit: str,
    readiness_sha256: str,
    runtime_identity_sha256: str,
    run_nonce: str,
    error: str,
    in_memory_partial: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Retain a complete v2 failure record after an unexpected case error."""

    case_id = str(case["id"])
    case_dir = output / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    partial_path = case_dir / "partial-metrics.json"
    partial: dict[str, Any] = (
        dict(in_memory_partial) if isinstance(in_memory_partial, dict) else {}
    )
    if (
        not partial
        and partial_path.is_file()
        and not partial_path.is_symlink()
        and partial_path.stat().st_size <= LIMITS["max_partial_checkpoint_bytes"]
    ):
        try:
            loaded = json.loads(partial_path.read_text(encoding="utf-8"))
            if (
                isinstance(loaded, dict)
                and loaded.get("schema") == LIVE_PARTIAL_METRICS_SCHEMA
            ):
                partial = loaded
        except Exception:
            partial = {}
    partial_error: str | None = None
    partial_valid = False
    try:
        metrics = recover_partial_case_metrics(
            partial,
            now_epoch=time.time(),
            expected_case_id=case_id,
            expected_source_commit=source_commit,
            expected_readiness_sha256=readiness_sha256,
            expected_runtime_identity_sha256=runtime_identity_sha256,
            expected_run_nonce=run_nonce,
        )
        partial_valid = bool(partial)
        if partial_valid:
            try:
                atomic_write_json(partial_path, partial)
            except Exception as exc:
                partial_valid = False
                partial_error = (
                    f"Partial checkpoint persistence failed: {type(exc).__name__}: {exc}"
                )
    except Exception as exc:
        metrics = empty_partial_case_metrics()
        partial_error = (
            f"Partial checkpoint validation failed: {type(exc).__name__}: {exc}"
        )
    if partial_error:
        error = f"{error}; {partial_error}"
    elapsed = metrics["elapsed_seconds"]
    usage = metrics["usage"]
    turns = metrics["provider_turns_observed"]
    fixture = partial.get("fixture")
    if not isinstance(fixture, dict) or fixture.get("kind") != "benchmark_setup":
        fixture = None
    transcript_path = case_dir / "provider-events.json"
    atomic_write_json(
        transcript_path,
        {
            "schema": "vibecad-live-provider-events-v1",
            "version": 1,
            "case_id": case_id,
            "provider": readiness["provider"],
            "model": readiness["model"],
            "source_commit": source_commit,
            "prompt": case["prompt"],
            "error": error,
            "events": list(metrics.get("events") or []),
            "tool_trace": [],
            "limits": LIMITS,
            "partial_metrics_recovered": bool(partial),
            "unexpected_case_failure": True,
        },
    )
    document_path = case_dir / f"{case_id}.FCStd"
    artifacts = [transcript_path]
    if document_path.is_file():
        artifacts.insert(0, document_path)
    if partial_path.is_file():
        artifacts.append(partial_path)
    artifact_paths = [str(path.resolve()) for path in artifacts]
    artifact_sha256 = {
        path: sha256_file(Path(path)) for path in artifact_paths
    }
    false_stage = lambda name: validation_stage(
        applicable=True,
        passed=False,
        evidence={"error": error, "check": name},
    )
    stages = {
        "geometry": false_stage("geometry"),
        "dimensions": false_stage("dimensions"),
        "constraints": validation_stage(
            applicable=False, reason="The failed case produced no solver evidence."
        ),
        "editability": false_stage("editability"),
        "follow_up": (
            false_stage("follow_up")
            if case_id in FOLLOW_UP_CASES
            else validation_stage(
                applicable=False, reason="This case starts a new design."
            )
        ),
        "reopen": false_stage("reopen"),
        "export": (
            false_stage("export")
            if case_id == "t1_export_stl"
            else validation_stage(
                applicable=False, reason="This case does not request an export."
            )
        ),
    }
    record = make_case_attempt(
        tier=1,
        case_id=case_id,
        attempt=1,
        provider=readiness["provider"],
        model=readiness["model"],
        executor=LIVE_EXECUTOR,
        live_model_score=True,
        stages=stages,
        question_count=int(metrics.get("question_count", 0) or 0),
        unnecessary_question_count=int(
            metrics.get("unnecessary_question_count", 0) or 0
        ),
        retry_count=int(metrics.get("retry_count", 0) or 0),
        usage=usage,
        instruction_adherence=unrated_instruction_adherence(
            "This failed raw live attempt still needs a separate human rating."
        ),
        elapsed_seconds=float(elapsed),
        diagnostics=failure_diagnostics(stages),
        artifact_paths=artifact_paths,
        source_commit=source_commit,
        artifact_sha256=artifact_sha256,
        allow_unrated_live=True,
    )
    final_sha = sha256_file(document_path) if document_path.is_file() else sha256_file(transcript_path)
    if fixture is None:
        fixture = {
            "kind": "benchmark_setup",
            "canonical_sha256": final_sha,
            "object_names": [],
        }
    api_request_count = int(metrics.get("api_request_count", 0) or 0)
    runtime = {
        "case_id": case_id,
        "provider_class": str(partial.get("provider_class") or ""),
        "provider_turn_count": max(turns, default=0),
        "provider_turns_observed": turns,
        "api_request_count": api_request_count,
        "api_attempt_upper_bound": api_request_count
        * (1 + LIVE_SDK_RETRIES_PER_REQUEST),
        "tool_call_count": int(metrics.get("tool_call_count", 0) or 0),
        "retry_count": int(metrics.get("retry_count", 0) or 0),
        "usage_event_count": int(metrics.get("usage_event_count", 0) or 0),
        "usage_mode": metrics.get("usage_mode"),
        "usage_complete": metrics.get("usage_complete") is True,
        "missing_usage_turns": list(metrics.get("missing_usage_turns") or []),
        "usage_integrity_error_count": len(
            list(metrics.get("usage_integrity_errors") or [])
        ),
        "event_evidence_complete": (
            metrics.get("event_evidence_complete") is True
        ),
        "partial_evidence_valid": partial_valid,
        "fixture": fixture,
        "final_sha256": final_sha,
        "isolated_validation_ok": False,
    }
    atomic_write_json(case_dir / "case-attempt-v2.json", record)
    _close_all()
    return record, runtime


def _run_case(
    case: dict[str, Any],
    output: Path,
    readiness: dict[str, Any],
    source_commit: str,
    readiness_sha256: str,
    runtime_identity_sha256: str,
    run_nonce: str,
    failure_state: dict[str, Any],
) -> dict[str, Any]:
    case_id = str(case["id"])
    case_dir = output / case_id
    case_dir.mkdir(parents=True, exist_ok=False)
    document_path = case_dir / f"{case_id}.FCStd"
    transcript_path = case_dir / "provider-events.json"
    partial_path = case_dir / "partial-metrics.json"
    measurements = Measurements()
    started_epoch = time.time()
    checkpoint_sequence = 0
    _close_all()
    Gui.activateWorkbench("PartDesignWorkbench")
    document = App.newDocument(f"Live_{case_id}")
    document.saveAs(str(document_path))
    service = VibeCADService()
    response = None
    execution_error: str | None = None
    provider_class = ""
    fixture: dict[str, Any] = {
        "kind": "benchmark_setup",
        "object_names": [],
        "canonical_sha256": sha256_file(document_path),
    }
    def checkpoint(*, force: bool = False) -> None:
        nonlocal checkpoint_sequence
        checkpoint_sequence += 1
        payload = {
            "schema": LIVE_PARTIAL_METRICS_SCHEMA,
            "version": LIVE_PARTIAL_METRICS_VERSION,
            "case_id": case_id,
            "source_commit": source_commit,
            "readiness_sha256": readiness_sha256,
            "runtime_identity_sha256": runtime_identity_sha256,
            "run_nonce": run_nonce,
            "started_at_epoch": started_epoch,
            "updated_at_epoch": time.time(),
            "checkpoint_sequence": checkpoint_sequence,
            "provider_class": provider_class,
            "fixture": fixture,
            "measurements": measurements.snapshot(),
        }
        validate_partial_metrics_checkpoint(
            payload,
            expected_case_id=case_id,
            expected_source_commit=source_commit,
            expected_readiness_sha256=readiness_sha256,
            expected_runtime_identity_sha256=runtime_identity_sha256,
            expected_run_nonce=run_nonce,
        )
        persist_partial_metrics_checkpoint(
            failure_state,
            payload,
            partial_path,
            force=force,
        )

    def progress(event: dict[str, Any]) -> None:
        measurements.progress(event)
        checkpoint()

    def questions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = measurements.questions(items)
        checkpoint()
        return result

    checkpoint(force=True)
    try:
        fixture = _prepare_case(case_id, service)
        checkpoint(force=True)
        selected = _selected_provider(service, readiness)
        provider_class = selected.__class__.__name__
        checkpoint(force=True)
        response = run_prompt(
            str(case["prompt"]),
            service=service,
            prefer_online=True,
            provider=selected,
            progress_callback=progress,
            cancellation_check=measurements.cancelled,
            question_callback=questions,
        )
        execution_error = response.error or measurements.limit_error
        if execution_error is None:
            App.ActiveDocument.recompute()
            App.ActiveDocument.save()
        checkpoint(force=True)
    except Exception as exc:
        execution_error = f"{type(exc).__name__}: {exc}"
        checkpoint(force=True)
    trace = list(response.tool_trace) if response is not None else []
    exported = _export_path(trace)
    _close_all()
    reopened = False
    revision_count = 0
    try:
        reopened_document = App.openDocument(str(document_path))
        reopened_document.recompute()
        reopened = True
        revision_count = len(VibeCADService().revision_timeline())
    except Exception as exc:
        reopened_document = App.ActiveDocument
        execution_error = execution_error or f"Reopen failed: {exc}"
    if reopened_document is None:
        raise RuntimeError(f"Case {case_id} has no reopenable CAD document.")
    _close_all()
    remaining = CASE_TOTAL_TIMEOUT_SECONDS - (
        time.monotonic() - measurements.started
    )
    if remaining <= 0.1:
        isolated_validation = {
            "ok": False,
            "errors": ["The case total-time limit expired before isolated validation."],
        }
        execution_error = execution_error or isolated_validation["errors"][0]
    else:
        try:
            isolated_validation = validate_saved_document(
                document_path, timeout=min(60.0, remaining)
            )
        except Exception as exc:
            isolated_validation = {"ok": False, "errors": [str(exc)]}
            execution_error = execution_error or f"Isolated validation failed: {exc}"
    checkpoint(force=True)
    if time.monotonic() - measurements.started > CASE_TOTAL_TIMEOUT_SECONDS:
        raise TimeoutError("The case total-time limit expired before the final reopen.")
    try:
        reopened_document = App.openDocument(str(document_path))
        reopened_document.recompute()
    except Exception as exc:
        raise RuntimeError(f"Case {case_id} failed its validation reopen: {exc}") from exc
    elapsed = time.monotonic() - measurements.started
    if elapsed > CASE_TOTAL_TIMEOUT_SECONDS:
        execution_error = execution_error or "The case exceeded the total-time limit."
    checkpoint(force=True)
    final_sha256 = sha256_file(document_path)
    try:
        usage = measurements.usage()
        if measurements.usage_event_count == 0:
            execution_error = execution_error or (
                "The provider returned no normalized usage evidence."
            )
    except Exception as exc:
        execution_error = execution_error or f"Usage evidence failed: {exc}"
        usage = normalized_usage()
    final_metrics = measurements.snapshot()
    if final_metrics["usage_complete"] is not True:
        execution_error = execution_error or (
            "The provider request has incomplete or invalid usage evidence."
        )
    if final_metrics["event_evidence_complete"] is not True:
        execution_error = execution_error or (
            "The retained provider progress evidence is incomplete."
        )
    stages = _case_validation(
        case_id,
        reopened_document,
        execution_error=execution_error,
        reopened=reopened,
        revision_count=revision_count,
        export_path=exported,
        fixture=fixture,
        final_sha256=final_sha256,
        isolated_validation=isolated_validation,
    )
    runtime = {
        "case_id": case_id,
        "provider_class": provider_class,
        "provider_turn_count": max(measurements.provider_turns, default=0),
        "provider_turns_observed": sorted(measurements.provider_turns),
        "api_request_count": measurements.api_request_count,
        "api_attempt_upper_bound": measurements.api_request_count
        * (1 + LIVE_SDK_RETRIES_PER_REQUEST),
        "tool_call_count": measurements.tool_count,
        "retry_count": measurements.retry_count,
        "usage_event_count": measurements.usage_event_count,
        "usage_mode": final_metrics["usage_mode"],
        "usage_complete": final_metrics["usage_complete"],
        "missing_usage_turns": final_metrics["missing_usage_turns"],
        "usage_integrity_error_count": len(
            final_metrics["usage_integrity_errors"]
        ),
        "event_evidence_complete": final_metrics["event_evidence_complete"],
        "partial_evidence_valid": True,
        "fixture": fixture,
        "final_sha256": final_sha256,
        "isolated_validation_ok": isolated_validation.get("ok") is True,
    }
    transcript = {
        "schema": "vibecad-live-provider-events-v1",
        "version": 1,
        "case_id": case_id,
        "provider": readiness["provider"],
        "model": readiness["model"],
        "provider_class": provider_class,
        "source_commit": source_commit,
        "prompt": case["prompt"],
        "final_output": response.final_output if response is not None else None,
        "error": execution_error,
        "events": measurements.events,
        "tool_trace": trace,
        "limits": LIMITS,
        "runtime": runtime,
    }
    atomic_write_json(transcript_path, transcript)
    artifact_paths = [
        str(document_path.resolve()),
        str(transcript_path.resolve()),
        str(partial_path.resolve()),
    ]
    if exported is not None and exported.is_file():
        artifact_paths.append(str(exported))
    artifact_sha256 = {
        path: sha256_file(Path(path)) for path in artifact_paths
    }
    diagnostics = failure_diagnostics(stages)
    if execution_error and not any(
        item.get("stage") == "execution" for item in diagnostics
    ):
        diagnostics.append(
            {
                "severity": "error",
                "stage": "execution",
                "code": "live_provider_execution_failed",
                "message": "The live provider case did not complete.",
                "details": {"error": execution_error},
            }
        )
    record = make_case_attempt(
        tier=1,
        case_id=case_id,
        attempt=1,
        provider=readiness["provider"],
        model=readiness["model"],
        executor=LIVE_EXECUTOR,
        live_model_score=True,
        stages=stages,
        question_count=measurements.question_count,
        unnecessary_question_count=measurements.unnecessary_question_count,
        retry_count=measurements.retry_count,
        usage=usage,
        instruction_adherence=unrated_instruction_adherence(
            "This raw live-model attempt needs a separate bound human rating."
        ),
        elapsed_seconds=elapsed,
        diagnostics=diagnostics,
        artifact_paths=artifact_paths,
        source_commit=source_commit,
        artifact_sha256=artifact_sha256,
        allow_unrated_live=True,
    )
    validate_case_attempt(record, allow_unrated_live=True)
    atomic_write_json(case_dir / "case-attempt-v2.json", record)
    _close_all()
    return record, runtime


def main() -> int:
    output_value = str(os.environ.get("VIBECAD_LIVE_BENCHMARK_OUTPUT") or "").strip()
    readiness_value = str(
        os.environ.get("VIBECAD_LIVE_BENCHMARK_READINESS_JSON") or ""
    ).strip()
    source_commit = str(
        os.environ.get("VIBECAD_LIVE_BENCHMARK_SOURCE_COMMIT") or ""
    ).strip()
    if not output_value or not readiness_value or not source_commit:
        raise RuntimeError("The live benchmark environment is incomplete.")
    output = Path(output_value).resolve()
    readiness = validate_live_readiness(json.loads(readiness_value))
    expected_digest = str(
        os.environ.get("VIBECAD_LIVE_BENCHMARK_READINESS_SHA256") or ""
    )
    expected_runtime_digest = str(
        os.environ.get("VIBECAD_LIVE_BENCHMARK_RUNTIME_SHA256") or ""
    )
    runtime_identity_value = str(
        os.environ.get("VIBECAD_LIVE_BENCHMARK_RUNTIME_PATH") or ""
    ).strip()
    if not runtime_identity_value:
        raise RuntimeError("The installed runtime identity is missing.")
    runtime_path = Path(runtime_identity_value)
    if runtime_path != output / "runtime-identity.json":
        raise RuntimeError("The installed runtime identity path is invalid.")
    runtime_snapshot, runtime_identity = load_bounded_json(
        runtime_path,
        max_bytes=4 * 1024 * 1024,
        label="live runtime identity",
        require_single_link=True,
    )
    with runtime_snapshot:
        if runtime_identity_digest(runtime_identity) != expected_runtime_digest:
            raise RuntimeError(
                "The installed runtime identity changed before execution."
            )
        validate_runtime_identity(
            runtime_identity,
            source_commit=source_commit,
        )
        runtime_snapshot.verify_unchanged()
    if readiness_digest(readiness) != expected_digest:
        raise RuntimeError("The provider readiness evidence changed before execution.")
    if (
        readiness["provider"]
        != os.environ.get("VIBECAD_LIVE_BENCHMARK_PROVIDER")
        or readiness["model"] != os.environ.get("VIBECAD_LIVE_BENCHMARK_MODEL")
    ):
        raise RuntimeError("The provider or model changed before execution.")
    suite_path = Path(__file__).resolve().with_name("tier1_cases.json")
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    cases = list(suite.get("cases") or [])
    if tuple(case.get("id") for case in cases) != TIER1_CASE_IDS:
        raise RuntimeError("The Tier 1 live case set is incomplete or reordered.")

    run_nonce = secrets.token_hex(32)
    attempts = []
    case_runtime = []
    for case in cases:
        failure_state: dict[str, Any] = {}
        try:
            record, runtime = _run_case(
                case,
                output,
                readiness,
                source_commit,
                expected_digest,
                expected_runtime_digest,
                run_nonce,
                failure_state,
            )
        except Exception as exc:
            record, runtime = _failed_case_attempt(
                case,
                output,
                readiness,
                source_commit,
                expected_digest,
                expected_runtime_digest,
                run_nonce,
                f"{type(exc).__name__}: {exc}",
                in_memory_partial=failure_state.get("partial"),
            )
        attempts.append(record)
        case_runtime.append(runtime)
    usage_fields = (
        "input_tokens", "output_tokens", "cached_input_tokens",
        "reasoning_tokens", "total_tokens",
    )
    usage_summary = {
        field: sum(item["normalized_usage"][field] for item in attempts)
        for field in usage_fields
    }
    unscorable_reasons = live_run_unscorable_reasons(case_runtime)
    report = {
        "schema": LIVE_RUN_SCHEMA,
        "version": LIVE_RUN_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tier": 1,
        "provider": readiness["provider"],
        "model": readiness["model"],
        "executor": LIVE_EXECUTOR,
        "source_commit": source_commit,
        "readiness_sha256": expected_digest,
        "runtime_identity_sha256": expected_runtime_digest,
        "run_nonce": run_nonce,
        "limits": LIMITS,
        "usage_summary": usage_summary,
        "case_runtime": case_runtime,
        "scorable": not unscorable_reasons,
        "unscorable_reasons": unscorable_reasons,
        "scored": False,
        "case_attempts": attempts,
    }
    validate_unrated_live_run(report, require_scorable=False)
    atomic_write_json(output / "tier1-live-unrated-run.json", report)
    return 0 if all(item["passed"] for item in attempts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
