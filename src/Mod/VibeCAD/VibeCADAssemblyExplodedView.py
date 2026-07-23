# SPDX-License-Identifier: LGPL-2.1-or-later
"""Versioned contracts for native, editable assembly exploded views."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping


EXPLODED_VIEW_SCHEMA = "vibecad-native-assembly-exploded-view-v1"
EXPLODED_VIEW_VERSION = 1
METADATA_PROPERTY = "VibeCADExplodedViewMetadata"
MANAGED_VIEW_PROPERTY = "VibeCADManagedExplodedView"
MANAGED_STEP_PROPERTY = "VibeCADManagedExplodedMove"
ACTIVE_VIEW_PROPERTY = "VibeCADActiveExplodedView"
CONTRACT_VERSION_PROPERTY = "VibeCADExplodedViewContractVersion"
CONFIGURATION_PROPERTY = "VibeCADExplodedViewConfigurationId"
STATE_PROPERTY = "VibeCADExplodedViewState"

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_PLACEMENT_TOLERANCE = 1.0e-8


def canonical_json(value: Any) -> str:
    """Return the one canonical JSON form used by the persisted contract."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def content_sha256(value: Mapping[str, Any]) -> str:
    """Hash a metadata record without its self-authenticating digest."""
    payload = dict(value)
    payload.pop("content_sha256", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def seal_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with a stable SHA-256 content binding."""
    payload = dict(value)
    payload["content_sha256"] = content_sha256(payload)
    return payload


def configuration_id(value: Mapping[str, Any]) -> str:
    """Create one deterministic identity from a complete move definition."""
    return hashlib.sha256(canonical_json(dict(value)).encode("utf-8")).hexdigest()


def configuration_identity_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact persisted fields that define a configuration identity."""
    return {
        "schema": value.get("schema"),
        "assembly_name": value.get("assembly_name"),
        "view_name": value.get("view_name"),
        "components": value.get("components"),
    }


def vector_fact(value: Any) -> dict[str, float]:
    """Return one JSON-safe XYZ vector."""
    return {
        "x": float(value.x),
        "y": float(value.y),
        "z": float(value.z),
    }


def placement_fact(value: Any) -> dict[str, Any]:
    """Return a stable placement with translation and a quaternion."""
    quaternion = tuple(float(item) for item in value.Rotation.Q)
    if len(quaternion) != 4:
        raise ValueError("A native placement rotation must have four quaternion values.")
    return {
        "position_mm": vector_fact(value.Base),
        "rotation_xyzw": list(quaternion),
    }


def placement_from_fact(value: Mapping[str, Any]) -> Any:
    """Restore one exact native FreeCAD placement from persisted metadata."""
    import FreeCAD as App

    position = value["position_mm"]
    quaternion = value["rotation_xyzw"]
    return App.Placement(
        App.Vector(float(position["x"]), float(position["y"]), float(position["z"])),
        App.Rotation(*(float(item) for item in quaternion)),
    )


def normalize_direction(value: Any, *, context: str) -> dict[str, float]:
    """Validate and normalize one exact direction vector."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an XYZ object.")
    if set(value) != {"x", "y", "z"}:
        raise ValueError(f"{context} must contain only x, y, and z.")
    result: dict[str, float] = {}
    for axis in ("x", "y", "z"):
        raw = value.get(axis)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{context}.{axis} must be a finite number.")
        number = float(raw)
        if not math.isfinite(number):
            raise ValueError(f"{context}.{axis} must be a finite number.")
        result[axis] = number
    length = math.sqrt(sum(number * number for number in result.values()))
    if length <= 1.0e-12:
        raise ValueError(f"{context} must not be a zero-length vector.")
    return {axis: result[axis] / length for axis in ("x", "y", "z")}


def finite_positive_distance(value: Any, *, context: str) -> float:
    """Validate one positive distance in millimetres."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a finite positive distance in mm.")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{context} must be a finite positive distance in mm.")
    return result


