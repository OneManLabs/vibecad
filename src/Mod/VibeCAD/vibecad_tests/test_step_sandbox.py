# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

import VibeCADStepSandbox as sandbox


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def test_unsupported_platform_has_a_typed_path_free_limitation(tmp_path: Path) -> None:
    status = sandbox.step_worker_sandbox_status(platform_name="linux")

    assert status == {
        "schema": sandbox.STEP_WORKER_SANDBOX_SCHEMA,
        "version": sandbox.STEP_WORKER_SANDBOX_VERSION,
        "available": False,
        "reason": "unsupported_platform",
        "network": "denied_when_available",
        "write_scope": "validator_staging_directory_when_available",
    }
    assert str(tmp_path) not in json.dumps(status)

    with pytest.raises(sandbox.StepWorkerSandboxUnavailable) as caught:
        sandbox.prepare_step_worker_sandbox(
            [sys.executable, "-c", "pass"],
            staging=_private_directory(tmp_path / "staging"),
            environment={},
            platform_name="linux",
        )
    assert caught.value.code == "STEP_VALIDATOR_SANDBOX_UNAVAILABLE"
    assert caught.value.reason == "unsupported_platform"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt test")
def test_plan_is_versioned_and_removes_loader_environment(tmp_path: Path) -> None:
    staging = _private_directory(tmp_path / "staging")
    plan = sandbox.prepare_step_worker_sandbox(
        [sys.executable, "-c", "pass"],
        staging=staging,
        environment={
            "HOME": str(staging),
            "DYLD_LIBRARY_PATH": "/untrusted/libraries",
            "DYLD_INSERT_LIBRARIES": "/untrusted/injected.dylib",
            "LD_PRELOAD": "/untrusted/preload.dylib",
            "PYTHONPATH": "/untrusted/python",
        },
        module_roots=(Path(__file__).resolve().parents[1],),
    )

    evidence = plan.provider_evidence()
    assert evidence["schema"] == sandbox.STEP_WORKER_SANDBOX_SCHEMA
    assert evidence["version"] == 1
    assert evidence["enforced"] is True
    assert evidence["network"] == "denied"
    assert evidence["write_scope"] == "validator_staging_directory_only"
    assert plan.command[0] == str(sandbox.MACOS_SANDBOX_EXECUTABLE)
    assert plan.command[-3:] == (str(Path(sys.executable).resolve()), "-c", "pass")
    assert plan.environment == {"HOME": str(staging)}
    assert "(deny network*)" in plan.profile
    assert plan.profile.count("(allow file-write*") == 1
    assert str(staging.resolve()) in plan.profile
    assert "/untrusted" not in plan.profile


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt test")
def test_adversarial_worker_cannot_escape_files_network_or_process_boundary(
    tmp_path: Path,
) -> None:
    staging = _private_directory(tmp_path / "staging")
    secret = tmp_path / "private-design.txt"
    outside_write = tmp_path / "escaped.txt"
    result = staging / "result.json"
    secret.write_text("sensitive CAD design", encoding="utf-8")

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    worker = """
import json
from pathlib import Path
import socket
import subprocess
import sys

secret = Path(sys.argv[1])
outside = Path(sys.argv[2])
result = Path(sys.argv[3])
port = int(sys.argv[4])

try:
    secret.read_bytes()
    read_denied = False
except OSError:
    read_denied = True

read_link = result.parent / "read-link"
read_link.symlink_to(secret)
try:
    read_link.read_bytes()
    linked_read_denied = False
except OSError:
    linked_read_denied = True

try:
    outside.write_text("escape", encoding="utf-8")
    write_denied = False
except OSError:
    write_denied = True

write_link = result.parent / "write-link"
write_link.symlink_to(outside)
try:
    write_link.write_text("escape", encoding="utf-8")
    linked_write_denied = False
except OSError:
    linked_write_denied = True

try:
    subprocess.run(["/usr/bin/true"], check=False)
    process_exec_denied = False
except OSError:
    process_exec_denied = True

probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
probe.settimeout(1.0)
try:
    probe.connect(("127.0.0.1", port))
    network_denied = False
except OSError:
    network_denied = True
finally:
    probe.close()

result.write_text(
    json.dumps(
        {
            "read_denied": read_denied,
            "linked_read_denied": linked_read_denied,
            "write_denied": write_denied,
            "linked_write_denied": linked_write_denied,
            "network_denied": network_denied,
            "process_exec_denied": process_exec_denied,
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
"""
    environment = {
        "HOME": str(staging),
        "LANG": "C",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "TEMP": str(staging),
        "TMP": str(staging),
        "TMPDIR": str(staging),
    }
    plan = sandbox.prepare_step_worker_sandbox(
        [
            sys.executable,
            "-I",
            "-c",
            worker,
            str(secret),
            str(outside_write),
            str(result),
            str(port),
        ],
        staging=staging,
        environment=environment,
    )
    try:
        completed = subprocess.run(
            plan.command,
            cwd=staging,
            env=dict(plan.environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15.0,
            check=False,
        )
    finally:
        listener.close()

    assert completed.returncode == 0, completed.stderr
    assert json.loads(result.read_text(encoding="utf-8")) == {
        "linked_read_denied": True,
        "linked_write_denied": True,
        "network_denied": True,
        "process_exec_denied": True,
        "read_denied": True,
        "write_denied": True,
    }
    assert outside_write.exists() is False


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt test")
def test_plan_rejects_a_staging_directory_link(tmp_path: Path) -> None:
    target = _private_directory(tmp_path / "target")
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="unsafe"):
        sandbox.prepare_step_worker_sandbox(
            [sys.executable, "-c", "pass"],
            staging=linked,
            environment={},
        )
