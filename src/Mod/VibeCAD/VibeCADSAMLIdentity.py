# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pinned-certificate SAML 2.0 assertion validation for managed sessions."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import secrets
import time
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlparse
import zlib

from lxml import etree
from signxml import DigestAlgorithm, SignatureConfiguration, SignatureMethod, XMLVerifier

from VibeCADIdentity import PRINCIPAL_SCHEMA, PRINCIPAL_VERSION, validate_principal


SAML_PROTOCOL = "urn:oasis:names:tc:SAML:2.0:protocol"
SAML_ASSERTION = "urn:oasis:names:tc:SAML:2.0:assertion"
XMLDSIG = "http://www.w3.org/2000/09/xmldsig#"
SAML_SESSION_SCHEMA = "vibecad-saml-session-v1"
KEYRING_SERVICE = "com.vibecad.desktop.identity"
MAX_SAML_RESPONSE_BYTES = 1024 * 1024
CLOCK_SKEW_SECONDS = 60
NS = {"samlp": SAML_PROTOCOL, "saml": SAML_ASSERTION, "ds": XMLDSIG}


def saml_config_from_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "idp_entity_id": str(policy.get("saml_idp_entity_id") or ""),
        "sso_url": str(policy.get("saml_sso_url") or ""),
        "sp_entity_id": str(policy.get("saml_sp_entity_id") or ""),
        "acs_url": str(policy.get("saml_acs_url") or ""),
        "idp_certificate": str(policy.get("saml_idp_certificate") or ""),
        "role_attribute": str(policy.get("saml_role_attribute") or "roles"),
        "role_mapping": dict(policy.get("saml_role_mapping") or {}),
        "organization_id": str(policy.get("organization_id") or "managed"),
    }


def create_saml_authn_request(
    config: Mapping[str, Any], *, now: datetime | None = None
) -> tuple[str, dict[str, str]]:
    """Create one SAML Redirect-binding authentication request."""
    sso = urlparse(str(config.get("sso_url") or ""))
    acs = urlparse(str(config.get("acs_url") or ""))
    if sso.scheme != "https" or not sso.hostname:
        raise RuntimeError("The SAML sign-in endpoint is invalid.")
    if acs.scheme != "http" or acs.hostname not in {"127.0.0.1", "localhost"} or not acs.port:
        raise RuntimeError("The SAML callback must use a fixed local loopback port.")
    request_id = "_" + secrets.token_urlsafe(24)
    relay_state = secrets.token_urlsafe(32)
    issued = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    root = etree.Element(
        f"{{{SAML_PROTOCOL}}}AuthnRequest",
        nsmap={"samlp": SAML_PROTOCOL, "saml": SAML_ASSERTION},
        ID=request_id,
        Version="2.0",
        IssueInstant=issued,
        Destination=str(config.get("sso_url") or ""),
        ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
        AssertionConsumerServiceURL=str(config.get("acs_url") or ""),
    )
    etree.SubElement(root, f"{{{SAML_ASSERTION}}}Issuer").text = str(
        config.get("sp_entity_id") or ""
    )
    compressor = zlib.compressobj(level=9, wbits=-15)
    compressed = compressor.compress(etree.tostring(root)) + compressor.flush()
    query = urlencode({
        "SAMLRequest": base64.b64encode(compressed).decode("ascii"),
        "RelayState": relay_state,
    })
    separator = "&" if sso.query else "?"
    return f"{config['sso_url']}{separator}{query}", {
        "request_id": request_id,
        "relay_state": relay_state,
        "created_at": str(time.time()),
    }


