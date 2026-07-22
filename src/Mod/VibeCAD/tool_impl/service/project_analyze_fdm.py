# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run structured heuristic FDM checks on exact named CAD objects."""

from __future__ import annotations

from typing import Any

from VibeCADManufacturing import analyze_fdm_shape
from VibeCADTools import unchanged_state


TOOL_SPEC = {
    "name": "project.analyze_fdm",
    "description": (
        "Check named shapes for FDM validity, solid state, wall spacing, overhangs, "
        "hole size, bounds, and orientation. Heuristics are not certification."
    ),
    "contextual": True,
    "safety": "READ",
    "requires_document": True,
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "object_names": {
                "type": "array", "items": {"type": "string", "minLength": 1},
                "minItems": 1, "maxItems": 32,
                "description": "Exact internal names of final shapes to check.",
            },
        },
        "required": ["object_names"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    object_names: list[str],
    minimum_wall_mm: float = 1.2,
    overhang_angle_degrees: float = 45.0,
    minimum_hole_mm: float = 2.0,
) -> dict[str, Any]:
    service.authorize("project.view")
    document = service._active_document()
    if document is None:
        return {"ok": False, "error": "No active document.", "state_change": unchanged_state()}
    names = list(dict.fromkeys(str(name or "").strip() for name in object_names or []))
    if not names or any(not name for name in names):
        return {"ok": False, "error": "Provide one or more exact object names.", "state_change": unchanged_state()}
    objects = [document.getObject(name) for name in names]
    missing = [name for name, obj in zip(names, objects) if obj is None]
    if missing:
        return {
            "ok": False,
            "error": "FDM analysis objects were not found: " + ", ".join(missing),
            "state_change": unchanged_state(),
        }
    reports = []
    try:
        for obj in objects:
            reports.append(
                analyze_fdm_shape(
                    getattr(obj, "Shape", None),
                    object_name=obj.Name,
                    minimum_wall_mm=float(minimum_wall_mm),
                    overhang_angle_degrees=float(overhang_angle_degrees),
                    minimum_hole_mm=float(minimum_hole_mm),
                )
            )
    except (RuntimeError, TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "state_change": unchanged_state()}
    return {
        "ok": True,
        "analysis": {
            "process": "FDM printing",
            "certification": False,
            "analysis_kind": "heuristic_guidance",
            "objects": reports,
            "warning": "These checks are heuristic guidance, not manufacturing certification.",
        },
        "state_change": unchanged_state(),
    }
