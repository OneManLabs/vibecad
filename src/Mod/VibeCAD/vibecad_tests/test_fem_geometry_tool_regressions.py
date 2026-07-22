# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression tests for the FEM-blocking geometry/material tool fixes.

Three previously broken behaviors are pinned down:

1. ``partdesign.measure`` crashed with ``AttributeError: 'tuple' object has
   no attribute 'x'`` because ``_outward_normal`` returns a
   ``(normal, diagnostic)`` tuple that two call sites treated as a bare
   vector (``_subelement_measurement`` and the angle-reference resolver).
2. ``find_subelements`` declared ``min_length``/``max_length`` as
   non-nullable numbers while rejecting them at runtime for face queries,
   so strict function-calling providers could not compose a valid call.
   The four range filters are now nullable and null means "filter unset".
3. ``material.list_materials`` used raw substring matching, so
   "aluminum 6061" and "aluminium" found no cards even though
   ``Aluminum-6061-T6`` exists in the library.
"""

from __future__ import annotations

import math
import sys
import types
from typing import Any

import pytest

from VibeCADTools import ToolArgumentValidationError, ToolSpec
from tool_impl.service import (
    material_list_materials,
    fem_solve,
    part_find_subelements,
    partdesign_find_subelements,
    partdesign_measure,
)


def test_fem_solver_classifies_native_constraint_without_python_proxy() -> None:
    native = types.SimpleNamespace(TypeId="Fem::ConstraintFixed", Proxy=None)
    assert fem_solve._proxy_type(native) == "Fem::ConstraintFixed"


def test_fem_solver_prefers_specific_python_proxy_type() -> None:
    scripted = types.SimpleNamespace(
        TypeId="Fem::ConstraintPython",
        Proxy=types.SimpleNamespace(Type="Fem::ConstraintForce"),
    )
    assert fem_solve._proxy_type(scripted) == "Fem::ConstraintForce"


# ---------------------------------------------------------------------------
# Minimal FreeCAD geometry stand-ins
# ---------------------------------------------------------------------------


class Vec:
    """Minimal stand-in for FreeCAD's Base.Vector."""

    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    @property
    def Length(self) -> float:
        return math.sqrt(self.x**2 + self.y**2 + self.z**2)

    def normalize(self) -> "Vec":
        length = self.Length
        self.x /= length
        self.y /= length
        self.z /= length
        return self

    def add(self, other: "Vec") -> "Vec":
        return Vec(self.x + other.x, self.y + other.y, self.z + other.z)

    def multiply(self, factor: float) -> "Vec":
        return Vec(self.x * factor, self.y * factor, self.z * factor)

    def dot(self, other: "Vec") -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z

    def __truediv__(self, divisor: float) -> "Vec":
        return Vec(self.x / divisor, self.y / divisor, self.z / divisor)


class Plane:
    """Class name drives _canonical_geometry_type() -> 'plane'."""


class FakeBoundBox:
    XMin = 0.0
    XMax = 10.0
    YMin = 0.0
    YMax = 10.0
    ZMin = 0.0
    ZMax = 10.0
    DiagonalLength = math.sqrt(300.0)


class FakePlanarFace:
    def __init__(self, normal: Vec, *, normal_error: str | None = None) -> None:
        self.Surface = Plane()
        self.Area = 100.0
        self.CenterOfMass = Vec(5.0, 5.0, 10.0)
        self.BoundBox = FakeBoundBox()
        self.ParameterRange = (0.0, 10.0, 0.0, 10.0)
        self._normal = normal
        self._normal_error = normal_error

    def normalAt(self, u: float, v: float) -> Vec:
        if self._normal_error is not None:
            raise RuntimeError(self._normal_error)
        return Vec(self._normal.x, self._normal.y, self._normal.z)


class FakeShape:
    Volume = 1000.0

    def __init__(self, face: FakePlanarFace) -> None:
        self.BoundBox = FakeBoundBox()
        self._face = face

    def getElement(self, name: str) -> FakePlanarFace:
        return self._face

    def isInside(self, point: Vec, tolerance: float, check_face: bool) -> bool:
        return False


class FakeObject:
    TypeId = "Part::Feature"

    def __init__(self, name: str, shape: FakeShape) -> None:
        self.Name = name
        self.Shape = shape


class FakeDocument:
    def __init__(self, objects: list[FakeObject]) -> None:
        self.Objects = list(objects)
        self._by_name = {obj.Name: obj for obj in objects}

    def getObject(self, name: str) -> FakeObject | None:
        return self._by_name.get(name)


class FakeService:
    def __init__(self, document: FakeDocument) -> None:
        self._document = document

    def _active_document(self) -> FakeDocument:
        return self._document

    def _document_object_summary(self, obj: Any) -> dict[str, Any]:
        return {"name": getattr(obj, "Name", "")}


# ---------------------------------------------------------------------------
# 1. partdesign.measure: _outward_normal tuple unpack
# ---------------------------------------------------------------------------


