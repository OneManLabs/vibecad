# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real OpenCASCADE FDM analysis integration check."""

import FreeCAD as App
import Part

from VibeCADManufacturing import analyze_fdm_shape


def main() -> int:
    shape = Part.makeBox(20, 30, 10).cut(
        Part.makeCylinder(1.5, 10, App.Vector(10, 15, 0))
    )
    report = analyze_fdm_shape(shape, object_name="PrintablePlate")
    assert report["native_checks"]["watertight_solid"] is True
    assert report["bounding_dimensions_mm"] == [20.0, 30.0, 10.0]
    assert any(
        abs(value - 3.0) < 1.0e-6
        for value in report["cylindrical_hole_diameters_mm"]
    )
    assert report["recommended_build_axis"] == "z"
    print("VibeCAD FDM FreeCAD integration passed")
    return 0
