# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from urllib.request import ProxyHandler

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from VibeCADManagedPolicy import default_policy, validate_policy
from VibeCADNetwork import managed_httpx_client, managed_network_handlers, managed_ssl_context


def _ca(path: Path) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "VibeCAD Test CA")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = certificate.public_bytes(serialization.Encoding.PEM)
    path.write_bytes(pem)
    return pem


def test_custom_ca_is_content_pinned_and_loaded(tmp_path: Path):
    path = tmp_path / "organization-ca.pem"
    pem = _ca(path)
    policy = default_policy()
    policy.update(custom_ca_path=str(path), custom_ca_sha256=hashlib.sha256(pem).hexdigest())
    managed_ssl_context(validate_policy(policy))
    path.write_bytes(pem + b"\n")
    with pytest.raises(RuntimeError, match="identity does not match"):
        managed_ssl_context(validate_policy(policy))


def test_direct_proxy_mode_disables_environment_proxy():
    policy = default_policy()
    policy["proxy_mode"] = "direct"
    handlers = managed_network_handlers(validate_policy(policy))
    proxy = next(item for item in handlers if isinstance(item, ProxyHandler))
    assert proxy.proxies == {}


def test_explicit_proxy_requires_allowlist_and_has_no_embedded_credentials():
    policy = default_policy()
    policy.update(
        managed=True, proxy_mode="explicit", proxy_url="http://proxy.example.com:8080",
        proxy_allowed_hosts=["proxy.example.com"],
    )
    proxy = next(item for item in managed_network_handlers(validate_policy(policy)) if isinstance(item, ProxyHandler))
    assert proxy.proxies["https"] == "http://proxy.example.com:8080"
    policy["proxy_url"] = "http://user:secret@proxy.example.com:8080"
    with pytest.raises(RuntimeError, match="proxy endpoint"):
        validate_policy(policy)


@pytest.mark.parametrize("mode, expected", [
    ("system", {"trust_env": True}),
    ("direct", {"trust_env": False}),
    ("explicit", {"trust_env": False, "proxy": "http://proxy.example.com:8080"}),
])
def test_sdk_client_uses_the_same_managed_proxy(monkeypatch, mode, expected):
    captured = {}
    monkeypatch.setattr("httpx.Client", lambda **kwargs: captured.update(kwargs) or object())
    policy = default_policy()
    policy.update(proxy_mode=mode)
    if mode == "explicit":
        policy.update(proxy_url="http://proxy.example.com:8080", proxy_allowed_hosts=["proxy.example.com"])
    managed_httpx_client(validate_policy(policy))
    assert {key: captured[key] for key in expected} == expected
    assert "verify" in captured


@pytest.mark.parametrize("path,digest", [("relative.pem", "0" * 64), ("", "0" * 64), ("/ca.pem", "")])
def test_custom_ca_configuration_requires_absolute_pinned_pair(path, digest):
    policy = default_policy()
    policy.update(custom_ca_path=path, custom_ca_sha256=digest)
    with pytest.raises(RuntimeError, match="custom CA"):
        validate_policy(policy)
