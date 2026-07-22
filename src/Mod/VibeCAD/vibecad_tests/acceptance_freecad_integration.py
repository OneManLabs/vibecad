# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real FreeCAD save, reopen, restore, recompute, and compare acceptance test."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

import FreeCAD as App
import Part

from VibeCADAcceptance import VibeCADAcceptanceCoordinator
from VibeCADRevision import create_revision_record
from VibeCADRevisionExport import create_revision_branch


def _record(project_id: str, parent: str | None, request: str, document_revision: str):
    return create_revision_record(
        project_id=project_id,
        parent_revision=parent,
        user_request=request,
        interpreted_intent=request,
        assumptions=[],
        plan=[{"operation": "create_box"}],
        tool_operations=[{"tool": "integration.create_box", "ok": True}],
        changed_objects=[{"name": "Box", "change": "created"}],
        validation_results=[{"name": "shape_valid", "ok": True}],
        provider="integration",
        model="deterministic",
        timestamp="2026-07-22T14:00:00Z",
        generated_source=None,
        preview_image=None,
        rollback={"available": True},
        transaction_id=request,
        document_revision=document_revision,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vibecad-acceptance-") as temporary:
        root = Path(temporary)
        canonical = root / "part.FCStd"
        project_root = root / "project"
        project_id = "freecad-integration"
        metadata_path = project_root / "accepted-head.json"
        doc = App.newDocument("AcceptanceIntegration")
        doc.saveAs(str(canonical))
        coordinator = VibeCADAcceptanceCoordinator(project_root, project_id)

        def save_copy(path: Path) -> None:
            doc.saveCopy(str(path))

        def validate(path: Path) -> dict:
            reopened = App.openDocument(str(path), True, True)
            try:
                reopened.recompute()
                valid = all(
                    not hasattr(obj, "Shape") or obj.Shape.isNull() is False
                    for obj in reopened.Objects
                )
                return {"ok": valid, "object_count": len(reopened.Objects)}
            finally:
                App.closeDocument(reopened.Name)

        def restore_live(_path: Path) -> None:
            doc.restore()
            doc.recompute()

        def write_metadata(revision_id: str | None) -> None:
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.write_text(
                json.dumps({"accepted_revision": revision_id}) + "\n",
                encoding="utf-8",
            )

        first_prepared = coordinator.prepare(canonical, save_copy)
        first = doc.addObject("PartDesign::Feature", "Box")
        first.Shape = Part.makeBox(10, 20, 30)
        doc.recompute()
        first_record = _record(project_id, None, "create first box", "doc-1")
        first_result = coordinator.promote(
            first_prepared,
            first_record,
            save_copy=save_copy,
            validate_document=validate,
            restore_live=restore_live,
            write_metadata=write_metadata,
        )
        first_revision = first_result["revision"]
        first_names = [obj.Name for obj in doc.Objects]
        assert first_names == ["Box"]
        App.closeDocument(doc.Name)
        doc = App.openDocument(str(canonical))
        doc.recompute()
        assert [obj.Name for obj in doc.Objects] == first_names
        branch_path = root / "first-revision-branch.FCStd"
        branch_result = create_revision_branch(
            coordinator.revisions, first_revision["revision_id"], branch_path
        )
        assert Path(branch_result["lineage_path"]).is_file()
        branch = App.openDocument(str(branch_path), True, True)
        try:
            branch.recompute()
            assert [obj.Name for obj in branch.Objects] == first_names
            assert branch.getObject("Box").Shape.isValid()
        finally:
            App.closeDocument(branch.Name)

        second_prepared = coordinator.prepare(canonical, save_copy)
        second = doc.addObject("PartDesign::Feature", "SecondBox")
        second.Shape = Part.makeBox(5, 5, 5)
        doc.recompute()
        second_record = _record(
            project_id,
            first_revision["revision_id"],
            "create second box",
            "doc-2",
        )

        second_result = coordinator.promote(
            second_prepared,
            second_record,
            save_copy=save_copy,
            validate_document=validate,
            restore_live=restore_live,
            write_metadata=write_metadata,
        )
        second_revision = second_result["revision"]
        assert [obj.Name for obj in doc.Objects] == ["Box", "SecondBox"]
        comparison = coordinator.revisions.compare(
            first_revision["revision_id"], second_revision["revision_id"]
        )
        assert comparison["changed"] is True
        App.closeDocument(doc.Name)
        doc = App.openDocument(str(canonical))
        doc.recompute()
        assert [obj.Name for obj in doc.Objects] == ["Box", "SecondBox"]

        coordinator.restore_revision(
            first_revision["revision_id"],
            canonical,
            save_copy=save_copy,
            validate_document=validate,
            restore_live=restore_live,
            write_metadata=write_metadata,
        )
        assert [obj.Name for obj in doc.Objects] == first_names
        assert coordinator.revisions.head()["revision_id"] == first_revision["revision_id"]

        third_prepared = coordinator.prepare(canonical, save_copy)
        third = doc.addObject("PartDesign::Feature", "FailedBox")
        third.Shape = Part.makeBox(2, 2, 2)
        doc.recompute()
        third_record = _record(
            project_id,
            first_revision["revision_id"],
            "create failed box",
            "doc-3",
        )

        def fail_after_head(boundary: str) -> None:
            if boundary == "after_head_promotion":
                raise OSError("injected provenance failure")

        try:
            coordinator.promote(
                third_prepared,
                third_record,
                save_copy=save_copy,
                validate_document=validate,
                restore_live=restore_live,
                write_metadata=write_metadata,
                fault=fail_after_head,
            )
        except RuntimeError as exc:
            assert "prior revision was restored" in str(exc)
        else:
            raise AssertionError("Injected acceptance failure did not fail.")
        assert [obj.Name for obj in doc.Objects] == first_names
        assert coordinator.revisions.head()["revision_id"] == first_revision["revision_id"]
        assert json.loads(metadata_path.read_text(encoding="utf-8"))["accepted_revision"] == first_revision["revision_id"]
        App.closeDocument(doc.Name)
        doc = App.openDocument(str(canonical))
        doc.recompute()
        assert [obj.Name for obj in doc.Objects] == first_names
        assert doc.getObject("Box").Shape.isValid()
        App.closeDocument(doc.Name)
    print("VibeCAD FreeCAD acceptance integration passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
