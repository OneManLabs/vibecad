# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD GUI lifecycle entry point for the transactional provider benchmark."""

from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys
import unittest


class TransactionalProviderBenchmark(unittest.TestCase):
    def tearDown(self) -> None:
        import FreeCAD as App
        for name in list(App.listDocuments()):
            App.closeDocument(name)

    def test_exact_box(self) -> None:
        root = Path.cwd()
        runner = root / "tests" / "benchmark" / "tier1_transactional_provider_runner.py"
        self.assertTrue(runner.is_file(), f"Benchmark runner not found: {runner}")
        output = Path(os.environ.get(
            "VIBECAD_BENCHMARK_OUTPUT", str(root / "build" / "benchmark" / "tier1-provider")
        ))
        namespace = runpy.run_path(str(runner))
        original = list(sys.argv)
        try:
            sys.argv = [str(runner), str(output)]
            self.assertEqual(namespace["main"](), 0)
        finally:
            sys.argv = original


def suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(TransactionalProviderBenchmark)
