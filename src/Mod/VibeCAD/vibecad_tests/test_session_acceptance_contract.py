# SPDX-License-Identifier: LGPL-2.1-or-later

from pathlib import Path
from types import SimpleNamespace

import pytest

import VibeCADSession as session
from VibeCADSaveBoundary import internal_document_save, internal_document_save_active
from VibeCADSession import (
    RUN_STATES,
    _candidate_decision,
    _changed_objects,
    _emit_run_state,
    _mutation_has_design_brief_update,
)


def _run_candidate_decision_turn(
    monkeypatch,
    *,
    candidate_decision_callback=None,
    cancellation_check=None,
    provider_error: BaseException | None = None,
    capability_error: BaseException | None = None,
    reject_error: BaseException | None = None,
    reactivation_error: BaseException | None = None,
    expected_error: BaseException | None = None,
):
    observed: dict[str, object] = {
        "calls": [],
        "cleanup_order": [],
        "events": [],
        "records": [],
    }
    document_dispatch = {"active": False}

    class Service(session.VibeCADService):
        def __init__(self):
            pass

        @staticmethod
        def authorize(permission):
            assert permission == "ai.use"

        @staticmethod
        def document_persistence_state():
            return {"enabled": True, "file_path": "/project/design.FCStd"}

        @staticmethod
        def route_modeling_strategy(_prompt):
            return None

        @staticmethod
        def active_workbench_name():
            return "PartDesignWorkbench"

        @staticmethod
        def project_scope_snapshot():
            return {
                "root": "/project",
                "project_id": "project-1",
                "document": {
                    "document": "Design",
                    "file_path": "/project/design.FCStd",
                },
            }

        @staticmethod
        def structural_document_revision():
            return "document-revision"

        @staticmethod
        def design_brief():
            return {"revision": "brief-revision"}

        @staticmethod
        def provider_registered_import_assets(
            *, scope, cancellation_check, progress_callback
        ):
            assert document_dispatch["active"] is False
            assert cancellation_check is cancellation_check_value
            assert progress_callback is progress_callback_value
            observed["import_asset_load_count"] = int(
                observed.get("import_asset_load_count", 0)
            ) + 1
            observed["import_asset_scope"] = dict(scope)
            return {
                "schema": "vibecad-project-import-assets-context-v1",
                "version": 1,
                "project_id": scope["project_id"],
                "asset_count": 0,
                "listed_asset_count": 0,
                "assets_omitted": 0,
                "asset_context_limit": 12,
                "supported_formats": ["step"],
                "assets": [],
            }

    prepared = SimpleNamespace(
        acceptance_id="acceptance-1",
        prior_head=None,
        journal_path=Path("/project/acceptance/acceptance-1/journal.json"),
    )

    class Coordinator:
        def __init__(self, root, project_id):
            assert (root, project_id) == ("/project", "project-1")

        @staticmethod
        def recover_incomplete(**_callbacks):
            return []

        @staticmethod
        def prepare(canonical_path, _save_copy):
            assert canonical_path == "/project/design.FCStd"
            observed["prepared"] = True
            return prepared

        @staticmethod
        def validate_candidate(_prepared, record, **callbacks):
            assert sorted(callbacks) == [
                "restore_live",
                "save_copy",
                "validate_document",
                "write_metadata",
            ]
            observed["calls"].append("validate")
            revision = record({"ok": True, "shape_checks": 1})
            observed["records"].append(revision)
            return {
                "acceptance_id": "acceptance-1",
                "prior_head": None,
                "candidate_sha256": "c" * 64,
                "validation": {"ok": True},
                "candidate_path": "/project/acceptance/acceptance-1/candidate.fcstd",
            }

        @staticmethod
        def accept_validated_candidate(_prepared, **callbacks):
            observed["calls"].append(("accept", callbacks["acceptance_mode"]))
            return {"ok": True, "revision": {"revision_id": "d" * 64}}

        @staticmethod
        def reject(_prepared, **callbacks):
            observed["cleanup_order"].append("reject")
            observed["calls"].append(
                (
                    "reject",
                    callbacks.get("decision_mode"),
                    callbacks.get("reason"),
                )
            )
            if reject_error is not None:
                raise reject_error
            return {"ok": True}

        @staticmethod
        def complete_without_mutation(_prepared):
            pytest.fail("The mutation turn was treated as a read-only turn.")

    class Provider:
        model = "deterministic-test"

    def tool_runner(_service, **kwargs):
        capability = kwargs["_prepared_mutation_capability"]
        assert session._prepared_mutation_capability_is_active(
            capability, _service
        )
        observed["mutation_capability"] = capability
        kwargs["tool_trace"].extend(
            [
                {
                    "tool_name": "partdesign.pad",
                    "ok": True,
                    "safety": "safe_write",
                    "arguments": {"length": 10},
                    "result": {},
                },
                {
                    "tool_name": "core.update_design_brief",
                    "ok": True,
                    "safety": "safe_write",
                    "arguments": {"purpose": "test"},
                    "result": {},
                },
            ]
        )
        return lambda *_args, **_kwargs: None

    monkeypatch.setattr(session, "VibeCADAcceptanceCoordinator", Coordinator)
    monkeypatch.setattr(session, "_prime_modeling_engine_for_session", lambda *_args: "native")
    monkeypatch.setattr(
        session,
        "_persist_session_conversation_turn",
        lambda *_args, **_kwargs: {"conversation_id": "conversation-1"},
    )
    monkeypatch.setattr(
        session,
        "_context_for_provider",
        lambda *_args, **_kwargs: {
            "workbench": "PartDesignWorkbench",
            "provider_tool_schemas": [],
        },
    )
    monkeypatch.setattr(
        session,
        "_acceptance_callbacks",
        lambda *_args: tuple(lambda *_inner: None for _index in range(4)),
    )
    monkeypatch.setattr(session, "_apply_managed_outbound_policy", lambda context, *_args, **_kwargs: context)
    monkeypatch.setattr(session, "_consume_context_view_attachment", lambda *_args: None)
    monkeypatch.setattr(session, "make_provider_tool_runner", tool_runner)
    monkeypatch.setattr(session, "_provider_prompt", lambda *_args, **_kwargs: "provider prompt")
    issue_capability = session._issue_prepared_mutation_capability

    def issue_prepared_mutation_capability(*args, **kwargs):
        observed["capability_issue_after_prepare"] = bool(observed.get("prepared"))
        if capability_error is not None:
            raise capability_error
        capability = issue_capability(*args, **kwargs)
        observed["issued_capability"] = capability
        return capability

    monkeypatch.setattr(
        session,
        "_issue_prepared_mutation_capability",
        issue_prepared_mutation_capability,
    )

    def run_provider(*_args, **_kwargs):
        if provider_error is not None:
            raise provider_error
        return SimpleNamespace(final_output="Candidate ready.")

    monkeypatch.setattr(session, "_run_provider", run_provider)

    def reactivate_scope_document(*_args):
        observed["cleanup_order"].append("reactivate")
        observed["reactivation_count"] = int(
            observed.get("reactivation_count", 0)
        ) + 1
        if reactivation_error is not None:
            raise reactivation_error

    monkeypatch.setattr(
        session,
        "_reactivate_scope_document",
        reactivate_scope_document,
    )

    def progress(event):
        observed["events"].append(dict(event))

    cancellation_check_value = cancellation_check
    progress_callback_value = progress

    def dispatch(operation):
        assert document_dispatch["active"] is False
        document_dispatch["active"] = True
        try:
            return operation()
        finally:
            document_dispatch["active"] = False

    decision_callback = None
    if candidate_decision_callback is not None:
        def decision_callback(payload):
            observed["calls"].append("decision")
            return candidate_decision_callback(payload)

    error_to_expect = expected_error
    if error_to_expect is None and (
        capability_error is not None
        or (
            provider_error is not None
            and not isinstance(provider_error, session.ProviderUnavailable)
        )
    ):
        error_to_expect = capability_error or provider_error

    if error_to_expect is None:
        response = session.run_prompt(
            "Create a part",
            service=Service(),
            provider=Provider(),
            progress_callback=progress,
            cancellation_check=cancellation_check,
            candidate_decision_callback=decision_callback,
            document_thread_dispatch=dispatch,
        )
    else:
        with pytest.raises(type(error_to_expect), match=str(error_to_expect)) as raised:
            session.run_prompt(
                "Create a part",
                service=Service(),
                provider=Provider(),
                progress_callback=progress,
                cancellation_check=cancellation_check,
                candidate_decision_callback=decision_callback,
                document_thread_dispatch=dispatch,
            )
        observed["raised_error"] = raised.value
        response = None
    return observed, response


