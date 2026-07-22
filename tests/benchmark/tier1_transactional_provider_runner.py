# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise one Tier 1 prompt through provider tools and acceptance in FreeCAD GUI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import uuid

import FreeCAD as App
import FreeCADGui as Gui

from VibeCADBenchmark import (
    failure_diagnostics,
    make_case_attempt,
    normalized_usage,
    unrated_instruction_adherence,
    validation_stage,
)
from VibeCADCore import VibeCADService
from VibeCADProvider import BaseProvider, ProviderResult
from VibeCADSession import run_prompt


EXECUTOR = "deterministic-provider-transactional-baseline"
EVIDENCE_PROVIDER = "deterministic-fixture"
EVIDENCE_MODEL = "deterministic-tier1-provider-v1"


def _constraint_evidence(sketches):
    results = []
    for sketch in sketches:
        if sketch is None:
            continue
        try:
            degrees_of_freedom = int(sketch.DoF)
        except Exception:
            degrees_of_freedom = None
        results.append(
            {
                "sketch": str(getattr(sketch, "Name", "")),
                "degrees_of_freedom": degrees_of_freedom,
                "conflicting_constraints": list(
                    getattr(sketch, "ConflictingConstraints", []) or []
                ),
            }
        )
    passed = bool(results) and all(
        item["degrees_of_freedom"] == 0
        and not item["conflicting_constraints"]
        for item in results
    )
    return passed, {"sketches": results, "fully_constrained": passed}


def _stages(
    *,
    geometry,
    dimensions,
    constraints,
    editability,
    reopen,
    follow_up=None,
    export=None,
):
    return {
        "geometry": validation_stage(
            applicable=True, passed=bool(geometry[0]), evidence=geometry[1]
        ),
        "dimensions": validation_stage(
            applicable=True, passed=bool(dimensions[0]), evidence=dimensions[1]
        ),
        "constraints": validation_stage(
            applicable=True, passed=bool(constraints[0]), evidence=constraints[1]
        ),
        "editability": validation_stage(
            applicable=True, passed=bool(editability[0]), evidence=editability[1]
        ),
        "follow_up": (
            validation_stage(
                applicable=True, passed=bool(follow_up[0]), evidence=follow_up[1]
            )
            if follow_up is not None
            else validation_stage(
                applicable=False, reason="This case starts a new design."
            )
        ),
        "reopen": validation_stage(
            applicable=True, passed=bool(reopen[0]), evidence=reopen[1]
        ),
        "export": (
            validation_stage(
                applicable=True, passed=bool(export[0]), evidence=export[1]
            )
            if export is not None
            else validation_stage(
                applicable=False, reason="This case does not request an export."
            )
        ),
    }


def _case_attempt(case_id, attempt, stages, elapsed_seconds, artifacts):
    return make_case_attempt(
        tier=1,
        case_id=case_id,
        attempt=attempt,
        provider=EVIDENCE_PROVIDER,
        model=EVIDENCE_MODEL,
        executor=EXECUTOR,
        live_model_score=False,
        stages=stages,
        question_count=0,
        unnecessary_question_count=0,
        retry_count=0,
        usage=normalized_usage(),
        instruction_adherence=unrated_instruction_adherence(
            "A deterministic fixture has no human instruction-adherence rating."
        ),
        elapsed_seconds=elapsed_seconds,
        diagnostics=failure_diagnostics(stages),
        artifact_paths=artifacts,
    )