def prepare_component_moves(
    assembly: Any,
    components: Any,
    direction: Any = None,
) -> list[dict[str, Any]]:
    """Bind requested moves to exact direct component occurrences."""
    if not isinstance(components, list) or not 1 <= len(components) <= 64:
        raise ValueError("components must contain between 1 and 64 moves.")
    uses_global_direction = direction is not None
    global_direction = (
        normalize_direction(direction, context="direction")
        if uses_global_direction
        else None
    )
    members = {
        str(getattr(child, "Name", "")): child
        for child in list(getattr(assembly, "Group", []) or [])
        if str(getattr(child, "TypeId", ""))
        in {"App::Link", "Assembly::AssemblyLink"}
    }
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(components):
        if not isinstance(raw, Mapping):
            raise ValueError(f"components[{index}] must be an object.")
        allowed = {"component_name", "distance_mm", "vector"}
        unknown = set(raw).difference(allowed)
        if unknown:
            raise ValueError(
                f"components[{index}] has unsupported fields: {', '.join(sorted(unknown))}."
            )
        name = str(raw.get("component_name") or "").strip()
        if not name:
            raise ValueError(f"components[{index}].component_name is required.")
        if name in seen:
            raise ValueError(f"Component occurrence {name!r} is listed more than once.")
        component = members.get(name)
        if component is None:
            raise ValueError(
                f"Component occurrence not found by exact internal name: {name}."
            )
        source = getattr(component, "LinkedObject", None)
        if source is None or not str(getattr(source, "Name", "")):
            raise ValueError(f"Component occurrence {name!r} has no linked source.")
        has_component_vector = raw.get("vector") is not None
        if uses_global_direction and has_component_vector:
            raise ValueError(
                "Use one global direction or one vector for every component, not both."
            )
        if not uses_global_direction and not has_component_vector:
            raise ValueError(
                f"components[{index}].vector is required when direction is absent."
            )
        move_direction = global_direction or normalize_direction(
            raw.get("vector"), context=f"components[{index}].vector"
        )
        distance = finite_positive_distance(
            raw.get("distance_mm"), context=f"components[{index}].distance_mm"
        )
        displacement = {
            axis: float(move_direction[axis]) * distance for axis in ("x", "y", "z")
        }
        prepared.append(
            {
                "component": component,
                "component_name": name,
                "linked_object_name": str(source.Name),
                "direction": dict(move_direction),
                "distance_mm": distance,
                "displacement_mm": displacement,
            }
        )
        seen.add(name)
    return prepared


