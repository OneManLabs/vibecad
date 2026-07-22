# SPDX-License-Identifier: LGPL-2.1-or-later
from pathlib import Path
import plistlib

import pytest

from VibeCADManagedPolicy import (
    default_policy,
    enforce_action,
    enforce_provider,
    enforce_provider_tool,
    filter_provider_context,
    load_managed_policy,
    provider_tool_allowed,
    validate_policy,
)


def test_local_only_removes_provider_and_network_permissions() -> None:
    policy = default_policy()
    policy.update({"managed": True, "local_only": True})
    clean = validate_policy(policy)
    assert clean["allowed_providers"] == []
    assert clean["allowed_provider_hosts"] == []
    with pytest.raises(PermissionError):
        enforce_provider(clean, "openai", "gpt", "https://api.openai.com")


def test_managed_plist_loads_and_marks_policy_managed(tmp_path: Path) -> None:
    policy = default_policy()
    policy["allowed_models"] = ["approved-model"]
    path = tmp_path / "com.vibecad.desktop.plist"
    path.write_bytes(plistlib.dumps(policy))
    loaded = load_managed_policy(path)
    assert loaded["managed"] is True
    enforce_provider(loaded, "openai", "approved-model", "https://api.openai.com/v1")
    with pytest.raises(PermissionError, match="Model"):
        enforce_provider(loaded, "openai", "other", "https://api.openai.com/v1")


def test_managed_gateway_allowlist_cannot_fall_back_to_official_host() -> None:
    policy = default_policy()
    policy.update(
        managed=True,
        allowed_providers=["openai"],
        allowed_provider_hosts=["gateway.example.com"],
    )
    with pytest.raises(PermissionError, match="endpoint"):
        enforce_provider(validate_policy(policy), "openai", "approved", None)


def test_invalid_update_host_and_channel_are_rejected() -> None:
    policy = default_policy()
    policy["allowed_update_hosts"] = ["good.example/path"]
    with pytest.raises(RuntimeError, match="host"):
        validate_policy(policy)
    policy = default_policy()
    policy["update_channel"] = "uncontrolled"
    with pytest.raises(RuntimeError, match="channel"):
        validate_policy(policy)


def test_managed_context_redacts_geometry_and_images_for_online_provider() -> None:
    policy = default_policy()
    policy.update(managed=True, allow_document_geometry=False, allow_images=False)
    context = {
        "document": {"objects": ["SecretPart"]},
        "selection": {"face": "Face1"},
        "design_brief": {"critical_dimensions": [{"value": 42}]},
        "view_screenshot": {"path": "/secret.png"},
        "reference_images": {"images": [{"path": "/reference.png"}]},
        "workbench": "PartDesignWorkbench",
    }
    filtered = filter_provider_context(context, policy, online=True)
    assert set(context) - set(filtered) == {
        "document", "selection", "design_brief", "view_screenshot", "reference_images"
    }
    assert filtered["managed_policy"]["geometry_shared"] is False
    assert filtered["managed_policy"]["images_shared"] is False


def test_local_provider_keeps_context_when_cloud_sharing_is_blocked() -> None:
    policy = default_policy()
    policy.update(managed=True, allow_document_geometry=False, allow_images=False)
    context = {"document": {"name": "Local"}, "reference_images": {"images": [1]}}
    assert filter_provider_context(context, policy, online=False) == context


def test_policy_denies_remote_geometry_image_export_and_plugin_actions() -> None:
    policy = default_policy()
    policy.update(
        managed=True,
        allow_document_geometry=False,
        allow_images=False,
        export_enabled=False,
        external_plugins_enabled=False,
    )
    assert provider_tool_allowed(policy, "conversation.ask_user", online=True)
    for tool_name in ("core.inspect", "core.capture_view_screenshot", "project.export_step"):
        assert not provider_tool_allowed(policy, tool_name, online=True)
        with pytest.raises(PermissionError, match="organization policy"):
            enforce_provider_tool(policy, tool_name, online=True)
    for action in ("images", "document_geometry", "export", "external_plugin"):
        with pytest.raises(PermissionError, match="organization policy"):
            enforce_action(policy, action)


def test_unmanaged_policy_does_not_disable_user_actions() -> None:
    policy = default_policy()
    policy.update(export_enabled=False, allow_images=False)
    enforce_action(policy, "export")
    assert provider_tool_allowed(policy, "core.capture_view_screenshot", online=True)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("audit_live_retention_days", 0),
        ("audit_live_retention_days", 3651),
        ("audit_live_max_events", 99),
        ("audit_live_max_events", 1_000_001),
    ],
)
def test_audit_retention_policy_range_is_strict(field, value) -> None:
    policy = default_policy()
    policy[field] = value
    with pytest.raises(RuntimeError, match=field):
        validate_policy(policy)
