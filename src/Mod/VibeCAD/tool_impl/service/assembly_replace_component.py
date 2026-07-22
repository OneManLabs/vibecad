# SPDX-License-Identifier: LGPL-2.1-or-later
"""Replace one assembly occurrence while preserving its stable identity."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime
from .assembly_insert_component import _validate_component_source


TOOL_SPEC = {
    "name": "assembly.replace_component",
    "description": (
        "Replace the linked source of one exact assembly component while "
        "preserving the occurrence name, label, placement, and joint references. "
        "The old and new source must have equal solid, face, edge, and vertex "
        "counts. Existing joints are solved again before the change is accepted."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "AssemblyWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "assembly_name": {"type": "string", "description": "Exact internal name of the native assembly."},
            "component_name": {"type": "string", "description": "Exact internal name of the component occurrence to replace."},
            "new_source_object_name": {"type": "string", "description": "Exact internal name of the replacement source part."},
        },
        "required": ["assembly_name", "component_name", "new_source_object_name"],
        "additionalProperties": False,
    },
}


def run(service: Any, assembly_name: str, component_name: str, new_source_object_name: str) -> dict[str, Any]:
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    assembly = next((item for item in service._assembly_objects() if item.Name == str(
        assembly_name or ""
    ).strip()), None)
    if assembly is None:
        return _invalid(f"Assembly not found by exact internal name: {assembly_name}")
    component = doc.getObject(str(component_name or "").strip())
    members = {child.Name for child in list(getattr(assembly, "Group", []) or [])}
    if component is None or component.Name not in members or str(component.TypeId) not in {
        "App::Link", "Assembly::AssemblyLink"
    }:
        return _invalid("The requested object is not a linked component of this assembly.")
    old_source = getattr(component, "LinkedObject", None)
    new_source = doc.getObject(str(new_source_object_name or "").strip())
    if old_source is None or new_source is None:
        return _invalid("The old or replacement source object is missing.")
    if old_source is new_source:
        return _invalid("The component already uses the requested source object.")
    source_validation = _validate_component_source(service, new_source)
    if not source_validation.get("ok"):
        return source_validation
    old_topology = _topology(old_source)
    new_topology = _topology(new_source)
    if old_topology != new_topology:
        return _invalid(
            "The replacement topology does not match the current source. "
            f"Current: {old_topology}; replacement: {new_topology}."
        )
    placement_before = domain_runtime.placement_summary(component)
    label_before = str(component.Label)
    joint_group = domain_runtime.assembly_joint_group(assembly)
    joint_count = len(list(getattr(joint_group, "Group", []) or [])) if joint_group else 0

    def replace() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        target_assembly = active.getObject(assembly.Name) if active else None
        target_component = active.getObject(component.Name) if active else None
        target_source = active.getObject(new_source.Name) if active else None
        if target_assembly is None or target_component is None or target_source is None:
            raise RuntimeError("The assembly, occurrence, or replacement source disappeared.")
        target_component.LinkedObject = target_source
        active.recompute()
        solver_code = int(target_assembly.solve(False)) if joint_count else None
        if joint_count:
            active.recompute()
        diagnostics = domain_runtime.assembly_solver_diagnostics(target_assembly)
        shape = getattr(target_component, "Shape", None)
        return {
            "assembly": target_assembly.Name,
            "component": target_component.Name,
            "component_label": str(target_component.Label),
            "old_source": old_source.Name,
            "new_source": getattr(getattr(target_component, "LinkedObject", None), "Name", None),
            "placement_before": placement_before,
            "placement_after": domain_runtime.placement_summary(target_component),
            "topology": _topology(target_source),
            "shape_valid": bool(shape is not None and not shape.isNull() and shape.isValid()),
            "joint_count": joint_count,
            "solver_code": solver_code,
            "solver_diagnostics": diagnostics,
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        diagnostics = result.get("solver_diagnostics") or {}
        checks = [
            {"name": "replacement_link", "ok": result.get("new_source") == new_source.Name},
            {"name": "stable_occurrence", "ok": result.get("component") == component.Name and result.get("component_label") == label_before},
            {"name": "placement_preserved", "ok": result.get("placement_after") == placement_before},
            {"name": "topology_preserved", "ok": result.get("topology") == old_topology},
            {"name": "valid_shape", "ok": result.get("shape_valid") is True},
            {"name": "solver", "ok": joint_count == 0 or (
                result.get("solver_code") == 0
                and not diagnostics.get("has_conflicts")
                and not diagnostics.get("has_malformed_constraints")
            )},
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Replace assembly component: {component.Name}", replace, verifier=verify
    )
    result = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "replace_component", "mutation": result},
        next_action="Run assembly.analyze_interference and review the updated BOM.",
    )


def _topology(obj: Any) -> dict[str, int]:
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull() or not shape.isValid():
        return {"solids": 0, "faces": 0, "edges": 0, "vertices": 0}
    return {
        "solids": len(list(shape.Solids)),
        "faces": len(list(shape.Faces)),
        "edges": len(list(shape.Edges)),
        "vertices": len(list(shape.Vertexes)),
    }


def _invalid(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False}
