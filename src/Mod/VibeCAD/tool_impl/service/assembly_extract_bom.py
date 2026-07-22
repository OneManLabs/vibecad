# SPDX-License-Identifier: LGPL-2.1-or-later
"""Extract a deterministic bill of materials from one native assembly."""

from __future__ import annotations

from typing import Any

from VibeCADTools import unchanged_state


TOOL_SPEC = {
    "name": "assembly.extract_bom",
    "description": (
        "Extract a bill of materials from one exact native assembly. Equal "
        "linked source parts are grouped into one line with a quantity. The "
        "result includes occurrence names, source name, part number, material, "
        "and description when those properties exist. This operation does not "
        "change the document."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "AssemblyWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {"assembly_name": {"type": "string", "description": "Exact internal name of the native assembly."}},
        "required": ["assembly_name"],
        "additionalProperties": False,
    },
}


def run(service: Any, assembly_name: str) -> dict[str, Any]:
    assembly = _find_assembly(service, assembly_name)
    if assembly is None:
        return _invalid(f"Assembly not found by exact internal name: {assembly_name}")
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for component in _components(assembly):
        source = getattr(component, "LinkedObject", None)
        if source is None:
            return _invalid(f"Assembly component {component.Name} has no linked source object.")
        part_number = _property(source, ("PartNumber", "Part_Number", "StockCode"))
        material = _material_name(source)
        description = _property(source, ("Description", "Comment"))
        key = (source.Name, part_number, material, description)
        line = grouped.setdefault(key, {
            "source_object": source.Name,
            "source_label": str(getattr(source, "Label", source.Name)),
            "part_number": part_number or None,
            "material": material or None,
            "description": description or None,
            "quantity": 0,
            "occurrences": [],
        })
        line["quantity"] += 1
        line["occurrences"].append(component.Name)
    lines = sorted(grouped.values(), key=lambda item: (
        str(item.get("part_number") or ""), str(item["source_label"]), str(item["source_object"])
    ))
    for line in lines:
        line["occurrences"].sort()
    return {
        "ok": True,
        "assembly": assembly.Name,
        "line_count": len(lines),
        "total_quantity": sum(int(line["quantity"]) for line in lines),
        "lines": lines,
        "state_change": unchanged_state(),
    }


def _find_assembly(service: Any, name: str) -> Any:
    clean = str(name or "").strip()
    return next((item for item in service._assembly_objects() if item.Name == clean), None)


def _components(assembly: Any) -> list[Any]:
    return [child for child in list(getattr(assembly, "Group", []) or []) if str(
        getattr(child, "TypeId", "")
    ) in {"App::Link", "Assembly::AssemblyLink"}]


def _property(obj: Any, names: tuple[str, ...]) -> str:
    for name in names:
        value = getattr(obj, name, None)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _material_name(obj: Any) -> str:
    explicit = _property(obj, ("Material", "MaterialName", "CardName"))
    if explicit:
        return explicit
    card = getattr(obj, "ShapeMaterial", None)
    if card is None or not str(getattr(card, "UUID", "") or "").strip():
        return ""
    return str(getattr(card, "Name", "") or "").strip()


def _invalid(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False}
