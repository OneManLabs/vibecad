# SPDX-License-Identifier: LGPL-2.1-or-later
"""Secure verification and download boundary for signed VibeCAD updates."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Mapping
from urllib.parse import urlparse
from urllib.request import Request, HTTPRedirectHandler

from VibeCADManagedPolicy import load_managed_policy, validate_policy


UPDATE_SCHEMA = "vibecad-update-manifest-v1"
UPDATE_VERSION = 1
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024
MAX_SIGNATURE_BYTES = 64 * 1024
UPDATE_CONFIG_SCHEMA = "vibecad-update-config-v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")


def build_opener(*handlers):
    """Build the update opener through the current managed network policy."""
    from VibeCADNetwork import build_managed_opener
    return build_managed_opener(load_managed_policy(), *handlers)


class _AllowedRedirect(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlparse(newurl)
        if target.scheme != "https" or target.hostname not in self.allowed_hosts:
            raise RuntimeError("The update request redirected to an unapproved endpoint.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_limited_https(url: str, *, allowed_hosts: set[str], limit: int) -> bytes:
    parsed = urlparse(str(url or ""))
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise RuntimeError("The update metadata endpoint is not allowed.")
    request = Request(url, headers={"User-Agent": "VibeCAD-Update/1"})
    with build_opener(_AllowedRedirect(allowed_hosts)).open(request, timeout=15) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or final.hostname not in allowed_hosts:
            raise RuntimeError("The update metadata endpoint changed unexpectedly.")
        content_length = int(response.headers.get("Content-Length") or 0)
        if content_length > limit:
            raise RuntimeError("The update metadata exceeds its size limit.")
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise RuntimeError("The update metadata exceeds its size limit.")
    return payload


def load_update_config(path: str | Path) -> dict[str, Any]:
    selected = Path(path)
    try:
        raw = json.loads(selected.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("Secure update verification is not configured in this build.") from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"The signed update configuration could not be read: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != UPDATE_CONFIG_SCHEMA or raw.get("version") != 1:
        raise RuntimeError("The signed update configuration is invalid.")
    manifest_url = str(raw.get("manifest_url") or "").strip()
    signature_url = str(raw.get("signature_url") or "").strip()
    channel = str(raw.get("channel") or "").strip().lower()
    if channel not in {"stable", "prerelease", "nightly"}:
        raise RuntimeError("The signed update configuration channel is invalid.")
    public_key = selected.parent / str(raw.get("public_key") or "")
    if not public_key.is_file() or public_key.parent.resolve() != selected.parent.resolve():
        raise RuntimeError("The pinned update public key is missing or unsafe.")
    return {
        "schema": UPDATE_CONFIG_SCHEMA,
        "version": 1,
        "manifest_url": manifest_url,
        "signature_url": signature_url,
        "channel": channel,
        "public_key_path": str(public_key),
    }


def _version_key(value: str) -> tuple[tuple[int, ...], int, str]:
    clean = str(value or "").strip().lstrip("v")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)*)(?:[-+]([0-9A-Za-z.-]+))?", clean)
    if not match:
        raise RuntimeError(f"The update version is invalid: {value!r}.")
    numbers = tuple(int(item) for item in match.group(1).split("."))
    qualifier = match.group(2) or ""
    return numbers, 1 if not qualifier else 0, qualifier


def check_for_updates(
    current_version: str,
    *,
    config_path: str | Path,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch and verify update metadata. Never download or install an artifact."""
    managed = validate_policy(dict(policy) if policy is not None else load_managed_policy())
    channel = managed["update_channel"]
    if channel == "disabled":
        return {"status": "disabled", "available": False, "current_version": current_version}
    config = load_update_config(config_path)
    if not managed["managed"]:
        channel = config["channel"]
    allowed_hosts = set(managed["allowed_update_hosts"])
    manifest_bytes = _read_limited_https(
        config["manifest_url"], allowed_hosts=allowed_hosts, limit=MAX_MANIFEST_BYTES
    )
    signature = _read_limited_https(
        config["signature_url"], allowed_hosts=allowed_hosts, limit=MAX_SIGNATURE_BYTES
    ).decode("ascii")
    manifest = load_verified_manifest(
        manifest_bytes,
        signature,
        Path(config["public_key_path"]).read_bytes(),
        allowed_channels={channel},
        allowed_hosts=allowed_hosts,
    )
    available = _version_key(manifest["release_version"]) > _version_key(current_version)
    return {
        "status": "available" if available else "current",
        "available": available,
        "current_version": str(current_version),
        "release_version": manifest["release_version"],
        "channel": manifest["channel"],
        "published_at": manifest["published_at"],
        "artifacts": manifest["artifacts"],
        "verified": True,
    }


