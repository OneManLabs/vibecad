# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native Qt gate for validated-candidate review controls."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

MODULE_ROOT = Path(__file__).resolve().parent.parent
while str(MODULE_ROOT) in sys.path:
    sys.path.remove(str(MODULE_ROOT))
sys.path.insert(0, str(MODULE_ROOT))

import FreeCADGui as Gui  # noqa: E402
from PySide import QtCore, QtGui, QtWidgets  # noqa: E402

import VibeCADGui as panel  # noqa: E402


def _activate_with_space(button) -> None:
    button.setFocus(QtCore.Qt.TabFocusReason)
    QtWidgets.QApplication.processEvents()
    assert button.hasFocus(), f"{button.objectName()} did not receive keyboard focus."
    press = QtGui.QKeyEvent(
        QtCore.QEvent.KeyPress,
        QtCore.Qt.Key_Space,
        QtCore.Qt.NoModifier,
    )
    release = QtGui.QKeyEvent(
        QtCore.QEvent.KeyRelease,
        QtCore.Qt.Key_Space,
        QtCore.Qt.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(button, press)
    QtWidgets.QApplication.sendEvent(button, release)
    QtWidgets.QApplication.processEvents()


def main() -> int:
    application = QtWidgets.QApplication.instance()
    if application is None:
        application = QtWidgets.QApplication([])
    dock = QtWidgets.QDockWidget("Candidate review")
    widget = panel._build_panel_widget()
    dock.setWidget(widget)
    dock.resize(420, 760)
    dock.show()
    QtWidgets.QApplication.processEvents()

    original_find_dock = panel._find_dock
    original_persistence = panel._document_persistence_state
    original_append = panel._append_conversation
    panel._find_dock = lambda: dock
    panel._document_persistence_state = lambda: {"enabled": True}
    panel._append_conversation = lambda *_args, **_kwargs: None
    run_id = panel._assistant_run_controller.begin()
    try:
        accept = dock.findChild(QtWidgets.QPushButton, "VibeAcceptRevision")
        reject = dock.findChild(QtWidgets.QPushButton, "VibeRejectPreview")
        stop = dock.findChild(QtWidgets.QPushButton, "VibeStop")
        review = dock.findChild(QtWidgets.QWidget, "VibeCandidateReview")
        assert accept is not None and reject is not None and stop is not None
        assert review is not None
        assert accept.accessibleName() == "Accept revision"
        assert reject.accessibleName() == "Reject preview"
        assert accept.focusPolicy() == QtCore.Qt.StrongFocus
        assert reject.focusPolicy() == QtCore.Qt.StrongFocus

        accept_waiter = panel._CandidateDecisionWaiter(
            {"acceptance_id": "keyboard-accept", "candidate_sha256": "a" * 64}
        )
        panel._show_candidate_review(accept_waiter.payload, accept_waiter)
        assert review.isVisible()
        _activate_with_space(accept)
        assert accept_waiter.decision == "accept"

        reject_waiter = panel._CandidateDecisionWaiter(
            {"acceptance_id": "keyboard-reject", "candidate_sha256": "b" * 64}
        )
        panel._show_candidate_review(reject_waiter.payload, reject_waiter)
        _activate_with_space(reject)
        assert reject_waiter.decision == "reject"

        stop_waiter = panel._CandidateDecisionWaiter(
            {"acceptance_id": "keyboard-stop", "candidate_sha256": "c" * 64}
        )
        panel._show_candidate_review(stop_waiter.payload, stop_waiter)
        _activate_with_space(stop)
        assert stop_waiter.decision == "reject"
        assert panel._assistant_run_controller.is_cancelled(run_id)

        observed_states = []
        for state in panel.ASSISTANT_RUN_STATES:
            panel._handle_progress_event(
                dock,
                {"event": "run_state_changed", "state": state},
            )
            observed_states.append(str(dock.property("VibeRunState")))
        assert observed_states == list(panel.ASSISTANT_RUN_STATES)

        print(
            json.dumps(
                {
                    "ok": True,
                    "integration": "candidate_review_gui",
                    "states": observed_states,
                    "keyboard_accept": True,
                    "keyboard_reject": True,
                    "keyboard_stop_reject": True,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    finally:
        panel._pending_candidate_decision_waiter = None
        panel._assistant_run_controller.finish(run_id)
        panel._find_dock = original_find_dock
        panel._document_persistence_state = original_persistence
        panel._append_conversation = original_append
        dock.close()
        dock.deleteLater()
        QtWidgets.QApplication.processEvents()
        main_window = Gui.getMainWindow() if hasattr(Gui, "getMainWindow") else None
        if main_window is not None:
            main_window.close()
        QtWidgets.QApplication.processEvents()


class CandidateReviewGUIIntegration(unittest.TestCase):
    def test_candidate_review_controls(self) -> None:
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    result_code = main()
    if result_code:
        raise RuntimeError(
            f"Candidate review GUI integration failed with {result_code}."
        )
