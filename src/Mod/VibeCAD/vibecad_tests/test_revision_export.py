# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from VibeCADDesignBrief import empty_design_brief, read_design_brief
from VibeCADIntentMemory import empty_memory, read_memory
from VibeCADProject import PROJECT_SCHEMA, VibeCADConversationStore, VibeCADProjectStore
from VibeCADRevision import VibeCADRevisionStore, create_revision_record
from VibeCADRevisionExport import (
    BRANCH_SCHEMA,
    REPORT_SCHEMA,
    _tree_sha256,
    create_revision_branch,
    create_revision_report,
    resolve_revision_project_snapshot,
)


PROJECT_ID = "revision-export-project"


def _append(
    store: VibeCADRevisionStore,
    *,
    parent=None,
    request="Create base",
    snapshot_files: dict[str, str] | None = None,
):
    artifact_dir = store.project_root / "acceptance" / request.replace(" ", "-")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    document = artifact_dir / "accepted.fcstd"
    document.write_bytes(("CAD:" + request).encode("utf-8"))
    snapshot = artifact_dir / "project-snapshot"
    snapshot.mkdir()
    for name, content in (snapshot_files or {}).items():
        path = snapshot / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    record = create_revision_record(
        project_id=PROJECT_ID,
        parent_revision=parent,
        user_request=request,
        interpreted_intent=request,
        assumptions=[],
        plan=[],
        tool_operations=[],
        changed_objects=[],
        validation_results=[{"name": "shape_valid", "ok": True}],
        provider="offline-test",
        model="deterministic",
        timestamp="2026-07-22T18:00:00Z",
        accepted_artifact={
            "document": document.relative_to(store.project_root).as_posix(),
            "document_sha256": hashlib.sha256(document.read_bytes()).hexdigest(),
            "project_snapshot": snapshot.relative_to(store.project_root).as_posix(),
            "project_tree_sha256": _tree_sha256(snapshot),
        },
    )
    return store.append(record), document


def test_report_is_versioned_content_bound_and_non_overwriting(tmp_path: Path):
    store = VibeCADRevisionStore(tmp_path / "project", PROJECT_ID)
    first, _ = _append(store)
    second, _ = _append(store, parent=first["revision_id"], request="Add holes")
    target = tmp_path / "report.json"

    result = create_revision_report(store, target)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema"] == REPORT_SCHEMA
    assert payload["head_revision"] == second["revision_id"]
    assert payload["revision_ids"] == [first["revision_id"], second["revision_id"]]
    assert result["content_sha256"] == payload["content_sha256"]
    with pytest.raises(FileExistsError):
        create_revision_report(store, target)


def test_report_can_export_selected_records_in_history_order(tmp_path: Path):
    store = VibeCADRevisionStore(tmp_path / "project", PROJECT_ID)
    first, _ = _append(store)
    second, _ = _append(store, parent=first["revision_id"], request="Add holes")

    result = create_revision_report(
        store, tmp_path / "selected.json", revision_ids=[second["revision_id"]]
    )

    assert result["revision_ids"] == [second["revision_id"]]


def test_branch_copies_exact_accepted_cad_and_writes_lineage(tmp_path: Path):
    store = VibeCADRevisionStore(tmp_path / "project", PROJECT_ID)
    revision, source = _append(store)
    target = tmp_path / "branches" / "base-copy.FCStd"

    result = create_revision_branch(store, revision["revision_id"], target)

    assert target.read_bytes() == source.read_bytes()
    lineage = json.loads(Path(result["lineage_path"]).read_text(encoding="utf-8"))
    assert lineage["schema"] == BRANCH_SCHEMA
    assert lineage["source_revision"] == revision["revision_id"]
    assert lineage["branch_document"] == target.name


def test_branch_rejects_tampered_source_and_existing_targets(tmp_path: Path):
    store = VibeCADRevisionStore(tmp_path / "project", PROJECT_ID)
    revision, source = _append(store)
    target = tmp_path / "copy.FCStd"
    source.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="integrity"):
        create_revision_branch(store, revision["revision_id"], target)
    source.write_bytes(b"CAD:Create base")
    target.write_bytes(b"user file")
    with pytest.raises(FileExistsError):
        create_revision_branch(store, revision["revision_id"], target)
    assert target.read_bytes() == b"user file"


def test_lineage_failure_removes_new_branch_document(tmp_path: Path):
    store = VibeCADRevisionStore(tmp_path / "project", PROJECT_ID)
    revision, _ = _append(store)
    target = tmp_path / "copy.FCStd"
    lineage = target.with_suffix(target.suffix + ".vibecad-branch.json")
    lineage.write_text("reserved", encoding="utf-8")

    with pytest.raises(FileExistsError):
        create_revision_branch(store, revision["revision_id"], target)

    assert not target.exists()
    assert lineage.read_text(encoding="utf-8") == "reserved"


