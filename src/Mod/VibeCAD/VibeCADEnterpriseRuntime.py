# SPDX-License-Identifier: LGPL-2.1-or-later
"""Versioned, redacted enterprise runtime-control evidence."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse

from VibeCADIdentity import permissions_for, principal_from_policy
from VibeCADManagedPolicy import (
    enforce_provider,
    filter_provider_context,
    provider_tool_allowed,
    validate_policy,
)


RUNTIME_CONTROL_SCHEMA = "vibecad-enterprise-runtime-control-v1"
RUNTIME_CONTROL_VERSION = 1


def evaluate_runtime_controls(
    policy: Mapping[str, Any],
    *,
    provider: str,
    model: str,
    endpoint: str | None,
    online: bool,
    context: Mapping[str, Any] | None = None,
    tool_names: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate one provider turn without exposing secrets or CAD content."""
    clean = validate_policy(dict(policy))
    provider_name = str(provider or "").strip()
    model_name = str(model or "").strip()
    endpoint_value = str(endpoint or "").strip() or None
    if online:
        enforce_provider(clean, provider_name, model_name, endpoint_value)
    filtered = filter_provider_context(dict(context or {}), clean, online=online)
    names = [str(name or "").strip() for name in list(tool_names or [])]
    allowed_tools = [
        name for name in names
        if name and provider_tool_allowed(clean, name, online=online)
    ]
    blocked_tools = [name for name in names if name and name not in allowed_tools]
    principal = principal_from_policy(clean)
    if clean["local_only"]:
        provider_mode = "local_only"
    elif endpoint_value:
        provider_mode = "managed_gateway" if clean["managed"] else "custom_endpoint"
    else:
        provider_mode = "managed_cloud" if clean["managed"] else "user_cloud"
    policy_evidence = filtered.get("managed_policy") or {
        "geometry_shared": True,
        "images_shared": True,
        "removed_context_fields": [],
    }
    return {
        "schema": RUNTIME_CONTROL_SCHEMA,
        "version": RUNTIME_CONTROL_VERSION,
        "managed": clean["managed"],
        "organization_id": clean["organization_id"],
        "provider_mode": provider_mode,
        "provider": provider_name if online else "offline",
        "model": model_name if online else "",
        "endpoint_host": urlparse(endpoint_value).hostname if endpoint_value else None,
        "proxy_mode": clean["proxy_mode"],
        "proxy_host": urlparse(clean["proxy_url"]).hostname if clean["proxy_url"] else None,
        "custom_ca_sha256": clean["custom_ca_sha256"] or None,
        "identity": {
            "actor_id": principal["actor_id"],
            "roles": principal["roles"],
            "permissions": sorted(permissions_for(principal)),
        },
        "context_policy": {
            "geometry_shared": bool(policy_evidence["geometry_shared"]),
            "images_shared": bool(policy_evidence["images_shared"]),
            "removed_context_fields": list(policy_evidence["removed_context_fields"]),
        },
        "allowed_tools": allowed_tools,
        "blocked_tools": blocked_tools,
        "export_enabled": clean["export_enabled"],
        "external_plugins_enabled": clean["external_plugins_enabled"],
        "update_channel": clean["update_channel"],
    }
