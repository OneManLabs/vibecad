# SPDX-License-Identifier: LGPL-2.1-or-later
"""Versioned, privacy-safe local organization membership records."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from VibeCADIdentity import validate_principal


PROVISIONING_SCHEMA = "vibecad-organization-membership-v1"
PROVISIONING_VERSION = 1


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _content(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "record_id"}


def create_membership_record(
    principal: Mapping[str, Any], *, provisioned_at: str | None = None
) -> dict[str, Any]:
    clean = validate_principal(dict(principal))
    record = {
        "schema": PROVISIONING_SCHEMA,
        "version": PROVISIONING_VERSION,
        "organization_id": clean["organization_id"],
        "actor_id": clean["actor_id"],
        "roles": clean["roles"],
        "identity_source": clean["source"],
        "status": "active",
        "provisioned_at": provisioned_at or datetime.now(timezone.utc).isoformat(),
    }
    record["record_id"] = hashlib.sha256(_canonical(record)).hexdigest()
    return validate_membership_record(record)


def validate_membership_record(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema") != PROVISIONING_SCHEMA or raw.get("version") != PROVISIONING_VERSION:
        raise RuntimeError("The organization membership schema is invalid.")
    record = dict(raw)
    for field in ("organization_id", "actor_id", "identity_source", "provisioned_at"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise RuntimeError(f"The organization membership field {field} is invalid.")
    if len(record["actor_id"]) != 64 or any(value not in "0123456789abcdef" for value in record["actor_id"]):
        raise RuntimeError("The organization membership actor identity is invalid.")
    roles = record.get("roles")
    if not isinstance(roles, list) or not roles or any(not isinstance(role, str) for role in roles):
        raise RuntimeError("The organization membership roles are invalid.")
    expected = hashlib.sha256(_canonical(_content(record))).hexdigest()
    if record.get("record_id") != expected:
        raise RuntimeError("The organization membership identity does not match its content.")
    return record


class VibeCADOrganizationStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root) / "organizations"

    def _path(self, organization_id: str, actor_id: str) -> Path:
        organization_key = hashlib.sha256(str(organization_id).encode("utf-8")).hexdigest()
        return self.root / organization_key / f"{actor_id}.json"

    def provision(self, principal: Mapping[str, Any]) -> dict[str, Any]:
        clean = validate_principal(dict(principal))
        path = self._path(clean["organization_id"], clean["actor_id"])
        if path.is_file():
            current = validate_membership_record(json.loads(path.read_text(encoding="utf-8")))
            if (
                current["roles"] == clean["roles"]
                and current["identity_source"] == clean["source"]
                and current["status"] == "active"
            ):
                return current
        record = create_membership_record(clean)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        return record

    def get(self, organization_id: str, actor_id: str) -> dict[str, Any] | None:
        path = self._path(organization_id, actor_id)
        if not path.is_file():
            return None
        return validate_membership_record(json.loads(path.read_text(encoding="utf-8")))
