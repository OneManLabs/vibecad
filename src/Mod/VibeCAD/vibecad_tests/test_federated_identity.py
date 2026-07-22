# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import base64
import json
import sys
import threading
import time
import types
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import pytest
import VibeCADFederatedIdentity as federation

from VibeCADFederatedIdentity import (
    clear_oidc_metadata_cache,
    complete_oidc_authorization,
    create_oidc_authorization_request,
    delete_oidc_session,
    fetch_oidc_metadata,
    oidc_config_from_policy,
    read_oidc_session,
    read_oidc_session_record,
    refresh_oidc_principal,
    run_oidc_browser_login,
    store_oidc_session,
    validate_oidc_callback,
    validate_oidc_id_token,
)
from VibeCADIdentity import authorize
from VibeCADManagedPolicy import default_policy, validate_policy


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _fixture(*, now: int = 2_000_000_000):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private.public_key().public_numbers()
    integer = lambda value: value.to_bytes((value.bit_length() + 7) // 8, "big")
    jwk = {
        "kty": "RSA", "use": "sig", "kid": "key-1", "alg": "RS256",
        "n": _b64(integer(numbers.n)), "e": _b64(integer(numbers.e)),
    }
    config = {
        "issuer": "https://identity.example.com",
        "client_id": "vibecad-desktop",
        "allowed_hosts": ["identity.example.com"],
        "allowed_algorithms": ["RS256"],
        "role_claim": "groups",
        "role_mapping": {"cad-designers": "designer", "cad-reviewers": "reviewer"},
        "organization_id": "org-1",
    }
    discovery = {"issuer": config["issuer"], "jwks_uri": f"{config['issuer']}/keys"}
    claims = {
        "iss": config["issuer"], "aud": config["client_id"], "sub": "user-123",
        "iat": now - 10, "nbf": now - 10, "exp": now + 600,
        "nonce": "nonce-1", "groups": ["cad-designers"],
    }

    def token(values=None, header=None, signing_key=private):
        protected = {"alg": "RS256", "kid": "key-1", "typ": "JWT"}
        protected.update(header or {})
        payload = dict(claims)
        payload.update(values or {})
        signing_input = f"{_b64(json.dumps(protected, separators=(',', ':')).encode())}.{_b64(json.dumps(payload, separators=(',', ':')).encode())}"
        signature = signing_key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{signing_input}.{_b64(signature)}"

    return config, discovery, {"keys": [jwk]}, token, now


def test_valid_oidc_token_maps_role_and_expiry_to_principal() -> None:
    config, discovery, jwks, token, now = _fixture()
    principal = validate_oidc_id_token(
        token(), config=config, discovery=discovery, jwks=jwks, nonce="nonce-1", now=now
    )
    assert principal["source"] == "oidc"
    assert principal["roles"] == ["designer"]
    assert principal["organization_id"] == "org-1"
    assert principal["session_expires_at"] == now + 600
    assert principal["subject"] == "user-123"


@pytest.mark.parametrize(
    ("claim", "value", "message"),
    [
        ("iss", "https://evil.example", "issuer"),
        ("aud", "other-client", "audience"),
        ("exp", 1_999_999_000, "expired"),
        ("iat", 2_000_001_000, "issued-at"),
        ("nbf", 2_000_001_000, "not active"),
        ("nonce", "wrong", "nonce"),
        ("sub", "", "subject"),
    ],
)
def test_oidc_security_claim_failures_are_rejected(claim, value, message) -> None:
    config, discovery, jwks, token, now = _fixture()
    with pytest.raises(RuntimeError, match=message):
        validate_oidc_id_token(
            token({claim: value}), config=config, discovery=discovery,
            jwks=jwks, nonce="nonce-1", now=now,
        )


def test_oidc_rejects_signature_tamper_unknown_key_and_none_algorithm() -> None:
    config, discovery, jwks, token, now = _fixture()
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(RuntimeError, match="signature"):
        validate_oidc_id_token(
            token(signing_key=other), config=config, discovery=discovery,
            jwks=jwks, nonce="nonce-1", now=now,
        )
    with pytest.raises(RuntimeError, match="key identity"):
        validate_oidc_id_token(
            token(header={"kid": "missing"}), config=config, discovery=discovery,
            jwks=jwks, nonce="nonce-1", now=now,
        )
    with pytest.raises(RuntimeError, match="unapproved algorithm"):
        validate_oidc_id_token(
            token(header={"alg": "none"}), config=config, discovery=discovery,
            jwks=jwks, nonce="nonce-1", now=now,
        )


def test_unmapped_oidc_group_fails_safe_to_viewer() -> None:
    config, discovery, jwks, token, now = _fixture()
    principal = validate_oidc_id_token(
        token({"groups": ["unknown"]}), config=config, discovery=discovery,
        jwks=jwks, nonce="nonce-1", now=now,
    )
    assert principal["roles"] == ["viewer"]


def test_managed_oidc_policy_requires_https_approved_issuer_and_algorithm() -> None:
    policy = default_policy()
    policy.update(
        managed=True, identity_mode="oidc",
        oidc_issuer="https://identity.example.com", oidc_client_id="desktop",
        oidc_allowed_hosts=["identity.example.com"],
        oidc_role_mapping={"designers": "designer"},
    )
    clean = validate_policy(policy)
    assert oidc_config_from_policy(clean)["issuer"] == "https://identity.example.com"
    for field, value, message in (
        ("oidc_issuer", "http://identity.example.com", "HTTPS"),
        ("oidc_allowed_hosts", ["other.example"], "not allowed"),
        ("oidc_allowed_algorithms", ["HS256"], "unsupported"),
    ):
        candidate = dict(policy)
        candidate[field] = value
        with pytest.raises(RuntimeError, match=message):
            validate_policy(candidate)


def test_oidc_session_token_uses_keyring_only(monkeypatch) -> None:
    values = {}
    errors = types.SimpleNamespace(PasswordDeleteError=KeyError)
    fake = types.SimpleNamespace(
        errors=errors,
        set_password=lambda service, user, value: values.__setitem__((service, user), value),
        get_password=lambda service, user: values.get((service, user)),
        delete_password=lambda service, user: values.pop((service, user)),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake)
    store_oidc_session("org-1", "signed.id.token")
    assert read_oidc_session("org-1") == "signed.id.token"
    delete_oidc_session("org-1")
    assert read_oidc_session("org-1") is None


def test_authorization_rejects_expired_federated_principal(monkeypatch) -> None:
    config, discovery, jwks, token, now = _fixture()
    principal = validate_oidc_id_token(
        token(), config=config, discovery=discovery, jwks=jwks, nonce="nonce-1", now=now
    )
    monkeypatch.setattr(time, "time", lambda: now + 1000)
    with pytest.raises(PermissionError, match="expired"):
        authorize(principal, "project.view")


def test_oidc_metadata_uses_bounded_cache(monkeypatch) -> None:
    config, discovery, jwks, _, _ = _fixture()
    responses = [discovery, jwks]
    calls = []
    monkeypatch.setattr(
        federation, "_read_json",
        lambda url, **kwargs: calls.append(url) or responses.pop(0),
    )
    clear_oidc_metadata_cache()
    assert fetch_oidc_metadata(config) == (discovery, jwks)
    assert fetch_oidc_metadata(config) == (discovery, jwks)
    assert len(calls) == 2
    clear_oidc_metadata_cache()


def test_oidc_pkce_request_and_callback_contract(monkeypatch) -> None:
    config, discovery, _, _, now = _fixture()
    discovery.update(
        authorization_endpoint=f"{config['issuer']}/authorize",
        token_endpoint=f"{config['issuer']}/token",
    )
    monkeypatch.setattr(time, "time", lambda: now)
    url, transaction = create_oidc_authorization_request(
        config, discovery, redirect_uri="http://127.0.0.1:49152/callback"
    )
    query = parse_qs(urlparse(url).query)
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert "code_challenge" in query
    assert "code_verifier" not in query
    callback = (
        f"{transaction['redirect_uri']}?code=one-time-code&state={transaction['state']}"
    )
    assert validate_oidc_callback(callback, transaction) == "one-time-code"
    with pytest.raises(RuntimeError, match="state"):
        validate_oidc_callback(
            f"{transaction['redirect_uri']}?code=x&state=wrong", transaction
        )


def test_oidc_pkce_completion_validates_before_keychain_write(monkeypatch) -> None:
    config, discovery, jwks, token, now = _fixture(now=int(time.time()))
    discovery.update(
        authorization_endpoint=f"{config['issuer']}/authorize",
        token_endpoint=f"{config['issuer']}/token",
    )
    _, transaction = create_oidc_authorization_request(
        config, discovery, redirect_uri="http://localhost:49152/callback"
    )
    values = {}
    fake = types.SimpleNamespace(
        errors=types.SimpleNamespace(PasswordDeleteError=KeyError),
        set_password=lambda service, user, value: values.__setitem__((service, user), value),
        get_password=lambda service, user: values.get((service, user)),
        delete_password=lambda service, user: values.pop((service, user)),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake)
    valid_token = token({"nonce": transaction["nonce"]})
    principal = complete_oidc_authorization(
        "code", config=config, discovery=discovery, jwks=jwks,
        transaction=transaction,
        token_request=lambda endpoint, fields, hosts: {"id_token": valid_token},
    )
    assert principal["roles"] == ["designer"]
    assert read_oidc_session("org-1") == valid_token
    delete_oidc_session("org-1")
    with pytest.raises(RuntimeError, match="three segments"):
        complete_oidc_authorization(
            "code", config=config, discovery=discovery, jwks=jwks,
            transaction=transaction,
            token_request=lambda endpoint, fields, hosts: {"id_token": "bad"},
        )
    assert read_oidc_session("org-1") is None


def test_oidc_loopback_browser_login_completes_valid_session(monkeypatch) -> None:
    config, discovery, jwks, token, _ = _fixture(now=int(time.time()))
    discovery.update(
        authorization_endpoint=f"{config['issuer']}/authorize",
        token_endpoint=f"{config['issuer']}/token",
    )
    policy = default_policy()
    policy.update(
        managed=True, identity_mode="oidc", organization_id="org-1",
        oidc_issuer=config["issuer"], oidc_client_id=config["client_id"],
        oidc_allowed_hosts=config["allowed_hosts"],
        oidc_role_claim="groups", oidc_role_mapping=config["role_mapping"],
    )
    values = {}
    fake = types.SimpleNamespace(
        errors=types.SimpleNamespace(PasswordDeleteError=KeyError),
        set_password=lambda service, user, value: values.__setitem__((service, user), value),
        get_password=lambda service, user: values.get((service, user)),
        delete_password=lambda service, user: values.pop((service, user)),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake)
    response = {}

    def open_browser(url):
        query = parse_qs(urlparse(url).query)
        response["id_token"] = token({"nonce": query["nonce"][0]})
        callback = f"{query['redirect_uri'][0]}?code=single-use&state={query['state'][0]}"
        threading.Thread(target=lambda: urlopen(callback, timeout=2).read(), daemon=True).start()
        return True

    principal = run_oidc_browser_login(
        policy, open_browser=open_browser, timeout=2,
        metadata=(discovery, jwks),
        token_request=lambda endpoint, fields, hosts: response,
    )
    assert principal["source"] == "oidc"
    assert principal["roles"] == ["designer"]
    assert read_oidc_session("org-1") == response["id_token"]


def test_oidc_refresh_renews_validated_session_without_client_secret(monkeypatch) -> None:
    config, discovery, jwks, token, _ = _fixture(now=int(time.time()))
    discovery["token_endpoint"] = f"{config['issuer']}/token"
    values = {}
    fake = types.SimpleNamespace(
        errors=types.SimpleNamespace(PasswordDeleteError=KeyError),
        set_password=lambda service, user, value: values.__setitem__((service, user), value),
        get_password=lambda service, user: values.get((service, user)),
        delete_password=lambda service, user: values.pop((service, user)),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake)
    renewed = token()
    captured = {}

    def request(endpoint, fields, hosts):
        captured.update(fields)
        return {"id_token": renewed, "refresh_token": "rotated-refresh"}

    principal = refresh_oidc_principal(
        config, discovery=discovery, jwks=jwks,
        record={"refresh_token": "old-refresh"}, token_request=request,
    )
    assert principal["roles"] == ["designer"]
    assert captured == {
        "grant_type": "refresh_token", "refresh_token": "old-refresh",
        "client_id": "vibecad-desktop",
    }
    assert "client_secret" not in captured
    assert read_oidc_session_record("org-1")["refresh_token"] == "rotated-refresh"


def test_managed_oidc_scopes_require_openid() -> None:
    policy = default_policy()
    policy.update(
        managed=True, identity_mode="oidc",
        oidc_issuer="https://identity.example.com", oidc_client_id="desktop",
        oidc_allowed_hosts=["identity.example.com"], oidc_scopes=["profile"],
    )
    with pytest.raises(RuntimeError, match="scopes"):
        validate_policy(policy)
