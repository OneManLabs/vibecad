#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run a VibeCAD deterministic CAD capability benchmark in FreeCADCmd."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freecadcmd", default="build/release/bin/FreeCADCmd")
    parser.add_argument("--suite", default="tests/benchmark/tier1_cases.json")
    parser.add_argument("--output", default="build/benchmark/tier1")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    command = [
        str((root / args.freecadcmd).resolve()), "-c",
        "exec(open('tests/benchmark/tier1_freecad_runner.py').read())", "--pass",
        args.suite, args.output,
    ]
    completed = subprocess.run(command, cwd=root, check=False)
    report_path = root / args.output / "tier1-results.json"
    if not report_path.is_file():
        raise RuntimeError("The benchmark did not produce its result record.")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema") != "vibecad-cad-benchmark-result-v1":
        raise RuntimeError("The benchmark result schema is invalid.")
    print(
        f"Tier {report['tier']} capability baseline: {report['passed']}/{report['case_count']} "
        f"passed ({report['valid_completion_rate']:.1%})."
    )
    return completed.returncode if completed.returncode else (0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    raise SystemExit(main())
