# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tier 2 functional wall-bracket workflow through provider tools."""

from __future__ import annotations

import json
import math
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
EVIDENCE_MODEL = "deterministic-tier2-provider-suite-v1"


def _constraint_evidence(document):
    sketches = [
        obj
        for obj in document.Objects
        if getattr(obj, "TypeId", "") == "Sketcher::SketchObject"
    ]
    results = []
    for sketch in sketches:
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


def _cylindrical_radii(shape):
    radii = []
    for face in list(getattr(shape, "Faces", []) or []):
        surface = getattr(face, "Surface", None)
        radius = getattr(surface, "Radius", None)
        if radius is not None:
            radii.append(round(float(radius), 6))
    return sorted(radii)


def _case_stages(
    *,
    geometry_passed,
    geometry_evidence,
    dimensions_passed,
    dimensions_evidence,
    constraints,
    editability_passed,
    editability_evidence,
    reopen_passed,
    reopen_evidence,
):
    return {
        "geometry": validation_stage(
            applicable=True,
            passed=bool(geometry_passed),
            evidence=geometry_evidence,
        ),
        "dimensions": validation_stage(
            applicable=True,
            passed=bool(dimensions_passed),
            evidence=dimensions_evidence,
        ),
        "constraints": validation_stage(
            applicable=True,
            passed=bool(constraints[0]),
            evidence=constraints[1],
        ),
        "editability": validation_stage(
            applicable=True,
            passed=bool(editability_passed),
            evidence=editability_evidence,
        ),
        "follow_up": validation_stage(
            applicable=False,
            reason="This functional-part case is one creation request.",
        ),
        "reopen": validation_stage(
            applicable=True,
            passed=bool(reopen_passed),
            evidence=reopen_evidence,
        ),
        "export": validation_stage(
            applicable=False,
            reason="This functional-part case does not request an export.",
        ),
    }


def _case_attempt(case_id, attempt, stages, elapsed_seconds, artifact):
    return make_case_attempt(
        tier=2,
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
        artifact_paths=[str(artifact)],
    )


