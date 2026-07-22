# SPDX-License-Identifier: LGPL-2.1-or-later
"""SCIM 2.0 role provisioning with a privacy-safe local assignment cache."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request

from VibeCADIdentity import PRINCIPAL_SCHEMA, PRINCIPAL_VERSION, ROLES, validate_principal


SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCIM_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ASSIGNMENT_SCHEMA = "vibecad-scim-assignment-v1"
ASSIGNMENT_VERSION = 1
KEYRING_SERVICE = "com.vibecad.desktop.scim"
MAX_SCIM_RESPONSE_BYTES = 4 * 1024 * 1024


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _content(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "assignment_id"}


class _SCIMRedirect(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source = urlparse(req.full_url)
        target = urlparse(newurl)
        if (
            target.scheme != "https"
            or target.hostname not in self.allowed_hosts
            or target.hostname != source.hostname
        ):
            raise RuntimeError("The SCIM request redirected to an unapproved endpoint.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_scim_json(
    url: str, token: str, allowed_hosts: set[str], policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise RuntimeError("The SCIM endpoint is not allowed.")
    if not token:
        raise PermissionError("No managed SCIM credential is available in Keychain.")
    request = Request(url, headers={
        "Accept": "application/scim+json, application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "VibeCAD-SCIM/1",
    })
    from VibeCADNetwork import build_managed_opener
    with build_managed_opener(policy or {}, _SCIMRedirect(allowed_hosts)).open(request, timeout=15) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or final.hostname not in allowed_hosts:
            raise RuntimeError("The SCIM endpoint changed unexpectedly.")
        payload = response.read(MAX_SCIM_RESPONSE_BYTES + 1)
    if len(payload) > MAX_SCIM_RESPONSE_BYTES:
        raise RuntimeError("The SCIM response exceeds its size limit.")
    try:
        raw = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("The SCIM response is not valid JSON.") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("The SCIM response is not an object.")
    return raw


def store_scim_token(organization_id: str, token: str) -> None:
    import keyring

    keyring.set_password(KEYRING_SERVICE, f"bearer:{organization_id}", str(token))


def read_scim_token(organization_id: str) -> str | None:
    import keyring

    return keyring.get_password(KEYRING_SERVICE, f"bearer:{organization_id}") or None


def _list_resources(raw: Any, resource_schema: str) -> list[dict[str, Any]]:
    if not isinstance(raw, dict) or SCIM_LIST_SCHEMA not in (raw.get("schemas") or []):
        raise RuntimeError("The SCIM list response schema is invalid.")
    resources = raw.get("Resources")
    if not isinstance(resources, list) or raw.get("totalResults") != len(resources):
        raise RuntimeError("The SCIM list response count is invalid.")
    clean = []
    for resource in resources:
        if not isinstance(resource, dict) or resource_schema not in (resource.get("schemas") or []):
            raise RuntimeError("A SCIM resource schema is invalid.")
        if not isinstance(resource.get("id"), str) or not resource["id"]:
            raise RuntimeError("A SCIM resource identity is invalid.")
        clean.append(dict(resource))
    return clean


def create_scim_assignment(
    *, organization_id: str, subject: str, active: bool,
    group_names: list[str], role_mapping: Mapping[str, str], etag: str = "",
) -> dict[str, Any]:
    principal = validate_principal({
        "schema": PRINCIPAL_SCHEMA,
        "version": PRINCIPAL_VERSION,
        "subject": subject,
        "organization_id": organization_id,
        "roles": ["viewer"],
        "source": "scim",
        "session_expires_at": None,
    })
    roles = list(dict.fromkeys(
        role_mapping[name] for name in group_names
        if name in role_mapping and role_mapping[name] in ROLES
    )) or ["viewer"]
    record = {
        "schema": ASSIGNMENT_SCHEMA,
        "version": ASSIGNMENT_VERSION,
        "organization_id": str(organization_id),
        "actor_id": principal["actor_id"],
        "active": bool(active),
        "roles": roles,
        "groups_hash": hashlib.sha256(_canonical(sorted(set(group_names)))).hexdigest(),
        "etag": str(etag),
    }
    record["assignment_id"] = hashlib.sha256(_canonical(record)).hexdigest()
    return validate_scim_assignment(record)


def validate_scim_assignment(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema") != ASSIGNMENT_SCHEMA or raw.get("version") != ASSIGNMENT_VERSION:
        raise RuntimeError("The SCIM assignment schema is invalid.")
    record = dict(raw)
    if len(str(record.get("actor_id") or "")) != 64:
        raise RuntimeError("The SCIM actor identity is invalid.")
    if not isinstance(record.get("active"), bool):
        raise RuntimeError("The SCIM active state is invalid.")
    if not isinstance(record.get("roles"), list) or not record["roles"] or any(role not in ROLES for role in record["roles"]):
        raise RuntimeError("The SCIM role assignment is invalid.")
    expected = hashlib.sha256(_canonical(_content(record))).hexdigest()
    if record.get("assignment_id") != expected:
        raise RuntimeError("The SCIM assignment identity does not match its content.")
    return record


class VibeCADSCIMStore:
    def __init__(self, root: str | Path, organization_id: str) -> None:
        organization_key = hashlib.sha256(str(organization_id).encode("utf-8")).hexdigest()
        self.directory = Path(root) / "scim" / organization_key

    def put(self, assignment: Mapping[str, Any]) -> dict[str, Any]:
        record = validate_scim_assignment(dict(assignment))
        path = self.directory / f"{record['actor_id']}.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        return record

    def get(self, actor_id: str) -> dict[str, Any] | None:
        path = self.directory / f"{actor_id}.json"
        if not path.is_file():
            return None
        return validate_scim_assignment(json.loads(path.read_text(encoding="utf-8")))


def sync_scim_subject(
    policy: Mapping[str, Any], subject: str, store: VibeCADSCIMStore,
    *, reader=_read_scim_json,
) -> dict[str, Any]:
    organization = str(policy.get("organization_id") or "managed")
    token = read_scim_token(organization)
    if not token:
        raise PermissionError("No managed SCIM credential is available in Keychain.")
    base = str(policy.get("scim_base_url") or "").rstrip("/")
    hosts = set(policy.get("scim_allowed_hosts") or [])
    if reader is _read_scim_json:
        reader = lambda url, token, hosts: _read_scim_json(url, token, hosts, policy)
    escaped = str(subject).replace("\\", "\\\\").replace('"', '\\"')
    user_filter = quote(f'externalId eq "{escaped}"')
    users = _list_resources(
        reader(f"{base}/Users?filter={user_filter}", token, hosts),
        SCIM_USER_SCHEMA,
    )
    if len(users) != 1:
        raise RuntimeError("SCIM must return exactly one user for the identity subject.")
    user = users[0]
    user_id = str(user["id"]).replace("\\", "\\\\").replace('"', '\\"')
    group_filter = quote(f'members.value eq "{user_id}"')
    groups = _list_resources(
        reader(f"{base}/Groups?filter={group_filter}", token, hosts),
        SCIM_GROUP_SCHEMA,
    )
    assignment = create_scim_assignment(
        organization_id=organization,
        subject=subject,
        active=bool(user.get("active", True)),
        group_names=[str(group.get("displayName") or "") for group in groups],
        role_mapping=dict(policy.get("scim_role_mapping") or {}),
        etag=str((user.get("meta") or {}).get("version") or ""),
    )
    return store.put(assignment)


def apply_scim_assignment(principal: Mapping[str, Any], assignment: Mapping[str, Any] | None) -> dict[str, Any]:
    clean = validate_principal(dict(principal))
    if assignment is None:
        raise PermissionError("No active SCIM assignment is available for this identity.")
    provisioned = validate_scim_assignment(dict(assignment))
    if provisioned["organization_id"] != clean["organization_id"] or provisioned["actor_id"] != clean["actor_id"]:
        raise PermissionError("The SCIM assignment does not match the authenticated identity.")
    if not provisioned["active"]:
        raise PermissionError("The organization has deactivated this SCIM user.")
    clean["roles"] = list(provisioned["roles"])
    return validate_principal(clean)
