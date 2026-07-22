#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bound provider authentication preflight without sending design data."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable


def run_probe(
    freecad: Path,
    child: Path,
    output: Path,
    timeout_seconds: float,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    output.unlink(missing_ok=True)
    timed_out = False
    return_code = None
    try:
        environment = dict(os.environ)
        environment["VIBECAD_PROVIDER_READINESS_OUTPUT"] = str(output.resolve())
        expression = (
            "import runpy; "
            f"runpy.run_path({str(child)!r}, run_name='__main__')"
        )
        completed = runner(
            [str(freecad), "-c", expression], env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout_seconds, check=False,
        )
        return_code = completed.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
    if output.is_file():
        result = json.loads(output.read_text(encoding="utf-8"))
    else:
        result = {
            "schema": "vibecad-provider-readiness-v1", "version": 1,
            "can_call_provider": False, "prompt_sent": False,
            "document_data_sent": False,
            "error": "Provider readiness did not return before the timeout." if timed_out else "Provider readiness produced no result.",
        }
    result["process_timed_out"] = timed_out
    result["process_exit_code"] = return_code
    result["ready_for_live_benchmark"] = bool(
        result.get("can_call_provider") and not result.get("prompt_sent")
        and not result.get("document_data_sent")
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freecad", default="build/release/bin/FreeCADCmd")
    parser.add_argument("--output", default="build/benchmark/provider-readiness.json")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    if args.timeout <= 0 or args.timeout > 60:
        raise ValueError("Timeout must be greater than zero and at most 60 seconds.")
    root = Path(__file__).resolve().parents[1]
    result = run_probe(
        (root / args.freecad).resolve(),
        (root / "tools" / "provider_readiness_child.py").resolve(),
        (root / args.output).resolve(),
        args.timeout,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ready_for_live_benchmark"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
