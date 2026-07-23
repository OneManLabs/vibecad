# SPDX-License-Identifier: LGPL-2.1-or-later
"""Create one editable native exploded-view configuration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from VibeCADAssemblyExplodedView import (
    ACTIVE_VIEW_PROPERTY,
    CONFIGURATION_PROPERTY,
    CONTRACT_VERSION_PROPERTY,
    EXPLODED_VIEW_SCHEMA,
    EXPLODED_VIEW_VERSION,
    MANAGED_STEP_PROPERTY,
    MANAGED_VIEW_PROPERTY,
    METADATA_PROPERTY,
    STATE_PROPERTY,
    canonical_json,
    configuration_id,
    configuration_identity_payload,
    placement_fact,
    prepare_component_moves,
    seal_metadata,
    validate_native_configuration,
)
from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "assembly.create_exploded_view",
    "description": (
        "Create one editable native exploded-view configuration for exact "
        "component occurrences. Use either one global direction or a vector "
        "on every component move. Every distance is an exact positive value "
        "in mm. The saved assembly placements stay assembled; native view "
        "steps calculate the exploded placements without motion simulation."
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
            "label": {
                "type": "string",
                "description": "Visible label for the exploded-view configuration.",
            },
            "direction": domain_runtime.vector_schema(
                "One global explosion direction. Omit it when every component has its own vector.",
                units=None,
            ),
            "components": {
                "type": "array",
                "minItems": 1,
                "maxItems": 64,
                "description": (
                    "Exact component occurrences and their positive translation distances."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "component_name": {
                            "type": "string",
                            "description": "Exact internal name of one direct linked occurrence.",
                        },
                        "distance_mm": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                            "description": "Exact translation distance in mm.",
                        },
                        "vector": domain_runtime.vector_schema(
                            "Direction for this component. Supply it only when the global direction is absent.",
                            units=None,
                        ),
                    },
                    "required": ["component_name", "distance_mm"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["assembly_name", "label", "components"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    assembly_name: str,
    label: str,
    components: list[dict[str, Any]],
    direction: dict[str, Any] | None = None,
    *,
    fault: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    permission = _authorize(service)
    if permission is not None:
        return permission
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    assembly = _find_assembly(service, assembly_name)
    if assembly is None:
        return _invalid(
            f"Assembly not found by exact internal name: {assembly_name}. "
            "Call core.inspect with scope='domain' for exact names."
        )
    if _managed_views(assembly):
        return _invalid(
            "This assembly already has a VibeCAD exploded-view configuration. "
            "This release supports one managed configuration for each assembly. "
            "Restore the existing configuration when you need assembled placements."
        )
    try:
        moves = prepare_component_moves(assembly, components, direction)
    except ValueError as exc:
        return _invalid(str(exc))

    document_before = assembly.Document
    object_names_before = {
        str(getattr(obj, "Name", "")) for obj in list(document_before.Objects)
    }
    assembly_properties_before = set(getattr(assembly, "PropertiesList", []) or [])
    assembly_property_values = {
        name: getattr(assembly, name)
        for name in (ACTIVE_VIEW_PROPERTY, CONTRACT_VERSION_PROPERTY)
        if name in assembly_properties_before
    }
    created_object_names: list[str] = []

    def inject(stage: str) -> None:
        if fault is not None:
            fault(stage)

    def create() -> dict[str, Any]:
        import FreeCAD as App
        import CommandCreateView
        import UtilsAssembly

        document = App.ActiveDocument
        target = document.getObject(assembly.Name) if document is not None else None
        if target is None:
            raise RuntimeError("The exact assembly disappeared before the transaction started.")
        target_components = {
            child.Name: child
            for child in list(getattr(target, "Group", []) or [])
            if str(getattr(child, "TypeId", ""))
            in {"App::Link", "Assembly::AssemblyLink"}
        }
        for move in moves:
            current = target_components.get(move["component_name"])
            if current is None or getattr(current, "LinkedObject", None) is None:
                raise RuntimeError(
                    f"Component occurrence {move['component_name']!r} disappeared."
                )
            if current.LinkedObject.Name != move["linked_object_name"]:
                raise RuntimeError(
                    f"Component occurrence {move['component_name']!r} changed source."
                )

        assembled = {
            name: placement_fact(component.Placement)
            for name, component in target_components.items()
            if name in {move["component_name"] for move in moves}
        }
        view_group = UtilsAssembly.getViewGroup(target)
        if view_group.Name not in object_names_before:
            created_object_names.append(view_group.Name)
        view = view_group.newObject("App::FeaturePython", "VibeCADExplodedView")
        created_object_names.append(view.Name)
        CommandCreateView.ExplodedView(view)
        if bool(getattr(App, "GuiUp", False)):
            CommandCreateView.ViewProviderExplodedView(view.ViewObject)
        view.Label = clean_label
        steps: list[Any] = []
        step_records: list[dict[str, Any]] = []
        for move in moves:
            component = target_components[move["component_name"]]
            step = target.newObject("App::FeaturePython", "VibeCADExplodedMove")
            created_object_names.append(step.Name)
            CommandCreateView.ExplodedViewStep(step, 0)
            if bool(getattr(App, "GuiUp", False)):
                CommandCreateView.ViewProviderExplodedViewStep(step.ViewObject)
            delta = move["displacement_mm"]
            step.MovementTransform = App.Placement(
                App.Vector(delta["x"], delta["y"], delta["z"]),
                App.Rotation(),
            )
            step.References = [target, [f"{component.Name}."]]
            step.Label = f"{clean_label}: {component.Label}"
            steps.append(step)
            step_records.append(
                {
                    "component_name": component.Name,
                    "linked_object_name": component.LinkedObject.Name,
                    "step_name": step.Name,
                    "direction": dict(move["direction"]),
                    "distance_mm": float(move["distance_mm"]),
                    "displacement_mm": dict(move["displacement_mm"]),
                    "assembled_placement": assembled[component.Name],
                }
            )
        view.Group = steps
        document.recompute()
        calculated, line_positions = view.Proxy._calculateExplodedPlacements(view)
        for record in step_records:
            component = target_components[record["component_name"]]
            final = calculated.get(component)
            if final is None:
                raise RuntimeError(
                    f"Native exploded view did not calculate {component.Name!r}."
                )
            record["exploded_placement"] = placement_fact(final)
        if len(line_positions) != len(step_records):
            raise RuntimeError(
                "Native exploded-view line count does not match the component moves."
            )

        identity_seed = configuration_identity_payload(
            {
                "schema": EXPLODED_VIEW_SCHEMA,
                "assembly_name": target.Name,
                "view_name": view.Name,
                "components": step_records,
            }
        )
        identity = configuration_id(identity_seed)
        for step in steps:
            _add_property(step, "App::PropertyBool", MANAGED_STEP_PROPERTY)
            _add_property(step, "App::PropertyString", CONFIGURATION_PROPERTY)
            setattr(step, MANAGED_STEP_PROPERTY, True)
            setattr(step, CONFIGURATION_PROPERTY, identity)
        metadata = seal_metadata(
            {
                "schema": EXPLODED_VIEW_SCHEMA,
                "version": EXPLODED_VIEW_VERSION,
                "configuration_id": identity,
                "generation": 1,
                "previous_content_sha256": None,
                "state": "exploded",
                "assembly_name": target.Name,
                "view_group_name": view_group.Name,
                "view_name": view.Name,
                "components": step_records,
            }
        )
        _add_property(view, "App::PropertyBool", MANAGED_VIEW_PROPERTY)
        _add_property(view, "App::PropertyString", CONFIGURATION_PROPERTY)
        _add_property(view, "App::PropertyString", STATE_PROPERTY)
        _add_property(view, "App::PropertyString", METADATA_PROPERTY)
        setattr(view, MANAGED_VIEW_PROPERTY, True)
        setattr(view, CONFIGURATION_PROPERTY, identity)
        setattr(view, STATE_PROPERTY, "exploded")
        setattr(view, METADATA_PROPERTY, canonical_json(metadata))
        _add_property(target, "App::PropertyLink", ACTIVE_VIEW_PROPERTY)
        _add_property(target, "App::PropertyInteger", CONTRACT_VERSION_PROPERTY)
        setattr(target, ACTIVE_VIEW_PROPERTY, view)
        setattr(target, CONTRACT_VERSION_PROPERTY, EXPLODED_VIEW_VERSION)
        inject("after_assembly_provenance")
        document.recompute()
        return {
            "document": document.Name,
            "assembly": target.Name,
            "view_group": view_group.Name,
            "view": view.Name,
            "steps": [step.Name for step in steps],
            "configuration_id": identity,
            "state": "exploded",
            "state_meaning": (
                "The native exploded-view graph is available. Accepted component "
                "placements remain assembled."
            ),
            "metadata": metadata,
            "assembled_component_placements": {
                name: placement_fact(target_components[name].Placement)
                for name in assembled
            },
            "exploded_component_placements": {
                record["component_name"]: record["exploded_placement"]
                for record in step_records
            },
            "line_count": len(line_positions),
            "native_view_proxy": type(view.Proxy).__name__,
            "native_step_proxies": [type(step.Proxy).__name__ for step in steps],
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        document = assembly.Document
        target = document.getObject(result.get("assembly"))
        view = document.getObject(result.get("view"))
        validation = (
            validate_native_configuration(target, view)
            if target is not None and view is not None
            else {"ok": False, "errors": ["created native objects are missing"]}
        )
        checks = [
            {
                "name": "native_editable_graph",
                "ok": result.get("native_view_proxy") == "ExplodedView"
                and all(name == "ExplodedViewStep" for name in result.get("native_step_proxies", [])),
            },
            {
                "name": "exact_move_count",
                "ok": len(result.get("steps") or []) == len(moves)
                and result.get("line_count") == len(moves),
            },
            {
                "name": "versioned_metadata",
                "ok": validation.get("ok") is True,
                "errors": validation.get("errors"),
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    def rollback() -> None:
        """Remove created graph objects and restore assembly-owned properties."""
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("The active document is unavailable during rollback.")
        target = active.getObject(assembly.Name)
        if target is None:
            raise RuntimeError("The assembly is unavailable during rollback.")
        for name in reversed(created_object_names):
            if active.getObject(name) is not None:
                active.removeObject(name)
        for name in (ACTIVE_VIEW_PROPERTY, CONTRACT_VERSION_PROPERTY):
            if name in assembly_properties_before:
                setattr(target, name, assembly_property_values[name])
            elif name in set(getattr(target, "PropertiesList", []) or []):
                target.removeProperty(name)
        active.recompute()

    transaction = run_freecad_transaction(
        f"Create assembly exploded view: {clean_label}",
        create,
        verifier=verify,
        rollback_handler=rollback,
    )
    mutation = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "create_exploded_view", "mutation": mutation},
        next_action=(
            "Review the native exploded-view steps. State 'exploded' means the "
            "view graph is available; accepted component placements remain "
            "assembled. Use "
            "assembly.restore_exploded_view with the exact view name to restore "
            "the stored assembled placements."
        ),
    )


def _authorize(service: Any) -> dict[str, Any] | None:
    authorizer = getattr(service, "authorize", None)
    if not callable(authorizer):
        return _invalid("Assembly edit permission cannot be verified.")
    try:
        authorizer("design.modify")
    except PermissionError as exc:
        return _invalid(str(exc), failure_code="RBAC_DENIED", failure_stage="permission")
    return None


def _find_assembly(service: Any, assembly_name: str) -> Any:
    clean = str(assembly_name or "").strip()
    if not clean:
        return None
    return next(
        (assembly for assembly in service._assembly_objects() if assembly.Name == clean),
        None,
    )


def _managed_views(assembly: Any) -> list[Any]:
    result: list[Any] = []
    for child in list(getattr(assembly, "Group", []) or []):
        if str(getattr(child, "TypeId", "")) != "Assembly::ViewGroup":
            continue
        result.extend(
            view
            for view in list(getattr(child, "Group", []) or [])
            if getattr(view, MANAGED_VIEW_PROPERTY, None) is True
            or str(getattr(view, "Name", "")).startswith("VibeCADExplodedView")
        )
    return result


def _add_property(obj: Any, property_type: str, name: str) -> None:
    if name not in set(getattr(obj, "PropertiesList", []) or []):
        obj.addProperty(
            property_type,
            name,
            "VibeCAD Exploded View",
            "VibeCAD versioned exploded-view provenance and restore metadata.",
        )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
