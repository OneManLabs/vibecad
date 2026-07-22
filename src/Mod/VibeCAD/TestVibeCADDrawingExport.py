# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD GUI integration entry point for typed TechDraw export."""

from pathlib import Path
import runpy
import unittest


class DrawingExportIntegrationTest(unittest.TestCase):
    def test_page_specific_pdf_dxf_svg_export(self) -> None:
        script = (
            Path.cwd()
            / "src/Mod/VibeCAD/vibecad_tests/project_drawing_export_freecad_integration.py"
        )
        namespace = runpy.run_path(str(script), run_name="vibecad_drawing_export")
        self.assertEqual(namespace["main"](), 0)


def suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(
        DrawingExportIntegrationTest
    )
