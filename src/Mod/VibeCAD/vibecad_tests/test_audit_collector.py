# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import base64
import json
from pathlib import Path
import types

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

import VibeCADAuditCollector as collector
from VibeCADAudit import VibeCADAuditStore, create_audit_event
from VibeCADAuditCollector import RECEIPT_SCHEMA, _canonical, store_audit_receipt, upload_audit_report, verify_audit_receipt
from VibeCADAuditReport import create_signed_audit_report
from VibeCADManagedPolicy import default_policy, validate_policy


def _receipt(private, report_id="report-1", organization="org-1"):
    body = {
        "schema": RECEIPT_SCHEMA, "version": 1, "report_id": report_id,
        "organization_id": organization, "status": "accepted",
        "received_at": "2026-07-22T18:00:00Z",
    }
    body["signature"] = base64.b64encode(private.sign(_canonical(body))).decode("ascii")
    return body


def _public(private):
    return base64.b64encode(private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )).decode("ascii")


def test_receipt_is_bound_to_report_organization_and_signature():
    private = Ed25519PrivateKey.generate()
    receipt = _receipt(private)
    assert verify_audit_receipt(receipt, report_id="report-1", organization_id="org-1", public_key_b64=_public(private))["status"] == "accepted"
    receipt["report_id"] = "other"
    with pytest.raises(RuntimeError, match="identity"):
        verify_audit_receipt(receipt, report_id="report-1", organization_id="org-1", public_key_b64=_public(private))


def test_upload_uses_bearer_idempotency_and_signed_receipt(tmp_path: Path, monkeypatch):
    store = VibeCADAuditStore(tmp_path, "project-1")
    event_path = store.append(create_audit_event(project_id="project-1", category="test", action="run", outcome="ok"))
    report = create_signed_audit_report(store, organization_id="org-1", private_key=Ed25519PrivateKey.generate())
    receipt_key = Ed25519PrivateKey.generate()
    receipt = _receipt(receipt_key, report["report_id"])
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, _limit): return json.dumps(receipt).encode("utf-8")

    class Opener:
        def open(self, request, timeout):
            captured.update(headers=dict(request.header_items()), timeout=timeout, body=request.data)
            return Response()

    monkeypatch.setattr("VibeCADNetwork.build_managed_opener", lambda *_: Opener())
    monkeypatch.setattr(collector, "read_collector_token", lambda _org: "collector-secret")
    policy = default_policy()
    policy.update({
        "managed": True, "organization_id": "org-1", "audit_collection_enabled": True,
        "audit_collection_url": "https://audit.example.com/v1/reports",
        "audit_collection_allowed_hosts": ["audit.example.com"],
        "audit_collection_receipt_public_key": _public(receipt_key),
    })
    accepted = upload_audit_report(report, validate_policy(policy))
    assert accepted["report_id"] == report["report_id"]
    assert captured["headers"]["Authorization"] == "Bearer collector-secret"
    assert captured["headers"]["Idempotency-key"] == report["report_id"]
    assert event_path
    assert len(store.list_events()) == 1
    path = store_audit_receipt(tmp_path, accepted)
    assert json.loads(path.read_text())["report_id"] == report["report_id"]
    assert len(store.list_events()) == 1


def test_audit_collection_policy_requires_pinned_https_endpoint():
    policy = default_policy()
    policy.update({"managed": True, "organization_id": "org-1", "audit_collection_enabled": True})
    with pytest.raises(RuntimeError, match="collector trust"):
        validate_policy(policy)
