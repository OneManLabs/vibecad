#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Score one retained Tier 1 live run with bound human ratings."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "Mod" / "VibeCAD"))

from VibeCADBenchmark import (  # noqa: E402
    apply_human_rating_set,
    finalize_series_report,
    series_exit_code,
)
from VibeCADLiveBenchmark import (  # noqa: E402
    TIER1_CASE_IDS,
    atomic_write_json,
    limits_digest,
    sha256_file,
    validate_unrated_live_run,
)


def score_run(raw_path: Path, ratings_path: Path, output_path: Path) -> dict:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    validate_unrated_live_run(raw, require_runner_result=True)
    rating_set = json.loads(ratings_path.read_text(encoding="utf-8"))
    rated_attempts = apply_human_rating_set(
        raw["case_attempts"],
        rating_set,
        run_binding={
            "readiness_sha256": raw["readiness_sha256"],
            "runtime_identity_sha256": raw["runtime_identity_sha256"],
            "limits_sha256": limits_digest(raw["limits"]),
            "raw_run_sha256": sha256_file(raw_path),
        },
    )
    series = finalize_series_report(
        rated_attempts,
        [raw["runner"]],
        expected_case_ids=TIER1_CASE_IDS,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    series.update(
        scored=True,
        source_commit=raw["source_commit"],
        readiness_sha256=raw["readiness_sha256"],
        runtime_identity_sha256=raw["runtime_identity_sha256"],
        human_rating_set_sha256=sha256_file(ratings_path),
        raw_live_run_sha256=sha256_file(raw_path),
    )
    atomic_write_json(output_path, series)
    return series


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_run")
    parser.add_argument("ratings")
    parser.add_argument("--output")
    args = parser.parse_args()
    raw_path = Path(args.raw_run).resolve()
    ratings_path = Path(args.ratings).resolve()
    output_path = (
        Path(args.output).resolve()
        if args.output
        else raw_path.with_name("tier1-live-scored-series.json")
    )
    series = score_run(raw_path, ratings_path, output_path)
    print(
        "Tier 1 live score: "
        f"{series['passed_case_attempts']}/{series['case_attempt_count']} "
        f"case attempts passed ({series['case_attempt_completion_rate']:.1%})."
    )
    return series_exit_code(series)


if __name__ == "__main__":
    raise SystemExit(main())
