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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freecad", default="build/release/bin/FreeCAD")
    parser.add_argument("--output", default="build/benchmark/tier2-provider")
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    if args.trials < 1:
        raise ValueError("Trial count must be positive.")
    root = Path(__file__).resolve().parents[1]
    output = root / args.output
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "tier2-provider-results.json"
    trials = []
    for index in range(args.trials):
        result_path.unlink(missing_ok=True)
        environment = dict(os.environ)
        environment.update(
            QT_QPA_PLATFORM="offscreen",
            VIBECAD_TIER2_BENCHMARK_OUTPUT=str(output),
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
        if not result["passed"]:
            print((completed.stdout or "")[-4000:])
    passed = sum(1 for trial in trials if trial["passed"])
    report = {
        "schema": "vibecad-tier2-provider-series-v1", "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "executor": "deterministic-provider-transactional-baseline",
        "live_model_score": False, "trial_count": len(trials),
        "passed": passed, "failed": len(trials) - passed,
        "valid_completion_rate": passed / len(trials), "trials": trials,
    }
    (output / "tier2-provider-series.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Tier 2 functional-part baseline: {passed}/{len(trials)} passed ({report['valid_completion_rate']:.1%}).")
    return 0 if passed == len(trials) else 1


if __name__ == "__main__":
    raise SystemExit(main())
