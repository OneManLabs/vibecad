# SPDX-License-Identifier: LGPL-2.1-or-later

"""Assistant-panel and provider-context contracts for STEP attachments."""

from __future__ import annotations

import json
from pathlib import Path
import queue
import sys
import threading
from types import ModuleType, SimpleNamespace

import VibeCADCore as core
import VibeCADGui as gui
import VibeCADImportAssets as import_assets


def _contains_path_field(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            key in {"path", "source_path", "relative_path", "internal_path"}
            or _contains_path_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_path_field(item) for item in value)
    return False


def _asset(
    index: int,
    *,
    private_path: str = "",
    availability: str = "verified",
) -> dict[str, object]:
    asset_id = f"{index:032x}"
    return {
        "schema": "vibecad-project-import-asset-v1",
        "version": 1,
        "asset_id": asset_id,
        "stored_name": f"{asset_id}.step",
        "format": "step",
        "size_bytes": index + 1,
        "sha256": f"{index:064x}",
        "created_at": "2026-07-22T12:00:00Z",
        "project_id": "step-ui-test",
        "availability": availability,
        "available": True,
        "path": private_path,
        "source_path": private_path,
    }


def test_provider_step_asset_list_is_bounded_path_free_and_preserves_status(
    monkeypatch,
) -> None:
    private_path = "/Users/example/Secret Design/source.step"
    statuses = ("verified", "changed", "missing")
    values = [
        _asset(
            index,
            private_path=private_path,
            availability=statuses[index % len(statuses)],
        )
        for index in range(20)
    ]
    listing_calls: list[tuple[str, str, int]] = []

    def list_assets(
        root: str,
        project_id: str,
        *,
        limit: int,
        cancellation_check=None,
        progress_callback=None,
    ):
        assert cancellation_check is None
        assert progress_callback is None
        listing_calls.append((root, project_id, limit))
        return {
            "asset_count": len(values),
            "assets": values,
        }

    monkeypatch.setattr(
        import_assets,
        "registered_import_assets",
        list_assets,
    )
    service = object.__new__(core.VibeCADService)
    service.project_scope_snapshot = lambda: {
        "root": "/private/project/root",
        "project_id": "step-ui-test",
    }

    summary = service.provider_registered_import_assets()

    assert listing_calls == [
        ("/private/project/root", "step-ui-test", core.MAX_PROVIDER_IMPORT_ASSETS)
    ]
    assert summary["schema"] == "vibecad-project-import-assets-context-v1"
    assert summary["asset_count"] == 20
    assert summary["listed_asset_count"] == core.MAX_PROVIDER_IMPORT_ASSETS
    assert summary["assets_omitted"] == 20 - core.MAX_PROVIDER_IMPORT_ASSETS
    assert [item["asset_id"] for item in summary["assets"]] == [
        f"{index:032x}" for index in range(8, 20)
    ]
    assert [item["availability"] for item in summary["assets"]] == [
        statuses[index % len(statuses)] for index in range(8, 20)
    ]
    assert all("available" not in item for item in summary["assets"])
    assert not _contains_path_field(summary)
    assert private_path not in json.dumps(summary)
    assert len(json.dumps(summary, separators=(",", ":")).encode("utf-8")) < 8192


def test_provider_context_includes_registered_step_assets(monkeypatch) -> None:
    service = object.__new__(core.VibeCADService)
    service.active_workbench_name = lambda: "PartWorkbench"
    service.modeling_engine = lambda: "native"
    service.last_capability_route = lambda: None
    service.provider_turn_document_summary = lambda: {"name": "Part"}
    service.provider_turn_selection_summary = lambda: {
        "selection_count": 0,
        "selection": [],
    }
    service.view_screenshot_summary = lambda: {"captured": False}
    service.pending_reference_image_attachments = lambda: []
    service.design_brief = lambda: {
        "schema": "vibecad-design-brief-v1",
        "version": 1,
    }
    registered = {
        "schema": "vibecad-project-import-assets-context-v1",
        "version": 1,
        "project_id": "step-ui-test",
        "asset_count": 1,
        "listed_asset_count": 1,
        "assets_omitted": 0,
        "asset_context_limit": core.MAX_PROVIDER_IMPORT_ASSETS,
        "supported_formats": ["step"],
        "assets": [
            {
                **_asset(1),
                "availability": "not_verified",
            }
        ],
    }
    registered["assets"][0].pop("available")
    registered["assets"][0].pop("path")
    registered["assets"][0].pop("source_path")
    service.provider_registered_import_assets = lambda: registered

    context = service.provider_context_summary()

    assert context["registered_import_assets"] == registered
    assert not _contains_path_field(context["registered_import_assets"])


