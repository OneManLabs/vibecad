# SPDX-License-Identifier: LGPL-2.1-or-later
"""Accepted native material assignment and BOM persistence integration."""

import json
from pathlib import Path
import tempfile

import FreeCAD as App
import Part

from VibeCADAcceptance import VibeCADAcceptanceCoordinator
from VibeCADDocumentValidator import validate_saved_document
from VibeCADRevision import create_revision_record
from tool_impl.service import (
    assembly_create_assembly,
    assembly_extract_bom,
    assembly_insert_component,
    material_apply_material,
    material_list_materials,
)


class Service:
    def __init__(self, document):
        self.document = document

    def _active_document(self):
        return self.document

    def _assembly_objects(self):
        return [
            obj for obj in self.document.Objects
            if obj.isDerivedFrom("Assembly::AssemblyObject")
        ]

    def _partdesign_body_for_feature(self, _object):
        return None


def _record(project_id, parent, request, revision, operations, objects):
    return create_revision_record(
        project_id=project_id, parent_revision=parent, user_request=request,
        interpreted_intent=request, assumptions=[],
        plan=[{"operation": operation} for operation in operations],
        tool_operations=[{"tool": operation, "ok": True} for operation in operations],
        changed_objects=[{"name": name, "change": "modified"} for name in objects],
        validation_results=[{"name": "material_semantic_reopen", "ok": True}],
        provider="integration", model="deterministic",
        timestamp="2026-07-22T23:45:00Z", generated_source=None,
        preview_image=None, rollback={"available": True},
        transaction_id=revision, document_revision=revision,
    )


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-material-acceptance-")
    root = Path(temporary.name)
    project_root = root / "project"
    canonical = root / "material-assembly.FCStd"
    metadata = project_root / "accepted-head.json"
    project_id = "material-acceptance-integration"
    document = App.newDocument("MaterialAcceptanceIntegration")
    document.saveAs(str(canonical))
    service = Service(document)
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
    source = document.addObject("Part::Feature", "Bracket")
    source.Shape = Part.makeBox(40, 25, 8)
    source.addProperty("App::PropertyString", "PartNumber")
    source.PartNumber = "BR-6061"
    document.recompute()
    created = assembly_create_assembly.run(service, "Material assembly")
    assert created["ok"], created
    assembly = service._assembly_objects()[0]
    inserted = assembly_insert_component.run(
        service, assembly.Name, source.Name, "Bracket occurrence",
        {"x": 0, "y": 0, "z": 0},
    )
    assert inserted["ok"], inserted
    source_name, assembly_name = source.Name, assembly.Name
    parent_result = coordinator.promote(
        parent_prepared,
        _record(
            project_id, None, "Create an unassigned bracket assembly",
            "material-parent-1",
            ["assembly.create_assembly", "assembly.insert_component"],
            [source_name, assembly_name],
        ),
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    parent = parent_result["revision"]
    App.setActiveDocument(document.Name)
    child_prepared = coordinator.prepare(canonical, save_copy)
    catalog = material_list_materials.run(service, "aluminum 6061")
    assert catalog["ok"] and catalog["material_count"] >= 1, catalog
    selected = next(
        item for item in catalog["materials"] if item["name"] == "Aluminum-6061-T6"
    )
    applied = material_apply_material.run(service, source_name, selected["uuid"])
    assert applied["ok"], applied
    assert applied["material_after"]["name"] == selected["name"]
    bom = assembly_extract_bom.run(service, assembly_name)
    assert bom["lines"][0]["material"] == selected["name"], bom
    child_result = coordinator.promote(
        child_prepared,
        _record(
            project_id, parent["revision_id"],
            "Assign Aluminum 6061-T6 and update the BOM", "material-child-2",
            ["material.apply_material", "assembly.extract_bom"], [source_name],
        ),
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    child = child_result["revision"]
    assert child_result["validation"]["material_checks"] >= 1
    comparison = coordinator.revisions.compare(parent["revision_id"], child["revision_id"])
    assert comparison["changed"] is True
    App.closeDocument(document.Name)
    document = App.openDocument(str(canonical))
    document.recompute()
    service.document = document
    reopened_source = document.getObject(source_name)
    assert str(reopened_source.ShapeMaterial.UUID) == selected["uuid"]
    assert str(reopened_source.ShapeMaterial.Name) == selected["name"]
    reopened_bom = assembly_extract_bom.run(service, assembly_name)
    assert reopened_bom["lines"][0]["material"] == selected["name"]
    coordinator.restore_revision(
        parent["revision_id"], canonical,
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    restored_source = document.getObject(source_name)
    restored_identity = (
        str(getattr(restored_source.ShapeMaterial, "UUID", "") or ""),
        str(getattr(restored_source.ShapeMaterial, "Name", "") or ""),
    )
    assert restored_identity != (selected["uuid"], selected["name"]), restored_identity
    restored_bom = assembly_extract_bom.run(service, assembly_name)
    assert restored_bom["lines"][0]["material"] != selected["name"], restored_bom
    App.closeDocument(document.Name)
    document = App.openDocument(str(canonical))
    document.recompute()
    restored_source = document.getObject(source_name)
    assert (
        str(getattr(restored_source.ShapeMaterial, "UUID", "") or ""),
        str(getattr(restored_source.ShapeMaterial, "Name", "") or ""),
    ) != (selected["uuid"], selected["name"])
    App.closeDocument(document.Name)
    temporary.cleanup()
    print("VibeCAD material acceptance FreeCAD integration passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
