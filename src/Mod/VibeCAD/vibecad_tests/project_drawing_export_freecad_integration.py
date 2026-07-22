# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real FreeCAD checks for page-specific PDF, DXF, and SVG export."""

from pathlib import Path
import importlib.util
import json
import sys
import tempfile

import FreeCAD as App
import Part

from VibeCADAcceptance import VibeCADAcceptanceCoordinator
from VibeCADDocumentValidator import validate_saved_document
from VibeCADRevision import create_revision_record
from tool_impl.service import (
    techdraw_add_annotation,
    techdraw_add_dimension,
    techdraw_add_view,
    techdraw_create_page,
)

SCRIPT_PATH = Path(
    globals().get(
        "__file__",
        "src/Mod/VibeCAD/vibecad_tests/project_drawing_export_freecad_integration.py",
    )
).resolve()
sys.path.insert(0, str(SCRIPT_PATH.parents[1]))
EXPORTER_PATH = SCRIPT_PATH.parents[1] / "tool_impl/service/project_export_drawing.py"
EXPORTER_SPEC = importlib.util.spec_from_file_location(
    "vibecad_source_project_export_drawing", EXPORTER_PATH
)
assert EXPORTER_SPEC is not None and EXPORTER_SPEC.loader is not None
project_export_drawing = importlib.util.module_from_spec(EXPORTER_SPEC)
EXPORTER_SPEC.loader.exec_module(project_export_drawing)


class Service:
    def __init__(self, document, root: Path):
        self.document = document
        self.root = root

    def authorize(self, _permission):
        return None

    def _active_document(self):
        return self.document

    def project_scope_snapshot(self):
        return {"root": str(self.root)}

    def record_audit_event(self, **_event):
        return None


