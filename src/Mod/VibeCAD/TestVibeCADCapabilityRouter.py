# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native FreeCAD integration checks for deterministic capability routing."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui

from VibeCADCapabilityRouter import ROUTER_SCHEMA, ROUTING_REQUEST_SCHEMA
from VibeCADCore import VibeCADService


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _call(service: VibeCADService, name: str, **arguments):
    result = service.registry.call(name, **arguments)
    if result.get("ok") is not True:
        raise AssertionError(f"{name} failed: {result}")
    return result


def _editable_structure(document) -> dict:
    body = document.getObject("Body")
    sketch = document.getObject("Sketch")
    pad = document.getObject("Pad")
    if body is None or sketch is None or pad is None:
        raise AssertionError("The expected Part Design feature tree is incomplete.")
    shape = getattr(pad, "Shape", None)
    return {
        "body_type": str(body.TypeId),
        "sketch_type": str(sketch.TypeId),
        "pad_type": str(pad.TypeId),
        "body_group": [item.Name for item in list(body.Group)],
        "body_tip": getattr(getattr(body, "Tip", None), "Name", None),
        "sketch_geometry_count": int(sketch.GeometryCount),
        "sketch_constraint_count": int(sketch.ConstraintCount),
        "pad_length": float(pad.Length),
        "shape_valid": bool(shape is not None and not shape.isNull() and shape.isValid()),
        "solid_count": len(list(shape.Solids)) if shape is not None else 0,
    }


class CapabilityRouterIntegrationTest(unittest.TestCase):
    """Prove route application with a native, reopenable Part Design model."""

    def test_route_creates_and_preserves_editable_native_structure(self) -> None:
        previous_home = os.environ.get("VIBECAD_HOME")
        prior_documents = set(App.listDocuments())
        with tempfile.TemporaryDirectory(prefix="vibecad-capability-route-") as directory:
            root = Path(directory)
            os.environ["VIBECAD_HOME"] = str(root / "vibecad-home")
            try:
                Gui.activateWorkbench("PartDesignWorkbench")
                document = App.newDocument("VibeCADCapabilityRouter")
                canonical = root / "capability-router.FCStd"
                document.saveAs(str(canonical))
                App.setActiveDocument(document.Name)
                service = VibeCADService()
                service.project_context()
                service.design_brief()

                creation_route = service.route_modeling_strategy(
                    "Create a precise parametric box",
                    capability_category="part_design",
                )
                self.assertEqual(creation_route["engine"], "native")
                self.assertEqual(
                    creation_route["target_workbench"], "PartDesignWorkbench"
                )
                self.assertEqual(
                    creation_route["request"]["schema"], ROUTING_REQUEST_SCHEMA
                )

                body = _call(
                    service, "partdesign.create_body", label="Routed Native Box"
                )["mutation"]["body"]
                sketch = _call(
                    service,
                    "partdesign.create_sketch",
                    body_name=body,
                    label="Routed Native Profile",
                    support={"type": "origin_plane", "plane": "XY_Plane"},
                )["mutation"]["sketch"]
                _call(service, "partdesign.edit_sketch", sketch_name=sketch)
                _call(
                    service,
                    "sketcher.draw_rectangle",
                    width=30,
                    height=20,
                    center_x=0,
                    center_y=0,
                    construction=False,
                )
                _call(service, "sketcher.close_sketch")
                _call(
                    service,
                    "partdesign.pad",
                    profile_name=sketch,
                    label="Routed Native Pad",
                    extent={"type": "length", "length": 10},
                    side="one_side",
                    reversed=False,
                    taper_angle_degrees=0,
                    second_taper_angle_degrees=0,
                    refine=True,
                )
                document.recompute()
                document.save()
                baseline_structure = _editable_structure(document)
                baseline_sha256 = _sha256(canonical)
                self.assertTrue(baseline_structure["shape_valid"])
                self.assertEqual(baseline_structure["solid_count"], 1)
                self.assertEqual(baseline_structure["body_tip"], "Pad")

                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(document.getObject("Pad"), "Face1")
                edit_route = service.route_modeling_strategy(
                    "Make this selected face longer"
                )
                self.assertEqual(edit_route["engine"], "native")
                self.assertEqual(edit_route["target_workbench"], "PartDesignWorkbench")
                self.assertTrue(edit_route["preserved_existing_structure"])
                self.assertEqual(
                    edit_route["request"]["selection_context"]["selection"][0]["object"],
                    "Pad",
                )
                self.assertEqual(_editable_structure(document), baseline_structure)
                self.assertEqual(_sha256(canonical), baseline_sha256)

                drawing_route = service.route_modeling_strategy(
                    "Create a dimensioned manufacturing drawing"
                )
                self.assertEqual(drawing_route["schema"], ROUTER_SCHEMA)
                self.assertEqual(drawing_route["engine"], "native")
                self.assertEqual(
                    drawing_route["target_workbench"], "TechDrawWorkbench"
                )
                self.assertEqual(
                    drawing_route["reason_code"], "professional_native_capability"
                )
                self.assertEqual(Gui.activeWorkbench().name(), "TechDrawWorkbench")
                self.assertEqual(_editable_structure(document), baseline_structure)
                self.assertEqual(_sha256(canonical), baseline_sha256)

                document.save()
                App.closeDocument(document.Name)
                reopened = App.openDocument(str(canonical))
                App.setActiveDocument(reopened.Name)
                reopened.recompute()
                self.assertEqual(_editable_structure(reopened), baseline_structure)
                reopened_service = VibeCADService()
                persisted = reopened_service.last_capability_route()
                self.assertIsNotNone(persisted)
                self.assertEqual(persisted["route_id"], drawing_route["route_id"])
                self.assertEqual(persisted["target_workbench"], "TechDrawWorkbench")
            finally:
                Gui.Selection.clearSelection()
                for name in set(App.listDocuments()) - prior_documents:
                    App.closeDocument(name)
                if previous_home is None:
                    os.environ.pop("VIBECAD_HOME", None)
                else:
                    os.environ["VIBECAD_HOME"] = previous_home


def suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(
        CapabilityRouterIntegrationTest
    )
