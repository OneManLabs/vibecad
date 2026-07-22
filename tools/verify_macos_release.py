#!/usr/bin/env python3
"""Verify a macOS DMG, PKG, checksums, SBOM, and provenance as one release."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile


CYCLONEDX_FORMAT = "CycloneDX"
CYCLONEDX_SPEC_VERSION = "1.5"
IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
SLSA_PROVENANCE_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
UPDATE_MANIFEST_SCHEMA = "vibecad-update-manifest-v1"


def run(args: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(args, check=True, text=True, capture_output=capture)
    return result.stdout if capture else ""


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise RuntimeError(f"The {label} is missing or invalid.")
    return value


def _expect(actual: object, expected: str, label: str) -> None:
    if not expected:
        raise RuntimeError(f"The expected {label} is empty.")
    if actual != expected:
        raise RuntimeError(f"The {label} does not match the trusted value.")


def verify_release_identity(
    sbom: dict,
    provenance: dict,
    update: dict,
    *,
    expected_application_name: str,
    expected_release_version: str,
    expected_source_uri: str,
    expected_source_sha: str,
    expected_builder_id: str,
    expected_build_type: str,
    expected_update_channel: str,
) -> None:
    """Verify that all release evidence describes the trusted build."""

    if (
        sbom.get("bomFormat") != CYCLONEDX_FORMAT
        or sbom.get("specVersion") != CYCLONEDX_SPEC_VERSION
        or sbom.get("version") != 1
    ):
        raise RuntimeError("The release SBOM is not CycloneDX 1.5 version 1.")
    component = _mapping(
        _mapping(sbom.get("metadata"), "CycloneDX metadata").get("component"),
        "CycloneDX application component",
    )
    if component.get("type") != "application":
        raise RuntimeError("The CycloneDX primary component is not an application.")
    _expect(component.get("name"), expected_application_name, "CycloneDX application name")
    _expect(component.get("version"), expected_release_version, "CycloneDX application version")

    if provenance.get("_type") != IN_TOTO_STATEMENT_TYPE:
        raise RuntimeError("The provenance in-toto statement type is invalid.")
    if provenance.get("predicateType") != SLSA_PROVENANCE_PREDICATE_TYPE:
        raise RuntimeError("The provenance SLSA predicate type is invalid.")
    predicate = _mapping(provenance.get("predicate"), "provenance predicate")
    definition = _mapping(predicate.get("buildDefinition"), "provenance build definition")
    _expect(definition.get("buildType"), expected_build_type, "provenance build type")
    external = _mapping(
        definition.get("externalParameters"),
        "provenance external parameters",
    )
    source = _mapping(external.get("source"), "provenance source")
    _expect(source.get("uri"), expected_source_uri, "provenance source URI")
    source_digest = _mapping(source.get("digest"), "provenance source digest")
    if set(source_digest) != {"sha1"}:
        raise RuntimeError("The provenance source digest schema is invalid.")
    source_sha = source_digest.get("sha1")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_source_sha):
        raise RuntimeError("The expected source commit SHA is not 40 lowercase hex characters.")
    if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise RuntimeError("The provenance source commit SHA is invalid.")
    _expect(source_sha, expected_source_sha, "provenance source commit SHA")
    run_details = _mapping(predicate.get("runDetails"), "provenance run details")
    builder = _mapping(run_details.get("builder"), "provenance builder")
    _expect(builder.get("id"), expected_builder_id, "provenance builder ID")

    if update.get("schema") != UPDATE_MANIFEST_SCHEMA or update.get("version") != 1:
        raise RuntimeError("The update manifest schema is invalid.")
    _expect(update.get("release_version"), expected_release_version, "update release version")
    _expect(update.get("channel"), expected_update_channel, "update channel")


def _expected_artifacts(artifacts: list[Path]) -> dict[str, dict[str, object]]:
    expected = {
        path.name: {"sha256": digest(path), "size": path.stat().st_size}
        for path in artifacts
    }
    if len(expected) != len(artifacts):
        raise RuntimeError("The release artifact file names are not unique.")
    return expected


def _artifact_entries(
    value: object,
    *,
    label: str,
    required_keys: set[str],
) -> dict[str, dict]:
    if not isinstance(value, list):
        raise RuntimeError(f"The {label} list is missing or invalid.")
    entries: dict[str, dict] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != required_keys:
            raise RuntimeError(f"A {label} entry has an invalid schema.")
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise RuntimeError(f"A {label} entry has an invalid name.")
        if name in entries:
            raise RuntimeError(f"The {label} list has a duplicate file name.")
        item_digest = item.get("digest")
        if not isinstance(item_digest, dict) or set(item_digest) != {"sha256"}:
            raise RuntimeError(f"A {label} entry has an invalid digest schema.")
        sha256 = item_digest.get("sha256")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise RuntimeError(f"A {label} entry has an invalid SHA-256 digest.")
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise RuntimeError(f"A {label} entry has an invalid byte size.")
        if "url" in required_keys and item.get("url") is not None and not isinstance(item.get("url"), str):
            raise RuntimeError(f"A {label} entry has an invalid URL.")
        entries[name] = item
    return entries


def _verify_artifact_entries(
    value: object,
    artifacts: list[Path],
    *,
    label: str,
    required_keys: set[str],
) -> None:
    expected = _expected_artifacts(artifacts)
    entries = _artifact_entries(value, label=label, required_keys=required_keys)
    if set(entries) != set(expected):
        raise RuntimeError(f"The {label} file names do not match the release artifacts.")
    for name, actual in expected.items():
        entry = entries[name]
        if entry["digest"]["sha256"] != actual["sha256"]:
            raise RuntimeError(f"The {label} SHA-256 digest does not match {name}.")
        if entry["size"] != actual["size"]:
            raise RuntimeError(f"The {label} byte size does not match {name}.")


def verify_subjects(provenance: dict, artifacts: list[Path]) -> None:
    _verify_artifact_entries(
        provenance.get("subject"),
        artifacts,
        label="provenance subject",
        required_keys={"name", "digest", "size"},
    )


def verify_update_artifacts(update: dict, artifacts: list[Path]) -> None:
    _verify_artifact_entries(
        update.get("artifacts"),
        artifacts,
        label="update artifact",
        required_keys={"name", "digest", "size", "url"},
    )


def verify_update_trust(app: Path, evidence_dir: Path, *, production: bool) -> None:
    config_path = app / "Contents" / "Resources" / "Mod" / "VibeCAD" / "update-config.json"
    if not production and not config_path.is_file():
        return
    if not config_path.is_file():
        raise RuntimeError("The production app has no signed update configuration.")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema") != "vibecad-update-config-v1" or config.get("version") != 1:
        raise RuntimeError("The bundled update configuration schema is invalid.")
    if config.get("channel") not in {"stable", "prerelease", "nightly"}:
        raise RuntimeError("The bundled update channel is invalid.")
    key_name = str(config.get("public_key") or "")
    if Path(key_name).name != key_name:
        raise RuntimeError("The bundled update public-key path is unsafe.")
    public_key = config_path.parent / key_name
    if not public_key.is_file():
        raise RuntimeError("The bundled update public key is missing.")
    for field, suffix in (
        ("manifest_url", "/vibecad-macos-update.json"),
        ("signature_url", "/vibecad-macos-update.json.sig"),
    ):
        if not str(config.get(field) or "").startswith("https://") or not str(config[field]).endswith(suffix):
            raise RuntimeError(f"The bundled {field} is invalid.")
    if not production:
        return
    manifest = evidence_dir / "vibecad-macos-update.json"
    signature_text = evidence_dir / "vibecad-macos-update.json.sig"
    if not signature_text.is_file():
        raise RuntimeError("The production update manifest signature is missing.")
    with tempfile.TemporaryDirectory(prefix="vibecad-update-signature-") as directory:
        signature = Path(directory) / "signature.bin"
        try:
            signature.write_bytes(base64.b64decode(signature_text.read_text().strip(), validate=True))
        except ValueError as exc:
            raise RuntimeError("The production update signature is not valid Base64.") from exc
        run([
            "openssl", "dgst", "-sha256", "-verify", str(public_key),
            "-signature", str(signature), str(manifest),
        ])


def mounted_app(dmg: Path) -> tuple[Path, str]:
    raw = run(["hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(dmg)], capture=True)
    info = plistlib.loads(raw.encode())
    entities = info.get("system-entities") or []
    mount = next((item.get("mount-point") for item in entities if item.get("mount-point")), None)
    device = next((item.get("dev-entry") for item in entities if item.get("dev-entry")), None)
    if not mount or not device:
        raise RuntimeError("The DMG did not expose a mounted volume.")
    apps = list(Path(mount).glob("*.app"))
    if len(apps) != 1:
        run(["hdiutil", "detach", device])
        raise RuntimeError("The DMG must contain exactly one application bundle.")
    return apps[0], device


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dmg", type=Path, required=True)
    parser.add_argument("--pkg", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--expected-application-name", required=True)
    parser.add_argument("--expected-release-version", required=True)
    parser.add_argument("--expected-source-uri", required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--expected-builder-id", required=True)
    parser.add_argument("--expected-build-type", required=True)
    parser.add_argument("--expected-update-channel", required=True)
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--skip-launch", action="store_true")
    args = parser.parse_args()
    artifacts = [args.dmg.resolve(), args.pkg.resolve()]
    if any(not path.is_file() for path in artifacts):
        raise RuntimeError("A release artifact is missing.")
    provenance = json.loads((args.evidence_dir / "vibecad-macos.intoto.jsonl").read_text())
    sbom = json.loads((args.evidence_dir / "vibecad-macos.cdx.json").read_text())
    update = json.loads((args.evidence_dir / "vibecad-macos-update.json").read_text())
    verify_release_identity(
        sbom,
        provenance,
        update,
        expected_application_name=args.expected_application_name,
        expected_release_version=args.expected_release_version,
        expected_source_uri=args.expected_source_uri,
        expected_source_sha=args.expected_source_sha,
        expected_builder_id=args.expected_builder_id,
        expected_build_type=args.expected_build_type,
        expected_update_channel=args.expected_update_channel,
    )
    verify_subjects(provenance, artifacts)
    verify_update_artifacts(update, artifacts)
    app, device = mounted_app(args.dmg)
    try:
        run(["codesign", "--verify", "--deep", "--strict", str(app)])
        info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
        if info.get("CFBundleIdentifier") != "com.vibecad.desktop":
            raise RuntimeError("The application bundle identifier is not com.vibecad.desktop.")
        if info.get("CFBundleShortVersionString") != args.expected_release_version:
            raise RuntimeError("The application version does not match the trusted release version.")
        executable = app / "Contents" / "MacOS" / str(info.get("CFBundleExecutable") or "")
        if not args.skip_launch:
            run([str(executable), "--vibecad-launcher-smoke"])
        verify_update_trust(app, args.evidence_dir, production=args.production)
        if args.production:
            run(["spctl", "--assess", "--type", "execute", str(app)])
            run(["xcrun", "stapler", "validate", str(args.dmg)])
    finally:
        run(["hdiutil", "detach", device])
    signature_result = subprocess.run(
        ["pkgutil", "--check-signature", str(args.pkg)],
        text=True,
        capture_output=True,
    )
    signature = signature_result.stdout + signature_result.stderr
    if args.production and "Developer ID Installer" not in signature:
        raise RuntimeError("The PKG has no Developer ID Installer signature.")
    if not args.production and signature_result.returncode not in {0, 1}:
        raise RuntimeError("The development PKG signature could not be inspected.")
    with tempfile.TemporaryDirectory(prefix="vibecad-pkg-smoke-") as directory:
        expanded = Path(directory) / "expanded"
        run(["pkgutil", "--expand-full", str(args.pkg), str(expanded)])
        apps = list(expanded.rglob("VibeCAD.app"))
        if len(apps) != 1:
            raise RuntimeError("The PKG payload does not contain one VibeCAD.app.")
    print("macOS release smoke verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