def test_candidate_decision_contract_is_exact_and_defaults_to_automatic() -> None:
    payload = {"acceptance_id": "acceptance-1"}
    assert _candidate_decision(None, payload) == ("accept", "automatic")
    assert _candidate_decision(lambda review: "accept", payload) == (
        "accept",
        "human",
    )
    assert _candidate_decision(lambda review: "reject", payload) == (
        "reject",
        "human",
    )
    with pytest.raises(ValueError, match="exactly 'accept' or 'reject'"):
        _candidate_decision(lambda review: "ACCEPT", payload)


def test_prepared_mutation_capability_is_exact_scoped_and_revocable() -> None:
    service = object()
    capability = session._issue_prepared_mutation_capability(
        service, SimpleNamespace(acceptance_id="acceptance-capability")
    )

    assert session._prepared_mutation_capability_is_active(capability, service)
    assert not session._prepared_mutation_capability_is_active(capability, object())

    class Forged(type(capability)):
        pass

    forged = object.__new__(Forged)
    forged._active = True
    forged._acceptance_id = "acceptance-capability"
    forged._seal = session._PREPARED_MUTATION_CAPABILITY_SEAL
    forged._service = service
    assert not session._prepared_mutation_capability_is_active(forged, service)

    session._revoke_prepared_mutation_capability(capability)
    assert not session._prepared_mutation_capability_is_active(capability, service)


