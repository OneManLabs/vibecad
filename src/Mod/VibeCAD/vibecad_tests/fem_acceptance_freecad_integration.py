# SPDX-License-Identifier: LGPL-2.1-or-later
"""Accepted native FEM mesh, solve, reopen, compare, and restore integration."""

import json
from pathlib import Path
import tempfile
import time

import FreeCAD as App
import Part
from PySide.QtCore import QCoreApplication

from VibeCADAcceptance import VibeCADAcceptanceCoordinator
from VibeCADDocumentValidator import validate_saved_document
from VibeCADRevision import create_revision_record
from tool_impl.service import (
    fem_add_constraint,
    fem_add_material,
    fem_create_analysis,
    fem_mesh_analysis,
    fem_solve,
    material_list_materials,
)


class Service:
    def __init__(self, document):
        self.document = document

    def _active_document(self):
        return self.document

    def _get_fem_analysis(self, name=None):
        analyses = [
            obj for obj in self.document.Objects
            if str(getattr(obj, "TypeId", "")) == "Fem::FemAnalysis"
        ]
        if name:
            candidate = self.document.getObject(name)
            return candidate if candidate in analyses else None
        return analyses[0] if len(analyses) == 1 else None


def _record(project_id, parent, request, revision, operations, objects):
    return create_revision_record(
        project_id=project_id, parent_revision=parent, user_request=request,
        interpreted_intent=request, assumptions=[],
        plan=[{"operation": operation} for operation in operations],
        tool_operations=[{"tool": operation, "ok": True} for operation in operations],
        changed_objects=[{"name": name, "change": "modified"} for name in objects],
        validation_results=[{"name": "fem_semantic_reopen", "ok": True}],
        provider="integration", model="deterministic",
        timestamp="2026-07-23T02:00:00Z", generated_source=None,
        preview_image=None, rollback={"available": True},
        transaction_id=revision, document_revision=revision,
    )


def _poll(run, service, operation_id, timeout_seconds=90):
    deadline = time.monotonic() + timeout_seconds
    last = None
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        last = run(service, {"action": "status", "operation_id": operation_id})
        if last.get("complete") is True:
            return last
        time.sleep(0.05)
    raise AssertionError({"error": "operation timed out", "last": last})


