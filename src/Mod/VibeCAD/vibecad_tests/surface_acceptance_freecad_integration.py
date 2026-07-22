# SPDX-License-Identifier: LGPL-2.1-or-later
"""Accepted native surface fill, thickening, reopen, export, and restore integration."""

import json
from pathlib import Path
import tempfile

import FreeCAD as App
import Part

from VibeCADAcceptance import VibeCADAcceptanceCoordinator
from VibeCADDocumentValidator import validate_saved_document
from VibeCADRevision import create_revision_record
from tool_impl.service import project_export, surface_fill, surface_thicken


class Service:
    def __init__(self, document, root):
        self.document = document
        self.root = root

    def _active_document(self):
        return self.document

    def _document_object_summary(self, obj):
        return {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}

    def authorize(self, _permission):
        return None

    def project_scope_snapshot(self):
        return {"root": str(self.root)}

    def record_audit_event(self, **_event):
        return None


def _record(project_id, parent, request, revision, operations, objects):
    return create_revision_record(
        project_id=project_id, parent_revision=parent, user_request=request,
        interpreted_intent=request, assumptions=[],
        plan=[{"operation": operation} for operation in operations],
        tool_operations=[{"tool": operation, "ok": True} for operation in operations],
        changed_objects=[{"name": name, "change": "modified"} for name in objects],
        validation_results=[{"name": "surface_semantic_reopen", "ok": True}],
        provider="integration", model="deterministic",
        timestamp="2026-07-23T00:30:00Z", generated_source=None,
        preview_image=None, rollback={"available": True},
        transaction_id=revision, document_revision=revision,
    )


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-surface-acceptance-")
    root = Path(temporary.name)
    project_root = root / "project"
    canonical = root / "surface-workflow.FCStd"
    metadata = project_root / "accepted-head.json"
    project_id = "surface-acceptance-integration"
    document = App.newDocument("SurfaceAcceptanceIntegration")
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

    parent_prepared = coordinator.prepare(canonical, save_copy)
    boundary = document.addObject("Part::Feature", "SurfaceBoundary")
    boundary.Label = "Editable closed surface boundary"
    points = [
        App.Vector(0, 0, 0), App.Vector(60, 0, 0),
        App.Vector(60, 40, 0), App.Vector(0, 40, 0), App.Vector(0, 0, 0),
    ]
    boundary.Shape = Part.makePolygon(points)
    document.recompute()
    boundary_name = boundary.Name
    parent_result = coordinator.promote(
        parent_prepared,
        _record(
            project_id, None, "Create an editable closed surface boundary",
            "surface-parent-1", ["part.create_wire"], [boundary_name],
        ),
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    parent = parent_result["revision"]

    App.setActiveDocument(document.Name)
    child_prepared = coordinator.prepare(canonical, save_copy)
    filled = surface_fill.run(
        service,
        [{"object_name": boundary_name, "selection": {"type": "whole_wire"}}],
        "Native filled surface",
    )
    assert filled["ok"], filled
    fill_name = filled["mutation"]["feature"]
    thickened = surface_thicken.run(
        service, fill_name, 3.0, "intersection", "Three millimeter solid panel"
    )
    assert thickened["ok"], thickened
    thick_name = thickened["mutation"]["feature"]
    child_result = coordinator.promote(
        child_prepared,
        _record(
            project_id, parent["revision_id"],
            "Fill the boundary and thicken it into a three millimeter panel",
            "surface-child-2", ["surface.fill", "surface.thicken"],
            [fill_name, thick_name],
        ),
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    child = child_result["revision"]
    assert child_result["validation"]["surface_checks"] == 2
    comparison = coordinator.revisions.compare(parent["revision_id"], child["revision_id"])
    assert comparison["changed"] is True

    App.closeDocument(document.Name)
    document = App.openDocument(str(canonical))
    document.recompute()
    service.document = document
    reopened_fill = document.getObject(fill_name)
    reopened_thick = document.getObject(thick_name)
    assert len(reopened_fill.Shape.Faces) == 1 and reopened_fill.Shape.isValid()
    assert len(reopened_thick.Shape.Solids) == 1 and reopened_thick.Shape.isValid()
    assert reopened_thick.Source.Name == fill_name
    exports = [
        project_export.run(service, [thick_name], "step", "accepted-surface-panel"),
        project_export.run(service, [thick_name], "stl", "accepted-surface-panel"),
    ]
    assert all(result["ok"] for result in exports), exports

    coordinator.restore_revision(
        parent["revision_id"], canonical,
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    assert document.getObject(boundary_name) is not None
    assert document.getObject(fill_name) is None
    assert document.getObject(thick_name) is None
    App.closeDocument(document.Name)
    document = App.openDocument(str(canonical))
    document.recompute()
    assert document.getObject(boundary_name) is not None
    assert document.getObject(fill_name) is None
    assert document.getObject(thick_name) is None
    App.closeDocument(document.Name)
    temporary.cleanup()
    print("VibeCAD surface acceptance FreeCAD integration passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
