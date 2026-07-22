# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import json
from pathlib import Path
import sys
import types
from urllib.parse import unquote

import pytest

from VibeCADIdentity import principal_from_policy
from VibeCADManagedPolicy import default_policy, validate_policy
from VibeCADSCIM import (
    _SCIMRedirect,
    SCIM_GROUP_SCHEMA,
    SCIM_LIST_SCHEMA,
    SCIM_USER_SCHEMA,
    VibeCADSCIMStore,
    apply_scim_assignment,
    create_scim_assignment,
    store_scim_token,
    sync_scim_subject,
    validate_scim_assignment,
)


def _principal() -> dict:
    policy = default_policy()
    policy.update(
        managed=True, organization_id="org-1", managed_subject="user-123",
        managed_roles=["viewer"],
    )
    return principal_from_policy(policy)


def _list(resources, schema):
    return {
        "schemas": [SCIM_LIST_SCHEMA], "totalResults": len(resources),
        "startIndex": 1, "itemsPerPage": len(resources),
        "Resources": [{"schemas": [schema], **resource} for resource in resources],
    }


def test_scim_assignment_is_content_bound_and_omits_raw_subject() -> None:
    assignment = create_scim_assignment(
        organization_id="org-1", subject="user-123", active=True,
        group_names=["cad-designers"], role_mapping={"cad-designers": "designer"},
    )
    assert assignment["roles"] == ["designer"]
    assert "user-123" not in json.dumps(assignment)
    assignment["active"] = False
    with pytest.raises(RuntimeError, match="identity does not match"):
        validate_scim_assignment(assignment)


def test_scim_sync_uses_keychain_allowlisted_filters_and_atomic_store(monkeypatch, tmp_path: Path) -> None:
    values = {}
    monkeypatch.setitem(sys.modules, "keyring", types.SimpleNamespace(
        set_password=lambda service, user, value: values.__setitem__((service, user), value),
        get_password=lambda service, user: values.get((service, user)),
    ))
    store_scim_token("org-1", "managed-token")
    policy = default_policy()
    policy.update(
        managed=True, organization_id="org-1", scim_enabled=True,
        scim_base_url="https://identity.example.com/scim/v2",
        scim_allowed_hosts=["identity.example.com"],
        scim_role_mapping={"cad-designers": "designer"},
    )
    requests = []

    def reader(url, token, hosts):
        requests.append((url, token, hosts))
        if "/Users?" in url:
            return _list([{
                "id": "scim-user-1", "externalId": "user-123", "active": True,
                "meta": {"version": 'W/"1"'},
            }], SCIM_USER_SCHEMA)
        return _list([{"id": "group-1", "displayName": "cad-designers"}], SCIM_GROUP_SCHEMA)

    store = VibeCADSCIMStore(tmp_path, "org-1")
    assignment = sync_scim_subject(validate_policy(policy), "user-123", store, reader=reader)
    assert assignment["roles"] == ["designer"]
    assert store.get(assignment["actor_id"]) == assignment
    assert all(token == "managed-token" for _, token, _ in requests)
    assert all(hosts == {"identity.example.com"} for _, _, hosts in requests)
    assert 'externalId eq "user-123"' in unquote(requests[0][0])
    assert 'members.value eq "scim-user-1"' in unquote(requests[1][0])


def test_scim_assignment_overrides_roles_and_deactivation_denies() -> None:
    principal = _principal()
    with pytest.raises(PermissionError, match="No active SCIM assignment"):
        apply_scim_assignment(principal, None)
    active = create_scim_assignment(
        organization_id="org-1", subject="user-123", active=True,
        group_names=["cad-managers"], role_mapping={"cad-managers": "cad_manager"},
    )
    assert apply_scim_assignment(principal, active)["roles"] == ["cad_manager"]
    inactive = create_scim_assignment(
        organization_id="org-1", subject="user-123", active=False,
        group_names=[], role_mapping={},
    )
    with pytest.raises(PermissionError, match="deactivated"):
        apply_scim_assignment(principal, inactive)


def test_scim_managed_policy_requires_https_allowlisted_endpoint() -> None:
    policy = default_policy()
    policy.update(
        managed=True, scim_enabled=True,
        scim_base_url="https://identity.example.com/scim/v2",
        scim_allowed_hosts=["identity.example.com"],
    )
    assert validate_policy(policy)["scim_enabled"] is True
    policy["scim_base_url"] = "http://identity.example.com/scim/v2"
    with pytest.raises(RuntimeError, match="SCIM endpoint"):
        validate_policy(policy)


def test_scim_bearer_redirect_cannot_change_host() -> None:
    handler = _SCIMRedirect({"identity.example.com", "other.example.com"})
    request = types.SimpleNamespace(full_url="https://identity.example.com/scim/v2/Users")
    with pytest.raises(RuntimeError, match="unapproved endpoint"):
        handler.redirect_request(
            request, None, 302, "Found", {}, "https://other.example.com/scim/v2/Users"
        )
