# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run exact pairwise solid-interference checks on one native assembly."""

from __future__ import annotations

from itertools import combinations
from typing import Any

from VibeCADTools import unchanged_state


TOOL_SPEC = {
    "name": "assembly.analyze_interference",
    "description": (
        "Check every pair of solid components in one exact native assembly for "
        "geometric overlap. Report each occurrence pair and exact common volume "
        "in cubic millimetres. Face contact with zero volume is not interference."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "AssemblyWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "assembly_name": {"type": "string", "description": "Exact internal name of the native assembly."},
            "minimum_volume_mm3": {"type": "number", "minimum": 0, "default": 1e-6, "description": "Minimum common volume to report in mm^3."},
        },
        "required": ["assembly_name"],
        "additionalProperties": False,
    },
}


def run(service: Any, assembly_name: str, minimum_volume_mm3: float = 1.0e-6) -> dict[str, Any]:
    assembly = next((item for item in service._assembly_objects() if item.Name == str(
        assembly_name or ""
    ).strip()), None)
    if assembly is None:
        return _invalid(f"Assembly not found by exact internal name: {assembly_name}")
    threshold = float(minimum_volume_mm3)
    if threshold < 0:
        return _invalid("minimum_volume_mm3 must not be negative.")
    components = [child for child in list(getattr(assembly, "Group", []) or []) if str(
        getattr(child, "TypeId", "")
    ) in {"App::Link", "Assembly::AssemblyLink"}]
    if len(components) > 100:
        return _invalid("Interference analysis is limited to 100 components per call.")
    shapes: dict[str, Any] = {}
    invalid: list[str] = []
    for component in components:
        shape = getattr(component, "Shape", None)
        if shape is None or shape.isNull() or not shape.isValid() or not shape.Solids:
            invalid.append(component.Name)
        else:
            shapes[component.Name] = shape
    if invalid:
        return _invalid("Every analyzed component must contain a valid solid: " + ", ".join(sorted(invalid)))
    interferences: list[dict[str, Any]] = []
    checked_pairs = 0
    for first, second in combinations(components, 2):
        checked_pairs += 1
        first_shape, second_shape = shapes[first.Name], shapes[second.Name]
        if not first_shape.BoundBox.intersected(second_shape.BoundBox):
            continue
        common = first_shape.common(second_shape)
        volume = float(common.Volume) if not common.isNull() else 0.0
        if volume > threshold:
            interferences.append({
                "component1": first.Name,
                "component2": second.Name,
                "common_volume_mm3": volume,
                "common_solid_count": len(list(common.Solids)),
            })
    interferences.sort(key=lambda item: (item["component1"], item["component2"]))
    return {
        "ok": True,
        "assembly": assembly.Name,
        "component_count": len(components),
        "checked_pair_count": checked_pairs,
        "has_interference": bool(interferences),
        "interference_count": len(interferences),
        "interferences": interferences,
        "minimum_volume_mm3": threshold,
        "state_change": unchanged_state(),
    }


def _invalid(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False}
