# SPDX-License-Identifier: LGPL-2.1-or-later
"""Update the durable structured design brief for an accepted design turn."""

from __future__ import annotations

from VibeCADDesignBrief import LIST_FIELDS


_ITEM = {"oneOf": [{"type": "string"}, {"type": "object"}]}
_FIELDS = {
    "purpose": {"type": "string"},
    "units": {"type": "string"},
    "manufacturing_process": {"type": "string"},
    "user_preferences": {"type": "object"},
    **{field: {"type": "array", "items": _ITEM} for field in LIST_FIELDS},
}

TOOL_SPEC = {
    "contextual": True,
    "description": (
        "Update durable design intent after requirements or accepted design facts change. "
        "Send complete replacement values only for changed fields. Do not put transient "
        "tool progress or geometry diagnostics in the design brief."
    ),
    "name": "core.update_design_brief",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "base_revision": {
                "type": "string", "minLength": 64, "maxLength": 64,
                "description": "Exact design brief revision from turn-start context.",
            },
            "changes": {
                "type": "object",
                "description": "Complete replacement values for only the fields that changed.",
                "properties": _FIELDS,
                "additionalProperties": False,
                "minProperties": 1,
            },
        },
        "required": ["base_revision", "changes"],
        "additionalProperties": False,
    },
    "safety": "SAFE_WRITE",
    "requires_document": True,
}


def run(service, base_revision: str, changes: dict) -> dict:
    try:
        saved = service.apply_design_brief_update(
            {"base_revision": base_revision, "changes": changes}
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "retry_same_call": True}
    return {
        "ok": True,
        "design_brief": {
            key: value for key, value in saved.items() if key not in {"path", "exists"}
        },
        "state_change": {
            "changed": ["project.design_brief"],
            "revision": saved["revision"],
        },
    }
