# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tool_impl.service import project_export


class Shape:
    def isNull(self):
        return False

    def isValid(self):
        return True


class Service:
    def __init__(self, root: Path):
        self.root = root
        self.obj = SimpleNamespace(Name="Printable", Shape=Shape())
        self.events = []
        self.permissions = []

    def authorize(self, permission):
        self.permissions.append(permission)

    def _active_document(self):
        return SimpleNamespace(getObject=lambda name: self.obj if name == self.obj.Name else None)

    def project_scope_snapshot(self):
        return {"root": str(self.root)}

    def record_audit_event(self, **event):
        self.events.append(event)


def _allow_policy(monkeypatch):
    import VibeCADManagedPolicy as policy

    monkeypatch.setattr(policy, "load_managed_policy", lambda: {})
    monkeypatch.setattr(policy, "enforce_action", lambda current, action: None)


def test_stl_export_is_project_scoped_content_bound_and_non_overwriting(tmp_path, monkeypatch) -> None:
    _allow_policy(monkeypatch)
    monkeypatch.setitem(
        sys.modules, "Mesh",
        SimpleNamespace(export=lambda objects, path: Path(path).write_bytes(b"solid exported\nendsolid\n")),
    )
    service = Service(tmp_path)

    result = project_export.run(service, ["Printable"], "stl", "printable-cube")

    assert result["ok"] is True
    target = tmp_path / "exports" / "printable-cube.stl"
    assert target.is_file()
    assert result["export"]["path"] == str(target)
    assert len(result["export"]["sha256"]) == 64
    assert service.permissions == ["export"]
    assert service.events[0]["action"] == "export"
    second = project_export.run(service, ["Printable"], "stl", "printable-cube")
    assert second["ok"] is False
    assert "already exists" in second["error"]


@pytest.mark.parametrize("format", ["stl", "3mf", "obj"])
def test_mesh_formats_use_mesh_export_with_exact_extension(
    tmp_path, monkeypatch, format
) -> None:
    _allow_policy(monkeypatch)
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "Mesh",
        SimpleNamespace(
            export=lambda objects, path: (
                calls.append((objects, path)), Path(path).write_bytes(b"mesh")
            )[-1]
        ),
    )

    result = project_export.run(Service(tmp_path), ["Printable"], format, "part")

    assert result["ok"] is True
    assert Path(result["export"]["path"]).suffix == f".{format}"
    assert Path(calls[0][1]).suffix == f".{format}"


def test_ready_native_mesh_can_export_without_a_shape(tmp_path, monkeypatch) -> None:
    _allow_policy(monkeypatch)
    service = Service(tmp_path)
    service.obj = SimpleNamespace(Name="Printable", Mesh=object())
    monkeypatch.setattr(
        project_export, "analyze_mesh", lambda _mesh: {"verdict": "ready"}
    )
    monkeypatch.setitem(
        sys.modules, "Mesh",
        SimpleNamespace(export=lambda _objects, path: Path(path).write_bytes(b"mesh")),
    )

    result = project_export.run(service, ["Printable"], "3mf", "mesh-part")

    assert result["ok"] is True


def test_defective_native_mesh_is_not_exported(tmp_path, monkeypatch) -> None:
    _allow_policy(monkeypatch)
    service = Service(tmp_path)
    service.obj = SimpleNamespace(Name="Printable", Mesh=object())
    monkeypatch.setattr(
        project_export, "analyze_mesh", lambda _mesh: {"verdict": "not_ready"}
    )

    result = project_export.run(service, ["Printable"], "stl", "bad-mesh")

    assert result["ok"] is False
    assert not (tmp_path / "exports").exists()


@pytest.mark.parametrize("format", ["step", "iges"])
def test_neutral_formats_use_part_export_with_exact_extension(
    tmp_path, monkeypatch, format
) -> None:
    _allow_policy(monkeypatch)
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "Part",
        SimpleNamespace(
            export=lambda objects, path: (
                calls.append((objects, path)), Path(path).write_bytes(b"brep")
            )[-1]
        ),
    )

    result = project_export.run(Service(tmp_path), ["Printable"], format, "part")

    assert result["ok"] is True
    assert Path(result["export"]["path"]).suffix == f".{format}"
    assert Path(calls[0][1]).suffix == f".{format}"


def test_missing_object_creates_no_export(tmp_path, monkeypatch) -> None:
    _allow_policy(monkeypatch)
    service = Service(tmp_path)
    result = project_export.run(service, ["Missing"], "stl", "missing")
    assert result["ok"] is False
    assert not (tmp_path / "exports").exists()


def test_policy_denial_happens_before_export_or_directory_creation(tmp_path, monkeypatch) -> None:
    import VibeCADManagedPolicy as policy

    monkeypatch.setattr(policy, "load_managed_policy", lambda: {"export_allowed": False})
    monkeypatch.setattr(
        policy, "enforce_action",
        lambda current, action: (_ for _ in ()).throw(PermissionError("blocked")),
    )
    with pytest.raises(PermissionError, match="blocked"):
        project_export.run(Service(tmp_path), ["Printable"], "stl", "blocked")
    assert not (tmp_path / "exports").exists()


@pytest.mark.parametrize("name", ["../escape", "/absolute", "name with spaces", ""])
def test_file_name_rejects_paths_and_nonportable_text(tmp_path, monkeypatch, name) -> None:
    _allow_policy(monkeypatch)
    with pytest.raises(ValueError, match="portable"):
        project_export.run(Service(tmp_path), ["Printable"], "stl", name)
    assert not (tmp_path / "exports").exists()
