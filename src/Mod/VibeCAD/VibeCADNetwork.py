# SPDX-License-Identifier: LGPL-2.1-or-later
"""Shared managed TLS and proxy transport for VibeCAD network adapters."""

from __future__ import annotations

import hashlib
from pathlib import Path
import ssl
from typing import Any, Mapping
from urllib.parse import urlparse
from urllib.request import HTTPSHandler, ProxyHandler, build_opener


def managed_ssl_context(policy: Mapping[str, Any]) -> ssl.SSLContext:
    context = ssl.create_default_context()
    ca_path = str(policy.get("custom_ca_path") or "").strip()
    expected = str(policy.get("custom_ca_sha256") or "").strip().lower()
    if not ca_path:
        return context
    selected = Path(ca_path)
    if not selected.is_absolute() or not expected:
        raise RuntimeError("The managed custom CA configuration is incomplete.")
    try:
        pem = selected.read_bytes()
    except OSError as exc:
        raise RuntimeError("The managed custom CA could not be read.") from exc
    if hashlib.sha256(pem).hexdigest() != expected:
        raise RuntimeError("The managed custom CA identity does not match.")
    try:
        context.load_verify_locations(cadata=pem.decode("ascii"))
    except (UnicodeDecodeError, ssl.SSLError) as exc:
        raise RuntimeError("The managed custom CA is invalid.") from exc
    return context


def managed_network_handlers(policy: Mapping[str, Any]) -> list[Any]:
    mode = str(policy.get("proxy_mode") or "system").strip().lower()
    if mode == "direct":
        proxy = ProxyHandler({})
    elif mode == "system":
        proxy = ProxyHandler()
    elif mode == "explicit":
        url = str(policy.get("proxy_url") or "").strip()
        parsed = urlparse(url)
        allowed = set(policy.get("proxy_allowed_hosts") or [])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.hostname not in allowed:
            raise RuntimeError("The managed proxy endpoint is not allowed.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RuntimeError("The managed proxy URL contains unsupported data.")
        proxy = ProxyHandler({"http": url, "https": url})
    else:
        raise RuntimeError("The managed proxy mode is invalid.")
    return [proxy, HTTPSHandler(context=managed_ssl_context(policy))]


def build_managed_opener(policy: Mapping[str, Any], *handlers: Any):
    return build_opener(*handlers, *managed_network_handlers(policy))


def managed_httpx_client(policy: Mapping[str, Any]):
    """Create the SDK HTTP client with the same proxy and TLS rules."""
    import httpx

    mode = str(policy.get("proxy_mode") or "system").strip().lower()
    kwargs: dict[str, Any] = {"verify": managed_ssl_context(policy)}
    if mode == "direct":
        kwargs["trust_env"] = False
    elif mode == "system":
        kwargs["trust_env"] = True
    elif mode == "explicit":
        handlers = managed_network_handlers(policy)
        proxy = next(item for item in handlers if isinstance(item, ProxyHandler))
        kwargs.update(proxy=proxy.proxies["https"], trust_env=False)
    else:
        raise RuntimeError("The managed proxy mode is invalid.")
    return httpx.Client(**kwargs)


def managed_sdk_http_client():
    from VibeCADManagedPolicy import load_managed_policy

    policy = load_managed_policy()
    if not policy.get("managed"):
        return None
    return managed_httpx_client(policy)
