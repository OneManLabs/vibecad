# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import types

import pytest

import VibeCADExportGuard as guard
import VibeCADManagedPolicy as policy_module
from VibeCADManagedPolicy import default_policy


def test_export_authorization_fails_before_workbench_export(monkeypatch) -> None:
    policy = default_policy()
    policy.update(managed=True, export_enabled=False)
    monkeypatch.setattr(policy_module, "load_managed_policy", lambda: policy)
    with pytest.raises(PermissionError, match="organization policy"):
        guard._authorize_export()


def test_loaded_registered_export_module_is_wrapped(monkeypatch) -> None:
    calls = []
    module = types.ModuleType("VibeCADTestLoadedExporter")
    module.export = lambda value: calls.append(value) or "exported"
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(guard, "_authorize_export", lambda: calls.append("authorized"))
    guard.install_export_guards([module.__name__])
    assert module.export("part") == "exported"
    assert calls == ["authorized", "part"]
    assert module.export.__vibecad_export_guard__ is True


def test_late_registered_export_module_is_guarded_on_import(monkeypatch, tmp_path: Path) -> None:
    name = "VibeCADTestLateExporter"
    (tmp_path / f"{name}.py").write_text(
        "calls = []\ndef export(value):\n    calls.append(value)\n    return 'late'\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, name, raising=False)
    authorizations = []
    monkeypatch.setattr(guard, "_authorize_export", lambda: authorizations.append(True))
    guard.install_export_guards([name])
    importlib.invalidate_caches()
    module = importlib.import_module(name)
    assert module.export("shape") == "late"
    assert authorizations == [True]
    assert module.calls == ["shape"]


def test_refresh_uses_all_registered_export_handler_names(monkeypatch) -> None:
    application = types.SimpleNamespace(
        getExportType=lambda: {
            "STEP (*.step)": "ImportGui",
            "Mesh (*.stl)": ["Mesh", "MeshGui"],
        }
    )
    targets = guard.refresh_export_guards(application)
    assert {"ImportGui", "Mesh", "MeshGui"}.issubset(targets)
