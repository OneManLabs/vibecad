# SPDX-License-Identifier: LGPL-2.1-or-later
"""Guard registered workbench module exports with enterprise policy and RBAC."""

from __future__ import annotations

import functools
import importlib.abc
import importlib.machinery
import re
import sys
import threading
from types import ModuleType
from typing import Any, Iterable


_TARGETS: set[str] = set()
_LOCK = threading.RLock()
_INSTALLED = False


def _authorize_export() -> None:
    from VibeCADManagedPolicy import enforce_action, load_managed_policy

    enforce_action(load_managed_policy(), "export")
    from VibeCADCore import get_service

    get_service().authorize("export")


def guard_export_module(module: ModuleType) -> bool:
    export = getattr(module, "export", None)
    if not callable(export) or getattr(export, "__vibecad_export_guard__", False):
        return False

    @functools.wraps(export)
    def guarded(*args: Any, **kwargs: Any):
        _authorize_export()
        return export(*args, **kwargs)

    guarded.__vibecad_export_guard__ = True
    module.export = guarded
    return True


class _GuardLoader(importlib.abc.Loader):
    def __init__(self, loader: importlib.abc.Loader) -> None:
        self.loader = loader

    def create_module(self, spec):
        create = getattr(self.loader, "create_module", None)
        return create(spec) if create else None

    def exec_module(self, module) -> None:
        self.loader.exec_module(module)
        guard_export_module(module)


class _GuardFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        with _LOCK:
            if fullname not in _TARGETS:
                return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is None or spec.loader is None or isinstance(spec.loader, _GuardLoader):
            return spec
        if not hasattr(spec.loader, "exec_module"):
            return spec
        spec.loader = _GuardLoader(spec.loader)
        return spec


_FINDER = _GuardFinder()


def install_export_guards(module_names: Iterable[str]) -> set[str]:
    global _INSTALLED
    clean = {
        str(name).strip() for name in module_names
        if isinstance(name, str)
        and re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", str(name).strip())
    }
    with _LOCK:
        _TARGETS.update(clean)
        if not _INSTALLED:
            sys.meta_path.insert(0, _FINDER)
            _INSTALLED = True
        for name in clean:
            module = sys.modules.get(name)
            if isinstance(module, ModuleType):
                guard_export_module(module)
        return set(_TARGETS)


def refresh_export_guards(application=None) -> set[str]:
    if application is None:
        import FreeCAD as application

    registered = application.getExportType()
    names: set[str] = set()
    if isinstance(registered, dict):
        for value in registered.values():
            if isinstance(value, str):
                names.add(value)
            elif isinstance(value, (list, tuple)):
                names.update(item for item in value if isinstance(item, str))
    return install_export_guards(names)