class ExactBoxProvider(BaseProvider):
    model = "deterministic-tier1-provider-v1"

    def run(self, prompt, context, tool_runner=None, cancellation_check=None, progress_callback=None):
        calls = []

        def call(name, arguments):
            result = tool_runner(name, json.dumps(arguments, separators=(",", ":")))
            calls.append({"name": name, "result": result})
            if not result.get("ok"):
                raise RuntimeError(f"{name} failed: {result}")
            return result

        if "export" in prompt.lower():
            selection = context.get("selection") or {}
            selected = selection.get("selection") or []
            if len(selected) != 1:
                raise RuntimeError(f"One explicit selected object is required: {selection}")
            exported = call("project.export", {
                "object_names": [selected[0]["object"]],
                "format": "stl", "file_name": "tier1-printable-model",
            })
            return ProviderResult(
                "Exported the selected model as a non-overwriting STL project artifact.",
                raw={"calls": calls, "export": exported.get("export")},
            )

        if "hollow enclosure" in prompt.lower():
            body = call("partdesign.create_body", {"label": "Hollow Enclosure"})
            body_name = body["mutation"]["body"]
            sketch = call("partdesign.create_sketch", {
                "body_name": body_name, "label": "Enclosure Profile",
                "support": {"type": "origin_plane", "plane": "XY_Plane"},
            })
            sketch_name = sketch["mutation"]["sketch"]
            call("partdesign.edit_sketch", {"sketch_name": sketch_name})
            call("sketcher.draw_rectangle", {
                "width": 60, "height": 40, "center_x": 0, "center_y": 0,
                "construction": False,
            })
            call("sketcher.close_sketch", {})
            pad = call("partdesign.pad", {
                "profile_name": sketch_name, "label": "Enclosure Blank",
                "extent": {"type": "length", "length": 25}, "side": "one_side",
                "reversed": False, "taper_angle_degrees": 0,
                "second_taper_angle_degrees": 0, "refine": True,
            })
            pad_name = pad["mutation"]["feature"]
            call("partdesign.thickness", {
                "base_feature_name": pad_name, "label": "Open Top Shell",
                "selection": {
                    "type": "query", "element_type": "face", "expected_count": 1,
                    "geometry_type": "plane", "normal": {"x": 0, "y": 0, "z": 1},
                    "normal_tolerance_degrees": 1,
                    "near_point": {"x": 0, "y": 0, "z": 25}, "max_distance": 1,
                },
                "wall_thickness": 2, "direction": "inward", "mode": "skin",
                "join": "intersection", "intersection_handling": False,
                "refine": True, "support_transform": False,
            })
            call("core.update_design_brief", {
                "base_revision": context["design_brief"]["revision"],
                "changes": {
                    "purpose": "An open-top editable enclosure.", "units": "mm",
                    "critical_dimensions": [
                        {"name": "width", "value": 60, "unit": "mm"},
                        {"name": "depth", "value": 40, "unit": "mm"},
                        {"name": "height", "value": 25, "unit": "mm"},
                        {"name": "wall_thickness", "value": 2, "unit": "mm"},
                    ],
                },
            })
            return ProviderResult("Created an editable open-top enclosure with 2 mm walls.", raw={"calls": calls})

        if "mirror" in prompt.lower() and "hole" in prompt.lower():
            body = call("partdesign.create_body", {"label": "Mirrored Hole Plate"})
            body_name = body["mutation"]["body"]
            base = call("partdesign.create_sketch", {
                "body_name": body_name, "label": "Plate Profile",
                "support": {"type": "origin_plane", "plane": "XY_Plane"},
            })
            base_name = base["mutation"]["sketch"]
            call("partdesign.edit_sketch", {"sketch_name": base_name})
            call("sketcher.draw_rectangle", {
                "width": 40, "height": 30, "center_x": 0, "center_y": 0,
                "construction": False,
            })
            call("sketcher.close_sketch", {})
            pad = call("partdesign.pad", {
                "profile_name": base_name, "label": "Plate",
                "extent": {"type": "length", "length": 5}, "side": "one_side",
                "reversed": False, "taper_angle_degrees": 0,
                "second_taper_angle_degrees": 0, "refine": True,
            })
            pad_name = pad["mutation"]["feature"]
            hole_sketch = call("partdesign.create_sketch", {
                "body_name": body_name, "label": "Source Hole",
                "support": {
                    "type": "planar_face", "object_name": pad_name,
                    "selection": {
                        "type": "query", "normal": {"x": 0, "y": 0, "z": 1},
                        "near_point": {"x": -10, "y": 0, "z": 5},
                        "normal_tolerance_degrees": 1, "max_distance": 1,
                    },
                },
            })
            hole_sketch_name = hole_sketch["mutation"]["sketch"]
            call("partdesign.edit_sketch", {"sketch_name": hole_sketch_name})
            call("sketcher.add_circle", {"center": [-10, 0], "radius": 2.5, "construction": False})
            call("sketcher.constrain", {
                "constraints": [
                    {
                        "type": "Lock",
                        "point": {"geometry": 0, "point": "center"},
                        "position_mm": [-10, 0],
                    },
                    {"type": "Diameter", "geometry": 0, "size_mm": 5},
                ],
            })
            call("sketcher.close_sketch", {})
            pocket = call("partdesign.pocket", {
                "profile_name": hole_sketch_name, "label": "Source Through Hole",
                "extent": {"type": "through_all"}, "side": "one_side",
                "reversed": False, "taper_angle_degrees": 0,
                "second_taper_angle_degrees": 0, "refine": True,
            })
            pocket_name = pocket["mutation"]["feature"]
            call("partdesign.mirror", {
                "feature_names": [pocket_name], "label": "Mirrored Through Hole",
                "plane": {"source": "body_origin", "plane": "YZ_Plane"},
                "transform_mode": "features", "refine": True,
            })
            call("core.update_design_brief", {
                "base_revision": context["design_brief"]["revision"],
                "changes": {
                    "purpose": "A plate with a symmetric pair of through-holes.",
                    "symmetry": ["Mirror the 5 mm source hole across the YZ center plane."],
                    "critical_dimensions": [
                        {"name": "plate_width", "value": 40, "unit": "mm"},
                        {"name": "plate_depth", "value": 30, "unit": "mm"},
                        {"name": "plate_thickness", "value": 5, "unit": "mm"},
                        {"name": "hole_diameter", "value": 5, "unit": "mm"},
                    ],
                },
            })
            return ProviderResult("Created a symmetric pair of editable 5 mm through-holes.", raw={"calls": calls})

        if "change" in prompt.lower() and "width" in prompt.lower():
            selection = context.get("selection") or {}
            selected = selection.get("selection") or []
            if len(selected) != 1 or selected[0].get("type") != "Sketcher::SketchObject":
                raise RuntimeError(f"One explicit selected sketch is required: {selection}")
            call("partdesign.edit_sketch", {"sketch_name": selected[0]["object"]})
            call("sketcher.edit_constraint", {
                "action": {
                    "operation": "set_value", "target": {"by": "index", "index": 8},
                    "value": -27.5,
                },
            })
            call("sketcher.edit_constraint", {
                "action": {
                    "operation": "set_value", "target": {"by": "index", "index": 10},
                    "value": 55,
                },
            })
            call("sketcher.close_sketch", {})
            call("core.update_design_brief", {
                "base_revision": context["design_brief"]["revision"],
                "changes": {
                    "critical_dimensions": [
                        {"name": "width", "value": 55, "unit": "mm"},
                        {"name": "depth", "value": 30, "unit": "mm"},
                        {"name": "height", "value": 20, "unit": "mm"},
                        {"name": "hole_diameter", "value": 6, "unit": "mm"},
                    ],
                },
            })
            return ProviderResult("Changed the selected sketch width to 55 mm and preserved its center.", raw={"calls": calls})

        if "round" in prompt.lower():
            selection = context.get("selection") or {}
            selected = selection.get("selection") or []
            if len(selected) != 1:
                raise RuntimeError(f"One explicit selected feature is required: {selection}")
            call("partdesign.fillet", {
                "base_feature_name": selected[0]["object"],
                "label": "Rounded Outer Edges",
                "selection": {"type": "all_edges"},
                "definition": {"radius": 2},
                "refine": True,
                "support_transform": False,
            })
            call("core.update_design_brief", {
                "base_revision": context["design_brief"]["revision"],
                "changes": {
                    "surface_requirements": ["Round the selected feature edges by 2 mm."],
                },
            })
            return ProviderResult("Rounded the selected feature edges by 2 mm.", raw={"calls": calls})

        if "hole" in prompt.lower():
            selection = context.get("selection") or {}
            selected = selection.get("selection") or []
            if len(selected) != 1 or len(selected[0].get("subelements") or []) != 1:
                raise RuntimeError(f"One explicit selected face is required: {selection}")
            item = selected[0]
            face_name = item["subelements"][0]
            if not face_name.startswith("Face"):
                raise RuntimeError(f"The explicit selection is not a face: {item}")
            body_name = context["document"]["edit_object"]["name"] if context["document"].get("edit_object") else "Body"
            sketch = call("partdesign.create_sketch", {
                "body_name": body_name, "label": "Selected Face Hole",
                "support": {
                    "type": "planar_face", "object_name": item["object"],
                    "selection": {"type": "exact", "subelement": face_name},
                },
            })
            sketch_name = sketch["mutation"]["sketch"]
            call("partdesign.edit_sketch", {"sketch_name": sketch_name})
            call("sketcher.add_circle", {"center": [0, 0], "radius": 3, "construction": False})
            call("sketcher.constrain", {
                "constraints": [
                    {
                        "type": "Lock",
                        "point": {"geometry": 0, "point": "center"},
                        "position_mm": [0, 0],
                    },
                    {"type": "Diameter", "geometry": 0, "size_mm": 6},
                ],
            })
            call("sketcher.close_sketch", {})
            call("partdesign.pocket", {
                "profile_name": sketch_name, "label": "Centered Through Hole",
                "extent": {"type": "through_all"}, "side": "one_side",
                "reversed": False, "taper_angle_degrees": 0,
                "second_taper_angle_degrees": 0, "refine": True,
            })
            call("core.update_design_brief", {
                "base_revision": context["design_brief"]["revision"],
                "changes": {
                    "critical_dimensions": [
                        {"name": "width", "value": 40, "unit": "mm"},
                        {"name": "depth", "value": 30, "unit": "mm"},
                        {"name": "height", "value": 20, "unit": "mm"},
                        {"name": "hole_diameter", "value": 6, "unit": "mm"},
                    ],
                },
            })
            return ProviderResult("Added a centered 6 mm through-hole to the selected face.", raw={"calls": calls})

        body = call("partdesign.create_body", {"label": "Exact Box"})
        body_name = body["mutation"]["body"]
        sketch = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Box Profile",
            "support": {"type": "origin_plane", "plane": "XY_Plane"},
        })
        sketch_name = sketch["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": sketch_name})
        call("sketcher.draw_rectangle", {
            "width": 40, "height": 30, "center_x": 0, "center_y": 0,
            "construction": False,
        })
        call("sketcher.close_sketch", {})
        call("partdesign.pad", {
            "profile_name": sketch_name, "label": "Box",
            "extent": {"type": "length", "length": 20}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        call("core.update_design_brief", {
            "base_revision": context["design_brief"]["revision"],
            "changes": {
                "purpose": "An exact editable rectangular box.", "units": "mm",
                "critical_dimensions": [
                    {"name": "width", "value": 40, "unit": "mm"},
                    {"name": "depth", "value": 30, "unit": "mm"},
                    {"name": "height", "value": 20, "unit": "mm"},
                ],
            },
        })
        return ProviderResult("Created an editable 40 by 30 by 20 mm box.", raw={"calls": calls})


def main():
    output = Path(sys.argv[-1])
    output.mkdir(parents=True, exist_ok=True)
    attempt = int(os.environ.get("VIBECAD_BENCHMARK_ATTEMPT", "1"))
    Gui.activateWorkbench("PartDesignWorkbench")
    trial_id = uuid.uuid4().hex[:12]
    doc = App.newDocument(f"Tier1TransactionalBox{trial_id}")
    document_path = output / f"tier1-provider-box-{trial_id}.FCStd"
    doc.saveAs(str(document_path))
    App.setActiveDocument(doc.Name)
    started = time.monotonic()
    box_started = time.monotonic()
    service = VibeCADService()
    response = run_prompt(
        "Create a box that is 40 mm wide, 30 mm deep, and 20 mm tall.",
        service=service, prefer_online=False, provider=ExactBoxProvider(),
    )
    doc.recompute()
    boxes = [obj for obj in doc.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pad"]
    base_sketch = next(
        (
            obj for obj in doc.Objects
            if getattr(obj, "TypeId", "") == "Sketcher::SketchObject"
            and getattr(obj, "Label", "") == "Box Profile"
        ),
        None,
    )
    box_geometry_passed = not response.error and len(boxes) == 1
    box_dimensions_passed = False
    if box_geometry_passed:
        shape = boxes[0].Shape
        box_geometry_passed = shape.isValid() and not shape.isNull()
        box_dimensions_passed = (
            abs(shape.BoundBox.XLength - 40) < 1e-6 and
            abs(shape.BoundBox.YLength - 30) < 1e-6 and
            abs(shape.BoundBox.ZLength - 20) < 1e-6
        )
    passed = box_geometry_passed and box_dimensions_passed
    box_elapsed = time.monotonic() - box_started
    top_face_index = max(
        range(len(boxes[0].Shape.Faces)),
        key=lambda index: boxes[0].Shape.Faces[index].CenterOfMass.z,
    )
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, boxes[0].Name, f"Face{top_face_index + 1}")
    hole_started = time.monotonic()
    follow_up = run_prompt(
        "Put a centered 6 mm hole through this face.",
        service=service, prefer_online=False, provider=ExactBoxProvider(),
    )
    doc.recompute()
    pockets = [obj for obj in doc.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"]
    hole_geometry_passed = not follow_up.error and len(pockets) == 1
    hole_dimensions_passed = False
    expected_volume = 40 * 30 * 20 - 3.141592653589793 * 3 * 3 * 20
    if hole_geometry_passed:
        shape = pockets[0].Shape
        hole_geometry_passed = shape.isValid() and not shape.isNull()
        hole_dimensions_passed = abs(shape.Volume - expected_volume) < 1e-3
    follow_up_passed = hole_geometry_passed and hole_dimensions_passed
    hole_elapsed = time.monotonic() - hole_started
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, pockets[0].Name)
    round_started = time.monotonic()
    round_response = run_prompt(
        "Round all outer edges of this feature by 2 mm.",
        service=service, prefer_online=False, provider=ExactBoxProvider(),
    )
    doc.recompute()
    fillets = [obj for obj in doc.Objects if getattr(obj, "TypeId", "") == "PartDesign::Fillet"]
    round_geometry_passed = not round_response.error and len(fillets) == 1
    round_dimensions_passed = False
    round_radius = None
    if round_geometry_passed:
        rounded = fillets[0].Shape
        round_geometry_passed = rounded.isValid() and not rounded.isNull()
        try:
            round_radius = float(fillets[0].Radius)
        except Exception:
            round_radius = None
        round_dimensions_passed = (
            round_radius is not None and abs(round_radius - 2) < 1e-9
        )
    round_passed = round_geometry_passed and round_dimensions_passed
    round_elapsed = time.monotonic() - round_started
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, base_sketch.Name)
    dimension_started = time.monotonic()
    dimension_response = run_prompt(
        "Change this box width from 40 mm to 55 mm and keep it centered.",
        service=service, prefer_online=False, provider=ExactBoxProvider(),
    )
    doc.recompute()
    dimension_geometry_passed = (
        not dimension_response.error and len(fillets) == 1
    )
    dimension_value_passed = False
    if dimension_geometry_passed:
        changed_shape = fillets[0].Shape
        dimension_geometry_passed = (
            changed_shape.isValid() and not changed_shape.isNull()
        )
        dimension_value_passed = (
            abs(changed_shape.BoundBox.XLength - 55) < 1e-6 and
            abs(changed_shape.BoundBox.Center.x) < 1e-6
        )
    dimension_passed = dimension_geometry_passed and dimension_value_passed
    dimension_elapsed = time.monotonic() - dimension_started
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, fillets[0].Name)
    head_before_export = service.revision_timeline()
    export_started = time.monotonic()
    export_response = run_prompt(
        "Export this selected model as STL for 3D printing.",
        service=service, prefer_online=False, provider=ExactBoxProvider(),
    )
    export_trace = next(
        (item for item in export_response.tool_trace if item.get("tool_name") == "project.export"),
        {},
    )
    export_payload = export_trace.get("result") or {}
    export_path = Path(str((export_payload.get("export") or {}).get("path") or ""))
    export_passed = (
        not export_response.error and export_payload.get("ok") is True and
        export_path.is_file() and export_path.stat().st_size > 100
    )
    export_elapsed = time.monotonic() - export_started
    box_constraint_passed, box_constraint_evidence = _constraint_evidence(
        [base_sketch]
    )
    hole_sketch = next(
        (
            obj
            for obj in doc.Objects
            if getattr(obj, "TypeId", "") == "Sketcher::SketchObject"
            and getattr(obj, "Label", "") == "Selected Face Hole"
        ),
        None,
    )
    hole_constraint_passed, hole_constraint_evidence = _constraint_evidence(
        [base_sketch, hole_sketch]
    )
    base_sketch_name = str(getattr(base_sketch, "Name", ""))
    box_types = [str(getattr(obj, "TypeId", "")) for obj in doc.Objects]
    doc.save()
    head = service.revision_timeline()
    App.closeDocument(doc.Name)
    reopened = App.openDocument(str(document_path))
    reopened.recompute()
    reopened_feature = next((obj for obj in reopened.Objects if getattr(obj, "TypeId", "") == "PartDesign::Fillet"), None)
    reopened_types = [str(getattr(obj, "TypeId", "")) for obj in reopened.Objects]
    reopen_passed = bool(
        reopened_feature
        and reopened_feature.Shape.isValid()
        and not reopened_feature.Shape.isNull()
        and abs(reopened_feature.Shape.BoundBox.XLength - 55) < 1e-6
    )
    App.closeDocument(reopened.Name)
    enclosure_doc = App.newDocument(f"Tier1TransactionalEnclosure{trial_id}")
    enclosure_path = output / f"tier1-provider-enclosure-{trial_id}.FCStd"
    enclosure_doc.saveAs(str(enclosure_path))
    enclosure_service = VibeCADService()
    enclosure_started = time.monotonic()
    enclosure_response = run_prompt(
        "Create a 60 by 40 by 25 mm hollow enclosure with 2 mm walls and an open top.",
        service=enclosure_service, prefer_online=False, provider=ExactBoxProvider(),
    )
    enclosure_doc.recompute()
    shells = [obj for obj in enclosure_doc.Objects if getattr(obj, "TypeId", "") == "PartDesign::Thickness"]
    enclosure_geometry_passed = not enclosure_response.error and len(shells) == 1
    enclosure_dimensions_passed = False
    if enclosure_geometry_passed:
        shell = shells[0].Shape
        enclosure_geometry_passed = shell.isValid() and not shell.isNull()
        enclosure_dimensions_passed = (
            abs(shell.BoundBox.XLength - 60) < 1e-6 and
            abs(shell.BoundBox.YLength - 40) < 1e-6 and
            abs(shell.BoundBox.ZLength - 25) < 1e-6 and
            abs(shell.Volume - 13632) < 1e-3
        )
    enclosure_passed = (
        enclosure_geometry_passed and enclosure_dimensions_passed
    )
    enclosure_elapsed = time.monotonic() - enclosure_started
    enclosure_constraint_passed, enclosure_constraint_evidence = (
        _constraint_evidence(
            [
                obj
                for obj in enclosure_doc.Objects
                if getattr(obj, "TypeId", "") == "Sketcher::SketchObject"
            ]
        )
    )
    enclosure_types = [
        str(getattr(obj, "TypeId", "")) for obj in enclosure_doc.Objects
    ]
    enclosure_doc.save()
    enclosure_head = enclosure_service.revision_timeline()
    App.closeDocument(enclosure_doc.Name)
    reopened_enclosure = App.openDocument(str(enclosure_path))
    reopened_enclosure.recompute()
    reopened_shell = next((obj for obj in reopened_enclosure.Objects if getattr(obj, "TypeId", "") == "PartDesign::Thickness"), None)
    enclosure_reopen_passed = bool(reopened_shell and reopened_shell.Shape.isValid() and not reopened_shell.Shape.isNull())
    reopened_enclosure_types = [
        str(getattr(obj, "TypeId", "")) for obj in reopened_enclosure.Objects
    ]
    App.closeDocument(reopened_enclosure.Name)
    mirror_doc = App.newDocument(f"Tier1TransactionalMirror{trial_id}")
    mirror_path = output / f"tier1-provider-mirror-{trial_id}.FCStd"
    mirror_doc.saveAs(str(mirror_path))
    mirror_service = VibeCADService()
    mirror_started = time.monotonic()
    mirror_response = run_prompt(
        "Create a 40 by 30 by 5 mm plate, add a 5 mm hole 10 mm left of center, and mirror this hole across the center.",
        service=mirror_service, prefer_online=False, provider=ExactBoxProvider(),
    )
    mirror_doc.recompute()
    mirrors = [obj for obj in mirror_doc.Objects if getattr(obj, "TypeId", "") == "PartDesign::Mirrored"]
    mirror_geometry_passed = not mirror_response.error and len(mirrors) == 1
    mirror_dimensions_passed = False
    expected_mirror_volume = (
        40 * 30 * 5 - 2 * 3.141592653589793 * 2.5 * 2.5 * 5
    )
    if mirror_geometry_passed:
        mirrored_shape = mirrors[0].Shape
        mirror_geometry_passed = (
            mirrored_shape.isValid() and not mirrored_shape.isNull()
        )
        mirror_dimensions_passed = (
            abs(mirrored_shape.Volume - expected_mirror_volume) < 1e-3
        )
    mirror_passed = mirror_geometry_passed and mirror_dimensions_passed
    mirror_elapsed = time.monotonic() - mirror_started
    mirror_constraint_passed, mirror_constraint_evidence = _constraint_evidence(
        [
            obj
            for obj in mirror_doc.Objects
            if getattr(obj, "TypeId", "") == "Sketcher::SketchObject"
        ]
    )
    mirror_types = [
        str(getattr(obj, "TypeId", "")) for obj in mirror_doc.Objects
    ]
    mirror_doc.save()
    mirror_head = mirror_service.revision_timeline()
    App.closeDocument(mirror_doc.Name)
    reopened_mirror = App.openDocument(str(mirror_path))
    reopened_mirror.recompute()
    reopened_mirrored = next((obj for obj in reopened_mirror.Objects if getattr(obj, "TypeId", "") == "PartDesign::Mirrored"), None)
    mirror_reopen_passed = bool(reopened_mirrored and reopened_mirrored.Shape.isValid() and not reopened_mirrored.Shape.isNull())
    reopened_mirror_types = [
        str(getattr(obj, "TypeId", "")) for obj in reopened_mirror.Objects
    ]
    App.closeDocument(reopened_mirror.Name)
    no_export_revision = len(head_before_export) == len(head)
    case_attempts = []

    box_stages = _stages(
        geometry=(
            box_geometry_passed,
            {"shape_valid": box_geometry_passed, "feature": "PartDesign::Pad"},
        ),
        dimensions=(
            box_dimensions_passed,
            {"expected_mm": [40, 30, 20], "tolerance_mm": 1e-6},
        ),
        constraints=(box_constraint_passed, box_constraint_evidence),
        editability=(
            "Sketcher::SketchObject" in box_types
            and "PartDesign::Pad" in box_types
            and len(head) >= 1,
            {
                "required_types": ["Sketcher::SketchObject", "PartDesign::Pad"],
                "revision_count_after_workflow": len(head),
            },
        ),
        reopen=(
            reopen_passed and "PartDesign::Pad" in reopened_types,
            {"reopened_types": reopened_types, "final_width_mm": 55},
        ),
    )
    case_attempts.append(
        _case_attempt(
            "t1_exact_box",
            attempt,
            box_stages,
            box_elapsed,
            [str(document_path)],
        )
    )

    hole_stages = _stages(
        geometry=(
            hole_geometry_passed,
            {"shape_valid": hole_geometry_passed, "feature": "PartDesign::Pocket"},
        ),
        dimensions=(
            hole_dimensions_passed,
            {
                "hole_diameter_mm": 6,
                "expected_volume_mm3": expected_volume,
                "tolerance_mm3": 1e-3,
            },
        ),
        constraints=(hole_constraint_passed, hole_constraint_evidence),
        editability=(
            "PartDesign::Pocket" in box_types
            and "Sketcher::SketchObject" in box_types,
            {
                "required_types": [
                    "Sketcher::SketchObject",
                    "PartDesign::Pocket",
                ],
                "selection_kind": "exact_face",
            },
        ),
        follow_up=(
            follow_up_passed,
            {"selection": "one exact planar face", "through_all": True},
        ),
        reopen=(
            reopen_passed and "PartDesign::Pocket" in reopened_types,
            {"reopened_types": reopened_types},
        ),
    )
    case_attempts.append(
        _case_attempt(
            "t1_centered_hole",
            attempt,
            hole_stages,
            hole_elapsed,
            [str(document_path)],
        )
    )

    round_stages = _stages(
        geometry=(
            round_geometry_passed,
            {"shape_valid": round_geometry_passed, "feature": "PartDesign::Fillet"},
        ),
        dimensions=(
            round_dimensions_passed,
            {"expected_radius_mm": 2, "observed_radius_mm": round_radius},
        ),
        constraints=(hole_constraint_passed, hole_constraint_evidence),
        editability=(
            "PartDesign::Fillet" in box_types,
            {"required_type": "PartDesign::Fillet", "selection_kind": "feature"},
        ),
        follow_up=(
            round_passed,
            {"selection": "one exact feature", "edge_scope": "all_edges"},
        ),
        reopen=(
            reopen_passed and "PartDesign::Fillet" in reopened_types,
            {"reopened_types": reopened_types},
        ),
    )
    case_attempts.append(
        _case_attempt(
            "t1_round_edges",
            attempt,
            round_stages,
            round_elapsed,
            [str(document_path)],
        )
    )

    dimension_stages = _stages(
        geometry=(
            dimension_geometry_passed,
            {
                "shape_valid": dimension_geometry_passed,
                "downstream_feature": "PartDesign::Fillet",
            },
        ),
        dimensions=(
            dimension_value_passed,
            {
                "expected_width_mm": 55,
                "expected_center_x_mm": 0,
                "tolerance_mm": 1e-6,
            },
        ),
        constraints=(hole_constraint_passed, hole_constraint_evidence),
        editability=(
            bool(base_sketch_name) and "PartDesign::Fillet" in box_types,
            {
                "edited_object": base_sketch_name,
                "downstream_type": "PartDesign::Fillet",
            },
        ),
        follow_up=(
            dimension_passed,
            {"selection": "one exact sketch", "parameter_changed": "width"},
        ),
        reopen=(
            reopen_passed,
            {"expected_width_mm": 55, "reopened_types": reopened_types},
        ),
    )
    case_attempts.append(
        _case_attempt(
            "t1_change_dimension",
            attempt,
            dimension_stages,
            dimension_elapsed,
            [str(document_path)],
        )
    )

    export_stages = _stages(
        geometry=(
            dimension_geometry_passed,
            {"source_shape_valid": dimension_geometry_passed},
        ),
        dimensions=(
            dimension_value_passed,
            {"source_width_mm": 55, "source_center_x_mm": 0},
        ),
        constraints=(
            hole_constraint_passed,
            hole_constraint_evidence,
        ),
        editability=(
            no_export_revision and "PartDesign::Fillet" in box_types,
            {
                "source_type": "PartDesign::Fillet",
                "revision_count_before": len(head_before_export),
                "revision_count_after": len(head),
            },
        ),
        follow_up=(
            export_passed,
            {"selection": "one exact object", "format": "stl"},
        ),
        reopen=(
            reopen_passed,
            {"source_reopened": reopen_passed, "reopened_types": reopened_types},
        ),
        export=(
            export_passed,
            {
                "format": "stl",
                "path": str(export_path),
                "size_bytes": export_path.stat().st_size
                if export_path.is_file()
                else 0,
                "non_overwriting_project_artifact": True,
            },
        ),
    )
    case_attempts.append(
        _case_attempt(
            "t1_export_stl",
            attempt,
            export_stages,
            export_elapsed,
            [str(document_path), str(export_path)],
        )
    )

    enclosure_stages = _stages(
        geometry=(
            enclosure_geometry_passed,
            {
                "shape_valid": enclosure_geometry_passed,
                "feature": "PartDesign::Thickness",
            },
        ),
        dimensions=(
            enclosure_dimensions_passed,
            {
                "expected_bounds_mm": [60, 40, 25],
                "wall_thickness_mm": 2,
                "expected_volume_mm3": 13632,
            },
        ),
        constraints=(
            enclosure_constraint_passed,
            enclosure_constraint_evidence,
        ),
        editability=(
            "Sketcher::SketchObject" in enclosure_types
            and "PartDesign::Pad" in enclosure_types
            and "PartDesign::Thickness" in enclosure_types
            and len(enclosure_head) == 1,
            {
                "required_types": [
                    "Sketcher::SketchObject",
                    "PartDesign::Pad",
                    "PartDesign::Thickness",
                ],
                "revision_count": len(enclosure_head),
            },
        ),
        reopen=(
            enclosure_reopen_passed
            and "PartDesign::Thickness" in reopened_enclosure_types,
            {"reopened_types": reopened_enclosure_types},
        ),
    )
    case_attempts.append(
        _case_attempt(
            "t1_hollow_enclosure",
            attempt,
            enclosure_stages,
            enclosure_elapsed,
            [str(enclosure_path)],
        )
    )

    mirror_stages = _stages(
        geometry=(
            mirror_geometry_passed,
            {
                "shape_valid": mirror_geometry_passed,
                "feature": "PartDesign::Mirrored",
            },
        ),
        dimensions=(
            mirror_dimensions_passed,
            {
                "plate_mm": [40, 30, 5],
                "hole_diameter_mm": 5,
                "source_offset_mm": -10,
                "expected_volume_mm3": expected_mirror_volume,
            },
        ),
        constraints=(mirror_constraint_passed, mirror_constraint_evidence),
        editability=(
            "PartDesign::Pocket" in mirror_types
            and "PartDesign::Mirrored" in mirror_types
            and len(mirror_head) == 1,
            {
                "required_types": [
                    "PartDesign::Pocket",
                    "PartDesign::Mirrored",
                ],
                "revision_count": len(mirror_head),
            },
        ),
        reopen=(
            mirror_reopen_passed
            and "PartDesign::Mirrored" in reopened_mirror_types,
            {"reopened_types": reopened_mirror_types},
        ),
    )
    case_attempts.append(
        _case_attempt(
            "t1_mirror_feature",
            attempt,
            mirror_stages,
            mirror_elapsed,
            [str(mirror_path)],
        )
    )
    report = {
        "schema": "vibecad-provider-benchmark-result-v1", "version": 1,
        "executor": EXECUTOR,
        "case_attempts": case_attempts,
        "prompt": "Create a box that is 40 mm wide, 30 mm deep, and 20 mm tall.",
        "passed": all(item["passed"] for item in case_attempts),
        "geometry_passed": bool(passed), "reopen_passed": reopen_passed,
        "selection_follow_up_passed": bool(follow_up_passed),
        "round_edges_passed": bool(round_passed),
        "dimension_change_passed": bool(dimension_passed),
        "export_stl_passed": bool(export_passed),
        "hollow_enclosure_passed": bool(enclosure_passed),
        "hollow_enclosure_reopen_passed": bool(enclosure_reopen_passed),
        "mirror_feature_passed": bool(mirror_passed),
        "mirror_feature_reopen_passed": bool(mirror_reopen_passed),
        "revision_count": len(head) + len(enclosure_head) + len(mirror_head),
        "tool_count": (
            len(response.tool_trace) + len(follow_up.tool_trace) +
            len(round_response.tool_trace) + len(dimension_response.tool_trace) +
            len(export_response.tool_trace) + len(enclosure_response.tool_trace) +
            len(mirror_response.tool_trace)
        ),
        "artifact": str(document_path),
        "artifacts": [str(document_path), str(enclosure_path), str(mirror_path)],
        "error": response.error, "elapsed_seconds": time.monotonic() - started,
    }
    (output / "tier1-provider-results.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
