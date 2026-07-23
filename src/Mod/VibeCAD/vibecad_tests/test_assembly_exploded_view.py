# SPDX-License-Identifier: LGPL-2.1-or-later

import copy
from types import SimpleNamespace

import pytest

from VibeCADAssemblyExplodedView import (
    ACTIVE_VIEW_PROPERTY,
    CONFIGURATION_PROPERTY,
    CONTRACT_VERSION_PROPERTY,
    EXPLODED_VIEW_SCHEMA,
    MANAGED_STEP_PROPERTY,
    MANAGED_VIEW_PROPERTY,
    METADATA_PROPERTY,
    STATE_PROPERTY,
    canonical_json,
    configuration_id,
    configuration_identity_payload,
    prepare_component_moves,
    seal_metadata,
    validate_metadata_payload,
    validate_native_configuration,
)
from VibeCADDocumentValidator import validate_open_document
from tool_impl.service import assembly_create_exploded_view


def _component(name, label=None, source_name="Source"):
    return SimpleNamespace(
        Name=name,
        Label=label or name,
        TypeId="App::Link",
        LinkedObject=SimpleNamespace(Name=source_name),
    )


def _assembly(*components):
    return SimpleNamespace(Name="Assembly", Label="Assembly", Group=list(components))


def _placement(x, y, z):
    return {
        "position_mm": {"x": float(x), "y": float(y), "z": float(z)},
        "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
    }


def _native_placement(x, y, z):
    return SimpleNamespace(
        Base=SimpleNamespace(x=float(x), y=float(y), z=float(z)),
        Rotation=SimpleNamespace(Q=(0.0, 0.0, 0.0, 1.0)),
    )


def _metadata():
    payload = {
        "schema": EXPLODED_VIEW_SCHEMA,
        "version": 1,
        "generation": 1,
        "previous_content_sha256": None,
        "state": "exploded",
        "assembly_name": "Assembly",
        "view_group_name": "ExplodedViews",
        "view_name": "VibeCADExplodedView",
        "components": [
            {
                "component_name": "Part001",
                "linked_object_name": "Source",
                "step_name": "VibeCADExplodedMove",
                "direction": {"x": 1.0, "y": 0.0, "z": 0.0},
                "distance_mm": 10.0,
                "displacement_mm": {"x": 10.0, "y": 0.0, "z": 0.0},
                "assembled_placement": _placement(0, 0, 0),
                "exploded_placement": _placement(10, 0, 0),
            }
        ],
    }
    payload["configuration_id"] = configuration_id(
        configuration_identity_payload(payload)
    )
    return seal_metadata(payload)


def test_prepares_global_direction_with_exact_component_identity():
    part = _component("Part001", label="Friendly name")
    assembly = _assembly(part)

    moves = prepare_component_moves(
        assembly,
        [{"component_name": "Part001", "distance_mm": 25}],
        {"x": 0, "y": 3, "z": 4},
    )

    assert moves[0]["component"] is part
    assert moves[0]["direction"] == {"x": 0.0, "y": 0.6, "z": 0.8}
    assert moves[0]["displacement_mm"] == {"x": 0.0, "y": 15.0, "z": 20.0}
    with pytest.raises(ValueError, match="exact internal name"):
        prepare_component_moves(
            assembly,
            [{"component_name": "Friendly name", "distance_mm": 25}],
            {"x": 1, "y": 0, "z": 0},
        )


def test_prepares_exact_per_component_vectors_without_a_global_direction():
    first = _component("Part001")
    second = _component("Part002", source_name="SecondSource")

    moves = prepare_component_moves(
        _assembly(first, second),
        [
            {
                "component_name": "Part001",
                "distance_mm": 5,
                "vector": {"x": -1, "y": 0, "z": 0},
            },
            {
                "component_name": "Part002",
                "distance_mm": 10,
                "vector": {"x": 0, "y": 1, "z": 0},
            },
        ],
    )

    assert [item["component"] for item in moves] == [first, second]
    assert [item["displacement_mm"] for item in moves] == [
        {"x": -5.0, "y": 0.0, "z": 0.0},
        {"x": 0.0, "y": 10.0, "z": 0.0},
    ]


