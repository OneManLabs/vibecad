#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bounded, no-follow file I/O for retained benchmark evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any, Callable, Iterable


READ_CHUNK_BYTES = 1024 * 1024


class EvidenceIOError(ValueError):
    """Raised when retained benchmark evidence is not safe to consume."""


def lexical_absolute_path(path: str | os.PathLike[str]) -> Path:
    """Return an absolute path without following any file-system link."""

    value = os.fspath(path)
    if not value or "\x00" in value:
        raise EvidenceIOError("An evidence path is empty or contains a null byte.")
    return Path(os.path.abspath(value))


def _directory_flags() -> int:
    required = ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required):
        raise EvidenceIOError(
            "This platform cannot enforce no-follow benchmark evidence I/O."
        )
    return os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW


def _file_flags() -> int:
    if not hasattr(os, "O_CLOEXEC") or not hasattr(os, "O_NOFOLLOW"):
        raise EvidenceIOError(
            "This platform cannot enforce no-follow benchmark evidence I/O."
        )
    return os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW


def _open_parent_directory(path: Path, *, label: str) -> tuple[int, str]:
    """Open each parent component without following a symbolic link."""

    absolute = lexical_absolute_path(path)
    parts = absolute.parts
    if len(parts) < 2 or not absolute.name:
        raise EvidenceIOError(f"The {label} path has no file name.")
    flags = _directory_flags()
    descriptor = os.open(parts[0], flags)
    try:
        for component in parts[1:-1]:
            if component in {"", ".", ".."}:
                raise EvidenceIOError(
                    f"The {label} path contains an unsafe directory component."
                )
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor, parts[-1]
    except Exception:
        os.close(descriptor)
        raise


