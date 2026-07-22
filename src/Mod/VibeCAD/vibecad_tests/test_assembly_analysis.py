# SPDX-License-Identifier: LGPL-2.1-or-later

from types import SimpleNamespace

from tool_impl.service import assembly_analyze_interference, assembly_extract_bom


class BoundBox:
    def __init__(self, disjoint=False):
        self.disjoint = disjoint

    def intersected(self, _other):
        return not self.disjoint


class Common:
    def __init__(self, volume):
        self.Volume = volume
        self.Solids = [object()] if volume else []

    def isNull(self):
        return self.Volume == 0


class Shape:
    def __init__(self, common_volume=0, disjoint=False):
        self.common_volume = common_volume
        self.BoundBox = BoundBox(disjoint)
        self.Solids = [object()]

    def isNull(self):
        return False

    def isValid(self):
        return True

    def common(self, _other):
        return Common(self.common_volume)


def _component(name, source, shape):
    return SimpleNamespace(
        Name=name, TypeId="App::Link", LinkedObject=source, Shape=shape
    )


def _service(assembly):
    return SimpleNamespace(_assembly_objects=lambda: [assembly])


def test_bom_groups_equal_sources_and_preserves_occurrences():
    bracket = SimpleNamespace(
        Name="Bracket", Label="Mounting bracket", PartNumber="BR-100",
        Material="Aluminium 6061", Description="Machined bracket",
    )
    pin = SimpleNamespace(Name="Pin", Label="Pin")
    assembly = SimpleNamespace(
        Name="Assembly",
        Group=[
            _component("Bracket001", bracket, Shape()),
            _component("Bracket002", bracket, Shape()),
            _component("Pin001", pin, Shape()),
        ],
    )

    result = assembly_extract_bom.run(_service(assembly), "Assembly")

    assert result["ok"] is True
    assert result["line_count"] == 2 and result["total_quantity"] == 3
    line = next(item for item in result["lines"] if item["source_object"] == "Bracket")
    assert line["quantity"] == 2
    assert line["occurrences"] == ["Bracket001", "Bracket002"]
    assert line["part_number"] == "BR-100"
    assert result["state_change"]["changed"] is False


def test_interference_reports_exact_positive_common_volume():
    source = SimpleNamespace(Name="Part", Label="Part")
    assembly = SimpleNamespace(
        Name="Assembly",
        Group=[
            _component("Part001", source, Shape(common_volume=125.0)),
            _component("Part002", source, Shape()),
        ],
    )

    result = assembly_analyze_interference.run(
        _service(assembly), "Assembly", minimum_volume_mm3=0.001
    )

    assert result["ok"] is True
    assert result["checked_pair_count"] == 1
    assert result["interference_count"] == 1
    assert result["interferences"][0] == {
        "component1": "Part001",
        "component2": "Part002",
        "common_volume_mm3": 125.0,
        "common_solid_count": 1,
    }
    assert result["state_change"]["changed"] is False


def test_analysis_rejects_missing_assembly_and_invalid_threshold():
    empty = SimpleNamespace(_assembly_objects=lambda: [])
    assert assembly_extract_bom.run(empty, "Missing")["ok"] is False
    assembly = SimpleNamespace(Name="Assembly", Group=[])
    assert assembly_analyze_interference.run(
        _service(assembly), "Assembly", -1
    )["ok"] is False


def test_bom_reads_native_shape_material_card_when_no_string_override():
    source = SimpleNamespace(
        Name="Bracket", Label="Bracket",
        ShapeMaterial=SimpleNamespace(UUID="material-1", Name="Aluminum 6061-T6"),
    )
    assembly = SimpleNamespace(
        Name="Assembly", Group=[_component("Bracket001", source, Shape())]
    )

    result = assembly_extract_bom.run(_service(assembly), "Assembly")

    assert result["lines"][0]["material"] == "Aluminum 6061-T6"
