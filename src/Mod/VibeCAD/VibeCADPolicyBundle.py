# SPDX-License-Identifier: LGPL-2.1-or-later
"""Signed, versioned organization-policy bundles with atomic local promotion."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


BUNDLE_SCHEMA = "vibecad-policy-bundle-v1"
BUNDLE_VERSION = 1
CACHE_SCHEMA = "vibecad-policy-bundle-cache-v1"
MAX_BUNDLE_BYTES = 2 * 1024 * 1024


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError(f"Policy bundle field {field} is invalid.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"Policy bundle field {field} is invalid.") from exc
    if parsed.tzinfo is None:
        raise RuntimeError(f"Policy bundle field {field} must include a time zone.")
    return parsed.astimezone(timezone.utc)


def verify_policy_bundle(
    raw: Any, *, public_key_b64: str, organization_id: str,
    now: datetime | None = None, minimum_sequence: int = 0,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError("The policy bundle is not an object.")
    signature = raw.get("signature")
    body = {key: value for key, value in raw.items() if key != "signature"}
    if body.get("schema") != BUNDLE_SCHEMA or body.get("version") != BUNDLE_VERSION:
        raise RuntimeError("The policy bundle schema is invalid.")
    sequence = body.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise RuntimeError("The policy bundle sequence is invalid.")
    if sequence < minimum_sequence:
        raise RuntimeError("The policy bundle would roll policy back.")
    if body.get("organization_id") != organization_id or not organization_id:
        raise RuntimeError("The policy bundle organization does not match.")
    issued = _time(body.get("issued_at"), "issued_at")
    expires = _time(body.get("expires_at"), "expires_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if issued > current or expires <= current or expires <= issued:
        raise RuntimeError("The policy bundle is not active.")
    if not isinstance(body.get("policy"), dict):
        raise RuntimeError("The policy bundle policy is invalid.")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64, validate=True))
        public_key.verify(base64.b64decode(str(signature), validate=True), _canonical(body))
    except Exception as exc:
        raise RuntimeError("The policy bundle signature is invalid.") from exc
    verified = dict(body)
    verified["signature"] = signature
    return verified


class _BundleRedirect(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in self.allowed_hosts:
            raise RuntimeError("The policy bundle redirected to an unapproved endpoint.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_policy_bundle(
    url: str, allowed_hosts: set[str], policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise RuntimeError("The policy bundle endpoint is not allowed.")
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "VibeCAD-Policy/1"})
    from VibeCADNetwork import build_managed_opener
    with build_managed_opener(policy or {}, _BundleRedirect(allowed_hosts)).open(request, timeout=15) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or final.hostname not in allowed_hosts:
            raise RuntimeError("The policy bundle endpoint changed unexpectedly.")
        payload = response.read(MAX_BUNDLE_BYTES + 1)
    if len(payload) > MAX_BUNDLE_BYTES:
        raise RuntimeError("The policy bundle exceeds its size limit.")
    try:
        raw = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("The policy bundle is not valid JSON.") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("The policy bundle is not an object.")
    return raw


class PolicyBundleStore:
    def __init__(self, root: str | Path, organization_id: str) -> None:
        actor = hashlib.sha256(organization_id.encode("utf-8")).hexdigest()
        self.path = Path(root) / "policy" / f"{actor}.json"

    def read(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            cache = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("The cached policy bundle could not be read.") from exc
        if not isinstance(cache, dict) or cache.get("schema") != CACHE_SCHEMA:
            raise RuntimeError("The cached policy bundle schema is invalid.")
        bundle = cache.get("bundle")
        if not isinstance(bundle, dict) or cache.get("content_sha256") != hashlib.sha256(_canonical(bundle)).hexdigest():
            raise RuntimeError("The cached policy bundle identity is invalid.")
        return bundle

    def promote(self, bundle: Mapping[str, Any], fault: Callable[[str], None] | None = None) -> None:
        cache = {
            "schema": CACHE_SCHEMA,
            "bundle": dict(bundle),
            "content_sha256": hashlib.sha256(_canonical(bundle)).hexdigest(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".policy-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(_canonical(cache))
                stream.flush()
                os.fsync(stream.fileno())
            if fault:
                fault("before_replace")
            os.replace(temporary, self.path)
            if fault:
                fault("after_replace")
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def sync_policy_bundle(
    bootstrap: Mapping[str, Any], store: PolicyBundleStore, *,
    fetch: Callable[..., dict[str, Any]] = fetch_policy_bundle,
    now: datetime | None = None,
) -> dict[str, Any]:
    prior = store.read()
    minimum = int(prior.get("sequence", 0)) if prior else 0
    if fetch is fetch_policy_bundle:
        raw = fetch(str(bootstrap["policy_bundle_url"]), set(bootstrap["policy_bundle_allowed_hosts"]), bootstrap)
    else:
        raw = fetch(str(bootstrap["policy_bundle_url"]), set(bootstrap["policy_bundle_allowed_hosts"]))
    verified = verify_policy_bundle(
        raw, public_key_b64=str(bootstrap["policy_bundle_public_key"]),
        organization_id=str(bootstrap["organization_id"]), now=now,
        minimum_sequence=minimum,
    )
    if prior and verified["sequence"] == prior["sequence"] and _canonical(verified) != _canonical(prior):
        raise RuntimeError("The policy bundle sequence has conflicting content.")
    store.promote(verified)
    return verified
