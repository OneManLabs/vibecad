# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cancellable, windowless process runner for scripted CAD engines."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Any


MAX_PROCESS_OUTPUT_BYTES = 1024 * 1024
PROCESS_OUTPUT_TAIL_BYTES = 64 * 1024


def process_memory_bytes(pid: int) -> int | None:
    if sys.platform == "win32":
        return _windows_process_memory_bytes(pid)
    if sys.platform == "darwin":
        return _darwin_process_memory_bytes(pid)
    status = Path(f"/proc/{int(pid)}/status")
    try:
        text = status.read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    resident: int | None = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if line.startswith("VmHWM:"):
            return int(parts[1]) * 1024
        if line.startswith("VmRSS:"):
            resident = int(parts[1]) * 1024
    return resident


def _darwin_process_memory_bytes(pid: int) -> int | None:
    try:
        completed = subprocess.run(
            ["/bin/ps", "-o", "rss=", "-p", str(int(pid))],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="ascii",
            errors="replace",
            timeout=1.0,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    return int(completed.stdout.strip()) * 1024


def _windows_process_memory_bytes(pid: int) -> int | None:
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
    except AttributeError:
        return None
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PeakWorkingSetSize)
    finally:
        kernel32.CloseHandle(handle)


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3.0)
    except Exception:
        process.kill()
        process.wait(timeout=3.0)


class _OutputBudget:
    """Count combined child output without retaining its complete content."""

    def __init__(self, limit_bytes: int) -> None:
        self.limit_bytes = int(limit_bytes)
        self.total_bytes = 0
        self.exceeded = threading.Event()
        self.lock = threading.Lock()

    def add(self, size: int) -> None:
        with self.lock:
            self.total_bytes += int(size)
            if self.total_bytes > self.limit_bytes:
                self.exceeded.set()


class _OutputTail:
    """Retain only the final bounded bytes from one child output stream."""

    def __init__(self, budget: _OutputBudget) -> None:
        self._budget = budget
        self._tail = bytearray()
        self._lock = threading.Lock()

    def add(self, block: bytes) -> None:
        self._budget.add(len(block))
        with self._lock:
            self._tail.extend(block)
            excess = len(self._tail) - PROCESS_OUTPUT_TAIL_BYTES
            if excess > 0:
                del self._tail[:excess]

    def text(self) -> str:
        with self._lock:
            encoded = bytes(self._tail)
        return encoded.decode("utf-8", errors="replace")[-16_000:]


def _drain_output(stream: Any, capture: _OutputTail) -> None:
    """Drain one child pipe into a fixed-size tail buffer."""

    try:
        for block in iter(lambda: stream.read(64 * 1024), b""):
            capture.add(block)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def run_process(
    command: list[str],
    *,
    cwd: str | Path,
    environment: dict[str, str],
    cancellation_check: Callable[[], bool] | None,
    timeout_seconds: float,
    memory_limit_bytes: int,
) -> dict[str, Any]:
    """Run one child process without a console window and enforce hard bounds."""
    creation_flags = (
        int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if sys.platform == "win32"
        else 0
    )
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=sys.platform != "win32",
            creationflags=creation_flags,
        )
    except Exception as exc:
        return {
            "started": False,
            "error": str(exc),
            "exception_type": type(exc).__name__,
        }

    if process.stdout is None or process.stderr is None:
        _terminate(process)
        return {
            "started": False,
            "error": "The child output pipes are unavailable.",
            "exception_type": "RuntimeError",
        }
    output_budget = _OutputBudget(MAX_PROCESS_OUTPUT_BYTES)
    stdout_capture = _OutputTail(output_budget)
    stderr_capture = _OutputTail(output_budget)
    readers = [
        threading.Thread(
            target=_drain_output,
            args=(process.stdout, stdout_capture),
            name="VibeCAD-worker-stdout",
            daemon=True,
        ),
        threading.Thread(
            target=_drain_output,
            args=(process.stderr, stderr_capture),
            name="VibeCAD-worker-stderr",
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    cancelled = False
    timed_out = False
    memory_exceeded = False
    output_exceeded = False
    observed_memory: int | None = None
    next_memory_check = 0.0
    while process.poll() is None:
        if cancellation_check is not None and cancellation_check():
            cancelled = True
            break
        if output_budget.exceeded.is_set():
            output_exceeded = True
            break
        now = time.monotonic()
        if now - started > timeout_seconds:
            timed_out = True
            break
        if memory_limit_bytes > 0 and now >= next_memory_check:
            next_memory_check = now + 0.5
            observed_memory = process_memory_bytes(process.pid)
            if observed_memory is not None and observed_memory > memory_limit_bytes:
                memory_exceeded = True
                break
        time.sleep(0.05)
    if cancelled or timed_out or memory_exceeded or output_exceeded:
        _terminate(process)
    process.wait()
    for reader in readers:
        reader.join(timeout=3.0)
    output_exceeded = bool(output_exceeded or output_budget.exceeded.is_set())
    return {
        "started": True,
        "returncode": process.returncode,
        "stdout": stdout_capture.text(),
        "stderr": stderr_capture.text(),
        "cancelled": cancelled,
        "timed_out": timed_out,
        "memory_exceeded": memory_exceeded,
        "output_exceeded": output_exceeded,
        "output_bytes": output_budget.total_bytes,
        "output_limit_bytes": output_budget.limit_bytes,
        "observed_memory_bytes": observed_memory,
        "elapsed_seconds": time.monotonic() - started,
    }
