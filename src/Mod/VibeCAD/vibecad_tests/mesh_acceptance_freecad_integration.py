# SPDX-License-Identifier: LGPL-2.1-or-later
"""Accepted native mesh conversion, reopen, export, and restore integration."""

import json
from pathlib import Path
import tempfile

import FreeCAD as App
import Mesh
import Part

from VibeCADAcceptance import VibeCADAcceptanceCoordinator
from VibeCADDocumentValidator import validate_saved_document
from VibeCADRevision import create_revision_record
from tool_impl.service import (
    mesh_analyze,
    mesh_repair,
    meshpart_mesh_from_shape,
    meshpart_shape_from_mesh,
    project_export,
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
        project_id=project_id, parent_revision=parent, user_request=request,
        interpreted_intent=request, assumptions=[],
        plan=[{"operation": operation} for operation in operations],
        tool_operations=[{"tool": operation, "ok": True} for operation in operations],
        changed_objects=[{"name": name, "change": "modified"} for name in objects],
        validation_results=[{"name": "mesh_semantic_reopen", "ok": True}],
        provider="integration", model="deterministic",
        timestamp="2026-07-23T00:15:00Z", generated_source=None,
        preview_image=None, rollback={"available": True},
        transaction_id=revision, document_revision=revision,
    )


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-mesh-acceptance-")
    root = Path(temporary.name)
    project_root = root / "project"
    canonical = root / "mesh-workflow.FCStd"
    metadata = project_root / "accepted-head.json"
    project_id = "mesh-acceptance-integration"
    document = App.newDocument("MeshAcceptanceIntegration")
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
    source = document.addObject("Part::Feature", "SourceSolid")
    source.Label = "Editable source solid"
    source.Shape = Part.makeBox(40, 30, 20)
    document.recompute()
    source_name = source.Name
    parent_result = coordinator.promote(
        parent_prepared,
        _record(
            project_id, None, "Create an editable source solid", "mesh-parent-1",
            ["part.create_box"], [source_name],
        ),
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    parent = parent_result["revision"]

    App.setActiveDocument(document.Name)
    child_prepared = coordinator.prepare(canonical, save_copy)
    p0, p1 = (0, 0, 0), (10, 0, 0)
    p2, p3 = (0, 10, 0), (0, 0, 10)
    tetrahedron = [
        (p0, p2, p1), (p0, p1, p3), (p1, p2, p3), (p2, p0, p3)
    ]
    repair_mesh = document.addObject("Mesh::Feature", "RepairCandidate")
    repair_mesh.Label = "Duplicate-facet repair candidate"
    repair_mesh.Mesh = Mesh.Mesh(tetrahedron + [tetrahedron[0]])
    document.recompute()
    defective = mesh_analyze.run(service, repair_mesh.Name)
    assert "duplicated_facet_indices" in defective["known_defects"], defective
    repaired = mesh_repair.run(
        service, repair_mesh.Name,
        False, True, False, False, False, 0,
    )
    assert repaired["ok"], repaired
    repaired_analysis = mesh_analyze.run(service, repair_mesh.Name)
    assert repaired_analysis["verdict"] == "ready", repaired_analysis
    meshed = meshpart_mesh_from_shape.run(
        service, source_name, 0.1, 28.5, "Validated manufacturing mesh"
    )
    assert meshed["ok"], meshed
    mesh_name = meshed["object"]
    analysis = mesh_analyze.run(service, mesh_name)
    assert analysis["ok"] and analysis["verdict"] == "ready", analysis
    recovered = meshpart_shape_from_mesh.run(
        service, mesh_name, 0.1, True, "Recovered faceted solid"
    )
    assert recovered["ok"], recovered
    recovered_name = recovered["feature"]
    child_result = coordinator.promote(
        child_prepared,
        _record(
            project_id, parent["revision_id"],
            "Create a validated mesh and recover a usable solid", "mesh-child-2",
            [
                "mesh.analyze", "mesh.repair", "meshpart.mesh_from_shape",
                "mesh.analyze", "meshpart.shape_from_mesh",
            ],
            [repair_mesh.Name, mesh_name, recovered_name],
        ),
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    child = child_result["revision"]
    assert child_result["validation"]["mesh_checks"] == 2
    comparison = coordinator.revisions.compare(parent["revision_id"], child["revision_id"])
    assert comparison["changed"] is True

    App.closeDocument(document.Name)
    document = App.openDocument(str(canonical))
    document.recompute()
    service.document = document
    reopened_analysis = mesh_analyze.run(service, mesh_name)
    assert reopened_analysis["verdict"] == "ready", reopened_analysis
    reopened_shape = document.getObject(recovered_name).Shape
    assert not reopened_shape.isNull() and reopened_shape.isValid()
    exports = [
        project_export.run(service, [mesh_name], format_name, f"accepted-mesh-{format_name}")
        for format_name in ("stl", "3mf", "obj")
    ]
    exports.append(
        project_export.run(service, [recovered_name], "step", "recovered-mesh-solid")
    )
    assert all(result["ok"] for result in exports), exports

    coordinator.restore_revision(
        parent["revision_id"], canonical,
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    assert document.getObject(source_name) is not None
    assert document.getObject(mesh_name) is None
    assert document.getObject(recovered_name) is None
    assert document.getObject("RepairCandidate") is None
    App.closeDocument(document.Name)
    document = App.openDocument(str(canonical))
    document.recompute()
    assert document.getObject(source_name) is not None
    assert document.getObject(mesh_name) is None
    assert document.getObject(recovered_name) is None
    assert document.getObject("RepairCandidate") is None
    App.closeDocument(document.Name)
    temporary.cleanup()
    print("VibeCAD mesh acceptance FreeCAD integration passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
