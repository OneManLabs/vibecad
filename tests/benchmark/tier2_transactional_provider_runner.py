# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tier 2 functional wall-bracket workflow through provider tools."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import time
import uuid

import FreeCAD as App
import FreeCADGui as Gui

from VibeCADCore import VibeCADService
from VibeCADProvider import BaseProvider, ProviderResult
from VibeCADSession import run_prompt


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


def main() -> int:
    output = Path(sys.argv[-1])
    output.mkdir(parents=True, exist_ok=True)
    Gui.activateWorkbench("PartDesignWorkbench")
    trial_id = uuid.uuid4().hex[:12]
    document = App.newDocument(f"Tier2WallBracket{trial_id}")
    artifact = output / f"tier2-wall-bracket-{trial_id}.FCStd"
    document.saveAs(str(artifact))
    service = VibeCADService()
    started = time.monotonic()
    response = run_prompt(
        "Create a 60 by 40 mm right-angle wall bracket, 30 mm wide with 8 mm legs, and add two 4 mm through mounting holes.",
        service=service, prefer_online=False, provider=WallBracketProvider(),
    )
    document.recompute()
    pockets = [obj for obj in document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"]
    passed = not response.error and len(pockets) == 1
    if passed:
        shape = pockets[0].Shape
        expected_volume = (60 * 8 + 8 * (40 - 8)) * 30 - 2 * math.pi * 2 * 2 * 30
        passed = (
            shape.isValid() and not shape.isNull() and
            abs(shape.BoundBox.XLength - 60) < 1e-6 and
            abs(shape.BoundBox.YLength - 40) < 1e-6 and
            abs(shape.BoundBox.ZLength - 30) < 1e-6 and
            abs(shape.Volume - expected_volume) < 1e-3
        )
    document.save()
    revisions = service.revision_timeline()
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(artifact))
    reopened.recompute()
    final_feature = next((obj for obj in reopened.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"), None)
    reopen_passed = bool(final_feature and final_feature.Shape.isValid() and not final_feature.Shape.isNull())
    App.closeDocument(reopened.Name)
    adapter_document = App.newDocument(f"Tier2MotorAdapter{trial_id}")
    adapter_artifact = output / f"tier2-motor-adapter-{trial_id}.FCStd"
    adapter_document.saveAs(str(adapter_artifact))
    adapter_service = VibeCADService()
    adapter_response = run_prompt(
        "Create an 80 mm round motor adapter, 8 mm thick, with a 20 mm shaft bore and four 5 mm holes on a 60 mm bolt circle.",
        service=adapter_service, prefer_online=False, provider=MotorAdapterProvider(),
    )
    adapter_document.recompute()
    adapter_pockets = [obj for obj in adapter_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"]
    adapter_passed = not adapter_response.error and len(adapter_pockets) == 1
    adapter_measurements = {}
    if adapter_passed:
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
        adapter_passed = (
            adapter_shape.isValid() and not adapter_shape.isNull() and
            # The live Sketcher-derived circle can have a transient OCC bound
            # up to 0.016 mm below its exact diameter before save/reopen.
            abs(adapter_shape.BoundBox.XLength - 80) < 0.02 and
            abs(adapter_shape.BoundBox.YLength - 80) < 0.02 and
            abs(adapter_shape.BoundBox.ZLength - 8) < 1e-6 and
            abs(adapter_shape.Volume - expected_adapter_volume) < 1e-3
        )
    adapter_document.save()
    adapter_revisions = adapter_service.revision_timeline()
    App.closeDocument(adapter_document.Name)
    reopened_adapter = App.openDocument(str(adapter_artifact))
    reopened_adapter.recompute()
    adapter_final = next((obj for obj in reopened_adapter.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"), None)
    adapter_reopen_passed = bool(adapter_final and adapter_final.Shape.isValid() and not adapter_final.Shape.isNull())
    App.closeDocument(reopened_adapter.Name)
    tray_document = App.newDocument(f"Tier2BatteryTray{trial_id}")
    tray_artifact = output / f"tier2-battery-tray-{trial_id}.FCStd"
    tray_document.saveAs(str(tray_artifact))
    tray_service = VibeCADService()
    tray_response = run_prompt(
        "Create a 100 by 60 by 20 mm battery tray with 2.5 mm walls and four 4 mm bottom mounting holes on a 70 by 30 mm pattern.",
        service=tray_service, prefer_online=False, provider=BatteryTrayProvider(),
    )
    tray_document.recompute()
    tray_pockets = [obj for obj in tray_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"]
    tray_passed = not tray_response.error and len(tray_pockets) == 1
    tray_measurements = {}
    if tray_passed:
        tray_shape = tray_pockets[0].Shape
        expected_tray_volume = 100 * 60 * 20 - 95 * 55 * 17.5 - 4 * math.pi * 2 * 2 * 2.5
        tray_measurements = {
            "x_length": tray_shape.BoundBox.XLength, "y_length": tray_shape.BoundBox.YLength,
            "z_length": tray_shape.BoundBox.ZLength, "volume": tray_shape.Volume,
            "expected_volume": expected_tray_volume,
        }
        tray_passed = (
            tray_shape.isValid() and not tray_shape.isNull() and
            abs(tray_shape.BoundBox.XLength - 100) < 1e-6 and
            abs(tray_shape.BoundBox.YLength - 60) < 1e-6 and
            abs(tray_shape.BoundBox.ZLength - 20) < 1e-6 and
            abs(tray_shape.Volume - expected_tray_volume) < 1e-3
        )
    tray_document.save()
    tray_revisions = tray_service.revision_timeline()
    App.closeDocument(tray_document.Name)
    reopened_tray = App.openDocument(str(tray_artifact))
    reopened_tray.recompute()
    tray_final = next((obj for obj in reopened_tray.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"), None)
    tray_reopen_passed = bool(tray_final and tray_final.Shape.isValid() and not tray_final.Shape.isNull())
    App.closeDocument(reopened_tray.Name)
    camera_document = App.newDocument(f"Tier2CameraMount{trial_id}")
    camera_artifact = output / f"tier2-camera-mount-{trial_id}.FCStd"
    camera_document.saveAs(str(camera_artifact))
    camera_service = VibeCADService()
    camera_response = run_prompt(
        "Create a 70 by 50 by 6 mm camera mounting plate with a centered 6.5 mm hole and two vertical 20 by 6 mm adjustment slots 22 mm from center.",
        service=camera_service, prefer_online=False, provider=CameraMountProvider(),
    )
    camera_document.recompute()
    camera_pockets = [obj for obj in camera_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"]
    camera_passed = not camera_response.error and len(camera_pockets) == 1
    camera_measurements = {}
    if camera_passed:
        camera_shape = camera_pockets[0].Shape
        slot_area = (20 - 6) * 6 + math.pi * 3 * 3
        expected_camera_volume = 70 * 50 * 6 - math.pi * 3.25 * 3.25 * 6 - 2 * slot_area * 6
        camera_measurements = {
            "x_length": camera_shape.BoundBox.XLength, "y_length": camera_shape.BoundBox.YLength,
            "z_length": camera_shape.BoundBox.ZLength, "volume": camera_shape.Volume,
            "expected_volume": expected_camera_volume,
        }
        camera_passed = (
            camera_shape.isValid() and not camera_shape.isNull() and
            abs(camera_shape.BoundBox.XLength - 70) < 1e-6 and
            abs(camera_shape.BoundBox.YLength - 50) < 1e-6 and
            abs(camera_shape.BoundBox.ZLength - 6) < 1e-6 and
            abs(camera_shape.Volume - expected_camera_volume) < 1e-3
        )
    camera_document.save()
    camera_revisions = camera_service.revision_timeline()
    App.closeDocument(camera_document.Name)
    reopened_camera = App.openDocument(str(camera_artifact))
    reopened_camera.recompute()
    camera_final = next((obj for obj in reopened_camera.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"), None)
    camera_reopen_passed = bool(camera_final and camera_final.Shape.isValid() and not camera_final.Shape.isNull())
    App.closeDocument(reopened_camera.Name)
    clamp_document = App.newDocument(f"Tier2PipeClamp{trial_id}")
    clamp_artifact = output / f"tier2-pipe-clamp-{trial_id}.FCStd"
    clamp_document.saveAs(str(clamp_artifact))
    clamp_service = VibeCADService()
    clamp_response = run_prompt(
        "Create a 12 mm wide split clamp for a 40 mm pipe, with a 60 mm outside diameter, a 4 mm radial split, and two 5 mm through mounting holes.",
        service=clamp_service, prefer_online=False, provider=PipeClampProvider(),
    )
    clamp_document.recompute()
    clamp_pockets = [obj for obj in clamp_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"]
    clamp_passed = not clamp_response.error and len(clamp_pockets) == 1
    clamp_measurements = {}
    if clamp_passed:
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
        clamp_passed = (
            clamp_shape.isValid() and not clamp_shape.isNull() and
            abs(clamp_shape.BoundBox.XLength - 60) < 0.02 and
            abs(clamp_shape.BoundBox.YLength - expected_split_y_bound) < 0.02 and
            abs(clamp_shape.BoundBox.ZLength - 12) < 1e-6 and
            abs(clamp_shape.Volume - expected_clamp_volume) < 1e-3
        )
    clamp_document.save()
    clamp_revisions = clamp_service.revision_timeline()
    App.closeDocument(clamp_document.Name)
    reopened_clamp = App.openDocument(str(clamp_artifact))
    reopened_clamp.recompute()
    clamp_final = next((obj for obj in reopened_clamp.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"), None)
    clamp_reopen_passed = bool(clamp_final and clamp_final.Shape.isValid() and not clamp_final.Shape.isNull())
    App.closeDocument(reopened_clamp.Name)
    cover_document = App.newDocument(f"Tier2VentilatedCover{trial_id}")
    cover_artifact = output / f"tier2-ventilated-cover-{trial_id}.FCStd"
    cover_document.saveAs(str(cover_artifact))
    cover_service = VibeCADService()
    cover_response = run_prompt(
        "Create an 80 by 50 by 3 mm equipment cover with five horizontal 50 by 3 mm through ventilation slots on 8 mm spacing.",
        service=cover_service, prefer_online=False, provider=VentilatedCoverProvider(),
    )
    cover_document.recompute()
    cover_pockets = [obj for obj in cover_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"]
    cover_passed = not cover_response.error and len(cover_pockets) == 1
    cover_measurements = {}
    if cover_passed:
        cover_shape = cover_pockets[0].Shape
        vent_area = (50 - 3) * 3 + math.pi * 1.5 * 1.5
        expected_cover_volume = 80 * 50 * 3 - 5 * vent_area * 3
        cover_measurements = {
            "x_length": cover_shape.BoundBox.XLength, "y_length": cover_shape.BoundBox.YLength,
            "z_length": cover_shape.BoundBox.ZLength, "volume": cover_shape.Volume,
            "expected_volume": expected_cover_volume,
        }
        cover_passed = (
            cover_shape.isValid() and not cover_shape.isNull() and
            abs(cover_shape.BoundBox.XLength - 80) < 1e-6 and
            abs(cover_shape.BoundBox.YLength - 50) < 1e-6 and
            abs(cover_shape.BoundBox.ZLength - 3) < 1e-6 and
            abs(cover_shape.Volume - expected_cover_volume) < 1e-3
        )
    cover_document.save()
    cover_revisions = cover_service.revision_timeline()
    App.closeDocument(cover_document.Name)
    reopened_cover = App.openDocument(str(cover_artifact))
    reopened_cover.recompute()
    cover_final = next((obj for obj in reopened_cover.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"), None)
    cover_reopen_passed = bool(cover_final and cover_final.Shape.isValid() and not cover_final.Shape.isNull())
    App.closeDocument(reopened_cover.Name)
    enclosure_document = App.newDocument(f"Tier2ElectronicsEnclosure{trial_id}")
    enclosure_artifact = output / f"tier2-electronics-enclosure-{trial_id}.FCStd"
    enclosure_document.saveAs(str(enclosure_artifact))
    enclosure_service = VibeCADService()
    enclosure_response = run_prompt(
        "Create a 120 by 80 by 35 mm electronics enclosure with 2.5 mm walls and a separate 3 mm removable lid with four 3.2 mm M3 clearance holes.",
        service=enclosure_service, prefer_online=False,
        provider=ElectronicsEnclosureProvider(),
    )
    enclosure_document.recompute()
    enclosure_bodies = [obj for obj in enclosure_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Body"]
    enclosure_shells = [obj for obj in enclosure_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Thickness"]
    enclosure_pockets = [obj for obj in enclosure_document.Objects if getattr(obj, "TypeId", "") == "PartDesign::Pocket"]
    enclosure_passed = (
        not enclosure_response.error and len(enclosure_bodies) == 2 and
        len(enclosure_shells) == 1 and len(enclosure_pockets) == 1
    )
    enclosure_measurements = {}
    if enclosure_passed:
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
        enclosure_passed = (
            housing_shape.isValid() and not housing_shape.isNull() and
            lid_shape.isValid() and not lid_shape.isNull() and
            abs(housing_shape.BoundBox.XLength - 120) < 1e-6 and
            abs(housing_shape.BoundBox.YLength - 80) < 1e-6 and
            abs(housing_shape.BoundBox.ZLength - 35) < 1e-6 and
            abs(housing_shape.Volume - expected_housing_volume) < 1e-3 and
            abs(lid_shape.BoundBox.XLength - 120) < 1e-6 and
            abs(lid_shape.BoundBox.YLength - 80) < 1e-6 and
            abs(lid_shape.BoundBox.ZLength - 3) < 1e-6 and
            abs(lid_shape.Volume - expected_lid_volume) < 1e-3
        )
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
    App.closeDocument(reopened_enclosure.Name)
    bracket_case = {
        "case": "wall-bracket", "passed": bool(passed and reopen_passed and len(revisions) == 1),
        "geometry_passed": bool(passed), "reopen_passed": reopen_passed,
        "revision_count": len(revisions), "tool_count": len(response.tool_trace),
        "artifact": str(artifact), "error": response.error,
    }
    adapter_case = {
        "case": "motor-adapter", "passed": bool(adapter_passed and adapter_reopen_passed and len(adapter_revisions) == 1),
        "geometry_passed": bool(adapter_passed), "reopen_passed": adapter_reopen_passed,
        "measurements": adapter_measurements,
        "revision_count": len(adapter_revisions), "tool_count": len(adapter_response.tool_trace),
        "artifact": str(adapter_artifact), "error": adapter_response.error,
    }
    tray_case = {
        "case": "battery-tray", "passed": bool(tray_passed and tray_reopen_passed and len(tray_revisions) == 1),
        "geometry_passed": bool(tray_passed), "reopen_passed": tray_reopen_passed,
        "revision_count": len(tray_revisions), "tool_count": len(tray_response.tool_trace),
        "measurements": tray_measurements, "artifact": str(tray_artifact), "error": tray_response.error,
    }
    camera_case = {
        "case": "camera-mount", "passed": bool(camera_passed and camera_reopen_passed and len(camera_revisions) == 1),
        "geometry_passed": bool(camera_passed), "reopen_passed": camera_reopen_passed,
        "revision_count": len(camera_revisions), "tool_count": len(camera_response.tool_trace),
        "measurements": camera_measurements, "artifact": str(camera_artifact), "error": camera_response.error,
    }
    clamp_case = {
        "case": "pipe-clamp", "passed": bool(clamp_passed and clamp_reopen_passed and len(clamp_revisions) == 1),
        "geometry_passed": bool(clamp_passed), "reopen_passed": clamp_reopen_passed,
        "revision_count": len(clamp_revisions), "tool_count": len(clamp_response.tool_trace),
        "measurements": clamp_measurements, "artifact": str(clamp_artifact), "error": clamp_response.error,
    }
    cover_case = {
        "case": "ventilated-cover", "passed": bool(cover_passed and cover_reopen_passed and len(cover_revisions) == 1),
        "geometry_passed": bool(cover_passed), "reopen_passed": cover_reopen_passed,
        "revision_count": len(cover_revisions), "tool_count": len(cover_response.tool_trace),
        "measurements": cover_measurements, "artifact": str(cover_artifact), "error": cover_response.error,
    }
    enclosure_case = {
        "case": "electronics-enclosure-and-lid",
        "passed": bool(enclosure_passed and enclosure_reopen_passed and len(enclosure_revisions) == 1),
        "geometry_passed": bool(enclosure_passed), "reopen_passed": enclosure_reopen_passed,
        "revision_count": len(enclosure_revisions), "tool_count": len(enclosure_response.tool_trace),
        "measurements": enclosure_measurements, "artifact": str(enclosure_artifact),
        "error": enclosure_response.error,
    }
    report = {
        "schema": "vibecad-tier2-provider-result-v1", "version": 1,
        "case_count": 7, "executor": "deterministic-provider-transactional-baseline",
        "live_model_score": False,
        "passed": bool(
            bracket_case["passed"] and adapter_case["passed"] and
            tray_case["passed"] and camera_case["passed"] and
            clamp_case["passed"] and cover_case["passed"] and
            enclosure_case["passed"]
        ),
        "cases": [bracket_case, adapter_case, tray_case, camera_case, clamp_case, cover_case, enclosure_case],
        "revision_count": (
            len(revisions) + len(adapter_revisions) + len(tray_revisions) +
            len(camera_revisions) + len(clamp_revisions) + len(cover_revisions) +
            len(enclosure_revisions)
        ),
        "tool_count": (
            len(response.tool_trace) + len(adapter_response.tool_trace) +
            len(tray_response.tool_trace) + len(camera_response.tool_trace) +
            len(clamp_response.tool_trace) + len(cover_response.tool_trace) +
            len(enclosure_response.tool_trace)
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
