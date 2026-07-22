# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real native-link checks for assembly BOM and interference tools."""

import FreeCAD as App
import Part
import json
from pathlib import Path
import tempfile

from VibeCADAcceptance import VibeCADAcceptanceCoordinator
from VibeCADDocumentValidator import validate_saved_document
from VibeCADRevision import create_revision_record

from tool_impl.service import (
    assembly_analyze_interference,
    assembly_create_assembly,
    assembly_create_joint,
    assembly_extract_bom,
    assembly_ground_component,
    assembly_insert_component,
    assembly_replace_component,
    assembly_solve,
)


class Service:
    def __init__(self, document):
        self.document = document

    def _active_document(self):
        return self.document

    def _assembly_objects(self):
        return [
            obj
            for obj in self.document.Objects
            if obj.isDerivedFrom("Assembly::AssemblyObject")
        ]

    def _partdesign_body_for_feature(self, _object):
        return None

    @staticmethod
    def _assembly_joint_objects(assembly):
        joints = []
        for child in list(getattr(assembly, "Group", []) or []):
            if getattr(child, "TypeId", "") == "Assembly::JointGroup":
                joints.extend(list(getattr(child, "Group", []) or []))
        return joints


def _record(project_id, parent, request, document_revision, operations, objects):
    return create_revision_record(
        project_id=project_id,
        parent_revision=parent,
        user_request=request,
        interpreted_intent=request,
        assumptions=[],
        plan=[{"operation": operation} for operation in operations],
        tool_operations=[{"tool": operation, "ok": True} for operation in operations],
        changed_objects=[{"name": name, "change": "created"} for name in objects],
        validation_results=[{"name": "assembly_semantic_reopen", "ok": True}],
        provider="integration",
        model="deterministic",
        timestamp="2026-07-22T23:00:00Z",
        generated_source=None,
        preview_image=None,
        rollback={"available": True},
        transaction_id=document_revision,
        document_revision=document_revision,
    )


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-assembly-acceptance-")
    root = Path(temporary.name)
    project_root = root / "project"
    project_id = "jointed-assembly-integration"
    canonical = root / "jointed-assembly.FCStd"
    metadata_path = project_root / "accepted-head.json"
    document = App.newDocument("AssemblyAnalysisIntegration")
    document.saveAs(str(canonical))
    coordinator = VibeCADAcceptanceCoordinator(project_root, project_id)

    def save_copy(path):
        document.saveCopy(str(path))

    def restore_live(_path):
        document.restore()
        document.recompute()

    def write_metadata(revision_id):
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps({"accepted_revision": revision_id}) + "\n", encoding="utf-8"
        )

    base_prepared = coordinator.prepare(canonical, save_copy)
    source = document.addObject("Part::Feature", "Bracket")
    source.Shape = Part.makeBox(10, 10, 10)
    source.addProperty("App::PropertyString", "PartNumber")
    source.PartNumber = "BR-100"
    source.addProperty("App::PropertyString", "Material")
    source.Material = "Aluminium 6061"
    replacement = document.addObject("Part::Feature", "LongBracket")
    replacement.Shape = Part.makeBox(20, 10, 10)
    replacement.addProperty("App::PropertyString", "PartNumber")
    replacement.PartNumber = "BR-200"
    source_names = {source.Name, replacement.Name}
    document.recompute()
    base_result = coordinator.promote(
        base_prepared,
        _record(
            project_id, None, "Create two source parts", "assembly-base-1",
            ["part.create_feature"], [source.Name, replacement.Name],
        ),
        save_copy=save_copy,
        validate_document=validate_saved_document,
        restore_live=restore_live,
        write_metadata=write_metadata,
    )
    base_revision = base_result["revision"]
    App.setActiveDocument(document.Name)
    assembly_prepared = coordinator.prepare(canonical, save_copy)
    service = Service(document)

    created = assembly_create_assembly.run(service, "Bracket assembly")
    assert created["ok"], created
    assembly = service._assembly_objects()[0]
    assembly_name = assembly.Name
    first = assembly_insert_component.run(
        service, assembly.Name, source.Name, "Bracket A", {"x": 0, "y": 0, "z": 0}
    )
    second = assembly_insert_component.run(
        service, assembly.Name, source.Name, "Bracket B", {"x": 5, "y": 0, "z": 0}
    )
    assert first["ok"] and second["ok"], (first, second)
    first_name = first["mutation"]["component"]
    second_name = second["mutation"]["component"]

    bom = assembly_extract_bom.run(service, assembly.Name)
    assert bom["ok"] and bom["line_count"] == 1 and bom["total_quantity"] == 2, bom
    assert bom["lines"][0]["part_number"] == "BR-100"
    assert bom["lines"][0]["occurrences"] == sorted([first_name, second_name])
    interference = assembly_analyze_interference.run(service, assembly.Name)
    assert interference["ok"] and interference["interference_count"] == 1, interference
    assert abs(interference["interferences"][0]["common_volume_mm3"] - 500.0) < 1e-7

    document.getObject(second_name).Placement.Base.x = 10
    document.recompute()
    touching = assembly_analyze_interference.run(service, assembly.Name)
    assert touching["ok"] and touching["has_interference"] is False, touching
    replaced = assembly_replace_component.run(
        service, assembly.Name, second_name, replacement.Name
    )
    assert replaced["ok"], replaced
    assert replaced["mutation"]["component"] == second_name
    assert replaced["mutation"]["new_source"] == replacement.Name
    replaced_bom = assembly_extract_bom.run(service, assembly.Name)
    assert replaced_bom["line_count"] == 2 and replaced_bom["total_quantity"] == 2
    assert {line["part_number"] for line in replaced_bom["lines"]} == {
        "BR-100", "BR-200"
    }
    document.getObject(second_name).Placement.Base.x = 5
    document.recompute()
    replaced_interference = assembly_analyze_interference.run(service, assembly.Name)
    assert replaced_interference["interference_count"] == 1, replaced_interference
    assert abs(
        replaced_interference["interferences"][0]["common_volume_mm3"] - 500.0
    ) < 1e-7
    grounded = assembly_ground_component.run(service, assembly.Name, first_name)
    assert grounded["ok"], grounded
    joint = assembly_create_joint.run(
        service,
        assembly.Name,
        {"component_name": first_name, "selection": {"type": "component_origin"}},
        {"component_name": second_name, "selection": {"type": "component_origin"}},
        {"type": "fixed"},
        "Bracket fixed joint",
    )
    assert joint["ok"], joint
    solved = assembly_solve.run(service, assembly.Name)
    assert solved["ok"], solved
    jointed_replacement = assembly_replace_component.run(
        service, assembly.Name, second_name, source.Name
    )
    assert jointed_replacement["ok"], jointed_replacement
    assert jointed_replacement["mutation"]["joint_count"] == 2
    assert jointed_replacement["mutation"]["solver_code"] == 0
    jointed_bom = assembly_extract_bom.run(service, assembly.Name)
    assert jointed_bom["line_count"] == 1 and jointed_bom["total_quantity"] == 2
    jointed_interference = assembly_analyze_interference.run(service, assembly.Name)
    assert jointed_interference["interference_count"] == 1
    assert abs(
        jointed_interference["interferences"][0]["common_volume_mm3"] - 1000.0
    ) < 1e-7
    assembly_result = coordinator.promote(
        assembly_prepared,
        _record(
            project_id,
            base_revision["revision_id"],
            "Create, solve, inspect, and update a fixed bracket assembly",
            "assembly-jointed-2",
            [
                "assembly.create_assembly", "assembly.insert_component",
                "assembly.ground_component", "assembly.create_joint",
                "assembly.solve", "assembly.replace_component",
            ],
            [assembly.Name, first_name, second_name, joint["mutation"]["joint"]],
        ),
        save_copy=save_copy,
        validate_document=validate_saved_document,
        restore_live=restore_live,
        write_metadata=write_metadata,
    )
    assembly_revision = assembly_result["revision"]
    assert assembly_result["validation"]["assembly_checks"] == 3
    comparison = coordinator.revisions.compare(
        base_revision["revision_id"], assembly_revision["revision_id"]
    )
    assert comparison["changed"] is True
    App.closeDocument(document.Name)
    document = App.openDocument(str(canonical))
    document.recompute()
    service.document = document
    reopened_validation = validate_saved_document(canonical)
    assert reopened_validation["ok"], reopened_validation
    reopened_bom = assembly_extract_bom.run(service, assembly_name)
    assert reopened_bom["line_count"] == 1 and reopened_bom["total_quantity"] == 2
    reopened_interference = assembly_analyze_interference.run(service, assembly_name)
    assert reopened_interference["interference_count"] == 1
    coordinator.restore_revision(
        base_revision["revision_id"],
        canonical,
        save_copy=save_copy,
        validate_document=validate_saved_document,
        restore_live=restore_live,
        write_metadata=write_metadata,
    )
    assert coordinator.revisions.head()["revision_id"] == base_revision["revision_id"]
    assert {obj.Name for obj in document.Objects} == source_names
    App.closeDocument(document.Name)
    document = App.openDocument(str(canonical))
    document.recompute()
    assert {obj.Name for obj in document.Objects} == source_names
    assert all(obj.Shape.isValid() for obj in document.Objects)
    App.closeDocument(document.Name)
    temporary.cleanup()
    print("VibeCAD assembly analysis FreeCAD integration passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
