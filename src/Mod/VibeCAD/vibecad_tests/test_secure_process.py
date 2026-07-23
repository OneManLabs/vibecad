# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for secure live-benchmark process and directory controls."""

from __future__ import annotations

import io
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from tools import vibecad_secure_process as secure_process


def _process_is_active(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    status = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    ).stdout.strip()
    return bool(status) and not status.startswith("Z")


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups are required.")
def test_normal_parent_exit_kills_descendant_that_closed_output(
    tmp_path: Path,
) -> None:
    child_pid_path = tmp_path / "normal-exit-child.pid"
    child_ready_path = tmp_path / "normal-exit-child.ready"
    child_code = (
        "import pathlib,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(child_ready_path)!r}).write_text('ready'); "
        "time.sleep(30)"
    )
    parent_code = (
        "import os,pathlib,subprocess,sys,time; "
        f"child=subprocess.Popen([sys.executable, '-c', {child_code!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL); "
        f"ready=pathlib.Path({str(child_ready_path)!r}); "
        "deadline=time.monotonic()+3; "
        "exec('while not ready.is_file() and time.monotonic() < deadline:"
        "\\n    time.sleep(0.01)'); "
        "assert ready.is_file(); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text("
        "f'{os.getpid()}:{child.pid}', encoding='utf-8')"
    )

    result = secure_process.run_bounded_process(
        [sys.executable, "-c", parent_code],
        timeout=5,
        termination_grace_seconds=0.1,
        output_limit_bytes=4096,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    parent_pid, child_pid = (
        int(value)
        for value in child_pid_path.read_text(encoding="utf-8").split(":")
    )
    deadline = time.monotonic() + 3
    while _process_is_active(child_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        assert _process_is_active(child_pid) is False
    finally:
        try:
            if os.getpgid(child_pid) == parent_pid:
                os.killpg(parent_pid, signal.SIGKILL)
        except (PermissionError, ProcessLookupError):
            pass


@pytest.mark.skipif(os.name != "posix", reason="POSIX file controls are required.")
def test_private_run_directory_is_contained_owned_and_mode_0700(
    tmp_path: Path,
) -> None:
    output_root = tmp_path.resolve()
    target = output_root / "run"

    created = secure_process.create_private_run_directory(
        target,
        allowed_root=output_root,
    )

    details = created.lstat()
    assert created == target
    assert stat.S_ISDIR(details.st_mode)
    assert details.st_uid == os.geteuid()
    assert stat.S_IMODE(details.st_mode) == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIX file controls are required.")
def test_private_run_directory_rejects_escape_before_side_effect(
    tmp_path: Path,
) -> None:
    output_root = tmp_path.resolve() / "allowed"
    output_root.mkdir(mode=0o700)
    target = tmp_path.resolve() / "outside" / "run"

    with pytest.raises(ValueError, match="outside the allowed output root"):
        secure_process.create_private_run_directory(
            target,
            allowed_root=output_root,
        )

    assert not target.exists()
    assert not target.parent.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX file controls are required.")
def test_private_run_directory_rejects_unsafe_parent_mode(
    tmp_path: Path,
) -> None:
    unsafe_root = tmp_path.resolve() / "unsafe"
    unsafe_root.mkdir(mode=0o700)
    unsafe_root.chmod(0o777)
    try:
        with pytest.raises(ValueError, match="parent with an unsafe mode"):
            secure_process.create_private_run_directory(
                unsafe_root / "run",
                allowed_root=unsafe_root,
            )
        assert not (unsafe_root / "run").exists()
    finally:
        unsafe_root.chmod(0o700)


@pytest.mark.skipif(os.name != "posix", reason="POSIX file controls are required.")
def test_private_run_directory_rejects_unsafe_parent_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secure_process.os, "geteuid", lambda: 501)
    details = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=502)

    with pytest.raises(ValueError, match="parent with an unsafe owner"):
        secure_process._validate_posix_parent(details)


