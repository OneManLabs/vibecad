# SPDX-License-Identifier: LGPL-2.1-or-later
"""Content-bound enterprise audit events with strict data redaction."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Mapping
import uuid


AUDIT_SCHEMA = "vibecad-audit-event-v1"
AUDIT_VERSION = 1
AUDIT_ARCHIVE_SCHEMA = "vibecad-audit-archive-v1"
AUDIT_ARCHIVE_VERSION = 1
_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|credential|password|secret|token|prompt|"
    r"user[_-]?request|geometry|image[_-]?(?:data|bytes|payload))",
    re.IGNORECASE,
)
_RETENTION_CHECKS: dict[str, float] = {}
_RETENTION_LOCK = threading.Lock()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sanitize(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if depth > 8:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:256] + ("...[TRUNCATED]" if len(value) > 256 else "")
    if isinstance(value, Mapping):
        return {
            str(item_key)[:80]: _sanitize(item, key=str(item_key), depth=depth + 1)
            for item_key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth=depth + 1) for item in list(value)[:50]]
    return str(value)[:256]


def audit_content(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": AUDIT_SCHEMA,
        "version": AUDIT_VERSION,
        "timestamp": record.get("timestamp"),
        "project_id": record.get("project_id"),
        "category": record.get("category"),
        "action": record.get("action"),
        "outcome": record.get("outcome"),
        "actor_type": record.get("actor_type"),
        "details": record.get("details"),
    }


def create_audit_event(
    *,
    project_id: str,
    category: str,
    action: str,
    outcome: str,
    details: Mapping[str, Any] | None = None,
    actor_type: str = "application",
    timestamp: str | None = None,
) -> dict[str, Any]:
    record = {
        "schema": AUDIT_SCHEMA,
        "version": AUDIT_VERSION,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "project_id": str(project_id or "").strip(),
        "category": str(category or "").strip(),
        "action": str(action or "").strip(),
        "outcome": str(outcome or "").strip(),
        "actor_type": str(actor_type or "application").strip(),
        "details": _sanitize(dict(details or {})),
    }
    for field in ("project_id", "category", "action", "outcome", "actor_type", "timestamp"):
        if not record[field]:
            raise ValueError(f"Audit field {field!r} must not be empty.")
    record["event_id"] = hashlib.sha256(_canonical(audit_content(record))).hexdigest()
    return validate_audit_event(record, project_id=record["project_id"])


def validate_audit_event(raw: Any, *, project_id: str | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema") != AUDIT_SCHEMA or raw.get("version") != AUDIT_VERSION:
        raise RuntimeError("The audit event schema is invalid.")
    record = dict(raw)
    if project_id is not None and record.get("project_id") != str(project_id):
        raise RuntimeError("The audit event belongs to a different project.")
    expected = hashlib.sha256(_canonical(audit_content(record))).hexdigest()
    if record.get("event_id") != expected:
        raise RuntimeError("The audit event identity does not match its content.")
    if _SENSITIVE_KEY.search(json.dumps(record.get("details") or {}, sort_keys=True)):
        # Keys can remain visible for review, but their values must be redacted.
        def check(value: Any, key: str = "") -> bool:
            if _SENSITIVE_KEY.search(key):
                return value == "[REDACTED]"
            if isinstance(value, dict):
                return all(check(item, str(item_key)) for item_key, item in value.items())
            if isinstance(value, list):
                return all(check(item) for item in value)
            return True
        if not check(record.get("details") or {}):
            raise RuntimeError("The audit event contains unredacted sensitive data.")
    return record


def _archive_content(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "archive_id"}


def create_audit_archive(
    *, project_id: str, events: list[Mapping[str, Any]],
    previous_archive_id: str | None = None, created_at: str | None = None,
) -> dict[str, Any]:
    clean = [validate_audit_event(dict(event), project_id=project_id) for event in events]
    if not clean:
        raise ValueError("An audit archive must contain at least one event.")
    clean.sort(key=lambda event: (event["timestamp"], event["event_id"]))
    archive = {
        "schema": AUDIT_ARCHIVE_SCHEMA,
        "version": AUDIT_ARCHIVE_VERSION,
        "project_id": str(project_id),
        "created_at": created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "previous_archive_id": previous_archive_id,
        "first_timestamp": clean[0]["timestamp"],
        "last_timestamp": clean[-1]["timestamp"],
        "event_count": len(clean),
        "events": clean,
    }
    archive["archive_id"] = hashlib.sha256(_canonical(archive)).hexdigest()
    return validate_audit_archive(archive, project_id=project_id)


def validate_audit_archive(raw: Any, *, project_id: str | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema") != AUDIT_ARCHIVE_SCHEMA or raw.get("version") != AUDIT_ARCHIVE_VERSION:
        raise RuntimeError("The audit archive schema is invalid.")
    archive = dict(raw)
    if project_id is not None and archive.get("project_id") != str(project_id):
        raise RuntimeError("The audit archive belongs to a different project.")
    events = archive.get("events")
    if not isinstance(events, list) or not events:
        raise RuntimeError("The audit archive has no events.")
    clean = [validate_audit_event(event, project_id=archive["project_id"]) for event in events]
    ordered = sorted(clean, key=lambda event: (event["timestamp"], event["event_id"]))
    if clean != ordered or archive.get("event_count") != len(clean):
        raise RuntimeError("The audit archive event order or count is invalid.")
    if archive.get("first_timestamp") != clean[0]["timestamp"] or archive.get("last_timestamp") != clean[-1]["timestamp"]:
        raise RuntimeError("The audit archive time range is invalid.")
    expected = hashlib.sha256(_canonical(_archive_content(archive))).hexdigest()
    if archive.get("archive_id") != expected:
        raise RuntimeError("The audit archive identity does not match its content.")
    return archive


class VibeCADAuditStore:
    """Store each immutable audit event in one atomic project file."""

    def __init__(self, project_root: str | Path, project_id: str) -> None:
        self.project_root = Path(project_root)
        self.project_id = str(project_id)
        self.directory = self.project_root / "audit" / "events"
        self.archive_directory = self.project_root / "audit" / "archives"

    def append(self, event: Mapping[str, Any]) -> dict[str, Any]:
        record = validate_audit_event(dict(event), project_id=self.project_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{record['timestamp'].replace(':', '')}-{record['event_id']}.json"
        if path.exists():
            existing = validate_audit_event(json.loads(path.read_text(encoding="utf-8")), project_id=self.project_id)
            if existing != record:
                raise RuntimeError("An audit event already has different content.")
            return record
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(record, stream, ensure_ascii=True, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return record

    def record(self, **values: Any) -> dict[str, Any]:
        return self.append(create_audit_event(project_id=self.project_id, **values))

    def list_events(self) -> list[dict[str, Any]]:
        by_identity: dict[str, dict[str, Any]] = {}
        for archive in self.list_archives():
            for event in archive["events"]:
                by_identity[event["event_id"]] = event
        if not self.directory.is_dir():
            return sorted(by_identity.values(), key=lambda event: (event["timestamp"], event["event_id"]))
        for path in sorted(self.directory.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"The audit event could not be read from {path}: {exc}") from exc
            event = validate_audit_event(raw, project_id=self.project_id)
            existing = by_identity.get(event["event_id"])
            if existing is not None and existing != event:
                raise RuntimeError("Archived and live audit event content differs.")
            by_identity[event["event_id"]] = event
        return sorted(by_identity.values(), key=lambda event: (event["timestamp"], event["event_id"]))

    def list_archives(self) -> list[dict[str, Any]]:
        if not self.archive_directory.is_dir():
            return []
        archives = []
        previous = None
        for path in sorted(self.archive_directory.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"The audit archive could not be read from {path}: {exc}") from exc
            archive = validate_audit_archive(raw, project_id=self.project_id)
            if archive.get("previous_archive_id") != previous:
                raise RuntimeError("The audit archive chain is invalid.")
            archives.append(archive)
            previous = archive["archive_id"]
        return archives

    def apply_retention(
        self, *, live_days: int, max_live_events: int, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """Archive selected live events before removing their individual files."""
        if not isinstance(live_days, int) or not 1 <= live_days <= 3650:
            raise ValueError("Audit live retention days must be from 1 through 3650.")
        if not isinstance(max_live_events, int) or not 100 <= max_live_events <= 1_000_000:
            raise ValueError("Audit live event count must be from 100 through 1000000.")
        if not self.directory.is_dir():
            return None
        live: list[tuple[Path, dict[str, Any]]] = []
        for path in sorted(self.directory.glob("*.json")):
            event = validate_audit_event(
                json.loads(path.read_text(encoding="utf-8")), project_id=self.project_id
            )
            live.append((path, event))
        current = now or datetime.now(timezone.utc)
        cutoff = current.timestamp() - live_days * 86400
        selected = {
            event["event_id"]
            for _, event in live
            if datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).timestamp() < cutoff
        }
        excess = max(0, len(live) - max_live_events)
        selected.update(event["event_id"] for _, event in live[:excess])
        candidates = [(path, event) for path, event in live if event["event_id"] in selected]
        if not candidates:
            return None
        archives = self.list_archives()
        archive = create_audit_archive(
            project_id=self.project_id,
            events=[event for _, event in candidates],
            previous_archive_id=(archives[-1]["archive_id"] if archives else None),
        )
        self.archive_directory.mkdir(parents=True, exist_ok=True)
        path = self.archive_directory / f"{len(archives):08d}-{archive['archive_id']}.json"
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(archive, stream, ensure_ascii=True, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(self.archive_directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            validate_audit_archive(json.loads(path.read_text(encoding="utf-8")), project_id=self.project_id)
            for event_path, _ in candidates:
                event_path.unlink()
            event_directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(event_directory_fd)
            finally:
                os.close(event_directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
        return archive


def apply_managed_retention(
    store: VibeCADAuditStore, policy: Mapping[str, Any], *, force: bool = False
) -> dict[str, Any] | None:
    if not policy.get("managed"):
        return None
    key = str(store.project_root.resolve()) + "\0" + store.project_id
    current = time.monotonic()
    with _RETENTION_LOCK:
        if not force and current - _RETENTION_CHECKS.get(key, 0) < 3600:
            return None
        _RETENTION_CHECKS[key] = current
    return store.apply_retention(
        live_days=int(policy.get("audit_live_retention_days", 90)),
        max_live_events=int(policy.get("audit_live_max_events", 10000)),
    )
