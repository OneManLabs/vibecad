#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run the deterministic Tier 3 STEP import acceptance workflow."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "Mod" / "VibeCAD"))

from VibeCADBenchmark import validate_case_attempt  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freecad", default="build/release/bin/FreeCAD")
    parser.add_argument(
        "--output",
        default="build/benchmark/tier3-step-import/step-import-case-attempt.json",
    )
    args = parser.parse_args()
    executable = (ROOT / args.freecad).resolve()
    output = (ROOT / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    environment = dict(os.environ)
    environment.update(
        QT_QPA_PLATFORM="offscreen",
        VIBECAD_STEP_IMPORT_EVIDENCE=str(output),
        VIBECAD_FREECADCMD=str((executable.parent / "FreeCADCmd").resolve()),
    )
    completed = subprocess.run(
        [str(executable), "-t", "TestVibeCADStepImport"],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=240,
        check=False,
    )
    if not output.is_file():
        print((completed.stdout or "")[-6000:])
        raise RuntimeError("The STEP import acceptance run produced no case-attempt record.")
    record = validate_case_attempt(json.loads(output.read_text(encoding="utf-8")))
    reported_ok = "\nOK\n" in (completed.stdout or "")
    print((completed.stdout or "")[-3000:])
    print(
        "Tier 3 deterministic STEP import acceptance: "
        f"passed={record['passed']}, live_model_score={record['live_model_score']}, "
        f"FreeCAD reported OK={reported_ok}, exit={completed.returncode}."
    )
    return 0 if record["passed"] and reported_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

