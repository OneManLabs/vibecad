# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import json
from pathlib import Path

import pytest

import VibeCADOrganization as organization
from VibeCADIdentity import principal_from_policy
from VibeCADManagedPolicy import default_policy
from VibeCADOrganization import (
    VibeCADOrganizationStore,
    create_membership_record,
    validate_membership_record,
)


def _principal(role: str = "designer") -> dict:
    policy = default_policy()
    policy.update(
        managed=True, organization_id="org/../one", managed_subject="user-secret",
        managed_roles=[role],
    )
    return principal_from_policy(policy)


def test_membership_record_is_content_bound_and_does_not_store_subject() -> None:
    record = create_membership_record(_principal(), provisioned_at="2026-07-22T12:00:00Z")
    assert len(record["record_id"]) == 64
    assert record["roles"] == ["designer"]
    assert "subject" not in record
    assert "user-secret" not in json.dumps(record)
    record["roles"] = ["viewer"]
    with pytest.raises(RuntimeError, match="identity does not match"):
        validate_membership_record(record)


def test_membership_store_provisions_reopens_and_updates_roles(tmp_path: Path) -> None:
    store = VibeCADOrganizationStore(tmp_path)
    first = store.provision(_principal("designer"))
    assert store.get(first["organization_id"], first["actor_id"]) == first
    second = store.provision(_principal("viewer"))
    assert second["roles"] == ["viewer"]
    assert second["record_id"] != first["record_id"]
    paths = list((tmp_path / "organizations").glob("*/*.json"))
    assert len(paths) == 1
    assert ".." not in str(paths[0].relative_to(tmp_path))


def test_failed_membership_promotion_preserves_prior_record(monkeypatch, tmp_path: Path) -> None:
    store = VibeCADOrganizationStore(tmp_path)
    accepted = store.provision(_principal("designer"))
    monkeypatch.setattr(organization.os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("fault")))
    with pytest.raises(OSError, match="fault"):
        store.provision(_principal("viewer"))
    assert store.get(accepted["organization_id"], accepted["actor_id"]) == accepted