def validate_update_manifest(
    raw: Any,
    *,
    allowed_channels: set[str],
    allowed_hosts: set[str],
) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema") != UPDATE_SCHEMA or raw.get("version") != UPDATE_VERSION:
        raise RuntimeError("The update manifest schema is invalid.")
    channel = str(raw.get("channel") or "").strip()
    if channel not in allowed_channels:
        raise RuntimeError(f"The update channel is not allowed: {channel!r}.")
    release_version = str(raw.get("release_version") or "").strip()
    if not release_version:
        raise RuntimeError("The update manifest has no release version.")
    artifacts = raw.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError("The update manifest has no artifacts.")
    clean_artifacts = []
    seen: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict):
            raise RuntimeError("An update artifact is not an object.")
        name = str(item.get("name") or "").strip()
        digest = str((item.get("digest") or {}).get("sha256") or "").strip().lower()
        size = item.get("size")
        url = str(item.get("url") or "").strip()
        parsed = urlparse(url)
        if not name or Path(name).name != name or name in seen:
            raise RuntimeError("An update artifact name is invalid or duplicated.")
        if not _SHA256.fullmatch(digest):
            raise RuntimeError(f"Update artifact {name!r} has an invalid SHA-256 value.")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_ARTIFACT_BYTES:
            raise RuntimeError(f"Update artifact {name!r} has an invalid size.")
        if parsed.scheme != "https" or not parsed.hostname or parsed.hostname not in allowed_hosts:
            raise RuntimeError(f"Update artifact {name!r} uses an unapproved endpoint.")
        seen.add(name)
        clean_artifacts.append({"name": name, "digest": {"sha256": digest}, "size": size, "url": url})
    return {
        "schema": UPDATE_SCHEMA,
        "version": UPDATE_VERSION,
        "release_version": release_version,
        "channel": channel,
        "published_at": str(raw.get("published_at") or ""),
        "artifacts": clean_artifacts,
    }