def _face_at_x(shape, target):
    candidates = []
    for index, face in enumerate(shape.Faces, start=1):
        center = face.CenterOfMass
        if abs(float(center.x) - target) <= 1.0e-8:
            candidates.append(f"Face{index}")
    assert len(candidates) == 1, candidates
    return candidates[0]


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-fem-acceptance-")
    root = Path(temporary.name)
    project_root = root / "project"
    canonical = root / "fem-workflow.FCStd"
    metadata = project_root / "accepted-head.json"
    project_id = "fem-acceptance-integration"
    document = App.newDocument("FEMAcceptanceIntegration")
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
    model = document.addObject("Part::Feature", "Cantilever")
    model.Label = "40 x 10 x 10 millimeter cantilever"
    model.Shape = Part.makeBox(40, 10, 10)
    document.recompute()
    model_name = model.Name
    fixed_face = _face_at_x(model.Shape, 0.0)
    loaded_face = _face_at_x(model.Shape, 40.0)
    parent_result = coordinator.promote(
        parent_prepared,
        _record(
            project_id, None, "Create a cantilever for structural analysis",
            "fem-parent-1", ["part.create_box"], [model_name],
        ),
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    parent = parent_result["revision"]

    App.setActiveDocument(document.Name)
    child_prepared = coordinator.prepare(canonical, save_copy)
    created = fem_create_analysis.run(service, "Static cantilever", "static")
    assert created["ok"], created
    analysis_name = created["transaction"]["result"]["analysis"]
    solver_name = created["transaction"]["result"]["solver"]
    catalog = material_list_materials.run(service, "aluminum 6061")
    assert catalog["ok"], catalog
    selected = next(
        item for item in catalog["materials"] if item["name"] == "Aluminum-6061-T6"
    )
    material = fem_add_material.run(
        service, analysis_name, selected["uuid"], "Aluminum 6061-T6"
    )
    assert material["ok"], material
    fixed = fem_add_constraint.run(
        service, analysis_name, "Fixed support",
        {"type": "fixed", "references": [
            {"object_name": model_name, "element": fixed_face}
        ]},
    )
    assert fixed["ok"], fixed
    force = fem_add_constraint.run(
        service, analysis_name, "100 newton end load",
        {
            "type": "force", "force_n": 100.0, "reversed": False,
            "references": [{"object_name": model_name, "element": loaded_face}],
        },
    )
    assert force["ok"], force
    mesh_start = fem_mesh_analysis.run(
        service,
        {
            "action": "start", "analysis_name": analysis_name,
            "source_object_name": model_name, "max_element_size_mm": 4.0,
            "element_order": "1st", "label": "Four millimeter FEM mesh",
        },
    )
    assert mesh_start["ok"], mesh_start
    mesh_operation_id = mesh_start["transaction"]["result"]["operation_id"]
    mesh_result = _poll(fem_mesh_analysis.run, service, mesh_operation_id)
    assert mesh_result["ok"], mesh_result
    mesh_name = mesh_result["transaction"]["result"]["mesh_object"]
    assert mesh_result["transaction"]["result"]["mesh"]["volume_element_count"] > 0

    solve_start = fem_solve.run(
        service, {"action": "start", "analysis_name": analysis_name}
    )
    assert solve_start["ok"], solve_start
    solve_operation_id = solve_start["transaction"]["result"]["operation_id"]
    solve_result = _poll(fem_solve.run, service, solve_operation_id)
    assert solve_result["ok"], solve_result
    solve_payload = solve_result["transaction"]["result"]
    assert solve_payload["result_completeness"]["complete"] is True, solve_payload
    result_names = solve_payload["created_results"] + solve_payload["changed_results"]
    assert result_names, solve_payload

    candidate_probe = root / "fem-candidate-probe.FCStd"
    document.saveCopy(str(candidate_probe))
    probe_validation = validate_saved_document(candidate_probe)
    assert probe_validation["ok"], probe_validation
    child_result = coordinator.promote(
        child_prepared,
        _record(
            project_id, parent["revision_id"],
            "Mesh and solve a fixed cantilever with a 100 newton end load",
            "fem-child-2",
            [
                "fem.create_analysis", "fem.add_material", "fem.add_constraint",
                "fem.mesh_analysis", "fem.solve",
            ],
            [analysis_name, solver_name, mesh_name, *result_names],
        ),
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    child = child_result["revision"]
    assert child_result["validation"]["fem_checks"] >= 1
    comparison = coordinator.revisions.compare(parent["revision_id"], child["revision_id"])
    assert comparison["changed"] is True

    App.closeDocument(document.Name)
    document = App.openDocument(str(canonical))
    document.recompute()
    service.document = document
    reopened_validation = validate_saved_document(canonical)
    assert reopened_validation["ok"], reopened_validation
    analysis = document.getObject(analysis_name)
    assert analysis is not None
    member_names = {member.Name for member in list(analysis.Group or [])}
    assert {solver_name, mesh_name, *result_names}.issubset(member_names), member_names

    coordinator.restore_revision(
        parent["revision_id"], canonical,
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    assert document.getObject(model_name) is not None
    assert document.getObject(analysis_name) is None
    App.closeDocument(document.Name)
    document = App.openDocument(str(canonical))
    document.recompute()
    assert document.getObject(model_name) is not None
    assert document.getObject(analysis_name) is None
    App.closeDocument(document.Name)
    temporary.cleanup()
    print("VibeCAD FEM acceptance FreeCAD integration passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
