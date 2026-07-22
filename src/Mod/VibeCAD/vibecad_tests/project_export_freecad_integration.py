# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real FreeCAD round-trip checks for typed project exports."""

from pathlib import Path
import tempfile

import FreeCAD as App
import Mesh
import Part

from tool_impl.service import project_export


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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vibecad-export-") as temporary:
        root = Path(temporary)
        document = App.newDocument("TypedExportIntegration")
        feature = document.addObject("PartDesign::Feature", "Printable")
        feature.Shape = Part.makeBox(12, 18, 6).cut(
            Part.makeCylinder(2, 6, App.Vector(6, 9, 0))
        )
        document.recompute()
        service = Service(document, root)
        results = {
            format_name: project_export.run(
                service, [feature.Name], format_name, f"printable-{format_name}"
            )
            for format_name in ("stl", "3mf", "obj", "step", "iges")
        }
        assert all(result["ok"] for result in results.values()), results
        for format_name in ("stl", "3mf", "obj"):
            mesh = Mesh.Mesh(results[format_name]["export"]["path"])
            assert mesh.CountFacets > 0, format_name
        for format_name in ("step", "iges"):
            shape = Part.read(results[format_name]["export"]["path"])
            assert not shape.isNull() and shape.isValid(), format_name
        blocked = project_export.run(
            service, [feature.Name], "3mf", "printable-3mf"
        )
        assert blocked["ok"] is False and "already exists" in blocked["error"]
        App.closeDocument(document.Name)
    print("VibeCAD project export FreeCAD integration passed")
    return 0