def test_unsupported_secure_store_does_not_block_provider_context(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        import_assets, "import_asset_store_supported", lambda: False
    )
    monkeypatch.setattr(
        import_assets,
        "registered_import_assets",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("The unsupported store must not be opened.")
        ),
    )
    service = object.__new__(core.VibeCADService)
    scope = {
        "root": "C:/Users/Designer/Project",
        "project_id": "windows-project",
    }

    summary = service.provider_registered_import_assets(scope=scope)

    assert summary["store_status"] == "unavailable"
    assert summary["unavailable_reason"] == (
        "secure_file_identity_is_not_supported"
    )
    assert summary["asset_count"] == 0
    assert summary["assets"] == []
    assert not _contains_path_field(summary)


def test_unsupported_secure_store_disables_step_attachment(monkeypatch) -> None:
    class _FileDialog:
        @staticmethod
        def getOpenFileName(*_args):
            raise AssertionError("The file dialog must not open.")

    pyside = ModuleType("PySide")
    pyside.QtWidgets = SimpleNamespace(QFileDialog=_FileDialog)
    monkeypatch.setitem(sys.modules, "PySide", pyside)
    monkeypatch.setattr(
        import_assets, "import_asset_store_supported", lambda: False
    )
    messages: list[str] = []
    monkeypatch.setattr(gui, "_require_saved_document", lambda: True)
    monkeypatch.setattr(gui, "_is_assistant_run_active", lambda: False)
    monkeypatch.setattr(gui, "_step_attachment_active", False)
    monkeypatch.setattr(gui, "_set_status_line", messages.append)

    gui._attach_step_from_panel()

    assert messages == [
        "Secure STEP attachment is not available on this platform."
    ]


