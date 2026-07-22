# SPDX-License-Identifier: LGPL-2.1-or-later

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tool_impl.service import project_export_drawing


class Service:
    def __init__(self, root: Path):
        self.root = root
        self.page = SimpleNamespace(Name="Page", TypeId="TechDraw::DrawPage")
        self.permissions = []
        self.events = []

    def authorize(self, permission):
        self.permissions.append(permission)

    def _active_document(self):
        return SimpleNamespace(
            getObject=lambda name: self.page if name == self.page.Name else None
        )

    def project_scope_snapshot(self):
        return {"root": str(self.root)}

    def record_audit_event(self, **event):
        self.events.append(event)


def _allow_policy(monkeypatch):
    import VibeCADManagedPolicy as policy

    monkeypatch.setattr(policy, "load_managed_policy", lambda: {})
    monkeypatch.setattr(policy, "enforce_action", lambda current, action: None)


@pytest.mark.parametrize("format", ["pdf", "svg"])
def test_gui_drawing_formats_are_project_scoped_and_non_overwriting(
    tmp_path, monkeypatch, format
):
    _allow_policy(monkeypatch)
    calls = []
    gui = SimpleNamespace(
        exportPageAsPdf=lambda page, path: (
            calls.append(("pdf", page.Name, path)), Path(path).write_bytes(b"%PDF")
        )[-1],
        exportPageAsSvg=lambda page, path: (
            calls.append(("svg", page.Name, path)), Path(path).write_text("<svg/>")
        )[-1],
    )
    monkeypatch.setitem(sys.modules, "TechDrawGui", gui)
    service = Service(tmp_path)

    result = project_export_drawing.run(service, "Page", format, "drawing")

    target = tmp_path / "exports" / f"drawing.{format}"
    assert result["ok"] is True and target.is_file()
    assert calls[0][0] == format
    assert service.permissions == ["export"]
    assert service.events[0]["action"] == "export_drawing"
    blocked = project_export_drawing.run(service, "Page", format, "drawing")
    assert blocked["ok"] is False and "already exists" in blocked["error"]


def test_dxf_uses_headless_techdraw_page_export(tmp_path, monkeypatch):
    _allow_policy(monkeypatch)
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "TechDraw",
        SimpleNamespace(
            writeDXFPage=lambda page, path: (
                calls.append((page.Name, path)), Path(path).write_text("0\nSECTION\n0\nEOF\n")
            )[-1]
        ),
    )

    result = project_export_drawing.run(Service(tmp_path), "Page", "dxf", "drawing")

    assert result["ok"] is True
    assert Path(result["export"]["path"]).read_text().endswith("EOF\n")
    assert calls[0][0] == "Page"


def test_drawing_export_requires_exact_page_and_portable_name(tmp_path, monkeypatch):
    _allow_policy(monkeypatch)
    service = Service(tmp_path)

    missing = project_export_drawing.run(service, "Missing", "pdf", "drawing")
    assert missing["ok"] is False
    with pytest.raises(ValueError, match="portable"):
        project_export_drawing.run(service, "Page", "pdf", "../drawing")
    assert not (tmp_path / "exports").exists()
