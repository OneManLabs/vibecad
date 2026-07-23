# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from tools.probe_provider_readiness import (
    CREDENTIAL_FINGERPRINT_ALGORITHM,
    canonical_provider_endpoint,
    credential_fingerprint,
    endpoint_identity_digest,
    readiness_execution_identity_matches,
    run_probe,
)


_TEST_CREDENTIAL = "test-provider-key-with-high-entropy-placeholder"


def _ready_child_result(
    binding_nonce: str,
    *,
    provider: str = "openai",
    model: str = "model",
    auth_source: str = "OS keyring",
    base_url: str | None = None,
    credential: str = _TEST_CREDENTIAL,
) -> dict[str, object]:
    endpoint = canonical_provider_endpoint(provider, base_url)
    fingerprint = (
        "a" * 64
        if auth_source == "environment"
        else credential_fingerprint(
            provider=provider,
            auth_source=auth_source,
            binding_nonce=binding_nonce,
            credential=credential,
        )
    )
    return {
        "schema": "vibecad-provider-readiness-v1",
        "version": 1,
        "created_at": "2026-07-22T12:00:00Z",
        "can_call_provider": True,
        "prompt_sent": False,
        "document_data_sent": False,
        "credential_validation_performed": True,
        "model_validation_performed": True,
        "model_available": True,
        "stage": "complete",
        "provider": provider,
        "model": model,
        "auth_status": "verified",
        "auth_source": auth_source,
        "online_by_default": True,
        "endpoint_identity": endpoint,
        "endpoint_sha256": endpoint_identity_digest(endpoint),
        "credential_binding_nonce": binding_nonce,
        "credential_fingerprint_algorithm": CREDENTIAL_FINGERPRINT_ALGORITHM,
        "credential_fingerprint": fingerprint,
    }


def test_timeout_before_result_fails_closed_without_data_claim(tmp_path) -> None:
    def runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    result = run_probe(Path("FreeCADCmd"), Path("child.py"), tmp_path / "result.json", 1, runner=runner)
    assert result["ready_for_live_benchmark"] is False
    assert result["process_timed_out"] is True
    assert result["prompt_sent"] is False
    assert result["document_data_sent"] is False


def test_probe_child_receives_no_ambient_secret_or_injection_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "result.json"
    hostile_values = {
        "OPENAI_API_KEY": "ambient-openai-secret",
        "ANTHROPIC_API_KEY": "ambient-anthropic-secret",
        "AWS_SECRET_ACCESS_KEY": "ambient-cloud-secret",
        "PYTHONPATH": "/tmp/hostile-python",
        "PYTHONHOME": "/tmp/hostile-home",
        "PYTHONSTARTUP": "/tmp/hostile-startup",
        "DYLD_INSERT_LIBRARIES": "/tmp/hostile.dylib",
        "DYLD_LIBRARY_PATH": "/tmp/hostile-loader",
        "LD_PRELOAD": "/tmp/hostile.so",
        "LD_LIBRARY_PATH": "/tmp/hostile-loader",
        "BASH_ENV": "/tmp/hostile-bash",
        "ENV": "/tmp/hostile-shell",
        "ZDOTDIR": "/tmp/hostile-zsh",
        "XDG_CONFIG_HOME": "/tmp/hostile-config",
        "XDG_DATA_HOME": "/tmp/hostile-data",
        "VIBECAD_HOSTILE_AMBIENT": "must-not-pass",
    }
    for name, value in hostile_values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HOME", str(tmp_path / "safe-home"))
    monkeypatch.setenv("PATH", "/tmp/hostile-path")

    def runner(command, **kwargs):
        environment = kwargs["env"]
        assert all(name not in environment for name in hostile_values)
        assert environment["HOME"] == str(tmp_path / "safe-home")
        assert environment["PATH"] == os.defpath
        assert environment["VIBECAD_PROVIDER_VALIDATE_CREDENTIALS"] == "1"
        nonce = environment["VIBECAD_PROVIDER_CREDENTIAL_BINDING_NONCE"]
        output.write_text(
            json.dumps(_ready_child_result(nonce)), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0)

    result = run_probe(
        Path("FreeCADCmd"),
        Path("child.py"),
        output,
        1,
        validate_credentials=True,
        runner=runner,
    )

    assert result["ready_for_live_benchmark"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", float("inf")),
        ("credential_validation_timeout", float("nan")),
        ("credential_validation_timeout", float("-inf")),
    ),
)
def test_probe_rejects_non_finite_timeouts_before_output_change(
    tmp_path: Path, field: str, value: float
) -> None:
    output = tmp_path / "result.json"
    output.write_text("unchanged", encoding="utf-8")
    values = {
        "timeout_seconds": 1.0,
        "credential_validation_timeout": 5.0,
    }
    values[field] = value

    with pytest.raises(ValueError, match="finite"):
        run_probe(
            Path("FreeCADCmd"),
            Path("child.py"),
            output,
            values["timeout_seconds"],
            credential_validation_timeout=values[
                "credential_validation_timeout"
            ],
        )

    assert output.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.parametrize("fault", ("oversized", "symlink"))
