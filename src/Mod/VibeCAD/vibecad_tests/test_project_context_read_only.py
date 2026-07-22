# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import json
from pathlib import Path

import VibeCADProject as project


def _scope(root: Path) -> dict:
    return {
        "project_id": "project-read-only",
        "title": "Read-only project",
        "root": str(root),
        "manifest_path": str(root / "project.vibecad.json"),
        "persistent": True,
        "document_saved": True,
        "document": {
            "document": "Design",
            "label": "Design",
            "file_path": str(root.parent / "Design.FCStd"),
            "saved": True,
        },
        "index_path": str(root.parent / "index.sqlite"),
    }


def test_existing_project_context_read_does_not_rewrite_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "project"
    scope = _scope(root)
    store = project.VibeCADProjectStore(
        "test-session", index_path=tmp_path / "index.sqlite"
    )
    monkeypatch.setattr(store, "project_scope", lambda: dict(scope))
    monkeypatch.setattr(project, "now_iso", lambda: "2026-07-22T20:00:00Z")
    store.save_manifest(store._default_manifest(scope))
    manifest_path = Path(scope["manifest_path"])
    before = manifest_path.read_bytes()

    monkeypatch.setattr(project, "now_iso", lambda: "2026-07-22T20:01:00Z")
    context = store.context()

    assert context["project_id"] == scope["project_id"]
    assert manifest_path.read_bytes() == before


def test_project_context_creates_missing_manifest_once(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "project"
    scope = _scope(root)
    store = project.VibeCADProjectStore(
        "test-session", index_path=tmp_path / "index.sqlite"
    )
    monkeypatch.setattr(store, "project_scope", lambda: dict(scope))

    store.context()

    manifest_path = Path(scope["manifest_path"])
    assert manifest_path.is_file()
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["schema"] == project.PROJECT_SCHEMA
    assert saved["version"] == 2


def test_project_context_persists_legacy_manifest_migration(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    scope = _scope(root)
    manifest_path = Path(scope["manifest_path"])
    manifest_path.write_text(
        json.dumps(
            {
                "schema": project.PROJECT_SCHEMA,
                "version": 1,
                "project_id": scope["project_id"],
                "partdesign_engine": "openscad",
                "title": scope["title"],
            }
        ),
        encoding="utf-8",
    )
    store = project.VibeCADProjectStore(
        "test-session", index_path=tmp_path / "index.sqlite"
    )
    monkeypatch.setattr(store, "project_scope", lambda: dict(scope))

    context = store.context()

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert context["modeling_engine"] == "openscad"
    assert saved["version"] == 2
    assert saved["modeling_engine"] == "openscad"
    assert "partdesign_engine" not in saved