def test_step_chooser_uses_platform_adapter_and_records_no_local_path(
    monkeypatch,
) -> None:
    private_path = "/Users/example/Secret Design/source.step"
    dialog_calls: list[tuple[object, str, str, str]] = []

    class _FileDialog:
        @staticmethod
        def getOpenFileName(parent, title, directory, file_filter):
            dialog_calls.append((parent, title, directory, file_filter))
            return private_path, file_filter

    pyside = ModuleType("PySide")
    pyside.QtWidgets = SimpleNamespace(QFileDialog=_FileDialog)
    monkeypatch.setitem(sys.modules, "PySide", pyside)
    dock = object()
    main_thread = threading.get_ident()
    project_scope = {
        "root": "/private/project/root",
        "project_id": "step-ui-test",
    }
    document = SimpleNamespace(Name="StepDocument", Uid="step-document-uid")
    monkeypatch.setattr(gui.App, "ActiveDocument", document, raising=False)
    authorization_calls: list[tuple[str, int]] = []

    class _Service:
        def project_scope_snapshot(self):
            return dict(project_scope)

        def authorize(self, permission: str) -> None:
            authorization_calls.append((permission, threading.get_ident()))

    service = _Service()
    adapter_calls: list[tuple[str, str, str, int]] = []
    policy_calls: list[int] = []
    event_calls: list[tuple[tuple, dict]] = []
    render_calls: list[tuple[str | None, int]] = []
    safe_asset = _asset(1, private_path=private_path)
    completion_calls: queue.Queue[object] = queue.Queue()
    worker_finished = threading.Event()

    def register(
        root,
        project_id,
        selected_path,
        *,
        policy_check,
        permission_check,
    ):
        adapter_calls.append(
            (root, project_id, selected_path, threading.get_ident())
        )
        policy_check()
        permission_check("design.modify")
        worker_finished.set()
        return dict(safe_asset)

    monkeypatch.setattr(import_assets, "register_import_asset", register)
    import VibeCADManagedPolicy as managed_policy

    monkeypatch.setattr(managed_policy, "load_managed_policy", lambda: {})
    monkeypatch.setattr(
        managed_policy,
        "validate_policy",
        lambda _policy: policy_calls.append(threading.get_ident()),
    )
    monkeypatch.setattr(gui, "get_service", lambda: service)
    monkeypatch.setattr(gui, "_find_dock", lambda: dock)
    monkeypatch.setattr(gui, "_require_saved_document", lambda: True)
    monkeypatch.setattr(gui, "_is_assistant_run_active", lambda: False)
    monkeypatch.setattr(
        gui,
        "_append_conversation",
        lambda *args, **kwargs: event_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        gui,
        "_render_assistant_run_state",
        lambda _dock, text=None: render_calls.append(
            (text, threading.get_ident())
        ),
    )
    monkeypatch.setattr(gui, "_ensure_document_thread_invoker", lambda: None)
    monkeypatch.setattr(
        gui,
        "_dispatch_to_document_thread",
        lambda operation: completion_calls.put(operation),
    )
    monkeypatch.setattr(gui, "_step_attachment_active", False)
    monkeypatch.setattr(gui, "_step_attachment_thread", None)

    gui._attach_step_from_panel()

    assert worker_finished.wait(2)
    completion = completion_calls.get(timeout=2)
    assert gui._step_attachment_active is True
    assert event_calls == []
    completion()

    assert dialog_calls == [
        (dock, "Attach STEP file", "", "STEP files (*.step *.stp)")
    ]
    assert authorization_calls == [("design.modify", main_thread)]
    assert len(adapter_calls) == 1
    assert adapter_calls[0][:3] == (
        "/private/project/root",
        "step-ui-test",
        private_path,
    )
    assert adapter_calls[0][3] != main_thread
    assert policy_calls == [adapter_calls[0][3]]
    assert render_calls == [
        ("Attaching STEP file...", main_thread),
        ("STEP file attached and ready for import.", main_thread),
    ]
    assert gui._step_attachment_active is False
    assert len(event_calls) == 1
    args, kwargs = event_calls[0]
    assert args == (
        "System",
        f"STEP file attached. Import asset ID: {safe_asset['asset_id']}.",
    )
    assert kwargs["persist"] is True
    assert kwargs["metadata"]["event"] == "step_import_asset_registered"
    assert not _contains_path_field(kwargs["metadata"])
    serialized_event = json.dumps({"args": args, "kwargs": kwargs})
    assert private_path not in serialized_event
    assert Path(private_path).name not in serialized_event


def test_step_control_is_disabled_while_busy(monkeypatch) -> None:
    class _Button:
        def __init__(self) -> None:
            self.enabled: list[bool] = []

        def setEnabled(self, value: bool) -> None:
            self.enabled.append(bool(value))

    class _Dock:
        def setProperty(self, _name: str, _value: object) -> None:
            return None

    button = _Button()
    run_state = {"active": True}
    monkeypatch.setattr(
        gui, "_is_assistant_run_active", lambda: run_state["active"]
    )
    monkeypatch.setattr(gui, "_is_assistant_cancel_requested", lambda: False)
    monkeypatch.setattr(gui, "_candidate_review_active", lambda: False)
    monkeypatch.setattr(
        gui, "_document_persistence_state", lambda: {"enabled": True}
    )
    monkeypatch.setattr(
        gui._sketch_close_continuation_controller, "snapshot", lambda: {}
    )
    monkeypatch.setattr(
        gui,
        "_find_child",
        lambda _widget_type, name, _dock=None: (
            button if name == "VibeAttachStep" else None
        ),
    )

    gui._render_assistant_run_state(_Dock())
    run_state["active"] = False
    monkeypatch.setattr(gui, "_step_attachment_active", True)
    gui._render_assistant_run_state(_Dock())
    monkeypatch.setattr(gui, "_step_attachment_active", False)
    gui._render_assistant_run_state(_Dock())

    assert button.enabled == [False, False, True]


