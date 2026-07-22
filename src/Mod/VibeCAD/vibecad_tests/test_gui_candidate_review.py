# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import inspect
from types import SimpleNamespace

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


def test_stop_during_normal_run_requests_session_cancellation(monkeypatch) -> None:
    dock = _Dock()
    conversation = []
    gui._pending_candidate_decision_waiter = None
    run_id = gui._assistant_run_controller.begin()
    monkeypatch.setattr(gui, "_find_dock", lambda: dock)
    monkeypatch.setattr(gui, "_render_assistant_run_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        gui,
        "_append_conversation",
        lambda role, text, **metadata: conversation.append((role, text, metadata)),
    )
    monkeypatch.setattr(gui, "_cancel_question_round", lambda: None)
    try:
        gui._stop_prompt_from_panel()
        assert gui._assistant_run_controller.is_cancelled(run_id)
        assert conversation[0] == (
            "User",
            "Stop.",
            {"persist": True, "metadata": {"source": "stop"}},
        )
        assert conversation[1][0:2] == (
            "AI thinking",
            "Stopping after the current provider/tool step.",
        )
    finally:
        gui._assistant_run_controller.finish(run_id)


def test_gui_steering_message_reaches_the_running_session(monkeypatch) -> None:
    dock = _Dock()
    observed = {"steering": [], "conversation": []}
    created_threads = []

    class Prompt:
        text = "Make the active feature 5 mm wider."

        def toPlainText(self):
            return self.text

        def clear(self):
            self.text = ""

    class Service:
        def __init__(self):
            self.pending = []

        @staticmethod
        def use_online_provider_by_default():
            return False

        @staticmethod
        def document_persistence_state():
            return {"enabled": True}

        def queue_steering_message(self, text):
            self.pending.append({"text": str(text), "consumed": False})
            return {"ok": True}

        def consume_steering_messages(self):
            result = [dict(item) for item in self.pending if not item["consumed"]]
            for item in self.pending:
                item["consumed"] = True
            return result

        @staticmethod
        def active_workbench_name():
            return "PartDesignWorkbench"

        @staticmethod
        def coerce_modeling_engine_for_workbench(_workbench):
            return {"changed": False}

    class DeferredThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon
            created_threads.append(self)

        def start(self):
            pass

        def run_now(self):
            self.target()

    service = Service()
    prompt = Prompt()

    def run_prompt(_prompt, **arguments):
        observed["steering"] = arguments["steering_check"]()
        return SimpleNamespace(final_output="", error=None, context={})

    monkeypatch.setattr(gui, "get_service", lambda: service)
    monkeypatch.setattr(gui, "run_prompt", run_prompt)
    monkeypatch.setattr(gui.threading, "Thread", DeferredThread)
    monkeypatch.setattr(gui, "_find_dock", lambda: dock)
    monkeypatch.setattr(
        gui,
        "_find_child",
        lambda _kind, name, _dock=None: prompt if name == "VibePrompt" else None,
    )
    monkeypatch.setattr(gui, "_ensure_document_thread_invoker", lambda: None)
    monkeypatch.setattr(gui, "_dispatch_to_document_thread", lambda operation: operation())
    monkeypatch.setattr(gui, "_render_assistant_run_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gui, "_clear_thinking", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gui, "_cancel_question_round", lambda: None)
    monkeypatch.setattr(gui, "_refresh_conversation_selector", lambda *_args: None)
    monkeypatch.setattr(gui, "_refresh_modeling_engine_selector", lambda *_args: None)
    monkeypatch.setattr(gui, "_refresh_view_status", lambda *_args: None)
    monkeypatch.setattr(gui, "_render_questions", lambda *_args: None)
    monkeypatch.setattr(gui, "_arm_sketch_close_continuation", lambda: None)
    monkeypatch.setattr(gui, "_warn", lambda _message: None)
    monkeypatch.setattr(
        gui,
        "_append_conversation",
        lambda role, text, **metadata: observed["conversation"].append(
            (role, text, metadata)
        ),
    )

    gui._execute_assistant_run(dock, service, prompt="Create a bracket.")
    assert gui._is_assistant_run_active()
    gui._run_prompt_from_panel()
    assert prompt.text == ""
    assert len(created_threads) == 1
    created_threads[0].run_now()

    assert observed["steering"] == ["Make the active feature 5 mm wider."]
    assert [item["text"] for item in service.pending if not item["consumed"]] == []
    assert any(
        role == "User" and metadata.get("metadata", {}).get("source") == "steering"
        for role, _text, metadata in observed["conversation"]
    )
    assert not gui._is_assistant_run_active()


def test_gui_passes_review_callback_and_exposes_accessible_controls() -> None:
    source = inspect.getsource(gui)
    assert '"candidate_decision_callback": _candidate_decision_callback' in source
    assert 'setObjectName("VibeAcceptRevision")' in source
    assert 'setAccessibleName("Accept revision")' in source
    assert 'setShortcut(QtGui.QKeySequence("Alt+A"))' in source
    assert 'setObjectName("VibeRejectPreview")' in source
    assert 'setAccessibleName("Reject preview")' in source
    assert 'setShortcut(QtGui.QKeySequence("Alt+R"))' in source