def _stable_metadata(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


@dataclass
class SecureFileSnapshot:
    """One open, content-bound regular file and its stable path binding."""

    path: Path
    label: str
    descriptor: int
    parent_descriptor: int
    file_name: str
    metadata: os.stat_result
    sha256: str
    data: bytes | None
    _closed: bool = False

    @property
    def identity(self) -> tuple[int, int]:
        return (self.metadata.st_dev, self.metadata.st_ino)

    @property
    def size(self) -> int:
        return self.metadata.st_size

    def verify_unchanged(self) -> None:
        """Require both the open object and its directory entry to be unchanged."""

        if self._closed:
            raise EvidenceIOError(f"The {self.label} evidence descriptor is closed.")
        current_open = os.fstat(self.descriptor)
        try:
            current_path = os.stat(
                self.file_name,
                dir_fd=self.parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError as exc:
            raise EvidenceIOError(
                f"The {self.label} evidence path was removed during scoring."
            ) from exc
        expected = _stable_metadata(self.metadata)
        if (
            not stat.S_ISREG(current_path.st_mode)
            or _stable_metadata(current_open) != expected
            or _stable_metadata(current_path) != expected
        ):
            raise EvidenceIOError(
                f"The {self.label} evidence path changed during scoring."
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        os.close(self.descriptor)
        os.close(self.parent_descriptor)

    def __enter__(self) -> "SecureFileSnapshot":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def open_bounded_regular_file(
    path: str | os.PathLike[str],
    *,
    max_bytes: int,
    label: str,
    retain_data: bool,
    require_single_link: bool = False,
) -> SecureFileSnapshot:
    """Open and hash one bounded regular file without following links."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer.")
    absolute = lexical_absolute_path(path)
    try:
        parent_descriptor, file_name = _open_parent_directory(absolute, label=label)
        try:
            descriptor = os.open(
                file_name,
                _file_flags(),
                dir_fd=parent_descriptor,
            )
        except Exception:
            os.close(parent_descriptor)
            raise
    except OSError as exc:
        raise EvidenceIOError(
            f"The {label} evidence path is missing, inaccessible, or a symbolic link."
        ) from exc

    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise EvidenceIOError(f"The {label} evidence path is not a regular file.")
        if require_single_link and before.st_nlink != 1:
            raise EvidenceIOError(
                f"The {label} evidence file has more than one hard link."
            )
        if before.st_size > max_bytes:
            raise EvidenceIOError(
                f"The {label} evidence file exceeds the {max_bytes}-byte limit."
            )
        digest = hashlib.sha256()
        retained = bytearray() if retain_data else None
        observed_size = 0
        while True:
            remaining = max_bytes + 1 - observed_size
            if remaining <= 0:
                raise EvidenceIOError(
                    f"The {label} evidence file exceeds the {max_bytes}-byte limit."
                )
            block = os.read(descriptor, min(READ_CHUNK_BYTES, remaining))
            if not block:
                break
            observed_size += len(block)
            digest.update(block)
            if retained is not None:
                retained.extend(block)
        after = os.fstat(descriptor)
        if observed_size > max_bytes:
            raise EvidenceIOError(
                f"The {label} evidence file exceeds the {max_bytes}-byte limit."
            )
        if (
            observed_size != before.st_size
            or _stable_metadata(after) != _stable_metadata(before)
        ):
            raise EvidenceIOError(
                f"The {label} evidence file changed while it was read."
            )
        snapshot = SecureFileSnapshot(
            path=absolute,
            label=label,
            descriptor=descriptor,
            parent_descriptor=parent_descriptor,
            file_name=file_name,
            metadata=before,
            sha256=digest.hexdigest(),
            data=bytes(retained) if retained is not None else None,
        )
        snapshot.verify_unchanged()
        return snapshot
    except Exception:
        os.close(descriptor)
        os.close(parent_descriptor)
        raise


def _json_object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceIOError(f"The JSON evidence contains duplicate key {key!r}.")
        result[key] = value
    return result


def load_bounded_json(
    path: str | os.PathLike[str],
    *,
    max_bytes: int,
    label: str,
    require_single_link: bool = False,
) -> tuple[SecureFileSnapshot, Any]:
    """Read, hash, and parse one JSON file from one retained descriptor."""

    snapshot = open_bounded_regular_file(
        path,
        max_bytes=max_bytes,
        label=label,
        retain_data=True,
        require_single_link=require_single_link,
    )
    try:
        assert snapshot.data is not None
        text = snapshot.data.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_json_object_without_duplicate_keys)
        return snapshot, value
    except Exception:
        snapshot.close()
        raise


def reject_file_aliases(snapshots: Iterable[SecureFileSnapshot]) -> None:
    """Reject two evidence paths that name the same file object."""

    seen: dict[tuple[int, int], SecureFileSnapshot] = {}
    for snapshot in snapshots:
        previous = seen.get(snapshot.identity)
        if previous is not None:
            raise EvidenceIOError(
                f"The {snapshot.label} evidence aliases the {previous.label} evidence."
            )
        seen[snapshot.identity] = snapshot


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("The scored evidence write made no progress.")
        offset += written


def write_json_exclusive(
    path: str | os.PathLike[str],
    value: Any,
    *,
    max_bytes: int,
    protected_inputs: Iterable[SecureFileSnapshot] = (),
    stability_check: Callable[[], None] | None = None,
) -> None:
    """Publish one new mode-0600 JSON file and fsync its directory."""

    absolute = lexical_absolute_path(path)
    payload = (
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n"
    ).encode("utf-8")
    if len(payload) > max_bytes:
        raise EvidenceIOError(
            f"The scored evidence exceeds the {max_bytes}-byte output limit."
        )
    protected = tuple(protected_inputs)
    for snapshot in protected:
        if absolute == snapshot.path:
            raise EvidenceIOError(
                "The scored output path aliases a raw or rating input path."
            )

    try:
        parent_descriptor, file_name = _open_parent_directory(
            absolute, label="scored output"
        )
    except OSError as exc:
        raise EvidenceIOError(
            "The scored output parent is missing, inaccessible, or a symbolic link."
        ) from exc
    temporary_name = ""
    promoted = False
    try:
        try:
            existing = os.stat(
                file_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            existing = None
        if existing is not None:
            for snapshot in protected:
                if (existing.st_dev, existing.st_ino) == snapshot.identity:
                    raise EvidenceIOError(
                        "The scored output path aliases a raw or rating input file."
                    )
            raise EvidenceIOError(
                "The scored output path already exists; evidence is never overwritten."
            )

        for _attempt in range(32):
            candidate = f".{file_name}.{secrets.token_hex(12)}.tmp"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_CLOEXEC
                    | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent_descriptor,
                )
                temporary_name = candidate
                break
            except FileExistsError:
                continue
        else:
            raise EvidenceIOError("A private scored-output staging file is unavailable.")

        try:
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, payload)
            os.fsync(descriptor)
            staged = os.fstat(descriptor)
        finally:
            os.close(descriptor)

        if stability_check is not None:
            stability_check()
        os.link(
            temporary_name,
            file_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        promoted = True
        if stability_check is not None:
            stability_check()
        published = os.stat(
            file_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(published.st_mode)
            or (published.st_dev, published.st_ino) != (staged.st_dev, staged.st_ino)
            or published.st_size != len(payload)
            or stat.S_IMODE(published.st_mode) != 0o600
        ):
            raise EvidenceIOError("The scored output failed its publication check.")
        os.fsync(parent_descriptor)
        os.unlink(temporary_name, dir_fd=parent_descriptor)
        temporary_name = ""
        os.fsync(parent_descriptor)
    except Exception:
        if promoted:
            try:
                os.unlink(file_name, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            except OSError:
                pass
        raise
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            except OSError:
                pass
        os.close(parent_descriptor)
