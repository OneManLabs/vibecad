# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

from types import SimpleNamespace

from VibeCADAddonManagerPolicy import install_addon_manager_policy_guard


def _addon_manager_module(events, *, result="opened"):
    class CommandAddonManager:
        def Activated(self):
            events.append("network")
            return result

    return SimpleNamespace(CommandAddonManager=CommandAddonManager)


def test_guard_enforces_external_plugin_before_original_activation() -> None:
    events = []
    module = _addon_manager_module(events)

    def load_policy():
        events.append("load")
        return {"managed": False}

    def enforce_action(policy, action):
        events.append(("enforce", policy, action))

    assert install_addon_manager_policy_guard(
        module,
        policy_loader=load_policy,
        action_enforcer=enforce_action,
        warning_presenter=lambda message: events.append(("warning", message)),
    )
    assert module.CommandAddonManager().Activated() == "opened"
    assert events == [
        "load",
        ("enforce", {"managed": False}, "external_plugin"),
        "network",
    ]


def test_guard_installation_is_idempotent() -> None:
    events = []
    module = _addon_manager_module(events)

    def load_policy():
        events.append("load")
        return {}

    def enforce_action(_policy, action):
        events.append(action)

    arguments = {
        "policy_loader": load_policy,
        "action_enforcer": enforce_action,
        "warning_presenter": events.append,
    }
    assert install_addon_manager_policy_guard(module, **arguments)
    assert not install_addon_manager_policy_guard(module, **arguments)
    module.CommandAddonManager().Activated()
    assert events == ["load", "external_plugin", "network"]


def test_guard_blocks_policy_denial_and_shows_clear_warning() -> None:
    events = []
    module = _addon_manager_module(events)

    def deny(_policy, _action):
        events.append("enforce")
        raise PermissionError(
            "The external plugin action is blocked by organization policy."
        )

    install_addon_manager_policy_guard(
        module,
        policy_loader=lambda: events.append("load") or {},
        action_enforcer=deny,
        warning_presenter=lambda message: events.append(("warning", message)),
    )
    assert module.CommandAddonManager().Activated() is None
    assert events == [
        "load",
        "enforce",
        (
            "warning",
            "The external plugin action is blocked by organization policy.",
        ),
    ]


def test_guard_fails_closed_when_managed_policy_is_invalid() -> None:
    events = []
    module = _addon_manager_module(events)

    def invalid_policy():
        events.append("load")
        raise RuntimeError("The managed policy schema is invalid.")

    install_addon_manager_policy_guard(
        module,
        policy_loader=invalid_policy,
        action_enforcer=lambda _policy, _action: events.append("enforce"),
        warning_presenter=lambda message: events.append(("warning", message)),
    )
    assert module.CommandAddonManager().Activated() is None
    assert events[0] == "load"
    assert events[1][0] == "warning"
    assert "blocked" in events[1][1]
    assert "invalid" in events[1][1]
    assert "network" not in events


def test_default_warning_uses_addon_manager_title_and_parent() -> None:
    events = []
    parent = object()
    module = _addon_manager_module(events)
    module.fci = SimpleNamespace(
        FreeCADGui=SimpleNamespace(getMainWindow=lambda: parent)
    )
    module.translate = lambda context, text: f"{context}:{text}"
    module.QtWidgets = SimpleNamespace(
        QMessageBox=SimpleNamespace(
            warning=lambda *args: events.append(("warning", args))
        )
    )

    install_addon_manager_policy_guard(
        module,
        policy_loader=lambda: {},
        action_enforcer=lambda _policy, _action: (_ for _ in ()).throw(
            PermissionError("Blocked by the managed policy.")
        ),
    )
    module.CommandAddonManager().Activated()
    assert events == [
        (
            "warning",
            (
                parent,
                "AddonsInstaller:Addon Manager Blocked",
                "Blocked by the managed policy.",
            ),
        )
    ]
