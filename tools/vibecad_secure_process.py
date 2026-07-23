#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Small process and directory controls for live benchmark tools."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import threading
import time
from typing import Any, Mapping, Sequence


DEFAULT_OUTPUT_LIMIT_BYTES = 256 * 1024
DEFAULT_TERMINATION_GRACE_SECONDS = 0.5

# These values identify the local user session or are required to start a GUI
# process. Provider credentials, language-loader controls, dynamic-loader
# controls, proxies, and shell-startup controls are intentionally absent.
_SAFE_AMBIENT_NAMES = frozenset(
    {
        "APPDATA",
        "COLORTERM",
        "COMSPEC",
        "DISPLAY",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOCALAPPDATA",
        "LOGNAME",
        "NUMBER_OF_PROCESSORS",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMDATA",
        "SECURITYSESSIONID",
        "SYSTEMROOT",
        "TERM",
        "TMPDIR",
        "TZ",
        "USER",
        "USERPROFILE",
        "WAYLAND_DISPLAY",
        "WINDIR",
        "XDG_RUNTIME_DIR",
        "__CF_USER_TEXT_ENCODING",
    }
)
_SAFE_EXPLICIT_NAMES = frozenset({"QT_QPA_PLATFORM"})


@dataclass(frozen=True)
class BoundedProcessResult:
    """Return the subprocess result and a bounded output tail."""

    args: Sequence[str]
    returncode: int
    stdout: str | bytes
    stderr: None = None
    output_truncated: bool = False
    output_bytes_seen: int = 0


def validate_finite_timeout(
    value: float,
    *,
    label: str,
    maximum: float,
    minimum: float = 0.0,
    minimum_inclusive: bool = False,
) -> float:
    """Return one bounded finite timeout or raise before any side effect."""

    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number.") from exc
    lower_ok = timeout >= minimum if minimum_inclusive else timeout > minimum
    if not math.isfinite(timeout) or not lower_ok or timeout > maximum:
        relation = "at least" if minimum_inclusive else "greater than"
        raise ValueError(
            f"{label} must be finite, {relation} {minimum:g}, and at most {maximum:g} seconds."
        )
    return timeout


