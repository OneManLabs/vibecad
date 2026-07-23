# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the bounded cross-platform Python gate."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.verify_vibecad_portability as portability
from tools.verify_vibecad_portability import (
    CONTRACT_SCHEMA,
    PortabilityContractError,
    compile_portable_modules,
    load_contract,
    run_contract_tests,
    scan_macos_framework_imports,
    verify_repository,
)


REPOSITORY = Path(__file__).resolve().parents[4]
CONTRACT_PATH = REPOSITORY / "tools" / "vibecad-portability-contract-v1.json"


def _write_contract(path: Path, contract: dict) -> Path:
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def _minimal_repository(tmp_path: Path) -> tuple[Path, dict]:
    source = tmp_path / "src" / "Mod" / "VibeCAD"
    tests = source / "vibecad_tests"
    tests.mkdir(parents=True)
    (source / "Shared.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "MacAdapter.py").write_text("VALUE = 2\n", encoding="utf-8")
    (tests / "test_pure.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (tests / "test_adapter.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    contract = {
        "schema": CONTRACT_SCHEMA,
        "version": 1,
        "python": {"implementation": "CPython", "version": "3.11.14"},
        "source_root": "src/Mod/VibeCAD",
        "portable_modules": ["src/Mod/VibeCAD/Shared.py"],
        "macos_framework_import_roots": [
            "AppKit", "Foundation", "Quartz", "objc"
        ],
        "macos_import_adapters": ["src/Mod/VibeCAD/MacAdapter.py"],
        "tests": {
            "pure": ["src/Mod/VibeCAD/vibecad_tests/test_pure.py"],
            "platform_adapter": [
                "src/Mod/VibeCAD/vibecad_tests/test_adapter.py"
            ],
        },
    }
    return source, contract


def test_repository_contract_is_versioned_explicit_and_clean() -> None:
    contract, report = verify_repository(
        repo_root=REPOSITORY,
        contract_path=CONTRACT_PATH,
        compile_modules=True,
    )
    assert contract["schema"] == CONTRACT_SCHEMA
    assert report["status"] == "pass"
    assert report["compiled_modules"] == contract["portable_modules"]
    assert report["source_scan"]["violations"] == []
    assert len(contract["tests"]["pure"]) == 6
    assert len(contract["tests"]["platform_adapter"]) == 3


def test_contract_rejects_unknown_fields_and_path_traversal(tmp_path: Path) -> None:
    _, contract = _minimal_repository(tmp_path)
    contract["unknown"] = True
    with pytest.raises(PortabilityContractError, match="fields are not exact"):
        load_contract(_write_contract(tmp_path / "unknown.json", contract), repo_root=tmp_path)

    contract.pop("unknown")
    contract["portable_modules"] = ["../escape.py"]
    with pytest.raises(PortabilityContractError, match="unsafe path"):
        load_contract(_write_contract(tmp_path / "escape.json", contract), repo_root=tmp_path)


def test_contract_rejects_missing_or_implicit_test_files(tmp_path: Path) -> None:
    _, contract = _minimal_repository(tmp_path)
    contract["tests"]["pure"] = ["src/Mod/VibeCAD/vibecad_tests/missing.py"]
    with pytest.raises(PortabilityContractError, match=r"test_\*\.py"):
        load_contract(_write_contract(tmp_path / "implicit.json", contract), repo_root=tmp_path)

    contract["tests"]["pure"] = ["src/Mod/VibeCAD/vibecad_tests/test_missing.py"]
    with pytest.raises(PortabilityContractError, match="missing Python file"):
        load_contract(_write_contract(tmp_path / "missing.json", contract), repo_root=tmp_path)


@pytest.mark.parametrize(
    "statement",
    [
        "import AppKit\n",
        "from Foundation import NSObject\n",
        "import importlib\nimportlib.import_module('objc')\n",
        "__import__('Quartz')\n",
    ],
)
def test_macos_framework_imports_fail_outside_adapter(
    tmp_path: Path, statement: str
) -> None:
    source, raw = _minimal_repository(tmp_path)
    (source / "Shared.py").write_text(statement, encoding="utf-8")
    contract = load_contract(
        _write_contract(tmp_path / "contract.json", raw), repo_root=tmp_path
    )
    scan = scan_macos_framework_imports(tmp_path, contract)
    assert scan["violations"][0]["kind"] == "macos_framework_import_outside_adapter"


def test_macos_framework_import_is_allowed_only_in_named_adapter(tmp_path: Path) -> None:
    source, raw = _minimal_repository(tmp_path)
    (source / "MacAdapter.py").write_text("import AppKit\n", encoding="utf-8")
    contract = load_contract(
        _write_contract(tmp_path / "contract.json", raw), repo_root=tmp_path
    )
    scan = scan_macos_framework_imports(tmp_path, contract)
    assert scan["violations"] == []
    assert scan["approved_adapter_imports"][0]["path"].endswith("MacAdapter.py")


def test_portable_module_compile_failure_is_not_skipped(tmp_path: Path) -> None:
    source, raw = _minimal_repository(tmp_path)
    (source / "Shared.py").write_text("def broken(:\n", encoding="utf-8")
    contract = load_contract(
        _write_contract(tmp_path / "contract.json", raw), repo_root=tmp_path
    )
    with pytest.raises(PortabilityContractError, match="compilation failed"):
        compile_portable_modules(tmp_path, contract)


def test_test_runner_receives_only_the_explicit_contract_files(tmp_path: Path) -> None:
    _, raw = _minimal_repository(tmp_path)
    contract = load_contract(
        _write_contract(tmp_path / "contract.json", raw), repo_root=tmp_path
    )
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    result = run_contract_tests(tmp_path, contract, runner=runner)
    explicit = [*raw["tests"]["pure"], *raw["tests"]["platform_adapter"]]
    assert result["files"] == explicit
    assert calls[0][0][-len(explicit) :] == explicit
    assert "--noconftest" in calls[0][0]
    assert calls[0][1]["check"] is False
    assert calls[0][1]["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert str(tmp_path / "src" / "Mod" / "VibeCAD") in calls[0][1]["env"][
        "PYTHONPATH"
    ].split(os.pathsep)


def test_exact_python_runtime_and_source_sha_fail_closed(monkeypatch) -> None:
    with pytest.raises(PortabilityContractError, match="must use CPython 3.11.14"):
        monkeypatch.setattr("platform.python_version", lambda: "3.11.13")
        verify_repository(
            repo_root=REPOSITORY,
            contract_path=CONTRACT_PATH,
            enforce_python=True,
        )
    with pytest.raises(PortabilityContractError, match="does not match"):
        verify_repository(
            repo_root=REPOSITORY,
            contract_path=CONTRACT_PATH,
            expected_source_sha="0" * 40,
        )


def test_exact_source_sha_rejects_dirty_worktree(monkeypatch) -> None:
    source_sha = portability._git_source_sha(REPOSITORY)
    monkeypatch.setattr(
        portability,
        "_git_worktree_changes",
        lambda _root: [" M src/Mod/VibeCAD/VibeCADBenchmark.py"],
    )
    with pytest.raises(PortabilityContractError, match="requires a clean worktree"):
        verify_repository(
            repo_root=REPOSITORY,
            contract_path=CONTRACT_PATH,
            expected_source_sha=source_sha,
        )
