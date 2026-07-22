# SPDX-License-Identifier: LGPL-2.1-or-later
"""Validated organization policy with a narrow macOS managed-preference adapter."""

from __future__ import annotations

import json
import base64
from pathlib import Path
import plistlib
import sys
from typing import Any
from urllib.parse import urlparse


POLICY_SCHEMA = "vibecad-managed-policy-v1"
POLICY_VERSION = 1
MACOS_POLICY_PATH = Path("/Library/Managed Preferences/com.vibecad.desktop.plist")
UPDATE_CHANNELS = {"stable", "prerelease", "nightly", "disabled"}
OFFICIAL_PROVIDER_HOSTS = {
    "openai": "api.openai.com",
    "anthropic": "api.anthropic.com",
    "chatgpt": "api.openai.com",
}


def default_policy() -> dict[str, Any]:
    return {
        "schema": POLICY_SCHEMA,
        "version": POLICY_VERSION,
        "managed": False,
        "local_only": False,
        "allowed_providers": ["openai", "anthropic", "chatgpt"],
        "allowed_models": [],
        "allowed_provider_hosts": ["api.openai.com", "api.anthropic.com"],
        "allow_document_geometry": True,
        "allow_images": True,
        "allow_diagnostic_uploads": False,
        "telemetry_enabled": False,
        "external_plugins_enabled": True,
        "export_enabled": True,
        "audit_live_retention_days": 90,
        "audit_live_max_events": 10000,
        "organization_id": "",
        "managed_subject": "",
        "managed_roles": ["designer"],
        "identity_mode": "local",
        "oidc_issuer": "",
        "oidc_client_id": "",
        "oidc_allowed_hosts": [],
        "oidc_allowed_algorithms": ["RS256"],
        "oidc_role_claim": "roles",
        "oidc_role_mapping": {},
        "oidc_scopes": ["openid", "profile"],
        "saml_idp_entity_id": "",
        "saml_sp_entity_id": "",
        "saml_sso_url": "",
        "saml_acs_url": "",
        "saml_allowed_hosts": [],
        "saml_idp_certificate": "",
        "saml_role_attribute": "roles",
        "saml_role_mapping": {},
        "scim_enabled": False,
        "scim_base_url": "",
        "scim_allowed_hosts": [],
        "scim_role_mapping": {},
        "policy_bundle_enabled": False,
        "policy_bundle_url": "",
        "policy_bundle_allowed_hosts": [],
        "policy_bundle_public_key": "",
        "proxy_mode": "system",
        "proxy_url": "",
        "proxy_allowed_hosts": [],
        "custom_ca_path": "",
        "custom_ca_sha256": "",
        "audit_collection_enabled": False,
        "audit_collection_url": "",
        "audit_collection_allowed_hosts": [],
        "audit_collection_receipt_public_key": "",
        "audit_report_signer_fingerprint": "",
        "update_channel": "stable",
        "allowed_update_hosts": [
            "github.com",
            "objects.githubusercontent.com",
            "release-assets.githubusercontent.com",
        ],
    }


def _host(value: Any, field: str) -> str:
    clean = str(value or "").strip().lower()
    parsed = urlparse(f"https://{clean}")
    if not clean or parsed.hostname != clean or "/" in clean or "@" in clean:
        raise RuntimeError(f"Managed policy field {field} has an invalid host.")
    return clean


