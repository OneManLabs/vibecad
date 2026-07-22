# SPDX-License-Identifier: LGPL-2.1-or-later
from pathlib import Path
import plistlib


REPOSITORY = Path(__file__).resolve().parents[4]


def test_macos_recipe_does_not_install_or_mount_vendor_drivers() -> None:
    source = (REPOSITORY / "package/rattler-build/build.sh").read_text(encoding="utf-8")
    assert "sudo installer" not in source
    assert "hdiutil attach" not in source
    assert "3DxWareMac" not in source
    assert "FREECAD_3DCONNEXION_SUPPORT:STRING=NavLib" in source


def test_macos_recipe_has_no_privileged_build_host_mutation() -> None:
    source = (REPOSITORY / "package/rattler-build/build.sh").read_text(encoding="utf-8")
    executable_lines = [
        line.strip() for line in source.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any(line == "sudo" or line.startswith("sudo ") for line in executable_lines)


def test_macos_app_and_installer_use_the_vibecad_bundle_identifier() -> None:
    template_path = (
        REPOSITORY / "package/rattler-build/osx/Info.plist.template"
    )
    bundle = (
        REPOSITORY / "package/rattler-build/osx/create_bundle.sh"
    ).read_text(encoding="utf-8")
    verifier = (
        REPOSITORY / "tools/verify_macos_release.py"
    ).read_text(encoding="utf-8")
    template = plistlib.loads(template_path.read_bytes())
    assert template["CFBundleIdentifier"] == "com.vibecad.desktop"
    assert '"com.vibecad.desktop"' in bundle
    assert '!= "com.vibecad.desktop"' in verifier
