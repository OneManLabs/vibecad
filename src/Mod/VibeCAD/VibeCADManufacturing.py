# SPDX-License-Identifier: LGPL-2.1-or-later
"""Structured heuristic manufacturing checks for native CAD shapes."""

from __future__ import annotations

import math
from typing import Any, Iterable


FDM_REPORT_SCHEMA = "vibecad-fdm-analysis-v1"
FDM_REPORT_VERSION = 1
_AXES = {
    "x": (1.0, 0.0, 0.0),
    "y": (0.0, 1.0, 0.0),
    "z": (0.0, 0.0, 1.0),
}


def _dot(left: Iterable[float], right: Iterable[float]) -> float:
    return sum(float(a) * float(b) for a, b in zip(left, right))


def _face_normal(face: Any) -> tuple[float, float, float] | None:
    try:
        u0, u1, v0, v1 = face.ParameterRange
        normal = face.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
        magnitude = math.sqrt(normal.x * normal.x + normal.y * normal.y + normal.z * normal.z)
        if magnitude <= 1.0e-12:
            return None
        return normal.x / magnitude, normal.y / magnitude, normal.z / magnitude
    except Exception:
        return None


def _face_center(face: Any) -> tuple[float, float, float]:
    center = face.CenterOfMass
    return float(center.x), float(center.y), float(center.z)


def _face_records(shape: Any) -> list[dict[str, Any]]:
    records = []
    for face in list(getattr(shape, "Faces", []) or []):
        normal = _face_normal(face)
        if normal is None:
            continue
        surface = getattr(face, "Surface", None)
        radius = getattr(surface, "Radius", None)
        hole_diameter = None
        orientation = str(getattr(face, "Orientation", "") or "").lower()
        if radius is not None and (not orientation or "reversed" in orientation):
            try:
                candidate = 2.0 * float(radius)
                if candidate > 0:
                    hole_diameter = candidate
            except (TypeError, ValueError):
                pass
        records.append(
            {
                "normal": normal,
                "center": _face_center(face),
                "area": float(getattr(face, "Area", 0.0) or 0.0),
                "hole_diameter": hole_diameter,
            }
        )
    return records


def _planar_positions(
    shape: Any, records: list[dict[str, Any]] | None = None
) -> dict[str, list[float]]:
    positions = {axis: [] for axis in _AXES}
    for record in records if records is not None else _face_records(shape):
        normal = record["normal"]
        center = record["center"]
        for index, axis in enumerate(("x", "y", "z")):
            if abs(abs(normal[index]) - 1.0) < 1.0e-6:
                value = center[index]
                if not any(abs(value - prior) < 1.0e-5 for prior in positions[axis]):
                    positions[axis].append(value)
    return {axis: sorted(values) for axis, values in positions.items()}


def heuristic_minimum_planar_spacing(
    shape: Any, *, _records: list[dict[str, Any]] | None = None
) -> float | None:
    gaps = []
    for values in _planar_positions(shape, _records).values():
        gaps.extend(
            right - left
            for left, right in zip(values, values[1:])
            if right - left > 1.0e-5
        )
    return min(gaps) if gaps else None


def cylindrical_hole_diameters(shape: Any) -> list[float]:
    diameters: list[float] = []
    for face in list(getattr(shape, "Faces", []) or []):
        surface = getattr(face, "Surface", None)
        radius = getattr(surface, "Radius", None)
        if radius is None:
            continue
        try:
            diameter = 2.0 * float(radius)
        except (TypeError, ValueError):
            continue
        # A concave cylindrical face is normally a hole. Orientation values
        # differ between OpenCASCADE bindings, so accept only REVERSED when it
        # is available and otherwise retain the result as a candidate.
        orientation = str(getattr(face, "Orientation", "") or "").lower()
        if orientation and "reversed" not in orientation:
            continue
        if diameter > 0 and not any(abs(diameter - item) < 1.0e-5 for item in diameters):
            diameters.append(diameter)
    return sorted(diameters)


