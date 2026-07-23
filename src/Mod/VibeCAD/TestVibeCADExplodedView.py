# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD GUI integration entry point for native exploded views."""

from pathlib import Path
import runpy
import unittest


class ExplodedViewIntegrationTest(unittest.TestCase):
    def test_accept_reopen_restore_and_compare(self) -> None:
        relative = Path(
            "src/Mod/VibeCAD/vibecad_tests/assembly_exploded_view_freecad_integration.py"
        )
        search_roots = [Path.cwd(), *Path.cwd().parents]
        module_path = Path(__file__).resolve()
        search_roots.extend([module_path.parent, *module_path.parents])
        script = next(
            (root / relative for root in search_roots if (root / relative).is_file()),
            None,
        )
        self.assertIsNotNone(script, f"Integration script not found: {relative}")
        namespace = runpy.run_path(str(script), run_name="vibecad_exploded_view")
        self.assertEqual(namespace["main"](), 0)


def suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(
        ExplodedViewIntegrationTest
    )
