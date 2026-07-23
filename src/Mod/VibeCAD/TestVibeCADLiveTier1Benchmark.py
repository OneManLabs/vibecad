# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD GUI entry point for the gated Tier 1 live-provider benchmark."""

from __future__ import annotations

from pathlib import Path
import runpy
import unittest


class LiveTier1ProviderBenchmark(unittest.TestCase):
    def tearDown(self) -> None:
        import FreeCAD as App

        for name in list(App.listDocuments()):
            App.closeDocument(name)

    def test_all_tier1_cases(self) -> None:
        runner = Path(__file__).resolve().parent / "live_benchmark" / "tier1_live_provider_runner.py"
        self.assertTrue(runner.is_file(), f"Live benchmark runner not found: {runner}")
        namespace = runpy.run_path(str(runner))
        self.assertEqual(namespace["main"](), 0)


def suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(
        LiveTier1ProviderBenchmark
    )
