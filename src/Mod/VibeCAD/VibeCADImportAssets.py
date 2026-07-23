# SPDX-License-Identifier: LGPL-2.1-or-later
"""Content-bound, project-local STEP assets selected by a human.

The provider sees only opaque asset metadata.  This module keeps operating-
system paths inside the trusted application boundary.  On POSIX systems, all
store entries are opened relative to verified directory descriptors.  A
project-local kernel lock and an authenticated journal make registration
recoverable after process termination.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
from typing import Any
import uuid


IMPORT_ASSET_SCHEMA = "vibecad-project-import-assets-v1"
IMPORT_ASSET_VERSION = 1
IMPORT_ASSET_DIRECTORY = "import-assets"
IMPORT_ASSET_MANIFEST = "manifest.json"
IMPORT_ASSET_LOCK = ".registration.lock"
IMPORT_ASSET_JOURNAL = "registration-journal.json"
IMPORT_ASSET_JOURNAL_SCHEMA = "vibecad-import-registration-journal-v1"
IMPORT_ASSET_JOURNAL_VERSION = 1
MAX_IMPORT_ASSETS = 128
MAX_IMPORT_ASSET_BYTES = 512 * 1024 * 1024
MAX_IMPORT_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_IMPORT_JOURNAL_BYTES = 4 * 1024 * 1024
DEFAULT_IMPORT_ASSET_LOCK_TIMEOUT_SECONDS = 5.0
DEFAULT_PROVIDER_IMPORT_ASSET_LIMIT = 12
SUPPORTED_IMPORT_EXTENSIONS = frozenset({".step", ".stp"})

_ASSET_ID = re.compile(r"^[0-9a-f]{32}$")
_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,256}$")
_ENTRY_FIELDS = {
    "asset_id",
    "stored_name",
    "format",
    "size_bytes",
    "sha256",
    "created_at",
    "project_id",
}
_MANIFEST_CONTENT_FIELDS = {
    "schema",
    "version",
    "project_id",
    "updated_at",
    "assets",
}
_JOURNAL_CONTENT_FIELDS = {
    "schema",
    "version",
    "project_id",
    "transaction_id",
    "state",
    "temporary_name",
    "prior_manifest_exists",
    "prior_manifest",
    "entry",
    "updated_at",
}
_JOURNAL_STATES = frozenset({"prepared", "asset_promoted", "manifest_promoted"})
_FILESYSTEM_ERROR = "The STEP import asset store operation failed."
_SECURE_PLATFORM_ERROR = (
    "This platform cannot prove safe no-follow import-store identity."
)

PolicyCheck = Callable[[], None]
PermissionCheck = Callable[[str], None]
FaultInjector = Callable[[str], None]

_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_ASSET_DIGEST_CACHE: OrderedDict[tuple[Any, ...], str] = OrderedDict()
_ASSET_DIGEST_CACHE_GUARD = threading.Lock()
_MAX_ASSET_DIGEST_CACHE_ENTRIES = 256


class ImportAssetScanCancelled(RuntimeError):
    """The user stopped asset authentication before provider work started."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _content_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
    )


def _same_unchanged_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        _same_identity(left, right)
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _descriptor_security_supported() -> bool:
    """Return true only when the required no-follow primitives are present.

    Windows must fail closed until a handle-relative implementation can prove
    the same no-follow and file-identity properties.
    """

    required_dir_fd = (os.open, os.stat, os.unlink, os.mkdir, os.rename, os.link)
    return bool(
        os.name != "nt"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and all(operation in os.supports_dir_fd for operation in required_dir_fd)
    )


def import_asset_store_supported() -> bool:
    """Return whether this platform has the required secure file primitives."""

    return _descriptor_security_supported()


def _safe_child_name(name: str) -> str:
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        raise RuntimeError("The project import store contains an unsafe name.")
    if Path(name).name != name or name in {".", ".."}:
        raise RuntimeError("The project import store contains an unsafe name.")
    return name