def test_step_attachment_preflight_failure_is_path_free_and_starts_no_worker(
    monkeypatch,
) -> None:
    private_path = "/Users/example/Secret Design/preflight.step"

    class _FileDialog:
        @staticmethod
        def getOpenFileName(_parent, _title, _directory, file_filter):
            return private_path, file_filter

    class _Service:
        def project_scope_snapshot(self):
            return {
                "root": "/private/project/root",
                "project_id": "step-ui-test",
            }

        def authorize(self, _permission: str) -> None:
            raise PermissionError(f"No access to {private_path}")

    pyside = ModuleType("PySide")
    pyside.QtWidgets = SimpleNamespace(QFileDialog=_FileDialog)
    monkeypatch.setitem(sys.modules, "PySide", pyside)
    monkeypatch.setattr(
        gui.App,
        "ActiveDocument",
        SimpleNamespace(Name="StepDocument", Uid="step-document-uid"),
        raising=False,
    )
    messages: list[object] = []
    registration_calls: list[object] = []
    monkeypatch.setattr(
        import_assets,
        "register_import_asset",
        lambda *_args, **_kwargs: registration_calls.append(True),
    )
    monkeypatch.setattr(gui, "get_service", _Service)
    monkeypatch.setattr(gui, "_find_dock", lambda: object())
    monkeypatch.setattr(gui, "_require_saved_document", lambda: True)
    monkeypatch.setattr(gui, "_is_assistant_run_active", lambda: False)
    monkeypatch.setattr(
        gui,
        "_append_conversation",
        lambda *args, **kwargs: messages.append({"args": args, "kwargs": kwargs}),
    )
    monkeypatch.setattr(
        gui,
        "_render_assistant_run_state",
        lambda _dock, text=None: messages.append({"status": text}),
    )
    monkeypatch.setattr(gui, "_warn", messages.append)
    monkeypatch.setattr(gui, "_step_attachment_active", False)
    monkeypatch.setattr(gui, "_step_attachment_thread", None)

    gui._attach_step_from_panel()

    assert registration_calls == []
    assert gui._step_attachment_active is False
    assert gui._step_attachment_thread is None
    serialized = json.dumps(messages)
    assert private_path not in serialized
    assert Path(private_path).name not in serialized
    assert "PermissionError" in serialized
    assert "STEP file not attached." in serialized


def test_step_registration_failure_does_not_put_a_local_path_in_conversation(
    monkeypatch,
) -> None:
    private_path = "/Users/example/Secret Design/fault.step"

    class _FileDialog:
        @staticmethod
        def getOpenFileName(_parent, _title, _directory, file_filter):
            return private_path, file_filter

    pyside = ModuleType("PySide")
    pyside.QtWidgets = SimpleNamespace(QFileDialog=_FileDialog)
    monkeypatch.setitem(sys.modules, "PySide", pyside)
    main_thread = threading.get_ident()
    project_scope = {
        "root": "/private/project/root",
        "project_id": "step-ui-test",
    }
    monkeypatch.setattr(
        gui.App,
        "ActiveDocument",
        SimpleNamespace(Name="StepDocument", Uid="step-document-uid"),
        raising=False,
    )

    class _Service:
        def project_scope_snapshot(self):
            return dict(project_scope)

        def authorize(self, _permission: str) -> None:
            return None

    messages: list[object] = []
    completion_calls: queue.Queue[object] = queue.Queue()
    worker_finished = threading.Event()

    def fail(_root, _project_id, _selected_path, **_kwargs):
        worker_finished.set()
        raise RuntimeError(f"Could not read {private_path}")

    monkeypatch.setattr(import_assets, "register_import_asset", fail)
    monkeypatch.setattr(gui, "get_service", _Service)
    monkeypatch.setattr(gui, "_find_dock", lambda: object())
    monkeypatch.setattr(gui, "_require_saved_document", lambda: True)
    monkeypatch.setattr(gui, "_is_assistant_run_active", lambda: False)
    monkeypatch.setattr(
        gui,
        "_append_conversation",
        lambda *args, **kwargs: messages.append({"args": args, "kwargs": kwargs}),
    )
    monkeypatch.setattr(
        gui,
        "_render_assistant_run_state",
        lambda _dock, text=None: messages.append(
            {"status": text, "thread": threading.get_ident()}
        ),
    )
    monkeypatch.setattr(gui, "_warn", messages.append)
    monkeypatch.setattr(gui, "_ensure_document_thread_invoker", lambda: None)
    monkeypatch.setattr(
        gui,
        "_dispatch_to_document_thread",
        lambda operation: completion_calls.put(operation),
    )
    monkeypatch.setattr(gui, "_step_attachment_active", False)
    monkeypatch.setattr(gui, "_step_attachment_thread", None)

    gui._attach_step_from_panel()
    assert worker_finished.wait(2)
    assert gui._step_attachment_active is True
    completion_calls.get(timeout=2)()

    serialized = json.dumps(messages)
    assert private_path not in serialized
    assert Path(private_path).name not in serialized
    assert gui._step_attachment_active is False
    assert all(
        item.get("thread", main_thread) == main_thread
        for item in messages
        if isinstance(item, dict)
    )