class WallBracketProvider(BaseProvider):
    model = "deterministic-tier2-wall-bracket-v1"

    def run(self, prompt, context, tool_runner=None, cancellation_check=None, progress_callback=None):
        calls = []

        def call(name, arguments):
            result = tool_runner(name, json.dumps(arguments, separators=(",", ":")))
            calls.append({"name": name, "result": result})
            if not result.get("ok"):
                raise RuntimeError(f"{name} failed: {result}")
            return result

        body = call("partdesign.create_body", {"label": "Wall Bracket"})
        body_name = body["mutation"]["body"]
        profile = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "L Profile",
            "support": {"type": "origin_plane", "plane": "XY_Plane"},
        })
        profile_name = profile["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": profile_name})
        call("sketcher.add_polyline", {
            "points": [[0, 0], [60, 0], [60, 8], [8, 8], [8, 40], [0, 40]],
            "closed": True, "lock_points": True, "construction": False,
        })
        call("sketcher.close_sketch", {})
        pad = call("partdesign.pad", {
            "profile_name": profile_name, "label": "Bracket Blank",
            "extent": {"type": "length", "length": 30}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        pad_name = pad["mutation"]["feature"]
        holes = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Mounting Holes",
            "support": {
                "type": "planar_face", "object_name": pad_name,
                "selection": {
                    "type": "query", "normal": {"x": 0, "y": 0, "z": 1},
                    "near_point": {"x": 4, "y": 20, "z": 30},
                    "normal_tolerance_degrees": 1, "max_distance": 1,
                },
            },
        })
        holes_name = holes["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": holes_name})
        call("sketcher.add_circle", {"center": [4, 18], "radius": 2, "construction": False})
        call("sketcher.add_circle", {"center": [4, 32], "radius": 2, "construction": False})
        call("sketcher.constrain", {
            "constraints": [
                {
                    "type": "Lock",
                    "point": {"geometry": 0, "point": "center"},
                    "position_mm": [4, 18],
                },
                {"type": "Diameter", "geometry": 0, "size_mm": 4},
                {
                    "type": "Lock",
                    "point": {"geometry": 1, "point": "center"},
                    "position_mm": [4, 32],
                },
                {"type": "Diameter", "geometry": 1, "size_mm": 4},
            ],
        })
        call("sketcher.close_sketch", {})
        call("partdesign.pocket", {
            "profile_name": holes_name, "label": "Through Mounting Holes",
            "extent": {"type": "through_all"}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        call("core.update_design_brief", {
            "base_revision": context["design_brief"]["revision"],
            "changes": {
                "purpose": "A printable right-angle wall bracket with two mounting holes.",
                "units": "mm", "manufacturing_process": "FDM printing",
                "critical_dimensions": [
                    {"name": "horizontal_leg", "value": 60, "unit": "mm"},
                    {"name": "vertical_leg", "value": 40, "unit": "mm"},
                    {"name": "leg_thickness", "value": 8, "unit": "mm"},
                    {"name": "bracket_width", "value": 30, "unit": "mm"},
                    {"name": "mounting_hole_diameter", "value": 4, "unit": "mm"},
                ],
                "validation_requirements": [
                    "The L profile is closed and editable.",
                    "Both mounting holes pass through the complete bracket width.",
                ],
            },
        })
        return ProviderResult("Created an editable wall bracket with two through mounting holes.", raw={"calls": calls})


class MotorAdapterProvider(BaseProvider):
    model = "deterministic-tier2-motor-adapter-v1"

    def run(self, prompt, context, tool_runner=None, cancellation_check=None, progress_callback=None):
        calls = []

        def call(name, arguments):
            result = tool_runner(name, json.dumps(arguments, separators=(",", ":")))
            calls.append({"name": name, "result": result})
            if not result.get("ok"):
                raise RuntimeError(f"{name} failed: {result}")
            return result

        body = call("partdesign.create_body", {"label": "Motor Adapter"})
        body_name = body["mutation"]["body"]
        profile = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Adapter Disc",
            "support": {"type": "origin_plane", "plane": "XY_Plane"},
        })
        profile_name = profile["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": profile_name})
        call("sketcher.add_circle", {"center": [0, 0], "radius": 40, "construction": False})
        call("sketcher.constrain", {
            "constraints": [
                {
                    "type": "Lock",
                    "point": {"geometry": 0, "point": "center"},
                    "position_mm": [0, 0],
                },
                {"type": "Diameter", "geometry": 0, "size_mm": 80},
            ],
        })
        call("sketcher.close_sketch", {})
        pad = call("partdesign.pad", {
            "profile_name": profile_name, "label": "Adapter Blank",
            "extent": {"type": "length", "length": 8}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        pad_name = pad["mutation"]["feature"]
        holes = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Motor Interface Holes",
            "support": {
                "type": "planar_face", "object_name": pad_name,
                "selection": {
                    "type": "query", "normal": {"x": 0, "y": 0, "z": 1},
                    "near_point": {"x": 0, "y": 0, "z": 8},
                    "normal_tolerance_degrees": 1, "max_distance": 1,
                },
            },
        })
        holes_name = holes["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": holes_name})
        call("sketcher.add_circle", {"center": [0, 0], "radius": 10, "construction": False})
        call("sketcher.constrain", {
            "constraints": [
                {
                    "type": "Lock",
                    "point": {"geometry": 0, "point": "center"},
                    "position_mm": [0, 0],
                },
                {"type": "Diameter", "geometry": 0, "size_mm": 20},
            ],
        })
        call("sketcher.add_hole_pattern", {
            "layout": {
                "type": "circular", "center_mm": [0, 0], "count": 4,
                "pitch_circle_diameter_mm": 60, "start_angle_degrees": 45,
            },
            "hole_diameter": 5, "name_prefix": "motor_bolt",
            "construction": False, "constrain_centers": True,
            "equal_diameters": True,
        })
        call("sketcher.close_sketch", {})
        call("partdesign.pocket", {
            "profile_name": holes_name, "label": "Shaft Bore and Bolt Pattern",
            "extent": {"type": "through_all"}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        call("core.update_design_brief", {
            "base_revision": context["design_brief"]["revision"],
            "changes": {
                "purpose": "A round motor adapter with a shaft bore and four-hole bolt circle.",
                "units": "mm", "manufacturing_process": "CNC machining",
                "critical_dimensions": [
                    {"name": "outside_diameter", "value": 80, "unit": "mm"},
                    {"name": "thickness", "value": 8, "unit": "mm"},
                    {"name": "shaft_bore_diameter", "value": 20, "unit": "mm"},
                    {"name": "bolt_circle_diameter", "value": 60, "unit": "mm"},
                    {"name": "bolt_hole_diameter", "value": 5, "unit": "mm"},
                ],
                "symmetry": ["Four equal bolt holes on a 60 mm pitch circle."],
                "validation_requirements": ["All five holes pass through the 8 mm plate."],
            },
        })
        return ProviderResult("Created an editable motor adapter with a shaft bore and four-hole bolt circle.", raw={"calls": calls})


class BatteryTrayProvider(BaseProvider):
    model = "deterministic-tier2-battery-tray-v1"

    def run(self, prompt, context, tool_runner=None, cancellation_check=None, progress_callback=None):
        calls = []

        def call(name, arguments):
            result = tool_runner(name, json.dumps(arguments, separators=(",", ":")))
            calls.append({"name": name, "result": result})
            if not result.get("ok"):
                raise RuntimeError(f"{name} failed: {result}")
            return result

        body = call("partdesign.create_body", {"label": "Battery Tray"})
        body_name = body["mutation"]["body"]
        profile = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Tray Outline",
            "support": {"type": "origin_plane", "plane": "XY_Plane"},
        })
        profile_name = profile["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": profile_name})
        call("sketcher.draw_rectangle", {
            "width": 100, "height": 60, "center_x": 0, "center_y": 0,
            "construction": False,
        })
        call("sketcher.close_sketch", {})
        pad = call("partdesign.pad", {
            "profile_name": profile_name, "label": "Tray Blank",
            "extent": {"type": "length", "length": 20}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        pad_name = pad["mutation"]["feature"]
        shell = call("partdesign.thickness", {
            "base_feature_name": pad_name, "label": "Open Battery Tray",
            "selection": {
                "type": "query", "element_type": "face", "expected_count": 1,
                "geometry_type": "plane", "normal": {"x": 0, "y": 0, "z": 1},
                "normal_tolerance_degrees": 1,
                "near_point": {"x": 0, "y": 0, "z": 20}, "max_distance": 1,
            },
            "wall_thickness": 2.5, "direction": "inward", "mode": "skin",
            "join": "intersection", "intersection_handling": False,
            "refine": True, "support_transform": False,
        })
        shell_name = shell["mutation"]["feature"]
        holes = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Tray Mounting Holes",
            "support": {
                "type": "planar_face", "object_name": shell_name,
                "selection": {
                    "type": "query", "normal": {"x": 0, "y": 0, "z": 1},
                    "near_point": {"x": 0, "y": 0, "z": 2.5},
                    "normal_tolerance_degrees": 1, "max_distance": 1,
                },
            },
        })
        holes_name = holes["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": holes_name})
        call("sketcher.add_hole_pattern", {
            "layout": {
                "type": "rectangular", "center_mm": [0, 0],
                "counts": [2, 2], "spacing_mm": [70, 30],
            },
            "hole_diameter": 4, "name_prefix": "tray_mount",
            "construction": False, "constrain_centers": True,
            "equal_diameters": True,
        })
        call("sketcher.close_sketch", {})
        call("partdesign.pocket", {
            "profile_name": holes_name, "label": "Tray Through Mounts",
            "extent": {"type": "through_all"}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        call("core.update_design_brief", {
            "base_revision": context["design_brief"]["revision"],
            "changes": {
                "purpose": "A printable battery tray with four bottom mounting holes.",
                "units": "mm", "manufacturing_process": "FDM printing",
                "critical_dimensions": [
                    {"name": "outside_width", "value": 100, "unit": "mm"},
                    {"name": "outside_depth", "value": 60, "unit": "mm"},
                    {"name": "height", "value": 20, "unit": "mm"},
                    {"name": "wall_thickness", "value": 2.5, "unit": "mm"},
                    {"name": "mounting_hole_diameter", "value": 4, "unit": "mm"},
                ],
                "symmetry": ["Four bottom holes on a centered 70 by 30 mm rectangular pattern."],
                "validation_requirements": ["The tray remains open at the top.", "All mounting holes pass through the bottom."],
            },
        })
        return ProviderResult("Created an editable battery tray with four through mounting holes.", raw={"calls": calls})


class CameraMountProvider(BaseProvider):
    model = "deterministic-tier2-camera-mount-v1"

    def run(self, prompt, context, tool_runner=None, cancellation_check=None, progress_callback=None):
        calls = []

        def call(name, arguments):
            result = tool_runner(name, json.dumps(arguments, separators=(",", ":")))
            calls.append({"name": name, "result": result})
            if not result.get("ok"):
                raise RuntimeError(f"{name} failed: {result}")
            return result

        body = call("partdesign.create_body", {"label": "Camera Mount"})
        body_name = body["mutation"]["body"]
        profile = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Mount Plate",
            "support": {"type": "origin_plane", "plane": "XY_Plane"},
        })
        profile_name = profile["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": profile_name})
        call("sketcher.draw_rectangle", {
            "width": 70, "height": 50, "center_x": 0, "center_y": 0,
            "construction": False,
        })
        call("sketcher.close_sketch", {})
        pad = call("partdesign.pad", {
            "profile_name": profile_name, "label": "Mount Blank",
            "extent": {"type": "length", "length": 6}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        pad_name = pad["mutation"]["feature"]
        features = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Camera and Adjustment Openings",
            "support": {
                "type": "planar_face", "object_name": pad_name,
                "selection": {
                    "type": "query", "normal": {"x": 0, "y": 0, "z": 1},
                    "near_point": {"x": 0, "y": 0, "z": 6},
                    "normal_tolerance_degrees": 1, "max_distance": 1,
                },
            },
        })
        feature_name = features["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": feature_name})
        call("sketcher.add_circle", {"center": [0, 0], "radius": 3.25, "construction": False})
        call("sketcher.constrain", {
            "constraints": [
                {
                    "type": "Lock",
                    "point": {"geometry": 0, "point": "center"},
                    "position_mm": [0, 0],
                },
                {"type": "Diameter", "geometry": 0, "size_mm": 6.5},
            ],
        })
        call("sketcher.add_slot", {
            "center_mm": [-22, 0], "overall_length": 20, "width": 6,
            "angle_degrees": 90, "construction": False,
        })
        call("sketcher.add_slot", {
            "center_mm": [22, 0], "overall_length": 20, "width": 6,
            "angle_degrees": 90, "construction": False,
        })
        call("sketcher.close_sketch", {})
        call("partdesign.pocket", {
            "profile_name": feature_name, "label": "Camera Hole and Adjustment Slots",
            "extent": {"type": "through_all"}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        call("core.update_design_brief", {
            "base_revision": context["design_brief"]["revision"],
            "changes": {
                "purpose": "An adjustable camera mounting plate.", "units": "mm",
                "manufacturing_process": "CNC machining",
                "critical_dimensions": [
                    {"name": "plate_width", "value": 70, "unit": "mm"},
                    {"name": "plate_depth", "value": 50, "unit": "mm"},
                    {"name": "plate_thickness", "value": 6, "unit": "mm"},
                    {"name": "camera_hole_diameter", "value": 6.5, "unit": "mm"},
                    {"name": "slot_length", "value": 20, "unit": "mm"},
                    {"name": "slot_width", "value": 6, "unit": "mm"},
                ],
                "symmetry": ["Two vertical adjustment slots centered 22 mm left and right."],
                "validation_requirements": ["The center hole and both slots pass through the plate."],
            },
        })
        return ProviderResult("Created an editable camera plate with a center hole and two adjustment slots.", raw={"calls": calls})


class PipeClampProvider(BaseProvider):
    model = "deterministic-tier2-pipe-clamp-v1"

    def run(self, prompt, context, tool_runner=None, cancellation_check=None, progress_callback=None):
        calls = []

        def call(name, arguments):
            result = tool_runner(name, json.dumps(arguments, separators=(",", ":")))
            calls.append({"name": name, "result": result})
            if not result.get("ok"):
                raise RuntimeError(f"{name} failed: {result}")
            return result

        body = call("partdesign.create_body", {"label": "Split Pipe Clamp"})
        body_name = body["mutation"]["body"]
        profile = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Clamp Ring",
            "support": {"type": "origin_plane", "plane": "XY_Plane"},
        })
        profile_name = profile["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": profile_name})
        call("sketcher.add_circle", {"center": [0, 0], "radius": 30, "construction": False})
        call("sketcher.add_circle", {"center": [0, 0], "radius": 20, "construction": False})
        call("sketcher.constrain", {
            "constraints": [
                {
                    "type": "Lock",
                    "point": {"geometry": 0, "point": "center"},
                    "position_mm": [0, 0],
                },
                {"type": "Diameter", "geometry": 0, "size_mm": 60},
                {
                    "type": "Lock",
                    "point": {"geometry": 1, "point": "center"},
                    "position_mm": [0, 0],
                },
                {"type": "Diameter", "geometry": 1, "size_mm": 40},
            ],
        })
        call("sketcher.close_sketch", {})
        pad = call("partdesign.pad", {
            "profile_name": profile_name, "label": "Clamp Blank",
            "extent": {"type": "length", "length": 12}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        pad_name = pad["mutation"]["feature"]
        cuts = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Clamp Split and Mounts",
            "support": {
                "type": "planar_face", "object_name": pad_name,
                "selection": {
                    "type": "query", "normal": {"x": 0, "y": 0, "z": 1},
                    "near_point": {"x": 0, "y": 25, "z": 12},
                    "normal_tolerance_degrees": 1, "max_distance": 1,
                },
            },
        })
        cuts_name = cuts["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": cuts_name})
        call("sketcher.draw_rectangle", {
            "width": 4, "height": 12, "center_x": 0, "center_y": 25,
            "construction": False,
        })
        call("sketcher.add_circle", {"center": [-25, 0], "radius": 2.5, "construction": False})
        call("sketcher.add_circle", {"center": [25, 0], "radius": 2.5, "construction": False})
        call("sketcher.constrain", {
            "constraints": [
                {
                    "type": "Lock",
                    "point": {"geometry": 4, "point": "center"},
                    "position_mm": [-25, 0],
                },
                {"type": "Diameter", "geometry": 4, "size_mm": 5},
                {
                    "type": "Lock",
                    "point": {"geometry": 5, "point": "center"},
                    "position_mm": [25, 0],
                },
                {"type": "Diameter", "geometry": 5, "size_mm": 5},
            ],
        })
        call("sketcher.close_sketch", {})
        call("partdesign.pocket", {
            "profile_name": cuts_name, "label": "Split and Through Mounts",
            "extent": {"type": "through_all"}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        call("core.update_design_brief", {
            "base_revision": context["design_brief"]["revision"],
            "changes": {
                "purpose": "A split clamp for a 40 mm pipe with two mounting holes.",
                "units": "mm", "manufacturing_process": "CNC machining",
                "critical_dimensions": [
                    {"name": "pipe_diameter", "value": 40, "unit": "mm"},
                    {"name": "outside_diameter", "value": 60, "unit": "mm"},
                    {"name": "clamp_width", "value": 12, "unit": "mm"},
                    {"name": "split_width", "value": 4, "unit": "mm"},
                    {"name": "mounting_hole_diameter", "value": 5, "unit": "mm"},
                ],
                "symmetry": ["Two equal through holes at 25 mm left and right of center."],
                "validation_requirements": ["The radial split opens the ring.", "Both mounting holes pass through the clamp width."],
            },
        })
        return ProviderResult("Created an editable split pipe clamp with two through mounting holes.", raw={"calls": calls})


class VentilatedCoverProvider(BaseProvider):
    model = "deterministic-tier2-ventilated-cover-v1"

    def run(self, prompt, context, tool_runner=None, cancellation_check=None, progress_callback=None):
        calls = []

        def call(name, arguments):
            result = tool_runner(name, json.dumps(arguments, separators=(",", ":")))
            calls.append({"name": name, "result": result})
            if not result.get("ok"):
                raise RuntimeError(f"{name} failed: {result}")
            return result

        body = call("partdesign.create_body", {"label": "Ventilated Cover"})
        body_name = body["mutation"]["body"]
        profile = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Cover Outline",
            "support": {"type": "origin_plane", "plane": "XY_Plane"},
        })
        profile_name = profile["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": profile_name})
        call("sketcher.draw_rectangle", {
            "width": 80, "height": 50, "center_x": 0, "center_y": 0,
            "construction": False,
        })
        call("sketcher.close_sketch", {})
        pad = call("partdesign.pad", {
            "profile_name": profile_name, "label": "Cover Blank",
            "extent": {"type": "length", "length": 3}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        pad_name = pad["mutation"]["feature"]
        vents = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Ventilation Slots",
            "support": {
                "type": "planar_face", "object_name": pad_name,
                "selection": {
                    "type": "query", "normal": {"x": 0, "y": 0, "z": 1},
                    "near_point": {"x": 0, "y": 0, "z": 3},
                    "normal_tolerance_degrees": 1, "max_distance": 1,
                },
            },
        })
        vents_name = vents["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": vents_name})
        for y_value in (-16, -8, 0, 8, 16):
            call("sketcher.add_slot", {
                "center_mm": [0, y_value], "overall_length": 50, "width": 3,
                "angle_degrees": 0, "construction": False,
            })
        call("sketcher.close_sketch", {})
        call("partdesign.pocket", {
            "profile_name": vents_name, "label": "Through Ventilation Slots",
            "extent": {"type": "through_all"}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        call("core.update_design_brief", {
            "base_revision": context["design_brief"]["revision"],
            "changes": {
                "purpose": "A ventilated equipment cover with five through-slots.",
                "units": "mm", "manufacturing_process": "laser cutting",
                "critical_dimensions": [
                    {"name": "cover_width", "value": 80, "unit": "mm"},
                    {"name": "cover_depth", "value": 50, "unit": "mm"},
                    {"name": "cover_thickness", "value": 3, "unit": "mm"},
                    {"name": "vent_length", "value": 50, "unit": "mm"},
                    {"name": "vent_width", "value": 3, "unit": "mm"},
                ],
                "symmetry": ["Five horizontal vents on 8 mm vertical spacing."],
                "validation_requirements": ["Every ventilation slot passes through the cover."],
            },
        })
        return ProviderResult("Created an editable cover with five through ventilation slots.", raw={"calls": calls})


class ElectronicsEnclosureProvider(BaseProvider):
    model = "deterministic-tier2-electronics-enclosure-v1"

    def run(self, prompt, context, tool_runner=None, cancellation_check=None, progress_callback=None):
        calls = []

        def call(name, arguments):
            result = tool_runner(name, json.dumps(arguments, separators=(",", ":")))
            calls.append({"name": name, "result": result})
            if not result.get("ok"):
                raise RuntimeError(f"{name} failed: {result}")
            return result

        housing = call("partdesign.create_body", {"label": "Electronics Enclosure"})
        housing_body = housing["mutation"]["body"]
        housing_profile = call("partdesign.create_sketch", {
            "body_name": housing_body, "label": "Housing Outline",
            "support": {"type": "origin_plane", "plane": "XY_Plane"},
        })
        housing_sketch = housing_profile["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": housing_sketch})
        call("sketcher.draw_rectangle", {
            "width": 120, "height": 80, "center_x": 0, "center_y": 0,
            "construction": False,
        })
        call("sketcher.close_sketch", {})
        housing_pad = call("partdesign.pad", {
            "profile_name": housing_sketch, "label": "Housing Blank",
            "extent": {"type": "length", "length": 35}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        call("partdesign.thickness", {
            "base_feature_name": housing_pad["mutation"]["feature"],
            "label": "Open Housing",
            "selection": {
                "type": "query", "element_type": "face", "expected_count": 1,
                "geometry_type": "plane", "normal": {"x": 0, "y": 0, "z": 1},
                "normal_tolerance_degrees": 1,
                "near_point": {"x": 0, "y": 0, "z": 35}, "max_distance": 1,
            },
            "wall_thickness": 2.5, "direction": "inward", "mode": "skin",
            "join": "intersection", "intersection_handling": False,
            "refine": True, "support_transform": False,
        })

        lid = call("partdesign.create_body", {"label": "Removable Lid"})
        lid_body = lid["mutation"]["body"]
        lid_profile = call("partdesign.create_sketch", {
            "body_name": lid_body, "label": "Lid Outline",
            "support": {"type": "origin_plane", "plane": "XY_Plane"},
        })
        lid_sketch = lid_profile["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": lid_sketch})
        call("sketcher.draw_rectangle", {
            "width": 120, "height": 80, "center_x": 140, "center_y": 0,
            "construction": False,
        })
        call("sketcher.close_sketch", {})
        lid_pad = call("partdesign.pad", {
            "profile_name": lid_sketch, "label": "Lid Plate",
            "extent": {"type": "length", "length": 3}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        lid_holes = call("partdesign.create_sketch", {
            "body_name": lid_body, "label": "M3 Lid Holes",
            "support": {
                "type": "planar_face", "object_name": lid_pad["mutation"]["feature"],
                "selection": {
                    "type": "query", "normal": {"x": 0, "y": 0, "z": 1},
                    "near_point": {"x": 140, "y": 0, "z": 3},
                    "normal_tolerance_degrees": 1, "max_distance": 1,
                },
            },
        })
        lid_hole_sketch = lid_holes["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": lid_hole_sketch})
        call("sketcher.add_hole_pattern", {
            "layout": {
                "type": "rectangular", "center_mm": [140, 0],
                "counts": [2, 2], "spacing_mm": [90, 60],
            },
            "hole_diameter": 3.2, "name_prefix": "lid_m3",
            "construction": False, "constrain_centers": True,
            "equal_diameters": True,
        })
        call("sketcher.close_sketch", {})
        call("partdesign.pocket", {
            "profile_name": lid_hole_sketch, "label": "M3 Lid Through Holes",
            "extent": {"type": "through_all"}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        call("core.update_design_brief", {
            "base_revision": context["design_brief"]["revision"],
            "changes": {
                "purpose": "A printable two-part electronics enclosure with a removable screw lid.",
                "units": "mm", "manufacturing_process": "FDM printing",
                "critical_dimensions": [
                    {"name": "housing_width", "value": 120, "unit": "mm"},
                    {"name": "housing_depth", "value": 80, "unit": "mm"},
                    {"name": "housing_height", "value": 35, "unit": "mm"},
                    {"name": "wall_thickness", "value": 2.5, "unit": "mm"},
                    {"name": "lid_thickness", "value": 3, "unit": "mm"},
                    {"name": "lid_hole_diameter", "value": 3.2, "unit": "mm"},
                ],
                "mating_parts": ["Electronics Enclosure", "Removable Lid"],
                "validation_requirements": [
                    "The housing and lid remain separate editable bodies.",
                    "All four M3 clearance holes pass through the lid.",
                ],
            },
        })
        return ProviderResult(
            "Created an editable enclosure and separate removable lid with four M3 clearance holes.",
            raw={"calls": calls},
        )


class FlangedCouplingProvider(BaseProvider):
    model = "deterministic-tier2-flanged-coupling-v1"

    def run(self, prompt, context, tool_runner=None, cancellation_check=None, progress_callback=None):
        calls = []

        def call(name, arguments):
            result = tool_runner(name, json.dumps(arguments, separators=(",", ":")))
            calls.append({"name": name, "result": result})
            if not result.get("ok"):
                raise RuntimeError(f"{name} failed: {result}")
            return result

        body = call("partdesign.create_body", {"label": "Flanged Coupling"})
        body_name = body["mutation"]["body"]
        flange = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Flange Disc",
            "support": {"type": "origin_plane", "plane": "XY_Plane"},
        })
        flange_name = flange["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": flange_name})
        call("sketcher.add_circle", {
            "center": [0, 0], "radius": 40, "construction": False,
        })
        call("sketcher.constrain", {
            "constraints": [
                {
                    "type": "Lock",
                    "point": {"geometry": 0, "point": "center"},
                    "position_mm": [0, 0],
                },
                {"type": "Diameter", "geometry": 0, "size_mm": 80},
            ],
        })
        call("sketcher.close_sketch", {})
        flange_pad = call("partdesign.pad", {
            "profile_name": flange_name, "label": "Coupling Flange",
            "extent": {"type": "length", "length": 10}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        flange_pad_name = flange_pad["mutation"]["feature"]
        holes = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Coupling Bore and Bolt Holes",
            "support": {
                "type": "planar_face", "object_name": flange_pad_name,
                "selection": {
                    "type": "query", "normal": {"x": 0, "y": 0, "z": 1},
                    "near_point": {"x": 0, "y": 0, "z": 10},
                    "normal_tolerance_degrees": 1, "max_distance": 1,
                },
            },
        })
        holes_name = holes["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": holes_name})
        call("sketcher.add_circle", {
            "center": [0, 0], "radius": 10, "construction": False,
        })
        call("sketcher.constrain", {
            "constraints": [
                {
                    "type": "Lock",
                    "point": {"geometry": 0, "point": "center"},
                    "position_mm": [0, 0],
                },
                {"type": "Diameter", "geometry": 0, "size_mm": 20},
            ],
        })
        call("sketcher.add_hole_pattern", {
            "layout": {
                "type": "circular", "center_mm": [0, 0], "count": 4,
                "pitch_circle_diameter_mm": 60, "start_angle_degrees": 45,
            },
            "hole_diameter": 6, "name_prefix": "flange_bolt",
            "construction": False, "constrain_centers": True,
            "equal_diameters": True,
        })
        call("sketcher.close_sketch", {})
        flange_cut = call("partdesign.pocket", {
            "profile_name": holes_name, "label": "Bore and Bolt Holes",
            "extent": {"type": "through_all"}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        hub = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Coupling Hub",
            "support": {
                "type": "planar_face",
                "object_name": flange_cut["mutation"]["feature"],
                "selection": {
                    "type": "query", "normal": {"x": 0, "y": 0, "z": 1},
                    "near_point": {"x": 15, "y": 0, "z": 10},
                    "normal_tolerance_degrees": 1, "max_distance": 1,
                },
            },
        })
        hub_name = hub["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": hub_name})
        call("sketcher.add_circle", {
            "center": [0, 0], "radius": 20, "construction": False,
        })
        call("sketcher.add_circle", {
            "center": [0, 0], "radius": 10, "construction": False,
        })
        call("sketcher.constrain", {
            "constraints": [
                {
                    "type": "Lock",
                    "point": {"geometry": 0, "point": "center"},
                    "position_mm": [0, 0],
                },
                {"type": "Diameter", "geometry": 0, "size_mm": 40},
                {
                    "type": "Lock",
                    "point": {"geometry": 1, "point": "center"},
                    "position_mm": [0, 0],
                },
                {"type": "Diameter", "geometry": 1, "size_mm": 20},
            ],
        })
        call("sketcher.close_sketch", {})
        call("partdesign.pad", {
            "profile_name": hub_name, "label": "Coupling Hub Pad",
            "extent": {"type": "length", "length": 10}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        call("core.update_design_brief", {
            "base_revision": context["design_brief"]["revision"],
            "changes": {
                "purpose": "A flanged shaft coupling with one hub and a four-hole bolt circle.",
                "units": "mm", "manufacturing_process": "CNC machining",
                "critical_dimensions": [
                    {"name": "flange_diameter", "value": 80, "unit": "mm"},
                    {"name": "flange_thickness", "value": 10, "unit": "mm"},
                    {"name": "hub_diameter", "value": 40, "unit": "mm"},
                    {"name": "overall_length", "value": 20, "unit": "mm"},
                    {"name": "bore_diameter", "value": 20, "unit": "mm"},
                    {"name": "bolt_circle_diameter", "value": 60, "unit": "mm"},
                    {"name": "bolt_hole_diameter", "value": 6, "unit": "mm"},
                ],
                "symmetry": ["Four equal holes on a 60 mm pitch circle."],
                "validation_requirements": [
                    "The bore passes through the flange and hub.",
                    "The final coupling remains one editable solid.",
                ],
            },
        })
        return ProviderResult(
            "Created an editable flanged coupling with a through-bore and four bolt holes.",
            raw={"calls": calls},
        )


class SimpleHingeProvider(BaseProvider):
    model = "deterministic-tier2-simple-hinge-v1"

    def run(self, prompt, context, tool_runner=None, cancellation_check=None, progress_callback=None):
        calls = []

        def call(name, arguments):
            result = tool_runner(name, json.dumps(arguments, separators=(",", ":")))
            calls.append({"name": name, "result": result})
            if not result.get("ok"):
                raise RuntimeError(f"{name} failed: {result}")
            return result

        def create_leaf(label, center_x):
            body = call("partdesign.create_body", {"label": label})
            body_name = body["mutation"]["body"]
            profile = call("partdesign.create_sketch", {
                "body_name": body_name, "label": f"{label} Outline",
                "support": {"type": "origin_plane", "plane": "XY_Plane"},
            })
            profile_name = profile["mutation"]["sketch"]
            call("partdesign.edit_sketch", {"sketch_name": profile_name})
            call("sketcher.draw_rectangle", {
                "width": 40, "height": 30,
                "center_x": center_x, "center_y": 0,
                "construction": False,
            })
            call("sketcher.close_sketch", {})
            pad = call("partdesign.pad", {
                "profile_name": profile_name, "label": f"{label} Plate",
                "extent": {"type": "length", "length": 3},
                "side": "one_side", "reversed": False,
                "taper_angle_degrees": 0,
                "second_taper_angle_degrees": 0, "refine": True,
            })
            holes = call("partdesign.create_sketch", {
                "body_name": body_name, "label": f"{label} Mounting Holes",
                "support": {
                    "type": "planar_face",
                    "object_name": pad["mutation"]["feature"],
                    "selection": {
                        "type": "query",
                        "normal": {"x": 0, "y": 0, "z": 1},
                        "near_point": {"x": center_x, "y": 0, "z": 3},
                        "normal_tolerance_degrees": 1, "max_distance": 1,
                    },
                },
            })
            holes_name = holes["mutation"]["sketch"]
            call("partdesign.edit_sketch", {"sketch_name": holes_name})
            call("sketcher.add_circle", {
                "center": [center_x, -8], "radius": 2,
                "construction": False,
            })
            call("sketcher.add_circle", {
                "center": [center_x, 8], "radius": 2,
                "construction": False,
            })
            call("sketcher.constrain", {
                "constraints": [
                    {
                        "type": "Lock",
                        "point": {"geometry": 0, "point": "center"},
                        "position_mm": [center_x, -8],
                    },
                    {"type": "Diameter", "geometry": 0, "size_mm": 4},
                    {
                        "type": "Lock",
                        "point": {"geometry": 1, "point": "center"},
                        "position_mm": [center_x, 8],
                    },
                    {"type": "Diameter", "geometry": 1, "size_mm": 4},
                ],
            })
            call("sketcher.close_sketch", {})
            call("partdesign.pocket", {
                "profile_name": holes_name,
                "label": f"{label} Through Mounts",
                "extent": {"type": "through_all"}, "side": "one_side",
                "reversed": False, "taper_angle_degrees": 0,
                "second_taper_angle_degrees": 0, "refine": True,
            })

        create_leaf("Left Hinge Leaf", -24)
        create_leaf("Right Hinge Leaf", 24)

        barrel_body = call("partdesign.create_body", {"label": "Hinge Barrel"})
        barrel = call("partdesign.create_sketch", {
            "body_name": barrel_body["mutation"]["body"],
            "label": "Hinge Barrel Profile",
            "support": {"type": "origin_plane", "plane": "XY_Plane"},
        })
        barrel_name = barrel["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": barrel_name})
        call("sketcher.add_circle", {
            "center": [0, 0], "radius": 4, "construction": False,
        })
        call("sketcher.add_circle", {
            "center": [0, 0], "radius": 3, "construction": False,
        })
        call("sketcher.constrain", {
            "constraints": [
                {
                    "type": "Lock",
                    "point": {"geometry": 0, "point": "center"},
                    "position_mm": [0, 0],
                },
                {"type": "Diameter", "geometry": 0, "size_mm": 8},
                {
                    "type": "Lock",
                    "point": {"geometry": 1, "point": "center"},
                    "position_mm": [0, 0],
                },
                {"type": "Diameter", "geometry": 1, "size_mm": 6},
            ],
        })
        call("sketcher.close_sketch", {})
        call("partdesign.pad", {
            "profile_name": barrel_name, "label": "Hinge Barrel Sleeve",
            "extent": {"type": "length", "length": 3}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })

        pin_body = call("partdesign.create_body", {"label": "Hinge Pin"})
        pin = call("partdesign.create_sketch", {
            "body_name": pin_body["mutation"]["body"],
            "label": "Hinge Pin Profile",
            "support": {"type": "origin_plane", "plane": "XY_Plane"},
        })
        pin_name = pin["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": pin_name})
        call("sketcher.add_circle", {
            "center": [0, 0], "radius": 2.5, "construction": False,
        })
        call("sketcher.constrain", {
            "constraints": [
                {
                    "type": "Lock",
                    "point": {"geometry": 0, "point": "center"},
                    "position_mm": [0, 0],
                },
                {"type": "Diameter", "geometry": 0, "size_mm": 5},
            ],
        })
        call("sketcher.close_sketch", {})
        call("partdesign.pad", {
            "profile_name": pin_name, "label": "Hinge Pin Shaft",
            "extent": {"type": "length", "length": 5}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        call("core.update_design_brief", {
            "base_revision": context["design_brief"]["revision"],
            "changes": {
                "purpose": "A four-component flat hinge layout with two leaves, a barrel, and a removable pin.",
                "units": "mm", "manufacturing_process": "CNC machining",
                "critical_dimensions": [
                    {"name": "leaf_width", "value": 40, "unit": "mm"},
                    {"name": "leaf_height", "value": 30, "unit": "mm"},
                    {"name": "leaf_thickness", "value": 3, "unit": "mm"},
                    {"name": "leaf_gap", "value": 8, "unit": "mm"},
                    {"name": "barrel_outside_diameter", "value": 8, "unit": "mm"},
                    {"name": "barrel_inside_diameter", "value": 6, "unit": "mm"},
                    {"name": "pin_diameter", "value": 5, "unit": "mm"},
                    {"name": "mounting_hole_diameter", "value": 4, "unit": "mm"},
                ],
                "mating_parts": [
                    "Left Hinge Leaf", "Right Hinge Leaf",
                    "Hinge Barrel", "Hinge Pin",
                ],
                "validation_requirements": [
                    "All four components remain separately editable.",
                    "The pin has 0.5 mm radial clearance in the barrel.",
                ],
            },
        })
        return ProviderResult(
            "Created an editable four-component flat hinge layout.",
            raw={"calls": calls},
        )


class BoltPatternPlateProvider(BaseProvider):
    model = "deterministic-tier2-bolt-pattern-plate-v1"

    def run(self, prompt, context, tool_runner=None, cancellation_check=None, progress_callback=None):
        calls = []

        def call(name, arguments):
            result = tool_runner(name, json.dumps(arguments, separators=(",", ":")))
            calls.append({"name": name, "result": result})
            if not result.get("ok"):
                raise RuntimeError(f"{name} failed: {result}")
            return result

        body = call("partdesign.create_body", {"label": "Bolt Pattern Plate"})
        body_name = body["mutation"]["body"]
        plate = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Plate Outline",
            "support": {"type": "origin_plane", "plane": "XY_Plane"},
        })
        plate_name = plate["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": plate_name})
        call("sketcher.draw_rectangle", {
            "width": 100, "height": 80, "center_x": 0, "center_y": 0,
            "construction": False,
        })
        call("sketcher.close_sketch", {})
        plate_pad = call("partdesign.pad", {
            "profile_name": plate_name, "label": "Bolt Pattern Plate Blank",
            "extent": {"type": "length", "length": 8}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        holes = call("partdesign.create_sketch", {
            "body_name": body_name, "label": "Center Bore and Bolt Pattern",
            "support": {
                "type": "planar_face",
                "object_name": plate_pad["mutation"]["feature"],
                "selection": {
                    "type": "query", "normal": {"x": 0, "y": 0, "z": 1},
                    "near_point": {"x": 0, "y": 0, "z": 8},
                    "normal_tolerance_degrees": 1, "max_distance": 1,
                },
            },
        })
        holes_name = holes["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": holes_name})
        call("sketcher.add_circle", {
            "center": [0, 0], "radius": 10, "construction": False,
        })
        call("sketcher.constrain", {
            "constraints": [
                {
                    "type": "Lock",
                    "point": {"geometry": 0, "point": "center"},
                    "position_mm": [0, 0],
                },
                {"type": "Diameter", "geometry": 0, "size_mm": 20},
            ],
        })
        call("sketcher.add_hole_pattern", {
            "layout": {
                "type": "circular", "center_mm": [0, 0], "count": 8,
                "pitch_circle_diameter_mm": 60, "start_angle_degrees": 0,
            },
            "hole_diameter": 6, "name_prefix": "plate_bolt",
            "construction": False, "constrain_centers": True,
            "equal_diameters": True,
        })
        call("sketcher.close_sketch", {})
        call("partdesign.pocket", {
            "profile_name": holes_name, "label": "Through Bore and Bolt Pattern",
            "extent": {"type": "through_all"}, "side": "one_side",
            "reversed": False, "taper_angle_degrees": 0,
            "second_taper_angle_degrees": 0, "refine": True,
        })
        call("core.update_design_brief", {
            "base_revision": context["design_brief"]["revision"],
            "changes": {
                "purpose": "A rectangular adapter plate with one bore and an eight-hole bolt circle.",
                "units": "mm", "manufacturing_process": "CNC machining",
                "critical_dimensions": [
                    {"name": "plate_width", "value": 100, "unit": "mm"},
                    {"name": "plate_height", "value": 80, "unit": "mm"},
                    {"name": "plate_thickness", "value": 8, "unit": "mm"},
                    {"name": "center_bore_diameter", "value": 20, "unit": "mm"},
                    {"name": "bolt_circle_diameter", "value": 60, "unit": "mm"},
                    {"name": "bolt_hole_diameter", "value": 6, "unit": "mm"},
                ],
                "symmetry": ["Eight equal holes on a 60 mm pitch circle."],
                "validation_requirements": [
                    "The bore and all eight bolt holes pass through the plate.",
                ],
            },
        })
        return ProviderResult(
            "Created an editable bolt-pattern plate with eight equal holes.",
            raw={"calls": calls},
        )


def main() -> int:
    output = Path(sys.argv[-1])
    output.mkdir(parents=True, exist_ok=True)
    attempt = int(os.environ.get("VIBECAD_BENCHMARK_ATTEMPT", "1"))
    Gui.activateWorkbench("PartDesignWorkbench")
    trial_id = uuid.uuid4().hex[:12]
    document = App.newDocument(f"Tier2WallBracket{trial_id}")
    artifact = output / f"tier2-wall-bracket-{trial_id}.FCStd"
    document.saveAs(str(artifact))
    service = VibeCADService()
    started = time.monotonic()
    bracket_started = time.monotonic()
    response = run_prompt(
        "Create a 60 by 40 mm right-angle wall bracket, 30 mm wide with 8 mm legs, and add two 4 mm through mounting holes.",
        service=service, prefer_online=False, provider=WallBracketProvider(),
    )
    document.recompute()
    pockets = [obj for obj in document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"]
    bracket_geometry_passed = not response.error and len(pockets) == 1
    bracket_dimensions_passed = False
    expected_volume = (
        (60 * 8 + 8 * (40 - 8)) * 30 - 2 * math.pi * 2 * 2 * 30
    )
    bracket_measurements = {}
    if bracket_geometry_passed:
        shape = pockets[0].Shape
        bracket_geometry_passed = shape.isValid() and not shape.isNull()
        bracket_measurements = {
            "x_length": shape.BoundBox.XLength,
            "y_length": shape.BoundBox.YLength,
            "z_length": shape.BoundBox.ZLength,
            "volume": shape.Volume,
            "expected_volume": expected_volume,
        }
        bracket_dimensions_passed = (
            abs(shape.BoundBox.XLength - 60) < 1e-6 and
            abs(shape.BoundBox.YLength - 40) < 1e-6 and
            abs(shape.BoundBox.ZLength - 30) < 1e-6 and
            abs(shape.Volume - expected_volume) < 1e-3
        )
    passed = bracket_geometry_passed and bracket_dimensions_passed
    bracket_constraints = _constraint_evidence(document)
    bracket_types = [
        str(getattr(obj, "TypeId", "")) for obj in document.Objects
    ]
    document.save()
    revisions = service.revision_timeline()
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(artifact))
    reopened.recompute()
    final_feature = next((obj for obj in reopened.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"), None)
    reopen_passed = bool(final_feature and final_feature.Shape.isValid() and not final_feature.Shape.isNull())
    bracket_reopened_types = [
        str(getattr(obj, "TypeId", "")) for obj in reopened.Objects
    ]
    App.closeDocument(reopened.Name)
    bracket_elapsed = time.monotonic() - bracket_started
    Gui.activateWorkbench("PartDesignWorkbench")
    adapter_document = App.newDocument(f"Tier2MotorAdapter{trial_id}")
    adapter_artifact = output / f"tier2-motor-adapter-{trial_id}.FCStd"
    adapter_document.saveAs(str(adapter_artifact))
    adapter_service = VibeCADService()
    adapter_started = time.monotonic()
    adapter_response = run_prompt(
        "Create an 80 mm round motor adapter, 8 mm thick, with a 20 mm shaft bore and four 5 mm holes on a 60 mm bolt circle.",
        service=adapter_service, prefer_online=False, provider=MotorAdapterProvider(),
    )
    adapter_document.recompute()
    adapter_pockets = [obj for obj in adapter_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"]
    adapter_geometry_passed = (
        not adapter_response.error and len(adapter_pockets) == 1
    )
    adapter_dimensions_passed = False
    adapter_measurements = {}
    if adapter_geometry_passed:
        adapter_shape = adapter_pockets[0].Shape
        expected_adapter_volume = math.pi * 40 * 40 * 8 - math.pi * 10 * 10 * 8 - 4 * math.pi * 2.5 * 2.5 * 8
        adapter_measurements = {
            "x_length": adapter_shape.BoundBox.XLength,
            "y_length": adapter_shape.BoundBox.YLength,
            "z_length": adapter_shape.BoundBox.ZLength,
            "volume": adapter_shape.Volume,
            "expected_volume": expected_adapter_volume,
            "valid": adapter_shape.isValid(),
            "null": adapter_shape.isNull(),
        }
        adapter_geometry_passed = (
            adapter_shape.isValid() and not adapter_shape.isNull()
        )
        adapter_dimensions_passed = (
            # The live Sketcher-derived circle can have a transient OCC bound
            # up to 0.016 mm below its exact diameter before save/reopen.
            abs(adapter_shape.BoundBox.XLength - 80) < 0.02 and
            abs(adapter_shape.BoundBox.YLength - 80) < 0.02 and
            abs(adapter_shape.BoundBox.ZLength - 8) < 1e-6 and
            abs(adapter_shape.Volume - expected_adapter_volume) < 1e-3
        )
    adapter_passed = adapter_geometry_passed and adapter_dimensions_passed
    adapter_constraints = _constraint_evidence(adapter_document)
    adapter_types = [
        str(getattr(obj, "TypeId", "")) for obj in adapter_document.Objects
    ]
    adapter_document.save()
    adapter_revisions = adapter_service.revision_timeline()
    App.closeDocument(adapter_document.Name)
    reopened_adapter = App.openDocument(str(adapter_artifact))
    reopened_adapter.recompute()
    adapter_final = next((obj for obj in reopened_adapter.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"), None)
    adapter_reopen_passed = bool(adapter_final and adapter_final.Shape.isValid() and not adapter_final.Shape.isNull())
    adapter_reopened_types = [
        str(getattr(obj, "TypeId", "")) for obj in reopened_adapter.Objects
    ]
    App.closeDocument(reopened_adapter.Name)
    adapter_elapsed = time.monotonic() - adapter_started
    Gui.activateWorkbench("PartDesignWorkbench")
    tray_document = App.newDocument(f"Tier2BatteryTray{trial_id}")
    tray_artifact = output / f"tier2-battery-tray-{trial_id}.FCStd"
    tray_document.saveAs(str(tray_artifact))
    tray_service = VibeCADService()
    tray_started = time.monotonic()
    tray_response = run_prompt(
        "Create a 100 by 60 by 20 mm battery tray with 2.5 mm walls and four 4 mm bottom mounting holes on a 70 by 30 mm pattern.",
        service=tray_service, prefer_online=False, provider=BatteryTrayProvider(),
    )
    tray_document.recompute()
    tray_pockets = [obj for obj in tray_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"]
    tray_geometry_passed = not tray_response.error and len(tray_pockets) == 1
    tray_dimensions_passed = False
    tray_measurements = {}
    if tray_geometry_passed:
        tray_shape = tray_pockets[0].Shape
        expected_tray_volume = 100 * 60 * 20 - 95 * 55 * 17.5 - 4 * math.pi * 2 * 2 * 2.5
        tray_measurements = {
            "x_length": tray_shape.BoundBox.XLength, "y_length": tray_shape.BoundBox.YLength,
            "z_length": tray_shape.BoundBox.ZLength, "volume": tray_shape.Volume,
            "expected_volume": expected_tray_volume,
        }
        tray_geometry_passed = tray_shape.isValid() and not tray_shape.isNull()
        tray_dimensions_passed = (
            abs(tray_shape.BoundBox.XLength - 100) < 1e-6 and
            abs(tray_shape.BoundBox.YLength - 60) < 1e-6 and
            abs(tray_shape.BoundBox.ZLength - 20) < 1e-6 and
            abs(tray_shape.Volume - expected_tray_volume) < 1e-3
        )
    tray_passed = tray_geometry_passed and tray_dimensions_passed
    tray_constraints = _constraint_evidence(tray_document)
    tray_types = [
        str(getattr(obj, "TypeId", "")) for obj in tray_document.Objects
    ]
    tray_document.save()
    tray_revisions = tray_service.revision_timeline()
    App.closeDocument(tray_document.Name)
    reopened_tray = App.openDocument(str(tray_artifact))
    reopened_tray.recompute()
    tray_final = next((obj for obj in reopened_tray.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"), None)
    tray_reopen_passed = bool(tray_final and tray_final.Shape.isValid() and not tray_final.Shape.isNull())
    tray_reopened_types = [
        str(getattr(obj, "TypeId", "")) for obj in reopened_tray.Objects
    ]
    App.closeDocument(reopened_tray.Name)
    tray_elapsed = time.monotonic() - tray_started
    Gui.activateWorkbench("PartDesignWorkbench")
    camera_document = App.newDocument(f"Tier2CameraMount{trial_id}")
    camera_artifact = output / f"tier2-camera-mount-{trial_id}.FCStd"
    camera_document.saveAs(str(camera_artifact))
    camera_service = VibeCADService()
    camera_started = time.monotonic()
    camera_response = run_prompt(
        "Create a 70 by 50 by 6 mm camera mounting plate with a centered 6.5 mm hole and two vertical 20 by 6 mm adjustment slots 22 mm from center.",
        service=camera_service, prefer_online=False, provider=CameraMountProvider(),
    )
    camera_document.recompute()
    camera_pockets = [obj for obj in camera_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"]
    camera_geometry_passed = (
        not camera_response.error and len(camera_pockets) == 1
    )
    camera_dimensions_passed = False
    camera_measurements = {}
    if camera_geometry_passed:
        camera_shape = camera_pockets[0].Shape
        slot_area = (20 - 6) * 6 + math.pi * 3 * 3
        expected_camera_volume = 70 * 50 * 6 - math.pi * 3.25 * 3.25 * 6 - 2 * slot_area * 6
        camera_measurements = {
            "x_length": camera_shape.BoundBox.XLength, "y_length": camera_shape.BoundBox.YLength,
            "z_length": camera_shape.BoundBox.ZLength, "volume": camera_shape.Volume,
            "expected_volume": expected_camera_volume,
        }
        camera_geometry_passed = (
            camera_shape.isValid() and not camera_shape.isNull()
        )
        camera_dimensions_passed = (
            abs(camera_shape.BoundBox.XLength - 70) < 1e-6 and
            abs(camera_shape.BoundBox.YLength - 50) < 1e-6 and
            abs(camera_shape.BoundBox.ZLength - 6) < 1e-6 and
            abs(camera_shape.Volume - expected_camera_volume) < 1e-3
        )
    camera_passed = camera_geometry_passed and camera_dimensions_passed
    camera_constraints = _constraint_evidence(camera_document)
    camera_types = [
        str(getattr(obj, "TypeId", "")) for obj in camera_document.Objects
    ]
    camera_document.save()
    camera_revisions = camera_service.revision_timeline()
    App.closeDocument(camera_document.Name)
    reopened_camera = App.openDocument(str(camera_artifact))
    reopened_camera.recompute()
    camera_final = next((obj for obj in reopened_camera.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"), None)
    camera_reopen_passed = bool(camera_final and camera_final.Shape.isValid() and not camera_final.Shape.isNull())
    camera_reopened_types = [
        str(getattr(obj, "TypeId", "")) for obj in reopened_camera.Objects
    ]
    App.closeDocument(reopened_camera.Name)
    camera_elapsed = time.monotonic() - camera_started
    Gui.activateWorkbench("PartDesignWorkbench")
    clamp_document = App.newDocument(f"Tier2PipeClamp{trial_id}")
    clamp_artifact = output / f"tier2-pipe-clamp-{trial_id}.FCStd"
    clamp_document.saveAs(str(clamp_artifact))
    clamp_service = VibeCADService()
    clamp_started = time.monotonic()
    clamp_response = run_prompt(
        "Create a 12 mm wide split clamp for a 40 mm pipe, with a 60 mm outside diameter, a 4 mm radial split, and two 5 mm through mounting holes.",
        service=clamp_service, prefer_online=False, provider=PipeClampProvider(),
    )
    clamp_document.recompute()
    clamp_pockets = [obj for obj in clamp_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"]
    clamp_geometry_passed = (
        not clamp_response.error and len(clamp_pockets) == 1
    )
    clamp_dimensions_passed = False
    clamp_measurements = {}
    if clamp_geometry_passed:
        clamp_shape = clamp_pockets[0].Shape
        def strip_integral(radius, x):
            return 0.5 * (x * math.sqrt(radius * radius - x * x) + radius * radius * math.asin(x / radius))
        split_area = 2 * (
            (strip_integral(30, 2) - strip_integral(30, 0)) -
            (strip_integral(20, 2) - strip_integral(20, 0))
        )
        expected_clamp_volume = (math.pi * (30 * 30 - 20 * 20) - split_area - 2 * math.pi * 2.5 * 2.5) * 12
        expected_split_y_bound = 30 + math.sqrt(30 * 30 - 2 * 2)
        clamp_measurements = {
            "x_length": clamp_shape.BoundBox.XLength, "y_length": clamp_shape.BoundBox.YLength,
            "z_length": clamp_shape.BoundBox.ZLength, "volume": clamp_shape.Volume,
            "expected_volume": expected_clamp_volume,
        }
        clamp_geometry_passed = (
            clamp_shape.isValid() and not clamp_shape.isNull()
        )
        clamp_dimensions_passed = (
            abs(clamp_shape.BoundBox.XLength - 60) < 0.02 and
            abs(clamp_shape.BoundBox.YLength - expected_split_y_bound) < 0.02 and
            abs(clamp_shape.BoundBox.ZLength - 12) < 1e-6 and
            abs(clamp_shape.Volume - expected_clamp_volume) < 1e-3
        )
    clamp_passed = clamp_geometry_passed and clamp_dimensions_passed
    clamp_constraints = _constraint_evidence(clamp_document)
    clamp_types = [
        str(getattr(obj, "TypeId", "")) for obj in clamp_document.Objects
    ]
    clamp_document.save()
    clamp_revisions = clamp_service.revision_timeline()
    App.closeDocument(clamp_document.Name)
    reopened_clamp = App.openDocument(str(clamp_artifact))
    reopened_clamp.recompute()
    clamp_final = next((obj for obj in reopened_clamp.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"), None)
    clamp_reopen_passed = bool(clamp_final and clamp_final.Shape.isValid() and not clamp_final.Shape.isNull())
    clamp_reopened_types = [
        str(getattr(obj, "TypeId", "")) for obj in reopened_clamp.Objects
    ]
    App.closeDocument(reopened_clamp.Name)
    clamp_elapsed = time.monotonic() - clamp_started
    Gui.activateWorkbench("PartDesignWorkbench")
    cover_document = App.newDocument(f"Tier2VentilatedCover{trial_id}")
    cover_artifact = output / f"tier2-ventilated-cover-{trial_id}.FCStd"
    cover_document.saveAs(str(cover_artifact))
    cover_service = VibeCADService()
    cover_started = time.monotonic()
    cover_response = run_prompt(
        "Create an 80 by 50 by 3 mm equipment cover with five horizontal 50 by 3 mm through ventilation slots on 8 mm spacing.",
        service=cover_service, prefer_online=False, provider=VentilatedCoverProvider(),
    )
    cover_document.recompute()
    cover_pockets = [obj for obj in cover_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"]
    cover_geometry_passed = (
        not cover_response.error and len(cover_pockets) == 1
    )
    cover_dimensions_passed = False
    cover_measurements = {}
    if cover_geometry_passed:
        cover_shape = cover_pockets[0].Shape
        vent_area = (50 - 3) * 3 + math.pi * 1.5 * 1.5
        expected_cover_volume = 80 * 50 * 3 - 5 * vent_area * 3
        cover_measurements = {
            "x_length": cover_shape.BoundBox.XLength, "y_length": cover_shape.BoundBox.YLength,
            "z_length": cover_shape.BoundBox.ZLength, "volume": cover_shape.Volume,
            "expected_volume": expected_cover_volume,
        }
        cover_geometry_passed = (
            cover_shape.isValid() and not cover_shape.isNull()
        )
        cover_dimensions_passed = (
            abs(cover_shape.BoundBox.XLength - 80) < 1e-6 and
            abs(cover_shape.BoundBox.YLength - 50) < 1e-6 and
            abs(cover_shape.BoundBox.ZLength - 3) < 1e-6 and
            abs(cover_shape.Volume - expected_cover_volume) < 1e-3
        )
    cover_passed = cover_geometry_passed and cover_dimensions_passed
    cover_constraints = _constraint_evidence(cover_document)
    cover_types = [
        str(getattr(obj, "TypeId", "")) for obj in cover_document.Objects
    ]
    cover_document.save()
    cover_revisions = cover_service.revision_timeline()
    App.closeDocument(cover_document.Name)
    reopened_cover = App.openDocument(str(cover_artifact))
    reopened_cover.recompute()
    cover_final = next((obj for obj in reopened_cover.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"), None)
    cover_reopen_passed = bool(cover_final and cover_final.Shape.isValid() and not cover_final.Shape.isNull())
    cover_reopened_types = [
        str(getattr(obj, "TypeId", "")) for obj in reopened_cover.Objects
    ]
    App.closeDocument(reopened_cover.Name)
    cover_elapsed = time.monotonic() - cover_started
    Gui.activateWorkbench("PartDesignWorkbench")
    enclosure_document = App.newDocument(f"Tier2ElectronicsEnclosure{trial_id}")
    enclosure_artifact = output / f"tier2-electronics-enclosure-{trial_id}.FCStd"
    enclosure_document.saveAs(str(enclosure_artifact))
    enclosure_service = VibeCADService()
    enclosure_started = time.monotonic()
    enclosure_response = run_prompt(
        "Create a 120 by 80 by 35 mm electronics enclosure with 2.5 mm walls and a separate 3 mm removable lid with four 3.2 mm M3 clearance holes.",
        service=enclosure_service, prefer_online=False,
        provider=ElectronicsEnclosureProvider(),
    )
    enclosure_document.recompute()
    enclosure_bodies = [obj for obj in enclosure_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Body"]
    enclosure_shells = [obj for obj in enclosure_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Thickness"]
    enclosure_pockets = [obj for obj in enclosure_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"]
    enclosure_structure_passed = (
        not enclosure_response.error and len(enclosure_bodies) == 2 and
        len(enclosure_shells) == 1 and len(enclosure_pockets) == 1
    )
    enclosure_geometry_passed = False
    enclosure_dimensions_passed = False
    enclosure_measurements = {}
    if enclosure_structure_passed:
        housing_shape = enclosure_shells[0].Shape
        lid_shape = enclosure_pockets[0].Shape
        expected_housing_volume = 120 * 80 * 35 - 115 * 75 * 32.5
        expected_lid_volume = 120 * 80 * 3 - 4 * math.pi * 1.6 * 1.6 * 3
        enclosure_measurements = {
            "housing_bounds": [housing_shape.BoundBox.XLength, housing_shape.BoundBox.YLength, housing_shape.BoundBox.ZLength],
            "housing_volume": housing_shape.Volume,
            "expected_housing_volume": expected_housing_volume,
            "lid_bounds": [lid_shape.BoundBox.XLength, lid_shape.BoundBox.YLength, lid_shape.BoundBox.ZLength],
            "lid_volume": lid_shape.Volume,
            "expected_lid_volume": expected_lid_volume,
        }
        enclosure_geometry_passed = (
            housing_shape.isValid() and not housing_shape.isNull()
            and lid_shape.isValid() and not lid_shape.isNull()
        )
        enclosure_dimensions_passed = (
            abs(housing_shape.BoundBox.XLength - 120) < 1e-6 and
            abs(housing_shape.BoundBox.YLength - 80) < 1e-6 and
            abs(housing_shape.BoundBox.ZLength - 35) < 1e-6 and
            abs(housing_shape.Volume - expected_housing_volume) < 1e-3 and
            abs(lid_shape.BoundBox.XLength - 120) < 1e-6 and
            abs(lid_shape.BoundBox.YLength - 80) < 1e-6 and
            abs(lid_shape.BoundBox.ZLength - 3) < 1e-6 and
            abs(lid_shape.Volume - expected_lid_volume) < 1e-3
        )
    enclosure_passed = (
        enclosure_structure_passed
        and enclosure_geometry_passed
        and enclosure_dimensions_passed
    )
    enclosure_constraints = _constraint_evidence(enclosure_document)
    enclosure_types = [
        str(getattr(obj, "TypeId", ""))
        for obj in enclosure_document.Objects
    ]
    enclosure_document.save()
    enclosure_revisions = enclosure_service.revision_timeline()
    App.closeDocument(enclosure_document.Name)
    reopened_enclosure = App.openDocument(str(enclosure_artifact))
    reopened_enclosure.recompute()
    reopened_bodies = [obj for obj in reopened_enclosure.Objects if getattr(obj, "TypeId", "") == "PartDesign::Body"]
    reopened_shell = next((obj for obj in reopened_enclosure.Objects if getattr(obj, "TypeId", "") == "PartDesign::Thickness"), None)
    reopened_lid = next((obj for obj in reopened_enclosure.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"), None)
    enclosure_reopen_passed = bool(
        len(reopened_bodies) == 2 and reopened_shell and reopened_lid and
        reopened_shell.Shape.isValid() and not reopened_shell.Shape.isNull() and
        reopened_lid.Shape.isValid() and not reopened_lid.Shape.isNull()
    )
    enclosure_reopened_types = [
        str(getattr(obj, "TypeId", "")) for obj in reopened_enclosure.Objects
    ]
    App.closeDocument(reopened_enclosure.Name)
    enclosure_elapsed = time.monotonic() - enclosure_started

    Gui.activateWorkbench("PartDesignWorkbench")
    coupling_document = App.newDocument(f"Tier2FlangedCoupling{trial_id}")
    coupling_artifact = output / f"tier2-flanged-coupling-{trial_id}.FCStd"
    coupling_document.saveAs(str(coupling_artifact))
    coupling_service = VibeCADService()
    coupling_started = time.monotonic()
    coupling_response = run_prompt(
        "Create an 80 mm flanged coupling, 20 mm overall length, with a 40 mm hub, 20 mm bore, and four 6 mm holes on a 60 mm bolt circle.",
        service=coupling_service,
        prefer_online=False,
        provider=FlangedCouplingProvider(),
    )
    coupling_document.recompute()
    coupling_bodies = [
        obj for obj in coupling_document.Objects
        if getattr(obj, "TypeId", "") == "PartDesign::Body"
    ]
    coupling_final = next(
        (
            obj for obj in coupling_document.Objects
            if getattr(obj, "Label", "") == "Coupling Hub Pad"
        ),
        None,
    )
    coupling_geometry_passed = bool(
        not coupling_response.error
        and len(coupling_bodies) == 1
        and coupling_final
        and coupling_final.Shape.isValid()
        and not coupling_final.Shape.isNull()
        and len(coupling_final.Shape.Solids) == 1
    )
    expected_coupling_volume = (
        math.pi * 40 * 40 * 10
        - math.pi * 10 * 10 * 10
        - 4 * math.pi * 3 * 3 * 10
        + math.pi * (20 * 20 - 10 * 10) * 10
    )
    coupling_measurements = {}
    coupling_dimensions_passed = False
    coupling_topology_passed = False
    if coupling_geometry_passed:
        coupling_shape = coupling_final.Shape
        coupling_radii = _cylindrical_radii(coupling_shape)
        coupling_measurements = {
            "x_length": coupling_shape.BoundBox.XLength,
            "y_length": coupling_shape.BoundBox.YLength,
            "z_length": coupling_shape.BoundBox.ZLength,
            "volume": coupling_shape.Volume,
            "expected_volume": expected_coupling_volume,
            "solid_count": len(coupling_shape.Solids),
            "cylindrical_radii": coupling_radii,
        }
        coupling_dimensions_passed = (
            abs(coupling_shape.BoundBox.XLength - 80) < 0.02
            and abs(coupling_shape.BoundBox.YLength - 80) < 0.02
            and abs(coupling_shape.BoundBox.ZLength - 20) < 1e-6
            and abs(coupling_shape.Volume - expected_coupling_volume) < 1e-3
        )
        coupling_topology_passed = (
            coupling_radii.count(40.0) == 1
            and coupling_radii.count(20.0) == 1
            and coupling_radii.count(10.0) == 1
            and coupling_radii.count(3.0) == 4
        )
    coupling_constraints = _constraint_evidence(coupling_document)
    coupling_types = [
        str(getattr(obj, "TypeId", "")) for obj in coupling_document.Objects
    ]
    coupling_document.save()
    coupling_revisions = coupling_service.revision_timeline()
    App.closeDocument(coupling_document.Name)
    reopened_coupling = App.openDocument(str(coupling_artifact))
    reopened_coupling.recompute()
    reopened_coupling_final = next(
        (
            obj for obj in reopened_coupling.Objects
            if getattr(obj, "Label", "") == "Coupling Hub Pad"
        ),
        None,
    )
    reopened_coupling_radii = (
        _cylindrical_radii(reopened_coupling_final.Shape)
        if reopened_coupling_final
        else []
    )
    coupling_reopen_passed = bool(
        reopened_coupling_final
        and reopened_coupling_final.Shape.isValid()
        and not reopened_coupling_final.Shape.isNull()
        and len(reopened_coupling_final.Shape.Solids) == 1
        and abs(reopened_coupling_final.Shape.BoundBox.XLength - 80) < 0.02
        and abs(reopened_coupling_final.Shape.BoundBox.YLength - 80) < 0.02
        and abs(reopened_coupling_final.Shape.BoundBox.ZLength - 20) < 1e-6
        and abs(
            reopened_coupling_final.Shape.Volume - expected_coupling_volume
        ) < 1e-3
        and reopened_coupling_radii.count(40.0) == 1
        and reopened_coupling_radii.count(20.0) == 1
        and reopened_coupling_radii.count(10.0) == 1
        and reopened_coupling_radii.count(3.0) == 4
    )
    coupling_reopen_measurements = {
        "bounds": (
            [
                reopened_coupling_final.Shape.BoundBox.XLength,
                reopened_coupling_final.Shape.BoundBox.YLength,
                reopened_coupling_final.Shape.BoundBox.ZLength,
            ]
            if reopened_coupling_final
            else []
        ),
        "volume": (
            reopened_coupling_final.Shape.Volume
            if reopened_coupling_final
            else None
        ),
        "cylindrical_radii": reopened_coupling_radii,
    }
    coupling_reopened_types = [
        str(getattr(obj, "TypeId", "")) for obj in reopened_coupling.Objects
    ]
    App.closeDocument(reopened_coupling.Name)
    coupling_elapsed = time.monotonic() - coupling_started

    Gui.activateWorkbench("PartDesignWorkbench")
    hinge_document = App.newDocument(f"Tier2SimpleHinge{trial_id}")
    hinge_artifact = output / f"tier2-simple-hinge-{trial_id}.FCStd"
    hinge_document.saveAs(str(hinge_artifact))
    hinge_service = VibeCADService()
    hinge_started = time.monotonic()
    hinge_response = run_prompt(
        "Create a simple flat hinge layout with two 40 by 30 by 3 mm leaves, an 8 mm barrel with a 6 mm bore, a separate 5 mm pin, and four 4 mm mounting holes.",
        service=hinge_service,
        prefer_online=False,
        provider=SimpleHingeProvider(),
    )
    hinge_document.recompute()
    hinge_bodies = [
        obj for obj in hinge_document.Objects
        if getattr(obj, "TypeId", "") == "PartDesign::Body"
    ]
    left_leaf = next(
        (
            obj for obj in hinge_document.Objects
            if getattr(obj, "Label", "") == "Left Hinge Leaf Through Mounts"
        ),
        None,
    )
    right_leaf = next(
        (
            obj for obj in hinge_document.Objects
            if getattr(obj, "Label", "") == "Right Hinge Leaf Through Mounts"
        ),
        None,
    )
    hinge_barrel = next(
        (
            obj for obj in hinge_document.Objects
            if getattr(obj, "Label", "") == "Hinge Barrel Sleeve"
        ),
        None,
    )
    hinge_pin = next(
        (
            obj for obj in hinge_document.Objects
            if getattr(obj, "Label", "") == "Hinge Pin Shaft"
        ),
        None,
    )
    hinge_features = [left_leaf, right_leaf, hinge_barrel, hinge_pin]
    hinge_geometry_passed = bool(
        not hinge_response.error
        and len(hinge_bodies) == 4
        and all(
            feature
            and feature.Shape.isValid()
            and not feature.Shape.isNull()
            and len(feature.Shape.Solids) == 1
            for feature in hinge_features
        )
    )
    expected_leaf_volume = 40 * 30 * 3 - 2 * math.pi * 2 * 2 * 3
    expected_barrel_volume = math.pi * (4 * 4 - 3 * 3) * 3
    expected_pin_volume = math.pi * 2.5 * 2.5 * 5
    expected_hinge_volume = (
        2 * expected_leaf_volume
        + expected_barrel_volume
        + expected_pin_volume
    )
    hinge_measurements = {}
    hinge_dimensions_passed = False
    hinge_topology_passed = False
    if hinge_geometry_passed:
        leaf_shapes = [left_leaf.Shape, right_leaf.Shape]
        total_hinge_volume = sum(
            feature.Shape.Volume for feature in hinge_features
        )
        hinge_measurements = {
            "leaf_bounds": [
                [
                    shape.BoundBox.XLength,
                    shape.BoundBox.YLength,
                    shape.BoundBox.ZLength,
                ]
                for shape in leaf_shapes
            ],
            "leaf_volumes": [shape.Volume for shape in leaf_shapes],
            "expected_leaf_volume": expected_leaf_volume,
            "barrel_bounds": [
                hinge_barrel.Shape.BoundBox.XLength,
                hinge_barrel.Shape.BoundBox.YLength,
                hinge_barrel.Shape.BoundBox.ZLength,
            ],
            "barrel_volume": hinge_barrel.Shape.Volume,
            "expected_barrel_volume": expected_barrel_volume,
            "pin_bounds": [
                hinge_pin.Shape.BoundBox.XLength,
                hinge_pin.Shape.BoundBox.YLength,
                hinge_pin.Shape.BoundBox.ZLength,
            ],
            "pin_volume": hinge_pin.Shape.Volume,
            "expected_pin_volume": expected_pin_volume,
            "total_volume": total_hinge_volume,
            "expected_total_volume": expected_hinge_volume,
            "body_count": len(hinge_bodies),
            "solid_count": sum(
                len(feature.Shape.Solids) for feature in hinge_features
            ),
        }
        hinge_dimensions_passed = (
            all(
                abs(shape.BoundBox.XLength - 40) < 1e-6
                and abs(shape.BoundBox.YLength - 30) < 1e-6
                and abs(shape.BoundBox.ZLength - 3) < 1e-6
                and abs(shape.Volume - expected_leaf_volume) < 1e-3
                for shape in leaf_shapes
            )
            and abs(hinge_barrel.Shape.BoundBox.XLength - 8) < 0.02
            and abs(hinge_barrel.Shape.BoundBox.YLength - 8) < 0.02
            and abs(hinge_barrel.Shape.BoundBox.ZLength - 3) < 1e-6
            and abs(
                hinge_barrel.Shape.Volume - expected_barrel_volume
            ) < 1e-3
            and abs(hinge_pin.Shape.BoundBox.XLength - 5) < 0.02
            and abs(hinge_pin.Shape.BoundBox.YLength - 5) < 0.02
            and abs(hinge_pin.Shape.BoundBox.ZLength - 5) < 1e-6
            and abs(hinge_pin.Shape.Volume - expected_pin_volume) < 1e-3
            and abs(total_hinge_volume - expected_hinge_volume) < 1e-3
        )
        hinge_topology_passed = (
            len(hinge_bodies) == 4
            and sum(
                len(feature.Shape.Solids) for feature in hinge_features
            ) == 4
        )
    hinge_constraints = _constraint_evidence(hinge_document)
    hinge_types = [
        str(getattr(obj, "TypeId", "")) for obj in hinge_document.Objects
    ]
    hinge_document.save()
    hinge_revisions = hinge_service.revision_timeline()
    App.closeDocument(hinge_document.Name)
    reopened_hinge = App.openDocument(str(hinge_artifact))
    reopened_hinge.recompute()
    reopened_hinge_bodies = [
        obj for obj in reopened_hinge.Objects
        if getattr(obj, "TypeId", "") == "PartDesign::Body"
    ]
    reopened_hinge_features = [
        next(
            (
                obj for obj in reopened_hinge.Objects
                if getattr(obj, "Label", "") == label
            ),
            None,
        )
        for label in (
            "Left Hinge Leaf Through Mounts",
            "Right Hinge Leaf Through Mounts",
            "Hinge Barrel Sleeve",
            "Hinge Pin Shaft",
        )
    ]
    hinge_reopen_passed = bool(
        len(reopened_hinge_bodies) == 4
        and all(
            feature
            and feature.Shape.isValid()
            and not feature.Shape.isNull()
            and len(feature.Shape.Solids) == 1
            for feature in reopened_hinge_features
        )
        and all(
            abs(feature.Shape.BoundBox.XLength - 40) < 1e-6
            and abs(feature.Shape.BoundBox.YLength - 30) < 1e-6
            and abs(feature.Shape.BoundBox.ZLength - 3) < 1e-6
            and abs(feature.Shape.Volume - expected_leaf_volume) < 1e-3
            for feature in reopened_hinge_features[:2]
        )
        and abs(
            reopened_hinge_features[2].Shape.BoundBox.XLength - 8
        ) < 0.02
        and abs(
            reopened_hinge_features[2].Shape.BoundBox.YLength - 8
        ) < 0.02
        and abs(
            reopened_hinge_features[2].Shape.BoundBox.ZLength - 3
        ) < 1e-6
        and abs(
            reopened_hinge_features[2].Shape.Volume - expected_barrel_volume
        ) < 1e-3
        and abs(
            reopened_hinge_features[3].Shape.BoundBox.XLength - 5
        ) < 0.02
        and abs(
            reopened_hinge_features[3].Shape.BoundBox.YLength - 5
        ) < 0.02
        and abs(
            reopened_hinge_features[3].Shape.BoundBox.ZLength - 5
        ) < 1e-6
        and abs(
            reopened_hinge_features[3].Shape.Volume - expected_pin_volume
        ) < 1e-3
        and abs(
            sum(feature.Shape.Volume for feature in reopened_hinge_features)
            - expected_hinge_volume
        ) < 1e-3
    )
    hinge_reopen_measurements = {
        "body_count": len(reopened_hinge_bodies),
        "feature_bounds": [
            [
                feature.Shape.BoundBox.XLength,
                feature.Shape.BoundBox.YLength,
                feature.Shape.BoundBox.ZLength,
            ]
            if feature
            else []
            for feature in reopened_hinge_features
        ],
        "feature_volumes": [
            feature.Shape.Volume if feature else None
            for feature in reopened_hinge_features
        ],
    }
    hinge_reopened_types = [
        str(getattr(obj, "TypeId", "")) for obj in reopened_hinge.Objects
    ]
    App.closeDocument(reopened_hinge.Name)
    hinge_elapsed = time.monotonic() - hinge_started

    Gui.activateWorkbench("PartDesignWorkbench")
    bolt_document = App.newDocument(f"Tier2BoltPatternPlate{trial_id}")
    bolt_artifact = output / f"tier2-bolt-pattern-plate-{trial_id}.FCStd"
    bolt_document.saveAs(str(bolt_artifact))
    bolt_service = VibeCADService()
    bolt_started = time.monotonic()
    bolt_response = run_prompt(
        "Create a 100 by 80 by 8 mm plate with a 20 mm center bore and eight 6 mm holes on a 60 mm bolt circle.",
        service=bolt_service,
        prefer_online=False,
        provider=BoltPatternPlateProvider(),
    )
    bolt_document.recompute()
    bolt_bodies = [
        obj for obj in bolt_document.Objects
        if getattr(obj, "TypeId", "") == "PartDesign::Body"
    ]
    bolt_final = next(
        (
            obj for obj in bolt_document.Objects
            if getattr(obj, "Label", "") == "Through Bore and Bolt Pattern"
        ),
        None,
    )
    bolt_geometry_passed = bool(
        not bolt_response.error
        and len(bolt_bodies) == 1
        and bolt_final
        and bolt_final.Shape.isValid()
        and not bolt_final.Shape.isNull()
        and len(bolt_final.Shape.Solids) == 1
    )
    expected_bolt_volume = (
        100 * 80 * 8
        - math.pi * 10 * 10 * 8
        - 8 * math.pi * 3 * 3 * 8
    )
    bolt_measurements = {}
    bolt_dimensions_passed = False
    bolt_topology_passed = False
    if bolt_geometry_passed:
        bolt_shape = bolt_final.Shape
        bolt_radii = _cylindrical_radii(bolt_shape)
        bolt_measurements = {
            "x_length": bolt_shape.BoundBox.XLength,
            "y_length": bolt_shape.BoundBox.YLength,
            "z_length": bolt_shape.BoundBox.ZLength,
            "volume": bolt_shape.Volume,
            "expected_volume": expected_bolt_volume,
            "solid_count": len(bolt_shape.Solids),
            "cylindrical_radii": bolt_radii,
        }
        bolt_dimensions_passed = (
            abs(bolt_shape.BoundBox.XLength - 100) < 1e-6
            and abs(bolt_shape.BoundBox.YLength - 80) < 1e-6
            and abs(bolt_shape.BoundBox.ZLength - 8) < 1e-6
            and abs(bolt_shape.Volume - expected_bolt_volume) < 1e-3
        )
        bolt_topology_passed = (
            bolt_radii.count(10.0) == 1
            and bolt_radii.count(3.0) == 8
        )
    bolt_constraints = _constraint_evidence(bolt_document)
    bolt_types = [
        str(getattr(obj, "TypeId", "")) for obj in bolt_document.Objects
    ]
    bolt_document.save()
    bolt_revisions = bolt_service.revision_timeline()
    App.closeDocument(bolt_document.Name)
    reopened_bolt = App.openDocument(str(bolt_artifact))
    reopened_bolt.recompute()
    reopened_bolt_final = next(
        (
            obj for obj in reopened_bolt.Objects
            if getattr(obj, "Label", "") == "Through Bore and Bolt Pattern"
        ),
        None,
    )
    reopened_bolt_radii = (
        _cylindrical_radii(reopened_bolt_final.Shape)
        if reopened_bolt_final
        else []
    )
    bolt_reopen_passed = bool(
        reopened_bolt_final
        and reopened_bolt_final.Shape.isValid()
        and not reopened_bolt_final.Shape.isNull()
        and len(reopened_bolt_final.Shape.Solids) == 1
        and abs(reopened_bolt_final.Shape.BoundBox.XLength - 100) < 1e-6
        and abs(reopened_bolt_final.Shape.BoundBox.YLength - 80) < 1e-6
        and abs(reopened_bolt_final.Shape.BoundBox.ZLength - 8) < 1e-6
        and abs(reopened_bolt_final.Shape.Volume - expected_bolt_volume) < 1e-3
        and reopened_bolt_radii.count(10.0) == 1
        and reopened_bolt_radii.count(3.0) == 8
    )
    bolt_reopen_measurements = {
        "bounds": (
            [
                reopened_bolt_final.Shape.BoundBox.XLength,
                reopened_bolt_final.Shape.BoundBox.YLength,
                reopened_bolt_final.Shape.BoundBox.ZLength,
            ]
            if reopened_bolt_final
            else []
        ),
        "volume": (
            reopened_bolt_final.Shape.Volume
            if reopened_bolt_final
            else None
        ),
        "cylindrical_radii": reopened_bolt_radii,
    }
    bolt_reopened_types = [
        str(getattr(obj, "TypeId", "")) for obj in reopened_bolt.Objects
    ]
    App.closeDocument(reopened_bolt.Name)
    bolt_elapsed = time.monotonic() - bolt_started

    bracket_stages = _case_stages(
        geometry_passed=bracket_geometry_passed,
        geometry_evidence={
            "shape_valid": bracket_geometry_passed,
            "feature": "PartDesign::Pocket",
        },
        dimensions_passed=bracket_dimensions_passed,
        dimensions_evidence=bracket_measurements,
        constraints=bracket_constraints,
        editability_passed=(
            "Sketcher::SketchObject" in bracket_types
            and "PartDesign::Pad" in bracket_types
            and "PartDesign::Pocket" in bracket_types
            and len(revisions) == 1
        ),
        editability_evidence={
            "required_types": [
                "Sketcher::SketchObject",
                "PartDesign::Pad",
                "PartDesign::Pocket",
            ],
            "revision_count": len(revisions),
        },
        reopen_passed=reopen_passed,
        reopen_evidence={"reopened_types": bracket_reopened_types},
    )
    adapter_stages = _case_stages(
        geometry_passed=adapter_geometry_passed,
        geometry_evidence={
            "shape_valid": adapter_geometry_passed,
            "feature": "PartDesign::Pocket",
        },
        dimensions_passed=adapter_dimensions_passed,
        dimensions_evidence=adapter_measurements,
        constraints=adapter_constraints,
        editability_passed=(
            "Sketcher::SketchObject" in adapter_types
            and "PartDesign::Pad" in adapter_types
            and "PartDesign::Pocket" in adapter_types
            and len(adapter_revisions) == 1
        ),
        editability_evidence={
            "required_types": [
                "Sketcher::SketchObject",
                "PartDesign::Pad",
                "PartDesign::Pocket",
            ],
            "revision_count": len(adapter_revisions),
        },
        reopen_passed=adapter_reopen_passed,
        reopen_evidence={"reopened_types": adapter_reopened_types},
    )
    tray_stages = _case_stages(
        geometry_passed=tray_geometry_passed,
        geometry_evidence={
            "shape_valid": tray_geometry_passed,
            "feature": "PartDesign::Pocket",
        },
        dimensions_passed=tray_dimensions_passed,
        dimensions_evidence=tray_measurements,
        constraints=tray_constraints,
        editability_passed=(
            "PartDesign::Thickness" in tray_types
            and "PartDesign::Pocket" in tray_types
            and len(tray_revisions) == 1
        ),
        editability_evidence={
            "required_types": [
                "PartDesign::Thickness",
                "PartDesign::Pocket",
            ],
            "revision_count": len(tray_revisions),
        },
        reopen_passed=tray_reopen_passed,
        reopen_evidence={"reopened_types": tray_reopened_types},
    )
    camera_stages = _case_stages(
        geometry_passed=camera_geometry_passed,
        geometry_evidence={
            "shape_valid": camera_geometry_passed,
            "feature": "PartDesign::Pocket",
        },
        dimensions_passed=camera_dimensions_passed,
        dimensions_evidence=camera_measurements,
        constraints=camera_constraints,
        editability_passed=(
            "Sketcher::SketchObject" in camera_types
            and "PartDesign::Pocket" in camera_types
            and len(camera_revisions) == 1
        ),
        editability_evidence={
            "required_types": [
                "Sketcher::SketchObject",
                "PartDesign::Pocket",
            ],
            "revision_count": len(camera_revisions),
        },
        reopen_passed=camera_reopen_passed,
        reopen_evidence={"reopened_types": camera_reopened_types},
    )
    clamp_stages = _case_stages(
        geometry_passed=clamp_geometry_passed,
        geometry_evidence={
            "shape_valid": clamp_geometry_passed,
            "feature": "PartDesign::Pocket",
        },
        dimensions_passed=clamp_dimensions_passed,
        dimensions_evidence=clamp_measurements,
        constraints=clamp_constraints,
        editability_passed=(
            "Sketcher::SketchObject" in clamp_types
            and "PartDesign::Pocket" in clamp_types
            and len(clamp_revisions) == 1
        ),
        editability_evidence={
            "required_types": [
                "Sketcher::SketchObject",
                "PartDesign::Pocket",
            ],
            "revision_count": len(clamp_revisions),
        },
        reopen_passed=clamp_reopen_passed,
        reopen_evidence={"reopened_types": clamp_reopened_types},
    )
    cover_stages = _case_stages(
        geometry_passed=cover_geometry_passed,
        geometry_evidence={
            "shape_valid": cover_geometry_passed,
            "feature": "PartDesign::Pocket",
        },
        dimensions_passed=cover_dimensions_passed,
        dimensions_evidence=cover_measurements,
        constraints=cover_constraints,
        editability_passed=(
            "Sketcher::SketchObject" in cover_types
            and "PartDesign::Pocket" in cover_types
            and len(cover_revisions) == 1
        ),
        editability_evidence={
            "required_types": [
                "Sketcher::SketchObject",
                "PartDesign::Pocket",
            ],
            "revision_count": len(cover_revisions),
        },
        reopen_passed=cover_reopen_passed,
        reopen_evidence={"reopened_types": cover_reopened_types},
    )
    enclosure_stages = _case_stages(
        geometry_passed=enclosure_geometry_passed,
        geometry_evidence={
            "shape_valid": enclosure_geometry_passed,
            "body_count": len(enclosure_bodies),
        },
        dimensions_passed=enclosure_dimensions_passed,
        dimensions_evidence=enclosure_measurements,
        constraints=enclosure_constraints,
        editability_passed=(
            enclosure_structure_passed
            and enclosure_types.count("PartDesign::Body") == 2
            and "PartDesign::Thickness" in enclosure_types
            and "PartDesign::Pocket" in enclosure_types
            and len(enclosure_revisions) == 1
        ),
        editability_evidence={
            "body_count": enclosure_types.count("PartDesign::Body"),
            "required_types": [
                "PartDesign::Thickness",
                "PartDesign::Pocket",
            ],
            "revision_count": len(enclosure_revisions),
        },
        reopen_passed=enclosure_reopen_passed,
        reopen_evidence={"reopened_types": enclosure_reopened_types},
    )
    coupling_stages = _case_stages(
        geometry_passed=(
            coupling_geometry_passed and coupling_topology_passed
        ),
        geometry_evidence={
            "shape_valid": coupling_geometry_passed,
            "topology_passed": coupling_topology_passed,
            "solid_count": coupling_measurements.get("solid_count"),
            "cylindrical_radii": coupling_measurements.get(
                "cylindrical_radii", []
            ),
        },
        dimensions_passed=coupling_dimensions_passed,
        dimensions_evidence=coupling_measurements,
        constraints=coupling_constraints,
        editability_passed=(
            coupling_types.count("PartDesign::Body") == 1
            and coupling_types.count("PartDesign::Pad") == 2
            and coupling_types.count("PartDesign::Pocket") == 1
            and len(coupling_revisions) == 1
        ),
        editability_evidence={
            "body_count": coupling_types.count("PartDesign::Body"),
            "pad_count": coupling_types.count("PartDesign::Pad"),
            "pocket_count": coupling_types.count("PartDesign::Pocket"),
            "revision_count": len(coupling_revisions),
        },
        reopen_passed=coupling_reopen_passed,
        reopen_evidence={
            "reopened_types": coupling_reopened_types,
            **coupling_reopen_measurements,
        },
    )
    hinge_stages = _case_stages(
        geometry_passed=hinge_geometry_passed and hinge_topology_passed,
        geometry_evidence={
            "shape_valid": hinge_geometry_passed,
            "topology_passed": hinge_topology_passed,
            "body_count": hinge_measurements.get("body_count"),
            "solid_count": hinge_measurements.get("solid_count"),
        },
        dimensions_passed=hinge_dimensions_passed,
        dimensions_evidence=hinge_measurements,
        constraints=hinge_constraints,
        editability_passed=(
            hinge_types.count("PartDesign::Body") == 4
            and hinge_types.count("PartDesign::Pocket") == 2
            and hinge_types.count("PartDesign::Pad") == 4
            and len(hinge_revisions) == 1
        ),
        editability_evidence={
            "body_count": hinge_types.count("PartDesign::Body"),
            "pad_count": hinge_types.count("PartDesign::Pad"),
            "pocket_count": hinge_types.count("PartDesign::Pocket"),
            "revision_count": len(hinge_revisions),
        },
        reopen_passed=hinge_reopen_passed,
        reopen_evidence={
            "reopened_types": hinge_reopened_types,
            **hinge_reopen_measurements,
        },
    )
    bolt_stages = _case_stages(
        geometry_passed=bolt_geometry_passed and bolt_topology_passed,
        geometry_evidence={
            "shape_valid": bolt_geometry_passed,
            "topology_passed": bolt_topology_passed,
            "solid_count": bolt_measurements.get("solid_count"),
            "cylindrical_radii": bolt_measurements.get(
                "cylindrical_radii", []
            ),
        },
        dimensions_passed=bolt_dimensions_passed,
        dimensions_evidence=bolt_measurements,
        constraints=bolt_constraints,
        editability_passed=(
            bolt_types.count("PartDesign::Body") == 1
            and bolt_types.count("PartDesign::Pad") == 1
            and bolt_types.count("PartDesign::Pocket") == 1
            and len(bolt_revisions) == 1
        ),
        editability_evidence={
            "body_count": bolt_types.count("PartDesign::Body"),
            "pad_count": bolt_types.count("PartDesign::Pad"),
            "pocket_count": bolt_types.count("PartDesign::Pocket"),
            "revision_count": len(bolt_revisions),
        },
        reopen_passed=bolt_reopen_passed,
        reopen_evidence={
            "reopened_types": bolt_reopened_types,
            **bolt_reopen_measurements,
        },
    )
    case_attempts = [
        _case_attempt(
            "t2_wall_bracket",
            attempt,
            bracket_stages,
            bracket_elapsed,
            artifact,
        ),
        _case_attempt(
            "t2_motor_adapter",
            attempt,
            adapter_stages,
            adapter_elapsed,
            adapter_artifact,
        ),
        _case_attempt(
            "t2_battery_tray",
            attempt,
            tray_stages,
            tray_elapsed,
            tray_artifact,
        ),
        _case_attempt(
            "t2_camera_mount",
            attempt,
            camera_stages,
            camera_elapsed,
            camera_artifact,
        ),
        _case_attempt(
            "t2_pipe_clamp",
            attempt,
            clamp_stages,
            clamp_elapsed,
            clamp_artifact,
        ),
        _case_attempt(
            "t2_ventilated_cover",
            attempt,
            cover_stages,
            cover_elapsed,
            cover_artifact,
        ),
        _case_attempt(
            "t2_electronics_enclosure_and_lid",
            attempt,
            enclosure_stages,
            enclosure_elapsed,
            enclosure_artifact,
        ),
        _case_attempt(
            "t2_flanged_coupling",
            attempt,
            coupling_stages,
            coupling_elapsed,
            coupling_artifact,
        ),
        _case_attempt(
            "t2_simple_hinge",
            attempt,
            hinge_stages,
            hinge_elapsed,
            hinge_artifact,
        ),
        _case_attempt(
            "t2_bolt_pattern_plate",
            attempt,
            bolt_stages,
            bolt_elapsed,
            bolt_artifact,
        ),
    ]
    attempts_by_case = {item["case_id"]: item for item in case_attempts}
    bracket_case = {
        "case": "wall-bracket",
        "passed": attempts_by_case["t2_wall_bracket"]["passed"],
        "geometry_passed": bool(passed), "reopen_passed": reopen_passed,
        "revision_count": len(revisions), "tool_count": len(response.tool_trace),
        "artifact": str(artifact), "error": response.error,
    }
    adapter_case = {
        "case": "motor-adapter",
        "passed": attempts_by_case["t2_motor_adapter"]["passed"],
        "geometry_passed": bool(adapter_passed), "reopen_passed": adapter_reopen_passed,
        "measurements": adapter_measurements,
        "revision_count": len(adapter_revisions), "tool_count": len(adapter_response.tool_trace),
        "artifact": str(adapter_artifact), "error": adapter_response.error,
    }
    tray_case = {
        "case": "battery-tray",
        "passed": attempts_by_case["t2_battery_tray"]["passed"],
        "geometry_passed": bool(tray_passed), "reopen_passed": tray_reopen_passed,
        "revision_count": len(tray_revisions), "tool_count": len(tray_response.tool_trace),
        "measurements": tray_measurements, "artifact": str(tray_artifact), "error": tray_response.error,
    }
    camera_case = {
        "case": "camera-mount",
        "passed": attempts_by_case["t2_camera_mount"]["passed"],
        "geometry_passed": bool(camera_passed), "reopen_passed": camera_reopen_passed,
        "revision_count": len(camera_revisions), "tool_count": len(camera_response.tool_trace),
        "measurements": camera_measurements, "artifact": str(camera_artifact), "error": camera_response.error,
    }
    clamp_case = {
        "case": "pipe-clamp",
        "passed": attempts_by_case["t2_pipe_clamp"]["passed"],
        "geometry_passed": bool(clamp_passed), "reopen_passed": clamp_reopen_passed,
        "revision_count": len(clamp_revisions), "tool_count": len(clamp_response.tool_trace),
        "measurements": clamp_measurements, "artifact": str(clamp_artifact), "error": clamp_response.error,
    }
    cover_case = {
        "case": "ventilated-cover",
        "passed": attempts_by_case["t2_ventilated_cover"]["passed"],
        "geometry_passed": bool(cover_passed), "reopen_passed": cover_reopen_passed,
        "revision_count": len(cover_revisions), "tool_count": len(cover_response.tool_trace),
        "measurements": cover_measurements, "artifact": str(cover_artifact), "error": cover_response.error,
    }
    enclosure_case = {
        "case": "electronics-enclosure-and-lid",
        "passed": attempts_by_case[
            "t2_electronics_enclosure_and_lid"
        ]["passed"],
        "geometry_passed": bool(enclosure_passed), "reopen_passed": enclosure_reopen_passed,
        "revision_count": len(enclosure_revisions), "tool_count": len(enclosure_response.tool_trace),
        "measurements": enclosure_measurements, "artifact": str(enclosure_artifact),
        "error": enclosure_response.error,
    }
    coupling_case = {
        "case": "flanged-coupling",
        "passed": attempts_by_case["t2_flanged_coupling"]["passed"],
        "geometry_passed": bool(
            coupling_geometry_passed and coupling_topology_passed
        ),
        "reopen_passed": coupling_reopen_passed,
        "revision_count": len(coupling_revisions),
        "tool_count": len(coupling_response.tool_trace),
        "measurements": coupling_measurements,
        "artifact": str(coupling_artifact),
        "error": coupling_response.error,
    }
    hinge_case = {
        "case": "simple-hinge",
        "passed": attempts_by_case["t2_simple_hinge"]["passed"],
        "geometry_passed": bool(
            hinge_geometry_passed and hinge_topology_passed
        ),
        "reopen_passed": hinge_reopen_passed,
        "revision_count": len(hinge_revisions),
        "tool_count": len(hinge_response.tool_trace),
        "measurements": hinge_measurements,
        "artifact": str(hinge_artifact),
        "error": hinge_response.error,
    }
    bolt_case = {
        "case": "bolt-pattern-plate",
        "passed": attempts_by_case["t2_bolt_pattern_plate"]["passed"],
        "geometry_passed": bool(
            bolt_geometry_passed and bolt_topology_passed
        ),
        "reopen_passed": bolt_reopen_passed,
        "revision_count": len(bolt_revisions),
        "tool_count": len(bolt_response.tool_trace),
        "measurements": bolt_measurements,
        "artifact": str(bolt_artifact),
        "error": bolt_response.error,
    }
    report = {
        "schema": "vibecad-tier2-provider-result-v1", "version": 1,
        "case_count": 10, "executor": EXECUTOR,
        "live_model_score": False,
        "passed": all(item["passed"] for item in case_attempts),
        "case_attempts": case_attempts,
        "cases": [
            bracket_case,
            adapter_case,
            tray_case,
            camera_case,
            clamp_case,
            cover_case,
            enclosure_case,
            coupling_case,
            hinge_case,
            bolt_case,
        ],
        "revision_count": (
            len(revisions) + len(adapter_revisions) + len(tray_revisions) +
            len(camera_revisions) + len(clamp_revisions) + len(cover_revisions) +
            len(enclosure_revisions) + len(coupling_revisions) +
            len(hinge_revisions) + len(bolt_revisions)
        ),
        "tool_count": (
            len(response.tool_trace) + len(adapter_response.tool_trace) +
            len(tray_response.tool_trace) + len(camera_response.tool_trace) +
            len(clamp_response.tool_trace) + len(cover_response.tool_trace) +
            len(enclosure_response.tool_trace) +
            len(coupling_response.tool_trace) + len(hinge_response.tool_trace) +
            len(bolt_response.tool_trace)
        ),
        "elapsed_seconds": time.monotonic() - started,
    }
    (output / "tier2-provider-results.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
