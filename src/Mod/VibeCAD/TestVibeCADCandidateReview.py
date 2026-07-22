# SPDX-License-Identifier: LGPL-2.1-or-later
"""Deterministic FreeCAD GUI test for validated candidate review."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets
from PySide6 import QtTest

import VibeCADGui as VibeGui
from VibeCADAudit import VibeCADAuditStore
from VibeCADCore import VibeCADService
from VibeCADProvider import BaseProvider, ProviderResult
from VibeCADRevision import VibeCADRevisionStore
from VibeCADSession import RUN_STATES, run_prompt


_PROJECT_HASH_EXCLUSIONS = {"acceptance", "audit", "conversations", "revisions"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _accepted_project_hash(root: Path) -> str:
    """Hash accepted project data but not acceptance or history records."""
    digest = hashlib.sha256()
    for item in sorted(
        root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()
    ):
        relative_path = item.relative_to(root)
        if relative_path.parts[0] in _PROJECT_HASH_EXCLUSIONS:
            continue
        relative = relative_path.as_posix().encode("utf-8")
        if item.is_symlink():
            digest.update(
                b"L\0"
                + relative
                + b"\0"
                + os.readlink(item).encode("utf-8")
                + b"\0"
            )
        elif item.is_dir():
            digest.update(b"D\0" + relative + b"\0")
        else:
            digest.update(
                b"F\0"
                + relative
                + b"\0"
                + _sha256(item).encode("ascii")
                + b"\0"
            )
    return digest.hexdigest()


def _accepted_project_entries(root: Path) -> dict[str, str]:
    """Return a readable map for candidate-review invariant failures."""
    result: dict[str, str] = {}
    for item in sorted(
        root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()
    ):
        relative_path = item.relative_to(root)
        if relative_path.parts[0] in _PROJECT_HASH_EXCLUSIONS:
            continue
        relative = relative_path.as_posix()
        if item.is_symlink():
            result[relative] = f"link:{os.readlink(item)}"
        elif item.is_dir():
            result[relative] = "directory"
        else:
            result[relative] = _sha256(item)
    return result


def _head_id(store: VibeCADRevisionStore) -> str | None:
    head = store.head()
    return str(head.get("revision_id")) if head else None


class _DeterministicBoxProvider(BaseProvider):
    """Create one native, editable Part Design box with no provider I/O."""

    model = "candidate-review-deterministic-v1"

    def __init__(self, capture_baseline) -> None:
        self._capture_baseline = capture_baseline

    def run(
        self,
        prompt,
        context,
        tool_runner=None,
        cancellation_check=None,
        progress_callback=None,
    ):
        del prompt, cancellation_check, progress_callback
        self._capture_baseline()
        calls = []

        def call(name, arguments):
            result = tool_runner(
                name, json.dumps(arguments, ensure_ascii=True, separators=(",", ":"))
            )
            calls.append({"name": name, "result": result})
            if result.get("ok") is not True:
                raise RuntimeError(f"{name} failed: {result}")
            return result

        body = call("partdesign.create_body", {"label": "Reviewed Box"})
        body_name = body["mutation"]["body"]
        sketch = call(
            "partdesign.create_sketch",
            {
                "body_name": body_name,
                "label": "Reviewed Box Profile",
                "support": {"type": "origin_plane", "plane": "XY_Plane"},
            },
        )
        sketch_name = sketch["mutation"]["sketch"]
        call("partdesign.edit_sketch", {"sketch_name": sketch_name})
        call(
            "sketcher.draw_rectangle",
            {
                "width": 24,
                "height": 16,
                "center_x": 0,
                "center_y": 0,
                "construction": False,
            },
        )
        call("sketcher.close_sketch", {})
        call(
            "partdesign.pad",
            {
                "profile_name": sketch_name,
                "label": "Reviewed Box Pad",
                "extent": {"type": "length", "length": 8},
                "side": "one_side",
                "reversed": False,
                "taper_angle_degrees": 0,
                "second_taper_angle_degrees": 0,
                "refine": True,
            },
        )
        call(
            "core.update_design_brief",
            {
                "base_revision": context["design_brief"]["revision"],
                "changes": {
                    "purpose": "A deterministic box for human candidate review.",
                    "units": "mm",
                    "critical_dimensions": [
                        {"name": "width", "value": 24, "unit": "mm"},
                        {"name": "depth", "value": 16, "unit": "mm"},
                        {"name": "height", "value": 8, "unit": "mm"},
                    ],
                },
            },
        )
        return ProviderResult(
            "Created a validated 24 by 16 by 8 mm editable box preview.",
            raw={"calls": calls},
        )


class CandidateReviewAcceptanceTest(unittest.TestCase):
    """Prove the human review boundary with native FreeCAD and Qt."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-candidate-review-"
        )
        self.root = Path(self._temporary.name)
        self._prior_home = os.environ.get("VIBECAD_HOME")
        os.environ["VIBECAD_HOME"] = str(self.root / "vibecad-home")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("VibeCADCandidateReview")
        self.canonical_path = self.root / "candidate-review.FCStd"
        self.document.saveAs(str(self.canonical_path))
        App.setActiveDocument(self.document.Name)
        self.service = VibeCADService()
        self.service.project_context()
        self.service.design_brief()
        self.scope = self.service.project_scope_snapshot()
        self.project_root = Path(str(self.scope["root"]))
        self.manifest_path = Path(str(self.scope["manifest_path"]))
        self.revisions = VibeCADRevisionStore(
            self.project_root, str(self.scope["project_id"])
        )

        self.dock = QtWidgets.QDockWidget("VibeCAD candidate review test")
        self.dock.setObjectName("VibeCADCandidateReviewTestDock")
        self.dock.setWidget(VibeGui._build_panel_widget())
        self.dock.resize(440, 780)
        self.dock.show()
        QtWidgets.QApplication.processEvents()
        self._original_find_dock = VibeGui._find_dock
        self._original_append = VibeGui._append_conversation
        VibeGui._find_dock = lambda: self.dock
        VibeGui._append_conversation = lambda *_args, **_kwargs: None

    def tearDown(self) -> None:
        VibeGui._pending_candidate_decision_waiter = None
        VibeGui._find_dock = self._original_find_dock
        VibeGui._append_conversation = self._original_append
        self.dock.close()
        self.dock.deleteLater()
        QtWidgets.QApplication.processEvents()
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        if self._prior_home is None:
            os.environ.pop("VIBECAD_HOME", None)
        else:
            os.environ["VIBECAD_HOME"] = self._prior_home
        self._temporary.cleanup()

    def _accepted_state(self) -> dict[str, object]:
        return {
            "canonical_sha256": _sha256(self.canonical_path),
            "head": _head_id(self.revisions),
            "project_sha256": _accepted_project_hash(self.project_root),
            "project_entries": _accepted_project_entries(self.project_root),
            "manifest": self.manifest_path.read_bytes(),
        }

    def _keyboard_activate(self, button) -> None:
        self.assertEqual(button.focusPolicy(), QtCore.Qt.StrongFocus)
        window = button.window()
        window.show()
        QtWidgets.QApplication.setActiveWindow(window)
        window.activateWindow()
        button.setFocus(QtCore.Qt.TabFocusReason)
        QtWidgets.QApplication.processEvents()
        # QtTest delivers the native press and release pair directly to the
        # widget when the offscreen platform cannot own the process keyboard.
        QtTest.QTest.keyClick(button, QtCore.Qt.Key_Space)
        QtWidgets.QApplication.processEvents()

    def _run_review(self, action: str):
        baseline: dict[str, object] = {}
        observed_states: list[str] = []
        controller_run_id = VibeGui._assistant_run_controller.begin()

        def capture_baseline() -> None:
            baseline.update(self._accepted_state())

        def progress(event) -> None:
            if event.get("event") != "run_state_changed":
                return
            observed_states.append(str(event.get("state") or ""))
            VibeGui._handle_progress_event(self.dock, dict(event))

        def decide(payload) -> str:
            self.assertTrue(baseline, "The provider did not capture its accepted state")
            self.assertEqual(payload.get("state"), "awaiting_decision")
            self.assertEqual(self._accepted_state(), baseline)
            self.assertTrue(Path(str(payload["candidate_path"])).is_file())
            self.assertEqual(
                _sha256(Path(str(payload["candidate_path"]))),
                payload.get("candidate_sha256"),
            )
            candidate_pads = [
                obj
                for obj in self.document.Objects
                if getattr(obj, "TypeId", "") == "PartDesign::Pad"
            ]
            self.assertEqual(len(candidate_pads), 1)

            waiter = VibeGui._CandidateDecisionWaiter(dict(payload))
            VibeGui._show_candidate_review(dict(payload), waiter)
            if action == "accept":
                control = self.dock.findChild(
                    QtWidgets.QPushButton, "VibeAcceptRevision"
                )
            elif action == "reject":
                control = self.dock.findChild(
                    QtWidgets.QPushButton, "VibeRejectPreview"
                )
            elif action == "stop":
                control = self.dock.findChild(QtWidgets.QPushButton, "VibeStop")
            else:
                raise AssertionError(f"Unknown review action: {action}")
            self.assertIsNotNone(control)
            self.assertTrue(control.isEnabled())
            if action == "stop":
                # Stop is not a candidate-decision control. Invoke its real Qt
                # signal and verify that it resolves review as rejection.
                control.click()
                QtWidgets.QApplication.processEvents()
            else:
                self._keyboard_activate(control)
            self.assertTrue(waiter.completed.is_set())
            expected = "accept" if action == "accept" else "reject"
            self.assertEqual(waiter.decision, expected)
            current = self._accepted_state()
            for key in (
                "canonical_sha256",
                "head",
                "manifest",
                "project_entries",
                "project_sha256",
            ):
                self.assertEqual(current[key], baseline[key], key)
            return waiter.decision

        provider = _DeterministicBoxProvider(capture_baseline)
        try:
            response = run_prompt(
                "Create a deterministic editable box for review.",
                service=self.service,
                prefer_online=False,
                provider=provider,
                progress_callback=progress,
                cancellation_check=lambda: VibeGui._assistant_run_controller.is_cancelled(
                    controller_run_id
                ),
                candidate_decision_callback=decide,
            )
        finally:
            VibeGui._assistant_run_controller.finish(controller_run_id)
        self.assertIsNone(response.error)
        return response, baseline, observed_states

    def test_candidate_review_is_transactional_and_keyboard_accessible(self) -> None:
        initial_sha256 = _sha256(self.canonical_path)

        rejected, reject_baseline, _reject_states = self._run_review("reject")
        self.assertEqual(rejected.context["candidate_decision"]["decision"], "reject")
        self.assertEqual(rejected.context["candidate_decision"]["mode"], "human")
        self.assertIsNone(rejected.context["candidate_decision"]["revision_id"])
        self.assertEqual(self._accepted_state(), reject_baseline)
        self.assertEqual(len(self.document.Objects), 0)
        self.assertEqual(self.revisions.list_records(), [])

        stopped, stop_baseline, _stop_states = self._run_review("stop")
        self.assertEqual(stopped.context["candidate_decision"]["decision"], "reject")
        self.assertEqual(stopped.context["candidate_decision"]["mode"], "human")
        self.assertIsNone(stopped.context["candidate_decision"]["revision_id"])
        self.assertEqual(self._accepted_state(), stop_baseline)
        self.assertEqual(len(self.document.Objects), 0)
        self.assertEqual(self.revisions.list_records(), [])
        self.assertEqual(_sha256(self.canonical_path), initial_sha256)

        accepted, accept_baseline, accept_states = self._run_review("accept")
        self.assertEqual(accept_states, list(RUN_STATES))
        self.assertEqual(accepted.context["candidate_decision"]["decision"], "accept")
        self.assertEqual(accepted.context["candidate_decision"]["mode"], "human")
        self.assertNotEqual(_sha256(self.canonical_path), initial_sha256)
        self.assertEqual(accept_baseline["canonical_sha256"], initial_sha256)

        records = self.revisions.list_records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["revision_id"], _head_id(self.revisions))
        self.assertEqual(
            accepted.context["candidate_decision"]["revision_id"],
            record["revision_id"],
        )
        self.assertEqual(record["rollback"]["acceptance_mode"], "human")
        self.assertEqual(record["accepted_artifact"]["acceptance_mode"], "human")
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("accepted_revision"), record["revision_id"])

        audit = VibeCADAuditStore(
            self.project_root, str(self.scope["project_id"])
        ).list_events()
        rejections = [
            event
            for event in audit
            if event.get("category") == "ai_revision"
            and event.get("action") == "reject"
        ]
        self.assertEqual(len(rejections), 2)
        self.assertTrue(all(event.get("actor_type") == "user" for event in rejections))
        accept_events = [
            event
            for event in audit
            if event.get("category") == "ai_revision"
            and event.get("action") == "accept"
        ]
        self.assertEqual(len(accept_events), 1)
        self.assertEqual(accept_events[0].get("actor_type"), "user")
        self.assertEqual(
            accept_events[0].get("details", {}).get("acceptance_mode"), "human"
        )

        accepted_sha256 = _sha256(self.canonical_path)
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(self.canonical_path))
        App.setActiveDocument(self.document.Name)
        self.document.recompute()
        pads = [
            obj
            for obj in self.document.Objects
            if getattr(obj, "TypeId", "") == "PartDesign::Pad"
        ]
        self.assertEqual(len(pads), 1)
        shape = pads[0].Shape
        self.assertTrue(shape.isValid())
        self.assertFalse(shape.isNull())
        self.assertAlmostEqual(shape.BoundBox.XLength, 24.0, places=6)
        self.assertAlmostEqual(shape.BoundBox.YLength, 16.0, places=6)
        self.assertAlmostEqual(shape.BoundBox.ZLength, 8.0, places=6)
        self.assertEqual(_sha256(self.canonical_path), accepted_sha256)

        reopened_service = VibeCADService()
        self.assertEqual(len(reopened_service.revision_timeline()), 1)
        self.assertEqual(
            reopened_service.design_brief().get("purpose"),
            "A deterministic box for human candidate review.",
        )


def suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(
        CandidateReviewAcceptanceTest
    )
