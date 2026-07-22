# SPDX-License-Identifier: LGPL-2.1-or-later
from pathlib import Path
import json
import pytest

from VibeCADDesignBrief import (
    apply_design_brief_update,
    empty_design_brief,
    ensure_migrated_design_brief,
    migrate_intent_memory,
    read_design_brief,
    write_design_brief,
)


def test_empty_brief_round_trip_is_content_bound(tmp_path: Path) -> None:
    brief = empty_design_brief("p1")
    saved = write_design_brief(tmp_path, brief, project_id="p1")
    assert read_design_brief(tmp_path, "p1")["revision"] == saved["revision"]


def test_tampered_brief_is_rejected(tmp_path: Path) -> None:
    write_design_brief(tmp_path, empty_design_brief("p1"), project_id="p1")
    path = tmp_path / "design-brief.json"
    raw = json.loads(path.read_text())
    raw["purpose"] = "tampered"
    path.write_text(json.dumps(raw))
    with pytest.raises(RuntimeError, match="revision"):
        read_design_brief(tmp_path, "p1")


def test_legacy_intent_migration_preserves_active_meaning() -> None:
    memory = {"entries": [
        {"category": "outcome", "statement": "Make a bracket", "status": "active"},
        {"category": "assumption", "statement": "Use aluminium", "status": "active"},
        {"category": "constraint", "statement": "Hold 20 kg", "status": "active"},
        {"category": "assumption", "statement": "Old value", "status": "superseded"},
    ]}
    brief = migrate_intent_memory(memory, project_id="p1")
    assert brief["purpose"] == "Make a bracket"
    assert brief["assumptions"] == ["Use aluminium"]
    assert brief["validation_requirements"] == ["Hold 20 kg"]


def test_migration_persists_backup_and_keeps_legacy_source(tmp_path: Path) -> None:
    legacy = {"schema": "vibecad-intent-memory-v1", "entries": []}
    source = tmp_path / "intent-memory.json"
    source.write_text(json.dumps(legacy), encoding="utf-8")
    saved = ensure_migrated_design_brief(tmp_path, "p1", legacy)
    backup = tmp_path / "migrations" / "vibecad-design-brief-v1" / "intent-memory.json"
    marker = tmp_path / "migrations" / "vibecad-design-brief-v1" / "migration.json"
    assert saved["exists"] is True
    assert source.read_text(encoding="utf-8") == json.dumps(legacy)
    assert backup.read_text(encoding="utf-8") == json.dumps(legacy)
    assert json.loads(marker.read_text(encoding="utf-8"))["source_preserved"] is True


def test_optimistic_update_rejects_stale_revision() -> None:
    brief = empty_design_brief("p1")
    changed = apply_design_brief_update(
        brief,
        {"base_revision": brief["revision"], "changes": {"purpose": "Make a bracket"}},
        project_id="p1",
    )
    assert changed["purpose"] == "Make a bracket"
    with pytest.raises(RuntimeError, match="stale"):
        apply_design_brief_update(
            changed,
            {"base_revision": brief["revision"], "changes": {"units": "inch"}},
            project_id="p1",
        )


def test_provider_tool_returns_structured_state_change() -> None:
    from tool_impl.service import project_update_design_brief as tool

    class Service:
        @staticmethod
        def apply_design_brief_update(update):
            return {
                **empty_design_brief("p1"),
                "purpose": update["changes"]["purpose"],
                "path": "/private/project/design-brief.json",
                "exists": True,
            }

    result = tool.run(Service(), "a" * 64, {"purpose": "Make a bracket"})
    assert result["ok"] is True
    assert result["state_change"]["changed"] == ["project.design_brief"]
    assert "path" not in result["design_brief"]
