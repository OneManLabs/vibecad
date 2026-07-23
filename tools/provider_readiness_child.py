#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Read provider readiness in FreeCAD without sending a prompt or document."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

repository_root = Path(__file__).resolve(strict=True).parents[1]
repository_text = os.fspath(repository_root)
if repository_text not in sys.path:
    sys.path.insert(0, repository_text)

from tools.probe_provider_readiness import (
    CREDENTIAL_FINGERPRINT_ALGORITHM,
    canonical_provider_endpoint,
    credential_fingerprint,
    endpoint_identity_digest,
)

def _write(target: Path, report: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    configured_output = str(os.environ.get("VIBECAD_PROVIDER_READINESS_OUTPUT") or "").strip()
    if not configured_output:
        raise RuntimeError("VIBECAD_PROVIDER_READINESS_OUTPUT is required.")
    target = Path(configured_output).resolve()
    base = {
        "schema": "vibecad-provider-readiness-v1", "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "can_call_provider": False, "prompt_sent": False, "document_data_sent": False,
        "credential_validation_performed": False,
        "model_validation_performed": False,
        "model_available": False,
    }
    _write(target, {**base, "stage": "importing_service"})
    from VibeCADCore import VibeCADService

    _write(target, {**base, "stage": "creating_service"})
    service = VibeCADService()
    _write(target, {**base, "stage": "reading_provider"})
    provider = service.provider_name()
    model = service.provider_model()
    base_url = service.provider_base_url()
    binding_nonce = str(
        os.environ.get("VIBECAD_PROVIDER_CREDENTIAL_BINDING_NONCE") or ""
    ).strip()
    if len(binding_nonce) != 64 or any(
        character not in "0123456789abcdef" for character in binding_nonce
    ):
        raise RuntimeError(
            "The credential binding nonce must be 64 lowercase hexadecimal characters."
        )
    endpoint_identity = canonical_provider_endpoint(provider, base_url)
    identity = {
        "endpoint_identity": endpoint_identity,
        "endpoint_sha256": endpoint_identity_digest(endpoint_identity),
        "credential_binding_nonce": binding_nonce,
        "credential_fingerprint_algorithm": CREDENTIAL_FINGERPRINT_ALGORITHM,
        "credential_fingerprint": None,
    }
    _write(target, {
        **base,
        "stage": "reading_dotenv_path",
        "provider": provider,
        "model": model,
    })
    dotenv_path = service._dotenv_path()
    _write(target, {**base, "stage": "resolving_auth", "provider": provider})
    from VibeCADAuth import (
        AuthState,
        AuthStatus,
        list_provider_models,
        resolve_auth_credential,
        resolve_auth_state,
    )

    auth = resolve_auth_state(dotenv_path=dotenv_path, provider=provider)
    validate_credentials = (
        str(os.environ.get("VIBECAD_PROVIDER_VALIDATE_CREDENTIALS") or "0") == "1"
    )
    validation_performed = False
    model_validation_performed = False
    model_available = False
    credential_value = None
    validated_account = None
    if validate_credentials:
        if auth.source == "environment":
            report = {
                **base,
                "stage": "complete",
                "provider": provider,
                "model": model,
                "auth_status": auth.status.value,
                "auth_source": auth.source,
                **identity,
                "error": (
                    "The live benchmark does not use an ambient environment credential. "
                    "Store the selected provider credential in the configured credential store."
                ),
                "online_by_default": bool(service.use_online_provider_by_default()),
            }
            _write(target, report)
            return 0
        from VibeCADManagedPolicy import enforce_provider, load_managed_policy

        policy = load_managed_policy()
        enforce_provider(policy, provider, model, base_url)
        timeout = float(
            os.environ.get("VIBECAD_PROVIDER_VALIDATION_TIMEOUT") or "5"
        )
        if not 0 < timeout <= 15:
            raise RuntimeError(
                "The credential check timeout must be greater than zero and at most 15 seconds."
            )
        _write(target, {
            **base,
            "stage": "validating_auth",
            "provider": provider,
            "model": model,
        })
        if provider == "chatgpt":
            from VibeCADCodex import CodexAppServerClient, account_binding_secret

            discovered: list[str] = []
            discovered_default = ""
            with CodexAppServerClient() as client:
                account_result = client.request(
                    "account/read", {"refreshToken": True}, timeout=timeout
                )
                account = (
                    account_result.get("account")
                    if isinstance(account_result, dict)
                    else None
                )
                validated_account = account
                cursor = None
                model_list_complete = False
                for _page in range(2):
                    parameters = {"limit": 100, "includeHidden": False}
                    if cursor:
                        parameters["cursor"] = cursor
                    model_result = client.request(
                        "model/list", parameters, timeout=timeout
                    )
                    for item in (
                        model_result.get("data", [])
                        if isinstance(model_result, dict)
                        else []
                    ):
                        if isinstance(item, dict) and str(item.get("id") or ""):
                            model_id = str(item["id"])
                            discovered.append(model_id)
                            if item.get("isDefault"):
                                discovered_default = model_id
                    cursor = (
                        str(model_result.get("nextCursor") or "").strip()
                        if isinstance(model_result, dict)
                        else ""
                    )
                    if not cursor:
                        model_list_complete = True
                        break
            if not model:
                model = discovered_default
            account_verified = bool(
                isinstance(account, dict) and account.get("type") == "chatgpt"
            )
            auth = AuthState(
                AuthStatus.VERIFIED if account_verified else AuthStatus.INVALID,
                source=auth.source,
                message=(
                    "The bounded Codex account check found a ChatGPT subscription."
                    if account_verified
                    else "The bounded Codex account check found no ChatGPT subscription."
                ),
            )
            models = {
                "ok": account_verified and model_list_complete,
                "models": sorted(set(discovered)),
                "error": (
                    None
                    if account_verified and model_list_complete
                    else "The bounded Codex account or model check was incomplete."
                ),
            }
        else:
            resolved_credential = resolve_auth_credential(
                dotenv_path=dotenv_path,
                provider=provider,
            )
            if (
                resolved_credential is None
                or resolved_credential.source != auth.source
                or resolved_credential.source == "environment"
            ):
                raise RuntimeError(
                    "The selected non-ambient credential changed during validation."
                )
            credential_value = resolved_credential.value
            models = list_provider_models(
                credential_value,
                provider=provider,
                timeout_seconds=timeout,
                base_url=base_url,
                max_pages=2,
            )
            auth = AuthState(
                AuthStatus.VERIFIED if models.get("ok") else AuthStatus.INVALID,
                source=auth.source,
                message=(
                    "The configured provider returned its bounded model list."
                    if models.get("ok")
                    else str(models.get("error") or "Provider model discovery failed.")
                ),
            )
        validation_performed = True
        model_validation_performed = True
        model_available = bool(
            models.get("ok") and model and model in (models.get("models") or [])
        )
        if auth.status is AuthStatus.VERIFIED and model_available:
            identity["credential_fingerprint"] = credential_fingerprint(
                provider=provider,
                auth_source=str(auth.source or ""),
                binding_nonce=binding_nonce,
                credential=credential_value,
                chatgpt_account=validated_account,
                chatgpt_binding_secret=(
                    account_binding_secret() if provider == "chatgpt" else None
                ),
            )
    report = {
        **base, "stage": "complete",
        "provider": provider, "model": model,
        "auth_status": auth.status.value, "auth_source": auth.source,
        "can_call_provider": bool(auth.can_call_provider),
        "credential_validation_performed": validation_performed,
        "model_validation_performed": model_validation_performed,
        "model_available": model_available,
        "online_by_default": bool(service.use_online_provider_by_default()),
        **identity,
    }
    _write(target, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
