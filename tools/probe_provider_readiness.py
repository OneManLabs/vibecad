#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bound provider authentication preflight without sending design data."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import subprocess
from typing import Any, Callable
from urllib.parse import SplitResult, urlsplit, urlunsplit

try:
    from tools.vibecad_secure_process import (
        minimal_child_environment,
        run_bounded_process,
        validate_finite_timeout,
    )
except ModuleNotFoundError:  # Direct execution from the tools directory.
    from vibecad_secure_process import (  # type: ignore[no-redef]
        minimal_child_environment,
        run_bounded_process,
        validate_finite_timeout,
    )
try:
    from tools.vibecad_benchmark_evidence_io import (
        EvidenceIOError,
        load_bounded_json,
    )
except ModuleNotFoundError:  # Direct execution from the tools directory.
    from vibecad_benchmark_evidence_io import (  # type: ignore[no-redef]
        EvidenceIOError,
        load_bounded_json,
    )


READINESS_SCHEMA = "vibecad-provider-readiness-v1"
READINESS_VERSION = 1
CREDENTIAL_FINGERPRINT_ALGORITHM = "hmac-sha256-v1"
_DEFAULT_PROVIDER_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "chatgpt": "codex-app-server://managed-chatgpt",
}
_READY_CHILD_FIELDS = {
    "schema",
    "version",
    "created_at",
    "can_call_provider",
    "prompt_sent",
    "document_data_sent",
    "credential_validation_performed",
    "model_validation_performed",
    "model_available",
    "stage",
    "provider",
    "model",
    "auth_status",
    "auth_source",
    "online_by_default",
    "endpoint_identity",
    "endpoint_sha256",
    "credential_binding_nonce",
    "credential_fingerprint_algorithm",
    "credential_fingerprint",
}


def _lower_hex(value: object, length: int, label: str) -> str:
    clean = str(value or "")
    if len(clean) != length or any(character not in "0123456789abcdef" for character in clean):
        raise ValueError(f"{label} must be {length} lowercase hexadecimal characters.")
    return clean