def validate_policy(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema") != POLICY_SCHEMA or raw.get("version") != POLICY_VERSION:
        raise RuntimeError("The managed policy schema is invalid.")
    policy = default_policy()
    policy.update(raw)
    policy["schema"] = POLICY_SCHEMA
    policy["version"] = POLICY_VERSION
    for field in (
        "managed", "local_only", "allow_document_geometry", "allow_images",
        "allow_diagnostic_uploads", "telemetry_enabled", "external_plugins_enabled",
        "export_enabled", "scim_enabled", "policy_bundle_enabled", "audit_collection_enabled",
    ):
        if not isinstance(policy.get(field), bool):
            raise RuntimeError(f"Managed policy field {field} must be boolean.")
    for field, minimum, maximum in (
        ("audit_live_retention_days", 1, 3650),
        ("audit_live_max_events", 100, 1_000_000),
    ):
        value = policy.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise RuntimeError(f"Managed policy field {field} is outside its allowed range.")
    for field in ("allowed_providers", "allowed_models"):
        values = policy.get(field)
        if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
            raise RuntimeError(f"Managed policy field {field} must be a string array.")
        policy[field] = list(dict.fromkeys(item.strip() for item in values))
    roles = policy.get("managed_roles")
    if not isinstance(roles, list) or any(not isinstance(item, str) or not item.strip() for item in roles):
        raise RuntimeError("Managed policy field managed_roles must be a string array.")
    from VibeCADIdentity import ROLES
    if any(item not in ROLES for item in roles):
        raise RuntimeError("Managed policy field managed_roles contains an unknown role.")
    policy["managed_roles"] = list(dict.fromkeys(roles))
    for field in ("organization_id", "managed_subject"):
        if not isinstance(policy.get(field), str):
            raise RuntimeError(f"Managed policy field {field} must be a string.")
        policy[field] = policy[field].strip()
    identity_mode = str(policy.get("identity_mode") or "").strip().lower()
    if identity_mode not in {"local", "managed", "oidc", "saml"}:
        raise RuntimeError("Managed policy field identity_mode is invalid.")
    policy["identity_mode"] = identity_mode
    for field in ("oidc_issuer", "oidc_client_id", "oidc_role_claim"):
        if not isinstance(policy.get(field), str):
            raise RuntimeError(f"Managed policy field {field} must be a string.")
        policy[field] = policy[field].strip()
    for field in ("oidc_allowed_hosts", "oidc_allowed_algorithms", "oidc_scopes"):
        values = policy.get(field)
        if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
            raise RuntimeError(f"Managed policy field {field} must be a string array.")
        policy[field] = list(dict.fromkeys(item.strip() for item in values))
    if "openid" not in policy["oidc_scopes"] or any(
        not item.replace("_", "").replace("-", "").isalnum()
        for item in policy["oidc_scopes"]
    ):
        raise RuntimeError("Managed OIDC scopes are invalid.")
    policy["oidc_allowed_hosts"] = [
        _host(item, "oidc_allowed_hosts") for item in policy["oidc_allowed_hosts"]
    ]
    if any(item not in {"RS256", "ES256"} for item in policy["oidc_allowed_algorithms"]):
        raise RuntimeError("Managed OIDC policy contains an unsupported algorithm.")
    mapping = policy.get("oidc_role_mapping")
    if not isinstance(mapping, dict) or any(
        not isinstance(key, str) or not key.strip() or value not in ROLES
        for key, value in mapping.items()
    ):
        raise RuntimeError("Managed OIDC role mapping is invalid.")
    policy["oidc_role_mapping"] = dict(mapping)
    if identity_mode == "oidc":
        issuer = urlparse(policy["oidc_issuer"])
        if issuer.scheme != "https" or not issuer.hostname or issuer.query or issuer.fragment:
            raise RuntimeError("The managed OIDC issuer must be an HTTPS URL.")
        if not policy["oidc_client_id"] or issuer.hostname not in policy["oidc_allowed_hosts"]:
            raise RuntimeError("The managed OIDC client or issuer host is not allowed.")
    for field in (
        "saml_idp_entity_id", "saml_sp_entity_id", "saml_sso_url", "saml_acs_url",
        "saml_idp_certificate", "saml_role_attribute",
    ):
        if not isinstance(policy.get(field), str):
            raise RuntimeError(f"Managed policy field {field} must be a string.")
        policy[field] = policy[field].strip()
    hosts = policy.get("saml_allowed_hosts")
    if not isinstance(hosts, list) or any(not isinstance(item, str) for item in hosts):
        raise RuntimeError("Managed policy field saml_allowed_hosts must be a string array.")
    policy["saml_allowed_hosts"] = list(dict.fromkeys(_host(item, "saml_allowed_hosts") for item in hosts))
    saml_mapping = policy.get("saml_role_mapping")
    if not isinstance(saml_mapping, dict) or any(
        not isinstance(key, str) or not key.strip() or value not in ROLES
        for key, value in saml_mapping.items()
    ):
        raise RuntimeError("Managed SAML role mapping is invalid.")
    policy["saml_role_mapping"] = dict(saml_mapping)
    if identity_mode == "saml":
        sso = urlparse(policy["saml_sso_url"])
        acs = urlparse(policy["saml_acs_url"])
        if sso.scheme != "https" or sso.hostname not in policy["saml_allowed_hosts"]:
            raise RuntimeError("The managed SAML sign-in endpoint is not allowed.")
        if acs.scheme != "http" or acs.hostname not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("The managed SAML callback must use a local loopback address.")
        if not policy["saml_idp_entity_id"] or not policy["saml_sp_entity_id"]:
            raise RuntimeError("The managed SAML entity identifiers are required.")
        certificate = policy["saml_idp_certificate"]
        if "BEGIN CERTIFICATE" not in certificate or "END CERTIFICATE" not in certificate:
            raise RuntimeError("The managed SAML identity certificate is invalid.")
    if not isinstance(policy.get("scim_base_url"), str):
        raise RuntimeError("Managed policy field scim_base_url must be a string.")
    policy["scim_base_url"] = policy["scim_base_url"].strip().rstrip("/")
    scim_hosts = policy.get("scim_allowed_hosts")
    if not isinstance(scim_hosts, list) or any(not isinstance(item, str) for item in scim_hosts):
        raise RuntimeError("Managed policy field scim_allowed_hosts must be a string array.")
    policy["scim_allowed_hosts"] = list(dict.fromkeys(_host(item, "scim_allowed_hosts") for item in scim_hosts))
    scim_mapping = policy.get("scim_role_mapping")
    if not isinstance(scim_mapping, dict) or any(
        not isinstance(key, str) or not key.strip() or value not in ROLES
        for key, value in scim_mapping.items()
    ):
        raise RuntimeError("Managed SCIM role mapping is invalid.")
    policy["scim_role_mapping"] = dict(scim_mapping)
    if policy["scim_enabled"]:
        base = urlparse(policy["scim_base_url"])
        if not policy["managed"] or base.scheme != "https" or base.hostname not in policy["scim_allowed_hosts"]:
            raise RuntimeError("The managed SCIM endpoint is not allowed.")
    if not isinstance(policy.get("policy_bundle_url"), str) or not isinstance(policy.get("policy_bundle_public_key"), str):
        raise RuntimeError("Managed policy bundle trust fields must be strings.")
    policy["policy_bundle_url"] = policy["policy_bundle_url"].strip()
    policy["policy_bundle_public_key"] = policy["policy_bundle_public_key"].strip()
    bundle_hosts = policy.get("policy_bundle_allowed_hosts")
    if not isinstance(bundle_hosts, list) or any(not isinstance(item, str) for item in bundle_hosts):
        raise RuntimeError("Managed policy field policy_bundle_allowed_hosts must be a string array.")
    policy["policy_bundle_allowed_hosts"] = list(dict.fromkeys(
        _host(item, "policy_bundle_allowed_hosts") for item in bundle_hosts
    ))
    if policy["policy_bundle_enabled"]:
        endpoint = urlparse(policy["policy_bundle_url"])
        try:
            key = base64.b64decode(policy["policy_bundle_public_key"], validate=True)
        except ValueError as exc:
            raise RuntimeError("The managed policy bundle public key is invalid.") from exc
        if (
            not policy["managed"] or not policy["organization_id"] or
            endpoint.scheme != "https" or endpoint.hostname not in policy["policy_bundle_allowed_hosts"] or
            len(key) != 32
        ):
            raise RuntimeError("The managed policy bundle trust configuration is invalid.")
    proxy_mode = str(policy.get("proxy_mode") or "").strip().lower()
    if proxy_mode not in {"system", "direct", "explicit"}:
        raise RuntimeError("The managed proxy mode is invalid.")
    policy["proxy_mode"] = proxy_mode
    if not isinstance(policy.get("proxy_url"), str):
        raise RuntimeError("Managed policy field proxy_url must be a string.")
    policy["proxy_url"] = policy["proxy_url"].strip()
    proxy_hosts = policy.get("proxy_allowed_hosts")
    if not isinstance(proxy_hosts, list) or any(not isinstance(item, str) for item in proxy_hosts):
        raise RuntimeError("Managed policy field proxy_allowed_hosts must be a string array.")
    policy["proxy_allowed_hosts"] = list(dict.fromkeys(_host(item, "proxy_allowed_hosts") for item in proxy_hosts))
    if proxy_mode == "explicit":
        proxy = urlparse(policy["proxy_url"])
        if (
            proxy.scheme not in {"http", "https"} or not proxy.hostname or
            proxy.hostname not in policy["proxy_allowed_hosts"] or proxy.username or
            proxy.password or proxy.query or proxy.fragment
        ):
            raise RuntimeError("The managed proxy endpoint is not allowed.")
    for field in ("custom_ca_path", "custom_ca_sha256"):
        if not isinstance(policy.get(field), str):
            raise RuntimeError(f"Managed policy field {field} must be a string.")
        policy[field] = policy[field].strip()
    if bool(policy["custom_ca_path"]) != bool(policy["custom_ca_sha256"]):
        raise RuntimeError("The managed custom CA configuration is incomplete.")
    if policy["custom_ca_path"]:
        if not Path(policy["custom_ca_path"]).is_absolute() or len(policy["custom_ca_sha256"]) != 64:
            raise RuntimeError("The managed custom CA configuration is invalid.")
        try:
            int(policy["custom_ca_sha256"], 16)
        except ValueError as exc:
            raise RuntimeError("The managed custom CA identity is invalid.") from exc
    for field in ("audit_collection_url", "audit_collection_receipt_public_key"):
        if not isinstance(policy.get(field), str):
            raise RuntimeError(f"Managed policy field {field} must be a string.")
        policy[field] = policy[field].strip()
    audit_hosts = policy.get("audit_collection_allowed_hosts")
    if not isinstance(audit_hosts, list) or any(not isinstance(item, str) for item in audit_hosts):
        raise RuntimeError("Managed policy field audit_collection_allowed_hosts must be a string array.")
    policy["audit_collection_allowed_hosts"] = list(dict.fromkeys(
        _host(item, "audit_collection_allowed_hosts") for item in audit_hosts
    ))
    if policy["audit_collection_enabled"]:
        endpoint = urlparse(policy["audit_collection_url"])
        try:
            receipt_key = base64.b64decode(policy["audit_collection_receipt_public_key"], validate=True)
        except ValueError as exc:
            raise RuntimeError("The managed audit receipt public key is invalid.") from exc
        if (
            not policy["managed"] or not policy["organization_id"] or endpoint.scheme != "https" or
            endpoint.hostname not in policy["audit_collection_allowed_hosts"] or len(receipt_key) != 32
        ):
            raise RuntimeError("The managed audit collector trust configuration is invalid.")
    if not isinstance(policy.get("audit_report_signer_fingerprint"), str):
        raise RuntimeError("Managed policy field audit_report_signer_fingerprint must be a string.")
    policy["audit_report_signer_fingerprint"] = policy["audit_report_signer_fingerprint"].strip().lower()
    if policy["audit_report_signer_fingerprint"]:
        value = policy["audit_report_signer_fingerprint"]
        try:
            int(value, 16)
        except ValueError as exc:
            raise RuntimeError("The managed audit signer fingerprint is invalid.") from exc
        if len(value) != 64:
            raise RuntimeError("The managed audit signer fingerprint is invalid.")
    for field in ("allowed_provider_hosts", "allowed_update_hosts"):
        values = policy.get(field)
        if not isinstance(values, list):
            raise RuntimeError(f"Managed policy field {field} must be an array.")
        policy[field] = list(dict.fromkeys(_host(item, field) for item in values))
    channel = str(policy.get("update_channel") or "").strip().lower()
    if channel not in UPDATE_CHANNELS:
        raise RuntimeError("The managed update channel is invalid.")
    policy["update_channel"] = channel
    if policy["local_only"]:
        policy["allowed_providers"] = []
        policy["allowed_provider_hosts"] = []
    return policy


def load_managed_policy(
    path: str | Path | None = None, *, bundle_cache_root: str | Path | None = None,
    resolve_bundle: bool = True,
) -> dict[str, Any]:
    selected = Path(path) if path is not None else (MACOS_POLICY_PATH if sys.platform == "darwin" else Path())
    if not selected or not selected.is_file():
        return default_policy()
    try:
        if selected.suffix.lower() == ".json":
            raw = json.loads(selected.read_text(encoding="utf-8"))
        else:
            raw = plistlib.loads(selected.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        raise RuntimeError(f"The managed policy could not be read: {exc}") from exc
    raw = dict(raw)
    raw["managed"] = True
    bootstrap = validate_policy(raw)
    if not bootstrap["policy_bundle_enabled"] or not resolve_bundle:
        return bootstrap
    from VibeCADPolicyBundle import PolicyBundleStore, verify_policy_bundle
    if bundle_cache_root is None:
        bundle_cache_root = Path.home() / "Library" / "Application Support" / "VibeCAD"
    bundle = PolicyBundleStore(bundle_cache_root, bootstrap["organization_id"]).read()
    if bundle is None:
        raise RuntimeError("No verified managed policy bundle is available.")
    verified = verify_policy_bundle(
        bundle, public_key_b64=bootstrap["policy_bundle_public_key"],
        organization_id=bootstrap["organization_id"],
    )
    policy = dict(verified["policy"])
    policy.update({
        "managed": True,
        "organization_id": bootstrap["organization_id"],
        "policy_bundle_enabled": True,
        "policy_bundle_url": bootstrap["policy_bundle_url"],
        "policy_bundle_allowed_hosts": bootstrap["policy_bundle_allowed_hosts"],
        "policy_bundle_public_key": bootstrap["policy_bundle_public_key"],
    })
    return validate_policy(policy)


def enforce_provider(policy: dict[str, Any], provider: str, model: str, endpoint: str | None) -> None:
    clean = validate_policy(policy)
    if clean["local_only"] or provider not in clean["allowed_providers"]:
        raise PermissionError(f"Provider {provider!r} is blocked by organization policy.")
    if clean["allowed_models"] and model not in clean["allowed_models"]:
        raise PermissionError(f"Model {model!r} is blocked by organization policy.")
    host = (
        urlparse(endpoint).hostname
        if endpoint
        else OFFICIAL_PROVIDER_HOSTS.get(str(provider or "").strip().lower())
    )
    if not host or host not in clean["allowed_provider_hosts"]:
        raise PermissionError("The provider endpoint is blocked by organization policy.")


_ACTION_FIELDS = {
    "document_geometry": "allow_document_geometry",
    "images": "allow_images",
    "diagnostic_upload": "allow_diagnostic_uploads",
    "external_plugin": "external_plugins_enabled",
    "export": "export_enabled",
}


def enforce_action(policy: dict[str, Any], action: str) -> None:
    """Fail closed before one policy-classified side effect or disclosure."""
    clean = validate_policy(policy)
    field = _ACTION_FIELDS.get(str(action))
    if field is None:
        raise ValueError(f"Unknown managed policy action: {action!r}.")
    if clean["managed"] and not clean[field]:
        label = str(action).replace("_", " ")
        raise PermissionError(f"The {label} action is blocked by organization policy.")


def provider_tool_action(tool_name: str) -> str | None:
    clean = str(tool_name or "").strip().lower()
    segments = set(clean.replace("-", "_").split("."))
    if "export" in segments or any(part.startswith("export_") for part in segments):
        return "export"
    if clean == "core.capture_view_screenshot" or clean.endswith(".capture_image"):
        return "images"
    return None


def provider_tool_allowed(
    policy: dict[str, Any], tool_name: str, *, online: bool
) -> bool:
    clean = validate_policy(policy)
    if not online or not clean["managed"]:
        return True
    action = provider_tool_action(tool_name)
    if action and not clean[_ACTION_FIELDS[action]]:
        return False
    if not clean["allow_document_geometry"]:
        return tool_name in {"conversation.ask_user"}
    return True


def enforce_provider_tool(
    policy: dict[str, Any], tool_name: str, *, online: bool
) -> None:
    if not provider_tool_allowed(policy, tool_name, online=online):
        raise PermissionError(
            f"Provider tool {tool_name!r} is blocked by organization policy."
        )


def filter_provider_context(
    context: dict[str, Any], policy: dict[str, Any], *, online: bool
) -> dict[str, Any]:
    """Remove outbound CAD and image data before a remote provider sees it."""
    clean = validate_policy(policy)
    filtered = dict(context)
    if not online or not clean["managed"]:
        return filtered
    removed: list[str] = []
    if not clean["allow_document_geometry"]:
        for field in ("document", "selection", "design_brief"):
            if field in filtered:
                filtered.pop(field, None)
                removed.append(field)
    if not clean["allow_images"]:
        for field in ("view_screenshot", "reference_images"):
            if field in filtered:
                filtered.pop(field, None)
                removed.append(field)
    filtered["managed_policy"] = {
        "geometry_shared": clean["allow_document_geometry"],
        "images_shared": clean["allow_images"],
        "removed_context_fields": sorted(removed),
    }
    return filtered
