#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run repeated Tier 2 provider transactions in the FreeCAD GUI lifecycle."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "Mod" / "VibeCAD"))

from VibeCADBenchmark import (  # noqa: E402
    finalize_series_report,
    series_exit_code,
)


EXPECTED_CASE_IDS = (
    "t2_wall_bracket",
    "t2_motor_adapter",
    "t2_battery_tray",
    "t2_camera_mount",
    "t2_pipe_clamp",
    "t2_ventilated_cover",
    "t2_electronics_enclosure_and_lid",
    "t2_flanged_coupling",
    "t2_simple_hinge",
    "t2_bolt_pattern_plate",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freecad", default="build/release/bin/FreeCAD")
    parser.add_argument("--output", default="build/benchmark/tier2-provider")
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    if args.trials < 1:
        raise ValueError("Trial count must be positive.")
    root = ROOT
    output = root / args.output
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "tier2-provider-results.json"
    trials = []
    case_attempts = []
    for index in range(args.trials):
        result_path.unlink(missing_ok=True)
        environment = dict(os.environ)
        environment.update(
            QT_QPA_PLATFORM="offscreen",
            VIBECAD_TIER2_BENCHMARK_OUTPUT=str(output),
            VIBECAD_BENCHMARK_ATTEMPT=str(index + 1),
        )
        completed = subprocess.run(
            [str((root / args.freecad).resolve()), "-t", "TestVibeCADTier2Benchmark"],
            cwd=root, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=180, check=False,
        )
        if not result_path.is_file():
            print((completed.stdout or "")[-4000:])
            raise RuntimeError(f"Tier 2 trial {index + 1} produced no fresh result.")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        test_ok = "\nOK\n" in (completed.stdout or "")
        result.update(
            trial=index + 1,
            gui_runner_exit_code=completed.returncode,
            gui_runner_reported_ok=test_ok,
        )
        result["passed"] = bool(result.get("passed") and test_ok)
        trials.append(result)
        evidence = result.get("case_attempts")
        if not isinstance(evidence, list) or not evidence:
            print((completed.stdout or "")[-4000:])
            raise RuntimeError(
                f"Tier 2 trial {index + 1} has no valid case-attempt evidence."
            )
        case_attempts.extend(evidence)
    report = finalize_series_report(
        case_attempts,
        [
            {
                "attempt": item["trial"],
                "case_evidence_passed": all(
                    case.get("passed") is True
                    for case in item["case_attempts"]
                ),
                "gui_runner_exit_code": item["gui_runner_exit_code"],
                "gui_runner_reported_ok": item["gui_runner_reported_ok"],
            }
            for item in trials
        ],
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        expected_case_ids=EXPECTED_CASE_IDS,
    )
    (output / "tier2-provider-series.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        "Tier 2 functional-part baseline: "
        f"{report['passed_case_attempts']}/{report['case_attempt_count']} "
        f"case attempts passed ({report['case_attempt_completion_rate']:.1%})."
    )
    return series_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