@pytest.mark.parametrize(
    ("components", "direction", "message"),
    [
        (
            [
                {"component_name": "Part001", "distance_mm": 5},
                {"component_name": "Part001", "distance_mm": 10},
            ],
            {"x": 1, "y": 0, "z": 0},
            "more than once",
        ),
        ([{"component_name": "Part001", "distance_mm": float("nan")}], {"x": 1, "y": 0, "z": 0}, "finite positive"),
        ([{"component_name": "Part001", "distance_mm": 5}], {"x": 0, "y": 0, "z": 0}, "zero-length"),
        ([{"component_name": "Part001", "distance_mm": 5}], None, "vector is required"),
        (
            [{"component_name": "Part001", "distance_mm": 5, "vector": {"x": 1, "y": 0, "z": 0}}],
            {"x": 1, "y": 0, "z": 0},
            "not both",
        ),
    ],
)
def test_rejects_ambiguous_duplicate_or_nonfinite_moves(components, direction, message):
    with pytest.raises(ValueError, match=message):
        prepare_component_moves(_assembly(_component("Part001")), components, direction)


def test_validates_versioned_metadata_and_rejects_tampering():
    metadata = _metadata()
    assert validate_metadata_payload(metadata)["ok"] is True

    changed_distance = copy.deepcopy(metadata)
    changed_distance["components"][0]["distance_mm"] = 11
    result = validate_metadata_payload(changed_distance)
    assert result["ok"] is False
    assert any("displacement does not match" in error for error in result["errors"])
    assert any("content digest does not match" in error for error in result["errors"])

    duplicate = copy.deepcopy(metadata)
    duplicate["components"].append(copy.deepcopy(duplicate["components"][0]))
    duplicate = seal_metadata(duplicate)
    result = validate_metadata_payload(duplicate)
    assert result["ok"] is False
    assert any("duplicated" in error for error in result["errors"])

    missing = copy.deepcopy(metadata)
    del missing["components"][0]["assembled_placement"]
    missing = seal_metadata(missing)
    assert validate_metadata_payload(missing)["ok"] is False

    false_identity = copy.deepcopy(metadata)
    false_identity["configuration_id"] = "a" * 64
    false_identity = seal_metadata(false_identity)
    false_identity_result = validate_metadata_payload(false_identity)
    assert false_identity_result["ok"] is False
    assert any(
        "identity does not match its move definition" in error
        for error in false_identity_result["errors"]
    )


def test_metadata_contract_rejects_unknown_top_level_and_component_fields():
    top_level = copy.deepcopy(_metadata())
    top_level["animation"] = {"frames": 20}
    top_level = seal_metadata(top_level)
    top_level_result = validate_metadata_payload(top_level)
    assert top_level_result["ok"] is False
    assert any("unsupported fields: animation" in item for item in top_level_result["errors"])

    component = copy.deepcopy(_metadata())
    component["components"][0]["velocity_mm_per_second"] = 10
    component = seal_metadata(component)
    component_result = validate_metadata_payload(component)
    assert component_result["ok"] is False
    assert any(
        "unsupported fields: velocity_mm_per_second" in item
        for item in component_result["errors"]
    )


def test_metadata_contract_requires_every_top_level_and_component_field():
    top_level = copy.deepcopy(_metadata())
    del top_level["previous_content_sha256"]
    top_level = seal_metadata(top_level)
    top_level_result = validate_metadata_payload(top_level)
    assert top_level_result["ok"] is False
    assert any(
        "missing fields: previous_content_sha256" in item
        for item in top_level_result["errors"]
    )

    component = copy.deepcopy(_metadata())
    del component["components"][0]["assembled_placement"]
    component = seal_metadata(component)
    component_result = validate_metadata_payload(component)
    assert component_result["ok"] is False
    assert any(
        "missing fields: assembled_placement" in item
        for item in component_result["errors"]
    )


def test_native_validation_rejects_missing_calculated_component():
    metadata = _metadata()
    source = SimpleNamespace(Name="Source")
    component = SimpleNamespace(
        Name="Part001",
        TypeId="App::Link",
        LinkedObject=source,
        Placement=_native_placement(0, 0, 0),
    )
    step_proxy = type("ExplodedViewStep", (), {})()
    step = SimpleNamespace(
        Name="VibeCADExplodedMove",
        Proxy=step_proxy,
        MoveType="Normal",
        MovementTransform=_native_placement(10, 0, 0),
    )
    setattr(step, MANAGED_STEP_PROPERTY, True)
    setattr(step, CONFIGURATION_PROPERTY, metadata["configuration_id"])

    class ExplodedView:
        def _calculateExplodedPlacements(self, _view):
            return {}, []

    view = SimpleNamespace(
        Name="VibeCADExplodedView",
        Group=[step],
        Proxy=ExplodedView(),
    )
    setattr(view, MANAGED_VIEW_PROPERTY, True)
    setattr(view, CONFIGURATION_PROPERTY, metadata["configuration_id"])
    setattr(view, STATE_PROPERTY, "exploded")
    setattr(view, METADATA_PROPERTY, canonical_json(metadata))
    view_group = SimpleNamespace(
        Name="ExplodedViews", TypeId="Assembly::ViewGroup", Group=[view]
    )
    assembly = SimpleNamespace(
        Name="Assembly", Group=[component, view_group]
    )
    setattr(assembly, ACTIVE_VIEW_PROPERTY, view)
    setattr(assembly, CONTRACT_VERSION_PROPERTY, 1)
    step.References = [assembly, ["Part001."]]

    result = validate_native_configuration(assembly, view)

    assert result["ok"] is False
    assert any("line count is inconsistent" in item for item in result["errors"])
    assert any("component set is inconsistent" in item for item in result["errors"])
    assert any("has no calculated placement" in item for item in result["errors"])


