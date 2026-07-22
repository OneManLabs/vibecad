# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from VibeCADOnboarding import (
    ONBOARDING_SCHEMA,
    START_CHOICES,
    choose_start,
    default_state,
    load_state,
    reset_onboarding,
)


def test_beginner_choices_use_intent_and_do_not_expose_internal_engines() -> None:
    assert len(START_CHOICES) == 7
    text = " ".join(
        f"{choice.title} {choice.description} {choice.prompt}" for choice in START_CHOICES
    ).lower()
    for internal_name in ("workbench", "part design", "build123d", "openscad", "vibescript"):
        assert internal_name not in text


def test_first_launch_defaults_to_incomplete_without_writing(tmp_path) -> None:
    path = tmp_path / "onboarding.json"
    assert load_state(path) == default_state()
    assert not path.exists()


def test_choice_is_saved_atomically_and_restored(tmp_path) -> None:
    path = tmp_path / "onboarding.json"
    choice = choose_start("enclosure", path)
    assert choice.title == "Create an enclosure"
    assert load_state(path) == {
        "schema": ONBOARDING_SCHEMA,
        "version": 1,
        "completed": True,
        "last_choice": "enclosure",
    }
    assert not list(tmp_path.glob(".onboarding.json.*"))


def test_invalid_or_unknown_state_fails_without_rewrite(tmp_path) -> None:
    path = tmp_path / "onboarding.json"
    original = json.dumps(
        {
            "schema": ONBOARDING_SCHEMA,
            "version": 1,
            "completed": True,
            "last_choice": "engine",
        }
    )
    path.write_text(original, encoding="utf-8")
    with pytest.raises(RuntimeError, match="unknown start choice"):
        load_state(path)
    assert path.read_text(encoding="utf-8") == original


def test_reset_returns_to_first_launch(tmp_path) -> None:
    path = tmp_path / "onboarding.json"
    choose_start("learn", path)
    assert reset_onboarding(path) == default_state()
    assert load_state(path)["completed"] is False


def test_gui_start_choice_creates_saved_project_and_editable_prompt(
    tmp_path, monkeypatch
) -> None:
    import VibeCADGui as gui

    calls = []

    class Cursor:
        MoveOperation = SimpleNamespace(End="end")

        def movePosition(self, operation):
            calls.append(("move", operation))

    class Prompt:
        def isEnabled(self):
            return True

        def isReadOnly(self):
            return False

        def setPlainText(self, value):
            calls.append(("prompt", value))

        def setFocus(self):
            calls.append(("focus",))

        def textCursor(self):
            return Cursor()

        def setTextCursor(self, cursor):
            calls.append(("cursor", cursor))

    class Dock:
        @staticmethod
        def isVisible():
            return True

    document = SimpleNamespace(FileName="")

    def save_as(path):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"FCStd")
        document.FileName = str(target)

    document.saveAs = save_as

    def new_document(*args):
        calls.append(("new", args))
        monkeypatch.setattr(gui.App, "ActiveDocument", document, raising=False)
        return document

    dock = Dock()
    project = {
        "persistent": True,
        "document_saved": True,
        "document": {"file_path": str(tmp_path / "local.FCStd")},
    }

    monkeypatch.setattr(
        gui,
        "start_choice",
        lambda choice_id: SimpleNamespace(
            prompt="Create an editable enclosure for: "
        ),
    )
    monkeypatch.setattr(
        gui,
        "choose_start",
        lambda choice_id: calls.append(("complete", choice_id)),
    )
    monkeypatch.setattr(gui.App, "ActiveDocument", None, raising=False)
    monkeypatch.setattr(gui.App, "newDocument", new_document, raising=False)
    monkeypatch.setattr(
        gui.Gui,
        "activateWorkbench",
        lambda name: calls.append(("activate", name)),
        raising=False,
    )
    monkeypatch.setattr(
        gui,
        "_onboarding_local_document_path",
        lambda _choice: tmp_path / "local.FCStd",
    )
    monkeypatch.setattr(gui, "_show_panel", lambda: calls.append(("panel",)) or dock)
    monkeypatch.setattr(gui, "_assistant_panel_is_built", lambda _dock: True)
    monkeypatch.setattr(gui, "_find_child", lambda *args: Prompt())
    monkeypatch.setattr(gui, "_render_assistant_run_state", lambda _dock: None)
    monkeypatch.setattr(
        gui,
        "get_service",
        lambda: SimpleNamespace(project_context=lambda: project),
    )
    gui._onboarding_dialog = None

    assert gui._apply_onboarding_choice("enclosure") is True

    assert any(call[0] == "new" for call in calls)
    assert (tmp_path / "local.FCStd").is_file()
    assert ("prompt", "Create an editable enclosure for: ") in calls
    assert ("panel",) in calls
    assert ("activate", "PartDesignWorkbench") in calls
    assert ("complete", "enclosure") in calls


