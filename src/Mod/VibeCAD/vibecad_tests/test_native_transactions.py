# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

from types import SimpleNamespace

import FreeCAD as App

from VibeCADTransactions import run_freecad_transaction


class _Document:
    def __init__(self) -> None:
        self.Name = "TestDocument"
        self.Objects: list[SimpleNamespace] = []
        self.Recomputing = False
        self.RecomputePending = False
        self.log: list[str] = []
        self._snapshot: list[SimpleNamespace] | None = None

    def openTransaction(self, name: str) -> None:
        self.log.append(f"open:{name}")
        self._snapshot = list(self.Objects)

    def commitTransaction(self) -> None:
        self.log.append("commit")
        self._snapshot = None

    def abortTransaction(self) -> None:
        self.log.append("abort")
        self.Objects = list(self._snapshot or [])
        self._snapshot = None

    def recompute(self, *_args) -> None:
        self.log.append("recompute")

    def getObject(self, name: str):
        return next((item for item in self.Objects if item.Name == name), None)

    def getRecomputeDiagnostics(self) -> dict:
        return {"generation": len(self.log), "diagnostics": []}


def _add(doc: _Document, name: str = "Feature") -> dict:
    doc.Objects.append(SimpleNamespace(Name=name, Label=name, TypeId="App::Feature", State=[]))
    return {"created": name}


def test_valid_native_candidate_is_committed(monkeypatch) -> None:
    doc = _Document()
    monkeypatch.setattr(App, "ActiveDocument", doc, raising=False)
    result = run_freecad_transaction("Create", lambda: _add(doc))
    assert result["ok"] is True
    assert doc.log[-1] == "commit"
    assert [obj.Name for obj in doc.Objects] == ["Feature"]
    assert result["rollback_attempted"] is False


def test_failed_postcondition_aborts_candidate(monkeypatch) -> None:
    doc = _Document()
    monkeypatch.setattr(App, "ActiveDocument", doc, raising=False)
    result = run_freecad_transaction(
        "Create",
        lambda: _add(doc),
        verifier=lambda _result: {"ok": False, "checks": [{"ok": False}]},
    )
    assert result["ok"] is False
    assert result["failure_code"] == "POSTCONDITION_FAILED"
    assert result["rollback_attempted"] is True
    assert result["rollback_succeeded"] is True
    assert result["candidate_document_delta"]["created_objects"]
    assert result["document_delta"]["created_objects"] == []
    assert doc.Objects == []
    assert doc.log[-1] == "abort"


def test_native_exception_aborts_candidate(monkeypatch) -> None:
    doc = _Document()
    monkeypatch.setattr(App, "ActiveDocument", doc, raising=False)

    def fail() -> dict:
        _add(doc)
        raise RuntimeError("candidate failed")

    result = run_freecad_transaction("Create", fail)
    assert result["ok"] is False
    assert result["failure_code"] == "NATIVE_OPERATION_FAILED"
    assert result["rollback_succeeded"] is True
    assert doc.Objects == []
