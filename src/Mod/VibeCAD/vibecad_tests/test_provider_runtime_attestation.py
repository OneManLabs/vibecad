# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from tools.provider_runtime_attestation import (
    ATTESTATION_SCHEMA,
    ProviderRuntimeAttestationError,
    _component,
    _safe_child_environment,
    attest_provider_runtime,
)


ROOT = Path(__file__).resolve().parents[4]


def test_attestation_child_environment_removes_ambient_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-cross-boundary")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-cross-boundary")
    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/untrusted/library.dylib")

    environment = _safe_child_environment({"VIBECAD_TEST_NONCE": "1" * 64})

    assert environment["VIBECAD_TEST_NONCE"] == "1" * 64
    assert "OPENAI_API_KEY" not in environment
    assert "ANTHROPIC_API_KEY" not in environment
    assert "PYTHONPATH" not in environment
    assert "DYLD_INSERT_LIBRARIES" not in environment


def test_component_hashing_rejects_symbolic_link(tmp_path: Path) -> None:
    target = tmp_path / "provider.py"
    target.write_bytes(b"provider")
    link = tmp_path / "provider-link.py"
    link.symlink_to(target)

    with pytest.raises(ProviderRuntimeAttestationError, match="symbolic link"):
        _component(tmp_path.resolve(), link, label="provider component")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS runtime attestation")
def test_selected_provider_runtime_attestation_is_exact_and_repeatable() -> None:
    freecad_cmd = ROOT / "build" / "release" / "bin" / "FreeCADCmd"
    if not freecad_cmd.is_file():
        pytest.skip("The local FreeCADCmd developer build is not available.")

    first = attest_provider_runtime(ROOT, freecad_cmd)
    second = attest_provider_runtime(ROOT, freecad_cmd)

    assert first == second
    assert first["schema"] == ATTESTATION_SCHEMA
    assert first["platform"] == "darwin"
    assert first["python"]["path"].startswith(".pixi/envs/default/")
    assert [item["name"] for item in first["provider_modules"]] == [
        "httpcore",
        "httpx",
        "openai",
    ]
    assert first["loaded_python_files"]
    assert first["native_libraries"]
    paths = [item["path"] for item in first["components"]]
    assert paths == sorted(set(paths))
    assert all(not os.path.isabs(path) and ".." not in Path(path).parts for path in paths)
