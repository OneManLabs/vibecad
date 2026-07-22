# SPDX-License-Identifier: LGPL-2.1-or-later
from pathlib import Path
import base64
import hashlib
import io
import json
import subprocess
import types

import pytest

import VibeCADUpdate as update
from VibeCADManagedPolicy import default_policy
from VibeCADUpdate import (
    _AllowedRedirect,
    check_for_updates,
    download_verified_artifact,
    load_update_config,
    load_verified_manifest,
    select_macos_update_artifact,
    validate_update_manifest,
    verify_macos_update_artifact,
)


def _manifest() -> dict:
    return {
        "schema": "vibecad-update-manifest-v1", "version": 1,
        "release_version": "26.3.2", "channel": "stable", "published_at": "2026-07-22T00:00:00Z",
        "artifacts": [{
            "name": "VibeCAD.dmg", "digest": {"sha256": "a" * 64},
            "size": 7,
            "url": "https://releases.vibecad.example/VibeCAD.dmg",
        }],
    }


def test_manifest_enforces_channel_host_digest_and_filename() -> None:
    clean = validate_update_manifest(_manifest(), allowed_channels={"stable"}, allowed_hosts={"releases.vibecad.example"})
    assert clean["channel"] == "stable"
    for field, value, message in (
        ("channel", "nightly", "channel"),
        ("url", "https://evil.example/VibeCAD.dmg", "endpoint"),
        ("name", "../VibeCAD.dmg", "name"),
        ("size", 0, "size"),
    ):
        raw = _manifest()
        if field in raw:
            raw[field] = value
        else:
            raw["artifacts"][0][field] = value
        with pytest.raises(RuntimeError, match=message):
            validate_update_manifest(raw, allowed_channels={"stable"}, allowed_hosts={"releases.vibecad.example"})


def test_signed_manifest_accepts_exact_content_and_rejects_tamper(tmp_path: Path) -> None:
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    manifest = json.dumps(_manifest(), sort_keys=True).encode()
    manifest_path = tmp_path / "manifest.json"
    signature = tmp_path / "signature.bin"
    manifest_path.write_bytes(manifest)
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(private)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True)
    subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(private), "-out", str(signature), str(manifest_path)], check=True)
    encoded = base64.b64encode(signature.read_bytes()).decode()
    result = load_verified_manifest(manifest, encoded, public.read_bytes(), allowed_channels={"stable"}, allowed_hosts={"releases.vibecad.example"})
    assert result["release_version"] == "26.3.2"
    with pytest.raises(RuntimeError, match="signature"):
        load_verified_manifest(manifest + b" ", encoded, public.read_bytes(), allowed_channels={"stable"}, allowed_hosts={"releases.vibecad.example"})


def _signed_update_fixture(tmp_path: Path) -> tuple[bytes, bytes, Path]:
    private = tmp_path / "private.pem"
    public = tmp_path / "update-public.pem"
    manifest = json.dumps(_manifest(), sort_keys=True).encode()
    manifest_path = tmp_path / "manifest.json"
    signature = tmp_path / "signature.bin"
    manifest_path.write_bytes(manifest)
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(private)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True)
    subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(private), "-out", str(signature), str(manifest_path)], check=True)
    config = tmp_path / "update-config.json"
    config.write_text(json.dumps({
        "schema": "vibecad-update-config-v1", "version": 1,
        "manifest_url": "https://releases.vibecad.example/manifest.json",
        "signature_url": "https://releases.vibecad.example/manifest.json.sig",
        "public_key": public.name,
        "channel": "stable",
    }), encoding="utf-8")
    return manifest, base64.b64encode(signature.read_bytes()) + b"\n", config


def test_update_check_verifies_pin_policy_channel_and_newer_version(tmp_path: Path, monkeypatch) -> None:
    manifest, signature, config = _signed_update_fixture(tmp_path)
    responses = {"manifest.json": manifest, "manifest.json.sig": signature}
    monkeypatch.setattr(
        update,
        "_read_limited_https",
        lambda url, **_kwargs: responses[url.rsplit("/", 1)[-1]],
    )
    policy = default_policy()
    policy["allowed_update_hosts"] = ["releases.vibecad.example"]
    result = check_for_updates("26.3.1", config_path=config, policy=policy)
    assert result["available"] is True
    assert result["verified"] is True
    assert result["release_version"] == "26.3.2"
    assert result["artifacts"][0]["name"] == "VibeCAD.dmg"