def test_probe_rejects_unsafe_or_oversized_readiness_evidence(
    tmp_path: Path,
    fault: str,
) -> None:
    output = tmp_path / "result.json"

    def runner(command, **kwargs):
        del command, kwargs
        if fault == "oversized":
            output.write_bytes(b"{" + b" " * (256 * 1024) + b"}")
        else:
            target = tmp_path / "outside.json"
            target.write_text("{}", encoding="utf-8")
            output.symlink_to(target)
        return SimpleNamespace(returncode=0)

    result = run_probe(
        Path("FreeCADCmd"),
        Path("child.py"),
        output,
        1,
        runner=runner,
    )

    assert result["ready_for_live_benchmark"] is False
    assert result["can_call_provider"] is False
    assert "rejected" in str(result["error"])


def test_verified_opt_in_result_can_authorize_live_benchmark(tmp_path) -> None:
    output = tmp_path / "result.json"

    def runner(command, **kwargs):
        nonce = kwargs["env"]["VIBECAD_PROVIDER_CREDENTIAL_BINDING_NONCE"]
        output.write_text(
            json.dumps(_ready_child_result(nonce)), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0)

    result = run_probe(
        Path("FreeCADCmd"), Path("child.py"), output, 1,
        validate_credentials=True, runner=runner,
    )
    assert result["ready_for_live_benchmark"] is True
    assert result["process_timed_out"] is False
    assert set(result) == set(_ready_child_result(result["credential_binding_nonce"])) | {
        "process_timed_out",
        "process_exit_code",
        "ready_for_live_benchmark",
    }


