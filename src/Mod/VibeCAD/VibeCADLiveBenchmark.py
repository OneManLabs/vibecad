# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fail-closed contracts for the Tier 1 live-provider benchmark."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from VibeCADBenchmark import BenchmarkEvidenceError, validate_case_attempt


LIVE_RUN_SCHEMA = "vibecad-live-benchmark-run-v1"
LIVE_RUN_VERSION = 1
LIVE_EXECUTOR = "vibecad-normal-session-live-v1"
LIVE_RUNTIME_IDENTITY_SCHEMA = "vibecad-live-runtime-identity-v1"
LIVE_RUNTIME_IDENTITY_VERSION = 1
LIVE_SUPPORTED_PROVIDERS = ("openai",)
LIVE_SESSION_CALLS = 7
LIVE_PROVIDER_TURNS_PER_CASE = 12
LIVE_VISIBLE_RETRY_EVENTS_PER_CASE = 4
LIVE_TOOL_CALLS_PER_CASE = 48
LIVE_PROVIDER_TIMEOUT_SECONDS = 120.0
LIVE_CASE_TOTAL_TIMEOUT_SECONDS = 180.0
LIVE_SDK_RETRIES_PER_REQUEST = 2
LIVE_MAX_REQUEST_BYTES = 150_000
LIVE_MAX_OUTPUT_TOKENS_PER_REQUEST = 20_000
LIVE_TOTAL_TOKENS_PER_CASE = 200_000
LIVE_WORST_CASE_API_ATTEMPTS = (
    LIVE_SESSION_CALLS
    * LIVE_PROVIDER_TURNS_PER_CASE
    * (1 + LIVE_SDK_RETRIES_PER_REQUEST)
)
LIVE_LIMITS = MappingProxyType(
    {
        "session_calls": LIVE_SESSION_CALLS,
        "provider_turns_per_case": LIVE_PROVIDER_TURNS_PER_CASE,
        "visible_retry_events_per_case": LIVE_VISIBLE_RETRY_EVENTS_PER_CASE,
        "tool_calls_per_case": LIVE_TOOL_CALLS_PER_CASE,
        "provider_timeout_seconds": LIVE_PROVIDER_TIMEOUT_SECONDS,
        "case_total_timeout_seconds": LIVE_CASE_TOTAL_TIMEOUT_SECONDS,
        "sdk_retries_per_request": LIVE_SDK_RETRIES_PER_REQUEST,
        "worst_case_api_attempts": LIVE_WORST_CASE_API_ATTEMPTS,
        "max_request_bytes": LIVE_MAX_REQUEST_BYTES,
        "max_output_tokens_per_request": LIVE_MAX_OUTPUT_TOKENS_PER_REQUEST,
        "total_tokens_per_case": LIVE_TOTAL_TOKENS_PER_CASE,
    }
)
TIER1_CASE_IDS = (
    "t1_exact_box",
    "t1_centered_hole",
    "t1_round_edges",
    "t1_hollow_enclosure",
    "t1_change_dimension",
    "t1_mirror_feature",
    "t1_export_stl",
)
READINESS_MAX_AGE_SECONDS = 120
READINESS_FUTURE_TOLERANCE_SECONDS = 5


def _utc_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BenchmarkEvidenceError(f"{field} must be a UTC timestamp that ends in Z.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise BenchmarkEvidenceError(f"{field} is not a valid UTC timestamp.") from exc
    if parsed.utcoffset() != timedelta(0):
        raise BenchmarkEvidenceError(f"{field} must use UTC.")
    return parsed


def _hex_value(value: Any, field: str, length: int) -> str:
    if not isinstance(value, str):
        raise BenchmarkEvidenceError(f"{field} must be a hex string.")
    clean = value.strip().lower()
    if value != clean or len(clean) != length or any(
        character not in "0123456789abcdef" for character in clean
    ):
        raise BenchmarkEvidenceError(f"{field} must be a lower-case {length}-character hex value.")
    return clean


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of one local artifact."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def exact_volume_evidence(
    shape: Any, expected_volume: float, *, tolerance: float = 1e-3
) -> dict[str, Any]:
    """Compare a native shape volume with one exact metric requirement."""

    observed = float(getattr(shape, "Volume", 0.0) or 0.0)
    difference = abs(observed - float(expected_volume))
    return {
        "passed": difference <= tolerance,
        "expected_volume_mm3": float(expected_volume),
        "observed_volume_mm3": observed,
        "absolute_error_mm3": difference,
        "tolerance_mm3": tolerance,
    }