def _canonical_network_endpoint(value: str) -> str:
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError("The provider endpoint contains whitespace or a control character.")
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("An API provider endpoint must use HTTP or HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("The provider endpoint must not contain user information.")
    if parsed.query or parsed.fragment:
        raise ValueError("The provider endpoint must not contain a query or fragment.")
    hostname = str(parsed.hostname or "").strip().lower()
    if not hostname:
        raise ValueError("The provider endpoint must contain a host name.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("The provider endpoint has an invalid port.") from exc
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    include_port = port is not None and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    )
    netloc = f"{hostname}:{port}" if include_port else hostname
    path = parsed.path.rstrip("/")
    return urlunsplit(SplitResult(scheme, netloc, path, "", ""))


def canonical_provider_endpoint(provider: str, base_url: str | None) -> str:
    """Return the sanitized endpoint identity used by a provider adapter."""

    clean_provider = str(provider or "").strip().lower()
    if clean_provider not in _DEFAULT_PROVIDER_ENDPOINTS:
        raise ValueError("The provider has no known endpoint identity.")
    if clean_provider == "chatgpt":
        if str(base_url or "").strip():
            raise ValueError("The managed ChatGPT provider does not accept a base URL.")
        return _DEFAULT_PROVIDER_ENDPOINTS[clean_provider]
    configured = str(base_url or "").strip()
    return _canonical_network_endpoint(
        configured or _DEFAULT_PROVIDER_ENDPOINTS[clean_provider]
    )


def endpoint_identity_digest(endpoint_identity: str) -> str:
    """Hash one canonical endpoint identity."""

    clean = str(endpoint_identity or "").strip()
    if not clean:
        raise ValueError("The endpoint identity is empty.")
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def chatgpt_account_binding_material(account: object) -> str:
    """Select a stable account identifier without returning it in evidence."""

    if not isinstance(account, dict) or account.get("type") != "chatgpt":
        raise ValueError("A verified ChatGPT account is required.")
    for field in ("accountId", "account_id", "id"):
        value = str(account.get(field) or "").strip()
        if value:
            return f"{field}:{value}"
    raise ValueError(
        "The ChatGPT account has no high-entropy opaque account identifier."
    )


def credential_fingerprint(
    *,
    provider: str,
    auth_source: str,
    binding_nonce: str,
    credential: str | None = None,
    chatgpt_account: object = None,
) -> str:
    """Create a run-bound digest without returning credential material."""

    clean_provider = str(provider or "").strip().lower()
    clean_source = str(auth_source or "").strip()
    nonce = _lower_hex(binding_nonce, 64, "The credential binding nonce")
    if not clean_provider or not clean_source or clean_source == "environment":
        raise ValueError("A configured, non-ambient credential source is required.")
    if clean_provider == "chatgpt":
        secret_material = chatgpt_account_binding_material(chatgpt_account)
    else:
        secret_material = str(credential or "")
        if not secret_material:
            raise ValueError("The selected provider credential is empty.")
    message = json.dumps(
        {
            "schema": "vibecad-provider-execution-binding-v1",
            "provider": clean_provider,
            "auth_source": clean_source,
            "binding_nonce": nonce,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(secret_material.encode("utf-8"), message, hashlib.sha256).hexdigest()


def readiness_execution_identity_matches(
    report: dict[str, Any],
    *,
    provider: str,
    base_url: str | None,
    auth_source: str,
    credential: str | None = None,
    chatgpt_account: object = None,
) -> bool:
    """Compare current execution identity with verified readiness evidence."""

    try:
        endpoint = canonical_provider_endpoint(provider, base_url)
        fingerprint = credential_fingerprint(
            provider=provider,
            auth_source=auth_source,
            binding_nonce=str(report.get("credential_binding_nonce") or ""),
            credential=credential,
            chatgpt_account=chatgpt_account,
        )
        return bool(
            report.get("provider") == str(provider or "").strip().lower()
            and report.get("auth_source") == str(auth_source or "").strip()
            and report.get("endpoint_identity") == endpoint
            and hmac.compare_digest(
                str(report.get("endpoint_sha256") or ""),
                endpoint_identity_digest(endpoint),
            )
            and report.get("credential_fingerprint_algorithm")
            == CREDENTIAL_FINGERPRINT_ALGORITHM
            and hmac.compare_digest(
                str(report.get("credential_fingerprint") or ""), fingerprint
            )
        )
    except (TypeError, ValueError):
        return False


def _ready_child_identity_is_valid(result: dict[str, Any], binding_nonce: str) -> bool:
    if set(result) != _READY_CHILD_FIELDS:
        return False
    try:
        provider = str(result.get("provider") or "").strip().lower()
        endpoint = str(result.get("endpoint_identity") or "")
        if provider == "chatgpt":
            endpoint_is_canonical = endpoint == canonical_provider_endpoint(
                provider, None
            )
        else:
            endpoint_is_canonical = endpoint == _canonical_network_endpoint(endpoint)
        return bool(
            endpoint_is_canonical
            and hmac.compare_digest(
                _lower_hex(
                    result.get("endpoint_sha256"), 64, "The endpoint digest"
                ),
                endpoint_identity_digest(endpoint),
            )
            and result.get("credential_binding_nonce") == binding_nonce
            and result.get("credential_fingerprint_algorithm")
            == CREDENTIAL_FINGERPRINT_ALGORITHM
            and bool(
                _lower_hex(
                    result.get("credential_fingerprint"),
                    64,
                    "The credential fingerprint",
                )
            )
        )
    except (TypeError, ValueError):
        return False


def run_probe(
    freecad: Path,
    child: Path,
    output: Path,
    timeout_seconds: float,
    *,
    validate_credentials: bool = False,
    credential_validation_timeout: float = 5.0,
    credential_binding_nonce: str | None = None,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    timeout_seconds = validate_finite_timeout(
        timeout_seconds,
        label="The readiness timeout",
        maximum=60,
    )
    credential_validation_timeout = validate_finite_timeout(
        credential_validation_timeout,
        label="The credential check timeout",
        maximum=15,
    )
    binding_nonce = _lower_hex(
        credential_binding_nonce or secrets.token_hex(32),
        64,
        "The credential binding nonce",
    )
    output.unlink(missing_ok=True)
    timed_out = False
    return_code = None
    try:
        environment = minimal_child_environment(
            {
                "VIBECAD_PROVIDER_READINESS_OUTPUT": str(output.resolve()),
                "VIBECAD_PROVIDER_VALIDATE_CREDENTIALS": (
                    "1" if validate_credentials else "0"
                ),
                "VIBECAD_PROVIDER_VALIDATION_TIMEOUT": str(
                    credential_validation_timeout
                ),
                "VIBECAD_PROVIDER_CREDENTIAL_BINDING_NONCE": binding_nonce,
            }
        )
        expression = (
            "import runpy; "
            f"runpy.run_path({str(child)!r}, run_name='__main__')"
        )
        process_runner = runner or run_bounded_process
        completed = process_runner(
            [str(freecad), "-c", expression], env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout_seconds, check=False,
        )
        return_code = completed.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
    if output.exists():
        try:
            snapshot, result = load_bounded_json(
                output,
                max_bytes=256 * 1024,
                label="provider readiness result",
            )
            with snapshot:
                snapshot.verify_unchanged()
            if not isinstance(result, dict):
                raise EvidenceIOError(
                    "The provider readiness result must be a JSON object."
                )
        except (EvidenceIOError, UnicodeError, ValueError) as exc:
            result = {
                "schema": "vibecad-provider-readiness-v1",
                "version": 1,
                "can_call_provider": False,
                "prompt_sent": False,
                "document_data_sent": False,
                "error": f"Provider readiness evidence was rejected: {exc}",
            }
    else:
        result = {
            "schema": "vibecad-provider-readiness-v1", "version": 1,
            "can_call_provider": False, "prompt_sent": False,
            "document_data_sent": False,
            "error": "Provider readiness did not return before the timeout." if timed_out else "Provider readiness produced no result.",
        }
    child_identity_valid = _ready_child_identity_is_valid(result, binding_nonce)
    result["process_timed_out"] = timed_out
    result["process_exit_code"] = return_code
    result["ready_for_live_benchmark"] = bool(
        child_identity_valid
        and result.get("schema") == READINESS_SCHEMA
        and result.get("version") == READINESS_VERSION
        and return_code == 0
        and result.get("credential_validation_performed") is True
        and result.get("model_validation_performed") is True
        and result.get("model_available") is True
        and result.get("auth_status") == "verified"
        and result.get("can_call_provider") is True
        and isinstance(result.get("provider"), str)
        and bool(result.get("provider").strip())
        and isinstance(result.get("model"), str)
        and bool(result.get("model").strip())
        and result.get("auth_source") != "environment"
        and result.get("prompt_sent") is False
        and result.get("document_data_sent") is False
        and result.get("stage") == "complete"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freecad", default="build/release/bin/FreeCADCmd")
    parser.add_argument("--output", default="build/benchmark/provider-readiness.json")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--validate-credentials",
        action="store_true",
        help="Verify the selected credential without sending prompt or CAD data.",
    )
    parser.add_argument(
        "--credential-validation-timeout",
        type=float,
        default=5.0,
        help="Set the credential check timeout in seconds.",
    )
    args = parser.parse_args()
    if args.timeout <= 0 or args.timeout > 60:
        raise ValueError("Timeout must be greater than zero and at most 60 seconds.")
    if not 0 < args.credential_validation_timeout <= 15:
        raise ValueError(
            "The credential check timeout must be greater than zero and at most 15 seconds."
        )
    root = Path(__file__).resolve().parents[1]
    result = run_probe(
        (root / args.freecad).resolve(),
        (root / "tools" / "provider_readiness_child.py").resolve(),
        (root / args.output).resolve(),
        args.timeout,
        validate_credentials=args.validate_credentials,
        credential_validation_timeout=args.credential_validation_timeout,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ready_for_live_benchmark"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
