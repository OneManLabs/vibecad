# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
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
    original = json.dumps({"schema": ONBOARDING_SCHEMA, "version": 1, "completed": True, "last_choice": "engine"})
    path.write_text(original, encoding="utf-8")
    with pytest.raises(RuntimeError, match="unknown start choice"):
        load_state(path)
    assert path.read_text(encoding="utf-8") == original


def test_reset_returns_to_first_launch(tmp_path) -> None:
    path = tmp_path / "onboarding.json"
    choose_start("learn", path)
    assert reset_onboarding(path) == default_state()
    assert load_state(path)["completed"] is False


def test_gui_start_choice_creates_document_and_fills_editable_prompt(monkeypatch) -> None:
    import VibeCADGui as gui

    calls = []

    class Cursor:
        MoveOperation = SimpleNamespace(End="end")

        def movePosition(self, operation):
            calls.append(("move", operation))

    class Prompt:
        def setPlainText(self, value):
            calls.append(("prompt", value))

        def setFocus(self):
            calls.append(("focus",))

        def textCursor(self):
            return Cursor()

        def setTextCursor(self, cursor):
            calls.append(("cursor", cursor))

    monkeypatch.setattr(gui, "start_choice", lambda choice_id: SimpleNamespace(prompt="Create an editable enclosure for: "))
    monkeypatch.setattr(gui, "choose_start", lambda choice_id: calls.append(("complete", choice_id)))
    monkeypatch.setattr(gui.App, "ActiveDocument", None, raising=False)
    monkeypatch.setattr(gui.App, "newDocument", lambda *args: calls.append(("new", args)), raising=False)
    monkeypatch.setattr(gui.Gui, "activateWorkbench", lambda name: calls.append(("activate", name)), raising=False)
    monkeypatch.setattr(gui, "_show_panel", lambda: calls.append(("panel",)))
    monkeypatch.setattr(gui, "_find_dock", lambda: object())
    monkeypatch.setattr(gui, "_find_child", lambda *args: Prompt())
    gui._onboarding_dialog = None

    gui._apply_onboarding_choice("enclosure")

    assert any(call[0] == "new" for call in calls)
    assert ("prompt", "Create an editable enclosure for: ") in calls
    assert ("panel",) in calls
    assert ("complete", "enclosure") in calls


def test_modify_start_opens_file_without_creating_blank_document(monkeypatch) -> None:
    import VibeCADGui as gui

    calls = []
    monkeypatch.setattr(gui, "start_choice", lambda choice_id: SimpleNamespace(prompt="Modify the open design to: "))
    monkeypatch.setattr(gui, "choose_start", lambda choice_id: calls.append(("complete", choice_id)))
    monkeypatch.setattr(gui.Gui, "runCommand", lambda name: calls.append(("command", name)), raising=False)
    monkeypatch.setattr(gui.Gui, "activateWorkbench", lambda name: calls.append(("activate", name)), raising=False)
    monkeypatch.setattr(gui.App, "newDocument", lambda *args: calls.append(("new", args)), raising=False)
    monkeypatch.setattr(gui, "_show_panel", lambda: None)
    monkeypatch.setattr(gui, "_find_dock", lambda: None)
    monkeypatch.setattr(gui, "_find_child", lambda *args: None)
    gui._onboarding_dialog = None

    gui._apply_onboarding_choice("modify-file")

    assert ("command", "Std_Open") in calls
    assert not any(call[0] == "new" for call in calls)


def test_failed_workspace_setup_does_not_complete_onboarding(monkeypatch) -> None:
    import VibeCADGui as gui

    completed = []
    monkeypatch.setattr(gui, "start_choice", lambda choice_id: SimpleNamespace(prompt="Create: "))
    monkeypatch.setattr(gui, "choose_start", lambda choice_id: completed.append(choice_id))
    monkeypatch.setattr(gui.App, "ActiveDocument", None, raising=False)
    monkeypatch.setattr(gui.App, "newDocument", lambda *args: (_ for _ in ()).throw(RuntimeError("injected creation failure")), raising=False)
    gui._onboarding_dialog = None

    with pytest.raises(RuntimeError, match="injected creation failure"):
        gui._apply_onboarding_choice("new-part")

    assert completed == []