def test_run_state_event_contract_is_stable() -> None:
    events = []
    for state in RUN_STATES:
        _emit_run_state(events.append, state)
    assert events == [
        {"event": "run_state_changed", "state": state}
        for state in RUN_STATES
    ]
    with pytest.raises(ValueError, match="Unknown VibeCAD run state"):
        _emit_run_state(events.append, "Reviewing")


def test_human_candidate_acceptance_occurs_after_validation(monkeypatch) -> None:
    review_payloads = []

    def accept(review):
        review_payloads.append(dict(review))
        return "accept"

    observed, response = _run_candidate_decision_turn(
        monkeypatch,
        candidate_decision_callback=accept,
    )
    assert observed["calls"] == ["validate", "decision", ("accept", "human")]
    assert [
        event["state"]
        for event in observed["events"]
        if event.get("event") == "run_state_changed"
    ] == list(RUN_STATES)
    assert review_payloads == [
        {
            "acceptance_id": "acceptance-1",
            "prior_head": None,
            "candidate_sha256": "c" * 64,
            "validation": {"ok": True},
            "candidate_path": "/project/acceptance/acceptance-1/candidate.fcstd",
        }
    ]
    assert observed["records"][0]["rollback"]["acceptance_mode"] == "human"
    assert observed["import_asset_load_count"] == 1
    assert response.context["candidate_decision"] == {
        "decision": "accept",
        "mode": "human",
        "acceptance_id": "acceptance-1",
        "candidate_sha256": "c" * 64,
        "revision_id": "d" * 64,
    }


def test_headless_candidate_acceptance_is_explicitly_automatic(monkeypatch) -> None:
    observed, response = _run_candidate_decision_turn(monkeypatch)
    assert observed["calls"] == ["validate", ("accept", "automatic")]
    assert observed["records"][0]["rollback"]["acceptance_mode"] == "automatic"
    assert response.context["candidate_decision"]["mode"] == "automatic"
    capability = observed["mutation_capability"]
    assert not session._prepared_mutation_capability_is_active(
        capability, capability._service
    )


