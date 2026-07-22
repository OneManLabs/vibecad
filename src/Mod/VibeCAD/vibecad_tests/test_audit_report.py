# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys
import types

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

import VibeCADAuditReport as reports
from VibeCADAudit import VibeCADAuditStore
from VibeCADAuditReport import (
    create_signed_audit_report,
    export_signed_audit_report,
    load_or_create_signing_key,
    verify_audit_report,
)


def _store(tmp_path: Path) -> VibeCADAuditStore:
    store = VibeCADAuditStore(tmp_path, "project-1")
    store.record(
        category="access", action="open", outcome="allowed",
        timestamp="2026-07-22T12:00:00Z",
    )
    return store


def test_signed_audit_report_verifies_with_pinned_public_key(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    report = create_signed_audit_report(
        _store(tmp_path), organization_id="org-1", private_key=key,
        created_at="2026-07-22T12:01:00Z",
    )
    assert report["event_count"] == 1
    assert verify_audit_report(report, trusted_public_key=key.public_key()) == report
    with pytest.raises(RuntimeError, match="not the trusted key"):
        verify_audit_report(report, trusted_public_key=Ed25519PrivateKey.generate().public_key())


def test_audit_report_tamper_is_rejected(tmp_path: Path) -> None:
    report = create_signed_audit_report(
        _store(tmp_path), organization_id="org-1",
        private_key=Ed25519PrivateKey.generate(),
    )
    report["organization_id"] = "attacker"
    with pytest.raises(RuntimeError, match="identity does not match"):
        verify_audit_report(report)


def test_managed_signer_fingerprint_rejects_replacement_key(tmp_path: Path) -> None:
    approved = Ed25519PrivateKey.generate()
    public = approved.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    fingerprint = hashlib.sha256(public).hexdigest()
    create_signed_audit_report(
        _store(tmp_path / "approved"), organization_id="org-1",
        private_key=approved, expected_signer_fingerprint=fingerprint,
    )
    with pytest.raises(RuntimeError, match="not approved"):
        create_signed_audit_report(
            _store(tmp_path / "replaced"), organization_id="org-1",
            private_key=Ed25519PrivateKey.generate(),
            expected_signer_fingerprint=fingerprint,
        )


def test_audit_signing_key_is_stable_in_keychain(monkeypatch) -> None:
    values = {}
    fake = types.SimpleNamespace(
        set_password=lambda service, user, value: values.__setitem__((service, user), value),
        get_password=lambda service, user: values.get((service, user)),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake)
    first = load_or_create_signing_key("org-1")
    second = load_or_create_signing_key("org-1")
    assert first.private_bytes_raw() == second.private_bytes_raw()
    assert "PRIVATE KEY" in next(iter(values.values()))


def test_atomic_report_export_reopens_and_failed_replace_preserves_target(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "audit-report.json"
    key = Ed25519PrivateKey.generate()
    report = export_signed_audit_report(
        target, _store(tmp_path / "project"), organization_id="org-1", private_key=key
    )
    assert verify_audit_report(json.loads(target.read_text(encoding="utf-8"))) == report
    target.write_text("accepted", encoding="utf-8")
    monkeypatch.setattr(reports.os, "replace", lambda source, destination: (_ for _ in ()).throw(OSError("fault")))
    with pytest.raises(OSError, match="fault"):
        export_signed_audit_report(
            target, _store(tmp_path / "other"), organization_id="org-1", private_key=key
        )
    assert target.read_text(encoding="utf-8") == "accepted"
