#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Attest the exact Python runtime that executes live provider calls."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
from typing import Any, Callable, Iterable, Mapping

try:
    from tools.vibecad_benchmark_evidence_io import (
        EvidenceIOError,
        load_bounded_json,
        open_bounded_regular_file,
    )
    from tools.vibecad_secure_process import (
        create_private_run_directory,
        minimal_child_environment,
        run_bounded_process,
        validate_finite_timeout,
    )
except ModuleNotFoundError:  # Direct execution from the tools directory.
    tools_directory = Path(__file__).resolve(strict=True).parent
    tools_text = os.fspath(tools_directory)
    if tools_text not in sys.path:
        sys.path.insert(0, tools_text)
    from vibecad_benchmark_evidence_io import (  # type: ignore[no-redef]
        EvidenceIOError,
        load_bounded_json,
        open_bounded_regular_file,
    )
    from vibecad_secure_process import (  # type: ignore[no-redef]
        create_private_run_directory,
        minimal_child_environment,
        run_bounded_process,
        validate_finite_timeout,
    )


DISCOVERY_SCHEMA = "vibecad-provider-runtime-discovery-v1"
DISCOVERY_VERSION = 1
ATTESTATION_SCHEMA = "vibecad-provider-runtime-attestation-v1"
ATTESTATION_VERSION = 1

MAX_DISCOVERY_BYTES = 2 * 1024 * 1024
MAX_DISCOVERY_FILES = 8192
MAX_DISTRIBUTIONS = 64
MAX_FILES_PER_DISTRIBUTION = 4096
MAX_NATIVE_LIBRARIES = 256
MAX_LOADED_PYTHON_FILES = 2048
MAX_COMPONENT_BYTES = 512 * 1024 * 1024
MAX_DISTRIBUTION_BYTES = 512 * 1024 * 1024
MAX_TOTAL_DISTRIBUTION_BYTES = 1024 * 1024 * 1024
MAX_TIMEOUT_SECONDS = 60.0
REQUIRED_PROVIDER_MODULES = ("httpcore", "httpx", "openai")
REQUIRED_DISTRIBUTIONS = ("httpcore", "httpx", "openai")
SYSTEM_LIBRARY_ROOTS = ("/System/Library/", "/usr/lib/")


