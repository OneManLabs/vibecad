#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Verify the bounded VibeCAD portable Python contract."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import py_compile
import subprocess
import sys
import tempfile
from typing import Any, Callable, Mapping, Sequence


CONTRACT_SCHEMA = "vibecad-portability-contract-v1"
CONTRACT_VERSION = 1
REPORT_SCHEMA = "vibecad-portability-report-v1"
REPORT_VERSION = 1
_CONTRACT_FIELDS = {
    "schema",
    "version",
    "python",
    "source_root",
    "portable_modules",
    "macos_framework_import_roots",
    "macos_import_adapters",
    "tests",
}
_TEST_FIELDS = {"pure", "platform_adapter"}


class PortabilityContractError(RuntimeError):
    """The portability contract or its checked source is invalid."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _safe_relative_path(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PortabilityContractError(f"Portability field {field} must contain paths.")
    if "\\" in value:
        raise PortabilityContractError(
            f"Portability field {field} must use forward slashes."
        )
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PortabilityContractError(
            f"Portability field {field} contains an unsafe path: {value!r}."
        )
    return path.as_posix()


def _path_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise PortabilityContractError(
            f"Portability field {field} must be a nonempty path array."
        )
    paths = [_safe_relative_path(item, field=field) for item in value]
    if len(paths) != len(set(paths)):
        raise PortabilityContractError(
            f"Portability field {field} contains a duplicate path."
        )
    return paths


def _require_files(repo_root: Path, paths: Sequence[str], *, field: str) -> None:
    root = repo_root.resolve()
    for relative in paths:
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PortabilityContractError(
                f"Portability field {field} escapes the repository: {relative}."
            ) from exc
        if candidate.suffix != ".py" or not candidate.is_file():
            raise PortabilityContractError(
                f"Portability field {field} names a missing Python file: {relative}."
            )


def load_contract(path: str | Path, *, repo_root: str | Path) -> dict[str, Any]:
    """Load and strictly validate one versioned portability contract."""

    contract_path = Path(path)
    try:
        raw = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PortabilityContractError(
            f"The portability contract could not be read: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise PortabilityContractError("The portability contract must be a JSON object.")
    if raw.get("schema") != CONTRACT_SCHEMA or raw.get("version") != CONTRACT_VERSION:
        raise PortabilityContractError("The portability contract schema is not supported.")
    if set(raw) != _CONTRACT_FIELDS:
        raise PortabilityContractError("The portability contract fields are not exact.")

    python_spec = raw.get("python")
    if not isinstance(python_spec, dict) or set(python_spec) != {
        "implementation",
        "version",
    }:
        raise PortabilityContractError("The portability Python runtime is invalid.")
    if python_spec.get("implementation") != "CPython":
        raise PortabilityContractError("The portability runtime must be CPython.")
    version_parts = str(python_spec.get("version") or "").split(".")
    if len(version_parts) != 3 or any(not part.isdigit() for part in version_parts):
        raise PortabilityContractError("The portability Python version must be exact.")

    source_root = _safe_relative_path(raw.get("source_root"), field="source_root")
    portable_modules = _path_list(raw.get("portable_modules"), field="portable_modules")
    adapters = _path_list(raw.get("macos_import_adapters"), field="macos_import_adapters")
    for relative in (*portable_modules, *adapters):
        if not PurePosixPath(relative).is_relative_to(PurePosixPath(source_root)):
            raise PortabilityContractError(
                "Portable modules and platform adapters must be below source_root."
            )

    import_roots = raw.get("macos_framework_import_roots")
    if (
        not isinstance(import_roots, list)
        or not import_roots
        or any(
            not isinstance(item, str)
            or not item
            or not item.replace("_", "").isalnum()
            for item in import_roots
        )
        or len(import_roots) != len(set(import_roots))
    ):
        raise PortabilityContractError(
            "The macOS-only framework import roots are invalid."
        )

    tests = raw.get("tests")
    if not isinstance(tests, dict) or set(tests) != _TEST_FIELDS:
        raise PortabilityContractError("The portability test groups are not exact.")
    pure_tests = _path_list(tests.get("pure"), field="tests.pure")
    adapter_tests = _path_list(
        tests.get("platform_adapter"), field="tests.platform_adapter"
    )
    if set(pure_tests) & set(adapter_tests):
        raise PortabilityContractError("The portability test groups overlap.")
    for relative in (*pure_tests, *adapter_tests):
        if not PurePosixPath(relative).name.startswith("test_"):
            raise PortabilityContractError(
                "The portability contract can run only explicit test_*.py files."
            )

    root = Path(repo_root)
    source_directory = root / source_root
    if not source_directory.is_dir():
        raise PortabilityContractError("The portability source root does not exist.")
    _require_files(root, portable_modules, field="portable_modules")
    _require_files(root, adapters, field="macos_import_adapters")
    _require_files(root, [*pure_tests, *adapter_tests], field="tests")

    return {
        **raw,
        "source_root": source_root,
        "portable_modules": portable_modules,
        "macos_import_adapters": adapters,
        "macos_framework_import_roots": sorted(import_roots),
        "tests": {"pure": pure_tests, "platform_adapter": adapter_tests},
    }


def _import_name(node: ast.AST) -> list[tuple[str, int]]:
    imports: list[tuple[str, int]] = []
    if isinstance(node, ast.Import):
        imports.extend((alias.name, node.lineno) for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        imports.append((node.module, node.lineno))
    elif isinstance(node, ast.Call):
        function = node.func
        dynamic = (
            isinstance(function, ast.Name) and function.id in {"__import__", "import_module"}
        ) or (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "importlib"
            and function.attr == "import_module"
        )
        if dynamic and node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                imports.append((value, node.lineno))
    return imports


def _production_python_files(source_root: Path) -> list[Path]:
    result = []
    for path in source_root.rglob("*.py"):
        relative = path.relative_to(source_root)
        if "vibecad_tests" in relative.parts or relative.name.startswith("Test"):
            continue
        result.append(path)
    return sorted(result)


def scan_macos_framework_imports(
    repo_root: str | Path, contract: Mapping[str, Any]
) -> dict[str, Any]:
    """Reject macOS framework imports outside the named adapter files."""

    root = Path(repo_root).resolve()
    source_root = root / str(contract["source_root"])
    allowed = set(contract["macos_import_adapters"])
    forbidden = set(contract["macos_framework_import_roots"])
    violations: list[dict[str, Any]] = []
    approved_imports: list[dict[str, Any]] = []
    files = _production_python_files(source_root)
    for path in files:
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, SyntaxError) as exc:
            violations.append(
                {"kind": "source_parse_error", "path": relative, "detail": str(exc)}
            )
            continue
        for node in ast.walk(tree):
            for imported, line in _import_name(node):
                import_root = imported.split(".", 1)[0]
                if import_root not in forbidden:
                    continue
                finding = {
                    "import": imported,
                    "line": line,
                    "path": relative,
                }
                if relative in allowed:
                    approved_imports.append(finding)
                else:
                    violations.append(
                        {"kind": "macos_framework_import_outside_adapter", **finding}
                    )
    return {
        "files_scanned": len(files),
        "approved_adapter_imports": sorted(
            approved_imports, key=lambda item: (item["path"], item["line"], item["import"])
        ),
        "violations": sorted(
            violations,
            key=lambda item: (
                str(item.get("path") or ""),
                int(item.get("line") or 0),
                str(item.get("kind") or ""),
            ),
        ),
    }


def compile_portable_modules(
    repo_root: str | Path, contract: Mapping[str, Any]
) -> list[str]:
    """Compile each named shared module without writing into the source tree."""

    root = Path(repo_root)
    compiled: list[str] = []
    with tempfile.TemporaryDirectory(prefix="vibecad-portability-") as temporary:
        output_root = Path(temporary)
        for index, relative in enumerate(contract["portable_modules"]):
            output = output_root / f"{index:03d}-{Path(relative).stem}.pyc"
            try:
                py_compile.compile(
                    str(root / relative), cfile=str(output), doraise=True
                )
            except py_compile.PyCompileError as exc:
                raise PortabilityContractError(
                    f"Portable module compilation failed for {relative}: {exc.msg}"
                ) from exc
            compiled.append(relative)
    return compiled


def run_contract_tests(
    repo_root: str | Path,
    contract: Mapping[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> dict[str, Any]:
    """Run only the exact pure and platform-adapter test files in the contract."""

    tests = [*contract["tests"]["pure"], *contract["tests"]["platform_adapter"]]
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--noconftest",
        *tests,
    ]
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    module_root = str(Path(repo_root) / str(contract["source_root"]))
    existing_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        module_root + os.pathsep + existing_python_path
        if existing_python_path
        else module_root
    )
    completed = runner(command, cwd=str(Path(repo_root)), env=environment, check=False)
    return {
        "command": command,
        "exit_code": int(completed.returncode),
        "files": tests,
        "file_count": len(tests),
    }


def _git_source_sha(repo_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PortabilityContractError("The checked source SHA could not be read.") from exc
    source_sha = completed.stdout.strip().lower()
    if len(source_sha) != 40 or any(character not in "0123456789abcdef" for character in source_sha):
        raise PortabilityContractError("The checked source SHA is invalid.")
    return source_sha


def _git_worktree_changes(repo_root: Path) -> list[str]:
    """Return every tracked or untracked worktree change."""

    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PortabilityContractError(
            "The checked source worktree state could not be read."
        ) from exc
    return [line for line in completed.stdout.splitlines() if line]


def verify_repository(
    *,
    repo_root: str | Path,
    contract_path: str | Path,
    compile_modules: bool = False,
    enforce_python: bool = False,
    expected_source_sha: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify source structure and return the normalized contract and report."""

    root = Path(repo_root).resolve()
    contract = load_contract(contract_path, repo_root=root)
    current_runtime = {
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
    }
    if enforce_python and current_runtime != contract["python"]:
        raise PortabilityContractError(
            "The portability check must use "
            f"{contract['python']['implementation']} {contract['python']['version']}; "
            f"it is using {current_runtime['implementation']} {current_runtime['version']}."
        )
    source_sha = _git_source_sha(root)
    expected = str(expected_source_sha or "").strip().lower()
    if expected and (
        len(expected) != 40
        or any(character not in "0123456789abcdef" for character in expected)
    ):
        raise PortabilityContractError("The expected source SHA is invalid.")
    if expected and source_sha != expected:
        raise PortabilityContractError(
            f"The checked source SHA {source_sha} does not match {expected}."
        )
    worktree_changes = _git_worktree_changes(root)
    if expected and worktree_changes:
        raise PortabilityContractError(
            "The exact-source portability check requires a clean worktree; "
            f"it found {len(worktree_changes)} change(s)."
        )
    scan = scan_macos_framework_imports(root, contract)
    if scan["violations"]:
        first = scan["violations"][0]
        raise PortabilityContractError(
            "The portability source scan failed at "
            f"{first.get('path')}:{first.get('line', 0)} ({first.get('kind')})."
        )
    compiled = compile_portable_modules(root, contract) if compile_modules else []
    report = {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "status": "pass",
        "source_sha": source_sha,
        "worktree_clean": not worktree_changes,
        "worktree_change_count": len(worktree_changes),
        "contract_sha256": hashlib.sha256(_canonical_json(contract)).hexdigest(),
        "python": current_runtime,
        "required_python": dict(contract["python"]),
        "source_scan": scan,
        "compiled_modules": compiled,
        "tests": {
            "status": "not_run",
            "pure": list(contract["tests"]["pure"]),
            "platform_adapter": list(contract["tests"]["platform_adapter"]),
        },
        "scope": "portable Python contracts only; native FreeCAD support is not asserted",
    }
    return contract, report


def _write_report(path: str | Path, report: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--contract", required=True)
    parser.add_argument("--source-sha")
    parser.add_argument("--compile", action="store_true")
    parser.add_argument("--enforce-python", action="store_true")
    parser.add_argument("--run-tests", action="store_true")
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)

    try:
        contract, report = verify_repository(
            repo_root=args.repo_root,
            contract_path=args.contract,
            compile_modules=args.compile,
            enforce_python=args.enforce_python,
            expected_source_sha=args.source_sha,
        )
        if args.run_tests:
            test_result = run_contract_tests(args.repo_root, contract)
            report["tests"] = {**report["tests"], **test_result}
            report["tests"]["status"] = (
                "pass" if test_result["exit_code"] == 0 else "fail"
            )
            if test_result["exit_code"] != 0:
                report["status"] = "fail"
        _write_report(args.report, report)
        print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
        return 0 if report["status"] == "pass" else 1
    except PortabilityContractError as exc:
        failure = {
            "schema": REPORT_SCHEMA,
            "version": REPORT_VERSION,
            "status": "fail",
            "error": str(exc),
        }
        _write_report(args.report, failure)
        print(json.dumps(failure, ensure_ascii=True, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
