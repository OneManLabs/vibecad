#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Verify that a packaged VibeCAD module matches the checked-out source."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys


REQUIRED_MODULES = (
    "VibeCADAcceptance.py",
    "VibeCADSession.py",
    "VibeCADGui.py",
    "VibeCADProject.py",
    "VibeCADAddonManagerPolicy.py",
)

_EXCLUDED_DIRECTORIES = {
    "__pycache__",
    "build123d_runtime",
    "codex_runtime",
    "generated",
    "openscad_runtime",
    "tests",
    "vibecad_tests",
}


def _is_packaged_python(relative_path: Path) -> bool:
    if relative_path.suffix != ".py":
        return False
    if any(part in _EXCLUDED_DIRECTORIES for part in relative_path.parts[:-1]):
        return False
    name = relative_path.name
    return not (
        name.startswith("Test")
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_manifest(root: Path) -> dict[str, str]:
    """Return the checked Python files and their SHA-256 values."""

    resolved = root.resolve()
    if not resolved.is_dir():
        raise ValueError(f"VibeCAD module directory does not exist: {resolved}")
    manifest: dict[str, str] = {}
    for path in sorted(resolved.rglob("*.py")):
        relative = path.relative_to(resolved)
        if _is_packaged_python(relative):
            manifest[relative.as_posix()] = _sha256(path)
    if not manifest:
        raise ValueError(f"No packaged VibeCAD Python files were found in: {resolved}")
    return manifest


def verify_source_identity(source: Path, installed: Path) -> dict[str, object]:
    """Compare source and installed Python files by relative path and SHA-256."""

    source_files = source_manifest(source)
    installed_files = source_manifest(installed)
    missing_required = [name for name in REQUIRED_MODULES if name not in source_files]
    if missing_required:
        raise ValueError(
            "Required VibeCAD source modules are missing: "
            + ", ".join(missing_required)
        )
    missing = sorted(set(source_files) - set(installed_files))
    unexpected = sorted(set(installed_files) - set(source_files))
    mismatched = sorted(
        path
        for path in set(source_files) & set(installed_files)
        if source_files[path] != installed_files[path]
    )
    if missing or unexpected or mismatched:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        if mismatched:
            details.append("SHA-256 mismatch: " + ", ".join(mismatched))
        raise ValueError("Installed VibeCAD source identity failed; " + "; ".join(details))
    return {
        "ok": True,
        "file_count": len(source_files),
        "required_modules": list(REQUIRED_MODULES),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--installed", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_source_identity(args.source, args.installed)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        "Installed VibeCAD source identity passed "
        f"for {result['file_count']} Python files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