@pytest.mark.skipif(os.name != "posix", reason="POSIX file controls are required.")
def test_private_run_directory_rejects_symlink_parent(tmp_path: Path) -> None:
    output_root = tmp_path.resolve()
    real_parent = output_root / "real"
    real_parent.mkdir(mode=0o700)
    linked_parent = output_root / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises((ValueError, OSError)):
        secure_process.create_private_run_directory(
            linked_parent / "run",
            allowed_root=output_root,
        )

    assert not (real_parent / "run").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX file controls are required.")
def test_posix_directory_creation_fails_without_safe_primitives(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path.resolve() / "run"
    monkeypatch.setattr(
        secure_process,
        "_posix_directory_primitives_available",
        lambda: False,
    )

    with pytest.raises(RuntimeError, match="requires POSIX no-follow"):
        secure_process.create_private_run_directory(target)

    assert not target.exists()


def test_windows_process_control_fails_before_spawn_without_job_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_called = False

    def deny_job_creation():
        raise RuntimeError("Job Objects are unavailable.")

    def unexpected_popen(*args, **kwargs):
        nonlocal popen_called
        popen_called = True
        raise AssertionError("The process must not start.")

    monkeypatch.setattr(secure_process.os, "name", "nt")
    monkeypatch.setattr(
        secure_process._WindowsJobObject,
        "create",
        staticmethod(deny_job_creation),
    )
    monkeypatch.setattr(secure_process.subprocess, "Popen", unexpected_popen)

    with pytest.raises(RuntimeError, match="Job Objects are unavailable"):
        secure_process.run_bounded_process(
            ["provider-child"],
            timeout=1,
        )

    assert popen_called is False


def test_windows_job_contains_suspended_child_and_terminates_after_normal_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    popen_options: dict[str, object] = {}

    class FakeProcess:
        pid = 42
        returncode = 0
        stdout = io.BytesIO(b"complete\n")

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            raise AssertionError("The direct child already exited.")

    class FakeJob:
        def assign(self, process):
            events.append("assign")

        def resume(self, process):
            events.append("resume")

        def terminate(self):
            events.append("terminate")

        def close(self):
            events.append("close")

    fake_job = FakeJob()

    def fake_popen(args, **kwargs):
        popen_options.update(kwargs)
        events.append("spawn")
        return FakeProcess()

    monkeypatch.setattr(secure_process.os, "name", "nt")
    monkeypatch.setattr(
        secure_process._WindowsJobObject,
        "create",
        staticmethod(lambda: fake_job),
    )
    monkeypatch.setattr(secure_process.subprocess, "Popen", fake_popen)

    result = secure_process.run_bounded_process(
        ["provider-child"],
        timeout=1,
    )

    assert result.returncode == 0
    assert result.stdout == "complete\n"
    assert events == ["spawn", "assign", "resume", "terminate", "close"]
    assert (
        int(popen_options["creationflags"])
        & secure_process._WindowsJobObject.CREATE_SUSPENDED
    )


def test_windows_job_handle_closes_when_termination_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeProcess:
        pid = 42
        returncode = 0
        stdout = io.BytesIO()

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    class FakeJob:
        def assign(self, process):
            events.append("assign")

        def resume(self, process):
            events.append("resume")

        def terminate(self):
            events.append("terminate")
            raise RuntimeError("The Job Object could not terminate.")

        def close(self):
            events.append("close")

    monkeypatch.setattr(secure_process.os, "name", "nt")
    monkeypatch.setattr(
        secure_process._WindowsJobObject,
        "create",
        staticmethod(FakeJob),
    )
    monkeypatch.setattr(
        secure_process.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(),
    )

    with pytest.raises(RuntimeError, match="could not terminate"):
        secure_process.run_bounded_process(
            ["provider-child"],
            timeout=1,
        )

    assert events == ["assign", "resume", "terminate", "close"]
