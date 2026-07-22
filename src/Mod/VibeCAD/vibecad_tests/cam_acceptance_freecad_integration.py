# SPDX-License-Identifier: LGPL-2.1-or-later
"""Accepted native CAM job, path, post-process, reopen, and restore integration."""

import hashlib
import json
from pathlib import Path as FilePath
import tempfile

import FreeCAD as App
import Part

from VibeCADAcceptance import VibeCADAcceptanceCoordinator
from VibeCADDocumentValidator import validate_saved_document
from VibeCADRevision import create_revision_record
from tool_impl.service import (
    cam_add_operation, cam_add_tool, cam_create_job, cam_list_jobs, cam_postprocess,
)


class Service:
    def __init__(self, document, root):
        self.document = document
        self.root = root

    def _active_document(self):
        return self.document

    def _get_cam_job(self, name=None):
        if name:
            return self.document.getObject(name)
        jobs = [
            obj for obj in self.document.Objects
            if all(hasattr(obj, prop) for prop in ("Model", "Stock", "Tools", "Operations"))
        ]
        return jobs[0] if len(jobs) == 1 else None

    def _cam_jobs(self):
        return [
            obj for obj in self.document.Objects
            if all(hasattr(obj, prop) for prop in ("Model", "Stock", "Tools", "Operations"))
        ]

    def _cam_job_summary(self, job):
        return {
            "name": job.Name, "label": job.Label,
            "tools": [obj.Name for obj in job.Tools.Group],
            "operations": [obj.Name for obj in job.Operations.Group],
            "stock": job.Stock.Name if job.Stock else None,
        }

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
        validation_results=[{"name": "cam_semantic_reopen", "ok": True}],
        provider="integration", model="deterministic",
        timestamp="2026-07-23T01:00:00Z", generated_source=None,
        preview_image=None, rollback={"available": True},
        transaction_id=revision, document_revision=revision,
    )


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-cam-acceptance-")
    root = FilePath(temporary.name)
    project_root = root / "project"
    canonical = root / "cam-workflow.FCStd"
    metadata = project_root / "accepted-head.json"
    project_id = "cam-acceptance-integration"
    document = App.newDocument("CAMAcceptanceIntegration")
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
    model = document.addObject("Part::Feature", "MachinedModel")
    model.Label = "Pocketed and drilled rectangular block"
    blank = Part.makeBox(40, 30, 10)
    pocket = Part.makeBox(18, 12, 4, App.Vector(11, 9, 6))
    first_hole = Part.makeCylinder(2, 10, App.Vector(6, 6, 0))
    second_hole = Part.makeCylinder(2, 10, App.Vector(34, 24, 0))
    model.Shape = blank.cut(pocket.fuse(first_hole).fuse(second_hole))
    document.recompute()
    model_name = model.Name
    parent_result = coordinator.promote(
        parent_prepared,
        _record(
            project_id, None, "Create a solid block for machining", "cam-parent-1",
            ["part.create_box"], [model_name],
        ),
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    parent = parent_result["revision"]

    App.setActiveDocument(document.Name)
    child_prepared = coordinator.prepare(canonical, save_copy)
    created = cam_create_job.run(
        service, "Multi-operation job", [model_name], {"x": 1, "y": 1, "z": 1}
    )
    assert created["ok"], created
    job_name = created["transaction"]["result"]["job"]
    added_tool = cam_add_tool.run(
        service, job_name, "Six millimeter endmill",
        {
            "shape": "endmill", "diameter_mm": 6, "length_mm": 50,
            "flutes": 2, "cutting_edge_height_mm": 20,
            "shank_diameter_mm": 6,
        },
        1, 12000, 600, 200,
    )
    assert added_tool["ok"], added_tool
    controller_name = added_tool["transaction"]["result"]["tool_controller"]
    added_drill = cam_add_tool.run(
        service, job_name, "Four millimeter drill",
        {
            "shape": "drill", "diameter_mm": 4, "length_mm": 50,
            "flutes": 2, "tip_angle_deg": 118,
        },
        2, 8000, 250, 100,
    )
    assert added_drill["ok"], added_drill
    drill_controller = added_drill["transaction"]["result"]["tool_controller"]
    job = document.getObject(job_name)
    clone = list(job.Model.Group)[0]
    pocket_faces = []
    hole_faces = []
    for index, face in enumerate(clone.Shape.Faces, start=1):
        surface = face.Surface
        if surface.TypeId == "Part::GeomPlane" and abs(face.CenterOfMass.z - 6.0) < 1.0e-6:
            pocket_faces.append({"object_name": clone.Name, "face_name": f"Face{index}"})
        if surface.TypeId == "Part::GeomCylinder" and abs(float(surface.Radius) - 2.0) < 1.0e-6:
            hole_faces.append({"object_name": clone.Name, "face_name": f"Face{index}"})
    assert len(pocket_faces) == 1, pocket_faces
    assert len(hole_faces) == 2, hole_faces
    profile = cam_add_operation.run(
        service, job_name, "Outside profile", 10.0, 0.0, 1.0,
        {"type": "profile", "side": "outside", "step_down_mm": 2.0},
        controller_name,
    )
    assert profile["ok"], profile
    pocket_result = cam_add_operation.run(
        service, job_name, "Central pocket", 10.0, 6.0, 1.0,
        {"type": "pocket", "faces": pocket_faces, "step_down_mm": 2.0,
         "step_over_percent": 50},
        controller_name,
    )
    assert pocket_result["ok"], pocket_result
    drilling = cam_add_operation.run(
        service, job_name, "Two drilled holes", 10.0, 0.0, 1.0,
        {"type": "drilling", "faces": hole_faces, "peck_depth_mm": 2.0},
        drill_controller,
    )
    assert drilling["ok"], drilling
    operation_names = [
        result["transaction"]["result"]["operation_object"]
        for result in (profile, pocket_result, drilling)
    ]
    command_counts = {
        name: len(document.getObject(name).Path.Commands) for name in operation_names
    }
    assert all(count > 0 for count in command_counts.values()), command_counts
    assert [obj.Name for obj in job.Operations.Group] == operation_names
    candidate_probe = root / "cam-candidate-probe.FCStd"
    document.saveCopy(str(candidate_probe))
    probe_validation = validate_saved_document(candidate_probe)
    assert probe_validation["ok"], probe_validation
    child_result = coordinator.promote(
        child_prepared,
        _record(
            project_id, parent["revision_id"],
            "Create profile, pocket, and drilling paths with exact tools",
            "cam-child-2", ["cam.create_job", "cam.add_tool", "cam.add_operation"],
            [job_name, controller_name, drill_controller, *operation_names],
        ),
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    child = child_result["revision"]
    assert child_result["validation"]["cam_checks"] == 1
    comparison = coordinator.revisions.compare(parent["revision_id"], child["revision_id"])
    assert comparison["changed"] is True

    App.closeDocument(document.Name)
    document = App.openDocument(str(canonical))
    document.recompute()
    service.document = document
    summary = cam_list_jobs.run(service)
    assert summary["ok"] and summary["job_count"] == 1, summary
    reopened_job = document.getObject(job_name)
    assert [obj.Name for obj in reopened_job.Operations.Group] == operation_names
    assert [obj.Name for obj in reopened_job.Tools.Group] == [controller_name, drill_controller]
    for name, command_count in command_counts.items():
        reopened_operation = document.getObject(name)
        assert reopened_operation in list(reopened_job.Operations.Group)
        assert len(reopened_operation.Path.Commands) == command_count
    output = cam_postprocess.run(
        service, job_name, "grbl", "metric", True, False, "multi-operation-program"
    )
    assert output["ok"], output
    artifact = FilePath(output["artifact"]["path"])
    assert artifact.is_file() and artifact.stat().st_size > 0
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == output["artifact"]["sha256"]
    assert output["artifact"]["machine_limits_checked"] is False

    coordinator.restore_revision(
        parent["revision_id"], canonical,
        save_copy=save_copy, validate_document=validate_saved_document,
        restore_live=restore_live, write_metadata=write_metadata,
    )
    assert document.getObject(model_name) is not None
    assert document.getObject(job_name) is None
    assert all(document.getObject(name) is None for name in operation_names)
    App.closeDocument(document.Name)
    document = App.openDocument(str(canonical))
    document.recompute()
    assert document.getObject(model_name) is not None
    assert document.getObject(job_name) is None
    App.closeDocument(document.Name)
    temporary.cleanup()
    print("VibeCAD CAM acceptance FreeCAD integration passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