def _revision_record(
    project_id: str,
    parent: str | None,
    request: str,
    document_revision: str,
    operations: list[str],
    changed_objects: list[str],
):
    return create_revision_record(
        project_id=project_id,
        parent_revision=parent,
        user_request=request,
        interpreted_intent=request,
        assumptions=[],
        plan=[{"operation": operation} for operation in operations],
        tool_operations=[{"tool": operation, "ok": True} for operation in operations],
        changed_objects=[{"name": name, "change": "created"} for name in changed_objects],
        validation_results=[{"name": "document_reopen", "ok": True}],
        provider="integration",
        model="deterministic",
        timestamp="2026-07-22T22:00:00Z",
        generated_source=None,
        preview_image=None,
        rollback={"available": True},
        transaction_id=document_revision,
        document_revision=document_revision,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vibecad-drawing-export-") as temporary:
        root = Path(temporary)
        project_root = root / "project"
        project_id = "drawing-package-integration"
        canonical = root / "bracket.FCStd"
        metadata_path = project_root / "accepted-head.json"
        document = App.newDocument("DrawingExportIntegration")
        document.saveAs(str(canonical))
        coordinator = VibeCADAcceptanceCoordinator(project_root, project_id)
        expected_drawing = False

        def save_copy(path: Path) -> None:
            document.saveCopy(str(path))

        def validate(path: Path) -> dict:
            reopened = App.openDocument(str(path), True, True)
            try:
                reopened.recompute()
                invalid_shapes = [
                    obj.Name
                    for obj in reopened.Objects
                    if hasattr(obj, "Shape")
                    and (obj.Shape.isNull() or not obj.Shape.isValid())
                ]
                drawing_checks = {"required": expected_drawing, "ok": True}
                if expected_drawing:
                    candidate_page = reopened.getObject(page_name)
                    candidate_view = reopened.getObject(view_result["view"])
                    candidate_dimension = reopened.getObject(
                        dimension_result["dimension"]
                    )
                    candidate_annotation = reopened.getObject(
                        note_result["transaction"]["result"]["annotation"]
                    )
                    projection = (
                        techdraw_add_view._wait_for_projected_elements(candidate_view)
                        if candidate_view is not None
                        else {"ok": False, "error": "view missing"}
                    )
                    if candidate_dimension is not None and candidate_page is not None:
                        candidate_dimension.touch()
                        techdraw_add_view._recompute_page_projection(
                            reopened, candidate_page
                        )
                    value = (
                        float(candidate_dimension.getRawValue())
                        if candidate_dimension is not None
                        else None
                    )
                    drawing_checks = {
                        "required": True,
                        "ok": bool(
                            candidate_page is not None
                            and candidate_view is not None
                            and candidate_dimension is not None
                            and candidate_annotation is not None
                            and projection.get("ok") is True
                            and value in {25.0, 40.0}
                        ),
                        "dimension_mm": value,
                        "projection": projection,
                    }
                return {
                    "ok": not invalid_shapes and drawing_checks["ok"],
                    "object_count": len(reopened.Objects),
                    "invalid_shapes": invalid_shapes,
                    "drawing": drawing_checks,
                }
            finally:
                App.closeDocument(reopened.Name)

        def restore_live(_path: Path) -> None:
            document.restore()
            document.recompute()

        def write_metadata(revision_id: str | None) -> None:
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.write_text(
                json.dumps({"accepted_revision": revision_id}) + "\n",
                encoding="utf-8",
            )

        base_prepared = coordinator.prepare(canonical, save_copy)
        feature = document.addObject("PartDesign::Feature", "Bracket")
        feature.Shape = Part.makeBox(40, 25, 8)
        feature_name = feature.Name
        document.recompute()
        base_result = coordinator.promote(
            base_prepared,
            _revision_record(
                project_id,
                None,
                "Create the bracket",
                "drawing-base-1",
                ["part.create_feature"],
                [feature.Name],
            ),
            save_copy=save_copy,
            validate_document=validate,
            restore_live=restore_live,
            write_metadata=write_metadata,
        )
        base_revision = base_result["revision"]
        App.setActiveDocument(document.Name)
        drawing_prepared = coordinator.prepare(canonical, save_copy)
        service = Service(document, project_root)
        page_result = techdraw_create_page.run(
            service, "a4_landscape", "Bracket manufacturing drawing"
        )
        assert page_result["ok"], page_result
        page_name = page_result["page"]
        view_result = techdraw_add_view.run(
            service, page_name, [feature.Name], "top", 90, 115, 2.0, "Top view"
        )
        assert view_result["ok"], view_result
        straight_edges = [
            edge
            for edge in view_result["projection"]["edges"]
            if str(edge.get("geometry_type") or "").lower() in {"line", "linesegment"}
            or str(edge.get("curve_type") or "").lower() in {"line", "linesegment"}
        ]
        assert straight_edges, view_result["projection"]
        edge_name = str(straight_edges[0]["name"])
        dimension_result = techdraw_add_dimension.run(
            service,
            page_name,
            view_result["view"],
            {"type": "length", "references": [edge_name]},
        )
        assert dimension_result["ok"], dimension_result
        assert float(dimension_result["value"]) in {25.0, 40.0}
        note_result = techdraw_add_annotation.run(
            service,
            page_name,
            ["BRACKET", "REVISION: integration-1", "UNITS: mm"],
            220,
            30,
            4,
        )
        assert note_result["ok"], note_result
        page = document.getObject(page_name)
        assert page is not None
        expected_drawing = True
        drawing_result = coordinator.promote(
            drawing_prepared,
            _revision_record(
                project_id,
                base_revision["revision_id"],
                "Create a manufacturing drawing with a revision note",
                "drawing-package-2",
                [
                    "techdraw.create_page",
                    "techdraw.add_view",
                    "techdraw.add_dimension",
                    "techdraw.add_annotation",
                ],
                [
                    page_name,
                    view_result["view"],
                    dimension_result["dimension"],
                    note_result["transaction"]["result"]["annotation"],
                ],
            ),
            save_copy=save_copy,
            validate_document=validate,
            restore_live=restore_live,
            write_metadata=write_metadata,
        )
        drawing_revision = drawing_result["revision"]
        comparison = coordinator.revisions.compare(
            base_revision["revision_id"], drawing_revision["revision_id"]
        )
        assert comparison["changed"] is True
        App.closeDocument(document.Name)
        isolated_validation = validate_saved_document(canonical)
        assert isolated_validation["ok"], isolated_validation
        assert isolated_validation["techdraw_checks"] == 3
        document = App.openDocument(str(canonical))
        document.recompute()
        service.document = document
        page = document.getObject(page_name)
        reopened_view = document.getObject(view_result["view"])
        reopened_dimension = document.getObject(dimension_result["dimension"])
        assert page is not None and reopened_view is not None
        assert reopened_dimension is not None
        assert [source.Name for source in reopened_view.Source] == [feature_name]
        reopened_projection = techdraw_add_view._wait_for_projected_elements(
            reopened_view
        )
        assert reopened_projection["ok"], reopened_projection
        reopened_dimension.touch()
        techdraw_add_view._recompute_page_projection(document, page)
        assert float(reopened_dimension.getRawValue()) in {25.0, 40.0}

        results = {
            output_format: project_export_drawing.run(
                service, page.Name, output_format, f"bracket-{output_format}"
            )
            for output_format in ("pdf", "dxf", "svg")
        }
        assert all(result["ok"] for result in results.values()), results
        pdf = Path(results["pdf"]["export"]["path"]).read_bytes()
        dxf = Path(results["dxf"]["export"]["path"]).read_text(
            encoding="utf-8", errors="replace"
        )
        svg = Path(results["svg"]["export"]["path"]).read_text(
            encoding="utf-8", errors="replace"
        )
        assert pdf.startswith(b"%PDF")
        assert "SECTION" in dxf and "EOF" in dxf
        assert "<svg" in svg
        blocked = project_export_drawing.run(
            service, page.Name, "pdf", "bracket-pdf"
        )
        assert blocked["ok"] is False and "already exists" in blocked["error"]
        expected_drawing = False
        coordinator.restore_revision(
            base_revision["revision_id"],
            canonical,
            save_copy=save_copy,
            validate_document=validate,
            restore_live=restore_live,
            write_metadata=write_metadata,
        )
        assert [obj.Name for obj in document.Objects] == [feature_name]
        assert coordinator.revisions.head()["revision_id"] == base_revision["revision_id"]
        App.closeDocument(document.Name)
        document = App.openDocument(str(canonical))
        document.recompute()
        assert [obj.Name for obj in document.Objects] == [feature_name]
        assert document.getObject(feature_name).Shape.isValid()
        App.closeDocument(document.Name)
    print("VibeCAD TechDraw export FreeCAD integration passed")
    return 0


if __name__ == "__main__":
    result = main()
    if App.GuiUp:
        import FreeCADGui as Gui

        Gui.getMainWindow().close()
    raise SystemExit(result)
