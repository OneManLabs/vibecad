# SPDX-License-Identifier: LGPL-2.1-or-later

from types import SimpleNamespace

from tool_impl.service import spreadsheet_bind_parameter, spreadsheet_set_cells


class Sheet:
    Name = "Spreadsheet"
    TypeId = "Spreadsheet::Sheet"

    def getCellFromAlias(self, alias):
        return "B1" if alias == "width" else ""


class Target:
    Name = "Box"
    PropertiesList = ["Length", "Label"]

    def getTypeIdOfProperty(self, name):
        return {"Length": "App::PropertyLength", "Label": "App::PropertyString"}[name]


def _service():
    sheet, target = Sheet(), Target()
    document = SimpleNamespace(
        getObject=lambda name: {sheet.Name: sheet, target.Name: target}.get(name)
    )
    return SimpleNamespace(_active_document=lambda: document)


def test_binding_rejects_missing_alias_and_non_numeric_property_before_mutation():
    missing = spreadsheet_bind_parameter.run(
        _service(), "Spreadsheet", "missing", "Box", "Length"
    )
    non_numeric = spreadsheet_bind_parameter.run(
        _service(), "Spreadsheet", "width", "Box", "Label"
    )

    assert missing["ok"] is False and "does not exist" in missing["error"]
    assert non_numeric["ok"] is False and "not a supported numeric" in non_numeric["error"]


def test_binding_rejects_free_form_expression_input():
    result = spreadsheet_bind_parameter.run(
        _service(), "Spreadsheet", "width + 5", "Box", "Length"
    )

    assert result["ok"] is False and "identifier" in result["error"]


def test_plain_text_marker_matches_requested_spreadsheet_text():
    assert spreadsheet_set_cells._content_matches("'Width", "Width") is True
    assert spreadsheet_set_cells._content_matches("40 mm", "40 mm") is True
    assert spreadsheet_set_cells._content_matches("=40 mm", "40 mm") is True
    assert spreadsheet_set_cells._content_matches("41 mm", "40 mm") is False


def test_spreadsheet_update_touches_expression_dependents():
    sheet = SimpleNamespace(Name="Spreadsheet", Label="Parameters")
    touched = []
    linked = SimpleNamespace(
        Name="Box", ExpressionEngine=[("Length", "Spreadsheet.part_width")],
        touch=lambda: touched.append("Box"),
    )
    labeled = SimpleNamespace(
        Name="Cylinder", ExpressionEngine=[("Radius", "<<Parameters>>.radius")],
        touch=lambda: touched.append("Cylinder"),
    )
    unrelated = SimpleNamespace(
        Name="Cone", ExpressionEngine=[("Radius1", "Other.value")],
        touch=lambda: touched.append("Cone"),
    )
    document = SimpleNamespace(Objects=[sheet, linked, labeled, unrelated])

    result = spreadsheet_set_cells._touch_expression_dependents(document, sheet)

    assert result == ["Box", "Cylinder"]
    assert touched == ["Box", "Cylinder"]