def test_unverified_credential_is_not_live_ready(tmp_path) -> None:
    output = tmp_path / "result.json"

    def runner(command, **kwargs):
        assert kwargs["env"]["VIBECAD_PROVIDER_VALIDATE_CREDENTIALS"] == "0"
        nonce = kwargs["env"]["VIBECAD_PROVIDER_CREDENTIAL_BINDING_NONCE"]
        result = _ready_child_result(nonce)
        result.update({
            "auth_status": "configured_unverified",
            "credential_validation_performed": False,
            "model_validation_performed": False,
            "model_available": False,
        })
        output.write_text(json.dumps(result), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    result = run_probe(
        Path("FreeCADCmd"), Path("child.py"), output, 1, runner=runner,
    )
    assert result["ready_for_live_benchmark"] is False


def test_ambient_environment_credential_is_not_live_ready(tmp_path) -> None:
    output = tmp_path / "result.json"

    def runner(command, **kwargs):
        nonce = kwargs["env"]["VIBECAD_PROVIDER_CREDENTIAL_BINDING_NONCE"]
        output.write_text(
            json.dumps(
                _ready_child_result(nonce, auth_source="environment")
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    result = run_probe(
        Path("FreeCADCmd"), Path("child.py"), output, 1,
        validate_credentials=True, runner=runner,
    )
    assert result["ready_for_live_benchmark"] is False


def test_selected_model_must_be_present_in_bounded_discovery(tmp_path) -> None:
    output = tmp_path / "result.json"

    def runner(command, **kwargs):
        nonce = kwargs["env"]["VIBECAD_PROVIDER_CREDENTIAL_BINDING_NONCE"]
        result = _ready_child_result(nonce, model="missing")
        result["model_available"] = False
        output.write_text(json.dumps(result), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    result = run_probe(
        Path("FreeCADCmd"), Path("child.py"), output, 1,
        validate_credentials=True, runner=runner,
    )
    assert result["ready_for_live_benchmark"] is False


def test_stale_result_is_deleted_before_failed_probe(tmp_path) -> None:
    output = tmp_path / "result.json"
    output.write_text('{"can_call_provider":true}', encoding="utf-8")

    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=9)

    result = run_probe(Path("FreeCADCmd"), Path("child.py"), output, 1, runner=runner)
    assert result["ready_for_live_benchmark"] is False
    assert result["process_exit_code"] == 9


def test_endpoint_change_breaks_execution_binding() -> None:
    nonce = "12" * 32
    report = _ready_child_result(
        nonce, base_url="https://gateway.example.test/v1"
    )
    assert readiness_execution_identity_matches(
        report,
        provider="openai",
        base_url="https://gateway.example.test/v1/",
        auth_source="OS keyring",
        credential=_TEST_CREDENTIAL,
    )
    assert not readiness_execution_identity_matches(
        report,
        provider="openai",
        base_url="https://other.example.test/v1",
        auth_source="OS keyring",
        credential=_TEST_CREDENTIAL,
    )


def test_credential_change_breaks_execution_binding() -> None:
    nonce = "34" * 32
    report = _ready_child_result(nonce)
    assert readiness_execution_identity_matches(
        report,
        provider="openai",
        base_url=None,
        auth_source="OS keyring",
        credential=_TEST_CREDENTIAL,
    )
    assert not readiness_execution_identity_matches(
        report,
        provider="openai",
        base_url=None,
        auth_source="OS keyring",
        credential="different-provider-key",
    )


def test_chatgpt_account_change_breaks_execution_binding_without_identifier_output() -> None:
    nonce = "56" * 32
    account = {
        "type": "chatgpt",
        "accountId": "acct_7f68b3d8f1b24a64a0c7614f39487e83",
    }
    endpoint = canonical_provider_endpoint("chatgpt", None)
    report = {
        **_ready_child_result(nonce),
        "provider": "chatgpt",
        "auth_source": "Codex credential store",
        "endpoint_identity": endpoint,
        "endpoint_sha256": endpoint_identity_digest(endpoint),
        "credential_fingerprint": credential_fingerprint(
            provider="chatgpt",
            auth_source="Codex credential store",
            binding_nonce=nonce,
            chatgpt_account=account,
        ),
    }
    assert account["accountId"] not in json.dumps(report)
    assert readiness_execution_identity_matches(
        report,
        provider="chatgpt",
        base_url=None,
        auth_source="Codex credential store",
        chatgpt_account=account,
    )
    assert not readiness_execution_identity_matches(
        report,
        provider="chatgpt",
        base_url=None,
        auth_source="Codex credential store",
        chatgpt_account={
            "type": "chatgpt",
            "accountId": "acct_062b303b91d546bc903f7efcdacaa6f6",
        },
    )


def test_email_only_chatgpt_account_cannot_create_execution_binding() -> None:
    with pytest.raises(ValueError, match="high-entropy opaque"):
        credential_fingerprint(
            provider="chatgpt",
            auth_source="Codex credential store",
            binding_nonce="78" * 32,
            chatgpt_account={
                "type": "chatgpt",
                "email": "designer@example.test",
            },
        )


def test_email_only_chatgpt_account_uses_private_local_binding_key() -> None:
    account = {
        "type": "chatgpt",
        "email": "designer@example.test",
        "planType": "pro",
    }
    secret = "9a" * 32
    nonce = "78" * 32
    fingerprint = credential_fingerprint(
        provider="chatgpt",
        auth_source="Codex credential store",
        binding_nonce=nonce,
        chatgpt_account=account,
        chatgpt_binding_secret=secret,
    )
    report = {
        **_ready_child_result(nonce),
        "provider": "chatgpt",
        "auth_source": "Codex credential store",
        "endpoint_identity": canonical_provider_endpoint("chatgpt", None),
        "endpoint_sha256": endpoint_identity_digest(
            canonical_provider_endpoint("chatgpt", None)
        ),
        "credential_fingerprint": fingerprint,
    }

    assert account["email"] not in json.dumps(report)
    assert readiness_execution_identity_matches(
        report,
        provider="chatgpt",
        base_url=None,
        auth_source="Codex credential store",
        chatgpt_account=account,
        chatgpt_binding_secret=secret,
    )
    assert not readiness_execution_identity_matches(
        report,
        provider="chatgpt",
        base_url=None,
        auth_source="Codex credential store",
        chatgpt_account=account,
        chatgpt_binding_secret="8b" * 32,
    )


def test_readiness_fingerprint_does_not_disclose_credential(tmp_path) -> None:
    output = tmp_path / "result.json"

    def runner(command, **kwargs):
        nonce = kwargs["env"]["VIBECAD_PROVIDER_CREDENTIAL_BINDING_NONCE"]
        output.write_text(
            json.dumps(_ready_child_result(nonce)), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0)

    result = run_probe(
        Path("FreeCADCmd"),
        Path("child.py"),
        output,
        1,
        validate_credentials=True,
        runner=runner,
    )
    assert _TEST_CREDENTIAL not in json.dumps(result)
    assert result["credential_fingerprint"] != _TEST_CREDENTIAL


def test_unknown_ready_field_fails_closed(tmp_path) -> None:
    output = tmp_path / "result.json"

    def runner(command, **kwargs):
        nonce = kwargs["env"]["VIBECAD_PROVIDER_CREDENTIAL_BINDING_NONCE"]
        result = _ready_child_result(nonce)
        result["unexpected"] = True
        output.write_text(json.dumps(result), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    result = run_probe(
        Path("FreeCADCmd"),
        Path("child.py"),
        output,
        1,
        validate_credentials=True,
        runner=runner,
    )
    assert result["ready_for_live_benchmark"] is False


def test_endpoint_with_embedded_secret_is_rejected() -> None:
    with pytest.raises(ValueError, match="user information"):
        canonical_provider_endpoint(
            "openai", "https://secret@example.test/v1"
        )