def test_project_branch_migrates_design_intent_conversation_and_identity(
    tmp_path: Path, monkeypatch
):
    source_root = tmp_path / "source-project"
    store = VibeCADRevisionStore(source_root, PROJECT_ID)
    brief = empty_design_brief(PROJECT_ID)
    brief["purpose"] = "Hold a camera"
    from VibeCADDesignBrief import brief_revision

    brief["revision"] = brief_revision(brief)
    memory = empty_memory(PROJECT_ID)
    snapshot_files = {
        "project.vibecad.json": json.dumps(
            {
                "schema": PROJECT_SCHEMA,
                "version": 2,
                "project_id": PROJECT_ID,
                "title": "Camera mount",
                "summary": "A source project",
                "modeling_engine": "native",
                "accepted_revision": None,
            }
        ),
        "design-brief.json": json.dumps(brief),
        "intent-memory.json": json.dumps(memory),
        "design.md": "# Camera mount\n",
    }
    revision, _ = _append(store, snapshot_files=snapshot_files)
    conversations = VibeCADConversationStore(source_root)
    active = conversations.active_history()
    conversations.write_conversation(
        active["conversation_id"],
        [
            {
                "role": "user",
                "content": "Create a camera mount.",
                "sequence": 1,
                "turn_id": "1" * 32,
            }
        ],
    )
    project = VibeCADProjectStore("source-session", index_path=tmp_path / "index.sqlite")
    source_scope = {
        "project_id": PROJECT_ID,
        "root": str(source_root),
        "manifest_path": str(source_root / "project.vibecad.json"),
    }
    monkeypatch.setattr(project, "project_scope", lambda: source_scope)
    monkeypatch.setattr(project, "revision_store", lambda: store)
    monkeypatch.setenv("VIBECAD_HOME", str(tmp_path / "vibecad-home"))
    target = tmp_path / "branches" / "camera-copy.FCStd"

    result = project.create_revision_branch(revision["revision_id"], target)

    target_root = Path(result["project_root"])
    manifest = json.loads((target_root / "project.vibecad.json").read_text())
    assert manifest["project_id"] == result["target_project_id"]
    assert manifest["accepted_revision"] is None
    assert manifest["branch_origin"]["source_revision"] == revision["revision_id"]
    assert read_design_brief(target_root, result["target_project_id"])["purpose"] == "Hold a camera"
    assert read_memory(target_root, result["target_project_id"])["project_id"] == result["target_project_id"]
    histories = VibeCADConversationStore(target_root).all_histories()
    assert histories[0]["conversation"][0]["content"] == "Create a camera mount."
    assert (target_root / "design.md").read_text() == "# Camera mount\n"


def test_project_branch_migration_failure_removes_all_new_artifacts(
    tmp_path: Path, monkeypatch
):
    source_root = tmp_path / "source-project"
    store = VibeCADRevisionStore(source_root, PROJECT_ID)
    revision, _ = _append(
        store, snapshot_files={"design-brief.json": "not-json"}
    )
    project = VibeCADProjectStore("source-session", index_path=tmp_path / "index.sqlite")
    monkeypatch.setattr(
        project,
        "project_scope",
        lambda: {
            "project_id": PROJECT_ID,
            "root": str(source_root),
            "manifest_path": str(source_root / "project.vibecad.json"),
        },
    )
    monkeypatch.setattr(project, "revision_store", lambda: store)
    monkeypatch.setenv("VIBECAD_HOME", str(tmp_path / "vibecad-home"))
    target = tmp_path / "broken.FCStd"

    with pytest.raises(RuntimeError, match="design brief"):
        project.create_revision_branch(revision["revision_id"], target)

    assert not target.exists()
    assert not target.with_suffix(target.suffix + ".vibecad-branch.json").exists()


def test_project_snapshot_rejects_a_link_outside_the_snapshot(tmp_path: Path):
    source_root = tmp_path / "source-project"
    store = VibeCADRevisionStore(source_root, PROJECT_ID)
    revision, _ = _append(store)
    snapshot = source_root / revision["accepted_artifact"]["project_snapshot"]
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    (snapshot / "unsafe-link").symlink_to(outside)
    record_path = store.record_path(revision["revision_id"])
    raw = json.loads(record_path.read_text())
    raw["accepted_artifact"]["project_tree_sha256"] = _tree_sha256(snapshot)
    from VibeCADRevision import calculate_revision_id

    raw["revision_id"] = calculate_revision_id(raw)
    record_path.unlink()
    new_path = store.record_path(raw["revision_id"])
    new_path.write_text(json.dumps(raw), encoding="utf-8")
    index = json.loads(store.index_path.read_text())
    index["revision_ids"] = [raw["revision_id"]]
    index["head_revision"] = raw["revision_id"]
    store.index_path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(RuntimeError, match="unsafe link"):
        resolve_revision_project_snapshot(store, raw["revision_id"])
