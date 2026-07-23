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


def test_managed_property_change_has_exact_provenance(monkeypatch) -> None:
    doc = _Document()
    view = SimpleNamespace(
        Name="VibeCADExplodedView",
        Label="Exploded view",
        TypeId="App::FeaturePython",
        State=[],
        PropertiesList=["VibeCADExplodedViewState"],
        VibeCADExplodedViewState="exploded",
    )
    doc.Objects.append(view)
    monkeypatch.setattr(App, "ActiveDocument", doc, raising=False)

    def restore_metadata() -> dict:
        view.VibeCADExplodedViewState = "assembled"
        return {"view": view.Name}

    result = run_freecad_transaction("Restore", restore_metadata)

    assert result["ok"] is True
    assert [item["name"] for item in result["document_delta"]["changed_objects"]] == [
        "VibeCADExplodedView"
    ]
    before, after = (
        result["document_delta"]["changed_objects"][0][key]
        for key in ("before", "after")
    )
    assert before["vibecad_properties"]["VibeCADExplodedViewState"] == "exploded"
    assert after["vibecad_properties"]["VibeCADExplodedViewState"] == "assembled"


def test_native_abort_does_not_claim_success_when_properties_remain(monkeypatch) -> None:
    doc = _Document()
    view = SimpleNamespace(
        Name="ManagedView",
        Label="Managed view",
        TypeId="App::FeaturePython",
        State=[],
        PropertiesList=["VibeCADState"],
        VibeCADState="accepted",
    )
    doc.Objects.append(view)
    monkeypatch.setattr(App, "ActiveDocument", doc, raising=False)

    def fail_after_change() -> dict:
        view.VibeCADState = "candidate"
        raise RuntimeError("injected failure")

    result = run_freecad_transaction("Fail", fail_after_change)

    assert result["ok"] is False
    assert result["rollback_attempted"] is True
    assert result["rollback_succeeded"] is False
    assert result["failure_code"] == "TRANSACTION_ROLLBACK_FAILED"
    assert result["operation_error"] == "injected failure"
    assert "left document changes" in result["rollback_error"]
    assert result["document_delta"]["changed_objects"]


def test_compensating_rollback_is_verified_against_snapshot(monkeypatch) -> None:
    doc = _Document()
    view = SimpleNamespace(
        Name="ManagedView",
        Label="Managed view",
        TypeId="App::FeaturePython",
        State=[],
        PropertiesList=["VibeCADState"],
        VibeCADState="accepted",
    )
    doc.Objects.append(view)
    monkeypatch.setattr(App, "ActiveDocument", doc, raising=False)

    def fail_after_change() -> dict:
        view.VibeCADState = "candidate"
        raise RuntimeError("injected failure")

    result = run_freecad_transaction(
        "Fail safely",
        fail_after_change,
        rollback_handler=lambda: setattr(view, "VibeCADState", "accepted"),
    )

    assert result["ok"] is False
    assert result["rollback_attempted"] is True
    assert result["rollback_succeeded"] is True
    assert result["rollback_error"] is None
    assert result["document_delta"]["changed_objects"] == []


def test_compensation_runs_after_native_abort_raises(monkeypatch) -> None:
    doc = _Document()
    view = SimpleNamespace(
        Name="ManagedView",
        Label="Managed view",
        TypeId="App::FeaturePython",
        State=[],
        PropertiesList=["VibeCADState"],
        VibeCADState="accepted",
    )
    doc.Objects.append(view)
    monkeypatch.setattr(App, "ActiveDocument", doc, raising=False)

    def abort_failure() -> None:
        doc.log.append("abort")
        raise RuntimeError("native abort failed")

    monkeypatch.setattr(doc, "abortTransaction", abort_failure)

    def fail_after_change() -> dict:
        view.VibeCADState = "candidate"
        raise RuntimeError("candidate failed")

    result = run_freecad_transaction(
        "Fail safely",
        fail_after_change,
        rollback_handler=lambda: setattr(view, "VibeCADState", "accepted"),
    )

    assert result["ok"] is False
    assert result["rollback_attempted"] is True
    assert result["rollback_succeeded"] is False
    assert result["failure_code"] == "TRANSACTION_ROLLBACK_FAILED"
    assert result["operation_error"] == "candidate failed"
    assert result["rollback_error"] == "native abort failed"
    assert result["document_delta"]["changed_objects"] == []


def test_active_document_switch_cannot_redirect_transaction_state(monkeypatch) -> None:
    original = _Document()
    original.Name = "OriginalDocument"
    other = _Document()
    other.Name = "OtherDocument"
    other.Objects.append(
        SimpleNamespace(
            Name="OtherMarker",
            Label="Other marker",
            TypeId="App::Feature",
            State=[],
        )
    )
    monkeypatch.setattr(App, "ActiveDocument", original, raising=False)

    def switch_after_mutation() -> dict:
        result = _add(original, "CandidateFeature")
        App.ActiveDocument = other
        return result

    result = run_freecad_transaction("Pinned document", switch_after_mutation)

    assert result["ok"] is False
    assert result["failure_code"] == "NATIVE_OPERATION_FAILED"
    assert "active CAD document changed" in result["operation_error"]
    assert result["rollback_attempted"] is True
    assert result["rollback_succeeded"] is True
    assert [
        item["name"]
        for item in result["candidate_document_delta"]["created_objects"]
    ] == ["CandidateFeature"]
    assert result["document_delta"]["created_objects"] == []
    assert original.Objects == []
    assert [obj.Name for obj in other.Objects] == ["OtherMarker"]
    assert original.log[-1] == "abort"
