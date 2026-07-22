# SPDX-License-Identifier: LGPL-2.1-or-later
"""Portable, signed export of redacted VibeCAD audit evidence."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from VibeCADAudit import VibeCADAuditStore, validate_audit_event


REPORT_SCHEMA = "vibecad-audit-report-v1"
REPORT_VERSION = 1
KEYRING_SERVICE = "com.vibecad.desktop.audit-signing"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _payload(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in record.items()
        if key not in {"report_id", "signature"}
    }


def _signed_content(record: Mapping[str, Any]) -> dict[str, Any]:
    content = _payload(record)
    content["report_id"] = record.get("report_id")
    return content


def _key_fingerprint(key: Ed25519PrivateKey) -> str:
    public_der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(public_der).hexdigest()


def load_or_create_signing_key(
    organization_id: str, *, expected_fingerprint: str = "",
) -> Ed25519PrivateKey:
    import keyring

    account = f"ed25519:{organization_id}"
    stored = keyring.get_password(KEYRING_SERVICE, account)
    if stored:
        try:
            key = serialization.load_pem_private_key(stored.encode("ascii"), password=None)
        except Exception as exc:
            raise RuntimeError("The audit signing key in Keychain is invalid.") from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise RuntimeError("The audit signing key has an unsupported type.")
        if expected_fingerprint and _key_fingerprint(key) != expected_fingerprint.lower():
            raise RuntimeError("The audit signing key is not approved by organization policy.")
        return key
    if expected_fingerprint:
        raise RuntimeError("The organization-approved audit signing key is not in Keychain.")
    key = Ed25519PrivateKey.generate()
    encoded = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    keyring.set_password(KEYRING_SERVICE, account, encoded)
    return key


def audit_signing_key_fingerprint(organization_id: str) -> str:
    """Return the public fingerprint for enrollment without exposing private key data."""
    return _key_fingerprint(load_or_create_signing_key(organization_id))


def create_signed_audit_report(
    store: VibeCADAuditStore,
    *,
    organization_id: str,
    private_key: Ed25519PrivateKey | None = None,
    created_at: str | None = None,
    expected_signer_fingerprint: str = "",
) -> dict[str, Any]:
    events = [validate_audit_event(event, project_id=store.project_id) for event in store.list_events()]
    key = private_key or load_or_create_signing_key(
        organization_id, expected_fingerprint=expected_signer_fingerprint
    )
    if expected_signer_fingerprint and _key_fingerprint(key) != expected_signer_fingerprint.lower():
        raise RuntimeError("The audit signing key is not approved by organization policy.")
    public = key.public_key()
    public_der = public.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    report = {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "project_id": store.project_id,
        "organization_id": str(organization_id),
        "created_at": created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event_count": len(events),
        "first_timestamp": events[0]["timestamp"] if events else None,
        "last_timestamp": events[-1]["timestamp"] if events else None,
        "archive_ids": [archive["archive_id"] for archive in store.list_archives()],
        "events": events,
        "signer": {
            "algorithm": "Ed25519",
            "public_key": base64.b64encode(public_der).decode("ascii"),
            "fingerprint_sha256": hashlib.sha256(public_der).hexdigest(),
            "trust": "local-device-keychain",
        },
    }
    report["report_id"] = hashlib.sha256(_canonical(report)).hexdigest()
    report["signature"] = base64.b64encode(key.sign(_canonical(_signed_content(report)))).decode("ascii")
    return verify_audit_report(report)


def verify_audit_report(
    raw: Any, *, trusted_public_key: bytes | Ed25519PublicKey | None = None
) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema") != REPORT_SCHEMA or raw.get("version") != REPORT_VERSION:
        raise RuntimeError("The audit report schema is invalid.")
    report = dict(raw)
    events = report.get("events")
    if not isinstance(events, list) or report.get("event_count") != len(events):
        raise RuntimeError("The audit report event count is invalid.")
    clean = [validate_audit_event(event, project_id=report.get("project_id")) for event in events]
    if clean != sorted(clean, key=lambda event: (event["timestamp"], event["event_id"])):
        raise RuntimeError("The audit report event order is invalid.")
    if report.get("first_timestamp") != (clean[0]["timestamp"] if clean else None):
        raise RuntimeError("The audit report first timestamp is invalid.")
    if report.get("last_timestamp") != (clean[-1]["timestamp"] if clean else None):
        raise RuntimeError("The audit report last timestamp is invalid.")
    expected_id = hashlib.sha256(_canonical(_payload(report))).hexdigest()
    if report.get("report_id") != expected_id:
        raise RuntimeError("The audit report identity does not match its content.")
    signer = report.get("signer")
    if not isinstance(signer, dict) or signer.get("algorithm") != "Ed25519":
        raise RuntimeError("The audit report signing algorithm is invalid.")
    try:
        public_der = base64.b64decode(str(signer.get("public_key") or ""), validate=True)
        embedded = serialization.load_der_public_key(public_der)
        signature = base64.b64decode(str(report.get("signature") or ""), validate=True)
    except Exception as exc:
        raise RuntimeError("The audit report signature data is invalid.") from exc
    if not isinstance(embedded, Ed25519PublicKey):
        raise RuntimeError("The audit report public key has an unsupported type.")
    fingerprint = hashlib.sha256(public_der).hexdigest()
    if signer.get("fingerprint_sha256") != fingerprint:
        raise RuntimeError("The audit report signer fingerprint is invalid.")
    trusted = trusted_public_key
    if isinstance(trusted, bytes):
        try:
            trusted = serialization.load_pem_public_key(trusted)
        except ValueError:
            trusted = serialization.load_der_public_key(trusted)
    if trusted is not None:
        if not isinstance(trusted, Ed25519PublicKey):
            raise RuntimeError("The trusted audit public key has an unsupported type.")
        trusted_der = trusted.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        if trusted_der != public_der:
            raise RuntimeError("The audit report signer is not the trusted key.")
    try:
        embedded.verify(signature, _canonical(_signed_content(report)))
    except Exception as exc:
        raise RuntimeError("The audit report signature is invalid.") from exc
    return report


def export_signed_audit_report(
    path: str | Path,
    store: VibeCADAuditStore,
    *,
    organization_id: str,
    private_key: Ed25519PrivateKey | None = None,
    expected_signer_fingerprint: str = "",
) -> dict[str, Any]:
    target = Path(path)
    report = create_signed_audit_report(
        store, organization_id=organization_id, private_key=private_key,
        expected_signer_fingerprint=expected_signer_fingerprint,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY)
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
    return verify_audit_report(json.loads(target.read_text(encoding="utf-8")))
