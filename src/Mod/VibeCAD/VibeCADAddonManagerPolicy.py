# SPDX-License-Identifier: LGPL-2.1-or-later
"""Install the parent-owned managed-policy guard for the Addon Manager."""

from __future__ import annotations

import functools
from types import ModuleType
from typing import Any, Callable


_GUARD_ATTRIBUTE = "__vibecad_addon_manager_policy_guard__"


def _show_blocked_warning(addon_manager: ModuleType, message: str) -> None:
    """Show the standard Addon Manager blocked message."""

    interface = addon_manager.fci
    gui = getattr(interface, "FreeCADGui", None)
    parent = gui.getMainWindow() if gui else None
    addon_manager.QtWidgets.QMessageBox.warning(
        parent,
        addon_manager.translate("AddonsInstaller", "Addon Manager Blocked"),
        message,
    )


def install_addon_manager_policy_guard(
    addon_manager: ModuleType | None = None,
    *,
    policy_loader: Callable[[], dict[str, Any]] | None = None,
    action_enforcer: Callable[[dict[str, Any], str], None] | None = None,
    warning_presenter: Callable[[str], None] | None = None,
) -> bool:
    """Guard Addon Manager activation before it can initialize its network.

    Return ``True`` when this call installs the guard. Return ``False`` when
    the command class already has the guard.
    """

    if addon_manager is None:
        import AddonManager as addon_manager

    if policy_loader is None or action_enforcer is None:
        from VibeCADManagedPolicy import enforce_action, load_managed_policy

        policy_loader = policy_loader or load_managed_policy
        action_enforcer = action_enforcer or enforce_action

    command_class = addon_manager.CommandAddonManager
    original = command_class.Activated
    if getattr(original, _GUARD_ATTRIBUTE, False):
        return False

    present_warning = warning_presenter or functools.partial(
        _show_blocked_warning, addon_manager
    )

    @functools.wraps(original)
    def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            policy = policy_loader()
            action_enforcer(policy, "external_plugin")
        except PermissionError as exc:
            present_warning(str(exc))
            return None
        except Exception as exc:
            present_warning(
                "The Addon Manager is blocked because the managed policy "
                f"is invalid or cannot be read: {exc}"
            )
            return None
        return original(self, *args, **kwargs)

    setattr(guarded, _GUARD_ATTRIBUTE, True)
    setattr(guarded, "__vibecad_original_activated__", original)
    command_class.Activated = guarded
    return True


__all__ = ["install_addon_manager_policy_guard"]
