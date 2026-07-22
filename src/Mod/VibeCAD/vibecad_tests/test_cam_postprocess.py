# SPDX-License-Identifier: LGPL-2.1-or-later

from pathlib import Path
import sys
from types import SimpleNamespace

from tool_impl.service import cam_postprocess


class Job:
    def __init__(self, commands=None):
        self.Name = "Job"
        self.PostProcessor = "old"
        self.PostProcessorArgs = "--old"
        operation = SimpleNamespace(
            Name="Face",
            Path=SimpleNamespace(
                Commands=list([object()] if commands is None else commands)
            ),
        )
        self.Operations = SimpleNamespace(Group=[operation])

    def getEnumerationsOfProperty(self, _name):
        return ["old", "grbl", "linuxcnc"]


class Service:
    def __init__(self, root, job):
        self.root = root
        self.job = job
        self.permissions = []
        self.events = []

    def authorize(self, permission):
        self.permissions.append(permission)

    def _get_cam_job(self, name=None):
        return self.job if name == self.job.Name else None

    def project_scope_snapshot(self):
        return {"root": str(self.root)}

    def record_audit_event(self, **event):
        self.events.append(event)


def _allow_policy(monkeypatch):
    import VibeCADManagedPolicy as policy

    monkeypatch.setattr(policy, "load_managed_policy", lambda: {})
    monkeypatch.setattr(policy, "enforce_action", lambda _current, _action: None)


def _native_modules(monkeypatch):
    Grbl = type("Grbl", (), {"export": lambda self: [("program", "G21\nG0 X0 Y0\nM2\n")]})
    Grbl.__module__ = "grbl_post"
    factory = SimpleNamespace(get_post_processor=lambda _job, _name: Grbl())
    processor = SimpleNamespace(PostProcessorFactory=factory)
    monkeypatch.setitem(sys.modules, "Path", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "Path.Post", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "Path.Post.Processor", processor)


def test_postprocess_writes_content_bound_nonoverwriting_artifact_and_restores_job(
    tmp_path, monkeypatch
):
    _allow_policy(monkeypatch)
    _native_modules(monkeypatch)
    job = Job()
    service = Service(tmp_path, job)

    result = cam_postprocess.run(
        service, "Job", "grbl", "metric", True, False, "face-program"
    )

    assert result["ok"] is True
    target = Path(result["artifact"]["path"])
    assert target.read_text(encoding="utf-8").startswith("G21")
    assert result["artifact"]["processor_module"] == "grbl_post"
    assert len(result["artifact"]["sha256"]) == 64
    assert job.PostProcessor == "old" and job.PostProcessorArgs == "--old"
    assert service.permissions == ["export"]
    assert service.events[0]["action"] == "cam_postprocess"
    second = cam_postprocess.run(
        service, "Job", "grbl", "metric", True, False, "face-program"
    )
    assert second["ok"] is False and "already exists" in second["error"]


def test_postprocess_rejects_empty_native_path_before_creating_directory(
    tmp_path, monkeypatch
):
    _allow_policy(monkeypatch)
    job = Job(commands=[])
    service = Service(tmp_path, job)

    result = cam_postprocess.run(
        service, "Job", "grbl", "metric", True, False, "empty"
    )

    assert result["ok"] is False
    assert "nonempty native path" in result["error"]
    assert not (tmp_path / "exports").exists()