def run_saml_browser_login(
    policy: Mapping[str, Any], *, open_browser, timeout: float = 300
) -> dict[str, Any]:
    """Run one SAML Redirect/POST login on a fixed loopback ACS."""
    config = saml_config_from_policy(policy)
    acs = urlparse(config["acs_url"])
    received: dict[str, str] = {}

    class ACSHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != (acs.path or "/"):
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400)
                return
            if length <= 0 or length > MAX_SAML_RESPONSE_BYTES * 2:
                self.send_error(413)
                return
            values = parse_qs(self.rfile.read(length).decode("ascii"), keep_blank_values=True)
            for field in ("SAMLResponse", "RelayState"):
                if len(values.get(field, [])) != 1:
                    self.send_error(400)
                    return
                received[field] = values[field][0]
            body = b"VibeCAD received the organization sign-in response. You can close this page."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", int(acs.port)), ACSHandler)
    server.timeout = max(1.0, min(float(timeout), 300.0))
    authorization_url, transaction = create_saml_authn_request(config)
    try:
        if open_browser(authorization_url) is False:
            raise RuntimeError("The system browser did not open the SAML sign-in page.")
        server.handle_request()
    finally:
        server.server_close()
    if not received:
        raise TimeoutError("The SAML sign-in response did not arrive before the timeout.")
    if received["RelayState"] != transaction["relay_state"]:
        raise RuntimeError("The SAML relay state is invalid.")
    if time.time() - float(transaction["created_at"]) > 300:
        raise RuntimeError("The SAML sign-in transaction has expired.")
    principal = validate_saml_response(
        received["SAMLResponse"], config=config,
        request_id=transaction["request_id"],
    )
    store_saml_session(
        config["organization_id"], received["SAMLResponse"], transaction["request_id"]
    )
    return principal


def _timestamp(value: str, field: str) -> float:
    clean = str(value or "").strip()
    if not clean:
        raise RuntimeError(f"The SAML {field} time is missing.")
    try:
        parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"The SAML {field} time is invalid.") from exc
    if parsed.tzinfo is None:
        raise RuntimeError(f"The SAML {field} time has no time zone.")
    return parsed.timestamp()


def _one(node: etree._Element, path: str, label: str) -> etree._Element:
    values = node.xpath(path, namespaces=NS)
    if len(values) != 1 or not isinstance(values[0], etree._Element):
        raise RuntimeError(f"The SAML response must contain one {label}.")
    return values[0]


