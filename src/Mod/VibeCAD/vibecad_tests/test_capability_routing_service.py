# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from VibeCADCapabilityRouter import LEGACY_ROUTER_SCHEMA, ROUTER_SCHEMA
from VibeCADCore import VibeCADService
from VibeCADProject import VibeCADProjectStore


def _scope(root: Path) -> dict:
    return {
        "project_id": "routing-project",
        "title": "Routing project",
        "root": str(root),
        "manifest_path": str(root / "project.vibecad.json"),
        "persistent": True,
        "document_saved": True,
        "document": {
            "document": "Routing",
            "label": "Routing",
            "file_path": str(root.parent / "Routing.FCStd"),
            "saved": True,
        },
        "index_path": str(root.parent / "index.sqlite"),
    }


def test_project_store_persists_and_clears_modeling_strategy_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope(tmp_path / "project")
    store = VibeCADProjectStore("routing-session", index_path=tmp_path / "index.sqlite")
    monkeypatch.setattr(store, "project_scope", lambda: dict(scope))

    assert store.modeling_strategy_lock() is None
    store.set_modeling_strategy_lock("build123d")
    assert store.modeling_strategy_lock() == "build123d"
    assert store.context()["modeling_strategy_lock"] == "build123d"

    store.set_modeling_strategy_lock(None)
    assert store.modeling_strategy_lock() is None
    with pytest.raises(ValueError, match="lock"):
        store.set_modeling_strategy_lock("unknown")


def test_project_store_accepts_and_migrates_version_one_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope(tmp_path / "project")
    store = VibeCADProjectStore("routing-session", index_path=tmp_path / "index.sqlite")
    monkeypatch.setattr(store, "project_scope", lambda: dict(scope))
    legacy = {
        "schema": LEGACY_ROUTER_SCHEMA,
        "version": 1,
        "route_id": "legacy-project-route",
        "engine": "native",
        "workbench": "PartDesignWorkbench",
        "reason_code": "native_editability_default",
        "explanation": "Legacy route.",
        "preserved_existing_structure": False,
        "automatic": True,
    }

    saved = store.set_capability_route(legacy)
    assert saved["schema"] == ROUTER_SCHEMA
    assert saved["evidence"]["legacy_route_id"] == "legacy-project-route"
    assert store.last_capability_route() == saved


class _RouteStore:
    def __init__(self, lock: str | None = None) -> None:
        self.lock = lock
        self.route: dict | None = None

    def modeling_strategy_lock(self) -> str | None:
        return self.lock

    def set_capability_route(self, route: dict) -> dict:
        self.route = dict(route)
        return dict(route)


def _service_for_route(monkeypatch: pytest.MonkeyPatch, *, lock: str | None = None):
    service = object.__new__(VibeCADService)
    service._last_capability_route = None
    service._project_store = _RouteStore(lock)
    service._active_document = lambda: SimpleNamespace(Objects=[])
    service.provider_turn_selection_summary = lambda: {
        "selection_count": 1,
        "selection": [{"object": "Pad", "subelements": ["Face1"]}],
    }
    service.design_brief = lambda: {"manufacturing_process": "unspecified"}
    service._routing_document_structure = lambda: {
        "object_count": 1,
        "has_geometry": True,
        "type_ids": ["PartDesign::Feature"],
        "detected_engines": ["native"],
        "established_engine": "native",
        "compatible_capabilities": ["part_edit"],
    }
    service.modeling_engine = lambda: "native"
    service.set_modeling_engine = lambda engine: pytest.fail(
        f"Unexpected modeling-engine change to {engine}."
    )
    activated: list[str] = []
    monkeypatch.setattr(service, "_activate_routed_workbench", activated.append)
    return service, activated


def test_service_applies_internal_workbench_target_and_records_exact_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, activated = _service_for_route(monkeypatch)
    service.active_workbench_name = lambda: "PartDesignWorkbench"
    requested_workbenches: list[str] = []

    def state(workbench: str) -> dict:
        requested_workbenches.append(workbench)
        return {"selected": "native", "available_engines": ["native", "vibescript"]}

    service.modeling_engine_state = state
    route = service.route_modeling_strategy(
        "Create a dimensioned drawing", capability_category="drawing"
    )

    assert requested_workbenches == ["TechDrawWorkbench"]
    assert activated == ["TechDrawWorkbench"]
    assert route["engine"] == "native"
    assert route["target_workbench"] == "TechDrawWorkbench"
    assert route["request"]["selection_context"]["selection"][0]["object"] == "Pad"
    assert route["evidence"]["manufacturing_intent_present"] is False
    assert service._project_store.route == route


def test_service_preserves_professional_workbench_for_compatible_follow_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, activated = _service_for_route(monkeypatch)
    service.active_workbench_name = lambda: "AssemblyWorkbench"
    service._routing_document_structure = lambda: {
        "object_count": 2,
        "has_geometry": True,
        "type_ids": ["App::Part"],
        "detected_engines": ["native"],
        "established_engine": "native",
        "compatible_capabilities": ["part_edit"],
    }
    requested_workbenches: list[str] = []
    service.modeling_engine_state = lambda workbench: (
        requested_workbenches.append(workbench)
        or {"selected": "native", "available_engines": ["native", "vibescript"]}
    )

    route = service.route_modeling_strategy("Move this component 5 mm")

    assert requested_workbenches == ["AssemblyWorkbench"]
    assert activated == ["AssemblyWorkbench"]
    assert route["target_workbench"] == "AssemblyWorkbench"
    assert route["preserved_existing_structure"] is True
