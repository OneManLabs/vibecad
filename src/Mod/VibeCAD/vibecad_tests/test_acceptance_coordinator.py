# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from VibeCADAcceptance import VibeCADAcceptanceCoordinator
from VibeCADRevision import create_revision_record


PROJECT_ID = "acceptance-project"


def _record(parent: str | None) -> dict:
    return create_revision_record(
        project_id=PROJECT_ID,
        parent_revision=parent,
        user_request="Add a hole",
        interpreted_intent="Add one validated centered hole.",
        assumptions=[],
        plan=[{"tool": "partdesign.hole"}],
        tool_operations=[{"tool": "partdesign.hole", "ok": True}],
        changed_objects=[{"name": "Hole", "change": "created"}],
        validation_results=[{"name": "reopen", "ok": True}],
        provider="test",
        model="deterministic",
        timestamp="2026-07-22T13:00:00Z",
        generated_source=None,
        preview_image=None,
        rollback={"available": True},
        transaction_id="tx-1",
        document_revision="doc-new",
    )


class _Harness:
    def __init__(self, root: Path) -> None:
        self.live = b"accepted-old"
        self.canonical = root / "part.FCStd"
        self.canonical.write_bytes(self.live)
        self.metadata_head: str | None = None

    def save_copy(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.live)

    @staticmethod
    def validate(path: Path) -> dict:
        return {"ok": path.read_bytes().startswith(b"accepted-")}

    def restore_live(self, path: Path) -> None:
        self.live = path.read_bytes()

    def write_metadata(self, revision_id: str | None) -> None:
        self.metadata_head = revision_id