def validate_saml_response(
    encoded_response: str,
    *,
    config: Mapping[str, Any],
    request_id: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Validate one signed assertion and return a provider-neutral principal."""
    try:
        payload = base64.b64decode(str(encoded_response), validate=True)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("The SAML response is not valid Base64.") from exc
    if not payload or len(payload) > MAX_SAML_RESPONSE_BYTES:
        raise RuntimeError("The SAML response is empty or too large.")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    try:
        root = etree.fromstring(payload, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise RuntimeError("The SAML response is not valid XML.") from exc
    if root.tag != f"{{{SAML_PROTOCOL}}}Response":
        raise RuntimeError("The SAML root element is invalid.")
    identified = [str(node.get("ID") or "") for node in root.xpath(".//*[@ID]")]
    if not identified or any(not value for value in identified) or len(identified) != len(set(identified)):
        raise RuntimeError("The SAML document contains a missing or duplicate identity.")
    if root.get("InResponseTo") != request_id:
        raise RuntimeError("The SAML response does not match the sign-in request.")
    if root.get("Destination") != config.get("acs_url"):
        raise RuntimeError("The SAML response destination is invalid.")
    status = _one(root, "./samlp:Status/samlp:StatusCode", "status code")
    if status.get("Value") != "urn:oasis:names:tc:SAML:2.0:status:Success":
        raise RuntimeError("The SAML identity provider did not return success.")
    assertion = _one(root, "./saml:Assertion", "assertion")
    if root.xpath(".//saml:EncryptedAssertion", namespaces=NS):
        raise RuntimeError("Encrypted SAML assertions are not supported by this client.")
    if len(assertion.xpath("./ds:Signature", namespaces=NS)) != 1:
        raise RuntimeError("The SAML assertion must contain one signature.")
    certificate = str(config.get("idp_certificate") or "")
    if "BEGIN CERTIFICATE" not in certificate:
        raise RuntimeError("The managed SAML identity certificate is missing.")
    expected = SignatureConfiguration(
        require_x509=True,
        location="./",
        expect_references=1,
        signature_methods=frozenset({SignatureMethod.RSA_SHA256, SignatureMethod.ECDSA_SHA256}),
        digest_algorithms=frozenset({DigestAlgorithm.SHA256}),
    )
    try:
        result = XMLVerifier().verify(
            assertion,
            x509_cert=certificate,
            expect_config=expected,
            id_attribute="ID",
        )
    except Exception as exc:
        raise RuntimeError("The SAML assertion signature is invalid.") from exc
    signed = result.signed_xml
    if signed.tag != f"{{{SAML_ASSERTION}}}Assertion" or signed.get("ID") != assertion.get("ID"):
        raise RuntimeError("The signed SAML element is not the selected assertion.")

    issuer = _one(signed, "./saml:Issuer", "assertion issuer")
    if (issuer.text or "").strip() != config.get("idp_entity_id"):
        raise RuntimeError("The SAML assertion issuer is invalid.")
    conditions = _one(signed, "./saml:Conditions", "conditions element")
    current = float(now if now is not None else time.time())
    not_before = _timestamp(conditions.get("NotBefore", ""), "not-before")
    not_after = _timestamp(conditions.get("NotOnOrAfter", ""), "expiry")
    if current + CLOCK_SKEW_SECONDS < not_before or current - CLOCK_SKEW_SECONDS >= not_after:
        raise RuntimeError("The SAML assertion is not active.")
    audience = _one(
        conditions, "./saml:AudienceRestriction/saml:Audience", "audience"
    )
    if (audience.text or "").strip() != config.get("sp_entity_id"):
        raise RuntimeError("The SAML assertion audience is invalid.")
    confirmation = _one(
        signed,
        "./saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData",
        "subject confirmation",
    )
    if confirmation.get("InResponseTo") != request_id:
        raise RuntimeError("The SAML subject does not match the sign-in request.")
    if confirmation.get("Recipient") != config.get("acs_url"):
        raise RuntimeError("The SAML subject recipient is invalid.")
    confirmation_expiry = _timestamp(confirmation.get("NotOnOrAfter", ""), "subject expiry")
    if current - CLOCK_SKEW_SECONDS >= confirmation_expiry:
        raise RuntimeError("The SAML subject confirmation has expired.")
    name_id = _one(signed, "./saml:Subject/saml:NameID", "subject identifier")
    subject = (name_id.text or "").strip()
    if not subject:
        raise RuntimeError("The SAML subject identifier is empty.")

    role_name = str(config.get("role_attribute") or "roles")
    role_values = signed.xpath(
        "./saml:AttributeStatement/saml:Attribute[@Name=$name]/saml:AttributeValue/text()",
        namespaces=NS,
        name=role_name,
    )
    mapping = config.get("role_mapping") or {}
    roles = list(dict.fromkeys(mapping[value.strip()] for value in role_values if value.strip() in mapping)) or ["viewer"]
    principal = {
        "schema": PRINCIPAL_SCHEMA,
        "version": PRINCIPAL_VERSION,
        "subject": subject,
        "organization_id": str(config.get("organization_id") or "managed"),
        "roles": roles,
        "source": "saml",
        "session_expires_at": min(not_after, confirmation_expiry),
    }
    return validate_principal(principal)


def store_saml_session(organization_id: str, response: str, request_id: str) -> None:
    import keyring

    if not request_id or len(str(response).encode("utf-8")) > MAX_SAML_RESPONSE_BYTES * 2:
        raise RuntimeError("The SAML session is empty or too large.")
    record = {
        "schema": SAML_SESSION_SCHEMA,
        "response": str(response),
        "request_id": str(request_id),
        "stored_at": time.time(),
    }
    keyring.set_password(
        KEYRING_SERVICE,
        f"saml:{organization_id}",
        json.dumps(record, sort_keys=True, separators=(",", ":")),
    )


def read_saml_session(organization_id: str) -> dict[str, Any] | None:
    import keyring

    raw = keyring.get_password(KEYRING_SERVICE, f"saml:{organization_id}") or ""
    if not raw:
        return None
    try:
        record = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("The stored SAML session is invalid.") from exc
    if not isinstance(record, dict) or record.get("schema") != SAML_SESSION_SCHEMA:
        raise RuntimeError("The stored SAML session contract is invalid.")
    return record


def delete_saml_session(organization_id: str) -> None:
    import keyring

    try:
        keyring.delete_password(KEYRING_SERVICE, f"saml:{organization_id}")
    except keyring.errors.PasswordDeleteError:
        pass


def resolve_saml_principal(policy: Mapping[str, Any]) -> dict[str, Any]:
    config = saml_config_from_policy(policy)
    record = read_saml_session(config["organization_id"])
    if not record:
        raise PermissionError("No managed SAML session is available in the OS credential store.")
    return validate_saml_response(
        str(record.get("response") or ""),
        config=config,
        request_id=str(record.get("request_id") or ""),
    )
