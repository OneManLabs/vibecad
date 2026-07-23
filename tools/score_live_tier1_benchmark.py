#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Score one retained Tier 1 live run with bound human ratings."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hmac
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src" / "Mod" / "VibeCAD"))

from VibeCADBenchmark import (  # noqa: E402
    apply_human_rating_set,
    finalize_series_report,
    series_exit_code,
)
from VibeCADLiveBenchmark import (  # noqa: E402
    TIER1_CASE_IDS,
    limits_digest,
    readiness_digest,
    runtime_identity_digest,
    validate_live_readiness,
    validate_runtime_identity,
    validate_unrated_live_run,
)
from tools.vibecad_benchmark_evidence_io import (  # noqa: E402
    EvidenceIOError,
    SecureFileSnapshot,
    lexical_absolute_path,
    load_bounded_json,
    open_bounded_regular_file,
    reject_file_aliases,
    write_json_exclusive,
)


MAX_RAW_JSON_BYTES = 16 * 1024 * 1024
MAX_RATING_JSON_BYTES = 4 * 1024 * 1024
MAX_READINESS_JSON_BYTES = 256 * 1024
MAX_RUNTIME_IDENTITY_JSON_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_ARTIFACT_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_SCORED_JSON_BYTES = 32 * 1024 * 1024
MAX_PROVIDER_COMPONENT_BYTES = 1024 * 1024 * 1024
MAX_PROVIDER_COMPONENT_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


def _canonical_artifact_path(
    value: Any,
    *,
    run_directory: Path,
    case_id: str,
) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise EvidenceIOError("A case artifact path is empty or invalid.")
    if not os.path.isabs(value) or os.path.normpath(value) != value:
        raise EvidenceIOError(
            f"Case {case_id} contains a noncanonical or escaped artifact path."
        )
    artifact = Path(value)
    if any(component in {"", ".", ".."} for component in artifact.parts[1:]):
        raise EvidenceIOError(
            f"Case {case_id} contains an unsafe artifact path component."
        )
    try:
        relative = Path(os.path.relpath(value, run_directory))
    except ValueError as exc:
        raise EvidenceIOError(
            f"Case {case_id} contains an artifact outside the live run directory."
        ) from exc
    if (
        relative.is_absolute()
        or not relative.parts
        or relative.parts[0] != case_id
        or len(relative.parts) < 2
        or any(component in {"", ".", ".."} for component in relative.parts)
    ):
        raise EvidenceIOError(
            f"Case {case_id} contains an artifact outside its case directory."
        )
    return lexical_absolute_path(artifact)


def _open_and_verify_artifacts(
    raw: Mapping[str, Any],
    *,
    run_directory: Path,
    input_snapshots: tuple[SecureFileSnapshot, ...],
    stack: ExitStack,
) -> list[SecureFileSnapshot]:
    seen_paths: set[Path] = set()
    seen_identities = {snapshot.identity for snapshot in input_snapshots}
    snapshots: list[SecureFileSnapshot] = []
    total_bytes = 0
    for attempt in raw["case_attempts"]:
        case_id = str(attempt["case_id"])
        artifact_paths = attempt["artifact_paths"]
        if len(artifact_paths) != len(set(artifact_paths)):
            raise EvidenceIOError(
                f"Case {case_id} contains a duplicate artifact path."
            )
        recorded_digests = attempt["artifact_sha256"]
        for index, value in enumerate(artifact_paths):
            artifact = _canonical_artifact_path(
                value,
                run_directory=run_directory,
                case_id=case_id,
            )
            if artifact in seen_paths:
                raise EvidenceIOError(
                    f"Artifact {artifact} occurs more than once in the live run."
                )
            snapshot = stack.enter_context(
                open_bounded_regular_file(
                    artifact,
                    max_bytes=MAX_ARTIFACT_BYTES,
                    label=f"case {case_id} artifact {index + 1}",
                    retain_data=False,
                    require_single_link=True,
                )
            )
            if snapshot.identity in seen_identities:
                raise EvidenceIOError(
                    f"Case {case_id} contains a duplicate or input-alias artifact."
                )
            expected = str(recorded_digests[value])
            if not hmac.compare_digest(snapshot.sha256, expected):
                raise EvidenceIOError(
                    f"Case {case_id} artifact SHA-256 does not match retained evidence."
                )
            total_bytes += snapshot.size
            if total_bytes > MAX_ARTIFACT_TOTAL_BYTES:
                raise EvidenceIOError(
                    "The live run exceeds the total artifact-byte limit."
                )
            seen_paths.add(artifact)
            seen_identities.add(snapshot.identity)
            snapshots.append(snapshot)
    return snapshots