def test_modify_start_opens_saved_file_without_creating_blank_document(
    tmp_path, monkeypatch
) -> None:
    import VibeCADGui as gui

    calls = []
    opened_path = tmp_path / "existing.FCStd"
    opened_path.write_bytes(b"FCStd")
    document = SimpleNamespace(FileName=str(opened_path))

    class Prompt:
        @staticmethod
        def isEnabled():
            return True

        @staticmethod
        def isReadOnly():
            return False

        @staticmethod
        def setPlainText(_value):
            pass

        @staticmethod
        def setFocus():
            pass

        @staticmethod
        def textCursor():
            return SimpleNamespace(
                MoveOperation=SimpleNamespace(End="end"),
                movePosition=lambda _operation: None,
            )

        @staticmethod
        def setTextCursor(_cursor):
            pass

    dock = SimpleNamespace(isVisible=lambda: True)

    def open_file(name):
        calls.append(("command", name))
        monkeypatch.setattr(gui.App, "ActiveDocument", document, raising=False)

    monkeypatch.setattr(
        gui,
        "start_choice",
        lambda choice_id: SimpleNamespace(prompt="Modify the open design to: "),
    )
    monkeypatch.setattr(
        gui,
        "choose_start",
        lambda choice_id: calls.append(("complete", choice_id)),
    )
    monkeypatch.setattr(gui.App, "ActiveDocument", None, raising=False)
    monkeypatch.setattr(gui.Gui, "runCommand", open_file, raising=False)
    monkeypatch.setattr(
        gui.Gui,
        "activateWorkbench",
        lambda name: calls.append(("activate", name)),
        raising=False,
    )
    monkeypatch.setattr(
        gui.App,
        "newDocument",
        lambda *args: calls.append(("new", args)),
        raising=False,
    )
    monkeypatch.setattr(gui, "_show_panel", lambda: dock)
    monkeypatch.setattr(gui, "_assistant_panel_is_built", lambda _dock: True)
    monkeypatch.setattr(gui, "_find_child", lambda *args: Prompt())
    monkeypatch.setattr(gui, "_render_assistant_run_state", lambda _dock: None)
    monkeypatch.setattr(
        gui,
        "get_service",
        lambda: SimpleNamespace(
            project_context=lambda: {
                "persistent": True,
                "document_saved": True,
                "document": {"file_path": str(opened_path)},
            }
        ),
    )
    gui._onboarding_dialog = None

    assert gui._apply_onboarding_choice("modify-file") is True

    assert ("command", "Std_Open") in calls
    assert not any(call[0] == "new" for call in calls)
    assert ("complete", "modify-file") in calls


def test_modify_start_cancellation_keeps_onboarding_incomplete(monkeypatch) -> None:
    import VibeCADGui as gui

    completed = []
    warnings = []
    dialog = object()
    monkeypatch.setattr(gui, "start_choice", lambda _choice: SimpleNamespace(prompt="Modify: "))
    monkeypatch.setattr(gui, "choose_start", completed.append)
    monkeypatch.setattr(gui.App, "ActiveDocument", None, raising=False)
    monkeypatch.setattr(gui.Gui, "runCommand", lambda _name: None, raising=False)
    monkeypatch.setattr(gui, "_set_onboarding_status", warnings.append)
    monkeypatch.setattr(gui, "_warn", lambda _message: None)
    gui._onboarding_dialog = dialog

    assert gui._apply_onboarding_choice("modify-file") is False
    assert completed == []
    assert gui._onboarding_dialog is dialog
    assert "No saved CAD file was opened" in warnings[-1]


def test_modify_cancellation_does_not_reuse_a_previously_active_file(
    tmp_path, monkeypatch
) -> None:
    import VibeCADGui as gui

    path = tmp_path / "already-open.FCStd"
    path.write_bytes(b"FCStd")
    previous = SimpleNamespace(FileName=str(path))
    completed = []
    monkeypatch.setattr(gui, "start_choice", lambda _choice: SimpleNamespace(prompt="Modify: "))
    monkeypatch.setattr(gui, "choose_start", completed.append)
    monkeypatch.setattr(gui.App, "ActiveDocument", previous, raising=False)
    monkeypatch.setattr(gui.Gui, "runCommand", lambda _name: None, raising=False)
    monkeypatch.setattr(gui, "_set_onboarding_status", lambda _message: None)
    monkeypatch.setattr(gui, "_warn", lambda _message: None)
    dialog = object()
    gui._onboarding_dialog = dialog

    assert gui._apply_onboarding_choice("modify-file") is False
    assert completed == []
    assert gui._onboarding_dialog is dialog