def test_disabled_update_policy_does_not_read_config_or_network(tmp_path: Path, monkeypatch) -> None:
    policy = default_policy()
    policy["update_channel"] = "disabled"
    monkeypatch.setattr(update, "_read_limited_https", lambda *_args, **_kwargs: pytest.fail("network used"))
    result = check_for_updates("26.3.2", config_path=tmp_path / "missing.json", policy=policy)
    assert result == {"status": "disabled", "available": False, "current_version": "26.3.2"}


def test_update_config_rejects_key_outside_signed_config_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside.pem"
    outside.write_text("key", encoding="utf-8")
    directory = tmp_path / "config"
    directory.mkdir()
    config = directory / "update-config.json"
    config.write_text(json.dumps({
        "schema": "vibecad-update-config-v1", "version": 1,
        "manifest_url": "https://releases.vibecad.example/manifest.json",
        "signature_url": "https://releases.vibecad.example/manifest.json.sig",
        "public_key": "../outside.pem",
        "channel": "stable",
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing or unsafe"):
        load_update_config(config)


def test_update_redirect_rejects_host_outside_managed_allowlist() -> None:
    handler = _AllowedRedirect({"github.com", "release-assets.githubusercontent.com"})
    with pytest.raises(RuntimeError, match="unapproved endpoint"):
        handler.redirect_request(
            None, None, 302, "Found", {}, "https://evil.example/update.json"
        )


class _Response(io.BytesIO):
    def __init__(self, payload: bytes, url: str, *, content_length: int | None = None):
        super().__init__(payload)
        self._url = url
        self.headers = {"Content-Length": str(content_length if content_length is not None else len(payload))}

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_verified_update_download_is_exclusive_atomic_and_content_bound(tmp_path: Path, monkeypatch) -> None:
    payload = b"signed-dmg"
    artifact = {
        "name": "VibeCAD.dmg", "size": len(payload),
        "digest": {"sha256": hashlib.sha256(payload).hexdigest()},
        "url": "https://releases.vibecad.example/VibeCAD.dmg",
    }
    monkeypatch.setattr(
        update, "build_opener",
        lambda *args: types.SimpleNamespace(open=lambda request, timeout: _Response(payload, artifact["url"])),
    )
    target = tmp_path / artifact["name"]
    result = download_verified_artifact(
        artifact, target, allowed_hosts={"releases.vibecad.example"}
    )
    assert target.read_bytes() == payload
    assert result["existing"] is False
    assert download_verified_artifact(
        artifact, target, allowed_hosts={"releases.vibecad.example"}
    )["existing"] is True
    assert not list(tmp_path.glob("*.download"))


def test_update_download_size_failure_leaves_no_target(tmp_path: Path, monkeypatch) -> None:
    payload = b"short"
    artifact = {
        "name": "VibeCAD.dmg", "size": len(payload) + 1,
        "digest": {"sha256": hashlib.sha256(payload).hexdigest()},
        "url": "https://releases.vibecad.example/VibeCAD.dmg",
    }
    monkeypatch.setattr(
        update, "build_opener",
        lambda *args: types.SimpleNamespace(open=lambda request, timeout: _Response(payload, artifact["url"], content_length=0)),
    )
    target = tmp_path / artifact["name"]
    with pytest.raises(RuntimeError, match="size"):
        download_verified_artifact(
            artifact, target, allowed_hosts={"releases.vibecad.example"}
        )
    assert not target.exists()


def test_macos_update_selection_and_package_checks_do_not_open_artifact(monkeypatch, tmp_path: Path) -> None:
    artifact = _manifest()["artifacts"][0]
    assert select_macos_update_artifact([artifact]) == artifact
    path = tmp_path / "VibeCAD.dmg"
    path.write_bytes(b"dmg")
    commands = []
    monkeypatch.setattr(update.sys, "platform", "darwin")
    monkeypatch.setattr(
        update.subprocess, "run",
        lambda command, **kwargs: commands.append(command) or types.SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )
    result = verify_macos_update_artifact(path)
    assert result["verified"] is True
    assert [command[1] for command in commands] == ["verify", "--assess", "stapler"]
    assert all(command[0] != "/usr/bin/open" for command in commands)


def test_update_ui_requires_download_and_finder_reveal_consent() -> None:
    source = (Path(__file__).resolve().parents[1] / "VibeCADGui.py").read_text(encoding="utf-8")
    flow = source[source.index("def _offer_verified_update_download"):source.index("class CheckForUpdatesCommand")]
    assert flow.index("QMessageBox.question") < flow.index("download_verified_artifact")
    assert flow.count("QMessageBox.question") >= 2
    assert 'startDetached("/usr/bin/open", ["-R", downloaded["path"]])' in flow
    assert "will not open or install" in flow.lower()
