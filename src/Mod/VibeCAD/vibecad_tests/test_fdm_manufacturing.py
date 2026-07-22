# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADManufacturing import (
    FDM_REPORT_SCHEMA,
    analyze_fdm_shape,
    cylindrical_hole_diameters,
    heuristic_minimum_planar_spacing,
)
from tool_impl.service import project_analyze_fdm


class Vector(SimpleNamespace):
    pass


class Face:
    ParameterRange = (0.0, 1.0, 0.0, 1.0)

    def __init__(self, normal, center, area, *, radius=None, orientation="Forward"):
        self._normal = Vector(x=normal[0], y=normal[1], z=normal[2])
        self.CenterOfMass = Vector(x=center[0], y=center[1], z=center[2])
        self.Area = area
        self.Orientation = orientation
        self.Surface = SimpleNamespace(**({"Radius": radius} if radius else {}))

    def normalAt(self, _u, _v):
        return self._normal


class Shape:
    def __init__(self, faces, bounds=(10, 20, 30), *, valid=True, solids=1):
        self.Faces = faces
        self.BoundBox = SimpleNamespace(
            XMin=0.0, YMin=0.0, ZMin=0.0,
            XLength=bounds[0], YLength=bounds[1], ZLength=bounds[2],
        )
        self._valid = valid
        self.Solids = [object()] * solids

    def isNull(self):
        return False

    def isValid(self):
        return self._valid


def box_faces():
    return [
        Face((-1, 0, 0), (0, 10, 15), 600),
        Face((1, 0, 0), (10, 10, 15), 600),
        Face((0, -1, 0), (5, 0, 15), 300),
        Face((0, 1, 0), (5, 20, 15), 300),
        Face((0, 0, -1), (5, 10, 0), 200),
        Face((0, 0, 1), (5, 10, 30), 200),
    ]


def test_native_fdm_report_is_structured_and_not_certification():
    report = analyze_fdm_shape(Shape(box_faces()), object_name="Box")

    assert report["schema"] == FDM_REPORT_SCHEMA
    assert report["certification"] is False
    assert report["analysis_kind"] == "heuristic_guidance"
    assert report["native_checks"]["shape_valid"] is True
    assert report["native_checks"]["watertight_solid"] is True
    assert report["bounding_dimensions_mm"] == [10.0, 20.0, 30.0]
    assert report["recommended_build_axis"] in {"x", "y", "z"}


def test_planar_spacing_and_small_hole_warning_are_explicit_heuristics():
    faces = box_faces() + [
        Face((-1, 0, 0), (1, 10, 15), 50),
        Face((1, 0, 0), (2, 10, 15), 50),
        Face((0, 1, 0), (5, 10, 15), 20, radius=0.75, orientation="Reversed"),
    ]
    shape = Shape(faces)

    assert heuristic_minimum_planar_spacing(shape) == pytest.approx(1.0)
    assert cylindrical_hole_diameters(shape) == [1.5]
    report = analyze_fdm_shape(
        shape, object_name="ThinPart", minimum_wall_mm=1.2, minimum_hole_mm=2.0
    )
    assert report["minimum_planar_spacing"]["is_heuristic"] is True
    assert any("planar spacing" in warning for warning in report["warnings"])
    assert any("hole diameters" in warning for warning in report["warnings"])


def test_invalid_or_open_shape_fails_native_checks_without_false_certification():
    report = analyze_fdm_shape(
        Shape(box_faces(), valid=False, solids=0), object_name="Broken"
    )

    assert report["native_checks"]["shape_valid"] is False
    assert report["native_checks"]["watertight_solid"] is False
    assert "The native shape is invalid." in report["warnings"]
    assert "The native shape is not a watertight solid." in report["warnings"]


def test_typed_tool_requires_exact_objects_and_preserves_state():
    shape = Shape(box_faces())
    document = SimpleNamespace(getObject=lambda name: SimpleNamespace(Name=name, Shape=shape) if name == "Part" else None)
    permissions = []
    service = SimpleNamespace(
        authorize=lambda permission: permissions.append(permission),
        _active_document=lambda: document,
    )

    result = project_analyze_fdm.run(service, ["Part"])
    missing = project_analyze_fdm.run(service, ["Missing"])

    assert result["ok"] is True
    assert result["analysis"]["certification"] is False
    assert result["state_change"]["document_changed"] is False
    assert missing["ok"] is False
    assert permissions == ["project.view", "project.view"]