class TestSubelementMeasurementNormalUnpack:
    def test_planar_face_reports_outward_normal_dict(self) -> None:
        """Regression: the raw (normal, diagnostic) tuple crashed _vector()."""
        shape = FakeShape(FakePlanarFace(Vec(0.0, 0.0, 1.0)))
        result = partdesign_measure._subelement_measurement(shape, "Face3")
        assert result["ok"] is True
        measurement = result["measurement"]
        normal = measurement["outward_normal"]
        assert isinstance(normal, dict)
        assert normal == {"x": 0.0, "y": 0.0, "z": 1.0}
        assert "outward_normal_error" not in measurement

    def test_unresolvable_normal_surfaces_diagnostic(self) -> None:
        face = FakePlanarFace(Vec(0.0, 0.0, 1.0), normal_error="normalAt exploded")
        result = partdesign_measure._subelement_measurement(FakeShape(face), "Face3")
        assert result["ok"] is True
        measurement = result["measurement"]
        assert "outward_normal" not in measurement
        error = measurement["outward_normal_error"]
        assert error["native_error"] == "normalAt exploded"


class TestMeasureAngleNormalUnpack:
    def _service(self) -> FakeService:
        top = FakeObject("BoxTop", FakeShape(FakePlanarFace(Vec(0.0, 0.0, 1.0))))
        side = FakeObject("BoxSide", FakeShape(FakePlanarFace(Vec(1.0, 0.0, 0.0))))
        broken_face = FakePlanarFace(
            Vec(0.0, 0.0, 1.0), normal_error="normalAt exploded"
        )
        broken = FakeObject("Broken", FakeShape(broken_face))
        return FakeService(FakeDocument([top, side, broken]))

    def test_angle_between_planar_faces(self) -> None:
        """Regression: face angle references crashed on the normal tuple."""
        result = partdesign_measure._measure_angle(
            self._service(),
            {"object_name": "BoxTop", "subelement": "Face1"},
            {"object_name": "BoxSide", "subelement": "Face1"},
        )
        assert result["ok"] is True
        assert result["angle_degrees"] == pytest.approx(90.0)
        assert result["first"]["reference_type"] == "planar_face_normal"
        assert result["second"]["direction_source"] == "native_face_outward_normal"

    def test_unresolvable_normal_returns_invalid_with_detail(self) -> None:
        result = partdesign_measure._measure_angle(
            self._service(),
            {"object_name": "BoxTop", "subelement": "Face1"},
            {"object_name": "Broken", "subelement": "Face1"},
        )
        assert result["ok"] is False
        assert "Cannot resolve outward normal" in result["error"]
        assert "normalAt exploded" in result["error"]


# ---------------------------------------------------------------------------
# 2. find_subelements: nullable, null-tolerant range filters
# ---------------------------------------------------------------------------

_FACE_QUERY_ALL_NULL_FILTERS = {
    "object_name": "Body",
    "element_type": "face",
    "min_area": None,
    "max_area": None,
    "min_length": None,
    "max_length": None,
}

_RANGE_KWARGS: dict[str, float] = {
    "normal_tolerance_degrees": 5.0,
    "direction_tolerance_degrees": 5.0,
    "radius_tolerance": 0.01,
    "max_distance": 1.0,
}


class TestNullableRangeFilters:
    @pytest.mark.parametrize(
        "module",
        [partdesign_find_subelements, part_find_subelements],
        ids=["partdesign.find_subelements", "part.find_subelements"],
    )
    def test_schema_accepts_null_filters_on_face_query(self, module: Any) -> None:
        """Regression: schema forced numbers that runtime then rejected."""
        spec = ToolSpec.from_mapping(module.TOOL_SPEC)
        spec.validate_arguments(dict(_FACE_QUERY_ALL_NULL_FILTERS))

    def test_part_spec_inherits_fix_with_own_name(self) -> None:
        spec = ToolSpec.from_mapping(part_find_subelements.TOOL_SPEC)
        assert spec.name == "part.find_subelements"

    def test_schema_accepts_numeric_length_filters_on_edge_query(self) -> None:
        spec = ToolSpec.from_mapping(partdesign_find_subelements.TOOL_SPEC)
        spec.validate_arguments(
            {
                "object_name": "Body",
                "element_type": "edge",
                "min_length": 2.0,
                "max_length": 50.0,
            }
        )

    def test_schema_still_rejects_negative_length(self) -> None:
        spec = ToolSpec.from_mapping(partdesign_find_subelements.TOOL_SPEC)
        with pytest.raises(ToolArgumentValidationError):
            spec.validate_arguments(
                {
                    "object_name": "Body",
                    "element_type": "edge",
                    "min_length": -1.0,
                }
            )

    def test_runtime_allows_null_filters_on_faces(self) -> None:
        error = partdesign_find_subelements._validate_ranges(
            "face",
            min_area=None,
            max_area=None,
            min_length=None,
            max_length=None,
            **_RANGE_KWARGS,
        )
        assert error is None

    def test_runtime_rejects_numeric_length_on_faces_with_guidance(self) -> None:
        error = partdesign_find_subelements._validate_ranges(
            "face",
            min_area=None,
            max_area=None,
            min_length=1.0,
            max_length=None,
            **_RANGE_KWARGS,
        )
        assert error is not None
        assert error["ok"] is False
        assert "element_type='edge'" in error["error"]
        assert "null" in error["error"]

    def test_runtime_rejects_numeric_area_on_edges_with_guidance(self) -> None:
        error = partdesign_find_subelements._validate_ranges(
            "edge",
            min_area=10.0,
            max_area=None,
            min_length=None,
            max_length=None,
            **_RANGE_KWARGS,
        )
        assert error is not None
        assert error["ok"] is False
        assert "element_type='face'" in error["error"]
        assert "null" in error["error"]

    def test_runtime_rejects_inverted_length_range(self) -> None:
        error = partdesign_find_subelements._validate_ranges(
            "edge",
            min_area=None,
            max_area=None,
            min_length=10.0,
            max_length=5.0,
            **_RANGE_KWARGS,
        )
        assert error is not None
        assert "min_length cannot exceed max_length" in error["error"]


