# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import json
from pathlib import Path
import types
from datetime import datetime, timezone

import pytest

import VibeCADAudit as audit
from VibeCADAudit import (
    VibeCADAuditStore, create_audit_event, validate_audit_archive,
    validate_audit_event,
)
from VibeCADCore import VibeCADService


def test_audit_event_redacts_secrets_prompts_and_geometry() -> None:
    event = create_audit_event(
        project_id="project-1",
        category="policy",
        action="deny",
        outcome="blocked",
        details={
            "api_key": "secret-key",
            "user_prompt": "confidential request",
            "geometry_payload": "BREP DATA",
            "safe": {"provider": "openai", "token": "secret-token"},
        },
        timestamp="2026-07-22T12:00:00Z",
    )
    assert event["details"]["api_key"] == "[REDACTED]"
    assert event["details"]["user_prompt"] == "[REDACTED]"
    assert event["details"]["geometry_payload"] == "[REDACTED]"
    assert event["details"]["safe"]["token"] == "[REDACTED]"
    assert event["details"]["safe"]["provider"] == "openai"
    assert "secret" not in json.dumps(event)


def test_audit_store_persists_and_reopens_in_order(tmp_path: Path) -> None:
    store = VibeCADAuditStore(tmp_path, "project-1")
    first = store.record(
        category="authentication", action="sign_in", outcome="success",
        timestamp="2026-07-22T12:00:00Z", details={"provider": "openai"},
    )
    second = store.record(
        category="export", action="step", outcome="blocked",
        timestamp="2026-07-22T12:00:01Z", details={"reason": "policy"},
    )
    assert VibeCADAuditStore(tmp_path, "project-1").list_events() == [first, second]


def test_audit_tamper_is_detected_on_reopen(tmp_path: Path) -> None:
    store = VibeCADAuditStore(tmp_path, "project-1")
    store.record(category="policy", action="deny", outcome="blocked")
    path = next(store.directory.glob("*.json"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["outcome"] = "allowed"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RuntimeError, match="identity does not match"):
        store.list_events()


def test_audit_project_scope_and_unredacted_sensitive_data_are_rejected() -> None:
    event = create_audit_event(
        project_id="project-1", category="policy", action="deny", outcome="blocked"
    )
    with pytest.raises(RuntimeError, match="different project"):
        validate_audit_event(event, project_id="project-2")
    event["details"] = {"password": "plain text"}
    event["event_id"] = __import__("hashlib").sha256(
        __import__("VibeCADAudit")._canonical(__import__("VibeCADAudit").audit_content(event))
    ).hexdigest()
    with pytest.raises(RuntimeError, match="unredacted"):
        validate_audit_event(event, project_id="project-1")


def test_audit_denial_survives_unresolved_enterprise_identity(tmp_path: Path) -> None:
    service = types.SimpleNamespace(
        project_scope_snapshot=lambda: {"root": str(tmp_path), "project_id": "project-1"},
        enterprise_principal=lambda: (_ for _ in ()).throw(PermissionError("no session")),
    )
    event = VibeCADService.record_audit_event(
        service,
        category="authorization",
        action="export",
        outcome="blocked",
        actor_type="user",
    )
    assert event["details"]["identity_status"] == "unresolved"
    assert event["details"]["roles"] == []
    assert len(event["details"]["actor_id"]) == 64


def test_retention_archives_before_removing_live_event_files(tmp_path: Path) -> None:
    store = VibeCADAuditStore(tmp_path, "project-1")
    old = store.record(
        category="access", action="open", outcome="allowed",
        timestamp="2025-01-01T00:00:00Z",
    )
    recent = store.record(
        category="access", action="review", outcome="allowed",
        timestamp="2026-07-21T00:00:00Z",
    )
    archive = store.apply_retention(
        live_days=30, max_live_events=100,
        now=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )
    assert archive["events"] == [old]
    assert validate_audit_archive(archive, project_id="project-1") == archive
    assert store.list_events() == [old, recent]
    assert len(list(store.directory.glob("*.json"))) == 1
    assert len(store.list_archives()) == 1


def test_retention_archive_promotion_failure_preserves_all_live_events(monkeypatch, tmp_path: Path) -> None:
    store = VibeCADAuditStore(tmp_path, "project-1")
    event = store.record(
        category="access", action="open", outcome="allowed",
        timestamp="2025-01-01T00:00:00Z",
    )
    monkeypatch.setattr(audit.os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("fault")))
    with pytest.raises(OSError, match="fault"):
        store.apply_retention(
            live_days=30, max_live_events=100,
            now=datetime(2026, 7, 22, tzinfo=timezone.utc),
        )
    assert store.list_events() == [event]
    assert store.list_archives() == []


def test_retention_interrupted_cleanup_keeps_complete_deduplicated_evidence(monkeypatch, tmp_path: Path) -> None:
    store = VibeCADAuditStore(tmp_path, "project-1")
    events = [
        store.record(
            category="access", action=f"step-{index}", outcome="allowed",
            timestamp=f"2025-01-0{index + 1}T00:00:00Z",
        )
        for index in range(2)
    ]
    original = Path.unlink
    calls = {"events": 0}

    def interrupted(path, *args, **kwargs):
        if path.parent == store.directory:
            calls["events"] += 1
            if calls["events"] == 2:
                raise OSError("interrupted cleanup")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupted)
    with pytest.raises(OSError, match="interrupted cleanup"):
        store.apply_retention(
            live_days=30, max_live_events=100,
            now=datetime(2026, 7, 22, tzinfo=timezone.utc),
        )
    assert store.list_events() == events
    assert len(store.list_archives()) == 1