def verify_manifest_signature(manifest_bytes: bytes, signature_text: str, public_key_pem: bytes) -> None:
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise RuntimeError("The update manifest exceeds its size limit.")
    try:
        signature = base64.b64decode(signature_text.strip(), validate=True)
    except ValueError as exc:
        raise RuntimeError("The update signature is not valid Base64.") from exc
    with tempfile.TemporaryDirectory(prefix="vibecad-update-verify-") as directory:
        root = Path(directory)
        manifest = root / "manifest.json"
        public_key = root / "public.pem"
        signature_path = root / "signature.bin"
        manifest.write_bytes(manifest_bytes)
        public_key.write_bytes(public_key_pem)
        signature_path.write_bytes(signature)
        result = subprocess.run(
            [
                "/usr/bin/openssl", "dgst", "-sha256", "-verify", str(public_key),
                "-signature", str(signature_path), str(manifest),
            ],
            text=True,
            capture_output=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError("The update manifest signature is invalid.")


def load_verified_manifest(
    manifest_bytes: bytes,
    signature_text: str,
    public_key_pem: bytes,
    *,
    allowed_channels: set[str],
    allowed_hosts: set[str],
) -> dict[str, Any]:
    verify_manifest_signature(manifest_bytes, signature_text, public_key_pem)
    try:
        raw = json.loads(manifest_bytes)
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("The signed update manifest is not valid JSON.") from exc
    return validate_update_manifest(raw, allowed_channels=allowed_channels, allowed_hosts=allowed_hosts)


def download_verified_artifact(
    artifact: Mapping[str, Any], destination: str | Path, *, allowed_hosts: set[str]
) -> dict[str, Any]:
    url = str(artifact.get("url") or "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise RuntimeError("The update artifact endpoint is not allowed.")
    expected = str((artifact.get("digest") or {}).get("sha256") or "")
    expected_size = artifact.get("size")
    if not _SHA256.fullmatch(expected):
        raise RuntimeError("The update artifact digest is invalid.")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or not 0 < expected_size <= MAX_ARTIFACT_BYTES:
        raise RuntimeError("The update artifact size is invalid.")
    target = Path(destination)
    if target.name != str(artifact.get("name") or "") or target.is_symlink():
        raise RuntimeError("The update artifact destination is unsafe.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.is_file() and target.stat().st_size == expected_size:
            existing = hashlib.sha256(target.read_bytes()).hexdigest()
            if existing == expected:
                return {"path": str(target), "sha256": expected, "size": expected_size, "existing": True}
        raise FileExistsError("A different file already uses the update artifact name.")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".download", dir=target.parent)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    total = 0
    request = Request(url, headers={"User-Agent": "VibeCAD-Update/1"})
    try:
        with os.fdopen(descriptor, "wb") as stream:
            with build_opener(_AllowedRedirect(allowed_hosts)).open(request, timeout=30) as response:
                final = urlparse(response.geturl())
                if final.scheme != "https" or final.hostname not in allowed_hosts:
                    raise RuntimeError("The update download changed to an unapproved endpoint.")
                content_length = int(response.headers.get("Content-Length") or 0)
                if content_length and content_length != expected_size:
                    raise RuntimeError("The update artifact size does not match signed metadata.")
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > MAX_ARTIFACT_BYTES:
                        raise RuntimeError("The update artifact exceeds its size limit.")
                    digest.update(block)
                    stream.write(block)
                stream.flush()
                os.fsync(stream.fileno())
        if digest.hexdigest() != expected:
            raise RuntimeError("The downloaded update artifact failed SHA-256 verification.")
        if total != expected_size:
            raise RuntimeError("The downloaded update artifact size does not match signed metadata.")
        os.link(temporary, target)
        temporary.unlink()
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(target), "sha256": expected, "size": total, "existing": False}


def select_macos_update_artifact(artifacts: list[Mapping[str, Any]]) -> dict[str, Any]:
    candidates = [dict(item) for item in artifacts if str(item.get("name") or "").lower().endswith(".dmg")]
    if len(candidates) != 1:
        raise RuntimeError("The signed update manifest must contain one macOS DMG artifact.")
    return candidates[0]


def verify_macos_update_artifact(path: str | Path) -> dict[str, Any]:
    """Verify the downloaded package without mounting, opening, or installing it."""
    artifact = Path(path)
    if sys.platform != "darwin":
        raise RuntimeError("macOS package verification is available on macOS only.")
    suffix = artifact.suffix.lower()
    if suffix == ".dmg":
        commands = [
            ["/usr/bin/hdiutil", "verify", str(artifact)],
            ["/usr/sbin/spctl", "--assess", "--type", "open", "--context", "context:primary-signature", str(artifact)],
            ["/usr/bin/xcrun", "stapler", "validate", str(artifact)],
        ]
    elif suffix == ".pkg":
        commands = [
            ["/usr/sbin/pkgutil", "--check-signature", str(artifact)],
            ["/usr/sbin/spctl", "--assess", "--type", "install", str(artifact)],
            ["/usr/bin/xcrun", "stapler", "validate", str(artifact)],
        ]
    else:
        raise RuntimeError("The downloaded update package type is unsupported.")
    evidence = []
    for command in commands:
        result = subprocess.run(command, text=True, capture_output=True, timeout=120)
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            raise RuntimeError(f"macOS rejected the downloaded update package during {Path(command[0]).name} verification.")
        evidence.append({"tool": Path(command[0]).name, "returncode": 0, "output": output[:2000]})
    return {"path": str(artifact), "verified": True, "checks": evidence}