def test_failed_workspace_setup_does_not_complete_onboarding(monkeypatch) -> None:
    import VibeCADGui as gui

    completed = []
    monkeypatch.setattr(gui, "start_choice", lambda choice_id: SimpleNamespace(prompt="Create: "))
    monkeypatch.setattr(gui, "choose_start", lambda choice_id: completed.append(choice_id))
    monkeypatch.setattr(gui.App, "ActiveDocument", None, raising=False)
    monkeypatch.setattr(
        gui.App,
        "newDocument",
        lambda *args: (_ for _ in ()).throw(
            RuntimeError("injected creation failure")
        ),
        raising=False,
    )
    monkeypatch.setattr(gui, "_set_onboarding_status", lambda _message: None)
    monkeypatch.setattr(gui, "_warn", lambda _message: None)
    dialog = object()
    gui._onboarding_dialog = dialog

    assert gui._apply_onboarding_choice("new-part") is False

    assert completed == []
    assert gui._onboarding_dialog is dialog


def test_missing_assistant_workspace_keeps_onboarding_retryable(monkeypatch) -> None:
    import VibeCADGui as gui

    completed = []
    document = SimpleNamespace(FileName="/tmp/saved.FCStd")
    dialog = object()
    monkeypatch.setattr(gui, "start_choice", lambda _choice: SimpleNamespace(prompt="Create: "))
    monkeypatch.setattr(gui, "choose_start", completed.append)
    monkeypatch.setattr(gui, "_create_or_reuse_onboarding_document", lambda _choice: document)
    monkeypatch.setattr(gui.Gui, "activateWorkbench", lambda _name: None, raising=False)
    monkeypatch.setattr(gui, "_show_panel", lambda: None)
    monkeypatch.setattr(gui, "_set_onboarding_status", lambda _message: None)
    monkeypatch.setattr(gui, "_warn", lambda _message: None)
    gui._onboarding_dialog = dialog

    assert gui._apply_onboarding_choice("new-part") is False
    assert completed == []
    assert gui._onboarding_dialog is dialog


@pytest.mark.parametrize(
    "failure",
    (
        "inactive_document",
        "invisible_dock",
        "missing_prompt",
        "read_only_prompt",
        "project_mismatch",
    ),
)
def test_each_workspace_readiness_failure_keeps_onboarding_retryable(
    failure, tmp_path, monkeypatch
) -> None:
    import VibeCADGui as gui

    completed = []
    file_path = tmp_path / "design.FCStd"
    file_path.write_bytes(b"FCStd")
    document = SimpleNamespace(FileName=str(file_path))

    class Dock:
        @staticmethod
        def isVisible():
            return failure != "invisible_dock"

    class Prompt:
        @staticmethod
        def isEnabled():
            return True

        @staticmethod
        def isReadOnly():
            return failure == "read_only_prompt"

    project_file = tmp_path / "other.FCStd" if failure == "project_mismatch" else file_path
    project = {
        "persistent": True,
        "document_saved": True,
        "document": {"file_path": str(project_file)},
    }
    active = None if failure == "inactive_document" else document
    monkeypatch.setattr(gui.App, "ActiveDocument", active, raising=False)
    monkeypatch.setattr(gui, "start_choice", lambda _choice: SimpleNamespace(prompt="Create: "))
    monkeypatch.setattr(gui, "choose_start", completed.append)
    monkeypatch.setattr(gui, "_create_or_reuse_onboarding_document", lambda _choice: document)
    monkeypatch.setattr(gui.Gui, "activateWorkbench", lambda _name: None, raising=False)
    monkeypatch.setattr(gui, "_show_panel", lambda: Dock())
    monkeypatch.setattr(gui, "_assistant_panel_is_built", lambda _dock: True)
    monkeypatch.setattr(
        gui,
        "_find_child",
        lambda *_args: None if failure == "missing_prompt" else Prompt(),
    )
    monkeypatch.setattr(gui, "_render_assistant_run_state", lambda _dock: None)
    monkeypatch.setattr(
        gui,
        "get_service",
        lambda: SimpleNamespace(project_context=lambda: project),
    )
    monkeypatch.setattr(gui, "_set_onboarding_status", lambda _message: None)
    monkeypatch.setattr(gui, "_warn", lambda _message: None)
    dialog = object()
    gui._onboarding_dialog = dialog

    assert gui._apply_onboarding_choice("new-part") is False
    assert completed == []
    assert gui._onboarding_dialog is dialog
