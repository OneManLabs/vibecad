# SPDX-License-Identifier: LGPL-2.1-or-later
"""Strict OIDC token validation and Keychain-backed federated sessions."""

from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import secrets
import threading
import time
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from VibeCADIdentity import PRINCIPAL_SCHEMA, PRINCIPAL_VERSION, validate_principal


MAX_ID_TOKEN_BYTES = 64 * 1024
MAX_OIDC_METADATA_BYTES = 1024 * 1024
OIDC_CLOCK_SKEW_SECONDS = 60
KEYRING_SERVICE = "com.vibecad.desktop.identity"
OIDC_SESSION_SCHEMA = "vibecad-oidc-session-v1"
OIDC_METADATA_CACHE_SECONDS = 3600
_METADATA_CACHE: dict[str, tuple[float, dict[str, Any], dict[str, Any]]] = {}
_METADATA_LOCK = threading.Lock()


def _b64url(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("The OIDC token contains invalid Base64URL data.") from exc


class _OIDCRedirect(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlparse(newurl)
        if target.scheme != "https" or target.hostname not in self.allowed_hosts:
            raise RuntimeError("The OIDC request redirected to an unapproved endpoint.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_json(
    url: str, *, allowed_hosts: set[str], network_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise RuntimeError("The OIDC metadata endpoint is not allowed.")
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "VibeCAD-OIDC/1"})
    from VibeCADNetwork import build_managed_opener
    with build_managed_opener(network_policy or {}, _OIDCRedirect(allowed_hosts)).open(request, timeout=15) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or final.hostname not in allowed_hosts:
            raise RuntimeError("The OIDC metadata endpoint changed unexpectedly.")
        payload = response.read(MAX_OIDC_METADATA_BYTES + 1)
    if len(payload) > MAX_OIDC_METADATA_BYTES:
        raise RuntimeError("The OIDC metadata exceeds its size limit.")
    try:
        raw = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("The OIDC metadata is not valid JSON.") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("The OIDC metadata is not a JSON object.")
    return raw


def fetch_oidc_metadata(
    config: Mapping[str, Any], *, use_cache: bool = True
) -> tuple[dict[str, Any], dict[str, Any]]:
    issuer = str(config.get("issuer") or "").rstrip("/")
    allowed_hosts = set(config.get("allowed_hosts") or [])
    issuer_url = urlparse(issuer)
    if issuer_url.scheme != "https" or issuer_url.hostname not in allowed_hosts:
        raise RuntimeError("The managed OIDC issuer is not allowed.")
    if use_cache:
        with _METADATA_LOCK:
            cached = _METADATA_CACHE.get(issuer)
            if cached and cached[0] > time.monotonic():
                return dict(cached[1]), dict(cached[2])
    discovery = _read_json(f"{issuer}/.well-known/openid-configuration", allowed_hosts=allowed_hosts, network_policy=config)
    if str(discovery.get("issuer") or "").rstrip("/") != issuer:
        raise RuntimeError("The OIDC discovery issuer does not match managed policy.")
    jwks_uri = str(discovery.get("jwks_uri") or "")
    jwks = _read_json(jwks_uri, allowed_hosts=allowed_hosts, network_policy=config)
    with _METADATA_LOCK:
        _METADATA_CACHE[issuer] = (
            time.monotonic() + OIDC_METADATA_CACHE_SECONDS,
            dict(discovery),
            dict(jwks),
        )
    return discovery, jwks


def clear_oidc_metadata_cache() -> None:
    with _METADATA_LOCK:
        _METADATA_CACHE.clear()


def _approved_endpoint(value: Any, config: Mapping[str, Any], name: str) -> str:
    endpoint = str(value or "")
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or parsed.hostname not in set(config.get("allowed_hosts") or []):
        raise RuntimeError(f"The OIDC {name} endpoint is not allowed.")
    return endpoint


def create_oidc_authorization_request(
    config: Mapping[str, Any], discovery: Mapping[str, Any], *, redirect_uri: str
) -> tuple[str, dict[str, str]]:
    """Create one public-client authorization-code request with PKCE."""
    redirect = urlparse(redirect_uri)
    if redirect.scheme != "http" or redirect.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("The OIDC callback must use a local loopback address.")
    endpoint = _approved_endpoint(discovery.get("authorization_endpoint"), config, "authorization")
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
    transaction = {
        "state": secrets.token_urlsafe(32),
        "nonce": secrets.token_urlsafe(32),
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "created_at": str(time.time()),
    }
    query = urlencode({
        "response_type": "code",
        "client_id": str(config.get("client_id") or ""),
        "redirect_uri": redirect_uri,
        "scope": " ".join(dict.fromkeys(["openid", *config.get("scopes", [])])),
        "state": transaction["state"],
        "nonce": transaction["nonce"],
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{endpoint}?{query}", transaction


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def validate_oidc_callback(callback_url: str, transaction: Mapping[str, str]) -> str:
    parsed = urlparse(callback_url)
    expected = urlparse(str(transaction.get("redirect_uri") or ""))
    if (parsed.scheme, parsed.hostname, parsed.port, parsed.path) != (
        expected.scheme, expected.hostname, expected.port, expected.path
    ):
        raise RuntimeError("The OIDC callback address is invalid.")
    values = parse_qs(parsed.query, keep_blank_values=True)
    if values.get("state") != [transaction.get("state")]:
        raise RuntimeError("The OIDC callback state is invalid.")
    if "error" in values:
        raise RuntimeError(f"The OIDC provider rejected sign-in: {values['error'][0]}.")
    codes = values.get("code")
    if not codes or len(codes) != 1 or not codes[0]:
        raise RuntimeError("The OIDC callback has no authorization code.")
    if time.time() - float(transaction.get("created_at") or 0) > 300:
        raise RuntimeError("The OIDC sign-in transaction has expired.")
    return codes[0]


def complete_oidc_authorization(
    code: str,
    *,
    config: Mapping[str, Any],
    discovery: Mapping[str, Any],
    jwks: Mapping[str, Any],
    transaction: Mapping[str, str],
    token_request=None,
) -> dict[str, Any]:
    """Exchange one code and store only a validated ID token in Keychain."""
    endpoint = _approved_endpoint(discovery.get("token_endpoint"), config, "token")
    fields = {
        "grant_type": "authorization_code",
        "code": str(code),
        "client_id": str(config.get("client_id") or ""),
        "redirect_uri": str(transaction.get("redirect_uri") or ""),
        "code_verifier": str(transaction.get("code_verifier") or ""),
    }
    request = token_request or (lambda url, fields, hosts: _post_oidc_token(url, fields, hosts, config))
    response = request(endpoint, fields, set(config.get("allowed_hosts") or []))
    token = str(response.get("id_token") or "") if isinstance(response, dict) else ""
    principal = validate_oidc_id_token(
        token, config=config, discovery=discovery, jwks=jwks,
        nonce=str(transaction.get("nonce") or ""),
    )
    store_oidc_session_record(
        str(config.get("organization_id") or "managed"),
        id_token=token,
        refresh_token=str(response.get("refresh_token") or ""),
    )
    return principal


def run_oidc_browser_login(
    policy: Mapping[str, Any],
    *,
    open_browser,
    timeout: float = 300,
    metadata: tuple[Mapping[str, Any], Mapping[str, Any]] | None = None,
    token_request=None,
) -> dict[str, Any]:
    """Run one loopback OIDC sign-in. Call this function from a worker thread."""
    config = oidc_config_from_policy(policy)
    discovery, jwks = metadata or fetch_oidc_metadata(config)
    callback: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if len(self.path) > 8192:
                self.send_error(414)
                return
            callback["url"] = f"http://127.0.0.1:{self.server.server_port}{self.path}"
            body = b"VibeCAD received the sign-in response. You can close this page."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
    server.timeout = max(1.0, min(float(timeout), 300.0))
    redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"
    authorization_url, transaction = create_oidc_authorization_request(
        config, discovery, redirect_uri=redirect_uri
    )
    try:
        if open_browser(authorization_url) is False:
            raise RuntimeError("The system browser did not open the OIDC sign-in page.")
        server.handle_request()
    finally:
        server.server_close()
    if not callback.get("url"):
        raise TimeoutError("The OIDC sign-in callback did not arrive before the timeout.")
    code = validate_oidc_callback(callback["url"], transaction)
    return complete_oidc_authorization(
        code,
        config=config,
        discovery=discovery,
        jwks=jwks,
        transaction=transaction,
        token_request=token_request,
    )


def _post_oidc_token(
    url: str, fields: Mapping[str, str], allowed_hosts: set[str],
    network_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    request = Request(
        url,
        data=urlencode(dict(fields)).encode("ascii"),
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "VibeCAD-OIDC/1"},
        method="POST",
    )
    from VibeCADNetwork import build_managed_opener
    with build_managed_opener(network_policy or {}, _OIDCRedirect(allowed_hosts)).open(request, timeout=15) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or final.hostname not in allowed_hosts:
            raise RuntimeError("The OIDC token endpoint changed unexpectedly.")
        payload = response.read(MAX_OIDC_METADATA_BYTES + 1)
    if len(payload) > MAX_OIDC_METADATA_BYTES:
        raise RuntimeError("The OIDC token response exceeds its size limit.")
    try:
        result = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("The OIDC token response is not valid JSON.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("The OIDC token response is not a JSON object.")
    return result


def oidc_config_from_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "issuer": str(policy.get("oidc_issuer") or "").rstrip("/"),
        "client_id": str(policy.get("oidc_client_id") or ""),
        "allowed_hosts": list(policy.get("oidc_allowed_hosts") or []),
        "allowed_algorithms": list(policy.get("oidc_allowed_algorithms") or ["RS256"]),
        "role_claim": str(policy.get("oidc_role_claim") or "roles"),
        "role_mapping": dict(policy.get("oidc_role_mapping") or {}),
        "scopes": list(policy.get("oidc_scopes") or ["openid", "profile"]),
        "organization_id": str(policy.get("organization_id") or "managed"),
        "proxy_mode": str(policy.get("proxy_mode") or "system"),
        "proxy_url": str(policy.get("proxy_url") or ""),
        "proxy_allowed_hosts": list(policy.get("proxy_allowed_hosts") or []),
        "custom_ca_path": str(policy.get("custom_ca_path") or ""),
        "custom_ca_sha256": str(policy.get("custom_ca_sha256") or ""),
    }


def _jwk_key(jwk: Mapping[str, Any], algorithm: str):
    if algorithm == "RS256" and jwk.get("kty") == "RSA":
        exponent = int.from_bytes(_b64url(str(jwk.get("e") or "")), "big")
        modulus = int.from_bytes(_b64url(str(jwk.get("n") or "")), "big")
        if exponent < 3 or modulus.bit_length() < 2048:
            raise RuntimeError("The OIDC RSA key is too weak.")
        return rsa.RSAPublicNumbers(exponent, modulus).public_key()
    if algorithm == "ES256" and jwk.get("kty") == "EC" and jwk.get("crv") == "P-256":
        x = int.from_bytes(_b64url(str(jwk.get("x") or "")), "big")
        y = int.from_bytes(_b64url(str(jwk.get("y") or "")), "big")
        return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    raise RuntimeError("The OIDC signing key type does not match the token algorithm.")


def _claim_number(claims: Mapping[str, Any], name: str) -> float:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"The OIDC {name} claim is missing or invalid.")
    return float(value)


def validate_oidc_id_token(
    token: str,
    *,
    config: Mapping[str, Any],
    discovery: Mapping[str, Any],
    jwks: Mapping[str, Any],
    nonce: str | None,
    now: float | None = None,
) -> dict[str, Any]:
    encoded = str(token or "").strip()
    if not encoded or len(encoded.encode("utf-8")) > MAX_ID_TOKEN_BYTES:
        raise RuntimeError("The OIDC ID token is empty or too large.")
    parts = encoded.split(".")
    if len(parts) != 3:
        raise RuntimeError("The OIDC ID token does not have three segments.")
    try:
        header = json.loads(_b64url(parts[0]))
        claims = json.loads(_b64url(parts[1]))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("The OIDC ID token header or claims are invalid JSON.") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise RuntimeError("The OIDC ID token header and claims must be objects.")
    algorithm = str(header.get("alg") or "")
    allowed_algorithms = set(config.get("allowed_algorithms") or ["RS256"])
    if algorithm not in allowed_algorithms or algorithm not in {"RS256", "ES256"}:
        raise RuntimeError("The OIDC ID token uses an unapproved algorithm.")
    kid = str(header.get("kid") or "")
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        raise RuntimeError("The OIDC JWKS has no key array.")
    matches = [item for item in keys if isinstance(item, dict) and item.get("kid") == kid and item.get("use", "sig") == "sig"]
    if not kid or len(matches) != 1:
        raise RuntimeError("The OIDC signing key identity is missing or ambiguous.")
    key = _jwk_key(matches[0], algorithm)
    signature = _b64url(parts[2])
    signed = f"{parts[0]}.{parts[1]}".encode("ascii")
    try:
        if algorithm == "RS256":
            key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        else:
            if len(signature) != 64:
                raise RuntimeError("The OIDC ES256 signature length is invalid.")
            der = encode_dss_signature(
                int.from_bytes(signature[:32], "big"),
                int.from_bytes(signature[32:], "big"),
            )
            key.verify(der, signed, ec.ECDSA(hashes.SHA256()))
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("The OIDC ID token signature is invalid.") from exc

    issuer = str(config.get("issuer") or "").rstrip("/")
    if str(discovery.get("issuer") or "").rstrip("/") != issuer or str(claims.get("iss") or "").rstrip("/") != issuer:
        raise RuntimeError("The OIDC token issuer is invalid.")
    client_id = str(config.get("client_id") or "")
    audience = claims.get("aud")
    audiences = [audience] if isinstance(audience, str) else audience
    if not isinstance(audiences, list) or client_id not in audiences:
        raise RuntimeError("The OIDC token audience is invalid.")
    if len(audiences) > 1 and claims.get("azp") != client_id:
        raise RuntimeError("The OIDC authorized party is invalid.")
    current = float(now if now is not None else time.time())
    expires = _claim_number(claims, "exp")
    issued = _claim_number(claims, "iat")
    if expires <= current - OIDC_CLOCK_SKEW_SECONDS:
        raise RuntimeError("The OIDC session has expired.")
    if issued > current + OIDC_CLOCK_SKEW_SECONDS:
        raise RuntimeError("The OIDC token issued-at time is in the future.")
    if "nbf" in claims and _claim_number(claims, "nbf") > current + OIDC_CLOCK_SKEW_SECONDS:
        raise RuntimeError("The OIDC token is not active yet.")
    if nonce is not None and claims.get("nonce") != nonce:
        raise RuntimeError("The OIDC token nonce is invalid.")
    subject = str(claims.get("sub") or "").strip()
    if not subject:
        raise RuntimeError("The OIDC token subject is missing.")

    claim_name = str(config.get("role_claim") or "roles")
    raw_roles = claims.get(claim_name) or []
    if isinstance(raw_roles, str):
        raw_roles = [raw_roles]
    if not isinstance(raw_roles, list) or any(not isinstance(item, str) for item in raw_roles):
        raise RuntimeError("The OIDC role claim is invalid.")
    mapping = config.get("role_mapping") or {}
    if not isinstance(mapping, dict):
        raise RuntimeError("The managed OIDC role mapping is invalid.")
    roles = list(dict.fromkeys(mapping[item] for item in raw_roles if item in mapping)) or ["viewer"]
    principal = {
        "schema": PRINCIPAL_SCHEMA,
        "version": PRINCIPAL_VERSION,
        "subject": subject,
        "organization_id": str(config.get("organization_id") or "managed"),
        "roles": roles,
        "source": "oidc",
        "session_expires_at": expires,
    }
    return validate_principal(principal)


def store_oidc_session_record(
    organization_id: str, *, id_token: str, refresh_token: str = ""
) -> None:
    import keyring

    record = {
        "schema": OIDC_SESSION_SCHEMA,
        "id_token": str(id_token),
        "refresh_token": str(refresh_token),
        "stored_at": time.time(),
    }
    keyring.set_password(
        KEYRING_SERVICE,
        f"oidc:{organization_id}",
        json.dumps(record, sort_keys=True, separators=(",", ":")),
    )


def store_oidc_session(organization_id: str, id_token: str) -> None:
    store_oidc_session_record(organization_id, id_token=id_token)


def read_oidc_session_record(organization_id: str) -> dict[str, Any] | None:
    import keyring

    value = keyring.get_password(KEYRING_SERVICE, f"oidc:{organization_id}") or ""
    if not value:
        return None
    try:
        record = json.loads(value)
    except ValueError:
        return {
            "schema": OIDC_SESSION_SCHEMA,
            "id_token": value,
            "refresh_token": "",
        }
    if not isinstance(record, dict) or record.get("schema") != OIDC_SESSION_SCHEMA:
        raise RuntimeError("The stored OIDC session contract is invalid.")
    if not isinstance(record.get("id_token"), str) or not isinstance(
        record.get("refresh_token"), str
    ):
        raise RuntimeError("The stored OIDC session fields are invalid.")
    return record


def read_oidc_session(organization_id: str) -> str | None:
    record = read_oidc_session_record(organization_id)
    return str(record["id_token"]) if record else None


def delete_oidc_session(organization_id: str) -> None:
    import keyring

    try:
        keyring.delete_password(KEYRING_SERVICE, f"oidc:{organization_id}")
    except keyring.errors.PasswordDeleteError:
        pass


def resolve_oidc_principal(policy: Mapping[str, Any]) -> dict[str, Any]:
    config = oidc_config_from_policy(policy)
    record = read_oidc_session_record(config["organization_id"])
    if not record:
        raise PermissionError("No managed OIDC session is available in the OS credential store.")
    discovery, jwks = fetch_oidc_metadata(config)
    try:
        return validate_oidc_id_token(
            record["id_token"], config=config, discovery=discovery, jwks=jwks,
            nonce=None,
        )
    except RuntimeError as exc:
        if "session has expired" not in str(exc) or not record.get("refresh_token"):
            raise
    return refresh_oidc_principal(
        config, discovery=discovery, jwks=jwks, record=record
    )


def refresh_oidc_principal(
    config: Mapping[str, Any],
    *,
    discovery: Mapping[str, Any],
    jwks: Mapping[str, Any],
    record: Mapping[str, Any],
    token_request=None,
) -> dict[str, Any]:
    refresh_token = str(record.get("refresh_token") or "")
    if not refresh_token:
        raise PermissionError("The OIDC session cannot renew. Sign in again.")
    endpoint = _approved_endpoint(discovery.get("token_endpoint"), config, "token")
    request = token_request or (lambda url, fields, hosts: _post_oidc_token(url, fields, hosts, config))
    response = request(
        endpoint,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": str(config.get("client_id") or ""),
        },
        set(config.get("allowed_hosts") or []),
    )
    token = str(response.get("id_token") or "") if isinstance(response, dict) else ""
    principal = validate_oidc_id_token(
        token, config=config, discovery=discovery, jwks=jwks, nonce=None
    )
    store_oidc_session_record(
        str(config.get("organization_id") or "managed"),
        id_token=token,
        refresh_token=str(response.get("refresh_token") or refresh_token),
    )
    return principal
