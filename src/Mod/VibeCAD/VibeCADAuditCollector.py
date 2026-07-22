# SPDX-License-Identifier: LGPL-2.1-or-later
"""Upload signed audit reports and accept only pinned signed receipts."""

from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from VibeCADAuditReport import verify_audit_report


RECEIPT_SCHEMA = "vibecad-audit-receipt-v1"
RECEIPT_VERSION = 1
KEYRING_SERVICE = "com.vibecad.desktop.audit-collector"
MAX_RECEIPT_BYTES = 256 * 1024


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


class _AuditRedirect(HTTPRedirectHandler):
    def __init__(self, hosts: set[str]) -> None:
        super().__init__()
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source, target = urlparse(req.full_url), urlparse(newurl)
        if target.scheme != "https" or target.hostname not in self.hosts or target.hostname != source.hostname:
            raise RuntimeError("The audit collector redirected to an unapproved endpoint.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def store_collector_token(organization_id: str, token: str) -> None:
    import keyring
    keyring.set_password(KEYRING_SERVICE, f"bearer:{organization_id}", str(token))


def read_collector_token(organization_id: str) -> str | None:
    import keyring
    return keyring.get_password(KEYRING_SERVICE, f"bearer:{organization_id}")


def verify_audit_receipt(
    raw: Any, *, report_id: str, organization_id: str, public_key_b64: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema") != RECEIPT_SCHEMA or raw.get("version") != RECEIPT_VERSION:
        raise RuntimeError("The audit receipt schema is invalid.")
    body = {key: value for key, value in raw.items() if key != "signature"}
    if body.get("report_id") != report_id or body.get("organization_id") != organization_id:
        raise RuntimeError("The audit receipt identity does not match.")
    if body.get("status") != "accepted":
        raise RuntimeError("The audit collector did not accept the report.")
    try:
        datetime.fromisoformat(str(body.get("received_at") or "").replace("Z", "+00:00"))
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64, validate=True))
        key.verify(base64.b64decode(str(raw.get("signature") or ""), validate=True), _canonical(body))
    except Exception as exc:
        raise RuntimeError("The audit receipt signature is invalid.") from exc
    return dict(raw)


def upload_audit_report(report: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    clean = verify_audit_report(dict(report))
    organization = str(policy.get("organization_id") or "")
    if clean["organization_id"] != organization:
        raise RuntimeError("The audit report organization does not match policy.")
    url = str(policy.get("audit_collection_url") or "")
    hosts = set(policy.get("audit_collection_allowed_hosts") or [])
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in hosts:
        raise RuntimeError("The audit collector endpoint is not allowed.")
    token = read_collector_token(organization)
    if not token:
        raise PermissionError("No managed audit collector credential is available in Keychain.")
    payload = _canonical(clean)
    request = Request(url, data=payload, method="POST", headers={
        "Accept": "application/json", "Content-Type": "application/json",
        "Authorization": f"Bearer {token}", "Idempotency-Key": clean["report_id"],
        "User-Agent": "VibeCAD-Audit/1",
    })
    from VibeCADNetwork import build_managed_opener
    with build_managed_opener(policy, _AuditRedirect(hosts)).open(request, timeout=30) as response:
        raw_bytes = response.read(MAX_RECEIPT_BYTES + 1)
    if len(raw_bytes) > MAX_RECEIPT_BYTES:
        raise RuntimeError("The audit receipt exceeds its size limit.")
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("The audit receipt is not valid JSON.") from exc
    return verify_audit_receipt(
        raw, report_id=clean["report_id"], organization_id=organization,
        public_key_b64=str(policy.get("audit_collection_receipt_public_key") or ""),
    )


def store_audit_receipt(project_root: str | Path, receipt: Mapping[str, Any]) -> Path:
    target = Path(project_root) / "audit" / "receipts" / f"{receipt['report_id']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical(dict(receipt))
    fd, temporary = tempfile.mkstemp(prefix=".receipt-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return target