def test_human_rejection_does_not_accept_a_revision(monkeypatch) -> None:
    observed, response = _run_candidate_decision_turn(
        monkeypatch,
        candidate_decision_callback=lambda _review: "reject",
    )
    assert observed["calls"] == [
        "validate",
        "decision",
        (
            "reject",
            "human",
            "The user rejected the validated candidate preview.",
        ),
    ]
    assert response.context["candidate_decision"]["decision"] == "reject"
    assert "Applying revision" not in [
        event["state"]
        for event in observed["events"]
        if event.get("event") == "run_state_changed"
    ]


def test_stop_during_review_overrides_accept_and_rejects(monkeypatch) -> None:
    observed, response = _run_candidate_decision_turn(
        monkeypatch,
        candidate_decision_callback=lambda _review: "accept",
        cancellation_check=lambda: True,
    )
    assert observed["calls"] == [
        "validate",
        "decision",
        (
            "reject",
            "human",
            "The user stopped the run during candidate review.",
        ),
    ]
    assert response.context["candidate_decision"]["decision"] == "reject"


def test_unexpected_provider_error_rejects_and_revokes_prepared_candidate(
    monkeypatch,
) -> None:
    observed, response = _run_candidate_decision_turn(
        monkeypatch,
        provider_error=RuntimeError("malformed provider process message"),
    )

    assert response is None
    assert observed["calls"] == [
        (
            "reject",
            None,
            "The candidate turn failed before it reached a recorded terminal acceptance state.",
        )
    ]
    assert observed["cleanup_order"] == ["reject", "reactivate"]
    capability = observed["mutation_capability"]
    assert not session._prepared_mutation_capability_is_active(
        capability, capability._service
    )


def test_provider_error_uses_terminal_cleanup_before_returning_failure(
    monkeypatch,
) -> None:
    provider_error = session.ProviderUnavailable("provider transport failed")
    observed, response = _run_candidate_decision_turn(
        monkeypatch,
        provider_error=provider_error,
    )

    assert response is not None
    assert response.error == "provider transport failed"
    assert observed["calls"] == [
        ("reject", None, "provider transport failed")
    ]
    assert observed["cleanup_order"] == ["reject", "reactivate"]
    capability = observed["mutation_capability"]
    assert not session._prepared_mutation_capability_is_active(
        capability, capability._service
    )


def test_capability_issue_failure_is_guarded_by_rollback_and_reactivation(
    monkeypatch,
) -> None:
    capability_error = RuntimeError("capability issue failed")
    observed, response = _run_candidate_decision_turn(
        monkeypatch,
        capability_error=capability_error,
    )

    assert response is None
    assert observed["capability_issue_after_prepare"] is True
    assert "issued_capability" not in observed
    assert "mutation_capability" not in observed
    assert observed["calls"] == [
        (
            "reject",
            None,
            "The candidate turn failed before it reached a recorded terminal acceptance state.",
        )
    ]
    assert observed["cleanup_order"] == ["reject", "reactivate"]
    assert observed["raised_error"] is capability_error


def test_reject_failure_still_reactivates_and_fails_closed(monkeypatch) -> None:
    provider_error = session.ProviderUnavailable("provider transport failed")
    reject_error = RuntimeError("prepared rollback failed")
    observed, response = _run_candidate_decision_turn(
        monkeypatch,
        provider_error=provider_error,
        reject_error=reject_error,
        expected_error=reject_error,
    )

    assert response is None
    assert observed["calls"] == [
        ("reject", None, "provider transport failed")
    ]
    assert observed["cleanup_order"] == ["reject", "reactivate"]
    assert observed["raised_error"] is reject_error
    assert observed["raised_error"].__cause__ is provider_error
    capability = observed["mutation_capability"]
    assert not session._prepared_mutation_capability_is_active(
        capability, capability._service
    )