def test_success_promotes_cad_record_metadata_and_head(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    coordinator = VibeCADAcceptanceCoordinator(tmp_path / "project", PROJECT_ID)
    prepared = coordinator.prepare(harness.canonical, harness.save_copy)
    harness.live = b"accepted-new"
    record = _record(prepared.prior_head)
    result = coordinator.promote(
        prepared,
        record,
        save_copy=harness.save_copy,
        validate_document=harness.validate,
        restore_live=harness.restore_live,
        write_metadata=harness.write_metadata,
    )
    assert result["ok"] is True
    assert harness.canonical.read_bytes() == b"accepted-new"
    accepted = result["revision"]
    assert result["acceptance_mode"] == "automatic"
    assert harness.metadata_head == accepted["revision_id"]
    assert coordinator.revisions.head() == accepted
    artifact = accepted["accepted_artifact"]
    assert artifact["schema"] == "vibecad-accepted-revision-artifact-v1"
    assert artifact["acceptance_mode"] == "automatic"
    assert (coordinator.project_root / artifact["document"]).read_bytes() == b"accepted-new"
    assert (coordinator.project_root / artifact["project_snapshot"]).is_dir()
    audit = coordinator.audit.list_events()
    assert [(item["category"], item["action"], item["outcome"]) for item in audit] == [
        ("ai_revision", "accept", "success")
    ]


@pytest.mark.parametrize(
    "boundary",
    [
        "after_candidate_save",
        "after_candidate_project_snapshot",
        "after_candidate_artifact_write",
        "after_candidate_record_write",
        "after_candidate_validation",
        "after_baseline_project_restore",
        "after_review_state_restored",
        "after_candidate_record_read",
        "after_accepted_artifact_write",
        "after_revision_stage",
        "after_project_promotion",
        "after_cad_promotion",
        "after_head_promotion",
        "after_audit_write",
        "after_metadata_promotion",
    ],
)
def test_fault_at_each_promotion_boundary_restores_prior_state(
    tmp_path: Path, boundary: str
) -> None:
    harness = _Harness(tmp_path)
    coordinator = VibeCADAcceptanceCoordinator(tmp_path / "project", PROJECT_ID)
    prepared = coordinator.prepare(harness.canonical, harness.save_copy)
    harness.live = b"accepted-new"

    def fault(point: str) -> None:
        if point == boundary:
            raise OSError(f"fault at {point}")

    with pytest.raises(RuntimeError, match="prior revision was restored"):
        coordinator.promote(
            prepared,
            _record(prepared.prior_head),
            save_copy=harness.save_copy,
            validate_document=harness.validate,
            restore_live=harness.restore_live,
            write_metadata=harness.write_metadata,
            fault=fault,
        )
    assert harness.live == b"accepted-old"
    assert harness.canonical.read_bytes() == b"accepted-old"
    assert harness.metadata_head is None
    assert coordinator.revisions.head() is None


def test_validation_preserves_accepted_state_and_writes_durable_review(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = project_root / "state.txt"
    state.write_text("accepted-old", encoding="utf-8")
    coordinator = VibeCADAcceptanceCoordinator(project_root, PROJECT_ID)
    prepared = coordinator.prepare(harness.canonical, harness.save_copy)
    canonical_sha256 = hashlib.sha256(harness.canonical.read_bytes()).hexdigest()

    harness.live = b"accepted-new"
    state.write_text("candidate-new", encoding="utf-8")
    generated = project_root / "candidate.step"
    generated.write_text("candidate", encoding="utf-8")
    result = coordinator.validate_candidate(
        prepared,
        _record(None),
        save_copy=harness.save_copy,
        validate_document=harness.validate,
        restore_live=harness.restore_live,
        write_metadata=harness.write_metadata,
    )

    assert result["state"] == "awaiting_decision"
    assert result["prior_head"] is None
    assert result["candidate_sha256"] == hashlib.sha256(b"accepted-new").hexdigest()
    assert isinstance(result["candidate_path"], str)
    assert isinstance(result["project_snapshot_path"], str)
    json.dumps(result)
    assert hashlib.sha256(harness.canonical.read_bytes()).hexdigest() == canonical_sha256
    assert harness.live == b"accepted-new"
    assert state.read_text(encoding="utf-8") == "accepted-old"
    assert not generated.exists()
    assert harness.metadata_head is None
    assert coordinator.revisions.head() is None
    assert coordinator.revisions.list_records() == []
    assert prepared.candidate_path.read_bytes() == b"accepted-new"
    assert (prepared.accepted_project_snapshot_path / "state.txt").read_text(
        encoding="utf-8"
    ) == "candidate-new"
    assert (prepared.accepted_project_snapshot_path / "candidate.step").is_file()

    pending = json.loads(prepared.validated_candidate_path.read_text(encoding="utf-8"))
    assert pending["schema"] == "vibecad-validated-candidate-v1"
    assert pending["prior_head"] is None
    assert pending["canonical_document"] == str(harness.canonical)
    assert pending["canonical_sha256"] == canonical_sha256
    assert pending["candidate_sha256"] == hashlib.sha256(b"accepted-new").hexdigest()
    assert pending["revision"]["parent_revision"] is None
    journal = json.loads(prepared.journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "awaiting_decision"


def test_reject_preserves_exact_canonical_bytes_when_save_copy_reencodes(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    original = harness.canonical.read_bytes()

    def reencoded_save(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"reencoded-archive:" + harness.live)

    coordinator = VibeCADAcceptanceCoordinator(tmp_path / "project", PROJECT_ID)
    prepared = coordinator.prepare(harness.canonical, reencoded_save)
    harness.live = b"accepted-new"
    coordinator.validate_candidate(
        prepared,
        _record(None),
        save_copy=reencoded_save,
        validate_document=lambda _path: {"ok": True},
        restore_live=harness.restore_live,
        write_metadata=harness.write_metadata,
    )
    coordinator.reject(
        prepared,
        restore_live=harness.restore_live,
        write_metadata=harness.write_metadata,
        reason="The user rejected the preview.",
        decision_mode="human",
    )

    assert harness.canonical.read_bytes() == original
    rollback = json.loads((prepared.directory / "rollback.json").read_text(encoding="utf-8"))
    assert Path(rollback["canonical_backup"]).read_bytes() == original
    assert rollback["canonical_backup_sha256"] == hashlib.sha256(original).hexdigest()


def test_failed_partial_promotion_restores_exact_canonical_bytes(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    original = harness.canonical.read_bytes()

    def reencoded_save(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"reencoded-archive:" + harness.live)

    coordinator = VibeCADAcceptanceCoordinator(tmp_path / "project", PROJECT_ID)
    prepared = coordinator.prepare(harness.canonical, reencoded_save)
    harness.live = b"accepted-new"

    with pytest.raises(RuntimeError, match="prior revision was restored"):
        coordinator.promote(
            prepared,
            _record(None),
            save_copy=reencoded_save,
            validate_document=lambda _path: {"ok": True},
            restore_live=harness.restore_live,
            write_metadata=harness.write_metadata,
            fault=lambda point: (_ for _ in ()).throw(OSError("fault"))
            if point == "after_cad_promotion"
            else None,
        )

    assert harness.canonical.read_bytes() == original
    assert coordinator.revisions.head() is None


def test_human_accept_promotes_one_durable_validated_candidate(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = project_root / "state.txt"
    state.write_text("old", encoding="utf-8")
    coordinator = VibeCADAcceptanceCoordinator(project_root, PROJECT_ID)
    prepared = coordinator.prepare(harness.canonical, harness.save_copy)
    harness.live = b"accepted-new"
    state.write_text("new", encoding="utf-8")
    coordinator.validate_candidate(
        prepared,
        _record(None),
        save_copy=harness.save_copy,
        validate_document=harness.validate,
        restore_live=harness.restore_live,
        write_metadata=harness.write_metadata,
    )

    result = coordinator.accept_validated_candidate(
        prepared,
        restore_live=harness.restore_live,
        write_metadata=harness.write_metadata,
        acceptance_mode="human",
    )

    assert result["acceptance_mode"] == "human"
    assert harness.canonical.read_bytes() == b"accepted-new"
    assert state.read_text(encoding="utf-8") == "new"
    assert len(coordinator.revisions.list_records()) == 1
    assert coordinator.revisions.head() == result["revision"]
    assert harness.metadata_head == result["revision"]["revision_id"]
    assert result["revision"]["accepted_artifact"]["acceptance_mode"] == "human"
    event = coordinator.audit.list_events()[-1]
    assert event["action"] == "accept"
    assert event["actor_type"] == "user"
    assert event["details"]["acceptance_mode"] == "human"

    with pytest.raises(RuntimeError, match="not awaiting"):
        coordinator.accept_validated_candidate(
            prepared,
            restore_live=harness.restore_live,
            write_metadata=harness.write_metadata,
            acceptance_mode="human",
        )
    assert harness.canonical.read_bytes() == b"accepted-new"
    assert coordinator.revisions.head() == result["revision"]
    assert len(coordinator.revisions.list_records()) == 1


def test_human_rejection_restores_state_without_staging_revision(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = project_root / "state.txt"
    state.write_text("old", encoding="utf-8")
    coordinator = VibeCADAcceptanceCoordinator(project_root, PROJECT_ID)
    prepared = coordinator.prepare(harness.canonical, harness.save_copy)
    harness.live = b"accepted-new"
    state.write_text("new", encoding="utf-8")
    coordinator.validate_candidate(
        prepared,
        _record(None),
        save_copy=harness.save_copy,
        validate_document=harness.validate,
        restore_live=harness.restore_live,
        write_metadata=harness.write_metadata,
    )

    result = coordinator.reject(
        prepared,
        restore_live=harness.restore_live,
        write_metadata=harness.write_metadata,
        reason="The user rejected the preview.",
        decision_mode="human",
    )

    assert result["decision_mode"] == "human"
    assert harness.live == b"accepted-old"
    assert harness.canonical.read_bytes() == b"accepted-old"
    assert state.read_text(encoding="utf-8") == "old"
    assert harness.metadata_head is None
    assert coordinator.revisions.head() is None
    assert coordinator.revisions.list_records() == []
    event = coordinator.audit.list_events()[-1]
    assert event["action"] == "reject"
    assert event["actor_type"] == "user"
    journal = json.loads(prepared.journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "rejected"


@pytest.mark.parametrize("target", ["candidate", "project_snapshot", "canonical"])
def test_accept_rejects_review_artifact_tampering(
    tmp_path: Path,
    target: str,
) -> None:
    harness = _Harness(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = project_root / "state.txt"
    state.write_text("old", encoding="utf-8")
    coordinator = VibeCADAcceptanceCoordinator(project_root, PROJECT_ID)
    prepared = coordinator.prepare(harness.canonical, harness.save_copy)
    harness.live = b"accepted-new"
    state.write_text("new", encoding="utf-8")
    coordinator.validate_candidate(
        prepared,
        _record(None),
        save_copy=harness.save_copy,
        validate_document=harness.validate,
        restore_live=harness.restore_live,
        write_metadata=harness.write_metadata,
    )
    if target == "candidate":
        prepared.candidate_path.write_bytes(b"tampered-candidate")
    elif target == "project_snapshot":
        (prepared.accepted_project_snapshot_path / "state.txt").write_text(
            "tampered-snapshot",
            encoding="utf-8",
        )
    else:
        harness.canonical.write_bytes(b"tampered-canonical")

    with pytest.raises(RuntimeError, match="prior revision was restored"):
        coordinator.accept_validated_candidate(
            prepared,
            restore_live=harness.restore_live,
            write_metadata=harness.write_metadata,
            acceptance_mode="human",
        )
    assert harness.canonical.read_bytes() == b"accepted-old"
    assert harness.live == b"accepted-old"
    assert state.read_text(encoding="utf-8") == "old"
    assert coordinator.revisions.head() is None
    assert coordinator.revisions.list_records() == []


def test_failed_candidate_save_does_not_advance_head(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    coordinator = VibeCADAcceptanceCoordinator(tmp_path / "project", PROJECT_ID)
    prepared = coordinator.prepare(harness.canonical, harness.save_copy)
    harness.live = b"accepted-new"

    def failed_save(_path: Path) -> None:
        raise OSError("disk full")

    with pytest.raises(RuntimeError, match="prior revision was restored"):
        coordinator.promote(
            prepared,
            _record(None),
            save_copy=failed_save,
            validate_document=harness.validate,
            restore_live=harness.restore_live,
            write_metadata=harness.write_metadata,
        )
    assert coordinator.revisions.head() is None
    assert harness.canonical.read_bytes() == b"accepted-old"


def test_failed_metadata_write_restores_cad_and_head(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    coordinator = VibeCADAcceptanceCoordinator(tmp_path / "project", PROJECT_ID)
    prepared = coordinator.prepare(harness.canonical, harness.save_copy)
    harness.live = b"accepted-new"

    def metadata_write(revision_id: str | None) -> None:
        if revision_id is not None:
            raise OSError("metadata write failed")
        harness.metadata_head = None

    with pytest.raises(RuntimeError, match="prior revision was restored"):
        coordinator.promote(
            prepared,
            _record(None),
            save_copy=harness.save_copy,
            validate_document=harness.validate,
            restore_live=harness.restore_live,
            write_metadata=metadata_write,
        )
    assert coordinator.revisions.head() is None
    assert harness.canonical.read_bytes() == b"accepted-old"
    assert harness.live == b"accepted-old"


def test_invalid_reopen_validation_never_promotes(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    coordinator = VibeCADAcceptanceCoordinator(tmp_path / "project", PROJECT_ID)
    prepared = coordinator.prepare(harness.canonical, harness.save_copy)
    harness.live = b"accepted-new"
    with pytest.raises(RuntimeError, match="prior revision was restored"):
        coordinator.promote(
            prepared,
            _record(None),
            save_copy=harness.save_copy,
            validate_document=lambda _path: {"ok": False},
            restore_live=harness.restore_live,
            write_metadata=harness.write_metadata,
        )
    assert coordinator.revisions.head() is None


def test_failed_promotion_restores_project_sidecars(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    source = project_root / "vibescript" / "model.py"
    source.parent.mkdir()
    source.write_text("old", encoding="utf-8")
    coordinator = VibeCADAcceptanceCoordinator(project_root, PROJECT_ID)
    prepared = coordinator.prepare(harness.canonical, harness.save_copy)
    source.write_text("new", encoding="utf-8")
    (project_root / "generated.step").write_text("partial", encoding="utf-8")
    harness.live = b"accepted-new"

    with pytest.raises(RuntimeError, match="prior revision was restored"):
        coordinator.promote(
            prepared,
            _record(None),
            save_copy=harness.save_copy,
            validate_document=harness.validate,
            restore_live=harness.restore_live,
            write_metadata=harness.write_metadata,
            fault=lambda point: (_ for _ in ()).throw(OSError("fault"))
            if point == "after_candidate_validation"
            else None,
        )
    assert source.read_text(encoding="utf-8") == "old"
    assert not (project_root / "generated.step").exists()


def test_revision_factory_receives_saved_reopen_validation(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    coordinator = VibeCADAcceptanceCoordinator(tmp_path / "project", PROJECT_ID)
    prepared = coordinator.prepare(harness.canonical, harness.save_copy)
    harness.live = b"accepted-new"
    observed = {}

    def factory(validation):
        observed.update(validation)
        return _record(None)

    coordinator.promote(
        prepared,
        factory,
        save_copy=harness.save_copy,
        validate_document=harness.validate,
        restore_live=harness.restore_live,
        write_metadata=harness.write_metadata,
    )
    assert observed == {"ok": True}


def test_process_interruption_is_recovered_from_journal(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    project_root = tmp_path / "project"
    coordinator = VibeCADAcceptanceCoordinator(project_root, PROJECT_ID)
    prepared = coordinator.prepare(harness.canonical, harness.save_copy)
    harness.live = b"accepted-new"

    def crash(point: str) -> None:
        if point == "after_cad_promotion":
            raise SystemExit("simulated process stop")

    with pytest.raises(SystemExit):
        coordinator.promote(
            prepared,
            _record(None),
            save_copy=harness.save_copy,
            validate_document=harness.validate,
            restore_live=harness.restore_live,
            write_metadata=harness.write_metadata,
            fault=crash,
        )
    assert harness.canonical.read_bytes() == b"accepted-new"
    recovered = VibeCADAcceptanceCoordinator(project_root, PROJECT_ID).recover_incomplete(
        restore_live=harness.restore_live,
        write_metadata=harness.write_metadata,
    )
    assert len(recovered) == 1
    assert harness.canonical.read_bytes() == b"accepted-old"
    assert harness.live == b"accepted-old"
    assert coordinator.revisions.head() is None


def test_rejected_multi_tool_turn_restores_prepared_document(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    coordinator = VibeCADAcceptanceCoordinator(tmp_path / "project", PROJECT_ID)
    prepared = coordinator.prepare(harness.canonical, harness.save_copy)
    harness.live = b"accepted-partial-turn"
    result = coordinator.reject(
        prepared,
        restore_live=harness.restore_live,
        write_metadata=harness.write_metadata,
        reason="a later mutation failed validation",
    )
    assert result["ok"] is True
    assert harness.live == b"accepted-old"
    assert harness.canonical.read_bytes() == b"accepted-old"
    assert coordinator.revisions.head() is None


def _accept_state(
    coordinator: VibeCADAcceptanceCoordinator,
    harness: _Harness,
    project_state: Path,
    value: str,
) -> dict:
    prepared = coordinator.prepare(harness.canonical, harness.save_copy)
    harness.live = f"accepted-{value}".encode()
    project_state.write_text(value, encoding="utf-8")
    return coordinator.promote(
        prepared,
        _record(prepared.prior_head),
        save_copy=harness.save_copy,
        validate_document=harness.validate,
        restore_live=harness.restore_live,
        write_metadata=harness.write_metadata,
    )["revision"]


def test_restore_revision_restores_cad_project_head_and_metadata(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    project_state = project_root / "state.txt"
    project_state.write_text("initial", encoding="utf-8")
    coordinator = VibeCADAcceptanceCoordinator(project_root, PROJECT_ID)
    first = _accept_state(coordinator, harness, project_state, "one")
    second = _accept_state(coordinator, harness, project_state, "two")

    result = coordinator.restore_revision(
        first["revision_id"],
        harness.canonical,
        save_copy=harness.save_copy,
        validate_document=harness.validate,
        restore_live=harness.restore_live,
        write_metadata=harness.write_metadata,
    )
    assert result["ok"] is True
    assert harness.canonical.read_bytes() == b"accepted-one"
    assert harness.live == b"accepted-one"
    assert project_state.read_text(encoding="utf-8") == "one"
    assert coordinator.revisions.head()["revision_id"] == first["revision_id"]
    assert harness.metadata_head == first["revision_id"]
    assert second["revision_id"] != first["revision_id"]
    assert coordinator.audit.list_events()[-1]["action"] == "restore"


@pytest.mark.parametrize(
    "boundary",
    [
        "after_restore_validation",
        "after_restore_project_promotion",
        "after_restore_cad_promotion",
        "after_restore_live",
        "after_restore_head_promotion",
        "after_restore_audit_write",
        "after_restore_metadata_promotion",
    ],
)
def test_fault_at_each_restore_boundary_restores_current_revision(
    tmp_path: Path, boundary: str
) -> None:
    harness = _Harness(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    project_state = project_root / "state.txt"
    project_state.write_text("initial", encoding="utf-8")
    coordinator = VibeCADAcceptanceCoordinator(project_root, PROJECT_ID)
    first = _accept_state(coordinator, harness, project_state, "one")
    second = _accept_state(coordinator, harness, project_state, "two")

    def fault(point: str) -> None:
        if point == boundary:
            raise OSError(f"fault at {point}")

    with pytest.raises(RuntimeError, match="prior revision was restored"):
        coordinator.restore_revision(
            first["revision_id"],
            harness.canonical,
            save_copy=harness.save_copy,
            validate_document=harness.validate,
            restore_live=harness.restore_live,
            write_metadata=harness.write_metadata,
            fault=fault,
        )
    assert harness.canonical.read_bytes() == b"accepted-two"
    assert harness.live == b"accepted-two"
    assert project_state.read_text(encoding="utf-8") == "two"
    assert coordinator.revisions.head()["revision_id"] == second["revision_id"]
    assert harness.metadata_head == second["revision_id"]


def test_restore_rejects_tampered_accepted_document(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = project_root / "state.txt"
    state.write_text("initial", encoding="utf-8")
    coordinator = VibeCADAcceptanceCoordinator(project_root, PROJECT_ID)
    first = _accept_state(coordinator, harness, state, "one")
    second = _accept_state(coordinator, harness, state, "two")
    artifact_path = project_root / first["accepted_artifact"]["document"]
    artifact_path.write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="integrity check failed"):
        coordinator.restore_revision(
            first["revision_id"],
            harness.canonical,
            save_copy=harness.save_copy,
            validate_document=harness.validate,
            restore_live=harness.restore_live,
            write_metadata=harness.write_metadata,
        )
    assert harness.canonical.read_bytes() == b"accepted-two"
    assert coordinator.revisions.head()["revision_id"] == second["revision_id"]


def test_interrupted_restore_recovers_current_revision_from_journal(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = project_root / "state.txt"
    state.write_text("initial", encoding="utf-8")
    coordinator = VibeCADAcceptanceCoordinator(project_root, PROJECT_ID)
    first = _accept_state(coordinator, harness, state, "one")
    second = _accept_state(coordinator, harness, state, "two")

    def crash(point: str) -> None:
        if point == "after_restore_head_promotion":
            raise SystemExit("simulated process stop")

    with pytest.raises(SystemExit):
        coordinator.restore_revision(
            first["revision_id"],
            harness.canonical,
            save_copy=harness.save_copy,
            validate_document=harness.validate,
            restore_live=harness.restore_live,
            write_metadata=harness.write_metadata,
            fault=crash,
        )
    recovered = VibeCADAcceptanceCoordinator(project_root, PROJECT_ID).recover_incomplete(
        restore_live=harness.restore_live,
        write_metadata=harness.write_metadata,
    )
    assert len(recovered) == 1
    assert harness.canonical.read_bytes() == b"accepted-two"
    assert harness.live == b"accepted-two"
    assert state.read_text(encoding="utf-8") == "two"
    assert coordinator.revisions.head()["revision_id"] == second["revision_id"]