def orientation_score(
    shape: Any,
    axis: str,
    *,
    overhang_angle_degrees: float,
    _records: list[dict[str, Any]] | None = None,
    _bounds: Any | None = None,
) -> dict[str, Any]:
    build = _AXES[axis]
    cosine_limit = math.cos(math.radians(overhang_angle_degrees))
    if isinstance(_bounds, dict):
        bounds = _bounds
    else:
        native_bounds = _bounds if _bounds is not None else shape.BoundBox
        bounds = {
            "x_min": float(native_bounds.XMin),
            "y_min": float(native_bounds.YMin),
            "z_min": float(native_bounds.ZMin),
            "x_length": float(native_bounds.XLength),
            "y_length": float(native_bounds.YLength),
            "z_length": float(native_bounds.ZLength),
        }
    minimum = bounds[f"{axis}_min"]
    overhang_area = 0.0
    base_area = 0.0
    evaluated = 0
    for record in _records if _records is not None else _face_records(shape):
        normal = record["normal"]
        evaluated += 1
        downward = _dot(normal, build)
        coordinate = record["center"][{"x": 0, "y": 1, "z": 2}[axis]]
        area = record["area"]
        if downward < -cosine_limit:
            if abs(coordinate - minimum) < 1.0e-4:
                base_area += area
            else:
                overhang_area += area
    height = bounds[f"{axis}_length"]
    return {
        "axis": axis,
        "evaluated_face_count": evaluated,
        "unsupported_overhang_area_mm2": overhang_area,
        "base_contact_area_mm2": base_area,
        "build_height_mm": float(height),
        "heuristic_score": overhang_area + 0.01 * float(height),
    }


def analyze_fdm_shape(
    shape: Any,
    *,
    object_name: str,
    minimum_wall_mm: float = 1.2,
    overhang_angle_degrees: float = 45.0,
    minimum_hole_mm: float = 2.0,
) -> dict[str, Any]:
    if shape is None or shape.isNull():
        raise RuntimeError(f"Object {object_name} has no usable shape.")
    valid = bool(shape.isValid())
    solid_count = len(list(getattr(shape, "Solids", []) or []))
    native_bounds = shape.BoundBox
    bounds = {
        "x_min": float(native_bounds.XMin),
        "y_min": float(native_bounds.YMin),
        "z_min": float(native_bounds.ZMin),
        "x_length": float(native_bounds.XLength),
        "y_length": float(native_bounds.YLength),
        "z_length": float(native_bounds.ZLength),
    }
    face_records = _face_records(shape)
    spacing = heuristic_minimum_planar_spacing(shape, _records=face_records)
    holes = sorted(
        {
            round(float(record["hole_diameter"]), 9)
            for record in face_records
            if record.get("hole_diameter") is not None
        }
    )
    orientations = [
        orientation_score(
            shape,
            axis,
            overhang_angle_degrees=overhang_angle_degrees,
            _records=face_records,
            _bounds=bounds,
        )
        for axis in ("x", "y", "z")
    ]
    recommended = min(orientations, key=lambda item: item["heuristic_score"])
    warnings = []
    if not valid:
        warnings.append("The native shape is invalid.")
    if solid_count < 1:
        warnings.append("The native shape is not a watertight solid.")
    if spacing is not None and spacing < minimum_wall_mm:
        warnings.append(
            f"The smallest detected planar spacing is {spacing:.3f} mm, below the {minimum_wall_mm:.3f} mm heuristic wall target."
        )
    small_holes = [value for value in holes if value < minimum_hole_mm]
    if small_holes:
        warnings.append(
            "Detected cylindrical hole diameters below the configured heuristic minimum: "
            + ", ".join(f"{value:.3f} mm" for value in small_holes)
            + "."
        )
    if recommended["unsupported_overhang_area_mm2"] > 0:
        warnings.append("The recommended orientation still has downward overhang area.")
    return {
        "schema": FDM_REPORT_SCHEMA,
        "version": FDM_REPORT_VERSION,
        "object_name": object_name,
        "process": "FDM printing",
        "certification": False,
        "analysis_kind": "heuristic_guidance",
        "native_checks": {
            "shape_valid": valid,
            "watertight_solid": bool(valid and solid_count >= 1),
            "solid_count": solid_count,
        },
        "bounding_dimensions_mm": [
            bounds["x_length"], bounds["y_length"], bounds["z_length"]
        ],
        "minimum_planar_spacing": {
            "value_mm": spacing,
            "threshold_mm": minimum_wall_mm,
            "is_heuristic": True,
        },
        "cylindrical_hole_diameters_mm": holes,
        "minimum_hole_threshold_mm": minimum_hole_mm,
        "orientation_candidates": orientations,
        "recommended_build_axis": recommended["axis"],
        "overhang_angle_degrees": overhang_angle_degrees,
        "warnings": warnings,
    }
