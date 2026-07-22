# SPDX-License-Identifier: LGPL-2.1-or-later
from dataclasses import replace
import hashlib
import json

import pytest

from VibeCADCapabilityRouter import (
    LEGACY_ROUTER_SCHEMA,
    ROUTER_SCHEMA,
    ROUTER_VERSION,
    ROUTING_REQUEST_SCHEMA,
    CapabilityRoutingRequest,
    infer_capability_category,
    make_routing_request,
    normalize_route_record,
    route_capability,
)


def test_existing_document_keeps_its_compatible_established_engine():
    route = route_capability(
        "Make the wall thicker",
        workbench="PartDesignWorkbench",
        current_engine="build123d",
        available_engines=["native", "build123d"],
        has_existing_geometry=True,
    )
    assert route.schema == ROUTER_SCHEMA
    assert route.version == ROUTER_VERSION
    assert route.engine == "build123d"
    assert route.preserved_existing_structure is True
    assert route.evidence["decision_factor"] == "compatible_established_engine"


def test_unrelated_existing_geometry_cannot_override_native_professional_route():
    route = route_capability(
        "Create a dimensioned manufacturing drawing",
        workbench="PartDesignWorkbench",
        current_engine="build123d",
        available_engines=["native", "build123d"],
        has_existing_geometry=True,
    )
    assert route.engine == "native"
    assert route.target_workbench == "TechDrawWorkbench"
    assert route.reason_code == "professional_native_capability"
    assert route.preserved_existing_structure is False


def test_missing_reliability_uses_safe_native_default_for_new_functional_part():
    route = route_capability(
        "Create an electronics enclosure with a removable lid",
        workbench="PartDesignWorkbench",
        current_engine="vibescript",
        available_engines=["native", "build123d", "vibescript"],
        has_existing_geometry=False,
    )
    assert route.engine == "native"
    assert route.reason_code == "native_editability_default"
    assert route.evidence["reliability"]["native"] == {
        "score": 1.0,
        "source": "safe_native_default",
    }
    assert route.evidence["reliability"]["build123d"]["source"] == "missing_data"


def test_explicit_deterministic_reliability_can_select_build123d():
    route = route_capability(
        "Create an electronics enclosure",
        workbench="PartDesignWorkbench",
        current_engine="native",
        available_engines=["native", "build123d"],
        reliability={
            "native": {"score": 0.86, "source": "benchmark-2026-07"},
            "build123d": {"score": 0.94, "source": "benchmark-2026-07"},
        },
    )
    assert route.engine == "build123d"
    assert route.reason_code == "reliable_functional_part"
    assert route.evidence["decision_factor"] == "explicit_reliability_advantage"


def test_structured_request_records_selection_manufacturing_and_structure():
    request = make_routing_request(
        "Make this face longer",
        workbench="PartDesignWorkbench",
        current_engine="native",
        available_engines=["native", "vibescript"],
        selection_context={
            "selection_count": 1,
            "selection": [{"object": "Pad", "subelements": ["Face1"]}],
        },
        manufacturing_intent={"process": "FDM"},
        existing_document_structure={
            "has_geometry": True,
            "established_engine": "native",
            "compatible_capabilities": ["part_edit"],
            "type_ids": ["PartDesign::Feature"],
        },
    )
    route = route_capability(request)
    assert request.schema == ROUTING_REQUEST_SCHEMA
    assert route.request["selection_context"]["selection"][0]["object"] == "Pad"
    assert route.request["manufacturing_intent"] == {"process": "FDM"}
    assert route.request["existing_document_structure"]["type_ids"] == [
        "PartDesign::Feature"
    ]
    assert route.evidence["selection_present"] is True


def test_structured_cam_intent_precedes_generic_selected_edit():
    category = infer_capability_category(
        "Change this selected face",
        selection_context={"selection_count": 1},
        manufacturing_intent={"process": "CNC milling"},
    )
    assert category == "cam"
    route = route_capability(
        "Change this selected face",
        workbench="PartDesignWorkbench",
        current_engine="build123d",
        available_engines=["native", "build123d"],
        has_existing_geometry=True,
        selection_context={"selection_count": 1},
        manufacturing_intent={"process": "CNC milling"},
    )
    assert route.engine == "native"
    assert route.target_workbench == "CAMWorkbench"


def test_camera_mount_is_functional_part_and_real_cam_phrases_remain_cam():
    camera = route_capability(
        "Create a camera mounting plate",
        workbench="PartDesignWorkbench",
        current_engine="native",
        available_engines=["native"],
    )
    assert camera.request["capability_category"] == "functional_part"
    assert camera.target_workbench == "PartDesignWorkbench"
    assert infer_capability_category("Create a CNC toolpath") == "cam"
    assert infer_capability_category("Create a CAM job") == "cam"


