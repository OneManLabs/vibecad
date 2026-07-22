# SPDX-License-Identifier: LGPL-2.1-or-later
"""Provider-neutral enterprise principal and role authorization contract."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping


PRINCIPAL_SCHEMA = "vibecad-principal-v1"
PRINCIPAL_VERSION = 1
ROLES = {
    "organization_owner",
    "administrator",
    "cad_manager",
    "designer",
    "reviewer",
    "viewer",
}
PERMISSIONS = {
    "project.view",
    "design.modify",
    "ai.use",
    "revision.restore",
    "export",
    "review",
    "policy.manage",
    "users.manage",
    "audit.view",
}
ROLE_PERMISSIONS = {
    "organization_owner": set(PERMISSIONS),
    "administrator": set(PERMISSIONS),
    "cad_manager": {
        "project.view", "design.modify", "ai.use", "revision.restore",
        "export", "review", "audit.view",
    },
    "designer": {
        "project.view", "design.modify", "ai.use", "revision.restore", "export",
    },
    "reviewer": {"project.view", "review", "audit.view"},
    "viewer": {"project.view"},
}


def principal_from_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    managed = bool(policy.get("managed"))
    roles = list(policy.get("managed_roles") or []) if managed else ["organization_owner"]
    if managed and not roles:
        roles = ["viewer"]
    subject = str(policy.get("managed_subject") or ("managed-user" if managed else "local-user"))
    organization = str(policy.get("organization_id") or ("managed" if managed else "local"))
    source = "managed_configuration" if managed else "local"
    raw = {
        "schema": PRINCIPAL_SCHEMA,
        "version": PRINCIPAL_VERSION,
        "subject": subject,
        "organization_id": organization,
        "roles": roles,
        "source": source,
        "session_expires_at": None,
    }
    return validate_principal(raw)


def validate_principal(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema") != PRINCIPAL_SCHEMA or raw.get("version") != PRINCIPAL_VERSION:
        raise RuntimeError("The enterprise principal schema is invalid.")
    principal = dict(raw)
    for field in ("subject", "organization_id", "source"):
        if not isinstance(principal.get(field), str) or not principal[field].strip():
            raise RuntimeError(f"The enterprise principal field {field!r} is invalid.")
        principal[field] = principal[field].strip()
    roles = principal.get("roles")
    if not isinstance(roles, list) or not roles or any(role not in ROLES for role in roles):
        raise RuntimeError("The enterprise principal roles are invalid.")
    principal["roles"] = list(dict.fromkeys(roles))
    expires = principal.get("session_expires_at")
    if expires is not None and (isinstance(expires, bool) or not isinstance(expires, (int, float))):
        raise RuntimeError("The enterprise principal session expiry is invalid.")
    identity = json.dumps(
        [principal["organization_id"], principal["subject"]],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    principal["actor_id"] = hashlib.sha256(identity).hexdigest()
    return principal


def permissions_for(principal: Mapping[str, Any]) -> set[str]:
    clean = validate_principal(dict(principal))
    result: set[str] = set()
    for role in clean["roles"]:
        result.update(ROLE_PERMISSIONS[role])
    return result


def authorize(principal: Mapping[str, Any], permission: str) -> None:
    clean_permission = str(permission or "").strip()
    if clean_permission not in PERMISSIONS:
        raise ValueError(f"Unknown enterprise permission: {permission!r}.")
    clean = validate_principal(dict(principal))
    if clean.get("session_expires_at") is not None and float(clean["session_expires_at"]) <= time.time():
        raise PermissionError("The enterprise identity session has expired.")
    if clean_permission not in permissions_for(clean):
        roles = ", ".join(clean["roles"])
        raise PermissionError(
            f"Role {roles!r} does not have permission {clean_permission!r}."
        )
