# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from VibeCADManagedPolicy import default_policy, load_managed_policy, validate_policy
from VibeCADPolicyBundle import (
    BUNDLE_SCHEMA, PolicyBundleStore, _BundleRedirect, _canonical,
    sync_policy_bundle, verify_policy_bundle,
)


NOW = datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc)


def _keys():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private, base64.b64encode(public).decode("ascii")


def _bundle(private, *, sequence=1, policy=None, organization="org-1"):
    body = {
        "schema": BUNDLE_SCHEMA,
        "version": 1,
        "sequence": sequence,
        "organization_id": organization,
        "issued_at": (NOW - timedelta(minutes=1)).isoformat(),
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
        "policy": policy or default_policy(),
    }
    body["signature"] = base64.b64encode(private.sign(_canonical(body))).decode("ascii")
    return body


def _bootstrap(path: Path, public_key: str):
    policy = default_policy()
    policy.update({
        "organization_id": "org-1", "policy_bundle_enabled": True,
        "policy_bundle_url": "https://policy.example.com/v1/policy.json",
        "policy_bundle_allowed_hosts": ["policy.example.com"],
        "policy_bundle_public_key": public_key,
    })
    path.write_text(json.dumps(policy), encoding="utf-8")


def test_signed_bundle_verifies_and_tamper_fails():
    private, public = _keys()
    bundle = _bundle(private)
    verified = verify_policy_bundle(bundle, public_key_b64=public, organization_id="org-1", now=NOW)
    assert verified["sequence"] == 1
    bundle["policy"]["export_enabled"] = False
    with pytest.raises(RuntimeError, match="signature"):
        verify_policy_bundle(bundle, public_key_b64=public, organization_id="org-1", now=NOW)


@pytest.mark.parametrize("change, message", [
    ({"organization_id": "other"}, "organization"),
    ({"sequence": 0}, "sequence"),
    ({"expires_at": (NOW - timedelta(seconds=1)).isoformat()}, "not active"),
])
def test_bundle_identity_sequence_and_time_are_strict(change, message):
    private, public = _keys()
    bundle = _bundle(private)
    body = {k: v for k, v in bundle.items() if k != "signature"}
    body.update(change)
    body["signature"] = base64.b64encode(private.sign(_canonical(body))).decode("ascii")
    with pytest.raises(RuntimeError, match=message):
        verify_policy_bundle(body, public_key_b64=public, organization_id="org-1", now=NOW)


def test_sync_rejects_rollback_and_preserves_prior_cache(tmp_path: Path):
    private, public = _keys()
    store = PolicyBundleStore(tmp_path, "org-1")
    bootstrap = {
        "policy_bundle_url": "https://policy.example.com/policy.json",
        "policy_bundle_allowed_hosts": ["policy.example.com"],
        "policy_bundle_public_key": public,
        "organization_id": "org-1",
    }
    sync_policy_bundle(bootstrap, store, fetch=lambda *_: _bundle(private, sequence=2), now=NOW)
    with pytest.raises(RuntimeError, match="roll policy back"):
        sync_policy_bundle(bootstrap, store, fetch=lambda *_: _bundle(private, sequence=1), now=NOW)
    assert store.read()["sequence"] == 2


def test_sync_rejects_conflicting_content_at_same_sequence(tmp_path: Path):
    private, public = _keys()
    store = PolicyBundleStore(tmp_path, "org-1")
    bootstrap = {
        "policy_bundle_url": "https://policy.example.com/policy.json",
        "policy_bundle_allowed_hosts": ["policy.example.com"],
        "policy_bundle_public_key": public,
        "organization_id": "org-1",
    }
    first = default_policy()
    second = default_policy()
    second["export_enabled"] = False
    sync_policy_bundle(bootstrap, store, fetch=lambda *_: _bundle(private, sequence=2, policy=first), now=NOW)
    with pytest.raises(RuntimeError, match="conflicting content"):
        sync_policy_bundle(bootstrap, store, fetch=lambda *_: _bundle(private, sequence=2, policy=second), now=NOW)
    assert store.read()["policy"]["export_enabled"] is True


def test_atomic_cache_fault_before_replace_preserves_prior(tmp_path: Path):
    private, public = _keys()
    store = PolicyBundleStore(tmp_path, "org-1")
    first = verify_policy_bundle(_bundle(private), public_key_b64=public, organization_id="org-1", now=NOW)
    second = verify_policy_bundle(_bundle(private, sequence=2), public_key_b64=public, organization_id="org-1", now=NOW)
    store.promote(first)
    with pytest.raises(OSError):
        store.promote(second, fault=lambda point: (_ for _ in ()).throw(OSError("fault")) if point == "before_replace" else None)
    assert store.read()["sequence"] == 1


def test_managed_loader_requires_and_applies_verified_cache(tmp_path: Path, monkeypatch):
    private, public = _keys()
    bootstrap = tmp_path / "managed.json"
    cache_root = tmp_path / "cache"
    _bootstrap(bootstrap, public)
    with pytest.raises(RuntimeError, match="No verified"):
        load_managed_policy(bootstrap, bundle_cache_root=cache_root)
    policy = default_policy()
    policy.update({"export_enabled": False, "allowed_models": ["managed-model"]})
    verified = verify_policy_bundle(_bundle(private, policy=policy), public_key_b64=public, organization_id="org-1", now=NOW)
    PolicyBundleStore(cache_root, "org-1").promote(verified)
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr("VibeCADPolicyBundle.datetime", FixedDateTime)
    loaded = load_managed_policy(bootstrap, bundle_cache_root=cache_root)
    assert loaded["managed"] is True
    assert loaded["export_enabled"] is False
    assert loaded["allowed_models"] == ["managed-model"]


def test_bundle_trust_configuration_and_redirect_are_strict():
    policy = default_policy()
    policy.update({"managed": True, "organization_id": "org-1", "policy_bundle_enabled": True})
    with pytest.raises(RuntimeError, match="trust configuration"):
        validate_policy(policy)
    handler = _BundleRedirect({"policy.example.com"})
    request = type("Request", (), {})()
    with pytest.raises(RuntimeError, match="unapproved"):
        handler.redirect_request(request, None, 302, "", {}, "https://evil.example/policy")
