# SPDX-License-Identifier: LGPL-2.1-or-later
"""Accept, reopen, restore, and compare one native exploded-view graph."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

import FreeCAD as App
import Part

from VibeCADAcceptance import VibeCADAcceptanceCoordinator
from VibeCADAssemblyExplodedView import (
    METADATA_PROPERTY,
    load_view_metadata,
    placement_fact,
    placement_from_fact,
    validate_native_configuration,
)
from VibeCADDocumentValidator import validate_open_document, validate_saved_document
from VibeCADRevision import create_revision_record
from VibeCADSession import _changed_objects
from tool_impl.service import (
    assembly_create_assembly,
    assembly_create_exploded_view,
    assembly_insert_component,
    assembly_restore_exploded_view,
)


class Service:
    def __init__(self, document):
        self.document = document
        self.permissions: list[str] = []

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

    def authorize(self, permission):
        assert permission == "design.modify", permission
        self.permissions.append(permission)


def _record(
    project_id,
    parent,
    request,
    document_revision,
    tool_operations,
    changed_objects,
    timestamp,
):
    return create_revision_record(
        project_id=project_id,
        parent_revision=parent,
        user_request=request,
        interpreted_intent=request,
        assumptions=[
            "Exploded views are stored as native editable view steps, not motion simulation."
        ],
        plan=[{"operation": item["tool"]} for item in tool_operations],
        tool_operations=tool_operations,
        changed_objects=changed_objects,
        validation_results=[
            {"name": "exploded_view_save_close_reopen_recompute", "ok": True}
        ],
        provider="integration",
        model="deterministic",
        timestamp=timestamp,
        generated_source=None,
        preview_image=None,
        rollback={"available": True},
        transaction_id=document_revision,
        document_revision=document_revision,
    )


def _close_reopen(document, canonical):
    name = document.Name
    App.closeDocument(name)
    reopened = App.openDocument(str(canonical))
    reopened.recompute()
    App.setActiveDocument(reopened.Name)
    return reopened


def _same_placement(first, second):
    return placement_fact(first) == placement_fact(second)


def main() -> int:
    if not __debug__:
        raise RuntimeError(
            "The exploded-view acceptance test requires Python assertions."
        )
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-exploded-view-")
    root = Path(temporary.name)
    project_root = root / "project"
    canonical = root / "exploded-assembly.FCStd"
    accepted_head = project_root / "accepted-head.json"
    project_id = "native-exploded-view-integration"
    document = App.newDocument("ExplodedViewAcceptanceIntegration")
    document.saveAs(str(canonical))
    service = Service(document)
    coordinator = VibeCADAcceptanceCoordinator(project_root, project_id)

    def save_copy(path):
        document.saveCopy(str(path))

    def restore_live(_path):
        document.restore()
        document.recompute()

    def write_metadata(revision_id):
        accepted_head.parent.mkdir(parents=True, exist_ok=True)
        accepted_head.write_text(
            json.dumps({"accepted_revision": revision_id}, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    exploded_prepared = coordinator.prepare(canonical, save_copy)
    first_source = document.addObject("Part::Feature", "BasePlate")
    first_source.Shape = Part.makeBox(20, 20, 4)
    second_source = document.addObject("Part::Feature", "CoverPlate")
    second_source.Shape = Part.makeBox(16, 16, 3)
    first_source_name = str(first_source.Name)
    second_source_name = str(second_source.Name)
    document.recompute()
    created = assembly_create_assembly.run(service, "Two plate assembly")
    assert created["ok"], created
    assembly = service._assembly_objects()[0]
    assembly_name = str(assembly.Name)
    first = assembly_insert_component.run(
        service,
        assembly_name,
        first_source_name,
        "Base occurrence",
        {"x": 0, "y": 0, "z": 0},
    )
    second = assembly_insert_component.run(
        service,
        assembly_name,
        second_source_name,
        "Cover occurrence",
        {"x": 2, "y": 2, "z": 4},
    )
    assert first["ok"] and second["ok"], (first, second)
    first_name = first["mutation"]["component"]
    second_name = second["mutation"]["component"]
    assembled_before = {
        name: document.getObject(name).Placement.copy()
        for name in (first_name, second_name)
    }
    object_names_before_failed_create = {obj.Name for obj in document.Objects}
    assembly_properties_before_failed_create = set(assembly.PropertiesList)

    def fail_after_provenance(stage):
        if stage == "after_assembly_provenance":
            raise RuntimeError("injected exploded-view create provenance failure")

    failed_create = assembly_create_exploded_view.run(
        service,
        assembly_name,
        "Failed exploded view",
        [
            {"component_name": first_name, "distance_mm": 12.5},
            {"component_name": second_name, "distance_mm": 30.0},
        ],
        {"x": 0, "y": 0, "z": 1},
        fault=fail_after_provenance,
    )
    assert failed_create["ok"] is False, failed_create
    assert failed_create["transaction"]["rollback_attempted"] is True
    assert failed_create["transaction"]["rollback_succeeded"] is True, failed_create
    assert failed_create["document_delta"]["created_objects"] == []
    assert failed_create["document_delta"]["changed_objects"] == []
    assert {obj.Name for obj in document.Objects} == object_names_before_failed_create
    assert set(assembly.PropertiesList) == assembly_properties_before_failed_create
    for name in (first_name, second_name):
        assert _same_placement(document.getObject(name).Placement, assembled_before[name])
    exploded = assembly_create_exploded_view.run(
        service,
        assembly_name,
        "Service exploded view",
        [
            {"component_name": first_name, "distance_mm": 12.5},
            {"component_name": second_name, "distance_mm": 30.0},
        ],
        {"x": 0, "y": 0, "z": 1},
    )
    assert exploded["ok"], exploded
    assert exploded["mutation"]["native_view_proxy"] == "ExplodedView"
    assert "remain assembled" in exploded["mutation"]["state_meaning"]
    assert exploded["mutation"]["native_step_proxies"] == [
        "ExplodedViewStep", "ExplodedViewStep"
    ]
    view_name = exploded["mutation"]["view"]
    configuration = exploded["mutation"]["configuration_id"]
    content_digest = exploded["mutation"]["metadata"]["content_sha256"]
    assert service.permissions == ["design.modify", "design.modify"]
    for name in (first_name, second_name):
        assert _same_placement(document.getObject(name).Placement, assembled_before[name])
    creation_changes = _changed_objects(
        [
            {"result": created},
            {"result": first},
            {"result": second},
            {"result": exploded},
        ]
    )
    assert len(creation_changes) == len(
        {(item["object"], item["change"]) for item in creation_changes}
    ), creation_changes
    creation_change_names = {item["object"] for item in creation_changes}
    assert {assembly_name, first_name, second_name, view_name}.issubset(
        creation_change_names
    )
    assert set(exploded["mutation"]["steps"]).issubset(creation_change_names)

    exploded_result = coordinator.promote(
        exploded_prepared,
        _record(
            project_id,
            None,
            "Create an exact editable exploded-view configuration",
            "exploded-view-1",
            [
                {"tool": "assembly.create_assembly", "ok": True},
                {"tool": "assembly.insert_component", "ok": True},
                {
                    "tool": "assembly.create_exploded_view",
                    "ok": True,
                    "configuration_id": configuration,
                    "metadata_sha256": content_digest,
                },
            ],
            creation_changes,
            "2026-07-23T01:00:00Z",
        ),
        save_copy=save_copy,
        validate_document=validate_saved_document,
        restore_live=restore_live,
        write_metadata=write_metadata,
    )
    exploded_revision = exploded_result["revision"]
    assert exploded_result["validation"]["exploded_view_checks"] == 1
    assert coordinator.revisions.head()["revision_id"] == exploded_revision["revision_id"]
    stored_revision = coordinator.revisions.read(exploded_revision["revision_id"])
    create_operation = next(
        item
        for item in stored_revision["tool_operations"]
        if item["tool"] == "assembly.create_exploded_view"
    )
    assert create_operation["configuration_id"] == configuration
    assert create_operation["metadata_sha256"] == content_digest
    assert json.loads(accepted_head.read_text(encoding="utf-8"))[
        "accepted_revision"
    ] == exploded_revision["revision_id"]

    document = _close_reopen(document, canonical)
    service.document = document
    reopened_validation = validate_saved_document(canonical)
    assert reopened_validation["ok"], reopened_validation
    reopened_assembly = document.getObject(assembly_name)
    reopened_view = document.getObject(view_name)
    reopened_contract = validate_native_configuration(reopened_assembly, reopened_view)
    assert reopened_contract["ok"], reopened_contract
    if App.GuiUp:
        assert type(reopened_view.ViewObject.Proxy).__name__ == (
            "ViewProviderExplodedView"
        )
        assert all(
            type(step.ViewObject.Proxy).__name__ == "ViewProviderExplodedViewStep"
            for step in reopened_view.Group
        )
        reopened_view.Proxy.explodeTemporarily(reopened_view)
        assert abs(document.getObject(first_name).Placement.Base.z - 12.5) < 1.0e-8
        assert abs(document.getObject(second_name).Placement.Base.z - 34.0) < 1.0e-8
        reopened_view.Proxy.restoreAssembly(reopened_view)
        document.recompute()
        for name in (first_name, second_name):
            assert _same_placement(
                document.getObject(name).Placement, assembled_before[name]
            )
    assert reopened_contract["metadata"]["state"] == "exploded"
    assert reopened_contract["metadata"]["configuration_id"] == configuration
    calculated, lines = reopened_view.Proxy._calculateExplodedPlacements(reopened_view)
    assert len(lines) == 2
    assert abs(calculated[document.getObject(first_name)].Base.z - 12.5) < 1.0e-8
    assert abs(calculated[document.getObject(second_name)].Base.z - 34.0) < 1.0e-8
    for name in (first_name, second_name):
        assert _same_placement(document.getObject(name).Placement, assembled_before[name])

    saved_metadata = str(getattr(reopened_view, METADATA_PROPERTY))
    malformed_candidate = root / "malformed-exploded-metadata.FCStd"
    setattr(reopened_view, METADATA_PROPERTY, "{not-json")
    document.recompute()
    document.saveCopy(str(malformed_candidate))
    setattr(reopened_view, METADATA_PROPERTY, saved_metadata)
    document.recompute()
    malformed_validation = validate_saved_document(malformed_candidate)
    assert malformed_validation["ok"] is False
    assert any("metadata is malformed" in item for item in malformed_validation["errors"])

    link_drift_candidate = root / "exploded-link-drift.FCStd"
    reopened_second = document.getObject(second_name)
    saved_source = reopened_second.LinkedObject
    reopened_second.LinkedObject = document.getObject(first_source_name)
    document.recompute()
    document.saveCopy(str(link_drift_candidate))
    reopened_second.LinkedObject = saved_source
    document.recompute()
    link_drift_validation = validate_saved_document(link_drift_candidate)
    assert link_drift_validation["ok"] is False
    assert any("linked source changed" in item for item in link_drift_validation["errors"])

    placement_drift_candidate = root / "exploded-placement-drift.FCStd"
    reopened_first = document.getObject(first_name)
    saved_first_placement = reopened_first.Placement.copy()
    reopened_first.Placement.Base.x += 1.0
    document.recompute()
    document.saveCopy(str(placement_drift_candidate))
    reopened_first.Placement = saved_first_placement
    document.recompute()
    placement_drift_validation = validate_saved_document(placement_drift_candidate)
    assert placement_drift_validation["ok"] is False
    assert any(
        "assembled placement changed" in item
        for item in placement_drift_validation["errors"]
    )

    restore_prepared = coordinator.prepare(canonical, save_copy)
    metadata = load_view_metadata(reopened_view)["metadata"]
    for item in metadata["components"]:
        document.getObject(item["component_name"]).Placement = placement_from_fact(
            item["exploded_placement"]
        )
    document.recompute()
    drift_validation = validate_open_document(document)
    assert drift_validation["ok"] is False
    assert any("assembled placement changed" in item for item in drift_validation["errors"])
    before_failed_restore = {
        name: document.getObject(name).Placement.copy()
        for name in (first_name, second_name)
    }
    metadata_before_failed_restore = str(getattr(reopened_view, METADATA_PROPERTY))
    original_seal_metadata = assembly_restore_exploded_view.seal_metadata

    def fail_seal(_metadata):
        raise RuntimeError("injected exploded-view metadata seal failure")

    assembly_restore_exploded_view.seal_metadata = fail_seal
    try:
        failed_restore = assembly_restore_exploded_view.run(
            service, reopened_assembly.Name, reopened_view.Name
        )
    finally:
        assembly_restore_exploded_view.seal_metadata = original_seal_metadata
    assert failed_restore["ok"] is False, failed_restore
    assert failed_restore["transaction"]["rollback_attempted"] is True
    assert failed_restore["transaction"]["rollback_succeeded"] is True, failed_restore
    assert failed_restore["document_delta"]["changed_objects"] == [], failed_restore
    assert str(getattr(reopened_view, METADATA_PROPERTY)) == metadata_before_failed_restore
    for name in (first_name, second_name):
        assert _same_placement(
            document.getObject(name).Placement, before_failed_restore[name]
        )
    restored = assembly_restore_exploded_view.run(
        service, reopened_assembly.Name, reopened_view.Name
    )
    assert restored["ok"], restored
    assert restored["mutation"]["configuration_id"] == configuration
    assert "assembled state" in restored["mutation"]["state_meaning"]
    assert restored["mutation"]["generation"] == 2
    assert restored["mutation"]["previous_content_sha256"] == content_digest
    assert service.permissions == ["design.modify"] * 4
    for name in (first_name, second_name):
        assert _same_placement(document.getObject(name).Placement, assembled_before[name])
    restore_changes = _changed_objects([{"result": restored}])
    assert len(restore_changes) == len(
        {(item["object"], item["change"]) for item in restore_changes}
    ), restore_changes
    restore_change_names = {item["object"] for item in restore_changes}
    assert {first_name, second_name, view_name}.issubset(restore_change_names)

    restored_result = coordinator.promote(
        restore_prepared,
        _record(
            project_id,
            exploded_revision["revision_id"],
            "Restore the exact assembled placements",
            "exploded-view-restore-2",
            [
                {
                    "tool": "assembly.restore_exploded_view",
                    "ok": True,
                    "configuration_id": configuration,
                    "metadata_sha256": restored["mutation"]["content_sha256"],
                }
            ],
            restore_changes,
            "2026-07-23T01:01:00Z",
        ),
        save_copy=save_copy,
        validate_document=validate_saved_document,
        restore_live=restore_live,
        write_metadata=write_metadata,
    )
    restored_revision = restored_result["revision"]
    assert restored_result["validation"]["exploded_view_checks"] == 1
    assert coordinator.revisions.head()["revision_id"] == restored_revision["revision_id"]
    comparison = coordinator.revisions.compare(
        exploded_revision["revision_id"], restored_revision["revision_id"]
    )
    assert comparison["changed"] is True
    assert comparison["changes"]["tool_operations"]["left"][-1]["tool"] == (
        "assembly.create_exploded_view"
    )
    assert comparison["changes"]["tool_operations"]["right"][0]["tool"] == (
        "assembly.restore_exploded_view"
    )

    document = _close_reopen(document, canonical)
    service.document = document
    final_validation = validate_saved_document(canonical)
    assert final_validation["ok"], final_validation
    final_assembly = document.getObject(assembly_name)
    final_view = document.getObject(view_name)
    final_contract = validate_native_configuration(final_assembly, final_view)
    assert final_contract["ok"], final_contract
    assert final_contract["metadata"]["state"] == "assembled"
    assert final_contract["metadata"]["generation"] == 2
    assert final_contract["metadata"]["previous_content_sha256"] == content_digest
    assert final_contract["metadata"]["configuration_id"] == configuration
    for name in (first_name, second_name):
        assert _same_placement(document.getObject(name).Placement, assembled_before[name])
    assert json.loads(accepted_head.read_text(encoding="utf-8"))[
        "accepted_revision"
    ] == restored_revision["revision_id"]
    App.closeDocument(document.Name)
    temporary.cleanup()
    print("VibeCAD native exploded-view acceptance integration passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
