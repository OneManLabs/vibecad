# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bind one numeric CAD property to one named spreadsheet parameter."""

from __future__ import annotations

import re
from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NUMERIC_PROPERTIES = {
    "App::PropertyAngle",
    "App::PropertyDistance",
    "App::PropertyFloat",
    "App::PropertyInteger",
    "App::PropertyLength",
    "App::PropertyQuantity",
}


TOOL_SPEC = {
    "name": "spreadsheet.bind_parameter",
    "description": (
        "Bind one existing numeric CAD property to one exact spreadsheet alias. "
        "The tool creates only a direct Sheet.alias expression. It does not run "
        "code or accept a free-form expression. The target recomputes when the "
        "spreadsheet value changes."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "SpreadsheetWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "sheet_name": {"type": "string", "description": "Exact internal spreadsheet name."},
            "alias": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]*$", "description": "Existing spreadsheet alias."},
            "target_object_name": {"type": "string", "description": "Exact internal CAD object name."},
            "property_name": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]*$", "description": "Exact existing numeric property name."},
        },
        "required": ["sheet_name", "alias", "target_object_name", "property_name"],
        "additionalProperties": False,
    },
}


def run(service: Any, sheet_name: str, alias: str, target_object_name: str, property_name: str) -> dict[str, Any]:
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    clean_sheet = str(sheet_name or "").strip()
    clean_alias = str(alias or "").strip()
    clean_target = str(target_object_name or "").strip()
    clean_property = str(property_name or "").strip()
    if not _NAME.fullmatch(clean_alias) or not _NAME.fullmatch(clean_property):
        return _invalid("alias and property_name must be simple identifier names.")
    sheet = doc.getObject(clean_sheet)
    target = doc.getObject(clean_target)
    if sheet is None or not domain_runtime.is_spreadsheet(sheet):
        return _invalid(f"Spreadsheet not found by exact internal name: {sheet_name}")
    if target is None:
        return _invalid(f"Target object not found by exact internal name: {target_object_name}")
    try:
        cell = str(sheet.getCellFromAlias(clean_alias) or "")
    except Exception as exc:
        return _invalid(f"Could not resolve spreadsheet alias {clean_alias}: {exc}")
    if not cell:
        return _invalid(f"Spreadsheet alias does not exist: {clean_alias}")
    if clean_property not in list(getattr(target, "PropertiesList", []) or []):
        return _invalid(f"Target property does not exist: {clean_property}")
    property_type = str(target.getTypeIdOfProperty(clean_property))
    if property_type not in _NUMERIC_PROPERTIES:
        return _invalid(
            f"Target property is not a supported numeric property: {property_type}"
        )
    expression = f"{sheet.Name}.{clean_alias}"

    def bind() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        native_target = active.getObject(target.Name) if active else None
        native_sheet = active.getObject(sheet.Name) if active else None
        if native_target is None or native_sheet is None:
            raise RuntimeError("The spreadsheet or target object disappeared.")
        native_target.setExpression(clean_property, expression)
        active.recompute()
        value = getattr(native_target, clean_property)
        spreadsheet_value = native_sheet.get(cell)
        return {
            "sheet": native_sheet.Name,
            "cell": cell,
            "alias": clean_alias,
            "target_object": native_target.Name,
            "property_name": clean_property,
            "property_type": property_type,
            "expression": str(dict(native_target.ExpressionEngine).get(clean_property) or ""),
            "target_value": _number(value),
            "spreadsheet_value": _number(spreadsheet_value),
            "feature_state": list(getattr(native_target, "State", []) or []),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        checks = [
            {"name": "expression_link", "ok": expression in str(result.get("expression") or "")},
            {"name": "value_link", "ok": abs(float(result.get("target_value", 0)) - float(result.get("spreadsheet_value", 1))) <= 1.0e-9},
            {"name": "feature_recomputed", "ok": "Invalid" not in list(result.get("feature_state") or [])},
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Bind {target.Name}.{clean_property} to {expression}", bind, verifier=verify
    )
    result = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "bind_parameter", "mutation": result},
        next_action="Change the aliased cell with spreadsheet.set_cells to create a size variant.",
    )


def _number(value: Any) -> float:
    raw = getattr(value, "Value", value)
    return float(raw)


def _invalid(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False}
