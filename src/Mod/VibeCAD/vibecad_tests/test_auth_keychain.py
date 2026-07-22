# SPDX-License-Identifier: LGPL-2.1-or-later

"""Tests for bounded provider credential reads."""

from types import SimpleNamespace
import subprocess
from unittest import mock

import pytest

import VibeCADAuth


def test_macos_keychain_read_returns_credential_without_logging_it():
    runner = mock.Mock(
        return_value=SimpleNamespace(returncode=0, stdout="test-secret\n", stderr="")
    )

    result = VibeCADAuth.read_keyring_key(
        platform_name="darwin", runner=runner, timeout_seconds=2.0
    )

    assert result == "test-secret"
    command = runner.call_args.args[0]
    assert command == [
        "/usr/bin/security",
        "find-generic-password",
        "-s",
        VibeCADAuth.KEYRING_SERVICE,
        "-a",
        VibeCADAuth.KEYRING_USERNAME,
        "-w",
    ]
    assert runner.call_args.kwargs["stdin"] is subprocess.DEVNULL
    assert runner.call_args.kwargs["timeout"] == 2.0


@pytest.mark.parametrize(
    ("returncode", "stderr"),
    [(44, ""), (1, "The specified item could not be found in the keychain.")],
)
def test_macos_keychain_read_returns_none_when_credential_is_missing(
    returncode, stderr
):
    runner = mock.Mock(
        return_value=SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)
    )

    assert (
        VibeCADAuth.read_keyring_key(platform_name="darwin", runner=runner) is None
    )


def test_macos_keychain_read_has_a_bounded_timeout():
    runner = mock.Mock(side_effect=subprocess.TimeoutExpired("security", 1.0))

    with pytest.raises(RuntimeError, match="Keychain lookup timed out") as error:
        VibeCADAuth.read_keyring_key(
            platform_name="darwin", runner=runner, timeout_seconds=1.0
        )

    assert "secret" not in str(error.value).lower()


def test_macos_keychain_read_reports_failure_without_stderr_or_secret():
    runner = mock.Mock(
        return_value=SimpleNamespace(
            returncode=36, stdout="test-secret", stderr="sensitive diagnostic"
        )
    )

    with pytest.raises(RuntimeError) as error:
        VibeCADAuth.read_keyring_key(platform_name="darwin", runner=runner)

    message = str(error.value)
    assert "test-secret" not in message
    assert "sensitive diagnostic" not in message


def test_non_macos_keyring_adapter_is_preserved(monkeypatch):
    adapter = mock.Mock()
    adapter.get_password.return_value = "portable-secret"
    monkeypatch.setattr(VibeCADAuth, "_keyring_module", lambda: adapter)

    result = VibeCADAuth.read_keyring_key(platform_name="linux")

    assert result == "portable-secret"
    adapter.get_password.assert_called_once_with(
        VibeCADAuth.KEYRING_SERVICE, VibeCADAuth.KEYRING_USERNAME
    )