def test_reactivation_failure_after_rollback_fails_closed(monkeypatch) -> None:
    provider_error = session.ProviderUnavailable("provider transport failed")
    reactivation_error = RuntimeError("document reactivation failed")
    observed, response = _run_candidate_decision_turn(
        monkeypatch,
        provider_error=provider_error,
        reactivation_error=reactivation_error,
        expected_error=reactivation_error,
    )

    assert response is None
    assert observed["calls"] == [
        ("reject", None, "provider transport failed")
    ]
    assert observed["cleanup_order"] == ["reject", "reactivate"]
    assert observed["raised_error"] is reactivation_error
    assert observed["raised_error"].__cause__ is provider_error
    capability = observed["mutation_capability"]
    assert not session._prepared_mutation_capability_is_active(
        capability, capability._service
    )


def test_sketch_close_continuation_forwards_candidate_decision_callback(monkeypatch) -> None:
    observed = {}
    callback = lambda _review: "accept"

    def run_turn(prompt, **arguments):
        observed["prompt"] = prompt
        observed["arguments"] = arguments
        return "response"

    monkeypatch.setattr(session, "_run_session_turn", run_turn)
    result = session.run_sketch_close_continuation(
        {
            "type": "human_closed_sketch",
            "document_uid": "document-1",
            "document_name": "Design",
            "sketch_name": "Sketch",
            "sketch_label": "Base sketch",
            "owner_body": "Body",
        },
        candidate_decision_callback=callback,
    )
    assert result == "response"
    assert observed["arguments"]["candidate_decision_callback"] is callback
    assert observed["arguments"]["persist_input_as_user"] is False


def test_cad_mutation_requires_successful_design_brief_update() -> None:
    cad = {"tool_name": "partdesign.pad", "ok": True, "safety": "safe_write"}
    assert _mutation_has_design_brief_update([cad]) is False
    assert _mutation_has_design_brief_update(
        [cad, {"tool_name": "core.update_design_brief", "ok": True, "safety": "safe_write"}]
    ) is True


def test_failed_or_standalone_brief_update_contract() -> None:
    cad = {"tool_name": "partdesign.pad", "ok": True, "safety": "safe_write"}
    failed = {"tool_name": "core.update_design_brief", "ok": False, "safety": "safe_write"}
    assert _mutation_has_design_brief_update([cad, failed]) is False
    assert _mutation_has_design_brief_update([failed]) is True


def test_changed_object_extraction_ignores_boolean_state_flags() -> None:
    traces = [{
        "result": {
            "state_change": {
                "changed": True,
                "created_objects": [{"name": "Body"}],
                "changed_objects": [{"before": {"name": "Sketch"}, "after": {"name": "Sketch"}}],
            },
            "document_delta": {
                "created_objects": [{"name": "Body"}],
                "changed_objects": [
                    {"before": {"name": "Sketch"}, "after": {"name": "Sketch"}}
                ],
                "deleted_objects": [{"name": "OldFeature"}],
            },
        }
    }]
    assert _changed_objects(traces) == [
        {"object": "Body", "change": "created"},
        {"object": "Sketch", "change": "modified"},
        {"object": "OldFeature", "change": "deleted"},
    ]


def test_internal_save_boundary_is_nested_and_always_clears() -> None:
    assert internal_document_save_active() is False
    with internal_document_save():
        assert internal_document_save_active() is True
        with internal_document_save():
            assert internal_document_save_active() is True
    assert internal_document_save_active() is False
    source = __import__("inspect").getsource(__import__("VibeCADGui"))
    assert source.count("internal_document_save_active()") >= 2