def minimal_child_environment(
    explicit: Mapping[str, object] | None = None,
    *,
    ambient: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a minimal environment without ambient credentials or injection controls."""

    source = os.environ if ambient is None else ambient
    result = {
        name: str(source[name])
        for name in _SAFE_AMBIENT_NAMES
        if name in source and "\x00" not in str(source[name])
    }
    # Never accept command lookup from the parent process. os.defpath contains
    # only the fixed system command directories on supported POSIX targets.
    result["PATH"] = os.defpath
    for raw_name, raw_value in dict(explicit or {}).items():
        name = str(raw_name)
        value = str(raw_value)
        if (
            not name
            or "=" in name
            or "\x00" in name
            or "\x00" in value
            or not (name.startswith("VIBECAD_") or name in _SAFE_EXPLICIT_NAMES)
        ):
            raise ValueError(f"The explicit child environment name is not allowed: {name!r}.")
        result[name] = value
    return result


def _open_directory_component(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(name, flags, dir_fd=parent_fd)


def _posix_directory_primitives_available() -> bool:
    """Return whether secure descriptor-relative directory creation is available."""

    return (
        os.open in os.supports_dir_fd
        and os.mkdir in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and bool(getattr(os, "O_DIRECTORY", 0))
        and bool(getattr(os, "O_NOFOLLOW", 0))
        and hasattr(os, "fchmod")
        and hasattr(os, "fstat")
    )


def _validate_posix_parent(details: os.stat_result) -> None:
    if not stat.S_ISDIR(details.st_mode):
        raise ValueError("The private run directory has a non-directory parent.")
    effective_uid = os.geteuid()
    if details.st_uid not in {0, effective_uid}:
        raise ValueError(
            "The private run directory has a parent with an unsafe owner."
        )
    writable_by_other_users = stat.S_IMODE(details.st_mode) & (
        stat.S_IWGRP | stat.S_IWOTH
    )
    is_root_sticky_directory = (
        details.st_uid == 0 and bool(details.st_mode & stat.S_ISVTX)
    )
    if writable_by_other_users and not is_root_sticky_directory:
        raise ValueError(
            "The private run directory has a parent with an unsafe mode."
        )


def _require_contained_output_path(path: Path, allowed_root: Path) -> tuple[Path, Path]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    root = Path(os.path.abspath(os.fspath(allowed_root)))
    try:
        common = Path(os.path.commonpath((os.fspath(absolute), os.fspath(root))))
    except ValueError as exc:
        raise ValueError(
            "The private run directory is outside the allowed output root."
        ) from exc
    if common != root or absolute == root:
        raise ValueError("The private run directory is outside the allowed output root.")
    return absolute, root


def _create_private_directory_posix(path: Path, allowed_root: Path) -> Path:
    if not _posix_directory_primitives_available():
        raise RuntimeError(
            "Secure private directory creation requires POSIX no-follow and "
            "descriptor-relative file operations."
        )
    absolute, _root = _require_contained_output_path(path, allowed_root)
    parts = absolute.parts
    if len(parts) < 2 or not absolute.name:
        raise ValueError("The private run directory must have a local directory name.")
    directory_fd = os.open(
        parts[0], os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        _validate_posix_parent(os.fstat(directory_fd))
        for component in parts[1:-1]:
            try:
                next_fd = _open_directory_component(directory_fd, component)
            except FileNotFoundError:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=directory_fd)
                except FileExistsError:
                    # Another process created the entry. The no-follow open and
                    # checks below decide whether it is safe.
                    pass
                next_fd = _open_directory_component(directory_fd, component)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ValueError(
                        "The private run directory has a symlink or unsafe parent."
                    ) from exc
                raise
            details = os.fstat(next_fd)
            try:
                _validate_posix_parent(details)
            except Exception:
                os.close(next_fd)
                raise
            os.close(directory_fd)
            directory_fd = next_fd
        try:
            os.mkdir(absolute.name, mode=0o700, dir_fd=directory_fd)
        except FileExistsError:
            raise
        final_fd = _open_directory_component(directory_fd, absolute.name)
        try:
            os.fchmod(final_fd, 0o700)
            details = os.fstat(final_fd)
            if not stat.S_ISDIR(details.st_mode):
                raise ValueError("The private run path is not a directory.")
            if details.st_uid != os.geteuid():
                raise ValueError("The private run path has an unsafe owner.")
            if stat.S_IMODE(details.st_mode) != 0o700:
                raise ValueError("The private run path does not have mode 0700.")
            entry_details = os.stat(
                absolute.name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if (
                entry_details.st_dev,
                entry_details.st_ino,
                entry_details.st_mode,
            ) != (details.st_dev, details.st_ino, details.st_mode):
                raise RuntimeError(
                    "The private run directory identity changed during creation."
                )
        finally:
            os.close(final_fd)
    finally:
        os.close(directory_fd)
    return absolute


def _is_windows_reparse_point(details: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = int(getattr(details, "st_file_attributes", 0))
    return bool(attributes & reparse_flag)


def _create_private_directory_windows(path: Path, allowed_root: Path) -> Path:
    absolute, _root = _require_contained_output_path(path, allowed_root)
    for parent in reversed(absolute.parents):
        if parent == parent.parent:
            continue
        try:
            details = parent.lstat()
        except FileNotFoundError:
            parent.mkdir(mode=0o700)
            details = parent.lstat()
        if (
            stat.S_ISLNK(details.st_mode)
            or _is_windows_reparse_point(details)
            or not stat.S_ISDIR(details.st_mode)
        ):
            raise ValueError(
                "The private run directory has a symlink or unsafe parent."
            )
    absolute.mkdir(mode=0o700)
    os.chmod(absolute, 0o700)
    final_details = absolute.lstat()
    if (
        stat.S_ISLNK(final_details.st_mode)
        or _is_windows_reparse_point(final_details)
        or not stat.S_ISDIR(final_details.st_mode)
    ):
        raise ValueError("The private run path is not a safe directory.")
    return absolute


def create_private_run_directory(
    path: Path, *, allowed_root: Path | None = None
) -> Path:
    """Create one private directory inside an explicit allowed output root.

    The target parent is the compatibility default. New callers can pass a
    narrower stable root. POSIX systems fail closed when the required
    descriptor-relative and no-follow operations are not available.
    """

    target = Path(path)
    root = Path(allowed_root) if allowed_root is not None else target.parent
    if os.name == "posix":
        return _create_private_directory_posix(target, root)
    if os.name == "nt":
        return _create_private_directory_windows(target, root)
    raise RuntimeError("Secure private directory creation is not supported on this OS.")


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class _WindowsJobObject:
    """Own one Windows Job Object with kill-on-close enabled."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9
    CREATE_SUSPENDED = 0x00000004

    def __init__(
        self,
        kernel32: Any,
        ntdll: Any,
        handle: Any,
        ctypes_module: Any,
    ) -> None:
        self._kernel32 = kernel32
        self._ntdll = ntdll
        self._handle = handle
        self._ctypes = ctypes_module
        self._closed = False

    @classmethod
    def create(cls) -> "_WindowsJobObject":
        try:
            import ctypes
            from ctypes import wintypes

            class BasicLimitInformation(ctypes.Structure):
                _fields_ = (
                    ("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                )

            class IoCounters(ctypes.Structure):
                _fields_ = (
                    ("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong),
                )

            class ExtendedLimitInformation(ctypes.Structure):
                _fields_ = (
                    ("BasicLimitInformation", BasicLimitInformation),
                    ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                )

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            ntdll = ctypes.WinDLL("ntdll")
            kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.SetInformationJobObject.argtypes = (
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            )
            kernel32.SetInformationJobObject.restype = wintypes.BOOL
            kernel32.AssignProcessToJobObject.argtypes = (
                wintypes.HANDLE,
                wintypes.HANDLE,
            )
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
            kernel32.TerminateJobObject.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
            ntdll.NtResumeProcess.restype = ctypes.c_long

            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            information = ExtendedLimitInformation()
            information.BasicLimitInformation.LimitFlags = cls._KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                handle,
                cls._EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(information),
                ctypes.sizeof(information),
            ):
                error = ctypes.WinError(ctypes.get_last_error())
                kernel32.CloseHandle(handle)
                raise error
            return cls(kernel32, ntdll, handle, ctypes)
        except Exception as exc:
            raise RuntimeError(
                "Secure Windows process control requires a Job Object with "
                "kill-on-close support."
            ) from exc

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        process_handle = getattr(process, "_handle", None)
        if process_handle is None or not self._kernel32.AssignProcessToJobObject(
            self._handle, process_handle
        ):
            error_code = self._ctypes.get_last_error()
            raise RuntimeError(
                "The child process could not enter the secure Windows Job Object "
                f"(Windows error {error_code})."
            )

    def resume(self, process: subprocess.Popen[bytes]) -> None:
        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            raise RuntimeError(
                "The suspended child process has no Windows process handle."
            )
        status = int(self._ntdll.NtResumeProcess(process_handle))
        if status != 0:
            raise RuntimeError(
                "The secure Windows child process could not resume "
                f"(NTSTATUS 0x{status & 0xFFFFFFFF:08x})."
            )

    def terminate(self) -> None:
        if self._closed:
            return
        if not self._kernel32.TerminateJobObject(self._handle, 1):
            error_code = self._ctypes.get_last_error()
            raise RuntimeError(
                "The secure Windows Job Object could not terminate its processes "
                f"(Windows error {error_code})."
            )

    def close(self) -> None:
        if self._closed:
            return
        if not self._kernel32.CloseHandle(self._handle):
            error_code = self._ctypes.get_last_error()
            raise RuntimeError(
                "The secure Windows Job Object handle could not close "
                f"(Windows error {error_code})."
            )
        self._closed = True


def _terminate_windows_job(
    process: subprocess.Popen[bytes],
    job: _WindowsJobObject,
    grace_seconds: float,
) -> None:
    job.terminate()
    if process.poll() is None:
        try:
            process.wait(timeout=max(grace_seconds, 0.1))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    grace_seconds: float,
    *,
    windows_job: _WindowsJobObject | None = None,
) -> None:
    if os.name != "posix":
        if os.name == "nt":
            if windows_job is None:
                raise RuntimeError(
                    "Secure Windows process cleanup requires an assigned Job Object."
                )
            _terminate_windows_job(process, windows_job, grace_seconds)
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        return

    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        # macOS can report EPERM during the short interval in which the last
        # group member exits. Accept only a confirmed group disappearance.
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline and _process_group_exists(process_group):
            time.sleep(0.01)
        if not _process_group_exists(process_group):
            return
        raise RuntimeError(
            "The POSIX child process group could not be terminated."
        ) from exc
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and _process_group_exists(process_group):
        time.sleep(0.01)
    if _process_group_exists(process_group):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=max(grace_seconds, 0.1))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_bounded_process(
    args: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    stdin: Any = subprocess.DEVNULL,
    stdout: Any = subprocess.PIPE,
    stderr: Any = subprocess.STDOUT,
    text: bool = True,
    timeout: float,
    check: bool = False,
    output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
    termination_grace_seconds: float = DEFAULT_TERMINATION_GRACE_SECONDS,
) -> BoundedProcessResult:
    """Run a child in a new group and retain only a bounded output tail."""

    timeout = validate_finite_timeout(
        timeout, label="The process timeout", maximum=24 * 60 * 60
    )
    grace = validate_finite_timeout(
        termination_grace_seconds,
        label="The process termination grace period",
        maximum=30,
    )
    if isinstance(output_limit_bytes, bool) or not isinstance(output_limit_bytes, int):
        raise ValueError("The process output limit must be an integer.")
    if output_limit_bytes <= 0 or output_limit_bytes > 16 * 1024 * 1024:
        raise ValueError(
            "The process output limit must be greater than zero and at most 16 MiB."
        )
    if stdout != subprocess.PIPE or stderr != subprocess.STDOUT:
        raise ValueError("The bounded runner requires one merged output pipe.")

    windows_job: _WindowsJobObject | None = None
    popen_options: dict[str, Any] = {
        "cwd": cwd,
        "env": None if env is None else dict(env),
        "stdin": stdin,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": False,
    }
    if os.name == "posix":
        popen_options["start_new_session"] = True
    elif os.name == "nt":
        windows_job = _WindowsJobObject.create()
        popen_options["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        ) | _WindowsJobObject.CREATE_SUSPENDED
    try:
        process = subprocess.Popen(list(args), **popen_options)
    except BaseException:
        if windows_job is not None:
            windows_job.close()
        raise
    if windows_job is not None:
        try:
            windows_job.assign(process)
            windows_job.resume(process)
        except BaseException:
            try:
                _terminate_windows_job(process, windows_job, grace)
            finally:
                windows_job.close()
            raise
    retained = bytearray()
    total_bytes = 0
    state_lock = threading.Lock()

    def drain_output() -> None:
        nonlocal total_bytes
        assert process.stdout is not None
        try:
            while True:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    return
                with state_lock:
                    total_bytes += len(chunk)
                    if len(chunk) >= output_limit_bytes:
                        retained[:] = chunk[-output_limit_bytes:]
                    else:
                        retained.extend(chunk)
                        excess = len(retained) - output_limit_bytes
                        if excess > 0:
                            del retained[:excess]
        except (OSError, ValueError):
            # The controller can close the pipe after it stops the full group.
            return

    reader = threading.Thread(
        target=drain_output, name="vibecad-bounded-output", daemon=True
    )
    timed_out = False
    try:
        reader.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process_tree(
                process,
                grace,
                windows_job=windows_job,
            )
        else:
            # A direct child can exit after it starts a detached worker that
            # closes the inherited output pipe. Check the process container
            # after every direct-child exit. Do not use pipe state as a proxy
            # for descendant state.
            if os.name == "posix":
                _terminate_process_tree(process, grace)
            elif os.name == "nt":
                assert windows_job is not None
                _terminate_windows_job(process, windows_job, grace)

        reader.join(timeout=max(grace, 0.1))
        if reader.is_alive():
            # Cleanup above should release every inherited output handle. Close
            # the local read handle if an OS pipe still does not reach EOF.
            if process.stdout is not None:
                process.stdout.close()
            reader.join(timeout=max(grace, 0.1))
    finally:
        try:
            if process.poll() is None:
                _terminate_process_tree(
                    process,
                    grace,
                    windows_job=windows_job,
                )
            elif os.name == "posix" and _process_group_exists(process.pid):
                _terminate_process_tree(process, grace)
        finally:
            # Closing an assigned Windows Job Object is the final fail-closed
            # boundary. Its limit policy kills any member that survived an
            # earlier termination attempt.
            if windows_job is not None:
                windows_job.close()
    with state_lock:
        output_bytes = bytes(retained)
        output_size = total_bytes
    output: str | bytes = (
        output_bytes.decode("utf-8", errors="replace") if text else output_bytes
    )
    if timed_out:
        raise subprocess.TimeoutExpired(args, timeout, output=output)
    result = BoundedProcessResult(
        args=tuple(str(item) for item in args),
        returncode=int(process.returncode),
        stdout=output,
        output_truncated=output_size > output_limit_bytes,
        output_bytes_seen=output_size,
    )
    if check and result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode, args, output=result.stdout
        )
    return result
