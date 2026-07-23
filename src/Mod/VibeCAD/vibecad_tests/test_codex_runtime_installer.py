# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS runtime package")
def test_codex_runtime_installer_uses_portable_sha256_and_is_repeatable(
    tmp_path: Path,
) -> None:
    installer = (
        ROOT
        / "package"
        / "rattler-build"
        / "scripts"
        / "install_vibecad_codex_runtime.sh"
    )
    module = tmp_path / "Mod" / "VibeCAD"
    command = ["bash", str(installer), sys.executable, str(module)]

    first = subprocess.run(
        command,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
        check=False,
    )
    assert first.returncode == 0, first.stdout
    assert "0.144.5 installed" in first.stdout

    runtime = module / "codex_runtime"
    executable = runtime / "codex-app-server"
    assert executable.is_file()
    assert executable.stat().st_mode & 0o111
    metadata = json.loads((runtime / "runtime.json").read_text(encoding="utf-8"))
    assert metadata["schema"] == "vibecad-codex-runtime-v1"
    assert metadata["version"] == "0.144.5"

    second = subprocess.run(
        command,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
        check=False,
    )
    assert second.returncode == 0, second.stdout
    assert "runtime is current" in second.stdout
