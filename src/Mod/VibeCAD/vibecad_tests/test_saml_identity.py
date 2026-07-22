# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import sys
import types
from urllib.parse import parse_qs, urlparse
import zlib

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
import pytest
from signxml import XMLSigner, methods

from VibeCADManagedPolicy import default_policy, validate_policy
from VibeCADSAMLIdentity import (
    create_saml_authn_request,
    delete_saml_session,
    read_saml_session,
    saml_config_from_policy,
    store_saml_session,
    validate_saml_response,
)


SAML_PROTOCOL = "urn:oasis:names:tc:SAML:2.0:protocol"
SAML_ASSERTION = "urn:oasis:names:tc:SAML:2.0:assertion"


def _fixture(*, audience="vibecad-desktop", destination="http://127.0.0.1:49152/acs", request_id="request-1", expired=False):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "identity.example.com")])
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    start = now - timedelta(minutes=10) if expired else now - timedelta(minutes=1)
    end = now - timedelta(minutes=5) if expired else now + timedelta(minutes=10)
    stamp = lambda value: value.isoformat().replace("+00:00", "Z")
    ns = {"samlp": SAML_PROTOCOL, "saml": SAML_ASSERTION}
    response = etree.Element(
        f"{{{SAML_PROTOCOL}}}Response", nsmap=ns, ID="response-1",
        InResponseTo=request_id, Destination=destination, Version="2.0", IssueInstant=stamp(now),
    )
    status = etree.SubElement(response, f"{{{SAML_PROTOCOL}}}Status")
    etree.SubElement(
        status, f"{{{SAML_PROTOCOL}}}StatusCode",
        Value="urn:oasis:names:tc:SAML:2.0:status:Success",
    )
    assertion = etree.SubElement(
        response, f"{{{SAML_ASSERTION}}}Assertion", ID="assertion-1",
        Version="2.0", IssueInstant=stamp(now),
    )
    etree.SubElement(assertion, f"{{{SAML_ASSERTION}}}Issuer").text = "https://identity.example.com"
    subject = etree.SubElement(assertion, f"{{{SAML_ASSERTION}}}Subject")
    etree.SubElement(subject, f"{{{SAML_ASSERTION}}}NameID").text = "user-123"
    confirmation = etree.SubElement(
        subject, f"{{{SAML_ASSERTION}}}SubjectConfirmation",
        Method="urn:oasis:names:tc:SAML:2.0:cm:bearer",
    )
    etree.SubElement(
        confirmation, f"{{{SAML_ASSERTION}}}SubjectConfirmationData",
        InResponseTo=request_id, Recipient=destination, NotOnOrAfter=stamp(end),
    )
    conditions = etree.SubElement(
        assertion, f"{{{SAML_ASSERTION}}}Conditions",
        NotBefore=stamp(start), NotOnOrAfter=stamp(end),
    )
    restriction = etree.SubElement(conditions, f"{{{SAML_ASSERTION}}}AudienceRestriction")
    etree.SubElement(restriction, f"{{{SAML_ASSERTION}}}Audience").text = audience
    statement = etree.SubElement(assertion, f"{{{SAML_ASSERTION}}}AttributeStatement")
    attribute = etree.SubElement(statement, f"{{{SAML_ASSERTION}}}Attribute", Name="groups")
    etree.SubElement(attribute, f"{{{SAML_ASSERTION}}}AttributeValue").text = "cad-designers"
    signed = XMLSigner(method=methods.enveloped, signature_algorithm="rsa-sha256", digest_algorithm="sha256").sign(
        assertion, key=key_pem, cert=cert_pem, reference_uri="#assertion-1", id_attribute="ID"
    )
    response.replace(assertion, signed)
    encoded = base64.b64encode(etree.tostring(response)).decode()
    config = {
        "idp_entity_id": "https://identity.example.com",
        "sp_entity_id": "vibecad-desktop",
        "acs_url": "http://127.0.0.1:49152/acs",
        "idp_certificate": cert_pem,
        "role_attribute": "groups",
        "role_mapping": {"cad-designers": "designer"},
        "organization_id": "org-1",
    }
    return encoded, config, request_id, response


def test_valid_signed_saml_assertion_maps_principal() -> None:
    response, config, request_id, _ = _fixture()
    principal = validate_saml_response(response, config=config, request_id=request_id)
    assert principal["source"] == "saml"
    assert principal["roles"] == ["designer"]
    assert principal["subject"] == "user-123"
    assert principal["organization_id"] == "org-1"


