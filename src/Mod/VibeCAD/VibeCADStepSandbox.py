# SPDX-License-Identifier: LGPL-2.1-or-later
"""Versioned macOS sandbox plan for the isolated STEP validator."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping, Sequence


STEP_WORKER_SANDBOX_SCHEMA = "vibecad-step-worker-sandbox-v1"
STEP_WORKER_SANDBOX_VERSION = 1
MACOS_SANDBOX_EXECUTABLE = Path("/usr/bin/sandbox-exec")

_LOADER_ENVIRONMENT_KEYS = frozenset(
    {
        "DYLD_FRAMEWORK_PATH",
        "DYLD_FALLBACK_FRAMEWORK_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "DYLD_IMAGE_SUFFIX",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "PYTHONHOME",
        "PYTHONPATH",
    }
)

_MACOS_SYSTEM_READ_ROOTS = (
    Path("/System"),
    Path("/usr/lib"),
    Path("/usr/share"),
    Path("/Library/Apple/System"),
    Path("/Library/Fonts"),
    Path("/private/etc"),
    Path("/private/var/db/dyld"),
    Path("/private/var/db/timezone"),
)

_MACOS_SYSTEM_READ_FILES = (
    Path("/dev/null"),
    Path("/dev/random"),
    Path("/dev/urandom"),
)


class StepWorkerSandboxUnavailable(RuntimeError):
    """Report a typed and path-free STEP sandbox limitation."""

    code = "STEP_VALIDATOR_SANDBOX_UNAVAILABLE"

    def __init__(self, reason: str):
        self.reason = str(reason)
        super().__init__("The operating-system STEP validator sandbox is unavailable.")

    def provider_evidence(self) -> dict[str, Any]:
        """Return stable evidence without a local path."""

        return {
            "schema": STEP_WORKER_SANDBOX_SCHEMA,
            "version": STEP_WORKER_SANDBOX_VERSION,
            "available": False,
            "reason": self.reason,
            "network": "denied_when_available",
            "write_scope": "validator_staging_directory_when_available",
        }


@dataclass(frozen=True)
class StepWorkerSandboxPlan:
    """Own one complete and versioned sandbox launch plan."""

    command: tuple[str, ...]
    environment: Mapping[str, str]
    profile: str
    profile_sha256: str

    def provider_evidence(self) -> dict[str, Any]:
        """Return stable evidence without local command or path values."""

        return {
            "schema": STEP_WORKER_SANDBOX_SCHEMA,
            "version": STEP_WORKER_SANDBOX_VERSION,
            "available": True,
            "enforced": True,
            "network": "denied",
            "write_scope": "validator_staging_directory_only",
            "profile_sha256": self.profile_sha256,
        }


def step_worker_sandbox_status(
    *,
    platform_name: str | None = None,
    sandbox_executable: str | Path = MACOS_SANDBOX_EXECUTABLE,
) -> dict[str, Any]:
    """Return the typed STEP sandbox availability for this platform."""

    platform_value = str(platform_name if platform_name is not None else sys.platform)
    if platform_value != "darwin":
        return StepWorkerSandboxUnavailable("unsupported_platform").provider_evidence()
    launcher = Path(sandbox_executable)
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        return StepWorkerSandboxUnavailable(
            "sandbox_launcher_unavailable"
        ).provider_evidence()
    return {
        "schema": STEP_WORKER_SANDBOX_SCHEMA,
        "version": STEP_WORKER_SANDBOX_VERSION,
        "available": True,
        "enforced": True,
        "network": "denied",
        "write_scope": "validator_staging_directory_only",
    }


def _resolved_directory(path: str | Path, *, field: str) -> Path:
    candidate = Path(path)
    try:
        metadata = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"The {field} is missing.") from exc
    if stat.S_ISLNK(metadata.st_mode) or not resolved.is_dir():
        raise ValueError(f"The {field} is unsafe.")
    return resolved


def _resolved_executable(path: str | Path) -> Path:
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("The STEP validator executable is missing.") from exc
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError("The STEP validator executable is unavailable.")
    return resolved


def _runtime_root(executable: Path) -> Path:
    parent = executable.parent
    if parent.name == "MacOS" and parent.parent.name == "Contents":
        return parent.parent
    if parent.name in {"bin", "MacOS"}:
        return parent.parent
    return parent


def _profile_string(value: Path) -> str:
    """Return one safe Seatbelt string literal."""

    text = str(value)
    if any(ord(char) < 0x20 for char in text):
        raise ValueError("A STEP sandbox path contains an unsupported character.")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unique_existing_paths(paths: Sequence[Path]) -> tuple[Path, ...]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        encoded = str(resolved)
        if encoded in seen:
            continue
        seen.add(encoded)
        result.append(resolved)
    return tuple(result)


def _seatbelt_profile(
    *, staging: Path, executable: Path, read_roots: Sequence[Path]
) -> str:
    root_rules = "\n".join(
        f"        (subpath {_profile_string(path)})" for path in read_roots
    )
    file_rules = "\n".join(
        f"        (literal {_profile_string(path)})"
        for path in _unique_existing_paths(_MACOS_SYSTEM_READ_FILES)
    )
    ancestor_rules = "\n".join(
        f"        (path-ancestors {_profile_string(path)})"
        for path in _unique_existing_paths(
            (*read_roots, executable, staging)
        )
    )
    executable_rule = _profile_string(executable)
    staging_rule = _profile_string(staging)
    return (
        "(version 1)\n"
        "(deny default)\n"
        "(import \"system.sb\")\n"
        "(deny network*)\n"
        "(deny file-write*\n"
        f"        (require-not (subpath {staging_rule})))\n"
        "(allow process-exec\n"
        f"        (literal {executable_rule}))\n"
        "(allow process-fork)\n"
        "(allow signal process-info-dirtycontrol process-info-pidinfo\n"
        "        (target self))\n"
        "(allow sysctl-read)\n"
        "(allow file-read-metadata file-test-existence\n"
        f"{ancestor_rules})\n"
        "(allow file-read*\n"
        f"        (literal {executable_rule})\n"
        f"{file_rules}\n"
        f"{root_rules}\n"
        f"        (subpath {staging_rule}))\n"
        "(allow file-write*\n"
        f"        (subpath {staging_rule}))\n"
    )


def prepare_step_worker_sandbox(
    command: Sequence[str],
    *,
    staging: str | Path,
    environment: Mapping[str, str],
    module_roots: Sequence[str | Path] = (),
    runtime_roots: Sequence[str | Path] = (),
    platform_name: str | None = None,
    sandbox_executable: str | Path = MACOS_SANDBOX_EXECUTABLE,
) -> StepWorkerSandboxPlan:
    """Wrap one STEP worker in a fail-closed macOS Seatbelt profile."""

    status = step_worker_sandbox_status(
        platform_name=platform_name,
        sandbox_executable=sandbox_executable,
    )
    if status.get("available") is not True:
        raise StepWorkerSandboxUnavailable(str(status.get("reason") or "unavailable"))
    if not command or not isinstance(command[0], str) or not command[0]:
        raise ValueError("The STEP validator command is empty.")

    clean_staging = _resolved_directory(
        staging, field="STEP validator staging directory"
    )
    if os.name != "nt" and clean_staging.stat().st_mode & 0o077:
        raise ValueError("The STEP validator staging directory is not private.")
    executable = _resolved_executable(command[0])
    launcher = _resolved_executable(sandbox_executable)

    requested_roots = [
        _runtime_root(executable),
        Path(sys.prefix),
        Path(sys.base_prefix),
        *(Path(path) for path in module_roots),
        *(Path(path) for path in runtime_roots),
        *_MACOS_SYSTEM_READ_ROOTS,
    ]
    read_roots = _unique_existing_paths(requested_roots)
    profile = _seatbelt_profile(
        staging=clean_staging,
        executable=executable,
        read_roots=read_roots,
    )
    clean_environment = {
        str(key): str(value)
        for key, value in environment.items()
        if str(key).upper() not in _LOADER_ENVIRONMENT_KEYS
    }
    wrapped = (
        str(launcher),
        "-p",
        profile,
        str(executable),
        *(str(item) for item in command[1:]),
    )
    return StepWorkerSandboxPlan(
        command=wrapped,
        environment=clean_environment,
        profile=profile,
        profile_sha256=hashlib.sha256(profile.encode("utf-8")).hexdigest(),
    )


__all__ = [
    "MACOS_SANDBOX_EXECUTABLE",
    "STEP_WORKER_SANDBOX_SCHEMA",
    "STEP_WORKER_SANDBOX_VERSION",
    "StepWorkerSandboxPlan",
    "StepWorkerSandboxUnavailable",
    "prepare_step_worker_sandbox",
    "step_worker_sandbox_status",
]
