# SPDX-License-Identifier: LGPL-2.1-or-later
"""Versioned structured design intent for one local VibeCAD project."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping
import uuid


DESIGN_BRIEF_SCHEMA = "vibecad-design-brief-v1"
DESIGN_BRIEF_VERSION = 1
DESIGN_BRIEF_NAME = "design-brief.json"
LIST_FIELDS = (
    "critical_dimensions", "named_parameters", "symmetry", "mating_parts",
    "clearances", "materials", "loads", "tolerances", "surface_requirements",
    "environmental_requirements", "unresolved_decisions", "assumptions",
    "validation_requirements",
)


def _content(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": DESIGN_BRIEF_SCHEMA,
        "version": DESIGN_BRIEF_VERSION,
        "project_id": str(payload.get("project_id") or ""),
        "purpose": str(payload.get("purpose") or ""),
        "units": str(payload.get("units") or "mm"),
        "manufacturing_process": str(payload.get("manufacturing_process") or "unspecified"),
        "user_preferences": dict(payload.get("user_preferences") or {}),
        **{field: list(payload.get(field) or []) for field in LIST_FIELDS},
    }


def brief_revision(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(_content(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def empty_design_brief(project_id: str) -> dict[str, Any]:
    payload = _content({"project_id": project_id})
    payload["revision"] = brief_revision(payload)
    return payload


def validate_design_brief(raw: Any, *, project_id: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError("The design brief is not a JSON object.")
    if raw.get("schema") != DESIGN_BRIEF_SCHEMA or raw.get("version") != DESIGN_BRIEF_VERSION:
        raise RuntimeError("The design brief has an unsupported schema.")
    payload = _content(raw)
    if payload["project_id"] != str(project_id):
        raise RuntimeError("The design brief belongs to a different project.")
    for field in LIST_FIELDS:
        if not isinstance(raw.get(field, []), list):
            raise RuntimeError(f"The design brief field {field} must be an array.")
    expected = brief_revision(payload)
    if raw.get("revision") and raw.get("revision") != expected:
        raise RuntimeError("The design brief revision does not match its content.")
    payload["revision"] = expected
    return payload


def migrate_intent_memory(memory: Mapping[str, Any], *, project_id: str) -> dict[str, Any]:
    brief = empty_design_brief(project_id)
    category_map = {
        "outcome": "purpose", "requirement": "validation_requirements",
        "constraint": "validation_requirements", "manufacturing": "manufacturing_process",
        "assumption": "assumptions", "open_question": "unresolved_decisions",
    }
    for entry in memory.get("entries") or []:
        if entry.get("status") != "active":
            continue
        statement = str(entry.get("statement") or "").strip()
        target = category_map.get(str(entry.get("category") or ""))
        if not statement or not target:
            continue
        if target in {"purpose", "manufacturing_process"}:
            if not brief[target] or brief[target] == "unspecified":
                brief[target] = statement
        elif statement not in brief[target]:
            brief[target].append(statement)
    brief["revision"] = brief_revision(brief)
    return brief


def read_design_brief(project_root: str | Path, project_id: str, *, legacy_memory: Mapping[str, Any] | None = None) -> dict[str, Any]:
    path = Path(project_root) / DESIGN_BRIEF_NAME
    if not path.exists():
        payload = migrate_intent_memory(legacy_memory, project_id=project_id) if legacy_memory else empty_design_brief(project_id)
        return {**payload, "exists": False, "path": str(path)}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"The design brief could not be read: {exc}") from exc
    return {**validate_design_brief(raw, project_id=project_id), "exists": True, "path": str(path)}


def write_design_brief(project_root: str | Path, payload: Mapping[str, Any], *, project_id: str) -> dict[str, Any]:
    validated = validate_design_brief(dict(payload), project_id=project_id)
    path = Path(project_root) / DESIGN_BRIEF_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(validated, stream, indent=2, sort_keys=True, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {**validated, "exists": True, "path": str(path)}


def apply_design_brief_update(
    current: Mapping[str, Any], update: Mapping[str, Any], *, project_id: str
) -> dict[str, Any]:
    """Apply one optimistic full-field patch to a validated design brief."""
    clean = validate_design_brief(dict(current), project_id=project_id)
    if str(update.get("base_revision") or "") != clean["revision"]:
        raise RuntimeError("The design brief update used a stale base revision.")
    allowed = {"purpose", "units", "manufacturing_process", "user_preferences", *LIST_FIELDS}
    changes = update.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise RuntimeError("The design brief update has no changes.")
    unknown = set(changes) - allowed
    if unknown:
        raise RuntimeError("The design brief update has unknown fields: " + ", ".join(sorted(unknown)))
    candidate = dict(clean)
    candidate.update(changes)
    candidate.pop("revision", None)
    candidate["revision"] = brief_revision(candidate)
    return validate_design_brief(candidate, project_id=project_id)


def ensure_migrated_design_brief(
    project_root: str | Path, project_id: str, legacy_memory: Mapping[str, Any]
) -> dict[str, Any]:
    """Persist the first design brief and retain an exact legacy backup."""
    root = Path(project_root)
    existing = read_design_brief(root, project_id, legacy_memory=legacy_memory)
    if existing["exists"]:
        return existing
    migration = root / "migrations" / DESIGN_BRIEF_SCHEMA
    migration.mkdir(parents=True, exist_ok=True)
    legacy_path = root / "intent-memory.json"
    backup_path = migration / "intent-memory.json"
    if legacy_path.is_file() and not backup_path.exists():
        temporary = backup_path.with_name(f".{backup_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copy2(legacy_path, temporary)
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, backup_path)
        finally:
            temporary.unlink(missing_ok=True)
    saved = write_design_brief(root, existing, project_id=project_id)
    marker = {
        "schema": "vibecad-migration-record-v1",
        "migration": DESIGN_BRIEF_SCHEMA,
        "source_preserved": True,
        "source_backup": str(backup_path) if backup_path.exists() else None,
        "design_brief_revision": saved["revision"],
    }
    marker_path = migration / "migration.json"
    temporary = marker_path.with_name(f".{marker_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(marker, stream, indent=2, sort_keys=True, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker_path)
    finally:
        temporary.unlink(missing_ok=True)
    return saved