def stl_export_evidence(
    path: Path | None,
    expected_dimensions: Iterable[float],
    *,
    tolerance: float = 1e-4,
) -> dict[str, Any]:
    """Reopen one STL and verify its mesh, bounds, and component count."""

    expected = sorted(float(value) for value in expected_dimensions)
    evidence: dict[str, Any] = {
        "passed": False,
        "path": str(path) if path is not None else None,
        "sha256": None,
        "size_bytes": 0,
        "facet_count": 0,
        "point_count": 0,
        "component_count": 0,
        "is_solid": False,
        "observed_bounds_mm": [],
        "expected_bounds_mm": expected,
        "error": None,
    }
    if path is None or path.suffix.lower() != ".stl" or not path.is_file():
        evidence["error"] = "The exported STL file is missing or has the wrong extension."
        return evidence
    evidence["size_bytes"] = path.stat().st_size
    evidence["sha256"] = sha256_file(path)
    try:
        import Mesh

        mesh = Mesh.Mesh(str(path))
        facets = int(mesh.CountFacets)
        points = int(mesh.CountPoints)
        components = int(mesh.countComponents())
        is_solid = bool(mesh.isSolid())
        bounds = sorted(
            [
                float(mesh.BoundBox.XLength),
                float(mesh.BoundBox.YLength),
                float(mesh.BoundBox.ZLength),
            ]
        )
        dimensions_match = len(bounds) == len(expected) and all(
            abs(observed - required) <= tolerance
            for observed, required in zip(bounds, expected)
        )
        evidence.update(
            facet_count=facets,
            point_count=points,
            component_count=components,
            is_solid=is_solid,
            observed_bounds_mm=bounds,
            passed=(
                facets > 0
                and points > 0
                and components == 1
                and is_solid
                and dimensions_match
            ),
        )
    except Exception as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
    return evidence


def centered_hole_evidence(shape: Any, radius: float) -> dict[str, Any]:
    """Verify circular hole edges are centered and use the through axis."""

    expected_x = (shape.BoundBox.XMin + shape.BoundBox.XMax) / 2
    expected_y = (shape.BoundBox.YMin + shape.BoundBox.YMax) / 2
    circles = []
    for edge in list(shape.Edges):
        curve = getattr(edge, "Curve", None)
        value = getattr(curve, "Radius", None)
        center = getattr(curve, "Center", None)
        axis = getattr(curve, "Axis", None)
        if not isinstance(value, (int, float)) or abs(float(value) - radius) > 1e-5:
            continue
        if center is None or axis is None:
            continue
        circles.append(
            {
                "center": [float(center.x), float(center.y), float(center.z)],
                "axis": [float(axis.x), float(axis.y), float(axis.z)],
                "centered_xy": (
                    abs(float(center.x) - expected_x) <= 1e-5
                    and abs(float(center.y) - expected_y) <= 1e-5
                ),
                "through_axis_z": abs(abs(float(axis.z)) - 1.0) <= 1e-5,
            }
        )
    passed = len(circles) >= 2 and all(
        item["centered_xy"] and item["through_axis_z"] for item in circles
    )
    surface_levels = {round(item["center"][2], 5) for item in circles}
    through_surfaces = {
        round(float(shape.BoundBox.ZMin), 5),
        round(float(shape.BoundBox.ZMax), 5),
    } <= surface_levels
    passed = passed and through_surfaces
    return {
        "passed": passed,
        "expected_center_xy": [expected_x, expected_y],
        "through_surfaces": through_surfaces,
        "circular_surface_levels_mm": sorted(surface_levels),
        "circular_edges": circles,
    }


