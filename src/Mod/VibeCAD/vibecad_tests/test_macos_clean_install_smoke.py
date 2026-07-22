# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

import pytest


REPOSITORY = Path(__file__).resolve().parents[4]


def _load_tool(name: str):
    path = REPOSITORY / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


identity = _load_tool("verify_vibecad_source_identity")
smoke = _load_tool("macos_clean_install_smoke")


def _write(path: Path, content: bytes = b"payload") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _module_tree(root: Path) -> None:
    for name in identity.REQUIRED_MODULES:
        _write(root / name, f"# {name}\n".encode())
    _write(root / "tool_impl/service/example.py", b"VALUE = 1\n")
    _write(root / "vibecad_tests/test_example.py", b"raise RuntimeError\n")
    _write(root / "build123d_runtime/site-packages/generated.py", b"generated\n")


def test_source_identity_compares_relative_paths_and_sha256(tmp_path: Path) -> None:
    source = tmp_path / "source"
    installed = tmp_path / "installed"
    _module_tree(source)
    shutil.copytree(source, installed)
    result = identity.verify_source_identity(source, installed)
    assert result["ok"] is True
    assert result["file_count"] == len(identity.REQUIRED_MODULES) + 1

    _write(installed / "VibeCADSession.py", b"stale source\n")
    with pytest.raises(ValueError, match="SHA-256 mismatch: VibeCADSession.py"):
        identity.verify_source_identity(source, installed)


def test_source_identity_rejects_missing_and_unexpected_modules(tmp_path: Path) -> None:
    source = tmp_path / "source"
    installed = tmp_path / "installed"
    _module_tree(source)
    shutil.copytree(source, installed)
    (installed / "VibeCADGui.py").unlink()
    _write(installed / "Unexpected.py", b"unexpected\n")
    with pytest.raises(ValueError) as error:
        identity.verify_source_identity(source, installed)
    assert "missing: VibeCADGui.py" in str(error.value)
    assert "unexpected: Unexpected.py" in str(error.value)


def _artifact(path: Path, **extra) -> dict[str, object]:
    _write(path)
    return {
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        **extra,
    }


def test_clean_install_report_verifies_paths_hashes_and_cad_results(
    tmp_path: Path,
) -> None:
    root = tmp_path / "smoke"
    app = tmp_path / "Applications/VibeCAD.app"
    module_root = app / "Contents/Resources/Mod/VibeCAD"
    module_paths = {}
    for name in ("VibeCADAcceptance", "VibeCADProject", "VibeCADSession", "project_export"):
        path = module_root / f"{name}.py"
        _write(path)
        module_paths[name] = str(path)
    document = _artifact(
        root / "part.FCStd",
        dimensions=list(smoke.DIMENSIONS),
        fully_constrained=True,
        shape_valid=True,
    )
    step = _artifact(root / "exports/part.step", shape_valid=True)
    stl = _artifact(root / "exports/part.stl", facet_count=12)
    report = root / "smoke-report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(
            {
                "schema": smoke.SCHEMA,
                "ok": True,
                "application_path": str(app),
                "run_states": list(smoke.RUN_STATES),
                "accepted_revision": "a" * 64,
                "revision_count": 1,
                "module_paths": module_paths,
                "document": document,
                "exports": {"step": step, "stl": stl},
            }
        ),
        encoding="utf-8",
    )
    payload = smoke.verify_report(report, root, app)
    assert payload["accepted_revision"] == "a" * 64

    (root / "exports/part.stl").write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256 changed"):
        smoke.verify_report(report, root, app)


def test_workflow_cleans_stale_builds_and_runs_exact_package_gate() -> None:
    source = (REPOSITORY / ".github/workflows/vibecad-macos.yml").read_text(
        encoding="utf-8"
    )
    restore = source.index("- name: Restore Pixi and runtime caches")
    clean = source.index("- name: Remove restored local package products")
    build = source.index("- name: Build package environment")
    identity_check = source.index("- name: Verify installed VibeCAD source identity")
    bundle = source.index("- name: Create and validate VibeCAD DMG")
    release_check = source.index("- name: Verify macOS release contents")
    package_gate = source.index("- name: Install and test generated macOS package")
    cleanup = source.index("- name: Remove clean-machine test installation")
    assert restore < clean < build < identity_check < bundle
    assert release_check < package_gate < cleanup
    assert "rm -rf -- .pixi/envs/default .pixi/envs/package" in source
    assert "pixi clean --build" in source
    assert "tools/verify_vibecad_source_identity.py" in source
    assert 'installed_app="/Applications/VibeCAD.app"' in source
    assert 'install_marker="${RUNNER_TEMP}/vibecad-clean-install-owned"' in source
    assert '/usr/bin/touch "${install_marker}"' in source
    assert 'sudo /usr/sbin/installer -pkg "${packages[0]}" -target /' in source
    assert "VIBECAD_RUN_CLEAN_INSTALL_SMOKE=1" in source
    cleanup_block = source[cleanup : source.index("- name: Verify production", cleanup)]
    assert "if: always()" in cleanup_block
    assert 'sudo /bin/rm -rf -- "${installed_app}"' in cleanup_block
    assert '"${installed_app}" != "/Applications/VibeCAD.app"' in cleanup_block
    assert '[[ ! -f "${install_marker}" ]]' in cleanup_block