def test_core_inspection_exposes_bounded_exploded_view_provenance():
    from VibeCADCore import VibeCADService

    metadata = _metadata()
    view = SimpleNamespace(
        Name="VibeCADExplodedView",
        Label="Service view",
        VibeCADManagedExplodedView=True,
        VibeCADExplodedViewMetadata=canonical_json(metadata),
    )
    view_group = SimpleNamespace(
        Name="ExplodedViews", TypeId="Assembly::ViewGroup", Group=[view]
    )

    summaries = VibeCADService._assembly_exploded_view_summaries(
        SimpleNamespace(Group=[view_group])
    )

    assert summaries == [
        {
            "name": "VibeCADExplodedView",
            "label": "Service view",
            "view_group": "ExplodedViews",
            "metadata_valid": True,
            "metadata_errors": [],
            "configuration_id": metadata["configuration_id"],
            "generation": 1,
            "state": "exploded",
            "state_meaning": (
                "The native exploded-view graph is available. Accepted component "
                "placements remain assembled."
            ),
            "content_sha256": metadata["content_sha256"],
            "components": [
                {
                    "component_name": "Part001",
                    "linked_object_name": "Source",
                    "step_name": "VibeCADExplodedMove",
                    "direction": {"x": 1.0, "y": 0.0, "z": 0.0},
                    "distance_mm": 10.0,
                    "assembled_placement": _placement(0, 0, 0),
                    "exploded_placement": _placement(10, 0, 0),
                }
            ],
        }
    ]


def test_document_validator_rejects_missing_managed_exploded_metadata():
    shape = SimpleNamespace(isNull=lambda: False, isValid=lambda: True)
    source = SimpleNamespace(Name="Source", TypeId="Part::Feature", Shape=shape)
    component = SimpleNamespace(
        Name="Part001", TypeId="App::Link", LinkedObject=source, Shape=shape
    )
    joint_group = SimpleNamespace(Name="Joints", TypeId="Assembly::JointGroup", Group=[])
    view = SimpleNamespace(
        Name="VibeCADExplodedView",
        TypeId="App::FeaturePython",
        VibeCADManagedExplodedView=True,
        VibeCADExplodedViewMetadata="",
        Group=[],
    )
    view_group = SimpleNamespace(
        Name="ExplodedViews", TypeId="Assembly::ViewGroup", Group=[view]
    )
    assembly = SimpleNamespace(
        Name="Assembly",
        TypeId="Assembly::AssemblyObject",
        Group=[joint_group, component, view_group],
        PropertiesList=[
            "VibeCADActiveExplodedView", "VibeCADExplodedViewContractVersion"
        ],
        VibeCADActiveExplodedView=view,
        VibeCADExplodedViewContractVersion=1,
    )
    document = SimpleNamespace(
        Objects=[source, assembly, joint_group, component, view_group, view],
        getRecomputeDiagnostics=lambda: [],
    )

    result = validate_open_document(document)

    assert result["ok"] is False
    assert result["exploded_view_checks"] == 1
    assert any("metadata is missing" in error for error in result["errors"])


def test_tool_requires_design_modify_permission_before_native_work():
    service = SimpleNamespace(
        authorize=lambda _permission: (_ for _ in ()).throw(
            PermissionError("Role 'viewer' cannot modify this assembly.")
        )
    )

    result = assembly_create_exploded_view.run(
        service,
        "Assembly",
        "Service view",
        [{"component_name": "Part001", "distance_mm": 10}],
        {"x": 1, "y": 0, "z": 0},
    )

    assert result["ok"] is False
    assert result["failure_code"] == "RBAC_DENIED"
    assert result["failure_stage"] == "permission"