def test_saml_redirect_request_uses_managed_destination_and_loopback_acs() -> None:
    _, config, _, _ = _fixture()
    config["sso_url"] = "https://identity.example.com/sso"
    url, transaction = create_saml_authn_request(config)
    query = parse_qs(urlparse(url).query)
    compressed = base64.b64decode(query["SAMLRequest"][0])
    xml = etree.fromstring(zlib.decompress(compressed, wbits=-15))
    assert xml.get("ID") == transaction["request_id"]
    assert xml.get("Destination") == config["sso_url"]
    assert xml.get("AssertionConsumerServiceURL") == config["acs_url"]
    assert query["RelayState"] == [transaction["relay_state"]]


def test_saml_signature_tamper_and_unsigned_assertion_are_rejected() -> None:
    response, config, request_id, xml = _fixture()
    assertion = xml.find(f"{{{SAML_ASSERTION}}}Assertion")
    assertion.find(f"{{{SAML_ASSERTION}}}Subject/{{{SAML_ASSERTION}}}NameID").text = "attacker"
    tampered = base64.b64encode(etree.tostring(xml)).decode()
    with pytest.raises(RuntimeError, match="signature"):
        validate_saml_response(tampered, config=config, request_id=request_id)
    for signature in assertion.xpath("./ds:Signature", namespaces={"ds": "http://www.w3.org/2000/09/xmldsig#"}):
        assertion.remove(signature)
    unsigned = base64.b64encode(etree.tostring(xml)).decode()
    with pytest.raises(RuntimeError, match="signature"):
        validate_saml_response(unsigned, config=config, request_id=request_id)


def test_saml_duplicate_identity_wrapping_is_rejected_before_signature_use() -> None:
    _, config, request_id, xml = _fixture()
    assertion = xml.find(f"{{{SAML_ASSERTION}}}Assertion")
    assertion.find(f"{{{SAML_ASSERTION}}}Subject").set("ID", "assertion-1")
    wrapped = base64.b64encode(etree.tostring(xml)).decode()
    with pytest.raises(RuntimeError, match="duplicate identity"):
        validate_saml_response(wrapped, config=config, request_id=request_id)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"audience": "other"}, "audience"),
        ({"destination": "http://127.0.0.1:49153/acs"}, "destination"),
        ({"request_id": "other"}, "sign-in request"),
        ({"expired": True}, "not active"),
    ],
)
def test_saml_context_and_time_failures(changes, message) -> None:
    response, config, _, _ = _fixture(**changes)
    with pytest.raises(RuntimeError, match=message):
        validate_saml_response(response, config=config, request_id="request-1")


def test_saml_keychain_session_contract(monkeypatch) -> None:
    values = {}
    fake = types.SimpleNamespace(
        errors=types.SimpleNamespace(PasswordDeleteError=KeyError),
        set_password=lambda service, user, value: values.__setitem__((service, user), value),
        get_password=lambda service, user: values.get((service, user)),
        delete_password=lambda service, user: values.pop((service, user)),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake)
    store_saml_session("org-1", "signed-response", "request-1")
    assert read_saml_session("org-1")["request_id"] == "request-1"
    delete_saml_session("org-1")
    assert read_saml_session("org-1") is None


def test_managed_saml_policy_requires_pinned_https_identity_and_loopback_acs() -> None:
    _, config, _, _ = _fixture()
    policy = default_policy()
    policy.update(
        managed=True, identity_mode="saml", organization_id="org-1",
        saml_idp_entity_id=config["idp_entity_id"], saml_sp_entity_id=config["sp_entity_id"],
        saml_sso_url="https://identity.example.com/sso",
        saml_acs_url=config["acs_url"], saml_allowed_hosts=["identity.example.com"],
        saml_idp_certificate=config["idp_certificate"],
        saml_role_attribute="groups", saml_role_mapping={"cad-designers": "designer"},
    )
    clean = validate_policy(policy)
    assert saml_config_from_policy(clean)["organization_id"] == "org-1"
    candidate = dict(policy)
    candidate["saml_sso_url"] = "http://identity.example.com/sso"
    with pytest.raises(RuntimeError, match="endpoint"):
        validate_policy(candidate)
