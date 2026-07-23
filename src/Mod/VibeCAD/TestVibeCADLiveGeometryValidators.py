# SPDX-License-Identifier: LGPL-2.1-or-later
"""Provider-free native checks for Tier 1 geometry evidence."""

from __future__ import annotations

import math
from pathlib import Path
import tempfile
import unittest

import FreeCAD as App
import Part

from VibeCADLiveBenchmark import (
    centered_hole_evidence,
    exact_volume_evidence,
    open_top_aperture_evidence,
    stl_export_evidence,
    symmetric_through_holes_evidence,
    visible_solid_target_evidence,
)


class LiveTier1GeometryValidators(unittest.TestCase):
    def test_centered_through_hole_uses_both_outer_surfaces(self) -> None:
        plate = Part.makeBox(40, 30, 10, App.Vector(-20, -15, 0))
        hole = Part.makeCylinder(3, 10, App.Vector(0, 0, 0))
        shape = plate.cut(hole)

        evidence = centered_hole_evidence(shape, 3)
        expected_volume = 40 * 30 * 10 - math.pi * 3 * 3 * 10
        self.assertTrue(evidence["passed"], evidence)
        self.assertAlmostEqual(shape.Volume, expected_volume, places=5)

    def test_open_enclosure_has_exact_uniform_cavity(self) -> None:
        outer = Part.makeBox(60, 40, 25, App.Vector(-30, -20, 0))
        cavity = Part.makeBox(56, 36, 23, App.Vector(-28, -18, 2))
        shape = outer.cut(cavity)

        evidence = open_top_aperture_evidence(shape, 2)
        self.assertTrue(evidence["passed"], evidence)
        self.assertEqual(
            evidence["inner_wall_sides"],
            ["x_max", "x_min", "y_max", "y_min"],
        )

        added_material = shape.fuse(
            Part.makeBox(4, 4, 1, App.Vector(-2, -2, 2))
        )
        expected_volume = 60 * 40 * 25 - 56 * 36 * 23
        self.assertFalse(
            exact_volume_evidence(added_material, expected_volume)["passed"]
        )

    def test_mirrored_holes_have_exact_symmetric_centers(self) -> None:
        plate = Part.makeBox(40, 30, 5, App.Vector(-20, -15, 0))
        left = Part.makeCylinder(2.5, 5, App.Vector(-10, 0, 0))
        right = Part.makeCylinder(2.5, 5, App.Vector(10, 0, 0))
        shape = plate.cut(left.fuse(right))

        evidence = symmetric_through_holes_evidence(
            shape, 2.5, ((-10, 0), (10, 0))
        )
        self.assertTrue(evidence["passed"], evidence)

    def test_unrelated_solid_debris_fails_target_scope(self) -> None:
        document = App.newDocument("LiveTargetScope")
        try:
            target = document.addObject("Part::Feature", "Target")
            target.Shape = Part.makeBox(20, 20, 20)
            debris = document.addObject("Part::Feature", "UnrelatedDebris")
            debris.Shape = Part.makeBox(1, 1, 1, App.Vector(30, 0, 0))
            document.recompute()

            evidence = visible_solid_target_evidence(document, target)
            self.assertFalse(evidence["passed"], evidence)
            self.assertEqual(evidence["visible_solid_owner_count"], 2)
        finally:
            App.closeDocument(document.Name)

    def test_large_junk_file_does_not_pass_stl_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "junk.stl"
            path.write_bytes(b"This is not an STL file.\n" * 32)

            evidence = stl_export_evidence(path, [20, 20, 20])
            self.assertFalse(evidence["passed"], evidence)
            self.assertGreater(evidence["size_bytes"], 100)
            self.assertEqual(evidence["facet_count"], 0)


def suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(
        LiveTier1GeometryValidators
    )
