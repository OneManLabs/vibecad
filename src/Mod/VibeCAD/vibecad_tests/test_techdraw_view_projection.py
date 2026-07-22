# SPDX-License-Identifier: LGPL-2.1-or-later

import sys
from types import SimpleNamespace

from tool_impl.service import techdraw_add_view


def test_projection_recompute_restores_user_update_preferences(monkeypatch):
    class Preferences:
        value = False

        def GetBool(self, _name, _default):
            return self.value

        def SetBool(self, _name, value):
            self.value = bool(value)

    preferences = Preferences()
    monkeypatch.setitem(
        sys.modules,
        "FreeCAD",
        SimpleNamespace(ParamGet=lambda _path: preferences),
    )
    page = SimpleNamespace(KeepUpdated=False)
    observations = []
    document = SimpleNamespace(
        recompute=lambda: observations.append(
            (preferences.value, page.KeepUpdated)
        )
    )

    techdraw_add_view._recompute_page_projection(document, page)

    assert observations == [(True, True)]
    assert preferences.value is False
    assert page.KeepUpdated is False


def test_projection_wait_processes_events_until_hlr_finishes(monkeypatch):
    calls = []

    def inventory(_view):
        calls.append(len(calls))
        if len(calls) < 3:
            return {"ok": False, "error": "not ready"}
        return {"ok": True, "edge_count": 4}

    events = []
    qt_core = SimpleNamespace(
        QCoreApplication=SimpleNamespace(
            processEvents=lambda flags, duration: events.append((flags, duration))
        ),
        QEventLoop=SimpleNamespace(
            ProcessEventsFlag=SimpleNamespace(AllEvents="all")
        ),
    )
    monkeypatch.setattr(
        techdraw_add_view, "_projected_element_inventory", inventory
    )
    monkeypatch.setitem(sys.modules, "PySide", SimpleNamespace(QtCore=qt_core))

    result = techdraw_add_view._wait_for_projected_elements(object(), 0.2)

    assert result == {"ok": True, "edge_count": 4}
    assert events == [("all", 20), ("all", 20)]
