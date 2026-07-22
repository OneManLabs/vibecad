# SPDX-License-Identifier: LGPL-2.1-or-later
"""Accepted spreadsheet-driven native size-variant integration."""

import json
from pathlib import Path
import tempfile

import FreeCAD as App

from VibeCADAcceptance import VibeCADAcceptanceCoordinator
from VibeCADDocumentValidator import validate_saved_document
from VibeCADRevision import create_revision_record
from tool_impl.service import (
    project_export,
    spreadsheet_bind_parameter,
    spreadsheet_create_sheet,
    spreadsheet_read_sheet,
    spreadsheet_set_cells,
)


class Service:
    def __init__(self, document, root):
        self.document = document
        self.root = root

    def _active_document(self):
        return self.document

    def authorize(self, _permission):
        return None

    def project_scope_snapshot(self):
        return {"root": str(self.root)}

    def record_audit_event(self, **_event):
        return None


def _record(project_id, parent, request, revision, operations, objects):
    return create_revision_record(
        project_id=project_id,
        parent_revision=parent,
        user_request=request,
        interpreted_intent=request,
        assumptions=[],
        plan=[{"operation": operation} for operation in operations],
        tool_operations=[{"tool": operation, "ok": True} for operation in operations],
        changed_objects=[{"name": name, "change": "modified"} for name in objects],
        validation_results=[{"name": "parametric_reopen", "ok": True}],
        provider="integration",
        model="deterministic",
        timestamp="2026-07-22T23:30:00Z",
        generated_source=None,
        preview_image=None,
        rollback={"available": True},
        transaction_id=revision,
        document_revision=revision,
    )


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-spreadsheet-variant-")
    root = Path(temporary.name)
    project_root = root / "project"
    canonical = root / "variant.FCStd"
    metadata = project_root / "accepted-head.json"
    project_id = "spreadsheet-variant-integration"
    document = App.newDocument("SpreadsheetVariantIntegration")
    document.saveAs(str(canonical))
    service = Service(document, project_root)
    coordinator = VibeCADAcceptanceCoordinator(project_root, project_id)

    def save_copy(path):
        document.saveCopy(str(path))

    def restore_live(_path):
        document.restore()
        document.recompute()

    def write_metadata(revision_id):
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(
            json.dumps({"accepted_revision": revision_id}) + "\n", encoding="utf-8"
        )

    base_prepared = coordinator.prepare(canonical, save_copy)
    box = document.addObject("Part::Box", "ParametricBox")
    box.Length, box.Width, box.Height = 10, 10, 10
    document.recompute()
    base = coordinator.promote(
        base_prepared,
        _record(project_id, None, "Create a base box", "variant-base-1", ["part.create_box"], [box.Name]),
        save_copy=save_copy,
        validate_document=validate_saved_document,
        restore_live=restore_live,
        write_metadata=write_metadata,
    )["revision"]
    App.setActiveDocument(document.Name)
    parameter_prepared = coordinator.prepare(canonical, save_copy)
    created = spreadsheet_create_sheet.run(service, "Named dimensions")
    assert created["ok"], created
    sheet = next(obj for obj in document.Objects if obj.TypeId == "Spreadsheet::Sheet")
    cells = [
        {"cell": "A1", "content": "Width"},
        {"cell": "B1", "content": "40 mm", "alias": "part_width"},
        {"cell": "A2", "content": "Depth"},
        {"cell": "B2", "content": "30 mm", "alias": "part_depth"},
        {"cell": "A3", "content": "Height"},
        {"cell": "B3", "content": "20 mm", "alias": "part_height"},
    ]
    written = spreadsheet_set_cells.run(service, sheet.Name, cells)
    assert written["ok"], written
    bindings = [
        spreadsheet_bind_parameter.run(service, sheet.Name, "part_width", box.Name, "Length"),
        spreadsheet_bind_parameter.run(service, sheet.Name, "part_depth", box.Name, "Width"),
        spreadsheet_bind_parameter.run(service, sheet.Name, "part_height", box.Name, "Height"),
    ]
    assert all(item["ok"] for item in bindings), bindings
    assert (float(box.Length), float(box.Width), float(box.Height)) == (40.0, 30.0, 20.0)
    parameter_result = coordinator.promote(
        parameter_prepared,
        _record(
            project_id, base["revision_id"], "Drive the box from named dimensions",
            "variant-parameters-2",
            ["spreadsheet.create_sheet", "spreadsheet.set_cells", "spreadsheet.bind_parameter"],
            [sheet.Name, box.Name],
        ),
        save_copy=save_copy,
        validate_document=validate_saved_document,
        restore_live=restore_live,
        write_metadata=write_metadata,
    )
    assert parameter_result["validation"]["spreadsheet_checks"] == 4
    parameter_revision = parameter_result["revision"]
    App.closeDocument(document.Name)
    document = App.openDocument(str(canonical))
    document.recompute()
    service.document = document
    box = document.getObject("ParametricBox")
    sheet = document.getObject("Spreadsheet")
    assert (float(box.Length), float(box.Width), float(box.Height)) == (40.0, 30.0, 20.0)
    assert dict(box.ExpressionEngine)["Length"] == "Spreadsheet.part_width"
    readback = spreadsheet_read_sheet.run(service, sheet.Name, 0, 20)
    assert readback["ok"] and readback["cell_count"] == 6

    App.setActiveDocument(document.Name)
    variant_prepared = coordinator.prepare(canonical, save_copy)
    variant_write = spreadsheet_set_cells.run(
        service,
        sheet.Name,
        [
            {"cell": "B1", "content": "55 mm", "alias": "part_width"},
            {"cell": "B3", "content": "25 mm", "alias": "part_height"},
        ],
    )
    assert variant_write["ok"], variant_write
    variant_dimensions = (float(box.Length), float(box.Width), float(box.Height))
    assert variant_dimensions == (55.0, 30.0, 25.0), (
        variant_dimensions,
        sheet.getContents("B1"),
        sheet.get("B1"),
        sheet.getContents("B3"),
        sheet.get("B3"),
        list(box.ExpressionEngine),
    )
    variant_result = coordinator.promote(
        variant_prepared,
        _record(
            project_id, parameter_revision["revision_id"], "Create the larger size variant",
            "variant-large-3", ["spreadsheet.set_cells"], [sheet.Name, box.Name],
        ),
        save_copy=save_copy,
        validate_document=validate_saved_document,
        restore_live=restore_live,
        write_metadata=write_metadata,
    )
    assert variant_result["validation"]["spreadsheet_checks"] == 4
    variant_revision = variant_result["revision"]
    comparison = coordinator.revisions.compare(
        parameter_revision["revision_id"], variant_revision["revision_id"]
    )
    assert comparison["changed"] is True
    App.closeDocument(document.Name)
    document = App.openDocument(str(canonical))
    document.recompute()
    service.document = document
    box = document.getObject("ParametricBox")
    assert (float(box.Length), float(box.Width), float(box.Height)) == (55.0, 30.0, 25.0)
    exports = [
        project_export.run(service, [box.Name], output_format, f"large-variant-{output_format}")
        for output_format in ("step", "stl")
    ]
    assert all(result["ok"] for result in exports), exports
    coordinator.restore_revision(
        parameter_revision["revision_id"], canonical,
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    box = document.getObject("ParametricBox")
    assert (float(box.Length), float(box.Width), float(box.Height)) == (40.0, 30.0, 20.0)
    App.closeDocument(document.Name)
    document = App.openDocument(str(canonical))
    document.recompute()
    box = document.getObject("ParametricBox")
    assert (float(box.Length), float(box.Width), float(box.Height)) == (40.0, 30.0, 20.0)
    App.closeDocument(document.Name)
    temporary.cleanup()
    print("VibeCAD spreadsheet variant FreeCAD integration passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
