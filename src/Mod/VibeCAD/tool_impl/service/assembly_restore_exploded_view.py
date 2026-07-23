# SPDX-License-Identifier: LGPL-2.1-or-later
"""Restore one managed exploded view to exact assembled placements."""

from __future__ import annotations

from typing import Any

from VibeCADAssemblyExplodedView import (
    ACTIVE_VIEW_PROPERTY,
    METADATA_PROPERTY,
    STATE_PROPERTY,
    canonical_json,
    load_view_metadata,
    placement_fact,
    placement_from_fact,
    seal_metadata,
    validate_native_configuration,
)
from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime
from .assembly_create_exploded_view import _authorize, _find_assembly, _invalid


TOOL_SPEC = {
    "name": "assembly.restore_exploded_view",
    "description": (
        "Restore every moved component in one exact managed native exploded "
        "view to its recorded assembled placement. The editable native view "
        "and its move steps remain in the document. The metadata advances to "
        "a new generation with state 'assembled'. This is not motion simulation."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "AssemblyWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "assembly_name": {
                "type": "string",
                "description": "Exact internal name of the native assembly.",
            },
            "view_name": {
                "type": "string",
                "description": "Exact internal name returned by assembly.create_exploded_view.",
            },
        },
        "required": ["assembly_name", "view_name"],
        "additionalProperties": False,
    },
}


def run(service: Any, assembly_name: str, view_name: str) -> dict[str, Any]:
    permission = _authorize(service)
    if permission is not None:
        return permission
    assembly = _find_assembly(service, assembly_name)
    if assembly is None:
        return _invalid(f"Assembly not found by exact internal name: {assembly_name}.")
    document = service._active_document()
    clean_view_name = str(view_name or "").strip()
    view = document.getObject(clean_view_name) if document is not None and clean_view_name else None
    if view is None or getattr(assembly, ACTIVE_VIEW_PROPERTY, None) is not view:
        return _invalid(
            f"Active exploded view not found by exact internal name: {view_name}."
        )
    validation = validate_native_configuration(
        assembly,
        view,
        allow_component_placement_drift=True,
    )
    if not validation.get("ok"):
        return _invalid(
            "The exploded-view metadata or native graph is not safe to restore.",
            validation_errors=list(validation.get("errors") or []),
        )
    metadata = dict(validation["metadata"])
    if metadata.get("state") != "exploded":
        return _invalid("The exploded view is already in the assembled state.")
    expected_identity = str(metadata["configuration_id"])
    expected_digest = str(metadata["content_sha256"])
    component_names = [item["component_name"] for item in metadata["components"]]
    rollback_placements = {
        name: assembly.Document.getObject(name).Placement.copy()
        for name in component_names
    }
    rollback_view_state = str(getattr(view, STATE_PROPERTY, "") or "")
    rollback_view_metadata = str(getattr(view, METADATA_PROPERTY, "") or "")

    def restore() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        target = active.getObject(assembly.Name) if active is not None else None
        target_view = active.getObject(view.Name) if active is not None else None
        if target is None or target_view is None:
            raise RuntimeError("The exact assembly or exploded view disappeared.")
        current = load_view_metadata(target_view)
        if not current.get("ok"):
            raise RuntimeError("The exploded-view metadata changed before restore.")
        current_metadata = dict(current["metadata"])
        if (
            current_metadata.get("configuration_id") != expected_identity
            or current_metadata.get("content_sha256") != expected_digest
        ):
            raise RuntimeError("The exploded-view identity changed before restore.")
        components = {
            child.Name: child
            for child in list(getattr(target, "Group", []) or [])
            if str(getattr(child, "TypeId", ""))
            in {"App::Link", "Assembly::AssemblyLink"}
        }
        before = {
            name: placement_fact(component.Placement)
            for name, component in components.items()
            if name in {item["component_name"] for item in current_metadata["components"]}
        }
        for item in current_metadata["components"]:
            component = components.get(item["component_name"])
            if component is None or getattr(component, "LinkedObject", None) is None:
                raise RuntimeError(
                    f"Component occurrence {item['component_name']!r} disappeared."
                )
            if component.LinkedObject.Name != item["linked_object_name"]:
                raise RuntimeError(
                    f"Component occurrence {item['component_name']!r} changed source."
                )
            component.Placement = placement_from_fact(item["assembled_placement"])
            component.purgeTouched()
        updated = dict(current_metadata)
        updated["generation"] = int(current_metadata["generation"]) + 1
        updated["previous_content_sha256"] = expected_digest
        updated["state"] = "assembled"
        updated = seal_metadata(updated)
        setattr(target_view, STATE_PROPERTY, "assembled")
        setattr(target_view, METADATA_PROPERTY, canonical_json(updated))
        setattr(target, ACTIVE_VIEW_PROPERTY, target_view)
        active.recompute()
        after = {
            item["component_name"]: placement_fact(
                components[item["component_name"]].Placement
            )
            for item in updated["components"]
        }
        return {
            "document": active.Name,
            "assembly": target.Name,
            "view": target_view.Name,
            "configuration_id": expected_identity,
            "state": "assembled",
            "state_meaning": (
                "Accepted component placements match the stored assembled state. "
                "The editable native view graph remains available."
            ),
            "generation": updated["generation"],
            "previous_content_sha256": expected_digest,
            "content_sha256": updated["content_sha256"],
            "placements_before": before,
            "assembled_component_placements": after,
            "metadata": updated,
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        active = assembly.Document
        target = active.getObject(result.get("assembly"))
        target_view = active.getObject(result.get("view"))
        checked = (
            validate_native_configuration(target, target_view)
            if target is not None and target_view is not None
            else {"ok": False, "errors": ["restored native objects are missing"]}
        )
        checks = [
            {
                "name": "configuration_identity",
                "ok": result.get("configuration_id") == expected_identity,
            },
            {
                "name": "metadata_generation",
                "ok": result.get("generation") == int(metadata["generation"]) + 1
                and result.get("previous_content_sha256") == expected_digest,
            },
            {
                "name": "assembled_state",
                "ok": checked.get("ok") is True
                and (checked.get("metadata") or {}).get("state") == "assembled",
                "errors": checked.get("errors"),
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    def rollback() -> None:
        """Restore exact pre-call placements and managed metadata after native abort."""
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("The active document is unavailable during rollback.")
        target = active.getObject(assembly.Name)
        target_view = active.getObject(view.Name)
        if target is None or target_view is None:
            raise RuntimeError("The exploded-view rollback objects are missing.")
        for name, placement in rollback_placements.items():
            component = active.getObject(name)
            if component is None:
                raise RuntimeError(f"Rollback component {name!r} is missing.")
            component.Placement = placement.copy()
            component.purgeTouched()
        setattr(target_view, STATE_PROPERTY, rollback_view_state)
        setattr(target_view, METADATA_PROPERTY, rollback_view_metadata)
        setattr(target, ACTIVE_VIEW_PROPERTY, target_view)
        active.recompute()

    transaction = run_freecad_transaction(
        f"Restore assembly exploded view: {view.Name}",
        restore,
        verifier=verify,
        rollback_handler=rollback,
    )
    mutation = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "restore_exploded_view", "mutation": mutation},
        next_action=(
            "The assembly placements match the stored assembled state. The "
            "native exploded-view configuration remains editable and inspectable."
        ),
    )
