# SPDX-License-Identifier: LGPL-2.1-or-later
"""End-to-end managed organization-policy acceptance integration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import plistlib
import tempfile
from urllib.request import ProxyHandler

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from VibeCADAudit import VibeCADAuditStore
from VibeCADEnterpriseRuntime import evaluate_runtime_controls
from VibeCADIdentity import authorize, principal_from_policy
from VibeCADManagedPolicy import default_policy, load_managed_policy
from VibeCADNetwork import managed_network_handlers, managed_ssl_context


def _create_ca(path: Path) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ACME CAD Root")])
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vibecad-enterprise-acceptance-") as temporary:
        root = Path(temporary)
        ca_path = root / "organization-ca.pem"
        ca_data = _create_ca(ca_path)
        raw = default_policy()
        raw.update(
            organization_id="acme-cad",
            managed_subject="reviewer-12",
            managed_roles=["reviewer"],
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
            custom_ca_path=str(ca_path),
            custom_ca_sha256=hashlib.sha256(ca_data).hexdigest(),
            update_channel="stable",
        )
        plist_path = root / "com.vibecad.desktop.plist"
        plist_path.write_bytes(plistlib.dumps(raw))
        policy = load_managed_policy(plist_path)
        assert policy["managed"] is True

        controls = evaluate_runtime_controls(
            policy, provider="openai", model="approved-cad-model",
            endpoint="https://gateway.example.com/v1", online=True,
            context={
                "document": {"geometry_payload": "SECRET-BREP"},
                "selection": {"face": "Face7"},
                "design_brief": {"critical_dimension": 42},
                "view_screenshot": {"image_data": "SECRET-IMAGE"},
                "workbench": "PartDesignWorkbench",
            },
            tool_names=[
                "conversation.ask_user", "core.inspect",
                "core.capture_view_screenshot", "project.export",
            ],
        )
        assert controls["provider_mode"] == "managed_gateway"
        assert controls["allowed_tools"] == ["conversation.ask_user"]
        assert controls["blocked_tools"] == [
            "core.inspect", "core.capture_view_screenshot", "project.export"
        ]
        assert controls["context_policy"]["removed_context_fields"] == [
            "design_brief", "document", "selection", "view_screenshot"
        ]
        assert "SECRET" not in str(controls)

        handlers = managed_network_handlers(policy)
        proxy = next(item for item in handlers if isinstance(item, ProxyHandler))
        assert proxy.proxies == {
            "http": "http://proxy.example.com:8080",
            "https": "http://proxy.example.com:8080",
        }
        managed_ssl_context(policy)

        principal = principal_from_policy(policy)
        authorize(principal, "audit.view")
        denied = False
        try:
            authorize(principal, "export")
        except PermissionError:
            denied = True
        assert denied

        store = VibeCADAuditStore(root / "project", "enterprise-acceptance")
        event = store.record(
            category="authorization", action="export", outcome="blocked",
            actor_type="user", details={
                "actor_id": principal["actor_id"],
                "roles": principal["roles"],
                "provider": controls["provider"],
                "prompt": "SECRET PROMPT",
                "geometry_payload": "SECRET-BREP",
            },
        )
        reopened = VibeCADAuditStore(
            root / "project", "enterprise-acceptance"
        ).list_events()
        assert reopened == [event]
        assert event["details"]["prompt"] == "[REDACTED]"
        assert event["details"]["geometry_payload"] == "[REDACTED]"

        local = dict(raw)
        local["local_only"] = True
        local_path = root / "local-only.plist"
        local_path.write_bytes(plistlib.dumps(local))
        local_controls = evaluate_runtime_controls(
            load_managed_policy(local_path), provider="openai", model="ignored",
            endpoint=None, online=False, context={"document": {"name": "Local"}},
            tool_names=["core.inspect"],
        )
        assert local_controls["provider_mode"] == "local_only"
        assert local_controls["provider"] == "offline"
        assert local_controls["allowed_tools"] == ["core.inspect"]

        ca_path.write_bytes(ca_data + b"\n")
        rejected_tamper = False
        try:
            managed_ssl_context(policy)
        except RuntimeError:
            rejected_tamper = True
        assert rejected_tamper
    print("VibeCAD enterprise policy acceptance integration passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
