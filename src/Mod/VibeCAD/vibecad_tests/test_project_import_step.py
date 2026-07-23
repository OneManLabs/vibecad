# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

from tool_impl.service import project_import_step


ASSET_ID = "f" * 32


class Service:
    def __init__(self, root: Path):
        self.root = root
        self.events = []
        self.document = SimpleNamespace(Name="ImportDocument", Uid="document-uid")

    def authorize(self, permission):
        self.events.append(("authorize", permission))

    def _active_document(self):
        self.events.append(("document", None))
        return self.document

    def project_scope_snapshot(self):
        self.events.append(("scope", None))
        return {
            "root": str(self.root),
            "project_id": "typed-step-import-test",
        }


class Candidate:
    def __init__(self, evidence=None):
        self.evidence = dict(evidence or _valid_evidence())
        self.cleaned = False

    def provider_evidence(self):
        return dict(self.evidence)

    def verified_brep_copy(self):
        raise AssertionError("This test must not open the private BREP.")

    def cleanup(self):
        self.cleaned = True


def _valid_evidence() -> dict:
    return {
        "schema": "vibecad-step-validation-result-v2",
        "version": 2,
        "ok": True,
        "project_id": "typed-step-import-test",
        "asset_id": ASSET_ID,
        "asset_sha256": "a" * 64,
        "size_bytes": 100,
        "format": "step",
        "shape": {
            "shape_type": "Solid",
            "null": False,
            "valid": True,
            "topology": {
                "solids": 1,
                "shells": 1,
                "faces": 6,
                "edges": 12,
                "vertices": 8,
            },
            "bounds_mm": {
                "min_x": 0.0,
                "min_y": 0.0,
                "min_z": 0.0,
                "max_x": 40.0,
                "max_y": 30.0,
                "max_z": 10.0,
                "size_x": 40.0,
                "size_y": 30.0,
                "size_z": 10.0,
            },
            "volume_mm3": 12000.0,
        },
        "brep_sha256": "c" * 64,
        "brep_size_bytes": 2048,
        "errors": [],
        "evidence_sha256": "b" * 64,
    }


def _owned_candidate(tmp_path: Path):
    import VibeCADStepValidator as step_validator

    temporary = tempfile.TemporaryDirectory(dir=tmp_path)
    artifact = Path(temporary.name) / "validated.brep"
    brep = b"DBRep_DrawableShape\nowned-candidate\n"
    artifact.write_bytes(brep)
    artifact.chmod(0o600)
    evidence = _valid_evidence()
    evidence.update(
        brep_sha256=hashlib.sha256(brep).hexdigest(),
        brep_size_bytes=len(brep),
    )
    evidence["evidence_sha256"] = step_validator._content_digest(
        evidence, "evidence_sha256"
    )
    return step_validator.ValidatedStepCandidate(
        evidence,
        temporary,
        artifact,
        _seal=step_validator._CANDIDATE_CONSTRUCTOR_SEAL,
    )


def test_provider_schema_accepts_only_an_opaque_asset_id() -> None:
    parameters = project_import_step.TOOL_SPEC["parameters"]
    assert parameters["required"] == ["asset_id"]
    assert set(parameters["properties"]) == {"asset_id"}
    assert parameters["additionalProperties"] is False
    assert "path" not in str(parameters).lower()


def test_capture_checks_policy_and_permission_before_project_or_document_access(
    tmp_path: Path, monkeypatch
) -> None:
    import VibeCADManagedPolicy as policy

    events = []
    monkeypatch.setattr(
        policy,
        "load_managed_policy",
        lambda: (events.append("policy") or policy.default_policy()),
    )
    original_validate = policy.validate_policy
    monkeypatch.setattr(
        policy,
        "validate_policy",
        lambda value: (events.append("policy_validated") or original_validate(value)),
    )
    service = Service(tmp_path)
    original_authorize = service.authorize

    def authorize(permission):
        events.append("permission")
        original_authorize(permission)

    service.authorize = authorize
    captured = project_import_step.capture_import_step(service, ASSET_ID)

    assert events == ["policy", "policy_validated", "permission"]
    assert service.events == [
        ("authorize", "design.modify"),
        ("document", None),
        ("scope", None),
    ]
    assert captured["asset_id"] == ASSET_ID


def test_invalid_managed_policy_fails_before_permission_or_scope(
    tmp_path: Path, monkeypatch
) -> None:
    import VibeCADManagedPolicy as policy

    monkeypatch.setattr(policy, "load_managed_policy", lambda: {"schema": "wrong"})
    service = Service(tmp_path)
    with pytest.raises(RuntimeError, match="policy schema"):
        project_import_step.capture_import_step(service, ASSET_ID)
    assert service.events == []