# ---------------------------------------------------------------------------
# 3. material.list_materials: tokenized, spelling-tolerant search
# ---------------------------------------------------------------------------


class FakeMaterial:
    def __init__(
        self,
        name: str,
        directory: str,
        tags: list[str] | None = None,
        description: str = "",
    ) -> None:
        self.Name = name
        self.Directory = directory
        self.LibraryName = "Standard"
        self.Tags = tags or []
        self.Description = description


_ALUMINUM = FakeMaterial("Aluminum-6061-T6", "Standard/Metal/Aluminum")
_STEEL = FakeMaterial("Steel-1045", "Standard/Metal/Steel")
_GLASS_FIBRE = FakeMaterial("Glass-E-GlassFibre", "Standard/Glass")
_POLYCARBONATE = FakeMaterial(
    "PC-Molded",
    "Standard/Thermoplast",
    description="Polycarbonates (PC) are a group of thermoplastic polymers.",
)


def _tokens(query: str) -> list[str]:
    return material_list_materials._normalize(query).split()


class TestMaterialSearch:
    @pytest.mark.parametrize(
        "query",
        ["aluminum 6061", "aluminium", "Aluminum-6061", "ALUMINIUM 6061", "6061 t6"],
    )
    def test_aluminum_variants_match_the_6061_card(self, query: str) -> None:
        """Regression: substring matching missed every one of these."""
        assert material_list_materials._matches(_ALUMINUM, _tokens(query))

    def test_steel_matches_steel_card(self) -> None:
        assert material_list_materials._matches(_STEEL, _tokens("steel"))

    @pytest.mark.parametrize("query", ["glass fibre", "glass fiber", "glassfiber"])
    def test_fibre_and_fiber_spellings_both_match(self, query: str) -> None:
        """British/American spelling folds apply to fibre as well as aluminium."""
        assert material_list_materials._matches(_GLASS_FIBRE, _tokens(query))

    def test_description_field_is_searched(self) -> None:
        """'polycarbonate' only appears in the card description, not the name."""
        assert material_list_materials._matches(
            _POLYCARBONATE, _tokens("polycarbonate")
        )
        assert not material_list_materials._matches(_STEEL, _tokens("polycarbonate"))

    def test_unrelated_query_does_not_match(self) -> None:
        assert not material_list_materials._matches(
            _ALUMINUM, _tokens("titanium grade 5")
        )

    def test_empty_query_matches_everything(self) -> None:
        assert material_list_materials._matches(_ALUMINUM, _tokens(""))
        assert material_list_materials._matches(_STEEL, _tokens(""))

    def test_tool_spec_validates_and_accepts_query(self) -> None:
        spec = ToolSpec.from_mapping(material_list_materials.TOOL_SPEC)
        spec.validate_arguments({"name_filter": "aluminum 6061"})

    def _install_fake_library(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_module = types.ModuleType("Materials")

        class FakeMaterialManager:
            Materials = {"uuid-alu": _ALUMINUM, "uuid-steel": _STEEL}

        fake_module.MaterialManager = FakeMaterialManager  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "Materials", fake_module)

    def test_run_finds_aluminium_card_end_to_end(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_fake_library(monkeypatch)
        result = material_list_materials.run(None, "aluminium 6061")
        assert result["ok"] is True
        assert result["material_count"] == 1
        assert result["materials"][0]["name"] == "Aluminum-6061-T6"
        assert result["materials"][0]["uuid"] == "uuid-alu"

    def test_run_zero_matches_returns_guidance_note(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_fake_library(monkeypatch)
        result = material_list_materials.run(None, "titanium grade 5")
        assert result["ok"] is True
        assert result["material_count"] == 0
        assert result["materials"] == []
        assert "note" in result
