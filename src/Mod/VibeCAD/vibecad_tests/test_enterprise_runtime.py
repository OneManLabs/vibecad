# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

from pathlib import Path

import pytest

from VibeCADEnterpriseRuntime import evaluate_runtime_controls
from VibeCADManagedPolicy import default_policy, validate_policy


def _policy(tmp_path: Path) -> dict:
    policy = default_policy()
    policy.update(
        managed=True,
        organization_id="acme-cad",
        managed_subject="designer-7",
        managed_roles=["designer"],
        allowed_providers=["openai"],
        allowed_models=["approved-cad-model"],
        allowed_provider_hosts=["gateway.example.com"],
        allow_document_geometry=False,
        allow_images=False,
        export_enabled=False,
        external_plugins_enabled=False,
        proxy_mode="explicit",
        proxy_url="http://proxy.example.com:8080",
        proxy_allowed_hosts=["proxy.example.com"],
    )
    return validate_policy(policy)


def test_runtime_control_evidence_is_redacted_and_complete(tmp_path: Path) -> None:
    result = evaluate_runtime_controls(
        _policy(tmp_path), provider="openai", model="approved-cad-model",
        endpoint="https://gateway.example.com/v1", online=True,
        context={
            "document": {"secret_geometry": "BREP"},
            "reference_images": {"images": ["secret"]},
            "workbench": "PartDesignWorkbench",
        },
        tool_names=["conversation.ask_user", "core.inspect", "project.export"],
    )
    assert result["provider_mode"] == "managed_gateway"
    assert result["endpoint_host"] == "gateway.example.com"
    assert result["proxy_host"] == "proxy.example.com"
    assert result["identity"]["roles"] == ["designer"]
    assert "design.modify" in result["identity"]["permissions"]
    assert result["allowed_tools"] == ["conversation.ask_user"]
    assert result["blocked_tools"] == ["core.inspect", "project.export"]
    assert result["context_policy"]["removed_context_fields"] == [
        "document", "reference_images"
    ]
    assert "BREP" not in str(result) and "secret" not in str(result)


def test_runtime_control_rejects_unapproved_gateway_and_model(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    with pytest.raises(PermissionError, match="endpoint"):
        evaluate_runtime_controls(
            policy, provider="openai", model="approved-cad-model",
            endpoint="https://api.openai.com/v1", online=True,
        )
    with pytest.raises(PermissionError, match="Model"):
        evaluate_runtime_controls(
            policy, provider="openai", model="unapproved",
            endpoint="https://gateway.example.com/v1", online=True,
        )
    with pytest.raises(PermissionError, match="endpoint"):
        evaluate_runtime_controls(
            policy, provider="openai", model="approved-cad-model",
            endpoint=None, online=True,
        )


def test_local_only_runtime_has_no_remote_provider_or_context_filter() -> None:
    policy = default_policy()
    policy.update(managed=True, local_only=True, organization_id="offline-org")
    result = evaluate_runtime_controls(
        validate_policy(policy), provider="openai", model="ignored", endpoint=None,
        online=False, context={"document": {"name": "Local"}},
        tool_names=["core.inspect"],
    )
    assert result["provider_mode"] == "local_only"
    assert result["provider"] == "offline"
    assert result["allowed_tools"] == ["core.inspect"]
    assert result["context_policy"]["geometry_shared"] is True