def open_top_aperture_evidence(
    shape: Any, wall_thickness: float, *, tolerance: float = 1e-5
) -> dict[str, Any]:
    """Verify an enclosure has a centered inner boundary in its top rim."""

    bounds = shape.BoundBox
    outer_width = float(bounds.XLength)
    outer_depth = float(bounds.YLength)
    inner_width = outer_width - 2 * wall_thickness
    inner_depth = outer_depth - 2 * wall_thickness
    center_x = (float(bounds.XMin) + float(bounds.XMax)) / 2
    center_y = (float(bounds.YMin) + float(bounds.YMax)) / 2
    top_z = float(bounds.ZMax)
    bottom_z = float(bounds.ZMin) + wall_thickness
    cavity_height = top_z - bottom_z
    expected_rim_area = outer_width * outer_depth - inner_width * inner_depth
    candidates: list[dict[str, Any]] = []
    for face in list(shape.Faces):
        vertices = list(getattr(face, "Vertexes", []) or [])
        if not vertices or any(
            abs(float(vertex.Point.z) - top_z) > tolerance for vertex in vertices
        ):
            continue
        wires = list(getattr(face, "Wires", []) or [])
        boundaries = []
        for wire in wires:
            wire_bounds = wire.BoundBox
            wire_center_x = (
                float(wire_bounds.XMin) + float(wire_bounds.XMax)
            ) / 2
            wire_center_y = (
                float(wire_bounds.YMin) + float(wire_bounds.YMax)
            ) / 2
            boundaries.append(
                {
                    "width_mm": float(wire_bounds.XLength),
                    "depth_mm": float(wire_bounds.YLength),
                    "center_xy": [wire_center_x, wire_center_y],
                }
            )
        outer_boundary = any(
            abs(item["width_mm"] - outer_width) <= tolerance
            and abs(item["depth_mm"] - outer_depth) <= tolerance
            and abs(item["center_xy"][0] - center_x) <= tolerance
            and abs(item["center_xy"][1] - center_y) <= tolerance
            for item in boundaries
        )
        inner_boundary = any(
            abs(item["width_mm"] - inner_width) <= tolerance
            and abs(item["depth_mm"] - inner_depth) <= tolerance
            and abs(item["center_xy"][0] - center_x) <= tolerance
            and abs(item["center_xy"][1] - center_y) <= tolerance
            for item in boundaries
        )
        candidate = {
            "face_area_mm2": float(face.Area),
            "boundary_count": len(boundaries),
            "boundaries": boundaries,
            "outer_boundary": outer_boundary,
            "inner_boundary": inner_boundary,
            "rim_area_matches": (
                abs(float(face.Area) - expected_rim_area) <= tolerance
            ),
        }
        candidate["passed"] = (
            len(boundaries) == 2
            and outer_boundary
            and inner_boundary
            and candidate["rim_area_matches"]
        )
        candidates.append(candidate)
    cavity_bottom_candidates = []
    inner_wall_sides: set[str] = set()
    expected_inner_area = inner_width * inner_depth
    expected_x_sides = {
        "x_min": center_x - inner_width / 2,
        "x_max": center_x + inner_width / 2,
    }
    expected_y_sides = {
        "y_min": center_y - inner_depth / 2,
        "y_max": center_y + inner_depth / 2,
    }
    for face in list(shape.Faces):
        face_bounds = face.BoundBox
        face_area = float(face.Area)
        if (
            abs(float(face_bounds.ZMin) - bottom_z) <= tolerance
            and abs(float(face_bounds.ZMax) - bottom_z) <= tolerance
            and abs(float(face_bounds.XLength) - inner_width) <= tolerance
            and abs(float(face_bounds.YLength) - inner_depth) <= tolerance
        ):
            bottom_center = [
                (float(face_bounds.XMin) + float(face_bounds.XMax)) / 2,
                (float(face_bounds.YMin) + float(face_bounds.YMax)) / 2,
            ]
            cavity_bottom_candidates.append(
                {
                    "area_mm2": face_area,
                    "center_xy": bottom_center,
                    "passed": (
                        abs(face_area - expected_inner_area) <= tolerance
                        and abs(bottom_center[0] - center_x) <= tolerance
                        and abs(bottom_center[1] - center_y) <= tolerance
                    ),
                }
            )
        if (
            abs(float(face_bounds.ZMin) - bottom_z) > tolerance
            or abs(float(face_bounds.ZMax) - top_z) > tolerance
        ):
            continue
        if (
            float(face_bounds.XLength) <= tolerance
            and abs(float(face_bounds.YLength) - inner_depth) <= tolerance
            and abs(face_area - inner_depth * cavity_height) <= tolerance
        ):
            x_value = (float(face_bounds.XMin) + float(face_bounds.XMax)) / 2
            for side, expected in expected_x_sides.items():
                if abs(x_value - expected) <= tolerance:
                    inner_wall_sides.add(side)
        if (
            float(face_bounds.YLength) <= tolerance
            and abs(float(face_bounds.XLength) - inner_width) <= tolerance
            and abs(face_area - inner_width * cavity_height) <= tolerance
        ):
            y_value = (float(face_bounds.YMin) + float(face_bounds.YMax)) / 2
            for side, expected in expected_y_sides.items():
                if abs(y_value - expected) <= tolerance:
                    inner_wall_sides.add(side)
    uniform_cavity = (
        any(item["passed"] for item in cavity_bottom_candidates)
        and inner_wall_sides == {"x_min", "x_max", "y_min", "y_max"}
    )
    return {
        "passed": any(item["passed"] for item in candidates) and uniform_cavity,
        "top_z_mm": top_z,
        "expected_center_xy": [center_x, center_y],
        "expected_outer_boundary_mm": [outer_width, outer_depth],
        "expected_inner_boundary_mm": [inner_width, inner_depth],
        "expected_rim_area_mm2": expected_rim_area,
        "top_face_candidates": candidates,
        "cavity_bottom_z_mm": bottom_z,
        "cavity_bottom_candidates": cavity_bottom_candidates,
        "inner_wall_sides": sorted(inner_wall_sides),
        "uniform_cavity": uniform_cavity,
    }


def all_edge_fillet_evidence(feature: Any) -> dict[str, Any]:
    """Verify a native fillet references every edge of the source cube."""

    if str(getattr(feature, "TypeId", "")) != "PartDesign::Fillet":
        return {"passed": False, "reason": "The final feature is not a native fillet."}
    base = getattr(feature, "Base", None)
    base_object = None
    subelements: list[str] = []
    if isinstance(base, tuple) and len(base) == 2:
        base_object = base[0]
        subelements = [str(item) for item in list(base[1] or [])]
    base_edge_count = len(
        list(getattr(getattr(base_object, "Shape", None), "Edges", []) or [])
    )
    expected_edges = {
        f"Edge{index}" for index in range(1, base_edge_count + 1)
    }
    passed = (
        base_object is not None
        and base_edge_count == 12
        and len(subelements) == base_edge_count
        and set(subelements) == expected_edges
    )
    return {
        "passed": passed,
        "base_object": getattr(base_object, "Name", None),
        "base_edge_count": base_edge_count,
        "filleted_subelements": subelements,
    }


def mirrored_link_evidence(
    feature: Any, source_name: str, mirror_plane_name: str
) -> dict[str, Any]:
    """Verify a native mirrored feature keeps a link to the selected source."""

    linked_names: set[str] = set()
    for property_name in ("Originals", "OriginalFeatures", "Features", "BaseFeature"):
        value = getattr(feature, property_name, None)
        values = list(value) if isinstance(value, (list, tuple)) else [value]
        for item in values:
            candidate = item[0] if isinstance(item, tuple) and item else item
            name = str(getattr(candidate, "Name", "") or "")
            if name:
                linked_names.add(name)
    plane = getattr(feature, "MirrorPlane", None)
    plane_object = plane[0] if isinstance(plane, tuple) and plane else plane
    observed_plane_name = str(getattr(plane_object, "Name", "") or "")
    passed = (
        str(getattr(feature, "TypeId", "")) == "PartDesign::Mirrored"
        and source_name in linked_names
        and bool(mirror_plane_name)
        and observed_plane_name == mirror_plane_name
    )
    return {
        "passed": passed,
        "feature_type": str(getattr(feature, "TypeId", "")),
        "source_feature": source_name,
        "linked_features": sorted(linked_names),
        "mirror_plane": observed_plane_name,
        "expected_mirror_plane": mirror_plane_name,
    }


