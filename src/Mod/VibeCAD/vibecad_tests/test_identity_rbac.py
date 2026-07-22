# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import pytest

from VibeCADIdentity import (
    PERMISSIONS,
    ROLE_PERMISSIONS,
    ROLES,
    authorize,
    permissions_for,
    principal_from_policy,
    validate_principal,
)
from VibeCADManagedPolicy import default_policy, validate_policy


def _principal(role: str) -> dict:
    policy = default_policy()
    policy.update(
        managed=True,
        organization_id="org-1",
        managed_subject="user-1",
        managed_roles=[role],
    )
    return principal_from_policy(validate_policy(policy))


def test_required_enterprise_roles_have_explicit_permission_sets() -> None:
    assert ROLES == {
        "organization_owner", "administrator", "cad_manager",
        "designer", "reviewer", "viewer",
    }
    assert set(ROLE_PERMISSIONS) == ROLES
    assert ROLE_PERMISSIONS["organization_owner"] == PERMISSIONS
    assert ROLE_PERMISSIONS["administrator"] == PERMISSIONS
    assert ROLE_PERMISSIONS["viewer"] == {"project.view"}
    assert "design.modify" in ROLE_PERMISSIONS["designer"]
    assert "review" in ROLE_PERMISSIONS["reviewer"]
    assert "policy.manage" not in ROLE_PERMISSIONS["cad_manager"]


@pytest.mark.parametrize("role", sorted(ROLES))
def test_each_role_allows_exact_matrix_and_denies_every_other_permission(role: str) -> None:
    principal = _principal(role)
    assert permissions_for(principal) == ROLE_PERMISSIONS[role]
    for permission in PERMISSIONS:
        if permission in ROLE_PERMISSIONS[role]:
            authorize(principal, permission)
        else:
            with pytest.raises(PermissionError, match="does not have permission"):
                authorize(principal, permission)


def test_local_individual_is_owner_and_managed_empty_roles_fail_safe_to_viewer() -> None:
    local = principal_from_policy(default_policy())
    assert local["roles"] == ["organization_owner"]
    managed = default_policy()
    managed.update(managed=True, managed_roles=[])
    assert principal_from_policy(managed)["roles"] == ["viewer"]


def test_actor_identity_is_stable_hash_and_does_not_expose_subject() -> None:
    principal = _principal("designer")
    assert len(principal["actor_id"]) == 64
    assert principal["actor_id"] == _principal("designer")["actor_id"]
    assert "user-1" not in principal["actor_id"]


def test_unknown_role_and_permission_are_rejected() -> None:
    policy = default_policy()
    policy.update(managed=True, managed_roles=["superuser"])
    with pytest.raises(RuntimeError, match="unknown role"):
        validate_policy(policy)
    with pytest.raises(ValueError, match="Unknown enterprise permission"):
        authorize(_principal("viewer"), "root.everything")
