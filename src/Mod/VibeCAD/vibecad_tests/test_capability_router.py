# SPDX-License-Identifier: LGPL-2.1-or-later
import pytest

from VibeCADCapabilityRouter import ROUTER_SCHEMA, route_capability


def test_existing_document_keeps_its_established_engine():
    route = route_capability(
        "Make the wall thicker", workbench="PartDesignWorkbench",
        current_engine="build123d", available_engines=["native", "build123d"],
        has_existing_geometry=True,
    )
    assert route.schema == ROUTER_SCHEMA
    assert route.engine == "build123d"
    assert route.preserved_existing_structure is True


def test_new_functional_part_uses_available_validated_scripted_route():
    route = route_capability(
        "Create an electronics enclosure with a removable lid",
        workbench="PartDesignWorkbench", current_engine="native",
        available_engines=["native", "build123d"], has_existing_geometry=False,
    )
    assert route.engine == "build123d"
    assert route.reason_code == "new_functional_part"


def test_professional_workflow_prefers_native_editable_capabilities():
    route = route_capability(
        "Create a dimensioned manufacturing drawing",
        workbench="TechDrawWorkbench", current_engine="vibescript",
        available_engines=["native", "vibescript"], has_existing_geometry=False,
    )
    assert route.engine == "native"
    assert route.reason_code == "professional_native_capability"


def test_default_is_native_and_route_identity_is_deterministic():
    args = dict(
        request="Create a 20 mm cube", workbench="PartDesignWorkbench",
        current_engine="vibescript", available_engines=["native", "vibescript"],
        has_existing_geometry=False,
    )
    first = route_capability(**args)
    second = route_capability(**args)
    assert first.engine == "native"
    assert first.route_id == second.route_id


def test_advanced_lock_is_explicit_and_unavailable_lock_fails():
    route = route_capability(
        "Create a box", workbench="PartDesignWorkbench", current_engine="native",
        available_engines=["native", "openscad"], has_existing_geometry=False,
        strategy_lock="openscad",
    )
    assert route.engine == "openscad"
    assert route.automatic is False
    with pytest.raises(RuntimeError, match="locked"):
        route_capability(
            "Create a box", workbench="PartDesignWorkbench", current_engine="native",
            available_engines=["native"], has_existing_geometry=False,
            strategy_lock="openscad",
        )