def symmetric_through_holes_evidence(
    shape: Any,
    radius: float,
    expected_centers: Iterable[tuple[float, float]],
    *,
    tolerance: float = 1e-5,
) -> dict[str, Any]:
    """Verify exact XY hole centers on both outer Z surfaces."""

    levels = (float(shape.BoundBox.ZMin), float(shape.BoundBox.ZMax))
    observed = []
    for edge in list(shape.Edges):
        curve = getattr(edge, "Curve", None)
        value = getattr(curve, "Radius", None)
        center = getattr(curve, "Center", None)
        axis = getattr(curve, "Axis", None)
        if (
            not isinstance(value, (int, float))
            or abs(float(value) - radius) > tolerance
            or center is None
            or axis is None
            or abs(abs(float(axis.z)) - 1.0) > tolerance
        ):
            continue
        observed.append(
            (float(center.x), float(center.y), float(center.z))
        )
    center_checks = []
    for expected_x, expected_y in expected_centers:
        matched_levels = sorted(
            level
            for level in levels
            if any(
                abs(x - expected_x) <= tolerance
                and abs(y - expected_y) <= tolerance
                and abs(z - level) <= tolerance
                for x, y, z in observed
            )
        )
        center_checks.append(
            {
                "center_xy": [expected_x, expected_y],
                "surface_levels_mm": matched_levels,
                "passed": len(matched_levels) == 2,
            }
        )
    return {
        "passed": bool(center_checks) and all(
            item["passed"] for item in center_checks
        ),
        "radius_mm": radius,
        "expected_surface_levels_mm": list(levels),
        "expected_centers": center_checks,
        "observed_circular_centers": [list(item) for item in observed],
    }


def changed_constraint_evidence(
    document: Any, fixture: Mapping[str, Any]
) -> dict[str, Any]:
    """Verify the original width constraint was edited in place."""

    sketch = document.getObject(str(fixture.get("sketch") or ""))
    index = fixture.get("width_constraint_index")
    if sketch is None or not isinstance(index, int):
        return {"passed": False, "reason": "The original fixture sketch is missing."}
    constraints = list(sketch.Constraints)
    if not 0 <= index < len(constraints):
        return {"passed": False, "reason": "The original width constraint is missing."}
    constraint = constraints[index]
    value = getattr(constraint, "Value", None)
    expected_name = str(fixture.get("width_constraint_name") or "")
    observed_name = str(getattr(constraint, "Name", "") or "")
    passed = (
        str(getattr(constraint, "Type", "")) == "DistanceX"
        and isinstance(value, (int, float))
        and abs(abs(float(value)) - 55) <= 1e-6
        and bool(expected_name)
        and observed_name == expected_name
        and int(sketch.GeometryCount) == fixture.get("sketch_geometry_count")
        and int(sketch.ConstraintCount) == fixture.get("sketch_constraint_count")
    )
    return {
        "passed": passed,
        "sketch": sketch.Name,
        "constraint_index": index,
        "constraint_type": str(getattr(constraint, "Type", "")),
        "constraint_name": observed_name,
        "expected_constraint_name": expected_name,
        "constraint_value_mm": value,
        "geometry_count": int(sketch.GeometryCount),
        "constraint_count": int(sketch.ConstraintCount),
    }


