# SPDX-License-Identifier: LGPL-2.1-or-later
"""Versioned and corruption-resistant AI revision provenance records."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
import uuid

REVISION_SCHEMA = "vibecad-ai-revision-v1"
REVISION_INDEX_SCHEMA = "vibecad-ai-revision-index-v1"
REVISION_VERSION = 1
_REVISION_ID = re.compile(r"[0-9a-f]{64}")
_TEXT_FIELDS = ("project_id", "user_request", "interpreted_intent", "provider", "model", "timestamp")
_LIST_FIELDS = ("assumptions", "plan", "tool_operations", "changed_objects", "validation_results")
_OPTIONAL_FIELDS = (
    "generated_source", "preview_image", "rollback", "transaction_id",
    "document_revision", "design_brief_revision", "accepted_artifact",
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
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


def _json_value(value: Any, field: str) -> Any:
    try:
        return json.loads(_canonical_json(value).decode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Revision field {field!r} must contain JSON data.") from exc


def revision_content(record: Mapping[str, Any]) -> dict[str, Any]:
    fields = (*_TEXT_FIELDS, *_LIST_FIELDS, *_OPTIONAL_FIELDS)
    content = {field: record.get(field) for field in fields}
    content.update(schema=REVISION_SCHEMA, version=REVISION_VERSION, parent_revision=record.get("parent_revision"))
    return content


def calculate_revision_id(record: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(revision_content(record))).hexdigest()


def create_revision_record(**values: Any) -> dict[str, Any]:
    """Create one immutable accepted-revision record."""
    record: dict[str, Any] = {"schema": REVISION_SCHEMA, "version": REVISION_VERSION}
    for field in _TEXT_FIELDS:
        text = str(values.get(field) or "").strip()
        if not text:
            raise ValueError(f"Revision field {field!r} must not be empty.")
        record[field] = text
    for field in _LIST_FIELDS:
        if not isinstance(values.get(field), list):
            raise ValueError(f"Revision field {field!r} must be an array.")
        record[field] = _json_value(values[field], field)
    parent = values.get("parent_revision")
    record["parent_revision"] = str(parent).strip().lower() if parent else None
    if record["parent_revision"] and not _REVISION_ID.fullmatch(record["parent_revision"]):
        raise ValueError("Parent revision must be a lowercase SHA-256 value.")
    for field in _OPTIONAL_FIELDS:
        record[field] = _json_value(values.get(field), field)
    record["revision_id"] = calculate_revision_id(record)
    return validate_revision_record(record)


def validate_revision_record(raw: Any, *, project_id: str | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError("AI revision record is not a JSON object.")
    if raw.get("schema") != REVISION_SCHEMA or raw.get("version") != REVISION_VERSION:
        raise RuntimeError("AI revision record has an unsupported schema.")
    record = dict(raw)
    for field in _TEXT_FIELDS:
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise RuntimeError(f"AI revision field {field!r} is invalid.")
        record[field] = record[field].strip()
    if project_id is not None and record["project_id"] != str(project_id):
        raise RuntimeError("AI revision belongs to a different project.")
    for field in _LIST_FIELDS:
        if not isinstance(record.get(field), list):
            raise RuntimeError(f"AI revision field {field!r} is not an array.")
    parent = record.get("parent_revision")
    if parent is not None and not _REVISION_ID.fullmatch(str(parent)):
        raise RuntimeError("AI revision parent identity is invalid.")
    if str(record.get("revision_id") or "") != calculate_revision_id(record):
        raise RuntimeError("AI revision identity does not match its content.")
    return record


class VibeCADRevisionStore:
    """Append-only accepted revision records with a guarded current head."""

    def __init__(self, project_root: str | Path, project_id: str) -> None:
        self.project_root = Path(project_root)
        self.project_id = str(project_id)
        self.directory = self.project_root / "revisions"
        self.records_directory = self.directory / "records"
        self.index_path = self.directory / "index.json"

    def record_path(self, revision_id: str) -> Path:
        clean = str(revision_id or "").strip().lower()
        if not _REVISION_ID.fullmatch(clean):
            raise ValueError("Revision identity must be a lowercase SHA-256 value.")
        return self.records_directory / f"{clean}.json"

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        validated = self.stage(record)
        self.promote(validated["revision_id"], expected_head=self._read_index()["head_revision"])
        return validated

    def stage(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Write an immutable record without changing the accepted head."""
        validated = validate_revision_record(dict(record), project_id=self.project_id)
        revision_id = validated["revision_id"]
        path = self.record_path(revision_id)
        if path.exists():
            if self.read(revision_id) != validated:
                raise RuntimeError(f"AI revision {revision_id} already has different content.")
        else:
            _atomic_write_json(path, validated)
        return dict(validated)

    def promote(self, revision_id: str, *, expected_head: str | None) -> dict[str, Any]:
        """Atomically add a staged record to history and make it the head."""
        validated = self.read(revision_id)
        index = self._read_index()
        if index["head_revision"] != expected_head:
            raise RuntimeError("AI revision head changed before promotion.")
        parent = validated.get("parent_revision")
        if parent is not None and parent not in index["revision_ids"]:
            raise RuntimeError(f"Parent AI revision {parent} does not exist.")
        if parent != expected_head:
            raise RuntimeError("AI revision parent does not match the accepted head.")
        if revision_id not in index["revision_ids"]:
            index["revision_ids"].append(revision_id)
        index["head_revision"] = revision_id
        _atomic_write_json(self.index_path, index)
        return validated

    def read(self, revision_id: str) -> dict[str, Any]:
        path = self.record_path(revision_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError(f"AI revision does not exist: {revision_id}") from exc
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"AI revision could not be read from {path}: {exc}") from exc
        return validate_revision_record(raw, project_id=self.project_id)

    def list_records(self) -> list[dict[str, Any]]:
        return [self.read(item) for item in self._read_index()["revision_ids"]]

    def compare(self, left_revision: str, right_revision: str) -> dict[str, Any]:
        """Return a stable, structured comparison of two accepted revisions."""
        left = self.read(left_revision)
        right = self.read(right_revision)
        fields = (
            "user_request", "interpreted_intent", "assumptions", "plan", "tool_operations",
            "changed_objects", "validation_results", "document_revision",
            "design_brief_revision", "accepted_artifact",
        )
        changes = {
            field: {"left": left.get(field), "right": right.get(field)}
            for field in fields
            if left.get(field) != right.get(field)
        }
        def object_name(item: Any) -> str:
            if isinstance(item, dict) and item.get("name"):
                return str(item["name"])
            return json.dumps(item, ensure_ascii=True, sort_keys=True)

        left_objects = set(object_name(item) for item in left.get("changed_objects", []))
        right_objects = set(object_name(item) for item in right.get("changed_objects", []))
        return {
            "left_revision": left["revision_id"],
            "right_revision": right["revision_id"],
            "changed": bool(changes),
            "changes": changes,
            "objects_added": sorted(right_objects - left_objects),
            "objects_removed": sorted(left_objects - right_objects),
            "objects_shared": sorted(left_objects & right_objects),
        }

    def head(self) -> dict[str, Any] | None:
        revision_id = self._read_index()["head_revision"]
        return self.read(revision_id) if revision_id else None

    def restore_head(self, revision_id: str, *, expected_head: str | None) -> dict[str, Any]:
        """Move the head after the caller restores CAD data from rollback data."""
        target = self.read(revision_id)
        index = self._read_index()
        if index["head_revision"] != expected_head:
            raise RuntimeError("AI revision head changed before restore.")
        index["head_revision"] = target["revision_id"]
        _atomic_write_json(self.index_path, index)
        return target

    def recover_head(self, revision_id: str | None, *, expected_head: str | None) -> None:
        """Restore a prior head during a failed multi-file acceptance."""
        index = self._read_index()
        if index["head_revision"] != expected_head:
            raise RuntimeError("AI revision head changed before recovery.")
        if revision_id is not None and revision_id not in index["revision_ids"]:
            raise RuntimeError("Recovered AI revision is not in the revision list.")
        index["head_revision"] = revision_id
        _atomic_write_json(self.index_path, index)

    def _read_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"schema": REVISION_INDEX_SCHEMA, "version": REVISION_VERSION, "project_id": self.project_id, "head_revision": None, "revision_ids": []}
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"AI revision index could not be read from {self.index_path}: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("schema") != REVISION_INDEX_SCHEMA or raw.get("version") != REVISION_VERSION:
            raise RuntimeError("AI revision index has an unsupported schema.")
        if str(raw.get("project_id") or "") != self.project_id:
            raise RuntimeError("AI revision index belongs to a different project.")
        revision_ids = raw.get("revision_ids")
        if not isinstance(revision_ids, list) or len(revision_ids) != len(set(revision_ids)):
            raise RuntimeError("AI revision index has invalid revision identities.")
        if any(not _REVISION_ID.fullmatch(str(item)) for item in revision_ids):
            raise RuntimeError("AI revision index has an invalid revision identity.")
        head = raw.get("head_revision")
        if head is not None and head not in revision_ids:
            raise RuntimeError("AI revision index head is not in the revision list.")
        return {"schema": REVISION_INDEX_SCHEMA, "version": REVISION_VERSION, "project_id": self.project_id, "head_revision": head, "revision_ids": list(revision_ids)}