class ProviderRuntimeAttestationError(RuntimeError):
    """Raised when the provider runtime cannot be bound exactly."""


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_distribution_name(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", "-").replace(".", "-")
    while "--" in text:
        text = text.replace("--", "-")
    if (
        not text
        or text.startswith("-")
        or text.endswith("-")
        or any(not (character.isascii() and (character.isalnum() or character == "-"))
               for character in text)
    ):
        raise ProviderRuntimeAttestationError(
            "A loaded provider distribution has an invalid name."
        )
    return text


def _lexical_absolute(path: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _canonical_root(root: Path) -> Path:
    lexical = _lexical_absolute(root)
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise ProviderRuntimeAttestationError(
            "The provider runtime source root is missing."
        ) from exc
    if lexical != resolved or not resolved.is_dir():
        raise ProviderRuntimeAttestationError(
            "The provider runtime source root is not canonical."
        )
    return resolved


def _relative_canonical_path(
    root: Path,
    raw_path: object,
    *,
    label: str,
    require_file: bool,
) -> tuple[Path, str]:
    text = str(raw_path or "")
    if not text or "\x00" in text:
        raise ProviderRuntimeAttestationError(f"The {label} path is empty or unsafe.")
    lexical = _lexical_absolute(text)
    if text != os.fspath(lexical):
        raise ProviderRuntimeAttestationError(f"The {label} path is not canonical.")
    try:
        resolved = lexical.resolve(strict=require_file)
    except OSError as exc:
        raise ProviderRuntimeAttestationError(
            f"The {label} path is missing."
        ) from exc
    if resolved != lexical:
        raise ProviderRuntimeAttestationError(
            f"The {label} path contains a symbolic link."
        )
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise ProviderRuntimeAttestationError(
            f"The {label} path is outside the repository runtime."
        ) from exc
    if (
        not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.is_absolute()
    ):
        raise ProviderRuntimeAttestationError(f"The {label} path is invalid.")
    if require_file and not lexical.is_file():
        raise ProviderRuntimeAttestationError(f"The {label} path is not a file.")
    return lexical, relative.as_posix()


def _component(
    root: Path,
    raw_path: object,
    *,
    label: str,
) -> dict[str, Any]:
    path, relative = _relative_canonical_path(
        root, raw_path, label=label, require_file=True
    )
    try:
        with open_bounded_regular_file(
            path,
            max_bytes=MAX_COMPONENT_BYTES,
            label=label,
            retain_data=False,
        ) as snapshot:
            snapshot.verify_unchanged()
            return {
                "path": relative,
                "size": snapshot.size,
                "sha256": snapshot.sha256,
            }
    except (EvidenceIOError, OSError) as exc:
        raise ProviderRuntimeAttestationError(
            f"The {label} file is missing, linked, too large, or unstable."
        ) from exc


def _bounded_string_list(
    value: object,
    *,
    label: str,
    maximum: int,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ProviderRuntimeAttestationError(f"The {label} list is invalid.")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or "\x00" in item:
            raise ProviderRuntimeAttestationError(
                f"The {label} list has an invalid path."
            )
        result.append(item)
    if len(result) != len(set(result)):
        raise ProviderRuntimeAttestationError(
            f"The {label} list has a duplicate path."
        )
    return result


def _distribution_manifest(
    root: Path,
    value: Mapping[str, Any],
    *,
    global_paths: set[str],
) -> dict[str, Any]:
    if set(value) != {"name", "version", "root", "declared_files"}:
        raise ProviderRuntimeAttestationError(
            "A loaded provider distribution has an invalid discovery contract."
        )
    name = _normalize_distribution_name(value.get("name"))
    version = str(value.get("version") or "").strip()
    if (
        not version
        or len(version.encode("utf-8")) > 256
        or any(ord(character) < 32 for character in version)
    ):
        raise ProviderRuntimeAttestationError(
            f"The {name} distribution has an invalid version."
        )
    distribution_root, relative_root = _relative_canonical_path(
        root,
        value.get("root"),
        label=f"{name} distribution root",
        require_file=False,
    )
    if not distribution_root.is_dir():
        raise ProviderRuntimeAttestationError(
            f"The {name} distribution root is not a directory."
        )
    declared = _bounded_string_list(
        value.get("declared_files"),
        label=f"{name} distribution file",
        maximum=MAX_FILES_PER_DISTRIBUTION,
    )
    existing_records: list[dict[str, Any]] = []
    missing_paths: list[str] = []
    total_bytes = 0
    local_paths: set[str] = set()
    for raw_path in declared:
        lexical = _lexical_absolute(raw_path)
        if raw_path != os.fspath(lexical):
            raise ProviderRuntimeAttestationError(
                f"The {name} distribution declared a noncanonical path."
            )
        try:
            relative = lexical.relative_to(root).as_posix()
        except ValueError as exc:
            raise ProviderRuntimeAttestationError(
                f"The {name} distribution declared a path outside the runtime."
            ) from exc
        if relative in local_paths:
            raise ProviderRuntimeAttestationError(
                f"The {name} distribution declared a duplicate path."
            )
        local_paths.add(relative)
        try:
            details = lexical.lstat()
        except FileNotFoundError:
            missing_paths.append(relative)
            continue
        if not lexical.is_file() or lexical.is_symlink():
            raise ProviderRuntimeAttestationError(
                f"The {name} distribution contains an unsafe file."
            )
        component = _component(
            root,
            lexical,
            label=f"{name} distribution file",
        )
        path = str(component["path"])
        if path in global_paths:
            raise ProviderRuntimeAttestationError(
                "Loaded provider distributions overlap one installed file."
            )
        global_paths.add(path)
        total_bytes += int(component["size"])
        if total_bytes > MAX_DISTRIBUTION_BYTES:
            raise ProviderRuntimeAttestationError(
                f"The {name} distribution exceeds the byte limit."
            )
        existing_records.append(component)
    existing_records.sort(key=lambda item: str(item["path"]))
    missing_paths.sort()
    return {
        "name": name,
        "version": version,
        "root": relative_root,
        "declared_file_count": len(declared),
        "file_count": len(existing_records),
        "missing_file_count": len(missing_paths),
        "total_bytes": total_bytes,
        "manifest_sha256": _canonical_json_sha256(existing_records),
        "missing_manifest_sha256": _canonical_json_sha256(missing_paths),
        "files": existing_records,
    }


def build_provider_runtime_identity(
    root: Path,
    discovery: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate discovery data and hash all referenced runtime bytes."""

    root = _canonical_root(root)
    if not isinstance(discovery, Mapping):
        raise ProviderRuntimeAttestationError(
            "The provider runtime discovery result is not an object."
        )
    expected_fields = {
        "schema",
        "version",
        "nonce",
        "platform",
        "python",
        "provider_modules",
        "loaded_python_files",
        "distributions",
        "native_libraries",
    }
    if set(discovery) != expected_fields:
        raise ProviderRuntimeAttestationError(
            "The provider runtime discovery result has missing or unknown fields."
        )
    if (
        discovery.get("schema") != DISCOVERY_SCHEMA
        or discovery.get("version") != DISCOVERY_VERSION
    ):
        raise ProviderRuntimeAttestationError(
            "The provider runtime discovery contract is invalid."
        )
    if discovery.get("platform") != "darwin":
        raise ProviderRuntimeAttestationError(
            "The live provider runtime attestation requires macOS."
        )
    nonce = str(discovery.get("nonce") or "")
    if len(nonce) != 64 or any(character not in "0123456789abcdef" for character in nonce):
        raise ProviderRuntimeAttestationError(
            "The provider runtime discovery nonce is invalid."
        )

    python = discovery.get("python")
    if not isinstance(python, Mapping) or set(python) != {
        "executable",
        "version",
        "implementation",
        "cache_tag",
        "prefix",
        "base_prefix",
    }:
        raise ProviderRuntimeAttestationError(
            "The selected provider Python identity is invalid."
        )
    executable = _component(
        root,
        python.get("executable"),
        label="selected provider Python",
    )
    if int(executable["size"]) <= 0:
        raise ProviderRuntimeAttestationError(
            "The selected provider Python is empty."
        )
    version = python.get("version")
    if (
        not isinstance(version, list)
        or len(version) != 5
        or any(
            isinstance(item, bool) or not isinstance(item, (int, str))
            for item in version
        )
        or not all(
            isinstance(version[index], int)
            and not isinstance(version[index], bool)
            and version[index] >= 0
            for index in (0, 1, 2, 4)
        )
        or version[3] not in {"alpha", "beta", "candidate", "final"}
    ):
        raise ProviderRuntimeAttestationError(
            "The selected provider Python version is invalid."
        )
    implementation = str(python.get("implementation") or "")
    cache_tag = str(python.get("cache_tag") or "")
    if implementation != "cpython" or not cache_tag.startswith("cpython-"):
        raise ProviderRuntimeAttestationError(
            "The selected provider Python implementation is invalid."
        )
    _, prefix = _relative_canonical_path(
        root,
        python.get("prefix"),
        label="selected provider Python prefix",
        require_file=False,
    )
    prefix_path = root / prefix
    if not prefix_path.is_dir():
        raise ProviderRuntimeAttestationError(
            "The selected provider Python prefix is not a directory."
        )
    _, base_prefix = _relative_canonical_path(
        root,
        python.get("base_prefix"),
        label="selected provider Python base prefix",
        require_file=False,
    )
    if not (root / base_prefix).is_dir():
        raise ProviderRuntimeAttestationError(
            "The selected provider Python base prefix is not a directory."
        )
    python_identity = {
        **executable,
        "version": list(version),
        "implementation": implementation,
        "cache_tag": cache_tag,
        "prefix": prefix,
        "base_prefix": base_prefix,
    }

    modules = discovery.get("provider_modules")
    if not isinstance(modules, list) or len(modules) != len(REQUIRED_PROVIDER_MODULES):
        raise ProviderRuntimeAttestationError(
            "The provider runtime has no exact required-module list."
        )
    provider_modules: list[dict[str, Any]] = []
    module_names: list[str] = []
    for item in modules:
        if not isinstance(item, Mapping) or set(item) != {"name", "path"}:
            raise ProviderRuntimeAttestationError(
                "A provider module has an invalid discovery contract."
            )
        name = str(item.get("name") or "")
        module_names.append(name)
        component = _component(
            root,
            item.get("path"),
            label=f"{name or 'provider'} module",
        )
        if int(component["size"]) <= 0:
            raise ProviderRuntimeAttestationError(
                f"The loaded {name or 'provider'} module is empty."
            )
        provider_modules.append({"name": name, **component})
    if tuple(module_names) != REQUIRED_PROVIDER_MODULES:
        raise ProviderRuntimeAttestationError(
            "The required provider modules are missing or reordered."
        )

    loaded_python_files = [
        _component(root, item, label="loaded Python module")
        for item in _bounded_string_list(
            discovery.get("loaded_python_files"),
            label="loaded Python module",
            maximum=MAX_LOADED_PYTHON_FILES,
        )
    ]
    loaded_python_files.sort(key=lambda item: str(item["path"]))
    if not loaded_python_files:
        raise ProviderRuntimeAttestationError(
            "The provider runtime has no loaded Python module file list."
        )

    distributions = discovery.get("distributions")
    if not isinstance(distributions, list) or not 1 <= len(distributions) <= MAX_DISTRIBUTIONS:
        raise ProviderRuntimeAttestationError(
            "The loaded provider distribution list is invalid."
        )
    distribution_identity: list[dict[str, Any]] = []
    global_distribution_paths: set[str] = set()
    total_distribution_bytes = 0
    total_distribution_files = 0
    for item in distributions:
        if not isinstance(item, Mapping):
            raise ProviderRuntimeAttestationError(
                "A loaded provider distribution is not an object."
            )
        manifest = _distribution_manifest(
            root, item, global_paths=global_distribution_paths
        )
        distribution_identity.append(manifest)
        total_distribution_bytes += int(manifest["total_bytes"])
        total_distribution_files += int(manifest["file_count"])
        if (
            total_distribution_bytes > MAX_TOTAL_DISTRIBUTION_BYTES
            or total_distribution_files > MAX_DISCOVERY_FILES
        ):
            raise ProviderRuntimeAttestationError(
                "The loaded provider distributions exceed the total limits."
            )
    distribution_identity.sort(key=lambda item: str(item["name"]))
    names = [str(item["name"]) for item in distribution_identity]
    if len(names) != len(set(names)):
        raise ProviderRuntimeAttestationError(
            "The loaded provider distribution list has a duplicate name."
        )
    if not set(REQUIRED_DISTRIBUTIONS).issubset(names):
        raise ProviderRuntimeAttestationError(
            "The OpenAI SDK and HTTP runtime distributions are incomplete."
        )

    raw_libraries = _bounded_string_list(
        discovery.get("native_libraries"),
        label="loaded non-system native library",
        maximum=MAX_NATIVE_LIBRARIES,
    )
    native_libraries = [
        _component(
            root,
            item,
            label="loaded non-system native library",
        )
        for item in raw_libraries
    ]
    if any(int(item["size"]) <= 0 for item in native_libraries):
        raise ProviderRuntimeAttestationError(
            "A loaded non-system native library is empty."
        )
    native_libraries.sort(key=lambda item: str(item["path"]))
    native_paths = [str(item["path"]) for item in native_libraries]
    if len(native_paths) != len(set(native_paths)):
        raise ProviderRuntimeAttestationError(
            "The loaded non-system native library list has a duplicate path."
        )

    components_by_path: dict[str, dict[str, Any]] = {}
    for component in (
        executable,
        *provider_modules,
        *loaded_python_files,
        *native_libraries,
        *(
            file_record
            for distribution in distribution_identity
            for file_record in distribution["files"]
        ),
    ):
        path = str(component["path"])
        record = {
            "path": path,
            "size": int(component["size"]),
            "sha256": str(component["sha256"]),
        }
        previous = components_by_path.get(path)
        if previous is not None and previous != record:
            raise ProviderRuntimeAttestationError(
                "The provider runtime reports conflicting component identities."
            )
        components_by_path[path] = record

    public_distributions = [
        {key: value for key, value in distribution.items() if key != "files"}
        for distribution in distribution_identity
    ]
    return {
        "schema": ATTESTATION_SCHEMA,
        "version": ATTESTATION_VERSION,
        "platform": "darwin",
        "python": python_identity,
        "provider_modules": provider_modules,
        "loaded_python_files": loaded_python_files,
        "distributions": public_distributions,
        "native_libraries": native_libraries,
        "components": [
            components_by_path[path] for path in sorted(components_by_path)
        ],
    }


def _safe_child_environment(explicit: Mapping[str, object]) -> dict[str, str]:
    safe_names = {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "TMPDIR",
        "TZ",
        "USER",
        "__CF_USER_TEXT_ENCODING",
    }
    result = {
        name: str(os.environ[name])
        for name in safe_names
        if name in os.environ and "\x00" not in str(os.environ[name])
    }
    result["PATH"] = os.defpath
    for name, value in explicit.items():
        text_name = str(name)
        text_value = str(value)
        if (
            not text_name.startswith("VIBECAD_")
            or "=" in text_name
            or "\x00" in text_name
            or "\x00" in text_value
        ):
            raise ProviderRuntimeAttestationError(
                "The provider attestation child environment is invalid."
            )
        result[text_name] = text_value
    return result


def _run_selected_python(
    executable: Path,
    script: Path,
    *,
    root: Path,
    output: Path,
    nonce: str,
    timeout_seconds: float,
) -> None:
    process = subprocess.Popen(
        [
            os.fspath(executable),
            "-I",
            "-S",
            os.fspath(script),
            "--child",
        ],
        env=_safe_child_environment(
            {
                "VIBECAD_PROVIDER_RUNTIME_ROOT": root,
                "VIBECAD_PROVIDER_RUNTIME_OUTPUT": output,
                "VIBECAD_PROVIDER_RUNTIME_NONCE": nonce,
            }
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
        raise ProviderRuntimeAttestationError(
            "The selected provider Python attestation timed out."
        ) from exc
    if return_code != 0:
        raise ProviderRuntimeAttestationError(
            "The selected provider Python attestation failed."
        )


def _freecad_stage() -> int:
    if sys.platform != "darwin":
        raise ProviderRuntimeAttestationError(
            "The live provider runtime attestation requires macOS."
        )
    root = _canonical_root(
        Path(str(os.environ.get("VIBECAD_PROVIDER_RUNTIME_ROOT") or ""))
    )
    output = _lexical_absolute(
        str(os.environ.get("VIBECAD_PROVIDER_RUNTIME_OUTPUT") or "")
    )
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise ProviderRuntimeAttestationError(
            "The provider runtime output is outside the repository runtime."
        ) from exc
    nonce = str(os.environ.get("VIBECAD_PROVIDER_RUNTIME_NONCE") or "")
    if len(nonce) != 64 or any(character not in "0123456789abcdef" for character in nonce):
        raise ProviderRuntimeAttestationError(
            "The provider runtime nonce is invalid."
        )
    installed = root / "build" / "release" / "Mod" / "VibeCAD"
    installed_text = os.fspath(installed)
    if installed_text not in sys.path:
        sys.path.insert(0, installed_text)
    import VibeCADProvider

    provider_file = Path(str(VibeCADProvider.__file__ or "")).resolve(strict=True)
    expected_provider_file = (installed / "VibeCADProvider.py").resolve(strict=True)
    if provider_file != expected_provider_file:
        raise ProviderRuntimeAttestationError(
            "FreeCAD loaded VibeCADProvider from an unexpected path."
        )
    selected = VibeCADProvider._provider_spawn_python_executable(
        prefer_windowless=False
    )
    if not selected:
        raise ProviderRuntimeAttestationError(
            "VibeCADProvider did not select a provider Python executable."
        )
    selected_lexical = _lexical_absolute(selected)
    try:
        selected_resolved = selected_lexical.resolve(strict=True)
        selected_resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ProviderRuntimeAttestationError(
            "The selected provider Python is outside the repository runtime."
        ) from exc
    if not selected_resolved.is_file() or not os.access(selected_resolved, os.X_OK):
        raise ProviderRuntimeAttestationError(
            "The selected provider Python is not executable."
        )
    script = Path(__file__).resolve(strict=True)
    expected_script = (root / "tools" / "provider_runtime_attestation.py").resolve(
        strict=True
    )
    if script != expected_script:
        raise ProviderRuntimeAttestationError(
            "FreeCAD loaded the provider attestation helper from an unexpected path."
        )
    _run_selected_python(
        selected_resolved,
        script,
        root=root,
        output=output,
        nonce=nonce,
        timeout_seconds=30.0,
    )
    return 0


def _loaded_dyld_images() -> list[str]:
    if sys.platform != "darwin":
        raise ProviderRuntimeAttestationError(
            "The live provider runtime attestation requires macOS dyld."
        )
    process = ctypes.CDLL(None)
    image_count = process._dyld_image_count
    image_count.restype = ctypes.c_uint32
    image_name = process._dyld_get_image_name
    image_name.argtypes = [ctypes.c_uint32]
    image_name.restype = ctypes.c_char_p
    count = int(image_count())
    if count <= 0 or count > 4096:
        raise ProviderRuntimeAttestationError(
            "The loaded dyld image count is invalid."
        )
    result: list[str] = []
    for index in range(count):
        raw = image_name(index)
        if raw is None:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProviderRuntimeAttestationError(
                "A loaded dyld image path is not UTF-8."
            ) from exc
        if text and not text.startswith(SYSTEM_LIBRARY_ROOTS):
            result.append(text)
    return result


def _write_discovery(path: Path, value: Mapping[str, Any]) -> None:
    payload = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_DISCOVERY_BYTES:
        raise ProviderRuntimeAttestationError(
            "The provider runtime discovery result exceeds its byte limit."
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("The provider runtime discovery write made no progress.")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _python_child() -> int:
    if sys.platform != "darwin":
        raise ProviderRuntimeAttestationError(
            "The live provider runtime attestation requires macOS."
        )
    root = _canonical_root(
        Path(str(os.environ.get("VIBECAD_PROVIDER_RUNTIME_ROOT") or ""))
    )
    output = _lexical_absolute(
        str(os.environ.get("VIBECAD_PROVIDER_RUNTIME_OUTPUT") or "")
    )
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise ProviderRuntimeAttestationError(
            "The provider runtime output is outside the repository runtime."
        ) from exc
    nonce = str(os.environ.get("VIBECAD_PROVIDER_RUNTIME_NONCE") or "")
    if len(nonce) != 64 or any(character not in "0123456789abcdef" for character in nonce):
        raise ProviderRuntimeAttestationError(
            "The provider runtime nonce is invalid."
        )
    executable = Path(sys.executable).resolve(strict=True)
    prefix = Path(sys.prefix).resolve(strict=True)
    base_prefix = Path(getattr(sys, "base_prefix", sys.prefix)).resolve(strict=True)
    for label, path in (
        ("provider Python", executable),
        ("provider Python prefix", prefix),
        ("provider Python base prefix", base_prefix),
    ):
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ProviderRuntimeAttestationError(
                f"The {label} is outside the repository runtime."
            ) from exc
    site_packages = (
        prefix
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    ).resolve(strict=True)
    try:
        site_packages.relative_to(root)
    except ValueError as exc:
        raise ProviderRuntimeAttestationError(
            "The provider Python site-packages directory is outside the runtime."
        ) from exc
    sys.path.insert(0, os.fspath(site_packages))

    import httpcore
    import httpx
    import openai
    import ssl  # noqa: F401 - load the exact TLS extension and libraries.

    provider_modules = []
    for name, module in (
        ("httpcore", httpcore),
        ("httpx", httpx),
        ("openai", openai),
    ):
        path = Path(str(module.__file__ or "")).resolve(strict=True)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ProviderRuntimeAttestationError(
                f"The loaded {name} module is outside the repository runtime."
            ) from exc
        provider_modules.append({"name": name, "path": os.fspath(path)})

    loaded_modules = tuple(sys.modules)
    from importlib import metadata

    package_distributions = metadata.packages_distributions()
    distribution_names = sorted(
        {
            distribution
            for module_name in loaded_modules
            for distribution in package_distributions.get(
                module_name.partition(".")[0], ()
            )
        },
        key=lambda value: _normalize_distribution_name(value),
    )
    if not 1 <= len(distribution_names) <= MAX_DISTRIBUTIONS:
        raise ProviderRuntimeAttestationError(
            "The loaded provider distribution count is invalid."
        )
    distributions: list[dict[str, Any]] = []
    total_declared_files = 0
    for distribution_name in distribution_names:
        distribution = metadata.distribution(distribution_name)
        declared_paths: list[str] = []
        for package_path in distribution.files or ():
            located = _lexical_absolute(distribution.locate_file(package_path))
            try:
                located.relative_to(root)
            except ValueError as exc:
                raise ProviderRuntimeAttestationError(
                    "A loaded provider distribution file is outside the runtime."
                ) from exc
            declared_paths.append(os.fspath(located))
        declared_paths = sorted(set(declared_paths))
        if len(declared_paths) > MAX_FILES_PER_DISTRIBUTION:
            raise ProviderRuntimeAttestationError(
                "A loaded provider distribution has too many files."
            )
        total_declared_files += len(declared_paths)
        if total_declared_files > MAX_DISCOVERY_FILES:
            raise ProviderRuntimeAttestationError(
                "The loaded provider distributions have too many files."
            )
        distribution_root = Path(distribution.locate_file("")).resolve(strict=True)
        try:
            distribution_root.relative_to(root)
        except ValueError as exc:
            raise ProviderRuntimeAttestationError(
                "A loaded provider distribution root is outside the runtime."
            ) from exc
        distributions.append(
            {
                "name": _normalize_distribution_name(distribution.metadata["Name"]),
                "version": str(distribution.version),
                "root": os.fspath(distribution_root),
                "declared_files": declared_paths,
            }
        )
    distributions.sort(key=lambda item: str(item["name"]))

    loaded_python_files: set[str] = set()
    for module_name in loaded_modules:
        module = sys.modules.get(module_name)
        raw_path = str(getattr(module, "__file__", "") or "")
        if not raw_path:
            continue
        path = Path(raw_path).resolve(strict=True)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ProviderRuntimeAttestationError(
                "A loaded Python module is outside the repository runtime."
            ) from exc
        if path.is_file():
            loaded_python_files.add(os.fspath(path))
    if not 1 <= len(loaded_python_files) <= MAX_LOADED_PYTHON_FILES:
        raise ProviderRuntimeAttestationError(
            "The loaded Python module file count is invalid."
        )

    native_libraries: set[str] = set()
    for raw_path in _loaded_dyld_images():
        path = Path(raw_path).resolve(strict=True)
        if path == executable:
            continue
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ProviderRuntimeAttestationError(
                "A loaded non-system native library is outside the runtime."
            ) from exc
        native_libraries.add(os.fspath(path))
    if len(native_libraries) > MAX_NATIVE_LIBRARIES:
        raise ProviderRuntimeAttestationError(
            "The loaded non-system native library count exceeds its limit."
        )

    discovery = {
        "schema": DISCOVERY_SCHEMA,
        "version": DISCOVERY_VERSION,
        "nonce": nonce,
        "platform": "darwin",
        "python": {
            "executable": os.fspath(executable),
            "version": [
                sys.version_info.major,
                sys.version_info.minor,
                sys.version_info.micro,
                sys.version_info.releaselevel,
                sys.version_info.serial,
            ],
            "implementation": sys.implementation.name,
            "cache_tag": str(sys.implementation.cache_tag or ""),
            "prefix": os.fspath(prefix),
            "base_prefix": os.fspath(base_prefix),
        },
        "provider_modules": provider_modules,
        "loaded_python_files": sorted(loaded_python_files),
        "distributions": distributions,
        "native_libraries": sorted(native_libraries),
    }
    _write_discovery(output, discovery)
    return 0


def attest_provider_runtime(
    root: Path,
    freecad_cmd: Path,
    *,
    timeout_seconds: float = 45.0,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Discover through FreeCADCmd, then hash the selected provider runtime."""

    if sys.platform != "darwin":
        raise ProviderRuntimeAttestationError(
            "The live provider runtime attestation requires macOS."
        )
    timeout = validate_finite_timeout(
        timeout_seconds,
        label="The provider runtime attestation timeout",
        maximum=MAX_TIMEOUT_SECONDS,
    )
    root = _canonical_root(root)
    freecad_component = _component(
        root, freecad_cmd, label="provider attestation FreeCADCmd"
    )
    canonical_freecad_cmd = root / str(freecad_component["path"])
    helper = Path(__file__).resolve(strict=True)
    expected_helper = (root / "tools" / "provider_runtime_attestation.py").resolve(
        strict=True
    )
    if helper != expected_helper:
        raise ProviderRuntimeAttestationError(
            "The provider runtime attestation helper is outside the source root."
        )

    nonce = secrets.token_hex(32)
    work_directory = root / "build" / f".provider-runtime-attestation-{nonce}"
    create_private_run_directory(work_directory, allowed_root=root / "build")
    output = work_directory / "provider-runtime-discovery.json"
    try:
        environment = minimal_child_environment(
            {
                "VIBECAD_PROVIDER_RUNTIME_ROOT": root,
                "VIBECAD_PROVIDER_RUNTIME_OUTPUT": output,
                "VIBECAD_PROVIDER_RUNTIME_NONCE": nonce,
            }
        )
        expression = (
            "import runpy; "
            f"runpy.run_path({os.fspath(helper)!r}, run_name='__main__')"
        )
        process_runner = runner or run_bounded_process
        completed = process_runner(
            [os.fspath(canonical_freecad_cmd), "-c", expression],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        if int(completed.returncode) != 0:
            tail = str(getattr(completed, "stdout", "") or "")[-2000:]
            raise ProviderRuntimeAttestationError(
                "FreeCADCmd could not attest the selected provider Python. " + tail
            )
        snapshot, discovery = load_bounded_json(
            output,
            max_bytes=MAX_DISCOVERY_BYTES,
            label="provider runtime discovery",
            require_single_link=True,
        )
        with snapshot:
            if not isinstance(discovery, dict):
                raise ProviderRuntimeAttestationError(
                    "The provider runtime discovery result is not an object."
                )
            if discovery.get("nonce") != nonce:
                raise ProviderRuntimeAttestationError(
                    "The provider runtime discovery result has the wrong nonce."
                )
            identity = build_provider_runtime_identity(root, discovery)
            snapshot.verify_unchanged()
        return identity
    except (EvidenceIOError, OSError, ValueError) as exc:
        if isinstance(exc, ProviderRuntimeAttestationError):
            raise
        raise ProviderRuntimeAttestationError(
            "The provider runtime attestation evidence was rejected."
        ) from exc
    finally:
        shutil.rmtree(work_directory, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true")
    args, _unknown = parser.parse_known_args()
    return _python_child() if args.child else _freecad_stage()


if __name__ == "__main__":
    raise SystemExit(main())