def _open_read_only(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        return os.open(path, flags)
    except OSError as exc:
        raise ValueError(
            "The import asset is missing or is not a safe regular file."
        ) from exc


def _file_sha256(path: Path) -> str:
    """Hash one no-follow regular file and verify its path identity."""

    descriptor = _open_read_only(path)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("The import asset is not a safe regular file.")
        digest = hashlib.sha256()
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        after = os.fstat(descriptor)
        try:
            path_after = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            raise ValueError("The import asset changed while it was read.") from exc
        if not _same_unchanged_file(before, after) or not _same_identity(
            after, path_after
        ):
            raise ValueError("The import asset changed while it was read.")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _fault(callback: FaultInjector | None, stage: str) -> None:
    if callback is not None:
        callback(stage)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _clean_project_id(project_id: Any) -> str:
    clean = str(project_id or "").strip()
    if not _PROJECT_ID.fullmatch(clean):
        raise ValueError("The active VibeCAD project identity is invalid.")
    return clean


def validate_project_root(project_root: str | Path) -> Path:
    """Return one absolute, non-link project root or fail closed."""

    raw = str(project_root or "").strip()
    if not raw:
        raise ValueError("The active VibeCAD project has no durable root.")
    root = Path(raw).expanduser()
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("The active VibeCAD project root is unsafe.")
    if root == Path(root.anchor) or root == Path.home():
        raise ValueError("The active VibeCAD project root is too broad.")
    try:
        if root.exists() and (root.is_symlink() or not root.is_dir()):
            raise ValueError("The active VibeCAD project root is unsafe.")
        # Resolve normal system aliases, such as macOS /var -> /private/var.
        # The final project root is opened with O_NOFOLLOW below.
        root = root.resolve(strict=False)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root_lstat = os.stat(root, follow_symlinks=False)
        if not stat.S_ISDIR(root_lstat.st_mode):
            raise ValueError("The active VibeCAD project root is unsafe.")
    except OSError as exc:
        raise RuntimeError(_FILESYSTEM_ERROR) from exc
    return root


class _SecureStore:
    """A project and store identity held by no-follow directory descriptors."""

    def __init__(
        self,
        root: Path,
        root_fd: int,
        root_identity: os.stat_result,
        store_fd: int,
        store_identity: os.stat_result,
    ) -> None:
        self.root = root
        self.root_fd = root_fd
        self.root_identity = root_identity
        self.fd = store_fd
        self.identity = store_identity
        self.closed = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        os.close(self.fd)
        os.close(self.root_fd)

    def verify(self) -> None:
        try:
            root_fd_stat = os.fstat(self.root_fd)
            root_path_stat = os.stat(self.root, follow_symlinks=False)
            store_fd_stat = os.fstat(self.fd)
            store_path_stat = os.stat(
                IMPORT_ASSET_DIRECTORY,
                dir_fd=self.root_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise RuntimeError("The project import store identity changed.") from exc
        if not (
            stat.S_ISDIR(root_fd_stat.st_mode)
            and stat.S_ISDIR(store_fd_stat.st_mode)
            and _same_identity(root_fd_stat, self.root_identity)
            and _same_identity(root_path_stat, self.root_identity)
            and _same_identity(store_fd_stat, self.identity)
            and _same_identity(store_path_stat, self.identity)
        ):
            raise RuntimeError("The project import store identity changed.")

    def open(self, name: str, flags: int, mode: int = 0o600) -> int:
        self.verify()
        clean_name = _safe_child_name(name)
        actual_flags = flags | os.O_NOFOLLOW
        if hasattr(os, "O_BINARY"):
            actual_flags |= os.O_BINARY
        descriptor = os.open(clean_name, actual_flags, mode, dir_fd=self.fd)
        try:
            self.verify()
        except Exception:
            os.close(descriptor)
            raise
        return descriptor

    def stat(self, name: str) -> os.stat_result | None:
        self.verify()
        try:
            result = os.stat(
                _safe_child_name(name), dir_fd=self.fd, follow_symlinks=False
            )
        except FileNotFoundError:
            result = None
        self.verify()
        return result

    def unlink(self, name: str, *, missing_ok: bool = False) -> None:
        self.verify()
        try:
            os.unlink(_safe_child_name(name), dir_fd=self.fd)
        except FileNotFoundError:
            if not missing_ok:
                raise
        self.verify()

    def link(self, source: str, target: str) -> None:
        self.verify()
        os.link(
            _safe_child_name(source),
            _safe_child_name(target),
            src_dir_fd=self.fd,
            dst_dir_fd=self.fd,
            follow_symlinks=False,
        )
        self.verify()

    def replace(self, source: str, target: str) -> None:
        self.verify()
        os.replace(
            _safe_child_name(source),
            _safe_child_name(target),
            src_dir_fd=self.fd,
            dst_dir_fd=self.fd,
        )
        self.verify()

    def fsync(self) -> None:
        self.verify()
        os.fsync(self.fd)
        self.verify()

    def names(self) -> list[str]:
        self.verify()
        names = list(os.listdir(self.fd))
        self.verify()
        return names


def _open_secure_store(root: Path) -> _SecureStore:
    if not _descriptor_security_supported():
        raise RuntimeError(_SECURE_PLATFORM_ERROR)
    root_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = -1
    store_fd = -1
    try:
        root_before = os.stat(root, follow_symlinks=False)
        root_fd = os.open(root, root_flags)
        root_identity = os.fstat(root_fd)
        if not (
            stat.S_ISDIR(root_identity.st_mode)
            and _same_identity(root_before, root_identity)
        ):
            raise RuntimeError("The active VibeCAD project root identity changed.")
        try:
            store_before = os.stat(
                IMPORT_ASSET_DIRECTORY, dir_fd=root_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            try:
                os.mkdir(IMPORT_ASSET_DIRECTORY, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                # Another process can create the directory between stat and
                # mkdir.  The no-follow open and identity checks below decide
                # whether that entry is safe.
                pass
            os.fsync(root_fd)
            store_before = os.stat(
                IMPORT_ASSET_DIRECTORY, dir_fd=root_fd, follow_symlinks=False
            )
        if not stat.S_ISDIR(store_before.st_mode):
            raise ValueError("The project import store is unsafe.")
        store_fd = os.open(
            IMPORT_ASSET_DIRECTORY, root_flags, dir_fd=root_fd
        )
        store_identity = os.fstat(store_fd)
        if not (
            stat.S_ISDIR(store_identity.st_mode)
            and _same_identity(store_before, store_identity)
        ):
            raise RuntimeError("The project import store identity changed.")
        store = _SecureStore(
            root, root_fd, root_identity, store_fd, store_identity
        )
        store.verify()
        root_fd = -1
        store_fd = -1
        return store
    finally:
        if store_fd >= 0:
            os.close(store_fd)
        if root_fd >= 0:
            os.close(root_fd)


def _thread_lock(key: str) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


def _clean_lock_timeout(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("The import-store lock timeout must be finite and nonnegative.")
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "The import-store lock timeout must be finite and nonnegative."
        ) from exc
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("The import-store lock timeout must be finite and nonnegative.")
    return timeout


@contextmanager
def _locked_store(
    project_root: str | Path,
    project_id: str,
    *,
    recover: bool = True,
    lock_timeout_seconds: float = DEFAULT_IMPORT_ASSET_LOCK_TIMEOUT_SECONDS,
) -> Iterator[_SecureStore]:
    clean_project_id = _clean_project_id(project_id)
    lock_timeout = _clean_lock_timeout(lock_timeout_seconds)
    root = validate_project_root(project_root)
    lock = _thread_lock(str(root))
    deadline = time.monotonic() + lock_timeout
    if not lock.acquire(timeout=lock_timeout):
        raise RuntimeError("The project import asset store is busy.")
    try:
        store: _SecureStore | None = None
        lock_fd = -1
        locked = False
        try:
            store = _open_secure_store(root)
            lock_fd = store.open(IMPORT_ASSET_LOCK, os.O_RDWR | os.O_CREAT)
            lock_stat = os.fstat(lock_fd)
            lock_path_stat = store.stat(IMPORT_ASSET_LOCK)
            if (
                lock_path_stat is None
                or not stat.S_ISREG(lock_stat.st_mode)
                or lock_stat.st_nlink != 1
                or not _same_identity(lock_stat, lock_path_stat)
            ):
                raise RuntimeError("The project import registration lock is unsafe.")
            os.fchmod(lock_fd, 0o600)
            import fcntl

            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError as exc:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "The project import asset store is busy."
                        ) from exc
                    time.sleep(min(0.01, remaining))
            locked_stat = os.fstat(lock_fd)
            locked_path_stat = store.stat(IMPORT_ASSET_LOCK)
            if (
                locked_path_stat is None
                or not _same_identity(lock_stat, locked_stat)
                or not _same_identity(locked_stat, locked_path_stat)
            ):
                raise RuntimeError("The project import registration lock changed.")
            store.verify()
            if recover:
                _recover_registration(store, clean_project_id)
            try:
                yield store
            except Exception as operation_error:
                if recover:
                    try:
                        _recover_registration(
                            store, clean_project_id, force_rollback=True
                        )
                    except Exception as recovery_error:
                        raise RuntimeError(
                            "The import registration failed and recovery did not complete."
                        ) from operation_error
                if isinstance(operation_error, OSError):
                    raise RuntimeError(_FILESYSTEM_ERROR) from operation_error
                raise
            store.verify()
        except OSError as exc:
            raise RuntimeError(_FILESYSTEM_ERROR) from exc
        finally:
            if lock_fd >= 0:
                if locked:
                    try:
                        import fcntl

                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                os.close(lock_fd)
            if store is not None:
                store.close()
    finally:
        lock.release()


def _clean_entry(raw: Any, *, index: int, project_id: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != _ENTRY_FIELDS:
        raise RuntimeError(f"Import asset entry {index} has invalid fields.")
    asset_id = raw.get("asset_id")
    if not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id):
        raise RuntimeError(f"Import asset entry {index} has an invalid asset id.")
    stored_name = raw.get("stored_name")
    if stored_name != f"{asset_id}.step" or Path(str(stored_name)).name != stored_name:
        raise RuntimeError(f"Import asset entry {index} has an unsafe stored name.")
    if raw.get("format") != "step":
        raise RuntimeError(f"Import asset entry {index} has an unsupported format.")
    size = raw.get("size_bytes")
    if isinstance(size, bool) or type(size) is not int or not 1 <= size <= MAX_IMPORT_ASSET_BYTES:
        raise RuntimeError(f"Import asset entry {index} has an invalid byte size.")
    digest = raw.get("sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise RuntimeError(f"Import asset entry {index} has an invalid SHA-256.")
    created_at = raw.get("created_at")
    if not isinstance(created_at, str) or not created_at.endswith("Z"):
        raise RuntimeError(f"Import asset entry {index} has an invalid creation time.")
    try:
        parsed_time = datetime.fromisoformat(created_at[:-1] + "+00:00")
    except ValueError as exc:
        raise RuntimeError(
            f"Import asset entry {index} has an invalid creation time."
        ) from exc
    if parsed_time.utcoffset() != timezone.utc.utcoffset(parsed_time):
        raise RuntimeError(f"Import asset entry {index} has an invalid creation time.")
    if raw.get("project_id") != project_id:
        raise RuntimeError(f"Import asset entry {index} belongs to another project.")
    return {field: raw[field] for field in sorted(_ENTRY_FIELDS)}


def _empty_manifest(project_id: str) -> dict[str, Any]:
    return {
        "schema": IMPORT_ASSET_SCHEMA,
        "version": IMPORT_ASSET_VERSION,
        "project_id": project_id,
        "updated_at": "",
        "assets": [],
    }


def _clean_manifest_content(raw: Any, project_id: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != _MANIFEST_CONTENT_FIELDS:
        raise RuntimeError("The project import manifest has invalid fields.")
    if raw.get("schema") != IMPORT_ASSET_SCHEMA or raw.get("version") != IMPORT_ASSET_VERSION:
        raise RuntimeError("The project import manifest has an unsupported schema.")
    if raw.get("project_id") != project_id:
        raise RuntimeError("The project import manifest belongs to another project.")
    values = raw.get("assets")
    if not isinstance(values, list) or len(values) > MAX_IMPORT_ASSETS:
        raise RuntimeError("The project import manifest has an invalid asset list.")
    normalized_assets = [
        _clean_entry(item, index=index, project_id=project_id)
        for index, item in enumerate(values)
    ]
    ids = [item["asset_id"] for item in normalized_assets]
    if len(ids) != len(set(ids)):
        raise RuntimeError("The project import manifest contains duplicate asset ids.")
    updated_at = raw.get("updated_at")
    if not isinstance(updated_at, str):
        raise RuntimeError("The project import manifest has an invalid update time.")
    return {
        "schema": IMPORT_ASSET_SCHEMA,
        "version": IMPORT_ASSET_VERSION,
        "project_id": project_id,
        "updated_at": updated_at,
        "assets": normalized_assets,
    }


def _manifest_payload(content: Mapping[str, Any]) -> dict[str, Any]:
    normalized = {field: content[field] for field in sorted(_MANIFEST_CONTENT_FIELDS)}
    return {**normalized, "manifest_sha256": _content_sha256(normalized)}


def _read_name(store: _SecureStore, name: str, maximum: int) -> bytes | None:
    try:
        descriptor = store.open(name, os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum
        ):
            raise RuntimeError("The project import record has an invalid byte size.")
        chunks: list[bytes] = []
        observed = 0
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            while True:
                block = stream.read(min(1024 * 1024, maximum + 1 - observed))
                if not block:
                    break
                observed += len(block)
                if observed > maximum:
                    raise RuntimeError("The project import record exceeds its byte limit.")
                chunks.append(block)
        after = os.fstat(descriptor)
        path_after = store.stat(name)
        if path_after is None or not _same_unchanged_file(before, after) or not _same_identity(after, path_after):
            raise RuntimeError("The project import record changed while it was read.")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _load_manifest_store(store: _SecureStore, project_id: str) -> dict[str, Any]:
    payload = _read_name(store, IMPORT_ASSET_MANIFEST, MAX_IMPORT_MANIFEST_BYTES)
    if payload is None:
        return _empty_manifest(project_id)
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError("The project import manifest cannot be read.") from exc
    expected = _MANIFEST_CONTENT_FIELDS | {"manifest_sha256"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise RuntimeError("The project import manifest has invalid fields.")
    content = _clean_manifest_content(
        {field: raw[field] for field in _MANIFEST_CONTENT_FIELDS}, project_id
    )
    if raw.get("manifest_sha256") != _content_sha256(content):
        raise RuntimeError("The project import manifest content hash is invalid.")
    return content


def _load_manifest(
    project_root: str | Path,
    project_id: str,
    *,
    _store: _SecureStore | None = None,
) -> dict[str, Any]:
    clean_project_id = _clean_project_id(project_id)
    if _store is not None:
        return _load_manifest_store(_store, clean_project_id)
    with _locked_store(project_root, clean_project_id) as store:
        return _load_manifest_store(store, clean_project_id)


def _atomic_write_payload(
    store: _SecureStore,
    name: str,
    payload: Mapping[str, Any],
    *,
    maximum: int,
    fault: FaultInjector | None = None,
    fault_prefix: str = "record",
) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode("ascii") + b"\n"
    if not 1 <= len(encoded) <= maximum:
        raise RuntimeError("The project import record exceeds its byte limit.")
    temporary = f".{name}.{uuid.uuid4().hex}.tmp"
    descriptor = -1
    try:
        descriptor = store.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(os.dup(descriptor), "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        written = os.fstat(descriptor)
        path_written = store.stat(temporary)
        if (
            path_written is None
            or not stat.S_ISREG(written.st_mode)
            or written.st_size != len(encoded)
            or not _same_identity(written, path_written)
        ):
            raise RuntimeError("The project import record write was not stable.")
        _fault(fault, f"after_{fault_prefix}_temp_write")
        os.close(descriptor)
        descriptor = -1
        _fault(fault, f"before_{fault_prefix}_promotion")
        store.replace(temporary, name)
        store.fsync()
        _fault(fault, f"after_{fault_prefix}_promotion")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            store.unlink(temporary, missing_ok=True)
        except Exception:
            pass


def _atomic_write_manifest(
    project_root: str | Path,
    project_id: str,
    assets: list[dict[str, Any]],
    *,
    updated_at: str,
    fault: FaultInjector | None = None,
    _store: _SecureStore | None = None,
) -> dict[str, Any]:
    clean_project_id = _clean_project_id(project_id)
    content = _clean_manifest_content(
        {
            "schema": IMPORT_ASSET_SCHEMA,
            "version": IMPORT_ASSET_VERSION,
            "project_id": clean_project_id,
            "updated_at": str(updated_at),
            "assets": list(assets),
        },
        clean_project_id,
    )
    payload = _manifest_payload(content)
    if _store is None:
        with _locked_store(project_root, clean_project_id) as store:
            _atomic_write_payload(
                store,
                IMPORT_ASSET_MANIFEST,
                payload,
                maximum=MAX_IMPORT_MANIFEST_BYTES,
                fault=fault,
                fault_prefix="manifest",
            )
    else:
        _atomic_write_payload(
            _store,
            IMPORT_ASSET_MANIFEST,
            payload,
            maximum=MAX_IMPORT_MANIFEST_BYTES,
            fault=fault,
            fault_prefix="manifest",
        )
    return payload


def _journal_payload(content: Mapping[str, Any]) -> dict[str, Any]:
    normalized = {field: content[field] for field in sorted(_JOURNAL_CONTENT_FIELDS)}
    return {**normalized, "journal_sha256": _content_sha256(normalized)}


def _clean_journal(raw: Any, project_id: str) -> dict[str, Any]:
    expected = _JOURNAL_CONTENT_FIELDS | {"journal_sha256"}
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise RuntimeError("The import registration journal has invalid fields.")
    content = {field: raw[field] for field in _JOURNAL_CONTENT_FIELDS}
    if (
        content["schema"] != IMPORT_ASSET_JOURNAL_SCHEMA
        or content["version"] != IMPORT_ASSET_JOURNAL_VERSION
    ):
        raise RuntimeError("The import registration journal has an unsupported schema.")
    if content["project_id"] != project_id:
        raise RuntimeError("The import registration journal belongs to another project.")
    transaction_id = content["transaction_id"]
    if not isinstance(transaction_id, str) or not _ASSET_ID.fullmatch(transaction_id):
        raise RuntimeError("The import registration journal has an invalid identity.")
    if content["state"] not in _JOURNAL_STATES:
        raise RuntimeError("The import registration journal has an invalid state.")
    raw_entry = content["entry"]
    if not isinstance(raw_entry, Mapping):
        raise RuntimeError("The import registration journal has an invalid entry.")
    temporary_name = content["temporary_name"]
    if temporary_name != f".{raw_entry.get('stored_name', '')}.{transaction_id}.tmp":
        raise RuntimeError("The import registration journal has an unsafe temporary name.")
    _safe_child_name(temporary_name)
    if type(content["prior_manifest_exists"]) is not bool:
        raise RuntimeError("The import registration journal has an invalid prior state.")
    prior = _clean_manifest_content(content["prior_manifest"], project_id)
    entry = _clean_entry(raw_entry, index=len(prior["assets"]), project_id=project_id)
    if any(item["asset_id"] == entry["asset_id"] for item in prior["assets"]):
        raise RuntimeError("The import registration journal reuses an asset identity.")
    if not isinstance(content["updated_at"], str):
        raise RuntimeError("The import registration journal has an invalid update time.")
    if not content["prior_manifest_exists"] and prior != _empty_manifest(project_id):
        raise RuntimeError("The import registration journal has an invalid prior state.")
    normalized = {
        "schema": IMPORT_ASSET_JOURNAL_SCHEMA,
        "version": IMPORT_ASSET_JOURNAL_VERSION,
        "project_id": project_id,
        "transaction_id": transaction_id,
        "state": content["state"],
        "temporary_name": temporary_name,
        "prior_manifest_exists": content["prior_manifest_exists"],
        "prior_manifest": prior,
        "entry": entry,
        "updated_at": content["updated_at"],
    }
    if raw.get("journal_sha256") != _content_sha256(normalized):
        raise RuntimeError("The import registration journal content hash is invalid.")
    return normalized


def _write_journal(store: _SecureStore, journal: Mapping[str, Any]) -> None:
    payload = _journal_payload(journal)
    _atomic_write_payload(
        store,
        IMPORT_ASSET_JOURNAL,
        payload,
        maximum=MAX_IMPORT_JOURNAL_BYTES,
        fault_prefix="journal",
    )


def _load_journal(store: _SecureStore, project_id: str) -> dict[str, Any] | None:
    payload = _read_name(store, IMPORT_ASSET_JOURNAL, MAX_IMPORT_JOURNAL_BYTES)
    if payload is None:
        return None
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError("The import registration journal cannot be read.") from exc
    return _clean_journal(raw, project_id)


def _entry_matches(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(left.get(field) == right.get(field) for field in _ENTRY_FIELDS)


def _open_verified_asset(
    store: _SecureStore, entry: Mapping[str, Any]
) -> tuple[int, os.stat_result]:
    try:
        descriptor = store.open(str(entry["stored_name"]), os.O_RDONLY)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("The registered STEP import asset is missing or unsafe.") from exc
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        os.close(descriptor)
        raise ValueError("The registered STEP import asset is missing or unsafe.")
    if before.st_size != entry["size_bytes"]:
        os.close(descriptor)
        raise ValueError("The registered STEP import asset changed byte size.")
    return descriptor, before


def _hash_verified_descriptor(
    store: _SecureStore,
    name: str,
    descriptor: int,
    before: os.stat_result,
    *,
    cancellation_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    asset_index: int | None = None,
    asset_count: int | None = None,
) -> str:
    digest = hashlib.sha256()
    bytes_hashed = 0
    with os.fdopen(os.dup(descriptor), "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            if cancellation_check is not None and cancellation_check():
                raise ImportAssetScanCancelled(
                    "The STEP asset scan was cancelled."
                )
            digest.update(block)
            bytes_hashed += len(block)
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "import_asset_scan_progress",
                        "asset_index": asset_index,
                        "asset_count": asset_count,
                        "bytes_hashed": bytes_hashed,
                        "cached": False,
                    }
                )
    if cancellation_check is not None and cancellation_check():
        raise ImportAssetScanCancelled("The STEP asset scan was cancelled.")
    after = os.fstat(descriptor)
    path_after = store.stat(name)
    if (
        path_after is None
        or not _same_unchanged_file(before, after)
        or not _same_identity(after, path_after)
    ):
        raise ValueError("The registered STEP import asset changed while it was read.")
    return digest.hexdigest()


def _asset_digest_cache_key(
    store: _SecureStore, entry: Mapping[str, Any], metadata: os.stat_result
) -> tuple[Any, ...]:
    return (
        str(store.root),
        str(entry["project_id"]),
        str(entry["stored_name"]),
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(stat.S_IFMT(metadata.st_mode)),
        int(metadata.st_nlink),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
        str(entry["sha256"]),
    )


def _cached_asset_digest(key: tuple[Any, ...]) -> str | None:
    with _ASSET_DIGEST_CACHE_GUARD:
        digest = _ASSET_DIGEST_CACHE.pop(key, None)
        if digest is not None:
            _ASSET_DIGEST_CACHE[key] = digest
        return digest


def _remember_asset_digest(key: tuple[Any, ...], digest: str) -> None:
    with _ASSET_DIGEST_CACHE_GUARD:
        _ASSET_DIGEST_CACHE.pop(key, None)
        _ASSET_DIGEST_CACHE[key] = str(digest)
        while len(_ASSET_DIGEST_CACHE) > _MAX_ASSET_DIGEST_CACHE_ENTRIES:
            _ASSET_DIGEST_CACHE.popitem(last=False)


def _asset_availability(
    store: _SecureStore,
    entry: Mapping[str, Any],
    *,
    cancellation_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    asset_index: int | None = None,
    asset_count: int | None = None,
) -> str:
    """Return verified, changed, or missing after bounded authentication."""

    if store.stat(str(entry["stored_name"])) is None:
        return "missing"
    descriptor = -1
    try:
        descriptor, before = _open_verified_asset(store, entry)
        key = _asset_digest_cache_key(store, entry, before)
        observed_digest = _cached_asset_digest(key)
        if observed_digest is not None:
            after = os.fstat(descriptor)
            path_after = store.stat(str(entry["stored_name"]))
            if (
                path_after is None
                or not _same_unchanged_file(before, after)
                or not _same_identity(after, path_after)
            ):
                return "changed"
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "import_asset_scan_progress",
                        "asset_index": asset_index,
                        "asset_count": asset_count,
                        "bytes_hashed": 0,
                        "cached": True,
                    }
                )
        else:
            observed_digest = _hash_verified_descriptor(
                store,
                str(entry["stored_name"]),
                descriptor,
                before,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
                asset_index=asset_index,
                asset_count=asset_count,
            )
            _remember_asset_digest(key, observed_digest)
        exact = observed_digest == entry["sha256"]
        return "verified" if exact else "changed"
    except ImportAssetScanCancelled:
        raise
    except (ValueError, OSError):
        return "changed"
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _asset_is_exact(store: _SecureStore, entry: Mapping[str, Any]) -> bool:
    return _asset_availability(store, entry) == "verified"


def _cleanup_known_temporaries(store: _SecureStore) -> None:
    allowed_prefixes = (
        f".{IMPORT_ASSET_MANIFEST}.",
        f".{IMPORT_ASSET_JOURNAL}.",
    )
    for name in store.names():
        is_record_temp = name.startswith(allowed_prefixes) and name.endswith(".tmp")
        is_asset_temp = (
            name.startswith(".")
            and name.endswith(".tmp")
            and ".step." in name
        )
        if is_record_temp or is_asset_temp:
            try:
                store.unlink(name, missing_ok=True)
            except (OSError, RuntimeError):
                raise RuntimeError("The import registration recovery failed.")


def _recover_registration(
    store: _SecureStore,
    project_id: str,
    *,
    force_rollback: bool = False,
) -> None:
    """Resolve one interrupted registration while the project lock is held."""

    journal = _load_journal(store, project_id)
    if journal is None:
        _cleanup_known_temporaries(store)
        return
    prior = journal["prior_manifest"]
    entry = journal["entry"]
    current: dict[str, Any] | None
    try:
        current = _load_manifest_store(store, project_id)
    except RuntimeError:
        current = None
    expected_current = {
        "schema": IMPORT_ASSET_SCHEMA,
        "version": IMPORT_ASSET_VERSION,
        "project_id": project_id,
        "updated_at": journal["updated_at"],
        "assets": [*prior["assets"], entry],
    }
    committed = bool(
        not force_rollback
        and journal["state"] in {"asset_promoted", "manifest_promoted"}
        and current == expected_current
        and _asset_is_exact(store, entry)
    )
    if not committed:
        if journal["prior_manifest_exists"]:
            _atomic_write_manifest(
                store.root,
                project_id,
                list(prior["assets"]),
                updated_at=str(prior["updated_at"]),
                _store=store,
            )
        else:
            store.unlink(IMPORT_ASSET_MANIFEST, missing_ok=True)
            store.fsync()
        if not any(
            item["stored_name"] == entry["stored_name"] for item in prior["assets"]
        ):
            store.unlink(str(entry["stored_name"]), missing_ok=True)
            store.fsync()
    store.unlink(str(journal["temporary_name"]), missing_ok=True)
    store.unlink(IMPORT_ASSET_JOURNAL, missing_ok=True)
    store.fsync()
    _cleanup_known_temporaries(store)


def _rollback_registration_in_memory(
    store: _SecureStore,
    project_id: str,
    *,
    prior_manifest_exists: bool,
    prior_manifest: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> None:
    """Restore a registration after its durable journal was removed."""

    if prior_manifest_exists:
        _atomic_write_manifest(
            store.root,
            project_id,
            list(prior_manifest["assets"]),
            updated_at=str(prior_manifest["updated_at"]),
            _store=store,
        )
    else:
        store.unlink(IMPORT_ASSET_MANIFEST, missing_ok=True)
        store.fsync()
    store.unlink(str(entry["stored_name"]), missing_ok=True)
    store.unlink(IMPORT_ASSET_JOURNAL, missing_ok=True)
    store.fsync()
    _cleanup_known_temporaries(store)


def _safe_asset_metadata(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Return provider-safe metadata.  Never include an operating-system path."""

    return {
        "schema": "vibecad-project-import-asset-v1",
        "version": IMPORT_ASSET_VERSION,
        "asset_id": entry["asset_id"],
        "stored_name": entry["stored_name"],
        "format": entry["format"],
        "size_bytes": entry["size_bytes"],
        "sha256": entry["sha256"],
        "created_at": entry["created_at"],
        "project_id": entry["project_id"],
    }


def _open_external_directory(path: Path) -> tuple[Path, int, os.stat_result]:
    if not path.name or path.name in {".", ".."} or "\x00" in path.name:
        raise ValueError("The selected import asset path is unsafe.")
    try:
        parent = path.parent.resolve(strict=True)
        before = os.stat(parent, follow_symlinks=False)
        descriptor = os.open(
            parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        identity = os.fstat(descriptor)
    except OSError as exc:
        raise ValueError("The selected import asset path is unsafe.") from exc
    if not stat.S_ISDIR(identity.st_mode) or not _same_identity(before, identity):
        os.close(descriptor)
        raise ValueError("The selected import asset path is unsafe.")
    return parent, descriptor, identity


def _source_path_identity(
    source_name: str,
    parent: Path,
    parent_descriptor: int,
    parent_identity: os.stat_result,
    descriptor_stat: os.stat_result,
) -> os.stat_result:
    try:
        current_parent_fd = os.fstat(parent_descriptor)
        current_parent_path = os.stat(parent, follow_symlinks=False)
        path_stat = os.stat(
            source_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise RuntimeError("The selected import asset changed while it was copied.") from exc
    if not (
        _same_identity(current_parent_fd, parent_identity)
        and _same_identity(current_parent_path, parent_identity)
        and _same_identity(path_stat, descriptor_stat)
    ):
        raise RuntimeError("The selected import asset changed while it was copied.")
    return path_stat


def register_import_asset(
    project_root: str | Path,
    project_id: str,
    source_path: str | Path,
    *,
    policy_check: PolicyCheck,
    permission_check: PermissionCheck,
    fault: FaultInjector | None = None,
    asset_id_factory: Callable[[], str] | None = None,
    now: Callable[[], str] | None = None,
    lock_timeout_seconds: float = DEFAULT_IMPORT_ASSET_LOCK_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Copy one human-selected STEP file into the durable project store."""

    policy_check()
    permission_check("design.modify")
    _fault(fault, "after_authorization")

    clean_project_id = _clean_project_id(project_id)
    source = Path(str(source_path or "")).expanduser()
    if not source.is_absolute() or ".." in source.parts:
        raise ValueError("The selected STEP file path is unsafe.")
    if source.suffix.lower() not in SUPPORTED_IMPORT_EXTENSIONS:
        raise ValueError("The selected import file must use .step or .stp.")
    if source.is_symlink():
        raise ValueError("A symbolic link cannot be registered as an import asset.")
    if not _descriptor_security_supported():
        raise RuntimeError(_SECURE_PLATFORM_ERROR)

    source_descriptor = -1
    source_parent_descriptor = -1
    temporary_name: str | None = None
    journal_written = False
    store_for_recovery: _SecureStore | None = None
    try:
        _fault(fault, "before_source_read")
        (
            source_parent,
            source_parent_descriptor,
            source_parent_identity,
        ) = _open_external_directory(source)
        try:
            source_descriptor = os.open(
                source.name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=source_parent_descriptor,
            )
        except OSError as exc:
            raise ValueError(
                "The import asset is missing or is not a safe regular file."
            ) from exc
        source_before = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_before.st_mode):
            raise ValueError("The selected import asset is not a regular file.")
        if not 1 <= source_before.st_size <= MAX_IMPORT_ASSET_BYTES:
            raise ValueError(
                f"The selected import asset must contain 1-{MAX_IMPORT_ASSET_BYTES} bytes."
            )
        _source_path_identity(
            source.name,
            source_parent,
            source_parent_descriptor,
            source_parent_identity,
            source_before,
        )

        with _locked_store(
            project_root,
            clean_project_id,
            lock_timeout_seconds=lock_timeout_seconds,
        ) as store:
            store_for_recovery = store
            prior_manifest_exists = store.stat(IMPORT_ASSET_MANIFEST) is not None
            prior = _load_manifest_store(store, clean_project_id)
            prior_assets = list(prior["assets"])
            if len(prior_assets) >= MAX_IMPORT_ASSETS:
                raise ValueError(
                    f"A project can contain at most {MAX_IMPORT_ASSETS} registered import assets."
                )
            factory = asset_id_factory or (lambda: uuid.uuid4().hex)
            asset_id = str(factory() or "").strip().lower()
            if not _ASSET_ID.fullmatch(asset_id):
                raise RuntimeError("The generated import asset identity is invalid.")
            if any(item["asset_id"] == asset_id for item in prior_assets):
                raise RuntimeError("The import asset identity is already registered.")
            stored_name = f"{asset_id}.step"
            if store.stat(stored_name) is not None:
                raise RuntimeError("The import asset target already exists.")
            transaction_id = uuid.uuid4().hex
            temporary_name = f".{stored_name}.{transaction_id}.tmp"
            destination_descriptor = store.open(
                temporary_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
            )
            digest = hashlib.sha256()
            copied = 0
            try:
                with os.fdopen(os.dup(source_descriptor), "rb") as source_stream, os.fdopen(
                    os.dup(destination_descriptor), "wb"
                ) as destination_stream:
                    for block in iter(lambda: source_stream.read(1024 * 1024), b""):
                        copied += len(block)
                        if copied > MAX_IMPORT_ASSET_BYTES:
                            raise ValueError("The selected import asset exceeds the byte limit.")
                        digest.update(block)
                        destination_stream.write(block)
                    destination_stream.flush()
                    os.fsync(destination_stream.fileno())
                _fault(fault, "after_source_copy")
                source_after = os.fstat(source_descriptor)
                _source_path_identity(
                    source.name,
                    source_parent,
                    source_parent_descriptor,
                    source_parent_identity,
                    source_after,
                )
                if not _same_unchanged_file(source_before, source_after) or copied != source_before.st_size:
                    raise RuntimeError("The selected import asset changed while it was copied.")
                destination_stat = os.fstat(destination_descriptor)
                destination_path_stat = store.stat(temporary_name)
                if (
                    destination_path_stat is None
                    or not stat.S_ISREG(destination_stat.st_mode)
                    or destination_stat.st_size != copied
                    or not _same_identity(destination_stat, destination_path_stat)
                ):
                    raise RuntimeError("The registered import copy was not stable.")
            finally:
                os.close(destination_descriptor)
            observed_digest = digest.hexdigest()
            if any(item["sha256"] == observed_digest for item in prior_assets):
                raise ValueError("The selected STEP content is already registered.")
            _fault(fault, "after_copy")
            store.verify()
            created_at = str((now or _utc_now)())
            entry = {
                "asset_id": asset_id,
                "stored_name": stored_name,
                "format": "step",
                "size_bytes": copied,
                "sha256": observed_digest,
                "created_at": created_at,
                "project_id": clean_project_id,
            }
            _clean_entry(entry, index=len(prior_assets), project_id=clean_project_id)
            journal = {
                "schema": IMPORT_ASSET_JOURNAL_SCHEMA,
                "version": IMPORT_ASSET_JOURNAL_VERSION,
                "project_id": clean_project_id,
                "transaction_id": transaction_id,
                "state": "prepared",
                "temporary_name": temporary_name,
                "prior_manifest_exists": bool(prior_manifest_exists),
                "prior_manifest": prior,
                "entry": entry,
                "updated_at": created_at,
            }
            _write_journal(store, journal)
            journal_written = True
            _fault(fault, "after_journal_prepare")
            _fault(fault, "before_asset_promotion")
            try:
                store.link(temporary_name, stored_name)
            except FileExistsError as exc:
                raise RuntimeError("The import asset target already exists.") from exc
            store.unlink(temporary_name)
            temporary_name = None
            store.fsync()
            journal = {**journal, "state": "asset_promoted"}
            _write_journal(store, journal)
            _fault(fault, "after_asset_promotion")
            _fault(fault, "before_manifest_write")
            _atomic_write_manifest(
                project_root,
                clean_project_id,
                [*prior_assets, entry],
                updated_at=created_at,
                fault=fault,
                _store=store,
            )
            _fault(fault, "after_manifest_write")
            journal = {**journal, "state": "manifest_promoted"}
            _write_journal(store, journal)
            try:
                _fault(fault, "before_journal_deletion")
                store.unlink(IMPORT_ASSET_JOURNAL)
                _fault(fault, "after_journal_deletion")
                _fault(fault, "before_final_directory_sync")
                store.fsync()
                _fault(fault, "after_final_directory_sync")
            except Exception as finalization_error:
                try:
                    _rollback_registration_in_memory(
                        store,
                        clean_project_id,
                        prior_manifest_exists=prior_manifest_exists,
                        prior_manifest=prior,
                        entry=entry,
                    )
                except Exception as rollback_error:
                    raise RuntimeError(
                        "The import registration failed and recovery did not complete."
                    ) from finalization_error
                raise
            journal_written = False
            store_for_recovery = None
            return _safe_asset_metadata(entry)
    except Exception as operation_error:
        rollback_error: Exception | None = None
        if (
            journal_written
            and store_for_recovery is not None
            and not store_for_recovery.closed
        ):
            try:
                _recover_registration(
                    store_for_recovery, clean_project_id, force_rollback=True
                )
            except Exception as exc:
                rollback_error = exc
        if rollback_error is not None:
            raise RuntimeError(
                "The import registration failed and recovery did not complete."
            ) from operation_error
        if isinstance(operation_error, OSError):
            raise RuntimeError(_FILESYSTEM_ERROR) from operation_error
        raise
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if source_parent_descriptor >= 0:
            os.close(source_parent_descriptor)
        if temporary_name is not None and store_for_recovery is not None:
            try:
                store_for_recovery.unlink(temporary_name, missing_ok=True)
            except Exception:
                pass


def registered_import_assets(
    project_root: str | Path,
    project_id: str,
    *,
    limit: int = DEFAULT_PROVIDER_IMPORT_ASSET_LIMIT,
    lock_timeout_seconds: float = DEFAULT_IMPORT_ASSET_LOCK_TIMEOUT_SECONDS,
    cancellation_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Return bounded provider-safe metadata with SHA-verified availability."""

    clean_project_id = _clean_project_id(project_id)
    if isinstance(limit, bool) or type(limit) is not int or not 0 <= limit <= MAX_IMPORT_ASSETS:
        raise ValueError(
            f"The import asset list limit must be 0-{MAX_IMPORT_ASSETS}."
        )
    try:
        with _locked_store(
            project_root,
            clean_project_id,
            lock_timeout_seconds=lock_timeout_seconds,
        ) as store:
            manifest = _load_manifest_store(store, clean_project_id)
            result_assets = []
            selected = manifest["assets"][-limit:] if limit else []
            for index, entry in enumerate(selected, start=1):
                if cancellation_check is not None and cancellation_check():
                    raise ImportAssetScanCancelled(
                        "The STEP asset scan was cancelled."
                    )
                availability = _asset_availability(
                    store,
                    entry,
                    cancellation_check=cancellation_check,
                    progress_callback=progress_callback,
                    asset_index=index,
                    asset_count=len(selected),
                )
                result_assets.append(
                    {
                        **_safe_asset_metadata(entry),
                        "availability": availability,
                        "available": availability == "verified",
                    }
                )
            return {
                "schema": IMPORT_ASSET_SCHEMA,
                "version": IMPORT_ASSET_VERSION,
                "project_id": manifest["project_id"],
                "asset_count": len(manifest["assets"]),
                "listed_asset_count": len(result_assets),
                "assets_omitted": len(manifest["assets"]) - len(result_assets),
                "asset_limit": MAX_IMPORT_ASSETS,
                "maximum_asset_bytes": MAX_IMPORT_ASSET_BYTES,
                "supported_formats": ["step"],
                "assets": result_assets,
            }
    except OSError as exc:
        raise RuntimeError(_FILESYSTEM_ERROR) from exc


def _resolve_entry(
    store: _SecureStore, project_id: str, asset_id: str
) -> tuple[dict[str, Any], int, os.stat_result]:
    clean_id = str(asset_id or "").strip().lower()
    if not _ASSET_ID.fullmatch(clean_id):
        raise ValueError("The STEP import asset id must be 32 lowercase hex characters.")
    manifest = _load_manifest_store(store, project_id)
    matches = [item for item in manifest["assets"] if item["asset_id"] == clean_id]
    if len(matches) != 1:
        raise ValueError("The STEP import asset is not registered for this project.")
    entry = matches[0]
    descriptor, before = _open_verified_asset(store, entry)
    try:
        observed_digest = _hash_verified_descriptor(
            store, str(entry["stored_name"]), descriptor, before
        )
        if observed_digest != entry["sha256"]:
            raise ValueError("The registered STEP import asset changed content.")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return entry, descriptor, before
    except Exception:
        os.close(descriptor)
        raise


def resolve_import_asset(
    project_root: str | Path,
    project_id: str,
    asset_id: str,
    *,
    lock_timeout_seconds: float = DEFAULT_IMPORT_ASSET_LOCK_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Resolve and reauthenticate one opaque id for trusted internal use."""

    clean_project_id = _clean_project_id(project_id)
    try:
        with _locked_store(
            project_root,
            clean_project_id,
            lock_timeout_seconds=lock_timeout_seconds,
        ) as store:
            entry, descriptor, _before = _resolve_entry(
                store, clean_project_id, asset_id
            )
            os.close(descriptor)
            # Keep this path only for existing trusted internal callers.  New
            # parsers must use copy_registered_import_asset instead.
            return {
                **dict(entry),
                "path": store.root / IMPORT_ASSET_DIRECTORY / entry["stored_name"],
            }
    except OSError as exc:
        raise RuntimeError(_FILESYSTEM_ERROR) from exc


class _DestinationDirectory:
    def __init__(self, path: Path) -> None:
        if not _descriptor_security_supported():
            raise RuntimeError(_SECURE_PLATFORM_ERROR)
        if not path.is_absolute() or ".." in path.parts or not path.name:
            raise ValueError("The private STEP copy target is unsafe.")
        self.name = _safe_child_name(path.name)
        try:
            self.parent = path.parent.resolve(strict=True)
            before = os.stat(self.parent, follow_symlinks=False)
            self.fd = os.open(
                self.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            self.identity = os.fstat(self.fd)
        except OSError as exc:
            raise RuntimeError(_FILESYSTEM_ERROR) from exc
        if not stat.S_ISDIR(self.identity.st_mode) or not _same_identity(before, self.identity):
            os.close(self.fd)
            raise ValueError("The private STEP copy directory is unsafe.")

    def verify(self) -> None:
        try:
            descriptor_stat = os.fstat(self.fd)
            path_stat = os.stat(self.parent, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError("The private STEP copy directory identity changed.") from exc
        if not _same_identity(descriptor_stat, self.identity) or not _same_identity(path_stat, self.identity):
            raise RuntimeError("The private STEP copy directory identity changed.")

    def close(self) -> None:
        os.close(self.fd)


def copy_registered_import_asset(
    project_root: str | Path,
    project_id: str,
    asset_id: str,
    destination: str | Path,
    *,
    lock_timeout_seconds: float = DEFAULT_IMPORT_ASSET_LOCK_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Copy exact registered bytes to one exclusive private destination.

    The return value is provider-safe metadata and never contains a path.
    """

    clean_project_id = _clean_project_id(project_id)
    destination_directory: _DestinationDirectory | None = None
    destination_created = False
    try:
        destination_path = Path(str(destination or ""))
        destination_directory = _DestinationDirectory(destination_path)
        with _locked_store(
            project_root,
            clean_project_id,
            lock_timeout_seconds=lock_timeout_seconds,
        ) as store:
            entry, source_descriptor, source_before = _resolve_entry(
                store, clean_project_id, asset_id
            )
            destination_descriptor = -1
            try:
                destination_directory.verify()
                destination_descriptor = os.open(
                    destination_directory.name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=destination_directory.fd,
                )
                destination_created = True
                digest = hashlib.sha256()
                copied = 0
                with os.fdopen(os.dup(source_descriptor), "rb") as source_stream, os.fdopen(
                    os.dup(destination_descriptor), "wb"
                ) as destination_stream:
                    for block in iter(lambda: source_stream.read(1024 * 1024), b""):
                        copied += len(block)
                        if copied > MAX_IMPORT_ASSET_BYTES:
                            raise ValueError("The registered STEP import asset exceeds the byte limit.")
                        digest.update(block)
                        destination_stream.write(block)
                    destination_stream.flush()
                    os.fsync(destination_stream.fileno())
                source_after = os.fstat(source_descriptor)
                source_path_after = store.stat(str(entry["stored_name"]))
                destination_after = os.fstat(destination_descriptor)
                destination_path_after = os.stat(
                    destination_directory.name,
                    dir_fd=destination_directory.fd,
                    follow_symlinks=False,
                )
                destination_directory.verify()
                store.verify()
                if (
                    source_path_after is None
                    or not _same_unchanged_file(source_before, source_after)
                    or not _same_identity(source_after, source_path_after)
                    or not stat.S_ISREG(destination_after.st_mode)
                    or not _same_identity(destination_after, destination_path_after)
                    or copied != entry["size_bytes"]
                    or destination_after.st_size != copied
                    or digest.hexdigest() != entry["sha256"]
                ):
                    raise ValueError("The registered STEP import asset changed during private copy.")
                os.fsync(destination_directory.fd)
                return _safe_asset_metadata(entry)
            finally:
                if destination_descriptor >= 0:
                    os.close(destination_descriptor)
                os.close(source_descriptor)
    except Exception as operation_error:
        if destination_created and destination_directory is not None:
            try:
                os.unlink(destination_directory.name, dir_fd=destination_directory.fd)
                os.fsync(destination_directory.fd)
            except OSError:
                pass
        if isinstance(operation_error, OSError):
            raise RuntimeError(_FILESYSTEM_ERROR) from operation_error
        raise
    finally:
        if destination_directory is not None:
            destination_directory.close()


def register_human_selected_step(service: Any, source_path: str | Path) -> dict[str, Any]:
    """Platform boundary for a file chosen by a human in a native dialog."""

    from VibeCADManagedPolicy import load_managed_policy, validate_policy

    scope = service.project_scope_snapshot()

    def policy_check() -> None:
        validate_policy(load_managed_policy())

    return register_import_asset(
        scope.get("root") or "",
        str(scope.get("project_id") or ""),
        source_path,
        policy_check=policy_check,
        permission_check=service.authorize,
    )


__all__ = [
    "IMPORT_ASSET_JOURNAL_SCHEMA",
    "IMPORT_ASSET_JOURNAL_VERSION",
    "IMPORT_ASSET_SCHEMA",
    "IMPORT_ASSET_VERSION",
    "ImportAssetScanCancelled",
    "MAX_IMPORT_ASSET_BYTES",
    "copy_registered_import_asset",
    "import_asset_store_supported",
    "register_human_selected_step",
    "register_import_asset",
    "registered_import_assets",
    "resolve_import_asset",
    "validate_project_root",
]