def test_permission_denial_fails_before_document_or_project_access(
    tmp_path: Path,
) -> None:
    service = Service(tmp_path)

    def deny(permission):
        service.events.append(("authorize", permission))
        raise PermissionError("viewer blocked")

    service.authorize = deny
    with pytest.raises(PermissionError, match="viewer blocked"):
        project_import_step.capture_import_step(service, ASSET_ID)
    assert service.events == [("authorize", "design.modify")]


@pytest.mark.parametrize("value", ["/tmp/part.step", "../part.step", "g" * 32, ""])
def test_direct_tool_rejects_raw_paths_and_invalid_ids_before_validation(
    tmp_path: Path, value: str
) -> None:
    service = Service(tmp_path)
    called = False

    with pytest.raises(ValueError, match="asset id"):
        project_import_step.capture_import_step(service, value)
    assert called is False


def test_direct_run_requires_the_session_acceptance_boundary(tmp_path: Path) -> None:
    service = Service(tmp_path)
    called = False

    def fail(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("Direct run must not validate or mutate.")

    result = project_import_step.run(service, ASSET_ID, validator=fail)
    assert result["ok"] is False
    assert result["failure_code"] == "STEP_IMPORT_SESSION_REQUIRED"
    assert result["failure_stage"] == "precondition"
    assert result["state_change"]["mutation_started"] is False
    assert result["state_change"]["document_changed"] is False
    assert called is False


def test_isolated_validator_receives_only_captured_project_identity(tmp_path: Path) -> None:
    service = Service(tmp_path)
    captured = project_import_step.capture_import_step(service, ASSET_ID)
    observed = {}

    def validate(root, project_id, asset_id, *, cancellation_check):
        observed.update(
            root=root,
            project_id=project_id,
            asset_id=asset_id,
            cancellation_check=cancellation_check,
        )
        return _valid_evidence()

    result = project_import_step.validate_captured_step(
        captured, cancellation_check=lambda: False, validator=validate
    )
    assert result["ok"] is True
    assert observed["root"] == str(tmp_path)
    assert observed["project_id"] == "typed-step-import-test"
    assert observed["asset_id"] == ASSET_ID
    assert callable(observed["cancellation_check"])


def test_scope_drift_rejects_before_asset_resolution_or_mutation(
    tmp_path: Path,
) -> None:
    service = Service(tmp_path)
    captured = project_import_step.capture_import_step(service, ASSET_ID)
    service.document = SimpleNamespace(Name="OtherDocument", Uid="other-uid")
    candidate = _owned_candidate(tmp_path)
    result = project_import_step.publish_validated_step(service, captured, candidate)
    assert result["ok"] is False
    assert result["failure_code"] == "STEP_IMPORT_SCOPE_CHANGED"
    assert result["state_change"]["mutation_started"] is False
    assert candidate.artifact_available() is False


def test_provider_runner_has_an_off_document_thread_validation_phase() -> None:
    from VibeCADSession import make_provider_tool_runner

    source = inspect.getsource(make_provider_tool_runner)
    capture = source.index("captured = _on_document_thread")
    validation = source.index("validation = validate_captured_step", capture)
    publication = source.index("payload = _on_document_thread", validation)
    assert "_on_document_thread" in source[capture:validation]
    assert "_on_document_thread" not in source[validation:publication]
    assert "_on_document_thread" in source[publication:]


def test_dispatch_failure_cleans_candidate_and_redacts_path(
    tmp_path: Path, monkeypatch
) -> None:
    import VibeCADSession as session
    import VibeCADStepValidator as step_validator
    from VibeCADTools import SafetyLevel

    candidate = _owned_candidate(tmp_path)
    dispatch_active = False
    fail_next_dispatch = False
    detached_parse_phases = []

    class DetachedShape:
        def importBrep(self, descriptor_path):
            assert dispatch_active is False
            assert Path(descriptor_path).name == "validated.brep"
            detached_parse_phases.append("outside_dispatch")

        @staticmethod
        def exportBrepToString():
            return "DBRep_DrawableShape\ndetached-owned-candidate\n"

    monkeypatch.setitem(sys.modules, "Part", SimpleNamespace(Shape=DetachedShape))
    monkeypatch.setattr(
        step_validator,
        "shape_evidence",
        lambda _shape: dict(_valid_evidence()["shape"]),
    )

    class Spec:
        requires_document = False
        edit_modes = frozenset({"none"})

        @staticmethod
        def validate_arguments(args):
            assert args == {"asset_id": ASSET_ID}

        @staticmethod
        def supports_edit_mode(mode):
            return mode == "none"

    class Registry:
        @staticmethod
        def get(name):
            assert name == "project.import_step"
            return SimpleNamespace(
                name=name,
                safety=SafetyLevel.SAFE_WRITE,
                workbench="PartWorkbench",
                spec=Spec(),
            )

    class RunnerService:
        registry = Registry()

        @staticmethod
        def authorize(permission):
            assert permission == "design.modify"

    monkeypatch.setattr(
        session,
        "_live_provider_surface_state",
        lambda _service: {
            "workbench": "PartWorkbench",
            "engine": "native",
            "surface_id": "part:native",
            "runtime_state": {"edit_mode": "none"},
            "tool_names": ["project.import_step"],
        },
    )
    monkeypatch.setattr(
        session,
        "_minimal_runtime_state",
        lambda _service: {"edit_mode": "none"},
    )
    monkeypatch.setattr(session, "enforce_provider_tool", lambda *_args, **_kwargs: None)

    def capture(_service, asset_id):
        assert dispatch_active is True
        assert asset_id == ASSET_ID
        return {"asset_id": asset_id}

    def validate(_captured, *, cancellation_check):
        nonlocal fail_next_dispatch
        assert dispatch_active is False
        assert cancellation_check is None
        step_validator.ValidatedStepCandidate.prepare_detached_shape(candidate)
        assert candidate.artifact_available() is False
        fail_next_dispatch = True
        return candidate

    def publish(*_args, **_kwargs):
        raise AssertionError("Publication must not start after dispatch fails.")

    monkeypatch.setattr(project_import_step, "capture_import_step", capture)
    monkeypatch.setattr(project_import_step, "validate_captured_step", validate)
    monkeypatch.setattr(project_import_step, "publish_validated_step", publish)

    def dispatch(callback):
        nonlocal dispatch_active, fail_next_dispatch
        if fail_next_dispatch:
            fail_next_dispatch = False
            raise RuntimeError(f"dispatch failed at {tmp_path / 'private.step'}")
        dispatch_active = True
        try:
            return callback()
        finally:
            dispatch_active = False

    runner_service = RunnerService()
    blocked_runner = session.make_provider_tool_runner(
        runner_service,
        tool_trace=[],
        progress_callback=None,
        cancellation_check=None,
        steering_check=None,
        question_callback=None,
        document_thread_dispatch=dispatch,
    )
    blocked = blocked_runner(
        "project.import_step", '{"asset_id":"' + ASSET_ID + '"}'
    )
    assert blocked["ok"] is False
    assert blocked["failure_code"] == "ACCEPTANCE_BOUNDARY_REQUIRED"
    assert candidate._cleaned is False

    capability = session._issue_prepared_mutation_capability(
        runner_service, SimpleNamespace(acceptance_id="test-step-import")
    )
    runner = session.make_provider_tool_runner(
        runner_service,
        tool_trace=[],
        progress_callback=None,
        cancellation_check=None,
        steering_check=None,
        question_callback=None,
        document_thread_dispatch=dispatch,
        _prepared_mutation_capability=capability,
    )
    result = runner("project.import_step", '{"asset_id":"' + ASSET_ID + '"}')

    assert result["ok"] is False
    assert result["failure_code"] == "STEP_IMPORT_FAILED"
    assert str(tmp_path) not in str(result)
    assert "private.step" not in str(result)
    assert detached_parse_phases == ["outside_dispatch"]
    assert candidate._cleaned is True
    assert candidate._detached_shape is None


def test_publication_source_uses_only_the_validated_private_brep() -> None:
    source = inspect.getsource(project_import_step.publish_validated_step)
    assert "consume_prepared_shape" in source
    assert "importBrep" not in source
    assert "importBrepFromString" not in source
    assert "resolve_import_asset" not in source
    assert "registered.step" not in source


def test_invalid_owned_candidate_evidence_fails_closed(monkeypatch) -> None:
    from VibeCADStepValidator import StepValidationError, ValidatedStepCandidate

    candidate = object.__new__(ValidatedStepCandidate)

    def reject(_candidate):
        raise StepValidationError(
            "STEP_VALIDATION_RESULT_INVALID",
            "The validated STEP candidate is invalid.",
        )

    monkeypatch.setattr(ValidatedStepCandidate, "revalidated_evidence", reject)

    assert project_import_step._candidate_evidence(candidate) is None


def test_forged_candidate_subclass_cannot_bypass_the_constructor_seal() -> None:
    from VibeCADStepValidator import ValidatedStepCandidate

    calls = []

    class ForgedCandidate(ValidatedStepCandidate):
        def __init__(self):
            pass

        def revalidated_evidence(self):
            calls.append("evidence")
            return _valid_evidence()

        def consume_prepared_shape(self):
            calls.append("artifact")
            return object(), {"sha256": "0" * 64, "size_bytes": 1}

        def cleanup(self):
            calls.append("cleanup")

    forged = ForgedCandidate()

    assert project_import_step._candidate_evidence(forged) is None
    project_import_step._cleanup_candidate(forged)
    assert calls == []


def test_native_step_acceptance_test_fails_under_optimized_python() -> None:
    source = Path(__file__).parents[1] / "TestVibeCADStepImport.py"
    completed = subprocess.run(
        [sys.executable, "-O", "-c", f"import runpy; runpy.run_path({str(source)!r})"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode != 0
    assert "requires normal Python assertion semantics" in (
        completed.stdout + completed.stderr
    )