def test_restore_accepted_revision_uses_transactional_coordinator(monkeypatch) -> None:
    observed = {}

    class Service:
        @staticmethod
        def project_scope_snapshot():
            return {
                "root": "/project",
                "project_id": "project-1",
                "document": {"file_path": "/design.FCStd"},
            }

    class Coordinator:
        def __init__(self, root, project_id):
            observed["scope"] = (root, project_id)

        def restore_revision(self, revision_id, canonical_path, **callbacks):
            observed["restore"] = (revision_id, canonical_path, sorted(callbacks))
            return {"ok": True, "revision": {"revision_id": revision_id}}

    callbacks = tuple(lambda *_args: None for _index in range(4))
    monkeypatch.setattr(session, "VibeCADAcceptanceCoordinator", Coordinator)
    monkeypatch.setattr(session, "_acceptance_callbacks", lambda *_args: callbacks)
    result = session.restore_accepted_revision(Service(), "A" * 64)
    assert result["ok"] is True
    assert observed["scope"] == ("/project", "project-1")
    assert observed["restore"][0] == "a" * 64
    assert observed["restore"][1] == "/design.FCStd"
    assert observed["restore"][2] == [
        "restore_live", "save_copy", "validate_document", "write_metadata"
    ]


def test_restore_accepted_revision_requires_saved_document() -> None:
    class Service:
        @staticmethod
        def project_scope_snapshot():
            return {"root": "/project", "project_id": "project-1", "document": {}}

    with pytest.raises(RuntimeError, match="Save the active CAD document"):
        session.restore_accepted_revision(Service(), "a" * 64)


def test_revision_workspace_exposes_timeline_compare_restore_branch_and_report_controls() -> None:
    source = __import__("inspect").getsource(__import__("VibeCADGui"))
    for object_name in (
        "VibeRevisionTimeline",
        "VibeRevisionCompare",
        "VibeRevisionRestore",
        "VibeRevisionRefresh",
        "VibeRevisionBranch",
        "VibeRevisionReport",
    ):
        assert object_name in source
    assert "restore_accepted_revision(" in source
    assert "Select exactly two revisions to compare." in source
    assert "create_revision_branch(" in source
    assert "export_revision_report(" in source


def test_managed_outbound_policy_removes_denied_tools_and_rehashes_surface() -> None:
    policy = __import__("VibeCADManagedPolicy").default_policy()
    policy.update(managed=True, allow_document_geometry=False, allow_images=False)
    schemas = [
        {"name": "core.inspect", "description": "inspect", "parameters": {"type": "object"}},
        {"name": "conversation.ask_user", "description": "ask", "parameters": {"type": "object"}},
    ]
    context = {
        "document": {"name": "Secret"},
        "reference_images": {"images": [{"path": "/secret.png"}]},
        "provider_tool_schemas": schemas,
        "provider_tool_surface": {
            "kind": "turn_start_snapshot",
            "tool_names": ["core.inspect", "conversation.ask_user"],
            "schema_count": 2,
            "schema_sha256": "old",
        },
    }
    filtered = session._apply_managed_outbound_policy(context, policy, online=True)
    assert "document" not in filtered
    assert "reference_images" not in filtered
    assert [item["name"] for item in filtered["provider_tool_schemas"]] == ["conversation.ask_user"]
    assert filtered["provider_tool_surface"]["tool_names"] == ["conversation.ask_user"]
    assert filtered["provider_tool_surface"]["schema_count"] == 1
    assert filtered["provider_tool_surface"]["schema_sha256"] != "old"


def test_ai_rbac_denial_happens_before_project_or_provider_side_effects() -> None:
    class Service:
        @staticmethod
        def authorize(permission):
            assert permission == "ai.use"
            raise PermissionError("viewer cannot use AI")

        @staticmethod
        def document_persistence_state():
            pytest.fail("persistence read happened after RBAC denial")

    with pytest.raises(PermissionError, match="viewer cannot use AI"):
        session.run_prompt("Create a part", service=Service())


def test_restore_rbac_denial_happens_before_scope_or_cad_access() -> None:
    class Service:
        @staticmethod
        def authorize(permission):
            assert permission == "revision.restore"
            raise PermissionError("reviewer cannot restore")

        @staticmethod
        def project_scope_snapshot():
            pytest.fail("project scope accessed after RBAC denial")

    with pytest.raises(PermissionError, match="reviewer cannot restore"):
        session.restore_accepted_revision(Service(), "a" * 64)
