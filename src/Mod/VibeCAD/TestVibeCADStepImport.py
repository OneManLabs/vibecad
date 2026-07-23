# SPDX-License-Identifier: LGPL-2.1-or-later
"""Accepted-revision workflow for one content-bound native STEP import."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
import unittest

if not __debug__:
    raise RuntimeError(
        "The native STEP acceptance test requires normal Python assertion semantics."
    )

import FreeCAD as App
import Mesh
import Part
import VibeCADSession as session_runtime
import VibeCADStepValidator as step_validator

from VibeCADAcceptance import VibeCADAcceptanceCoordinator
from VibeCADBenchmark import (
    make_case_attempt,
    normalized_usage,
    unrated_instruction_adherence,
    validation_stage,
)
from VibeCADDocumentValidator import validate_open_document, validate_saved_document
from VibeCADImportAssets import register_import_asset
from VibeCADProvider import BaseProvider, ProviderResult, ProviderUnavailable
from VibeCADRevision import VibeCADRevisionStore, create_revision_record
from VibeCADStepValidator import StepValidationError, validate_registered_step
from VibeCADTools import ToolRegistry
from VibeCADSession import make_provider_tool_runner
from tool_impl.service import register_tools
from tool_impl.service import part_boolean, project_export, project_import_step


_PROJECT_HASH_EXCLUSIONS = {"acceptance", "audit", "conversations", "revisions"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _project_hash(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for item in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        relative = item.relative_to(root)
        if relative.parts[0] in _PROJECT_HASH_EXCLUSIONS:
            continue
        encoded = relative.as_posix().encode("utf-8")
        if item.is_symlink():
            digest.update(b"L\0" + encoded + b"\0" + os.readlink(item).encode("utf-8"))
        elif item.is_dir():
            digest.update(b"D\0" + encoded + b"\0")
        else:
            digest.update(b"F\0" + encoded + b"\0" + _sha256(item).encode("ascii"))
    return digest.hexdigest()


def _revision_record(
    project_id: str,
    parent_revision: str | None,
    *,
    request: str,
    timestamp: str,
    tool_operations: list[dict],
    changed_objects: list[str],
    validation_results: list[dict],
) -> dict:
    return create_revision_record(
        project_id=project_id,
        parent_revision=parent_revision,
        user_request=request,
        interpreted_intent=request,
        assumptions=[
            "The registered STEP contains one valid solid.",
            "The imported source remains a native linked feature under the cut.",
        ],
        plan=[{"operation": item["tool"]} for item in tool_operations],
        tool_operations=tool_operations,
        changed_objects=[{"name": name, "change": "created"} for name in changed_objects],
        validation_results=validation_results,
        provider="integration",
        model="deterministic-step-import-v1",
        timestamp=timestamp,
        generated_source=None,
        preview_image=None,
        rollback={"available": True},
        transaction_id=f"transaction-{timestamp}",
        document_revision=f"document-{timestamp}",
    )


class _Service:
    def __init__(self, holder: dict, root: Path, project_id: str):
        self.holder = holder
        self.root = root
        self.project_id = project_id
        self.permissions: list[str] = []
        self.events: list[dict] = []
        self._design_brief = {
            "purpose": "",
            "revision": "0" * 64,
        }
        self.registry = ToolRegistry()
        register_tools(self.registry, self)

    def authorize(self, permission):
        if permission not in {"ai.use", "design.modify", "export"}:
            raise PermissionError(f"Unexpected permission: {permission}")
        self.permissions.append(permission)

    def _active_document(self):
        return self.holder["document"]

    def project_scope_snapshot(self):
        document = self._active_document()
        return {
            "root": str(self.root),
            "project_id": self.project_id,
            "manifest_path": str(self.root / "project.json"),
            "document": {
                "document": str(document.Name),
                "file_path": str(document.FileName),
                "saved": True,
            },
        }

    def document_persistence_state(self):
        return {
            "enabled": True,
            "file_path": str(self._active_document().FileName),
        }

    def route_modeling_strategy(self, _prompt):
        return None

    def structural_document_revision(self):
        names = ",".join(obj.Name for obj in self._active_document().Objects)
        return hashlib.sha256(names.encode("utf-8")).hexdigest()

    def design_brief(self):
        return dict(self._design_brief)

    def apply_design_brief_update(self, update):
        if update["base_revision"] != self._design_brief["revision"]:
            raise ValueError("The design brief base revision changed.")
        purpose = str(update["changes"].get("purpose") or "")
        revision = hashlib.sha256(purpose.encode("utf-8")).hexdigest()
        self._design_brief = {"purpose": purpose, "revision": revision}
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "design-brief.json").write_text(
            json.dumps(self._design_brief, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return dict(self._design_brief)

    def record_audit_event(self, **event):
        self.events.append(event)

    def active_workbench_name(self):
        return "PartWorkbench"

    def modeling_engine(self):
        return "native"

    def design_review_enabled(self):
        return False

    def provider_edit_object_summary(self):
        return None

    def note_provider_tool_targets(self, _arguments, _payload):
        return None


class _RunPromptStepProvider(BaseProvider):
    model = "deterministic-run-prompt-step-v1"

    def __init__(self, asset_id: str):
        self.asset_id = asset_id

    def run(
        self,
        _prompt,
        context,
        tool_runner=None,
        cancellation_check=None,
        progress_callback=None,
    ):
        del cancellation_check, progress_callback
        imported = tool_runner(
            "project.import_step",
            json.dumps({"asset_id": self.asset_id}),
        )
        if not imported.get("ok"):
            raise ProviderUnavailable(f"STEP import failed: {imported}")
        brief = tool_runner(
            "core.update_design_brief",
            json.dumps(
                {
                    "base_revision": context["design_brief"]["revision"],
                    "changes": {
                        "purpose": "Import one registered STEP solid.",
                    },
                }
            ),
        )
        if not brief.get("ok"):
            raise ProviderUnavailable(f"Design brief update failed: {brief}")
        return ProviderResult("Imported and validated the registered STEP solid.")


class StepImportAcceptanceTest(unittest.TestCase):
    """Prove content-bound import, review, acceptance, reopen, and restore."""

    def test_run_prompt_import_creates_exactly_one_revision(self) -> None:
        prior_documents = set(App.listDocuments())
        with tempfile.TemporaryDirectory(prefix="vibecad-step-run-prompt-") as name:
            root = Path(name)
            project_root = root / "project"
            canonical = root / "accepted.FCStd"
            metadata_path = project_root / "accepted-head.json"
            project_id = "native-step-run-prompt"
            holder = {"document": App.newDocument("StepRunPromptAcceptance")}
            document = holder["document"]
            document.saveAs(str(canonical))
            App.setActiveDocument(document.Name)
            service = _Service(holder, project_root, project_id)

            source_path = root / "selected-solid.step"
            source_document = App.newDocument("StepRunPromptSource")
            source_feature = source_document.addObject("Part::Feature", "SourceSolid")
            source_feature.Shape = Part.makeBox(12, 8, 4)
            source_document.recompute()
            Part.export([source_feature], str(source_path))
            App.closeDocument(source_document.Name)
            App.setActiveDocument(document.Name)
            asset = register_import_asset(
                project_root,
                project_id,
                source_path,
                policy_check=lambda: None,
                permission_check=service.authorize,
                asset_id_factory=lambda: "9" * 32,
                now=lambda: "2026-07-22T12:10:00Z",
            )

            def save_copy(path: Path) -> None:
                holder["document"].saveCopy(str(path))

            def restore_live(_path: Path) -> None:
                holder["document"].restore()
                holder["document"].recompute()

            def write_metadata(revision_id: str | None) -> None:
                metadata_path.parent.mkdir(parents=True, exist_ok=True)
                metadata_path.write_text(
                    json.dumps({"accepted_revision": revision_id}, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )

            original_context = session_runtime._context_for_provider
            original_callbacks = session_runtime._acceptance_callbacks
            original_persist = session_runtime._persist_session_conversation_turn
            session_runtime._context_for_provider = lambda *_args, **_kwargs: {
                "workbench": "PartWorkbench",
                "design_brief": service.design_brief(),
                "provider_tool_schemas": [],
                "registered_import_assets": {
                    "assets": [
                        {
                            "asset_id": asset["asset_id"],
                            "availability": "verified",
                        }
                    ]
                },
            }
            session_runtime._acceptance_callbacks = lambda *_args: (
                save_copy,
                restore_live,
                validate_saved_document,
                write_metadata,
            )
            session_runtime._persist_session_conversation_turn = (
                lambda *_args, **_kwargs: {"conversation_id": "step-run-prompt"}
            )
            try:
                response = session_runtime.run_prompt(
                    "Import the registered STEP solid.",
                    service=service,
                    provider=_RunPromptStepProvider(asset["asset_id"]),
                    prefer_online=False,
                    candidate_decision_callback=lambda _candidate: "accept",
                )
            finally:
                session_runtime._context_for_provider = original_context
                session_runtime._acceptance_callbacks = original_callbacks
                session_runtime._persist_session_conversation_turn = original_persist

            revisions = VibeCADRevisionStore(project_root, project_id)
            self.assertIsNone(response.error)
            self.assertEqual(len(revisions.list_records()), 1)
            self.assertEqual(
                response.context["candidate_decision"]["revision_id"],
                revisions.head()["revision_id"],
            )
            self.assertEqual(
                response.context["candidate_decision"]["mode"], "human"
            )
            imported = document.getObject("ImportedSTEP")
            self.assertIsNotNone(imported)
            self.assertAlmostEqual(imported.Shape.Volume, 12 * 8 * 4, places=7)

            App.closeDocument(document.Name)
            reopened = App.openDocument(str(canonical))
            holder["document"] = reopened
            reopened.recompute()
            self.assertIsNotNone(reopened.getObject("ImportedSTEP"))
            self.assertTrue(validate_open_document(reopened)["ok"])

        for document_name in set(App.listDocuments()) - prior_documents:
            App.closeDocument(document_name)

    def test_step_import_acceptance_boundary(self) -> None:
        self.assertTrue(
            __debug__,
            "The native STEP acceptance test cannot run with Python optimization.",
        )
        started = time.monotonic()
        prior_documents = set(App.listDocuments())
        with tempfile.TemporaryDirectory(prefix="vibecad-step-import-") as name:
            root = Path(name)
            project_root = root / "project"
            canonical = root / "accepted.FCStd"
            metadata_path = project_root / "accepted-head.json"
            project_id = "native-step-import-acceptance"
            holder = {"document": App.newDocument("StepImportAcceptance")}
            document = holder["document"]
            document.saveAs(str(canonical))
            App.setActiveDocument(document.Name)
            service = _Service(holder, project_root, project_id)
            coordinator = VibeCADAcceptanceCoordinator(project_root, project_id)
            revisions = VibeCADRevisionStore(project_root, project_id)

            def save_copy(path: Path) -> None:
                holder["document"].saveCopy(str(path))

            def restore_live(_path: Path) -> None:
                holder["document"].restore()
                holder["document"].recompute()

            def write_metadata(revision_id: str | None) -> None:
                metadata_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = metadata_path.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps({"accepted_revision": revision_id}, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary, metadata_path)

            # Establish one accepted parent with a reopenable native feature.
            parent_prepared = coordinator.prepare(canonical, save_copy)
            parent = document.addObject("Part::Feature", "ParentMarker")
            parent.Label = "Accepted parent marker"
            parent.Shape = Part.makeBox(2, 2, 2)
            document.recompute()
            parent_record = _revision_record(
                project_id,
                None,
                request="Create the accepted parent marker.",
                timestamp="2026-07-22T12:00:00Z",
                tool_operations=[{"tool": "part.parent_fixture", "ok": True}],
                changed_objects=[parent.Name],
                validation_results=[{"name": "parent_shape_valid", "ok": parent.Shape.isValid()}],
            )
            coordinator.validate_candidate(
                parent_prepared,
                parent_record,
                save_copy=save_copy,
                validate_document=validate_saved_document,
                restore_live=restore_live,
                write_metadata=write_metadata,
            )
            parent_acceptance = coordinator.accept_validated_candidate(
                parent_prepared,
                restore_live=restore_live,
                write_metadata=write_metadata,
                acceptance_mode="automatic",
            )
            parent_revision = parent_acceptance["revision"]["revision_id"]
            self.assertEqual(len(revisions.list_records()), 1)
            self.assertEqual(revisions.head()["revision_id"], parent_revision)

            # Create an exact 40 x 30 x 10 mm STEP source outside the accepted document.
            source_path = root / "selected-solid.step"
            source_document = App.newDocument("StepSourceFixture")
            source_feature = source_document.addObject("Part::Feature", "SourceSolid")
            source_feature.Shape = Part.makeBox(40, 30, 10)
            source_document.recompute()
            Part.export([source_feature], str(source_path))
            App.closeDocument(source_document.Name)
            App.setActiveDocument(document.Name)
            asset = register_import_asset(
                project_root,
                project_id,
                source_path,
                policy_check=lambda: None,
                permission_check=service.authorize,
                asset_id_factory=lambda: "1" * 32,
                now=lambda: "2026-07-22T12:01:00Z",
            )
            self.assertNotIn("path", asset)

            # Malformed and truncated registered files fail in the isolated
            # process and cannot change the accepted document or revision.
            invalid_sources = {
                "2" * 32: b"ISO-10303-21;\nmalformed\nEND-ISO-10303-21;\n",
                "3" * 32: source_path.read_bytes()[: source_path.stat().st_size // 2],
            }
            for invalid_id, invalid_bytes in invalid_sources.items():
                invalid_path = root / f"invalid-{invalid_id[0]}.step"
                invalid_path.write_bytes(invalid_bytes)
                invalid_asset = register_import_asset(
                    project_root,
                    project_id,
                    invalid_path,
                    policy_check=lambda: None,
                    permission_check=service.authorize,
                    asset_id_factory=lambda value=invalid_id: value,
                    now=lambda: "2026-07-22T12:01:30Z",
                )
                before_invalid_sha = _sha256(canonical)
                before_invalid_head = revisions.head()["revision_id"]
                before_invalid_project = _project_hash(project_root)
                before_invalid_objects = [obj.Name for obj in document.Objects]
                with self.assertRaises(StepValidationError) as invalid_error:
                    validate_registered_step(
                        project_root, project_id, invalid_asset["asset_id"]
                    )
                self.assertEqual(invalid_error.exception.code, "STEP_CONTENT_INVALID")
                self.assertEqual(_sha256(canonical), before_invalid_sha)
                self.assertEqual(revisions.head()["revision_id"], before_invalid_head)
                self.assertEqual(_project_hash(project_root), before_invalid_project)
                self.assertEqual([obj.Name for obj in document.Objects], before_invalid_objects)

            review_canonical_sha = _sha256(canonical)
            review_project_sha = _project_hash(project_root)
            review_head = revisions.head()["revision_id"]
            review_metadata = metadata_path.read_bytes()

            # A provenance write fault must abort the live native transaction.
            captured = project_import_step.capture_import_step(service, asset["asset_id"])
            isolated = project_import_step.validate_captured_step(captured)

            # A duck-typed candidate cannot substitute another private BREP,
            # even when the substitute has the same coarse shape evidence.
            duck_evidence = isolated.provider_evidence()
            isolated.cleanup()
            alternate_brep_path = root / "private-alternate.brep"
            alternate_points = [
                App.Vector(40, 0, 0),
                App.Vector(40, 30, 0),
                App.Vector(0, 30, 0),
                App.Vector(0, 0, 0),
                App.Vector(40, 0, 0),
            ]
            alternate_shape = Part.Face(
                Part.makePolygon(alternate_points)
            ).extrude(App.Vector(0, 0, 10))
            self.assertTrue(
                project_import_step.compare_shape_evidence(
                    duck_evidence["shape"],
                    project_import_step.shape_evidence(alternate_shape),
                )["ok"]
            )
            alternate_shape.exportBrep(str(alternate_brep_path))
            alternate_brep_path.chmod(0o600)

            class DuckCandidate:
                def __init__(self):
                    self.cleaned = False

                def provider_evidence(self):
                    return dict(duck_evidence)

                @contextmanager
                def verified_brep_copy(self):
                    yield alternate_brep_path

                def cleanup(self):
                    self.cleaned = True

            duck_candidate = DuckCandidate()
            failed_duck = project_import_step.publish_validated_step(
                service, captured, duck_candidate
            )
            self.assertFalse(failed_duck["ok"])
            self.assertTrue(duck_candidate.cleaned)
            self.assertIsNone(document.getObject("ImportedSTEP"))
            self.assertNotIn(str(root), json.dumps(failed_duck, default=str))

            isolated = project_import_step.validate_captured_step(captured)

            def provenance_fault(stage: str) -> None:
                if stage == "after_provenance_write":
                    raise RuntimeError("injected provenance write fault")

            failed_provenance = project_import_step.publish_validated_step(
                service, captured, isolated, fault=provenance_fault
            )
            self.assertFalse(failed_provenance["ok"])
            self.assertFalse(
                failed_provenance["state_change"]["document_changed"],
                failed_provenance,
            )
            self.assertIsNone(document.getObject("ImportedSTEP"))
            self.assertEqual(_sha256(canonical), review_canonical_sha)
            self.assertEqual(_project_hash(project_root), review_project_sha)
            self.assertEqual(revisions.head()["revision_id"], review_head)

            # A verifier fault occurs after object and property creation. The
            # exact compensator must remove every candidate change.
            verifier_candidate = project_import_step.validate_captured_step(captured)

            def verifier_fault(stage: str) -> None:
                if stage == "before_verifier_success":
                    raise RuntimeError("injected verifier fault")

            failed_verifier = project_import_step.publish_validated_step(
                service, captured, verifier_candidate, fault=verifier_fault
            )
            self.assertFalse(failed_verifier["ok"])
            self.assertTrue(failed_verifier["rollback_attempted"])
            self.assertTrue(failed_verifier["rollback_succeeded"])
            self.assertEqual(failed_verifier["document_delta"]["created_objects"], [])
            self.assertEqual(failed_verifier["document_delta"]["changed_objects"], [])
            self.assertEqual(failed_verifier["document_delta"]["deleted_objects"], [])
            self.assertIsNone(document.getObject("ImportedSTEP"))

            # A service-scope switch during verification must not redirect the
            # compensator to a same-named object in another document.
            scope_candidate = project_import_step.validate_captured_step(captured)
            other_document = App.newDocument("StepRollbackOtherScope")
            other_feature = other_document.addObject("Part::Feature", "ImportedSTEP")
            other_feature.Shape = Part.makeBox(7, 7, 7)
            other_document.recompute()
            App.setActiveDocument(document.Name)

            def scope_switch_fault(stage: str) -> None:
                if stage == "before_verifier_success":
                    holder["document"] = other_document
                    App.setActiveDocument(other_document.Name)
                    raise RuntimeError("injected service scope switch")

            failed_scope = project_import_step.publish_validated_step(
                service, captured, scope_candidate, fault=scope_switch_fault
            )
            holder["document"] = document
            App.setActiveDocument(document.Name)
            self.assertFalse(failed_scope["ok"])
            self.assertTrue(failed_scope["rollback_succeeded"], failed_scope)
            self.assertIsNone(document.getObject("ImportedSTEP"))
            self.assertIsNotNone(other_document.getObject("ImportedSTEP"))
            App.closeDocument(other_document.Name)
            App.setActiveDocument(document.Name)

            # A different BREP can have identical coarse topology, bounds, and
            # volume. Exact native BREP verification must still reject it.
            exact_candidate = project_import_step.validate_captured_step(captured)

            def equal_evidence_brep_swap(stage: str) -> None:
                if stage != "before_verifier_success":
                    return
                feature = document.getObject("ImportedSTEP")
                self.assertIsNotNone(feature)
                points = [
                    App.Vector(40, 0, 0),
                    App.Vector(40, 30, 0),
                    App.Vector(0, 30, 0),
                    App.Vector(0, 0, 0),
                    App.Vector(40, 0, 0),
                ]
                alternate = Part.Face(Part.makePolygon(points)).extrude(
                    App.Vector(0, 0, 10)
                )
                coarse = project_import_step.compare_shape_evidence(
                    project_import_step.shape_evidence(feature.Shape),
                    project_import_step.shape_evidence(alternate),
                )
                self.assertTrue(coarse["ok"], coarse)
                self.assertNotEqual(
                    project_import_step._native_brep_identity(feature.Shape),
                    project_import_step._native_brep_identity(alternate),
                )
                feature.Shape = alternate
                document.recompute()

            failed_exact = project_import_step.publish_validated_step(
                service,
                captured,
                exact_candidate,
                fault=equal_evidence_brep_swap,
            )
            self.assertFalse(failed_exact["ok"])
            self.assertTrue(failed_exact["rollback_succeeded"])
            self.assertIsNone(document.getObject("ImportedSTEP"))
            exact_checks = {
                check["name"]: check["ok"]
                for check in failed_exact["verification"]["checks"]
            }
            self.assertFalse(exact_checks["native_shape_matches_published_brep_exactly"])

            prepared = coordinator.prepare(canonical, save_copy)
            dispatch_active = False
            dispatch_phases: list[str] = []
            source_auth_phases: list[str] = []
            original_capture = project_import_step.capture_import_step
            original_validate = project_import_step.validate_captured_step
            original_publish = project_import_step.publish_validated_step
            original_copy_asset = step_validator.copy_registered_import_asset
            original_resolve_asset = step_validator.resolve_import_asset

            def dispatched(operation):
                nonlocal dispatch_active
                self.assertFalse(dispatch_active)
                dispatch_active = True
                try:
                    return operation()
                finally:
                    dispatch_active = False

            def traced_capture(*args, **kwargs):
                self.assertTrue(dispatch_active)
                dispatch_phases.append("capture")
                return original_capture(*args, **kwargs)

            def traced_validate(*args, **kwargs):
                self.assertFalse(dispatch_active)
                dispatch_phases.append("validation")
                return original_validate(*args, **kwargs)

            def traced_publish(*args, **kwargs):
                self.assertTrue(dispatch_active)
                dispatch_phases.append("publish")
                return original_publish(*args, **kwargs)

            def traced_copy_asset(*args, **kwargs):
                self.assertFalse(dispatch_active)
                source_auth_phases.append("copy")
                return original_copy_asset(*args, **kwargs)

            def traced_resolve_asset(*args, **kwargs):
                self.assertFalse(dispatch_active)
                source_auth_phases.append("resolve")
                return original_resolve_asset(*args, **kwargs)

            project_import_step.capture_import_step = traced_capture
            project_import_step.validate_captured_step = traced_validate
            project_import_step.publish_validated_step = traced_publish
            step_validator.copy_registered_import_asset = traced_copy_asset
            step_validator.resolve_import_asset = traced_resolve_asset
            tool_trace: list[dict] = []
            prepared_capability = session_runtime._issue_prepared_mutation_capability(
                service, prepared
            )
            try:
                runner = make_provider_tool_runner(
                    service,
                    tool_trace=tool_trace,
                    progress_callback=None,
                    cancellation_check=None,
                    steering_check=None,
                    question_callback=None,
                    document_thread_dispatch=dispatched,
                    _prepared_mutation_capability=prepared_capability,
                )
                imported = runner(
                    "project.import_step",
                    json.dumps({"asset_id": asset["asset_id"]}),
                )
            finally:
                session_runtime._revoke_prepared_mutation_capability(
                    prepared_capability
                )
                project_import_step.capture_import_step = original_capture
                project_import_step.validate_captured_step = original_validate
                project_import_step.publish_validated_step = original_publish
                step_validator.copy_registered_import_asset = original_copy_asset
                step_validator.resolve_import_asset = original_resolve_asset
            self.assertTrue(imported["ok"], imported)
            self.assertEqual(
                [phase for phase in dispatch_phases if phase in {"capture", "validation", "publish"}],
                ["capture", "validation", "publish"],
            )
            self.assertTrue(source_auth_phases)
            self.assertEqual(len(tool_trace), 1)
            self.assertTrue(tool_trace[0]["ok"])
            isolated = imported["validation"]
            imported_name = imported["mutation"]["feature"]
            imported_feature = document.getObject(imported_name)
            self.assertIsNotNone(imported_feature)

            # The through-hole uses a native parametric cylinder and a native linked cut.
            cylinder = document.addObject("Part::Cylinder", "CenteredHoleTool")
            cylinder.Label = "Centered 6 mm through hole"
            cylinder.Radius = 3.0
            cylinder.Height = 10.0
            cylinder.Placement.Base = App.Vector(20.0, 15.0, 0.0)
            cylinder_name = str(cylinder.Name)
            document.recompute()
            cut_result = part_boolean.run(
                service,
                "cut",
                imported_name,
                [cylinder_name],
                "Imported solid with centered 6 mm hole",
                True,
            )
            self.assertTrue(cut_result["ok"], cut_result)
            cut_name = cut_result["mutation"]["feature"]
            cut = document.getObject(cut_name)
            document.recompute()
            expected_volume = 12000.0 - math.pi * 3.0 * 3.0 * 10.0
            cylindrical_faces = sum(
                type(face.Surface).__name__ == "Cylinder" for face in cut.Shape.Faces
            )
            self.assertTrue(cut.Shape.isValid())
            self.assertEqual(len(cut.Shape.Solids), 1)
            self.assertAlmostEqual(cut.Shape.BoundBox.XLength, 40.0, places=7)
            self.assertAlmostEqual(cut.Shape.BoundBox.YLength, 30.0, places=7)
            self.assertAlmostEqual(cut.Shape.BoundBox.ZLength, 10.0, places=7)
            self.assertAlmostEqual(cut.Shape.Volume, expected_volume, places=6)
            self.assertEqual(cylindrical_faces, 1)
            self.assertIs(cut.Base, imported_feature)
            self.assertIs(cut.Tool, cylinder)
            self.assertEqual(imported_feature.VibeCADImportAssetId, asset["asset_id"])
            self.assertEqual(imported_feature.VibeCADImportAssetSHA256, asset["sha256"])
            self.assertEqual(imported_feature.VibeCADImportProjectId, project_id)
            self.assertEqual(
                imported_feature.VibeCADImportValidationSHA256,
                isolated["evidence_sha256"],
            )

            import_record = _revision_record(
                project_id,
                parent_revision,
                request="Import the registered STEP and add a centered 6 mm through-hole.",
                timestamp="2026-07-22T12:02:00Z",
                tool_operations=[
                    {
                        "tool": "project.import_step",
                        "asset_id": asset["asset_id"],
                        "asset_sha256": asset["sha256"],
                        "validation_sha256": isolated["evidence_sha256"],
                        "ok": True,
                    },
                    {"tool": "part.boolean", "operation": "cut", "ok": True},
                ],
                changed_objects=[imported_name, cylinder_name, cut_name],
                validation_results=[
                    {"name": "shape_valid", "ok": cut.Shape.isValid()},
                    {"name": "exact_bounds", "ok": True, "value_mm": [40, 30, 10]},
                    {"name": "centered_hole_diameter", "ok": True, "value_mm": 6},
                    {"name": "isolated_step_validation", "ok": isolated["ok"]},
                ],
            )
            validated_review = coordinator.validate_candidate(
                prepared,
                import_record,
                save_copy=save_copy,
                validate_document=validate_saved_document,
                restore_live=restore_live,
                write_metadata=write_metadata,
            )
            self.assertEqual(validated_review["state"], "awaiting_decision")
            self.assertEqual(_sha256(canonical), review_canonical_sha)
            self.assertEqual(_project_hash(project_root), review_project_sha)
            self.assertEqual(revisions.head()["revision_id"], parent_revision)
            self.assertEqual(metadata_path.read_bytes(), review_metadata)

            accepted = coordinator.accept_validated_candidate(
                prepared,
                restore_live=restore_live,
                write_metadata=write_metadata,
                acceptance_mode="human",
            )
            import_revision = accepted["revision"]["revision_id"]
            self.assertEqual(len(revisions.list_records()), 2)
            self.assertEqual(revisions.head()["revision_id"], import_revision)
            self.assertEqual(
                json.loads(metadata_path.read_text(encoding="utf-8"))["accepted_revision"],
                import_revision,
            )
            comparison = revisions.compare(parent_revision, import_revision)
            self.assertTrue(comparison["changed"])
            self.assertIn(cut_name, comparison["objects_added"])

            App.closeDocument(document.Name)
            reopened = App.openDocument(str(canonical))
            holder["document"] = reopened
            App.setActiveDocument(reopened.Name)
            reopened.recompute()
            reopened_validation = validate_open_document(reopened)
            self.assertTrue(reopened_validation["ok"], reopened_validation)
            reopened_cut = reopened.getObject(cut_name)
            reopened_import = reopened.getObject(imported_name)
            reopened_cylinder = reopened.getObject(cylinder_name)
            self.assertIsNotNone(reopened_cut)
            self.assertIs(reopened_cut.Base, reopened_import)
            self.assertIs(reopened_cut.Tool, reopened_cylinder)
            self.assertAlmostEqual(reopened_cut.Shape.Volume, expected_volume, places=6)
            self.assertEqual(reopened_import.VibeCADImportAssetSHA256, asset["sha256"])
            reopened_native_identity = project_import_step._native_brep_identity(
                reopened_import.Shape
            )
            self.assertEqual(
                reopened_import.VibeCADImportNativeShapeSHA256,
                reopened_native_identity["sha256"],
            )
            self.assertEqual(
                reopened_import.VibeCADImportNativeShapeByteSize,
                reopened_native_identity["size_bytes"],
            )

            step_export = project_export.run(
                service, [cut_name], "step", "accepted-import-with-hole"
            )
            stl_export = project_export.run(
                service, [cut_name], "stl", "accepted-import-with-hole"
            )
            self.assertTrue(step_export["ok"], step_export)
            self.assertTrue(stl_export["ok"], stl_export)
            exported_step = Path(step_export["export"]["path"])
            exported_stl = Path(stl_export["export"]["path"])
            roundtrip_shape = Part.read(str(exported_step))
            roundtrip_mesh = Mesh.Mesh(str(exported_stl))
            self.assertTrue(roundtrip_shape.isValid())
            self.assertAlmostEqual(roundtrip_shape.Volume, expected_volume, places=5)
            self.assertGreater(roundtrip_mesh.CountFacets, 0)

            # A candidate save fault cannot change the accepted CAD or head.
            accepted_sha = _sha256(canonical)
            accepted_project_sha = _project_hash(project_root)
            accepted_metadata = metadata_path.read_bytes()
            fault_prepared = coordinator.prepare(canonical, save_copy)
            fault_feature = reopened.addObject("Part::Feature", "UnacceptedSaveFault")
            fault_feature.Shape = Part.makeBox(3, 3, 3)
            reopened.recompute()

            def failed_save(_path: Path) -> None:
                raise OSError("injected candidate CAD save fault")

            with self.assertRaisesRegex(RuntimeError, "candidate CAD save fault"):
                coordinator.validate_candidate(
                    fault_prepared,
                    import_record,
                    save_copy=failed_save,
                    validate_document=validate_saved_document,
                    restore_live=restore_live,
                    write_metadata=write_metadata,
                )
            self.assertEqual(_sha256(canonical), accepted_sha)
            self.assertEqual(_project_hash(project_root), accepted_project_sha)
            self.assertEqual(revisions.head()["revision_id"], import_revision)
            self.assertEqual(metadata_path.read_bytes(), accepted_metadata)
            self.assertIsNone(holder["document"].getObject("UnacceptedSaveFault"))

            restored = coordinator.restore_revision(
                parent_revision,
                canonical,
                save_copy=save_copy,
                validate_document=validate_saved_document,
                restore_live=restore_live,
                write_metadata=write_metadata,
            )
            self.assertTrue(restored["ok"])
            self.assertEqual(revisions.head()["revision_id"], parent_revision)
            App.closeDocument(holder["document"].Name)
            parent_reopened = App.openDocument(str(canonical))
            holder["document"] = parent_reopened
            App.setActiveDocument(parent_reopened.Name)
            parent_reopened.recompute()
            self.assertIsNotNone(parent_reopened.getObject("ParentMarker"))
            self.assertIsNone(parent_reopened.getObject(imported_name))
            self.assertIsNone(parent_reopened.getObject(cut_name))
            self.assertTrue(validate_open_document(parent_reopened)["ok"])

            attempt = make_case_attempt(
                tier=3,
                case_id="t3_content_bound_step_import_and_native_edit",
                attempt=1,
                provider="deterministic",
                model="typed-step-import-v1",
                executor="freecad-native-acceptance",
                live_model_score=False,
                stages={
                    "geometry": validation_stage(
                        applicable=True,
                        passed=True,
                        evidence={
                            "shape_valid": True,
                            "solid_count": 1,
                            "cylindrical_face_count": cylindrical_faces,
                            "volume_mm3": expected_volume,
                        },
                    ),
                    "dimensions": validation_stage(
                        applicable=True,
                        passed=True,
                        evidence={"bounds_mm": [40, 30, 10], "hole_diameter_mm": 6},
                    ),
                    "constraints": validation_stage(
                        applicable=False,
                        reason="A neutral STEP source has no native sketch constraints.",
                    ),
                    "editability": validation_stage(
                        applicable=True,
                        passed=True,
                        evidence={
                            "import_type": "Part::Feature",
                            "hole_tool_type": "Part::Cylinder",
                            "cut_type": "Part::Cut",
                            "linked_dependencies": [imported_name, cylinder_name],
                            "provenance_sha256": isolated["evidence_sha256"],
                        },
                    ),
                    "follow_up": validation_stage(
                        applicable=True,
                        passed=True,
                        evidence={"operation": "centered_native_hole_after_import"},
                    ),
                    "reopen": validation_stage(
                        applicable=True,
                        passed=True,
                        evidence={"accepted_revision": import_revision, "restore_parent": True},
                    ),
                    "export": validation_stage(
                        applicable=True,
                        passed=True,
                        evidence={
                            "step_sha256": step_export["export"]["sha256"],
                            "stl_sha256": stl_export["export"]["sha256"],
                            "step_reopen_valid": True,
                            "stl_facets": roundtrip_mesh.CountFacets,
                        },
                    ),
                },
                question_count=0,
                unnecessary_question_count=0,
                retry_count=0,
                usage=normalized_usage(),
                instruction_adherence=unrated_instruction_adherence(
                    "This deterministic native run is not a live-model score."
                ),
                elapsed_seconds=time.monotonic() - started,
                artifact_paths=[
                    "accepted.FCStd",
                    "accepted-import-with-hole.step",
                    "accepted-import-with-hole.stl",
                ],
            )
            self.assertTrue(attempt["passed"])
            self.assertFalse(attempt["live_model_score"])
            evidence_path = os.environ.get("VIBECAD_STEP_IMPORT_EVIDENCE", "").strip()
            if evidence_path:
                target = Path(evidence_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    json.dumps(attempt, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

        for document_name in set(App.listDocuments()) - prior_documents:
            App.closeDocument(document_name)


def suite() -> unittest.TestSuite:
    return unittest.defaultTestLoader.loadTestsFromTestCase(StepImportAcceptanceTest)