def validate_metadata_payload(raw: Any) -> dict[str, Any]:
    """Validate one persisted metadata payload without trusting its graph."""
    errors: list[str] = []
    if not isinstance(raw, Mapping):
        return {"ok": False, "errors": ["exploded-view metadata is not an object"]}
    value = dict(raw)
    allowed_fields = {
        "schema",
        "version",
        "configuration_id",
        "generation",
        "previous_content_sha256",
        "state",
        "assembly_name",
        "view_group_name",
        "view_name",
        "components",
        "content_sha256",
    }
    unknown_fields = set(value).difference(allowed_fields)
    if unknown_fields:
        errors.append(
            "exploded-view metadata has unsupported fields: "
            + ", ".join(sorted(str(item) for item in unknown_fields))
        )
    missing_fields = allowed_fields.difference(value)
    if missing_fields:
        errors.append(
            "exploded-view metadata has missing fields: "
            + ", ".join(sorted(str(item) for item in missing_fields))
        )
    if value.get("schema") != EXPLODED_VIEW_SCHEMA:
        errors.append("exploded-view metadata schema is invalid")
    if value.get("version") != EXPLODED_VIEW_VERSION:
        errors.append("exploded-view metadata version is invalid")
    for field in (
        "configuration_id",
        "assembly_name",
        "view_group_name",
        "view_name",
    ):
        if not isinstance(value.get(field), str) or not value[field].strip():
            errors.append(f"exploded-view metadata field {field} is invalid")
    if not _SHA256_PATTERN.fullmatch(str(value.get("configuration_id") or "")):
        errors.append("exploded-view configuration identity is invalid")
    else:
        try:
            expected_configuration_id = configuration_id(
                configuration_identity_payload(value)
            )
        except (TypeError, ValueError) as exc:
            errors.append(
                f"exploded-view configuration identity is not canonical JSON: {exc}"
            )
        else:
            if value.get("configuration_id") != expected_configuration_id:
                errors.append(
                    "exploded-view configuration identity does not match its move definition"
                )
    if value.get("state") not in {"exploded", "assembled"}:
        errors.append("exploded-view metadata state is invalid")
    generation = value.get("generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        errors.append("exploded-view metadata generation is invalid")
    previous = value.get("previous_content_sha256")
    if generation == 1 and previous is not None:
        errors.append("first-generation exploded-view metadata has a previous digest")
    if isinstance(generation, int) and generation > 1 and not _SHA256_PATTERN.fullmatch(
        str(previous or "")
    ):
        errors.append("exploded-view metadata previous digest is invalid")
    components = value.get("components")
    if not isinstance(components, list) or not 1 <= len(components) <= 64:
        errors.append("exploded-view metadata components are invalid")
        components = []
    component_names: set[str] = set()
    step_names: set[str] = set()
    for index, component in enumerate(components):
        context = f"exploded-view component {index}"
        if not isinstance(component, Mapping):
            errors.append(f"{context} is not an object")
            continue
        allowed_component_fields = {
            "component_name",
            "linked_object_name",
            "step_name",
            "direction",
            "distance_mm",
            "displacement_mm",
            "assembled_placement",
            "exploded_placement",
        }
        unknown_component_fields = set(component).difference(
            allowed_component_fields
        )
        if unknown_component_fields:
            errors.append(
                f"{context} has unsupported fields: "
                + ", ".join(
                    sorted(str(item) for item in unknown_component_fields)
                )
            )
        missing_component_fields = allowed_component_fields.difference(component)
        if missing_component_fields:
            errors.append(
                f"{context} has missing fields: "
                + ", ".join(
                    sorted(str(item) for item in missing_component_fields)
                )
            )
        for field in ("component_name", "linked_object_name", "step_name"):
            if not isinstance(component.get(field), str) or not component[field].strip():
                errors.append(f"{context} field {field} is invalid")
        name = str(component.get("component_name") or "")
        step_name = str(component.get("step_name") or "")
        if name in component_names:
            errors.append(f"exploded-view component {name!r} is duplicated")
        if step_name in step_names:
            errors.append(f"exploded-view step {step_name!r} is duplicated")
        component_names.add(name)
        step_names.add(step_name)
        try:
            direction = normalize_direction(
                component.get("direction"), context=f"{context} direction"
            )
            distance = finite_positive_distance(
                component.get("distance_mm"), context=f"{context} distance_mm"
            )
            displacement = _finite_vector(
                component.get("displacement_mm"), context=f"{context} displacement_mm"
            )
            for axis in ("x", "y", "z"):
                if not math.isclose(
                    displacement[axis], direction[axis] * distance,
                    rel_tol=0.0, abs_tol=_PLACEMENT_TOLERANCE,
                ):
                    errors.append(f"{context} displacement does not match its vector and distance")
                    break
        except ValueError as exc:
            errors.append(str(exc))
        for placement_name in ("assembled_placement", "exploded_placement"):
            try:
                _validate_placement_fact(
                    component.get(placement_name),
                    context=f"{context} {placement_name}",
                )
            except ValueError as exc:
                errors.append(str(exc))
        try:
            assembled = _validate_placement_fact(
                component.get("assembled_placement"),
                context=f"{context} assembled_placement",
            )
            exploded = _validate_placement_fact(
                component.get("exploded_placement"),
                context=f"{context} exploded_placement",
            )
            displacement = _finite_vector(
                component.get("displacement_mm"), context=f"{context} displacement_mm"
            )
            for axis in ("x", "y", "z"):
                expected = assembled["position_mm"][axis] + displacement[axis]
                if not math.isclose(
                    exploded["position_mm"][axis], expected,
                    rel_tol=0.0, abs_tol=_PLACEMENT_TOLERANCE,
                ):
                    errors.append(f"{context} exploded placement is inconsistent")
                    break
            if not _quaternions_equal(
                assembled["rotation_xyzw"], exploded["rotation_xyzw"]
            ):
                errors.append(f"{context} changed rotation during a translation-only move")
        except ValueError:
            pass
    digest = str(value.get("content_sha256") or "")
    if not _SHA256_PATTERN.fullmatch(digest):
        errors.append("exploded-view metadata content digest is invalid")
    else:
        try:
            expected_digest = content_sha256(value)
        except (TypeError, ValueError) as exc:
            errors.append(f"exploded-view metadata is not canonical JSON: {exc}")
        else:
            if digest != expected_digest:
                errors.append("exploded-view metadata content digest does not match")
    return {"ok": not errors, "errors": errors, "metadata": value}


def load_view_metadata(view: Any) -> dict[str, Any]:
    """Load and validate the JSON contract from one native view object."""
    raw = getattr(view, METADATA_PROPERTY, None)
    if not isinstance(raw, str) or not raw.strip():
        return {"ok": False, "errors": ["exploded-view metadata is missing"]}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return {"ok": False, "errors": [f"exploded-view metadata is malformed: {exc}"]}
    return validate_metadata_payload(parsed)


def validate_native_configuration(
    assembly: Any,
    view: Any,
    *,
    allow_component_placement_drift: bool = False,
) -> dict[str, Any]:
    """Validate persisted metadata against exact native FreeCAD identities."""
    loaded = load_view_metadata(view)
    errors = list(loaded.get("errors") or [])
    if not loaded.get("ok"):
        return {"ok": False, "errors": errors, "metadata": loaded.get("metadata")}
    metadata = dict(loaded["metadata"])
    if metadata["assembly_name"] != str(getattr(assembly, "Name", "")):
        errors.append("exploded-view metadata names a different assembly")
    if metadata["view_name"] != str(getattr(view, "Name", "")):
        errors.append("exploded-view metadata names a different native view")
    if getattr(assembly, ACTIVE_VIEW_PROPERTY, None) is not view:
        errors.append("assembly active exploded-view identity is inconsistent")
    if int(getattr(assembly, CONTRACT_VERSION_PROPERTY, 0) or 0) != EXPLODED_VIEW_VERSION:
        errors.append("assembly exploded-view contract version is inconsistent")
    if str(getattr(view, CONFIGURATION_PROPERTY, "") or "") != metadata["configuration_id"]:
        errors.append("native exploded-view configuration identity is inconsistent")
    if str(getattr(view, STATE_PROPERTY, "") or "") != metadata["state"]:
        errors.append("native exploded-view state is inconsistent")
    if getattr(view, MANAGED_VIEW_PROPERTY, None) is not True:
        errors.append("native exploded-view managed marker is missing")
    if type(getattr(view, "Proxy", None)).__name__ != "ExplodedView":
        errors.append("native exploded-view application proxy is invalid")

    components = {
        str(getattr(child, "Name", "")): child
        for child in list(getattr(assembly, "Group", []) or [])
        if str(getattr(child, "TypeId", ""))
        in {"App::Link", "Assembly::AssemblyLink"}
    }
    view_groups = [
        child for child in list(getattr(assembly, "Group", []) or [])
        if str(getattr(child, "TypeId", "")) == "Assembly::ViewGroup"
    ]
    matching_groups = [
        group for group in view_groups
        if str(getattr(group, "Name", "")) == metadata["view_group_name"]
    ]
    if len(matching_groups) != 1 or view not in list(getattr(matching_groups[0], "Group", []) or []):
        errors.append("native exploded view is not in its exact Assembly::ViewGroup")

    steps = list(getattr(view, "Group", []) or [])
    steps_by_name = {str(getattr(step, "Name", "")): step for step in steps}
    expected_step_names = [item["step_name"] for item in metadata["components"]]
    if len(steps) != len(expected_step_names) or set(steps_by_name) != set(expected_step_names):
        errors.append("native exploded-view steps do not match metadata")
    final_placements: dict[Any, Any] = {}
    line_positions: list[Any] = []
    calculator = getattr(getattr(view, "Proxy", None), "_calculateExplodedPlacements", None)
    if not callable(calculator):
        errors.append("native exploded view cannot calculate placements")
    else:
        try:
            final_placements, line_positions = calculator(view)
        except Exception as exc:
            errors.append(f"native exploded-view placement calculation failed: {exc}")
    if len(line_positions) != len(metadata["components"]):
        errors.append("native exploded-view line count is inconsistent")
    expected_calculated_component_names = {
        item["component_name"]
        for item in metadata["components"]
        if components.get(item["component_name"]) is not None
    }
    calculated_component_names = {
        str(getattr(component, "Name", "")) for component in final_placements
    }
    calculated_by_name = {
        str(getattr(component, "Name", "")): placement
        for component, placement in final_placements.items()
    }
    if calculated_component_names != expected_calculated_component_names:
        errors.append(
            "native exploded-view calculated component set is inconsistent"
        )

    for item in metadata["components"]:
        name = item["component_name"]
        component = components.get(name)
        if component is None:
            errors.append(f"exploded-view component {name!r} is missing from the assembly")
            continue
        source_name = str(getattr(getattr(component, "LinkedObject", None), "Name", "") or "")
        if source_name != item["linked_object_name"]:
            errors.append(f"exploded-view component {name!r} linked source changed")
        if (
            not allow_component_placement_drift
            and not _placements_equal(
                placement_fact(component.Placement), item["assembled_placement"]
            )
        ):
            errors.append(f"exploded-view component {name!r} assembled placement changed")
        step = steps_by_name.get(item["step_name"])
        if step is None:
            continue
        if type(getattr(step, "Proxy", None)).__name__ != "ExplodedViewStep":
            errors.append(
                f"exploded-view step {step.Name!r} application proxy is invalid"
            )
        if getattr(step, MANAGED_STEP_PROPERTY, None) is not True:
            errors.append(f"exploded-view step {step.Name!r} managed marker is missing")
        if str(getattr(step, CONFIGURATION_PROPERTY, "") or "") != metadata["configuration_id"]:
            errors.append(f"exploded-view step {step.Name!r} configuration identity changed")
        if str(getattr(step, "MoveType", "")) != "Normal":
            errors.append(f"exploded-view step {step.Name!r} is not a normal move")
        reference = getattr(step, "References", None)
        try:
            root = reference[0]
            paths = list(reference[1] or [])
        except (TypeError, IndexError):
            root, paths = None, []
        if root is not assembly or paths != [f"{name}."]:
            errors.append(f"exploded-view step {step.Name!r} target identity changed")
        movement = getattr(step, "MovementTransform", None)
        if movement is None:
            errors.append(f"exploded-view step {step.Name!r} has no movement transform")
        else:
            actual_displacement = vector_fact(movement.Base)
            if not _vectors_equal(actual_displacement, item["displacement_mm"]):
                errors.append(f"exploded-view step {step.Name!r} distance or vector changed")
            identity = [0.0, 0.0, 0.0, 1.0]
            if not _quaternions_equal(list(movement.Rotation.Q), identity):
                errors.append(f"exploded-view step {step.Name!r} adds an unsupported rotation")
        calculated = calculated_by_name.get(name)
        if calculated is None:
            errors.append(
                f"exploded-view component {name!r} has no calculated placement"
            )
        elif not allow_component_placement_drift and not _placements_equal(
            placement_fact(calculated), item["exploded_placement"]
        ):
            errors.append(f"exploded-view component {name!r} calculated placement changed")
    return {
        "ok": not errors,
        "errors": errors,
        "metadata": metadata,
        "component_count": len(metadata["components"]),
        "line_count": len(line_positions),
    }


def validate_assembly_configurations(assembly: Any) -> dict[str, Any]:
    """Validate every VibeCAD-managed exploded view under one assembly."""
    errors: list[str] = []
    view_groups = [
        child for child in list(getattr(assembly, "Group", []) or [])
        if str(getattr(child, "TypeId", "")) == "Assembly::ViewGroup"
    ]
    views: list[Any] = []
    for group in view_groups:
        for child in list(getattr(group, "Group", []) or []):
            if (
                getattr(child, MANAGED_VIEW_PROPERTY, None) is True
                or str(getattr(child, "Name", "")).startswith("VibeCADExplodedView")
            ):
                views.append(child)
    has_contract = CONTRACT_VERSION_PROPERTY in set(
        getattr(assembly, "PropertiesList", []) or []
    ) or hasattr(assembly, CONTRACT_VERSION_PROPERTY)
    active = getattr(assembly, ACTIVE_VIEW_PROPERTY, None)
    if has_contract and active is None:
        errors.append(f"{assembly.Name}: active exploded-view metadata is missing")
    if active is not None and active not in views:
        errors.append(f"{assembly.Name}: active exploded-view object is not managed by this assembly")
    for view in views:
        validation = validate_native_configuration(assembly, view)
        errors.extend(f"{view.Name}: {error}" for error in validation["errors"])
    managed_steps = [
        child for child in list(getattr(assembly, "Group", []) or [])
        if getattr(child, MANAGED_STEP_PROPERTY, None) is True
        or str(getattr(child, "Name", "")).startswith("VibeCADExplodedMove")
    ]
    referenced_steps = {
        step for view in views for step in list(getattr(view, "Group", []) or [])
    }
    orphan_steps = [step.Name for step in managed_steps if step not in referenced_steps]
    if orphan_steps:
        errors.append(
            f"{assembly.Name}: exploded-view steps have no managed view: {', '.join(sorted(orphan_steps))}"
        )
    return {"ok": not errors, "errors": errors, "view_count": len(views)}


def _finite_vector(value: Any, *, context: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise ValueError(f"{context} must contain only finite x, y, and z values.")
    result: dict[str, float] = {}
    for axis in ("x", "y", "z"):
        raw = value.get(axis)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{context}.{axis} must be finite.")
        result[axis] = float(raw)
        if not math.isfinite(result[axis]):
            raise ValueError(f"{context}.{axis} must be finite.")
    return result


def _validate_placement_fact(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"position_mm", "rotation_xyzw"}:
        raise ValueError(f"{context} is invalid.")
    position = _finite_vector(value.get("position_mm"), context=f"{context}.position_mm")
    rotation = value.get("rotation_xyzw")
    if (
        not isinstance(rotation, list)
        or len(rotation) != 4
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            for item in rotation
        )
    ):
        raise ValueError(f"{context}.rotation_xyzw is invalid.")
    clean_rotation = [float(item) for item in rotation]
    magnitude = math.sqrt(sum(item * item for item in clean_rotation))
    if not math.isclose(magnitude, 1.0, rel_tol=0.0, abs_tol=1.0e-7):
        raise ValueError(f"{context}.rotation_xyzw is not normalized.")
    return {"position_mm": position, "rotation_xyzw": clean_rotation}


def _vectors_equal(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    try:
        return all(
            math.isclose(
                float(first[axis]), float(second[axis]),
                rel_tol=0.0, abs_tol=_PLACEMENT_TOLERANCE,
            )
            for axis in ("x", "y", "z")
        )
    except (KeyError, TypeError, ValueError):
        return False


def _quaternions_equal(first: Any, second: Any) -> bool:
    try:
        left = [float(item) for item in first]
        right = [float(item) for item in second]
    except (TypeError, ValueError):
        return False
    if len(left) != 4 or len(right) != 4:
        return False
    direct = all(
        math.isclose(a, b, rel_tol=0.0, abs_tol=_PLACEMENT_TOLERANCE)
        for a, b in zip(left, right)
    )
    negated = all(
        math.isclose(a, -b, rel_tol=0.0, abs_tol=_PLACEMENT_TOLERANCE)
        for a, b in zip(left, right)
    )
    return direct or negated


def _placements_equal(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    try:
        return _vectors_equal(first["position_mm"], second["position_mm"]) and _quaternions_equal(
            first["rotation_xyzw"], second["rotation_xyzw"]
        )
    except (KeyError, TypeError):
        return False
