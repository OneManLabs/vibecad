#!/usr/bin/env python3
"""Build synthetic macOS artifacts and exercise the release verifier."""

from pathlib import Path
import plistlib
import subprocess
import tempfile


with tempfile.TemporaryDirectory(prefix="vibecad-release-smoke-") as directory:
    root = Path(directory)
    source_uri = "https://example.invalid/vibecad"
    source_sha = "a" * 40
    builder_id = "selftest"
    build_type = "https://vibecad.dev/build-types/macos-release/v1"
    release_version = "1.0"
    update_channel = "development"
    staging = root / "staging"
    app = staging / "VibeCAD.app"
    executable = app / "Contents" / "MacOS" / "VibeCAD"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    info = {
        "CFBundleIdentifier": "com.vibecad.desktop",
        "CFBundlePackageType": "APPL",
        "CFBundleExecutable": "VibeCAD",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
    }
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    dmg = root / "VibeCAD-test.dmg"
    subprocess.run(["hdiutil", "create", "-fs", "HFS+", "-srcfolder", str(staging), "-volname", "VibeCAD", str(dmg)], check=True, stdout=subprocess.DEVNULL)
    pkg = root / "VibeCAD-test.pkg"
    subprocess.run(["bash", "package/scripts/create_macos_pkg.sh", "--app", str(app), "--output", str(pkg), "--version", "1.0"], check=True)
    evidence = root / "evidence"
    subprocess.run([
        "python3", "tools/generate_release_evidence.py", "--artifact", str(dmg),
        "--artifact", str(pkg), "--output-dir", str(evidence), "--source-uri",
        source_uri, "--source-sha", source_sha,
        "--builder-id", builder_id, "--build-type", build_type,
        "--application-name", "VibeCAD", "--version", release_version,
        "--channel", update_channel,
    ], check=True)
    subprocess.run([
        "python3", "tools/verify_macos_release.py", "--dmg", str(dmg),
        "--pkg", str(pkg), "--evidence-dir", str(evidence),
        "--expected-application-name", "VibeCAD",
        "--expected-release-version", release_version,
        "--expected-source-uri", source_uri,
        "--expected-source-sha", source_sha,
        "--expected-builder-id", builder_id,
        "--expected-build-type", build_type,
        "--expected-update-channel", update_channel,
    ], check=True)
print("macOS release smoke self-test passed")