def test_step_registration_scope_switch_does_not_write_current_conversation(
    monkeypatch,
) -> None:
    private_path = "/Users/example/Secret Design/scope-switch.step"

    class _FileDialog:
        @staticmethod
        def getOpenFileName(_parent, _title, _directory, file_filter):
            return private_path, file_filter

    pyside = ModuleType("PySide")
    pyside.QtWidgets = SimpleNamespace(QFileDialog=_FileDialog)
    monkeypatch.setitem(sys.modules, "PySide", pyside)
    first_scope = {"root": "/private/project/one", "project_id": "project-one"}
    second_scope = {"root": "/private/project/two", "project_id": "project-two"}
    current_scope = {"value": first_scope}
    first_document = SimpleNamespace(Name="First", Uid="first-uid")
    second_document = SimpleNamespace(Name="Second", Uid="second-uid")
    monkeypatch.setattr(gui.App, "ActiveDocument", first_document, raising=False)

    class _Service:
        def project_scope_snapshot(self):
            return dict(current_scope["value"])

        def authorize(self, _permission: str) -> None:
            return None

    entered = threading.Event()
    release = threading.Event()
    completions: queue.Queue[object] = queue.Queue()
    registrations: list[tuple[str, str]] = []
    conversation: list[object] = []
    statuses: list[str | None] = []

    def register(root, project_id, _path, **_kwargs):
        registrations.append((root, project_id))
        entered.set()
        assert release.wait(2)
        return _asset(3)

    monkeypatch.setattr(import_assets, "register_import_asset", register)
    monkeypatch.setattr(gui, "get_service", _Service)
    monkeypatch.setattr(gui, "_find_dock", lambda: object())
    monkeypatch.setattr(gui, "_require_saved_document", lambda: True)
    monkeypatch.setattr(gui, "_is_assistant_run_active", lambda: False)
    monkeypatch.setattr(gui, "_ensure_document_thread_invoker", lambda: None)
    monkeypatch.setattr(
        gui,
        "_dispatch_to_document_thread",
        lambda operation: completions.put(operation),
    )
    monkeypatch.setattr(
        gui,
        "_append_conversation",
        lambda *args, **kwargs: conversation.append((args, kwargs)),
    )
    monkeypatch.setattr(
        gui,
        "_render_assistant_run_state",
        lambda _dock, text=None: statuses.append(text),
    )
    monkeypatch.setattr(gui, "_step_attachment_active", False)
    monkeypatch.setattr(gui, "_step_attachment_thread", None)

    gui._attach_step_from_panel()
    assert entered.wait(2)
    gui.App.ActiveDocument = second_document
    current_scope["value"] = second_scope
    release.set()
    completions.get(timeout=2)()

    assert registrations == [(first_scope["root"], first_scope["project_id"])]
    assert conversation == []
    assert statuses == ["Attaching STEP file...", None]
    assert gui._step_attachment_active is False
    assert private_path not in json.dumps(statuses)


def test_step_control_has_accessible_text_and_action() -> None:
    source = Path(gui.__file__).read_text(encoding="utf-8")

    assert 'QPushButton("Attach STEP file"' in source
    assert 'setObjectName("VibeAttachStep")' in source
    assert 'setAccessibleName("Attach STEP file")' in source
    assert "setAccessibleDescription(" in source
    assert "attach_step_button.clicked.connect(_attach_step_from_panel)" in source