def _open_and_verify_execution_evidence(
    raw: Mapping[str, Any],
    *,
    run_directory: Path,
    stack: ExitStack,
) -> tuple[
    tuple[SecureFileSnapshot, SecureFileSnapshot],
    dict[str, Any],
    dict[str, Any],
]:
    readiness_snapshot, readiness = load_bounded_json(
        run_directory / "provider-readiness.json",
        max_bytes=MAX_READINESS_JSON_BYTES,
        label="provider readiness",
        require_single_link=True,
    )
    stack.enter_context(readiness_snapshot)
    runtime_snapshot, runtime_identity = load_bounded_json(
        run_directory / "runtime-identity.json",
        max_bytes=MAX_RUNTIME_IDENTITY_JSON_BYTES,
        label="runtime identity",
        require_single_link=True,
    )
    stack.enter_context(runtime_snapshot)
    if not isinstance(readiness, Mapping):
        raise EvidenceIOError("The retained provider readiness is not an object.")
    if not isinstance(runtime_identity, Mapping):
        raise EvidenceIOError("The retained runtime identity is not an object.")
    try:
        checked_readiness = validate_live_readiness(
            readiness,
            require_fresh=False,
        )
        checked_runtime = validate_runtime_identity(
            runtime_identity,
            source_commit=str(raw["source_commit"]),
        )
    except Exception as exc:
        raise EvidenceIOError(
            f"The retained execution identity is invalid: {exc}"
        ) from exc
    expected_readiness = str(raw["readiness_sha256"])
    observed_readiness = readiness_digest(checked_readiness)
    if not hmac.compare_digest(observed_readiness, expected_readiness):
        raise EvidenceIOError(
            "The retained provider readiness does not match the raw live run."
        )
    expected_runtime = str(raw["runtime_identity_sha256"])
    observed_runtime = runtime_identity_digest(checked_runtime)
    if not hmac.compare_digest(observed_runtime, expected_runtime):
        raise EvidenceIOError(
            "The retained runtime identity does not match the raw live run."
        )
    return (
        (readiness_snapshot, runtime_snapshot),
        checked_readiness,
        checked_runtime,
    )


def _verify_provider_runtime_components(
    runtime_identity: Mapping[str, Any],
) -> None:
    """Re-open and hash every selected provider runtime component."""

    provider_runtime = runtime_identity["provider_runtime"]
    seen_identities: set[tuple[int, int]] = set()
    total_bytes = 0
    for index, component in enumerate(provider_runtime["components"]):
        relative = str(component["path"])
        candidate = lexical_absolute_path(ROOT / relative)
        try:
            candidate.relative_to(ROOT)
        except ValueError as exc:
            raise EvidenceIOError(
                "A provider runtime component is outside the source root."
            ) from exc
        with open_bounded_regular_file(
            candidate,
            max_bytes=MAX_PROVIDER_COMPONENT_BYTES,
            label=f"provider runtime component {index + 1}",
            retain_data=False,
            require_single_link=True,
        ) as snapshot:
            if snapshot.identity in seen_identities:
                raise EvidenceIOError(
                    "The provider runtime component list contains a file alias."
                )
            if (
                snapshot.size != int(component["size"])
                or not hmac.compare_digest(
                    snapshot.sha256, str(component["sha256"])
                )
            ):
                raise EvidenceIOError(
                    "A provider runtime component does not match its attestation."
                )
            snapshot.verify_unchanged()
            seen_identities.add(snapshot.identity)
            total_bytes += snapshot.size
            if total_bytes > MAX_PROVIDER_COMPONENT_TOTAL_BYTES:
                raise EvidenceIOError(
                    "The provider runtime component set exceeds its byte limit."
                )


def score_run(raw_path: Path, ratings_path: Path, output_path: Path) -> dict:
    with ExitStack() as stack:
        raw_snapshot, raw = load_bounded_json(
            raw_path,
            max_bytes=MAX_RAW_JSON_BYTES,
            label="raw live run",
        )
        stack.enter_context(raw_snapshot)
        rating_snapshot, rating_set = load_bounded_json(
            ratings_path,
            max_bytes=MAX_RATING_JSON_BYTES,
            label="human rating set",
        )
        stack.enter_context(rating_snapshot)
        validate_unrated_live_run(
            raw,
            require_runner_result=True,
            require_scorable=True,
        )
        execution_snapshots, readiness, runtime_identity = (
            _open_and_verify_execution_evidence(
                raw,
                run_directory=raw_snapshot.path.parent,
                stack=stack,
            )
        )
        _verify_provider_runtime_components(runtime_identity)
        input_snapshots = (
            raw_snapshot,
            rating_snapshot,
            *execution_snapshots,
        )
        reject_file_aliases(input_snapshots)
        artifact_snapshots = _open_and_verify_artifacts(
            raw,
            run_directory=raw_snapshot.path.parent,
            input_snapshots=input_snapshots,
            stack=stack,
        )
        rated_attempts = apply_human_rating_set(
            raw["case_attempts"],
            rating_set,
            run_binding={
                "readiness_sha256": raw["readiness_sha256"],
                "runtime_identity_sha256": raw["runtime_identity_sha256"],
                "limits_sha256": limits_digest(raw["limits"]),
                "raw_run_sha256": raw_snapshot.sha256,
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
            human_rating_set_sha256=rating_snapshot.sha256,
            raw_live_run_sha256=raw_snapshot.sha256,
            readiness_evidence_file_sha256=execution_snapshots[0].sha256,
            runtime_identity_evidence_file_sha256=execution_snapshots[1].sha256,
            readiness_endpoint_sha256=readiness["endpoint_sha256"],
            runtime_module_manifest_sha256=runtime_identity[
                "module_manifest_sha256"
            ],
        )

        all_snapshots = (*input_snapshots, *artifact_snapshots)

        def verify_stable_evidence() -> None:
            for snapshot in all_snapshots:
                snapshot.verify_unchanged()
            _verify_provider_runtime_components(runtime_identity)

        verify_stable_evidence()
        write_json_exclusive(
            output_path,
            series,
            max_bytes=MAX_SCORED_JSON_BYTES,
            protected_inputs=input_snapshots,
            stability_check=verify_stable_evidence,
        )
        return series


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_run")
    parser.add_argument("ratings")
    parser.add_argument("--output")
    args = parser.parse_args()
    raw_path = Path(args.raw_run)
    ratings_path = Path(args.ratings)
    output_path = (
        Path(args.output)
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
