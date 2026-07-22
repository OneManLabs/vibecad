#!/usr/bin/env python3
from pathlib import Path
import copy
import json
import subprocess
import tempfile

from verify_macos_release import (
    verify_release_identity,
    verify_subjects,
    verify_update_artifacts,
    verify_update_trust,
)


APPLICATION_NAME = "VibeCAD"
RELEASE_VERSION = "26.3.2"
SOURCE_URI = "https://github.com/10-X-eng/vibecad"
SOURCE_SHA = "a" * 40
BUILDER_ID = "test-builder"
BUILD_TYPE = "https://vibecad.dev/build-types/macos-release/v1"
UPDATE_CHANNEL = "stable"


def verify_identity(sbom: dict, provenance: dict, update: dict) -> None:
    verify_release_identity(
        sbom,
        provenance,
        update,
        expected_application_name=APPLICATION_NAME,
        expected_release_version=RELEASE_VERSION,
        expected_source_uri=SOURCE_URI,
        expected_source_sha=SOURCE_SHA,
        expected_builder_id=BUILDER_ID,
        expected_build_type=BUILD_TYPE,
        expected_update_channel=UPDATE_CHANNEL,
    )


def set_nested(document: dict, path: tuple[str, ...], value: str) -> None:
    current = document
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    dmg = root / "VibeCAD.dmg"
    pkg = root / "VibeCAD.pkg"
    dmg.write_bytes(b"dmg release")
    pkg.write_bytes(b"pkg release")
    artifacts = [dmg, pkg]
    packages = root / "packages.txt"
    packages.write_text("python 3.11.14\nqt 6.8.3\n", encoding="utf-8")
    output = root / "evidence"
    subprocess.run([
        "python3", "tools/generate_release_evidence.py", "--artifact", str(dmg),
        "--artifact", str(pkg),
        "--packages", str(packages), "--output-dir", str(output),
        "--source-uri", SOURCE_URI, "--source-sha", SOURCE_SHA,
        "--builder-id", BUILDER_ID, "--build-type", BUILD_TYPE,
        "--application-name", APPLICATION_NAME, "--version", RELEASE_VERSION,
        "--channel", UPDATE_CHANNEL,
    ], check=True)
    checksums = (output / "SHA256SUMS").read_text()
    assert "VibeCAD.dmg" in checksums
    assert "VibeCAD.pkg" in checksums
    sbom = json.loads((output / "vibecad-macos.cdx.json").read_text())
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["metadata"]["component"]["version"] == RELEASE_VERSION
    provenance = json.loads((output / "vibecad-macos.intoto.jsonl").read_text())
    assert provenance["predicateType"] == "https://slsa.dev/provenance/v1"
    update = json.loads((output / "vibecad-macos-update.json").read_text())
    assert update["schema"] == "vibecad-update-manifest-v1"
    assert update["artifacts"][0]["size"] == dmg.stat().st_size
    verify_identity(sbom, provenance, update)
    verify_subjects(provenance, artifacts)
    verify_update_artifacts(update, artifacts)

    tamper_cases = (
        ("CycloneDX application name", "sbom", ("metadata", "component", "name")),
        ("CycloneDX application version", "sbom", ("metadata", "component", "version")),
        ("in-toto statement type", "provenance", ("_type",)),
        ("SLSA predicate type", "provenance", ("predicateType",)),
        (
            "provenance source URI",
            "provenance",
            ("predicate", "buildDefinition", "externalParameters", "source", "uri"),
        ),
        (
            "provenance source commit SHA",
            "provenance",
            ("predicate", "buildDefinition", "externalParameters", "source", "digest", "sha1"),
        ),
        (
            "provenance builder ID",
            "provenance",
            ("predicate", "runDetails", "builder", "id"),
        ),
        (
            "provenance build type",
            "provenance",
            ("predicate", "buildDefinition", "buildType"),
        ),
        ("update release version", "update", ("release_version",)),
        ("update channel", "update", ("channel",)),
    )
    clean = {"sbom": sbom, "provenance": provenance, "update": update}
    for label, target, path in tamper_cases:
        changed = copy.deepcopy(clean)
        tampered_value = "b" * 40 if label == "provenance source commit SHA" else "tampered"
        set_nested(changed[target], path, tampered_value)
        try:
            verify_identity(changed["sbom"], changed["provenance"], changed["update"])
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"Tampered {label} passed verification.")

    artifact_evidence = (
        ("provenance subject", "provenance", "subject", verify_subjects),
        ("update artifact", "update", "artifacts", verify_update_artifacts),
    )
    artifact_tamper_count = 0
    for label, target, key, checker in artifact_evidence:
        source_entries = clean[target][key]
        missing = copy.deepcopy(clean[target])
        missing[key].pop()
        extra = copy.deepcopy(clean[target])
        extra_entry = copy.deepcopy(source_entries[0])
        extra_entry["name"] = "Unexpected.pkg"
        extra[key].append(extra_entry)
        duplicate = copy.deepcopy(clean[target])
        duplicate[key].append(copy.deepcopy(source_entries[0]))
        wrong_name = copy.deepcopy(clean[target])
        wrong_name[key][0]["name"] = "Renamed.dmg"
        wrong_digest = copy.deepcopy(clean[target])
        wrong_digest[key][0]["digest"]["sha256"] = "f" * 64
        wrong_size = copy.deepcopy(clean[target])
        wrong_size[key][0]["size"] += 1
        for fault, changed in (
            ("missing", missing),
            ("extra", extra),
            ("duplicate", duplicate),
            ("wrong name", wrong_name),
            ("wrong digest", wrong_digest),
            ("wrong size", wrong_size),
        ):
            try:
                checker(changed, artifacts)
            except RuntimeError:
                artifact_tamper_count += 1
            else:
                raise AssertionError(f"The {label} {fault} tamper passed verification.")
    private_key = root / "update-private.pem"
    public_key = root / "update-public.pem"
    signature = output / "vibecad-macos-update.json.sig"
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(private_key)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["openssl", "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)], check=True)
    subprocess.run(["bash", "package/scripts/sign_update_manifest.sh", str(output / "vibecad-macos-update.json"), str(private_key), str(signature)], check=True)
    raw_signature = root / "signature.bin"
    subprocess.run(["openssl", "base64", "-d", "-A", "-in", str(signature), "-out", str(raw_signature)], check=True)
    subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(public_key), "-signature", str(raw_signature), str(output / "vibecad-macos-update.json")], check=True)
    app_config = root / "VibeCAD.app" / "Contents" / "Resources" / "Mod" / "VibeCAD"
    app_config.mkdir(parents=True)
    (app_config / "update-public.pem").write_bytes(public_key.read_bytes())
    (app_config / "update-config.json").write_text(json.dumps({
        "schema": "vibecad-update-config-v1",
        "version": 1,
        "manifest_url": "https://github.com/10-X-eng/vibecad/releases/download/test/vibecad-macos-update.json",
        "signature_url": "https://github.com/10-X-eng/vibecad/releases/download/test/vibecad-macos-update.json.sig",
        "public_key": "update-public.pem",
        "channel": UPDATE_CHANNEL,
    }), encoding="utf-8")
    verify_update_trust(root / "VibeCAD.app", output, production=True)
print(
    "release evidence self-test passed: "
    f"1 positive, {len(tamper_cases)} identity tamper, and {artifact_tamper_count} artifact tamper cases"
)