def test_scripted_part_hints_do_not_match_inside_larger_words():
    assert infer_capability_category("Change the material amount") == "part_edit"
    assert infer_capability_category("Create a mounting bracket") == "functional_part"


def test_compatible_follow_up_preserves_established_professional_workbench():
    route = route_capability(
        "Move this component 5 mm",
        workbench="AssemblyWorkbench",
        current_engine="native",
        available_engines=["native", "vibescript"],
        has_existing_geometry=True,
        selection_context={"selection_count": 1},
        manufacturing_intent={},
    )
    assert route.engine == "native"
    assert route.target_workbench == "AssemblyWorkbench"
    assert route.preserved_existing_structure is True
    assert route.evidence["manufacturing_intent_present"] is False


def test_default_is_native_and_route_identity_is_deterministic():
    args = dict(
        request="Create a 20 mm cube",
        workbench="PartDesignWorkbench",
        current_engine="vibescript",
        available_engines=["native", "vibescript"],
        has_existing_geometry=False,
    )
    first = route_capability(**args)
    second = route_capability(**args)
    assert first.engine == "native"
    assert first.route_id == second.route_id
    assert normalize_route_record(first.summary()) == first.summary()


def test_advanced_lock_is_explicit_and_unavailable_lock_fails():
    route = route_capability(
        "Create a box",
        workbench="PartDesignWorkbench",
        current_engine="native",
        available_engines=["native", "openscad"],
        has_existing_geometry=False,
        strategy_lock="openscad",
    )
    assert route.engine == "openscad"
    assert route.automatic is False
    with pytest.raises(RuntimeError, match="locked"):
        route_capability(
            "Create a box",
            workbench="PartDesignWorkbench",
            current_engine="native",
            available_engines=["native"],
            has_existing_geometry=False,
            strategy_lock="openscad",
        )


def test_nonnative_lock_cannot_override_required_native_capability():
    with pytest.raises(RuntimeError, match="locked|native"):
        route_capability(
            "Create a drawing",
            workbench="PartDesignWorkbench",
            current_engine="build123d",
            available_engines=["native", "build123d"],
            strategy_lock="build123d",
        )


def test_unknown_category_and_request_version_are_rejected():
    with pytest.raises(ValueError, match="Unknown CAD capability"):
        make_routing_request(
            "Do something",
            workbench="PartDesignWorkbench",
            current_engine="native",
            available_engines=["native"],
            capability_category="arbitrary-provider-category",
        )
    request = make_routing_request(
        "Create a box",
        workbench="PartDesignWorkbench",
        current_engine="native",
        available_engines=["native"],
    ).summary()
    request["version"] = 99
    with pytest.raises(RuntimeError, match="schema"):
        route_capability(request)
    typed = make_routing_request(
        "Create a box",
        workbench="PartDesignWorkbench",
        current_engine="native",
        available_engines=["native"],
    )
    with pytest.raises(RuntimeError, match="schema"):
        route_capability(replace(typed, capability_category="unknown"))


def test_version_one_route_migrates_and_retains_legacy_identity():
    legacy = {
        "schema": LEGACY_ROUTER_SCHEMA,
        "version": 1,
        "route_id": "legacy-id",
        "engine": "native",
        "workbench": "PartDesignWorkbench",
        "reason_code": "native_editability_default",
        "explanation": "Legacy route.",
        "preserved_existing_structure": False,
        "automatic": True,
    }
    migrated = normalize_route_record(legacy)
    assert migrated["schema"] == ROUTER_SCHEMA
    assert migrated["route_id"] != "legacy-id"
    assert migrated["evidence"]["legacy_route_id"] == "legacy-id"
    assert normalize_route_record(migrated) == migrated


def test_version_two_route_rejects_tampered_evidence():
    route = route_capability(
        "Create a box",
        workbench="PartDesignWorkbench",
        current_engine="native",
        available_engines=["native"],
    ).summary()
    route["evidence"]["decision_factor"] = "tampered"
    with pytest.raises(RuntimeError, match="content hash"):
        normalize_route_record(route)


def test_version_two_route_rejects_rehashed_unknown_nested_category():
    route = route_capability(
        "Create a box",
        workbench="PartDesignWorkbench",
        current_engine="native",
        available_engines=["native"],
    ).summary()
    route["request"]["capability_category"] = "provider-injected-category"
    content = {key: value for key, value in route.items() if key != "route_id"}
    route["route_id"] = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(RuntimeError, match="schema"):
        normalize_route_record(route)
