# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD GUI lifecycle entry point for the Tier 2 provider benchmark."""

from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys
import unittest


class Tier2ProviderBenchmark(unittest.TestCase):
    def tearDown(self) -> None:
        import FreeCAD as App
        for name in list(App.listDocuments()):
            App.closeDocument(name)

    def test_functional_parts(self) -> None:
        root = Path.cwd()
        runner = root / "tests" / "benchmark" / "tier2_transactional_provider_runner.py"
        output = Path(os.environ.get(
            "VIBECAD_TIER2_BENCHMARK_OUTPUT",
            str(root / "build" / "benchmark" / "tier2-provider"),
        ))
        namespace = runpy.run_path(str(runner))
        original = list(sys.argv)
        try:
            sys.argv = [str(runner), str(output)]
            self.assertEqual(namespace["main"](), 0)
        finally:
            sys.argv = original


def suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(Tier2ProviderBenchmark)
