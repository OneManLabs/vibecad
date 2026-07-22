# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import json
from pathlib import Path

import pytest

from VibeCADRevision import REVISION_INDEX_SCHEMA, VibeCADRevisionStore, create_revision_record, validate_revision_record

PROJECT_ID = "project-1"


def _record(parent: str | None = None, *, request: str = "Create a bracket") -> dict:
    return create_revision_record(
        project_id=PROJECT_ID,
        parent_revision=parent,
        user_request=request,
        interpreted_intent="Make an editable wall bracket.",
        assumptions=[{"name": "material", "value": "PETG"}],
        plan=[{"operation": "pad", "status": "complete"}],
        tool_operations=[{"tool": "partdesign.pad", "ok": True}],
        changed_objects=[{"name": "Pad", "change": "created"}],
        validation_results=[{"name": "shape_valid", "ok": True}],
        provider="offline-test",
        model="deterministic",
        timestamp="2026-07-22T12:00:00Z",
        generated_source=None,
        preview_image="previews/revision.png",
        rollback={"available": True, "transaction_id": "tx-1"},
        transaction_id="tx-1",
        document_revision="document-sha",
    )


def test_record_identity_is_stable_and_content_bound() -> None:
    assert _record()["revision_id"] == _record()["revision_id"]
    assert _record()["revision_id"] != _record(request="Create a larger bracket")["revision_id"]


def test_store_appends_parent_chain_and_restores_head(tmp_path: Path) -> None:
    store = VibeCADRevisionStore(tmp_path, PROJECT_ID)
    first = store.append(_record())
    second = store.append(_record(first["revision_id"], request="Add two holes"))
    assert store.head() == second
    assert store.list_records() == [first, second]
    assert store.restore_head(first["revision_id"], expected_head=second["revision_id"]) == first
    assert store.head() == first


def test_store_rejects_missing_parent(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="does not exist"):
        VibeCADRevisionStore(tmp_path, PROJECT_ID).append(_record("a" * 64))


def test_store_rejects_stale_restore(tmp_path: Path) -> None:
    store = VibeCADRevisionStore(tmp_path, PROJECT_ID)
    first = store.append(_record())
    with pytest.raises(RuntimeError, match="head changed"):
        store.restore_head(first["revision_id"], expected_head=None)


def test_record_tamper_is_detected() -> None:
    record = _record()
    record["user_request"] = "Tampered"
    with pytest.raises(RuntimeError, match="identity does not match"):
        validate_revision_record(record, project_id=PROJECT_ID)


def test_project_scope_is_enforced(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="different project"):
        VibeCADRevisionStore(tmp_path, "other-project").append(_record())


def test_corrupt_index_is_not_silently_replaced(tmp_path: Path) -> None:
    store = VibeCADRevisionStore(tmp_path, PROJECT_ID)
    store.directory.mkdir(parents=True)
    store.index_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="could not be read"):
        store.list_records()


def test_index_has_versioned_schema(tmp_path: Path) -> None:
    store = VibeCADRevisionStore(tmp_path, PROJECT_ID)
    record = store.append(_record())
    index = json.loads(store.index_path.read_text(encoding="utf-8"))
    assert index["schema"] == REVISION_INDEX_SCHEMA
    assert index["head_revision"] == record["revision_id"]


def test_compare_reports_content_and_changed_object_differences(tmp_path: Path) -> None:
    store = VibeCADRevisionStore(tmp_path, PROJECT_ID)
    first = store.append(_record())
    second = store.append(_record(first["revision_id"], request="Add two holes"))
    comparison = store.compare(first["revision_id"], second["revision_id"])
    assert comparison["left_revision"] == first["revision_id"]
    assert comparison["right_revision"] == second["revision_id"]
    assert comparison["changed"] is True
    assert comparison["changes"]["user_request"]["right"] == "Add two holes"
    assert comparison["objects_shared"] == ["Pad"]
