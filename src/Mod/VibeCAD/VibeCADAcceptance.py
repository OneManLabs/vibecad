# SPDX-License-Identifier: LGPL-2.1-or-later
"""Crash-safe promotion of a validated CAD candidate and its provenance."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Callable, Mapping
import uuid

from VibeCADRevision import VibeCADRevisionStore, calculate_revision_id, validate_revision_record
from VibeCADAudit import VibeCADAuditStore


ROLLBACK_SCHEMA = "vibecad-rollback-artifact-v1"
ACCEPTANCE_SCHEMA = "vibecad-acceptance-journal-v1"
ACCEPTED_ARTIFACT_SCHEMA = "vibecad-accepted-revision-artifact-v1"
VALIDATED_CANDIDATE_SCHEMA = "vibecad-validated-candidate-v1"
ACCEPTANCE_VERSION = 1

_ACCEPTANCE_MODES = {"automatic", "human"}

SaveCopy = Callable[[Path], None]
ValidateDocument = Callable[[Path], Mapping[str, Any]]
RestoreLive = Callable[[Path], None]
MetadataWrite = Callable[[str | None], None]
FaultInjector = Callable[[str], None]
RevisionRecord = Mapping[str, Any] | Callable[[Mapping[str, Any]], Mapping[str, Any]]

_SNAPSHOT_EXCLUSIONS = {"acceptance", "audit", "revisions", "conversations"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_sha256(path: Path, *, exclude_top_level: set[str] | None = None) -> str:
    """Hash names, types, link targets, and contents in a project snapshot."""
    digest = hashlib.sha256()
    if not path.is_dir():
        raise RuntimeError(f"Project snapshot is missing: {path}")
    for item in sorted(path.rglob("*"), key=lambda value: value.relative_to(path).as_posix()):
        relative_path = item.relative_to(path)
        if exclude_top_level and relative_path.parts[0] in exclude_top_level:
            continue
        relative = relative_path.as_posix().encode("utf-8")
        if item.is_symlink():
            digest.update(b"L\0" + relative + b"\0" + os.readlink(item).encode("utf-8") + b"\0")
        elif item.is_dir():
            digest.update(b"D\0" + relative + b"\0")
        else:
            digest.update(b"F\0" + relative + b"\0" + _sha256(item).encode("ascii") + b"\0")
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class PreparedAcceptance:
    acceptance_id: str
    directory: Path
    journal_path: Path
    rollback_path: Path
    candidate_path: Path
    canonical_path: Path
    prior_head: str | None
    project_snapshot_path: Path
    accepted_project_snapshot_path: Path
    accepted_artifact_path: Path

    @property
    def validated_candidate_path(self) -> Path:
        """Return the durable review record for this acceptance."""
        return self.directory / "validated-candidate.json"

    @property
    def canonical_backup_path(self) -> Path:
        """Return the byte-exact pre-run copy of the canonical CAD file."""
        return self.directory / "canonical-backup.fcstd"


class VibeCADAcceptanceCoordinator:
    """Own one candidate-to-accepted promotion across CAD and JSON files."""

    def __init__(self, project_root: str | Path, project_id: str) -> None:
        self.project_root = Path(project_root)
        self.project_id = str(project_id)
        self.revisions = VibeCADRevisionStore(self.project_root, self.project_id)
        self.audit = VibeCADAuditStore(self.project_root, self.project_id)
        self.directory = self.project_root / "acceptance"

    def prepare(self, canonical_path: str | Path, save_copy: SaveCopy) -> PreparedAcceptance:
        acceptance_id = uuid.uuid4().hex
        directory = self.directory / acceptance_id
        prepared = PreparedAcceptance(
            acceptance_id=acceptance_id,
            directory=directory,
            journal_path=directory / "journal.json",
            rollback_path=directory / "rollback.fcstd",
            candidate_path=directory / "candidate.fcstd",
            canonical_path=Path(canonical_path),
            prior_head=(self.revisions.head() or {}).get("revision_id"),
            project_snapshot_path=directory / "project-state",
            accepted_project_snapshot_path=directory / "accepted-project-state",
            accepted_artifact_path=directory / "accepted-artifact.json",
        )
        self._snapshot_project_state(prepared.project_snapshot_path)
        if not prepared.canonical_path.is_file():
            raise RuntimeError("The canonical CAD document does not exist.")
        _atomic_copy(prepared.canonical_path, prepared.canonical_backup_path)
        save_copy(prepared.rollback_path)
        if not prepared.rollback_path.is_file():
            raise RuntimeError("FreeCAD did not create the rollback document.")
        rollback = {
            "schema": ROLLBACK_SCHEMA,
            "version": ACCEPTANCE_VERSION,
            "project_id": self.project_id,
            "acceptance_id": acceptance_id,
            "prior_head": prepared.prior_head,
            "document": str(prepared.rollback_path),
            "sha256": _sha256(prepared.rollback_path),
            "canonical_document": str(prepared.canonical_path),
            "canonical_sha256": _sha256(prepared.canonical_path),
            "canonical_backup": str(prepared.canonical_backup_path),
            "canonical_backup_sha256": _sha256(prepared.canonical_backup_path),
            "project_snapshot": str(prepared.project_snapshot_path),
            "project_tree_sha256": _tree_sha256(prepared.project_snapshot_path),
            "project_entries": self._mutable_project_entries(),
        }
        _atomic_json(directory / "rollback.json", rollback)
        self._journal(
            prepared,
            "prepared",
            rollback_sha256=rollback["sha256"],
            canonical_sha256=rollback["canonical_sha256"],
            project_tree_sha256=rollback["project_tree_sha256"],
        )
        return prepared

    def promote(
        self,
        prepared: PreparedAcceptance,
        record: RevisionRecord,
        *,
        save_copy: SaveCopy,
        validate_document: ValidateDocument,
        restore_live: RestoreLive,
        write_metadata: MetadataWrite,
        fault: FaultInjector | None = None,
        acceptance_mode: str = "automatic",
    ) -> dict[str, Any]:
        """Validate and accept a candidate for compatibility with automatic callers."""
        self.validate_candidate(
            prepared,
            record,
            save_copy=save_copy,
            validate_document=validate_document,
            restore_live=restore_live,
            write_metadata=write_metadata,
            fault=fault,
        )
        return self.accept_validated_candidate(
            prepared,
            restore_live=restore_live,
            write_metadata=write_metadata,
            acceptance_mode=acceptance_mode,
            fault=fault,
        )

    def validate_candidate(
        self,
        prepared: PreparedAcceptance,
        record: RevisionRecord,
        *,
        save_copy: SaveCopy,
        validate_document: ValidateDocument,
        restore_live: RestoreLive,
        write_metadata: MetadataWrite,
        fault: FaultInjector | None = None,
    ) -> dict[str, Any]:
        """Save and validate a candidate without changing the accepted state."""
        validation: dict[str, Any] | None = None
        try:
            save_copy(prepared.candidate_path)
            self._fault(fault, "after_candidate_save")
            validation = dict(validate_document(prepared.candidate_path))
            if validation.get("ok") is not True:
                raise RuntimeError("Saved candidate document validation failed.")
            self._snapshot_project_state(prepared.accepted_project_snapshot_path)
            self._fault(fault, "after_candidate_project_snapshot")
            artifact = self._write_accepted_artifact(prepared)
            self._fault(fault, "after_candidate_artifact_write")
            raw_record = dict(record(validation) if callable(record) else record)
            raw_record["accepted_artifact"] = artifact
            raw_record["revision_id"] = calculate_revision_id(raw_record)
            validated = validate_revision_record(raw_record, project_id=self.project_id)
            if validated.get("parent_revision") != prepared.prior_head:
                raise RuntimeError("Candidate revision parent does not match the prepared head.")
            rollback = self._rollback_record(prepared)
            pending = {
                "schema": VALIDATED_CANDIDATE_SCHEMA,
                "version": ACCEPTANCE_VERSION,
                "project_id": self.project_id,
                "acceptance_id": prepared.acceptance_id,
                "prior_head": prepared.prior_head,
                "canonical_document": str(prepared.canonical_path),
                "canonical_sha256": rollback["canonical_sha256"],
                "baseline_project_tree_sha256": rollback["project_tree_sha256"],
                "candidate_document": str(prepared.candidate_path),
                "candidate_sha256": _sha256(prepared.candidate_path),
                "project_snapshot": str(prepared.accepted_project_snapshot_path),
                "project_tree_sha256": _tree_sha256(prepared.accepted_project_snapshot_path),
                "validation": validation,
                "revision": validated,
            }
            _atomic_json(prepared.validated_candidate_path, pending)
            self._journal(
                prepared,
                "candidate_validated",
                candidate_sha256=pending["candidate_sha256"],
                project_tree_sha256=pending["project_tree_sha256"],
            )
            self._fault(fault, "after_candidate_record_write")
            self._fault(fault, "after_candidate_validation")

            # Tool calls can change project sidecars before review. Keep those
            # changes only in the candidate snapshot until a decision is made.
            self._restore_project_state(prepared.project_snapshot_path)
            self._fault(fault, "after_baseline_project_restore")
            self._assert_accepted_identity(prepared, pending)
            self._journal(
                prepared,
                "awaiting_decision",
                candidate_sha256=pending["candidate_sha256"],
                project_tree_sha256=pending["project_tree_sha256"],
            )
            self._fault(fault, "after_review_state_restored")
            return {
                "ok": True,
                "state": "awaiting_decision",
                "revision": validated,
                "validation": validation,
                "acceptance_id": prepared.acceptance_id,
                "prior_head": prepared.prior_head,
                "candidate_sha256": pending["candidate_sha256"],
                "candidate_path": str(prepared.candidate_path),
                "project_snapshot_path": str(prepared.accepted_project_snapshot_path),
            }
        except Exception as exc:
            self._rollback_failed_acceptance(
                prepared,
                exc,
                restore_live=restore_live,
                write_metadata=write_metadata,
            )

    def accept_validated_candidate(
        self,
        prepared: PreparedAcceptance,
        *,
        restore_live: RestoreLive,
        write_metadata: MetadataWrite,
        acceptance_mode: str,
        fault: FaultInjector | None = None,
    ) -> dict[str, Any]:
        """Promote one durable validated candidate as one accepted revision."""
        mode = self._validate_acceptance_mode(acceptance_mode)
        if self._journal_state(prepared) != "awaiting_decision":
            raise RuntimeError("Validated candidate is not awaiting an acceptance decision.")
        validated: dict[str, Any] | None = None
        head_promoted = False
        metadata_promoted = False
        audit_recorded = False
        try:
            pending = self._read_validated_candidate(prepared)
            self._fault(fault, "after_candidate_record_read")
            validation = dict(pending["validation"])
            raw_record = dict(pending["revision"])
            artifact = self._write_accepted_artifact(prepared, acceptance_mode=mode)
            self._fault(fault, "after_accepted_artifact_write")
            raw_record["accepted_artifact"] = artifact
            raw_record["revision_id"] = calculate_revision_id(raw_record)
            validated = validate_revision_record(raw_record, project_id=self.project_id)
            if validated.get("parent_revision") != prepared.prior_head:
                raise RuntimeError("Candidate revision parent does not match the prepared head.")
            self.revisions.stage(validated)
            self._journal(prepared, "revision_staged", revision_id=validated["revision_id"])
            self._fault(fault, "after_revision_stage")
            self._restore_project_state(prepared.accepted_project_snapshot_path)
            self._journal(prepared, "project_promoted", revision_id=validated["revision_id"])
            self._fault(fault, "after_project_promotion")
            _atomic_copy(prepared.candidate_path, prepared.canonical_path)
            self._journal(prepared, "cad_promoted", revision_id=validated["revision_id"])
            self._fault(fault, "after_cad_promotion")
            self.revisions.promote(validated["revision_id"], expected_head=prepared.prior_head)
            head_promoted = True
            self._journal(prepared, "head_promoted", revision_id=validated["revision_id"])
            self._fault(fault, "after_head_promotion")
            write_metadata(validated["revision_id"])
            metadata_promoted = True
            self.audit.record(
                category="ai_revision",
                action="accept",
                outcome="success",
                actor_type="user" if mode == "human" else "ai_provider",
                details={
                    "acceptance_id": prepared.acceptance_id,
                    "acceptance_mode": mode,
                    "revision_id": validated["revision_id"],
                    "parent_revision": prepared.prior_head,
                    "provider": validated.get("provider"),
                    "model": validated.get("model"),
                },
            )
            audit_recorded = True
            self._fault(fault, "after_audit_write")
            self._journal(
                prepared,
                "accepted",
                revision_id=validated["revision_id"],
                acceptance_mode=mode,
            )
            self._fault(fault, "after_metadata_promotion")
            return {
                "ok": True,
                "revision": validated,
                "validation": validation,
                "acceptance_id": prepared.acceptance_id,
                "acceptance_mode": mode,
            }
        except Exception as exc:
            self._rollback_failed_acceptance(
                prepared,
                exc,
                restore_live=restore_live,
                write_metadata=write_metadata,
                validated=validated,
                head_promoted=head_promoted,
                metadata_promoted=metadata_promoted,
                audit_recorded=audit_recorded,
            )

    def reject(
        self,
        prepared: PreparedAcceptance,
        *,
        restore_live: RestoreLive,
        write_metadata: MetadataWrite,
        reason: str,
        decision_mode: str | None = None,
    ) -> dict[str, Any]:
        """Reject a complete turn and restore its prepared accepted state."""
        mode = self._validate_acceptance_mode(decision_mode) if decision_mode is not None else None
        self._restore_canonical_document(prepared)
        self._restore_project_state(prepared.project_snapshot_path)
        restore_live(prepared.rollback_path)
        current = self.revisions.head()
        current_head = current.get("revision_id") if current else None
        if current_head != prepared.prior_head:
            self.revisions.recover_head(prepared.prior_head, expected_head=current_head)
            write_metadata(prepared.prior_head)
        if mode == "human":
            self.audit.record(
                category="ai_revision",
                action="reject",
                outcome="success",
                actor_type="user",
                details={
                    "acceptance_id": prepared.acceptance_id,
                    "decision_mode": mode,
                    "restored_revision": prepared.prior_head,
                    "reason": str(reason),
                },
            )
        state = "rejected" if mode == "human" else "rolled_back"
        self._journal(prepared, state, error=str(reason), decision_mode=mode)
        return {
            "ok": True,
            "restored_head": prepared.prior_head,
            "reason": str(reason),
            "decision_mode": mode,
        }

    def restore_revision(
        self,
        revision_id: str,
        canonical_path: str | Path,
        *,
        save_copy: SaveCopy,
        validate_document: ValidateDocument,
        restore_live: RestoreLive,
        write_metadata: MetadataWrite,
        fault: FaultInjector | None = None,
    ) -> dict[str, Any]:
        """Restore one accepted artifact as one crash-safe head transition."""
        prepared = self.prepare(canonical_path, save_copy)
        target = self.revisions.read(revision_id)
        head_changed = False
        metadata_changed = False
        audit_recorded = False
        try:
            artifact, document, snapshot = self._resolve_accepted_artifact(target)
            validation = dict(validate_document(document))
            if validation.get("ok") is not True:
                raise RuntimeError("Accepted revision document validation failed.")
            self._journal(prepared, "restore_validated", revision_id=revision_id)
            self._fault(fault, "after_restore_validation")
            self._restore_project_state(snapshot)
            self._journal(prepared, "restore_project_promoted", revision_id=revision_id)
            self._fault(fault, "after_restore_project_promotion")
            _atomic_copy(document, prepared.canonical_path)
            self._journal(prepared, "restore_cad_promoted", revision_id=revision_id)
            self._fault(fault, "after_restore_cad_promotion")
            restore_live(document)
            self._fault(fault, "after_restore_live")
            self.revisions.restore_head(revision_id, expected_head=prepared.prior_head)
            head_changed = True
            self._journal(prepared, "restore_head_promoted", revision_id=revision_id)
            self._fault(fault, "after_restore_head_promotion")
            write_metadata(revision_id)
            metadata_changed = True
            self.audit.record(
                category="revision",
                action="restore",
                outcome="success",
                actor_type="user",
                details={
                    "acceptance_id": prepared.acceptance_id,
                    "revision_id": revision_id,
                    "previous_revision": prepared.prior_head,
                },
            )
            audit_recorded = True
            self._fault(fault, "after_restore_audit_write")
            self._journal(prepared, "restored", revision_id=revision_id)
            self._fault(fault, "after_restore_metadata_promotion")
            return {
                "ok": True,
                "revision": target,
                "validation": validation,
                "accepted_artifact": artifact,
                "acceptance_id": prepared.acceptance_id,
            }
        except Exception as exc:
            rollback_errors: list[str] = []
            try:
                self._restore_canonical_document(prepared)
            except Exception as rollback_exc:
                rollback_errors.append(f"document file: {rollback_exc}")
            try:
                self._restore_project_state(prepared.project_snapshot_path)
            except Exception as rollback_exc:
                rollback_errors.append(f"project state: {rollback_exc}")
            try:
                restore_live(prepared.rollback_path)
            except Exception as rollback_exc:
                rollback_errors.append(f"live document: {rollback_exc}")
            if head_changed:
                try:
                    self.revisions.recover_head(prepared.prior_head, expected_head=revision_id)
                except Exception as rollback_exc:
                    rollback_errors.append(f"revision head: {rollback_exc}")
            if metadata_changed or head_changed:
                try:
                    write_metadata(prepared.prior_head)
                except Exception as rollback_exc:
                    rollback_errors.append(f"project metadata: {rollback_exc}")
            if audit_recorded:
                try:
                    self.audit.record(
                        category="revision",
                        action="restore_reverted",
                        outcome="rolled_back",
                        details={
                            "acceptance_id": prepared.acceptance_id,
                            "revision_id": revision_id,
                            "restored_revision": prepared.prior_head,
                        },
                    )
                except Exception as rollback_exc:
                    rollback_errors.append(f"audit compensation: {rollback_exc}")
            self._journal(
                prepared,
                "rolled_back" if not rollback_errors else "rollback_failed",
                revision_id=revision_id,
                error=str(exc),
                rollback_errors=rollback_errors,
            )
            if rollback_errors:
                raise RuntimeError(f"Revision restore failed and rollback was incomplete: {'; '.join(rollback_errors)}") from exc
            raise RuntimeError(f"Revision restore failed and the prior revision was restored: {exc}") from exc

    def recover_incomplete(
        self,
        *,
        restore_live: RestoreLive,
        write_metadata: MetadataWrite,
    ) -> list[dict[str, Any]]:
        """Restore the prior accepted state for each interrupted promotion."""
        recovered: list[dict[str, Any]] = []
        if not self.directory.is_dir():
            return recovered
        for journal_path in sorted(self.directory.glob("*/journal.json")):
            try:
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"Acceptance journal could not be read: {journal_path}: {exc}") from exc
            if journal.get("schema") != ACCEPTANCE_SCHEMA or journal.get("version") != ACCEPTANCE_VERSION:
                raise RuntimeError(f"Acceptance journal has an unsupported schema: {journal_path}")
            if journal.get("state") in {"accepted", "restored", "rejected", "rolled_back", "recovered", "no_mutation"}:
                continue
            rollback_path = Path(str(journal.get("rollback_document") or ""))
            rollback_record_path = journal_path.parent / "rollback.json"
            try:
                rollback_record = json.loads(rollback_record_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"Rollback record could not be read: {rollback_record_path}: {exc}") from exc
            if rollback_record.get("schema") != ROLLBACK_SCHEMA:
                raise RuntimeError(f"Rollback record has an unsupported schema: {rollback_record_path}")
            if not rollback_path.is_file() or _sha256(rollback_path) != rollback_record.get("sha256"):
                raise RuntimeError(f"Rollback document integrity check failed: {rollback_path}")
            canonical = Path(str(journal.get("canonical_document") or ""))
            prior_head = journal.get("prior_head")
            current = self.revisions.head()
            current_head = current.get("revision_id") if current else None
            revision_id = journal.get("revision_id")
            if revision_id and current_head == revision_id:
                self.revisions.recover_head(prior_head, expected_head=revision_id)
            elif current_head != prior_head:
                raise RuntimeError("Cannot recover acceptance because the revision head changed.")
            snapshot_path = Path(str(rollback_record.get("project_snapshot") or ""))
            prepared = PreparedAcceptance(
                acceptance_id=str(journal["acceptance_id"]),
                directory=journal_path.parent,
                journal_path=journal_path,
                rollback_path=rollback_path,
                candidate_path=Path(str(journal.get("candidate_document") or "")),
                canonical_path=canonical,
                prior_head=prior_head,
                project_snapshot_path=snapshot_path,
                accepted_project_snapshot_path=journal_path.parent / "accepted-project-state",
                accepted_artifact_path=journal_path.parent / "accepted-artifact.json",
            )
            self._restore_canonical_document(prepared)
            self._restore_project_state(snapshot_path)
            restore_live(rollback_path)
            write_metadata(prior_head)
            self._journal(prepared, "recovered", interrupted_state=journal.get("state"))
            recovered.append({"acceptance_id": prepared.acceptance_id, "prior_head": prior_head})
        return recovered

    @staticmethod
    def _validate_acceptance_mode(value: str) -> str:
        mode = str(value or "").strip().lower()
        if mode not in _ACCEPTANCE_MODES:
            raise ValueError("Acceptance mode must be 'automatic' or 'human'.")
        return mode

    def _rollback_record(self, prepared: PreparedAcceptance) -> dict[str, Any]:
        path = prepared.directory / "rollback.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Rollback record could not be read: {path}: {exc}") from exc
        if (
            not isinstance(raw, dict)
            or raw.get("schema") != ROLLBACK_SCHEMA
            or raw.get("version") != ACCEPTANCE_VERSION
            or raw.get("project_id") != self.project_id
            or raw.get("acceptance_id") != prepared.acceptance_id
        ):
            raise RuntimeError("Rollback record has an unsupported identity or schema.")
        return dict(raw)

    def _restore_canonical_document(self, prepared: PreparedAcceptance) -> None:
        """Restore the exact pre-run canonical bytes when they changed."""
        rollback = self._rollback_record(prepared)
        expected_sha256 = str(rollback.get("canonical_sha256") or "")
        if (
            prepared.canonical_path.is_file()
            and expected_sha256
            and _sha256(prepared.canonical_path) == expected_sha256
        ):
            return

        backup_value = str(rollback.get("canonical_backup") or "")
        backup = Path(backup_value) if backup_value else prepared.canonical_backup_path
        backup_sha256 = str(
            rollback.get("canonical_backup_sha256") or expected_sha256
        )
        if backup_value:
            if backup != prepared.canonical_backup_path:
                raise RuntimeError("The canonical rollback backup identity is invalid.")
            if not backup.is_file() or _sha256(backup) != backup_sha256:
                raise RuntimeError("The canonical rollback backup integrity check failed.")
            _atomic_copy(backup, prepared.canonical_path)
            if expected_sha256 and _sha256(prepared.canonical_path) != expected_sha256:
                raise RuntimeError("The canonical CAD document rollback was not byte exact.")
            return

        # Compatibility for acceptance journals created before the exact-byte
        # canonical backup field was added. These records can restore geometry,
        # but their FreeCAD ZIP bytes were not preserved separately.
        _atomic_copy(prepared.rollback_path, prepared.canonical_path)

    @staticmethod
    def _journal_state(prepared: PreparedAcceptance) -> str:
        try:
            raw = json.loads(prepared.journal_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Acceptance journal could not be read: {prepared.journal_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise RuntimeError("Acceptance journal is not a JSON object.")
        return str(raw.get("state") or "")

    def _assert_accepted_identity(
        self,
        prepared: PreparedAcceptance,
        pending: Mapping[str, Any],
    ) -> None:
        if str(pending.get("canonical_document") or "") != str(prepared.canonical_path):
            raise RuntimeError("Validated candidate canonical identity does not match the prepared document.")
        if not prepared.canonical_path.is_file():
            raise RuntimeError("The canonical CAD document changed during candidate review.")
        if _sha256(prepared.canonical_path) != pending.get("canonical_sha256"):
            raise RuntimeError("The canonical CAD document changed during candidate review.")
        current = self.revisions.head()
        current_head = current.get("revision_id") if current else None
        if current_head != prepared.prior_head or current_head != pending.get("prior_head"):
            raise RuntimeError("The accepted revision head changed during candidate review.")
        baseline_hash = _tree_sha256(
            self.project_root,
            exclude_top_level=_SNAPSHOT_EXCLUSIONS,
        )
        if baseline_hash != pending.get("baseline_project_tree_sha256"):
            raise RuntimeError("The accepted project state changed during candidate review.")

    def _read_validated_candidate(self, prepared: PreparedAcceptance) -> dict[str, Any]:
        try:
            pending = json.loads(prepared.validated_candidate_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"Validated candidate record could not be read: {prepared.validated_candidate_path}: {exc}"
            ) from exc
        if (
            not isinstance(pending, dict)
            or pending.get("schema") != VALIDATED_CANDIDATE_SCHEMA
            or pending.get("version") != ACCEPTANCE_VERSION
            or pending.get("project_id") != self.project_id
            or pending.get("acceptance_id") != prepared.acceptance_id
        ):
            raise RuntimeError("Validated candidate has an unsupported identity or schema.")
        try:
            journal = json.loads(prepared.journal_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Acceptance journal could not be read: {prepared.journal_path}: {exc}") from exc
        if journal.get("state") != "awaiting_decision":
            raise RuntimeError("Validated candidate is not awaiting an acceptance decision.")
        if str(pending.get("candidate_document") or "") != str(prepared.candidate_path):
            raise RuntimeError("Validated candidate document identity is invalid.")
        if (
            not prepared.candidate_path.is_file()
            or _sha256(prepared.candidate_path) != pending.get("candidate_sha256")
        ):
            raise RuntimeError("Validated candidate document integrity check failed.")
        if str(pending.get("project_snapshot") or "") != str(prepared.accepted_project_snapshot_path):
            raise RuntimeError("Validated candidate project snapshot identity is invalid.")
        if _tree_sha256(prepared.accepted_project_snapshot_path) != pending.get("project_tree_sha256"):
            raise RuntimeError("Validated candidate project snapshot integrity check failed.")
        self._assert_accepted_identity(prepared, pending)
        validation = pending.get("validation")
        if not isinstance(validation, dict) or validation.get("ok") is not True:
            raise RuntimeError("Validated candidate does not contain a successful validation result.")
        revision = validate_revision_record(pending.get("revision"), project_id=self.project_id)
        if revision.get("parent_revision") != prepared.prior_head:
            raise RuntimeError("Validated candidate parent does not match the prepared head.")
        artifact = revision.get("accepted_artifact")
        try:
            artifact_file = json.loads(prepared.accepted_artifact_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Validated candidate artifact could not be read: {exc}") from exc
        if artifact_file != artifact:
            raise RuntimeError("Validated candidate artifact record does not match its revision.")
        self._resolve_accepted_artifact(revision)
        normalized = dict(pending)
        normalized["validation"] = dict(validation)
        normalized["revision"] = revision
        return normalized

    def _rollback_failed_acceptance(
        self,
        prepared: PreparedAcceptance,
        error: Exception,
        *,
        restore_live: RestoreLive,
        write_metadata: MetadataWrite,
        validated: Mapping[str, Any] | None = None,
        head_promoted: bool = False,
        metadata_promoted: bool = False,
        audit_recorded: bool = False,
    ) -> None:
        rollback_errors: list[str] = []
        try:
            self._restore_canonical_document(prepared)
        except Exception as rollback_exc:
            rollback_errors.append(f"document file: {rollback_exc}")
        try:
            self._restore_project_state(prepared.project_snapshot_path)
        except Exception as rollback_exc:
            rollback_errors.append(f"project state: {rollback_exc}")
        try:
            restore_live(prepared.rollback_path)
        except Exception as rollback_exc:
            rollback_errors.append(f"live document: {rollback_exc}")
        if head_promoted and validated is not None:
            try:
                self.revisions.recover_head(
                    prepared.prior_head,
                    expected_head=str(validated["revision_id"]),
                )
            except Exception as rollback_exc:
                rollback_errors.append(f"revision head: {rollback_exc}")
        if metadata_promoted or head_promoted:
            try:
                write_metadata(prepared.prior_head)
            except Exception as rollback_exc:
                rollback_errors.append(f"project metadata: {rollback_exc}")
        if audit_recorded and validated is not None:
            try:
                self.audit.record(
                    category="ai_revision",
                    action="accept_reverted",
                    outcome="rolled_back",
                    details={
                        "acceptance_id": prepared.acceptance_id,
                        "revision_id": validated["revision_id"],
                        "restored_revision": prepared.prior_head,
                    },
                )
            except Exception as rollback_exc:
                rollback_errors.append(f"audit compensation: {rollback_exc}")
        self._journal(
            prepared,
            "rolled_back" if not rollback_errors else "rollback_failed",
            error=str(error),
            rollback_errors=rollback_errors,
        )
        if rollback_errors:
            raise RuntimeError(
                f"Acceptance failed and rollback was incomplete: {'; '.join(rollback_errors)}"
            ) from error
        raise RuntimeError(
            f"Acceptance failed and the prior revision was restored: {error}"
        ) from error

    @staticmethod
    def _fault(fault: FaultInjector | None, boundary: str) -> None:
        if fault is not None:
            fault(boundary)

    def _journal(self, prepared: PreparedAcceptance, state: str, **fields: Any) -> None:
        payload = {
            "schema": ACCEPTANCE_SCHEMA,
            "version": ACCEPTANCE_VERSION,
            "project_id": self.project_id,
            "acceptance_id": prepared.acceptance_id,
            "state": state,
            "prior_head": prepared.prior_head,
            "canonical_document": str(prepared.canonical_path),
            "rollback_document": str(prepared.rollback_path),
            "candidate_document": str(prepared.candidate_path),
            **fields,
        }
        _atomic_json(prepared.journal_path, payload)

    def complete_without_mutation(self, prepared: PreparedAcceptance) -> None:
        """Close a prepared turn that made no CAD or project mutation."""
        self._journal(prepared, "no_mutation")

    def _write_accepted_artifact(
        self,
        prepared: PreparedAcceptance,
        *,
        acceptance_mode: str | None = None,
    ) -> dict[str, Any]:
        artifact = {
            "schema": ACCEPTED_ARTIFACT_SCHEMA,
            "version": ACCEPTANCE_VERSION,
            "project_id": self.project_id,
            "acceptance_id": prepared.acceptance_id,
            "document": prepared.candidate_path.relative_to(self.project_root).as_posix(),
            "document_sha256": _sha256(prepared.candidate_path),
            "project_snapshot": prepared.accepted_project_snapshot_path.relative_to(self.project_root).as_posix(),
            "project_tree_sha256": _tree_sha256(prepared.accepted_project_snapshot_path),
        }
        if acceptance_mode is not None:
            artifact["acceptance_mode"] = self._validate_acceptance_mode(acceptance_mode)
        _atomic_json(prepared.accepted_artifact_path, artifact)
        return artifact

    def _resolve_accepted_artifact(
        self, record: Mapping[str, Any]
    ) -> tuple[dict[str, Any], Path, Path]:
        artifact = record.get("accepted_artifact")
        if not isinstance(artifact, dict):
            raise RuntimeError("Accepted revision does not have a rollback artifact.")
        if artifact.get("schema") != ACCEPTED_ARTIFACT_SCHEMA or artifact.get("version") != ACCEPTANCE_VERSION:
            raise RuntimeError("Accepted revision artifact has an unsupported schema.")
        if artifact.get("project_id") != self.project_id:
            raise RuntimeError("Accepted revision artifact belongs to a different project.")

        def resolve(field: str) -> Path:
            relative = Path(str(artifact.get(field) or ""))
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"Accepted revision artifact {field} path is unsafe.")
            path = (self.project_root / relative).resolve()
            root = self.project_root.resolve()
            if path != root and root not in path.parents:
                raise RuntimeError(f"Accepted revision artifact {field} path leaves the project.")
            return path

        document = resolve("document")
        snapshot = resolve("project_snapshot")
        if not document.is_file() or _sha256(document) != artifact.get("document_sha256"):
            raise RuntimeError("Accepted revision document integrity check failed.")
        if _tree_sha256(snapshot) != artifact.get("project_tree_sha256"):
            raise RuntimeError("Accepted revision project snapshot integrity check failed.")
        return dict(artifact), document, snapshot

    def _mutable_project_entries(self) -> list[str]:
        if not self.project_root.is_dir():
            return []
        return sorted(
            entry.name
            for entry in self.project_root.iterdir()
            if entry.name not in _SNAPSHOT_EXCLUSIONS
        )

    def _snapshot_project_state(self, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        for name in self._mutable_project_entries():
            source = self.project_root / name
            target = destination / name
            if source.is_dir():
                shutil.copytree(source, target, symlinks=True)
            else:
                shutil.copy2(source, target, follow_symlinks=False)

    def _restore_project_state(self, snapshot: Path) -> None:
        if not snapshot.is_dir():
            raise RuntimeError(f"Project rollback snapshot is missing: {snapshot}")
        baseline = {entry.name for entry in snapshot.iterdir()}
        for entry in self.project_root.iterdir():
            if entry.name in _SNAPSHOT_EXCLUSIONS:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink(missing_ok=True)
        for name in baseline:
            source = snapshot / name
            target = self.project_root / name
            if source.is_dir():
                shutil.copytree(source, target, symlinks=True)
            else:
                shutil.copy2(source, target, follow_symlinks=False)
