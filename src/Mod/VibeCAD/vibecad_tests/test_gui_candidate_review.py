# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import inspect

import VibeCADGui as gui


class _Control:
    def __init__(self) -> None:
        self.enabled = False
        self.visible = False
        self.properties = {}

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)

    def setVisible(self, visible):
        self.visible = bool(visible)

    def setProperty(self, name, value):
        self.properties[name] = value


class _Dock(_Control):
    pass


def test_candidate_waiter_accepts_one_exact_decision() -> None:
    waiter = gui._CandidateDecisionWaiter({"acceptance_id": "candidate-1"})
    assert waiter.finish("accept") is True
    assert waiter.finish("reject") is False
    assert waiter.completed.is_set()
    assert waiter.decision == "accept"


def test_stable_run_states_render_in_order(monkeypatch) -> None:
    dock = _Dock()
    observed = []

    class Label:
        def setText(self, text):
            observed.append(text)

        def setVisible(self, _visible):
            pass

        def setAccessibleDescription(self, _description):
            pass

    monkeypatch.setattr(gui, "_find_child", lambda *_args, **_kwargs: Label())
    monkeypatch.setattr(gui, "_set_status_line", lambda *_args, **_kwargs: None)
    for state in gui.ASSISTANT_RUN_STATES:
        gui._handle_progress_event(
            dock,
            {"event": "run_state_changed", "state": state},
        )
    assert observed == list(gui.ASSISTANT_RUN_STATES)
    assert dock.properties["VibeRunState"] == "Complete"


def test_stop_during_review_resolves_reject(monkeypatch) -> None:
    dock = _Dock()
    panel = _Control()
    controls = {
        "VibeCandidateReview": panel,
        "VibeAcceptRevision": _Control(),
        "VibeRejectPreview": _Control(),
    }
    waiter = gui._CandidateDecisionWaiter({"acceptance_id": "candidate-stop"})
    gui._pending_candidate_decision_waiter = waiter
    run_id = gui._assistant_run_controller.begin()
    monkeypatch.setattr(gui, "_find_dock", lambda: dock)
    monkeypatch.setattr(
        gui,
        "_find_child",
        lambda _kind, name, _dock=None: controls.get(name),
    )
    monkeypatch.setattr(gui, "_render_assistant_run_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gui, "_append_conversation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gui, "_cancel_question_round", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gui, "_set_status_line", lambda *_args, **_kwargs: None)
    try:
        gui._stop_prompt_from_panel()
        assert waiter.completed.is_set()
        assert waiter.decision == "reject"
        assert gui._pending_candidate_decision_waiter is None
        assert gui._assistant_run_controller.is_cancelled(run_id)
    finally:
        gui._pending_candidate_decision_waiter = None
        gui._assistant_run_controller.finish(run_id)


def test_gui_passes_review_callback_and_exposes_accessible_controls() -> None:
    source = inspect.getsource(gui)
    assert '"candidate_decision_callback": _candidate_decision_callback' in source
    assert 'setObjectName("VibeAcceptRevision")' in source
    assert 'setAccessibleName("Accept revision")' in source
    assert 'setShortcut(QtGui.QKeySequence("Alt+A"))' in source
    assert 'setObjectName("VibeRejectPreview")' in source
    assert 'setAccessibleName("Reject preview")' in source
    assert 'setShortcut(QtGui.QKeySequence("Alt+R"))' in source