def visible_solid_target_evidence(document: Any, target: Any) -> dict[str, Any]:
    """Require one visible solid owner and bind it to the selected target."""

    def solid_owner(obj: Any) -> Any:
        if str(getattr(obj, "TypeId", "")) == "PartDesign::Body":
            return obj
        current = obj
        visited: set[int] = set()
        while id(current) not in visited:
            visited.add(id(current))
            getter = getattr(current, "getParentGeoFeatureGroup", None)
            parent = getter() if callable(getter) else None
            if parent is None:
                break
            if str(getattr(parent, "TypeId", "")) == "PartDesign::Body":
                return parent
            current = parent
        return obj

    def is_visible(obj: Any) -> bool:
        view = getattr(obj, "ViewObject", None)
        value = getattr(view, "Visibility", None) if view is not None else None
        return value if isinstance(value, bool) else True

    owners: dict[str, dict[str, Any]] = {}
    for obj in list(getattr(document, "Objects", []) or []):
        shape = getattr(obj, "Shape", None)
        try:
            has_solid = bool(
                shape is not None
                and not shape.isNull()
                and list(shape.Solids)
            )
        except Exception:
            has_solid = False
        if not has_solid:
            continue
        owner = solid_owner(obj)
        if not is_visible(owner):
            continue
        owner_name = str(getattr(owner, "Name", "") or "")
        if not owner_name:
            owner_name = f"unnamed:{id(owner)}"
        item = owners.setdefault(
            owner_name,
            {
                "name": str(getattr(owner, "Name", "") or ""),
                "type_id": str(getattr(owner, "TypeId", "") or ""),
                "solid_objects": [],
            },
        )
        item["solid_objects"].append(str(getattr(obj, "Name", "") or ""))

    target_owner = solid_owner(target) if target is not None else None
    target_owner_name = str(getattr(target_owner, "Name", "") or "")
    visible_owners = sorted(owners)
    return {
        "passed": len(visible_owners) == 1 and target_owner_name in owners,
        "target": str(getattr(target, "Name", "") or ""),
        "target_owner": target_owner_name,
        "visible_solid_owner_count": len(visible_owners),
        "visible_solid_owners": [owners[name] for name in visible_owners],
    }


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write one JSON object without exposing a partial file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, default=str)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_live_readiness(
    report: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Require a fresh credential check that sent no prompt or CAD data."""

    if not isinstance(report, Mapping):
        raise BenchmarkEvidenceError("The provider readiness result must be an object.")
    expected_fields = {
        "schema",
        "version",
        "created_at",
        "can_call_provider",
        "prompt_sent",
        "document_data_sent",
        "credential_validation_performed",
        "model_validation_performed",
        "model_available",
        "stage",
        "provider",
        "model",
        "auth_status",
        "auth_source",
        "online_by_default",
        "endpoint_identity",
        "endpoint_sha256",
        "credential_binding_nonce",
        "credential_fingerprint_algorithm",
        "credential_fingerprint",
        "process_timed_out",
        "process_exit_code",
        "ready_for_live_benchmark",
    }
    if set(report) != expected_fields:
        raise BenchmarkEvidenceError(
            "The verified provider readiness result contains missing or unknown fields."
        )
    checks = (
        (report.get("schema") == "vibecad-provider-readiness-v1", "schema"),
        (report.get("version") == 1, "version"),
        (report.get("process_exit_code") == 0, "process exit code"),
        (report.get("process_timed_out") is False, "timeout state"),
        (
            report.get("credential_validation_performed") is True,
            "credential validation state",
        ),
        (
            report.get("model_validation_performed") is True,
            "model validation state",
        ),
        (report.get("model_available") is True, "selected model availability"),
        (report.get("auth_status") == "verified", "verified auth state"),
        (report.get("can_call_provider") is True, "provider call state"),
        (report.get("prompt_sent") is False, "prompt boundary"),
        (report.get("document_data_sent") is False, "CAD data boundary"),
        (report.get("ready_for_live_benchmark") is True, "live readiness state"),
        (report.get("stage") == "complete", "completion stage"),
    )
    for passed, name in checks:
        if not passed:
            raise BenchmarkEvidenceError(
                f"The provider readiness {name} is not valid for a live benchmark."
            )
    provider = report.get("provider")
    model = report.get("model")
    source = report.get("auth_source")
    if not isinstance(provider, str) or not provider.strip():
        raise BenchmarkEvidenceError("The provider readiness result has no provider ID.")
    if provider not in LIVE_SUPPORTED_PROVIDERS:
        raise BenchmarkEvidenceError(
            "The live benchmark supports only the bounded OpenAI API adapter."
        )
    if not isinstance(model, str) or not model.strip():
        raise BenchmarkEvidenceError("The provider readiness result has no model ID.")
    if not isinstance(source, str) or not source.strip() or source == "environment":
        raise BenchmarkEvidenceError(
            "The live benchmark needs a configured, non-ambient credential source."
        )
    endpoint = report.get("endpoint_identity")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise BenchmarkEvidenceError("The provider readiness endpoint identity is missing.")
    endpoint_sha256 = _hex_value(
        report.get("endpoint_sha256"), "endpoint_sha256", 64
    )
    if endpoint_sha256 != hashlib.sha256(endpoint.encode("utf-8")).hexdigest():
        raise BenchmarkEvidenceError("The provider readiness endpoint digest is invalid.")
    _hex_value(
        report.get("credential_binding_nonce"), "credential_binding_nonce", 64
    )
    if report.get("credential_fingerprint_algorithm") != "hmac-sha256-v1":
        raise BenchmarkEvidenceError("The credential fingerprint algorithm is invalid.")
    _hex_value(
        report.get("credential_fingerprint"), "credential_fingerprint", 64
    )
    created_at = _utc_datetime(report.get("created_at"), "readiness created_at")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise BenchmarkEvidenceError("The readiness comparison time must use UTC.")
    current = current.astimezone(timezone.utc)
    age = (current - created_at).total_seconds()
    if age < -READINESS_FUTURE_TOLERANCE_SECONDS:
        raise BenchmarkEvidenceError("The provider readiness result is from the future.")
    if age > READINESS_MAX_AGE_SECONDS:
        raise BenchmarkEvidenceError("The provider readiness result is stale.")
    return dict(report)


def readiness_digest(report: Mapping[str, Any]) -> str:
    """Bind a run to the exact readiness evidence used before execution."""

    if not isinstance(report, Mapping):
        raise BenchmarkEvidenceError("The provider readiness result must be an object.")
    encoded = json.dumps(
        dict(report), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def limits_digest(limits: Mapping[str, Any]) -> str:
    """Bind evidence and ratings to the exact immutable live limits."""

    if not isinstance(limits, Mapping) or dict(limits) != dict(LIVE_LIMITS):
        raise BenchmarkEvidenceError("The live benchmark limits are not the fixed contract.")
    encoded = json.dumps(
        dict(limits), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def recover_partial_case_metrics(
    partial: Mapping[str, Any], *, now_epoch: float | None = None
) -> dict[str, Any]:
    """Recover measured values for a failed case without replacing them with zero."""

    zero_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "cost_usd": None,
    }
    if (
        not isinstance(partial, Mapping)
        or partial.get("schema") != "vibecad-live-partial-metrics-v1"
    ):
        return {
            "elapsed_seconds": 0.0,
            "events": [],
            "question_count": 0,
            "unnecessary_question_count": 0,
            "retry_count": 0,
            "tool_call_count": 0,
            "api_request_count": 0,
            "provider_turns_observed": [],
            "usage_event_count": 0,
            "usage": zero_usage,
        }
    raw = partial.get("measurements")
    metrics = raw if isinstance(raw, Mapping) else {}

    def count(name: str) -> int:
        value = metrics.get(name)
        return (
            int(value)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else 0
        )

    elapsed = metrics.get("elapsed_seconds")
    elapsed_seconds = (
        float(elapsed)
        if isinstance(elapsed, (int, float))
        and not isinstance(elapsed, bool)
        and elapsed >= 0
        else 0.0
    )
    started_epoch = partial.get("started_at_epoch")
    if (
        now_epoch is not None
        and isinstance(started_epoch, (int, float))
        and not isinstance(started_epoch, bool)
    ):
        elapsed_seconds = max(
            elapsed_seconds, max(0.0, float(now_epoch) - float(started_epoch))
        )
    turns = metrics.get("provider_turns_observed")
    observed_turns = (
        sorted(
            value
            for value in turns
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
        )
        if isinstance(turns, list)
        else []
    )
    usage = metrics.get("usage")
    return {
        "elapsed_seconds": elapsed_seconds,
        "events": list(metrics.get("events") or []),
        "question_count": count("question_count"),
        "unnecessary_question_count": count("unnecessary_question_count"),
        "retry_count": count("retry_count"),
        "tool_call_count": count("tool_call_count"),
        "api_request_count": count("api_request_count"),
        "provider_turns_observed": observed_turns,
        "usage_event_count": count("usage_event_count"),
        "usage": dict(usage) if isinstance(usage, Mapping) else zero_usage,
    }


def persist_partial_metrics_checkpoint(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    path: Path,
    *,
    writer: Any = atomic_write_json,
) -> None:
    """Update recoverable memory before a fallible checkpoint write."""

    state["partial"] = dict(payload)
    writer(path, payload)


def require_complete_partial_usage(metrics: Mapping[str, Any]) -> None:
    """Reject a failed live attempt when a started request has unknown usage."""

    requests = metrics.get("api_request_count")
    usage_events = metrics.get("usage_event_count")
    if (
        isinstance(requests, int)
        and not isinstance(requests, bool)
        and requests > 0
        and (
            not isinstance(usage_events, int)
            or isinstance(usage_events, bool)
            or usage_events < requests
        )
    ):
        raise BenchmarkEvidenceError(
            "A started provider request has incomplete usage evidence; the live run is invalid."
        )


def validate_runtime_identity(
    identity: Mapping[str, Any], *, source_commit: str
) -> dict[str, Any]:
    """Validate the copied runtime identity before a provider can run."""

    if not isinstance(identity, Mapping):
        raise BenchmarkEvidenceError("The live runtime identity must be an object.")
    expected_fields = {
        "schema",
        "version",
        "source_commit",
        "module_file_count",
        "module_manifest_sha256",
        "gui_entry_sha256",
        "gui_runner_sha256",
        "case_catalog_sha256",
    }
    if set(identity) != expected_fields:
        raise BenchmarkEvidenceError(
            "The live runtime identity contains missing or unknown fields."
        )
    if (
        identity.get("schema") != LIVE_RUNTIME_IDENTITY_SCHEMA
        or identity.get("version") != LIVE_RUNTIME_IDENTITY_VERSION
    ):
        raise BenchmarkEvidenceError("The live runtime identity contract is invalid.")
    expected_commit = _hex_value(source_commit, "expected source_commit", 40)
    observed_commit = _hex_value(
        identity.get("source_commit"), "runtime source_commit", 40
    )
    if observed_commit != expected_commit:
        raise BenchmarkEvidenceError(
            "The live runtime identity does not match the source commit."
        )
    file_count = identity.get("module_file_count")
    if isinstance(file_count, bool) or not isinstance(file_count, int) or file_count <= 0:
        raise BenchmarkEvidenceError("The live runtime module file count is invalid.")
    _hex_value(
        identity.get("module_manifest_sha256"),
        "runtime module_manifest_sha256",
        64,
    )
    _hex_value(
        identity.get("gui_entry_sha256"), "runtime gui_entry_sha256", 64
    )
    _hex_value(
        identity.get("gui_runner_sha256"), "runtime gui_runner_sha256", 64
    )
    _hex_value(
        identity.get("case_catalog_sha256"), "runtime case_catalog_sha256", 64
    )
    return dict(identity)


def validate_unrated_live_run(
    run: Mapping[str, Any],
    *,
    expected_case_ids: Iterable[str] = TIER1_CASE_IDS,
    require_runner_result: bool = False,
) -> dict[str, Any]:
    """Validate a complete raw run without converting it to a score."""

    if not isinstance(run, Mapping):
        raise BenchmarkEvidenceError("The live benchmark run must be an object.")
    expected_case_tuple = tuple(expected_case_ids)
    expected_fields = {
        "schema",
        "version",
        "created_at",
        "tier",
        "provider",
        "model",
        "executor",
        "source_commit",
        "readiness_sha256",
        "runtime_identity_sha256",
        "limits",
        "usage_summary",
        "case_runtime",
        "scored",
        "case_attempts",
    }
    if require_runner_result:
        expected_fields.add("runner")
    if set(run) != expected_fields:
        raise BenchmarkEvidenceError(
            "The live benchmark run contains missing or unknown top-level fields."
        )
    if run.get("schema") != LIVE_RUN_SCHEMA or run.get("version") != LIVE_RUN_VERSION:
        raise BenchmarkEvidenceError("The live benchmark run contract is invalid.")
    if run.get("tier") != 1:
        raise BenchmarkEvidenceError("The live benchmark run must use Tier 1.")
    _utc_datetime(run.get("created_at"), "live run created_at")
    provider = str(run.get("provider") or "").strip()
    model = str(run.get("model") or "").strip()
    source_commit = str(run.get("source_commit") or "").strip()
    executor = str(run.get("executor") or "").strip()
    if not all((provider, model, source_commit, executor)):
        raise BenchmarkEvidenceError("The live benchmark run identity is incomplete.")
    if provider not in LIVE_SUPPORTED_PROVIDERS:
        raise BenchmarkEvidenceError("The live benchmark provider is not supported.")
    _hex_value(source_commit, "source_commit", 40)
    if executor != LIVE_EXECUTOR:
        raise BenchmarkEvidenceError("The live benchmark executor is invalid.")
    _hex_value(run.get("readiness_sha256"), "readiness_sha256", 64)
    _hex_value(run.get("runtime_identity_sha256"), "runtime_identity_sha256", 64)
    if run.get("scored") is not False:
        raise BenchmarkEvidenceError("Raw live benchmark evidence must remain unscored.")
    limits = run.get("limits")
    expected_limit_fields = {
        "session_calls",
        "provider_turns_per_case",
        "visible_retry_events_per_case",
        "tool_calls_per_case",
        "provider_timeout_seconds",
        "case_total_timeout_seconds",
        "sdk_retries_per_request",
        "worst_case_api_attempts",
        "max_request_bytes",
        "max_output_tokens_per_request",
        "total_tokens_per_case",
    }
    if not isinstance(limits, Mapping) or set(limits) != expected_limit_fields:
        raise BenchmarkEvidenceError("The live benchmark execution limits are invalid.")
    if expected_limit_fields != set(LIVE_LIMITS):
        raise RuntimeError("The live benchmark limit validator is out of date.")
    limits_digest(limits)
    usage_fields = {
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
        "total_tokens",
    }
    usage_summary = run.get("usage_summary")
    if not isinstance(usage_summary, Mapping) or set(usage_summary) != usage_fields:
        raise BenchmarkEvidenceError("The live benchmark usage summary is invalid.")
    for field in usage_fields:
        value = usage_summary.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BenchmarkEvidenceError(f"The live benchmark usage field {field} is invalid.")
    if usage_summary["total_tokens"] < (
        usage_summary["input_tokens"] + usage_summary["output_tokens"]
    ):
        raise BenchmarkEvidenceError("The live benchmark token total is inconsistent.")
    case_runtime = run.get("case_runtime")
    if not isinstance(case_runtime, list) or len(case_runtime) != len(expected_case_tuple):
        raise BenchmarkEvidenceError("The live benchmark case runtime evidence is incomplete.")
    runtime_fields = {
        "case_id",
        "provider_class",
        "provider_turn_count",
        "provider_turns_observed",
        "api_request_count",
        "api_attempt_upper_bound",
        "tool_call_count",
        "retry_count",
        "usage_event_count",
        "fixture",
        "final_sha256",
        "isolated_validation_ok",
    }
    runtime_ids = []
    for item in case_runtime:
        if not isinstance(item, Mapping) or set(item) != runtime_fields:
            raise BenchmarkEvidenceError("One live case runtime record is invalid.")
        runtime_ids.append(item.get("case_id"))
        if not isinstance(item.get("provider_class"), str):
            raise BenchmarkEvidenceError("The live provider class is invalid.")
        for field in (
            "provider_turn_count",
            "api_request_count",
            "api_attempt_upper_bound",
            "tool_call_count",
            "retry_count",
            "usage_event_count",
        ):
            value = item.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise BenchmarkEvidenceError(f"The live case runtime {field} is invalid.")
        turns = item.get("provider_turns_observed")
        if not isinstance(turns, list) or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in turns
        ):
            raise BenchmarkEvidenceError("The observed provider turns are invalid.")
        if item["provider_turn_count"] != max(turns, default=0):
            raise BenchmarkEvidenceError("The provider-turn count is inconsistent.")
        if item["provider_turn_count"] > limits["provider_turns_per_case"]:
            raise BenchmarkEvidenceError("A case exceeded the provider-turn limit.")
        if item["api_attempt_upper_bound"] != (
            item["api_request_count"] * (1 + limits["sdk_retries_per_request"])
        ):
            raise BenchmarkEvidenceError("The API-attempt upper bound is inconsistent.")
        if item["usage_event_count"] != item["api_request_count"]:
            raise BenchmarkEvidenceError(
                "A live case has incomplete request-to-usage coverage."
            )
        if item["tool_call_count"] > limits["tool_calls_per_case"]:
            raise BenchmarkEvidenceError("A case exceeded the tool-call limit.")
        if item["retry_count"] > limits["visible_retry_events_per_case"]:
            raise BenchmarkEvidenceError("A case exceeded the retry limit.")
        if not isinstance(item.get("fixture"), Mapping) or item["fixture"].get("kind") != "benchmark_setup":
            raise BenchmarkEvidenceError("The benchmark setup evidence is invalid.")
        _hex_value(
            item["fixture"].get("canonical_sha256"),
            "fixture canonical_sha256",
            64,
        )
        object_names = item["fixture"].get("object_names")
        if not isinstance(object_names, list) or any(
            not isinstance(name, str) or not name for name in object_names
        ):
            raise BenchmarkEvidenceError("The benchmark setup object identities are invalid.")
        _hex_value(item.get("final_sha256"), "case final_sha256", 64)
        if not isinstance(item.get("isolated_validation_ok"), bool):
            raise BenchmarkEvidenceError("The isolated validation result is invalid.")
    if tuple(runtime_ids) != expected_case_tuple:
        raise BenchmarkEvidenceError("The live case runtime order is invalid.")

    attempts = run.get("case_attempts")
    if not isinstance(attempts, list):
        raise BenchmarkEvidenceError("The live benchmark run has no case-attempt list.")
    validated_attempts = [
        validate_case_attempt(item, allow_unrated_live=True) for item in attempts
    ]
    for attempt in validated_attempts:
        if attempt["passed"] and (
            attempt["normalized_usage"]["total_tokens"]
            > limits["total_tokens_per_case"]
        ):
            raise BenchmarkEvidenceError("A passed case exceeded the token-use limit.")
        if attempt["passed"] and (
            attempt["elapsed_seconds"] > limits["case_total_timeout_seconds"]
        ):
            raise BenchmarkEvidenceError("A passed case exceeded the elapsed-time limit.")
    runtime_by_case = {item["case_id"]: item for item in case_runtime}
    for attempt in validated_attempts:
        runtime = runtime_by_case[attempt["case_id"]]
        if attempt["passed"] and (
            runtime["provider_class"] != "OpenAIProvider"
            or runtime["api_request_count"] == 0
            or runtime["api_request_count"] != runtime["provider_turn_count"]
            or runtime["provider_turn_count"] == 0
            or runtime["provider_turns_observed"]
            != list(range(1, runtime["provider_turn_count"] + 1))
            or attempt["normalized_usage"]["total_tokens"] == 0
            or runtime["isolated_validation_ok"] is not True
        ):
            raise BenchmarkEvidenceError(
                "A passed live case lacks provider-turn, usage, or isolated-validation evidence."
            )
    for field in usage_fields:
        observed = sum(
            attempt["normalized_usage"][field] for attempt in validated_attempts
        )
        if usage_summary[field] != observed:
            raise BenchmarkEvidenceError(
                f"The live benchmark aggregate usage field {field} is inconsistent."
            )
    expected = set(expected_case_tuple)
    keys: set[tuple[str, int]] = set()
    for attempt in validated_attempts:
        key = (attempt["case_id"], attempt["attempt"])
        if key in keys:
            raise BenchmarkEvidenceError(
                f"Duplicate live case evidence for {key[0]} attempt {key[1]}."
            )
        keys.add(key)
        adherence = attempt.get("instruction_adherence")
        if not isinstance(adherence, Mapping) or adherence.get("status") != "not_rated":
            raise BenchmarkEvidenceError("Raw live case evidence must be unrated.")
        identity = (
            attempt.get("tier") == 1
            and attempt.get("provider") == provider
            and attempt.get("model") == model
            and attempt.get("executor") == executor
            and attempt.get("source_commit") == source_commit
            and attempt.get("live_model_score") is True
        )
        if not identity:
            raise BenchmarkEvidenceError(
                f"Live case {attempt.get('case_id')} has mismatched run identity."
            )
    observed = {case_id for case_id, attempt in keys if attempt == 1}
    if keys != {(case_id, 1) for case_id in expected} or observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise BenchmarkEvidenceError(
            f"The Tier 1 live case set is incomplete; missing={missing}, extra={extra}."
        )

    runner = run.get("runner")
    if require_runner_result:
        if not isinstance(runner, Mapping):
            raise BenchmarkEvidenceError("The live benchmark runner result is missing.")
        if set(runner) != {
            "attempt",
            "case_evidence_passed",
            "gui_runner_exit_code",
            "gui_runner_reported_ok",
        }:
            raise BenchmarkEvidenceError("The live benchmark runner result is invalid.")
        if runner.get("attempt") != 1:
            raise BenchmarkEvidenceError("The live benchmark runner attempt is invalid.")
        if not isinstance(runner.get("case_evidence_passed"), bool):
            raise BenchmarkEvidenceError("The case evidence runner result is invalid.")
        if isinstance(runner.get("gui_runner_exit_code"), bool) or not isinstance(
            runner.get("gui_runner_exit_code"), int
        ):
            raise BenchmarkEvidenceError("The GUI runner exit code is invalid.")
        if not isinstance(runner.get("gui_runner_reported_ok"), bool):
            raise BenchmarkEvidenceError("The GUI runner result is invalid.")
        expected_runner_pass = (
            all(attempt["passed"] for attempt in validated_attempts)
            and runner.get("gui_runner_exit_code") == 0
            and runner.get("gui_runner_reported_ok") is True
        )
        if runner.get("case_evidence_passed") is not expected_runner_pass:
            raise BenchmarkEvidenceError(
                "The live benchmark runner result does not match its cases and process exit."
            )
    return dict(run)
