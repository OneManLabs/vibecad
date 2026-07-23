#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run and verify the installed macOS package CAD acceptance smoke test."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from typing import Any


SCHEMA = "vibecad-macos-clean-install-smoke-v1"
RUN_STATES = (
    "Understanding",
    "Inspecting design",
    "Planning",
    "Creating preview",
    "Validating",
    "Applying revision",
    "Complete",
)
DIMENSIONS = (24.0, 16.0, 8.0)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _checked_file(record: dict[str, Any], root: Path, suffix: str) -> Path:
    path = Path(str(record.get("path") or ""))
    if not path.is_absolute() or not _inside(path, root):
        raise ValueError(f"Smoke artifact is outside the expected root: {path}")
    if path.suffix.lower() != suffix.lower():
        raise ValueError(f"Smoke artifact has the wrong extension: {path}")
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"Smoke artifact is missing or empty: {path}")
    if int(record.get("size") or 0) != path.stat().st_size:
        raise ValueError(f"Smoke artifact size changed: {path}")
    if str(record.get("sha256") or "") != _sha256(path):
        raise ValueError(f"Smoke artifact SHA-256 changed: {path}")
    return path


def verify_report(
    report_path: Path,
    expected_root: Path,
    expected_application: Path,
) -> dict[str, Any]:
    """Verify durable smoke evidence with no FreeCAD dependency."""

    report_file = report_path.resolve()
    root = expected_root.resolve()
    application = expected_application.resolve()
    if not _inside(report_file, root) or not report_file.is_file():
        raise ValueError("The smoke report is missing from the expected root.")
    payload = json.loads(report_file.read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA or payload.get("ok") is not True:
        raise ValueError(
            "The installed package smoke test did not pass: "
            + str(payload.get("error") or "invalid report")
        )
    if Path(str(payload.get("application_path") or "")).resolve() != application:
        raise ValueError("The smoke test did not use the expected installed application.")
    if tuple(payload.get("run_states") or ()) != RUN_STATES:
        raise ValueError("The deterministic session did not emit all stable run states.")
    revision_id = str(payload.get("accepted_revision") or "")
    if len(revision_id) != 64 or any(character not in "0123456789abcdef" for character in revision_id):
        raise ValueError("The smoke report has no valid accepted revision identifier.")
    if int(payload.get("revision_count") or 0) != 1:
        raise ValueError("The smoke test did not create exactly one accepted revision.")

    module_root = application / "Contents/Resources/Mod/VibeCAD"
    module_paths = payload.get("module_paths")
    if not isinstance(module_paths, dict) or not module_paths:
        raise ValueError("The smoke report has no installed module paths.")
    for name, value in module_paths.items():
        path = Path(str(value or ""))
        if not path.is_file() or not _inside(path, module_root):
            raise ValueError(f"The smoke test loaded {name} outside the installed app: {path}")

    document = payload.get("document")
    if not isinstance(document, dict):
        raise ValueError("The smoke report has no document evidence.")
    _checked_file(document, root, ".FCStd")
    if tuple(float(value) for value in document.get("dimensions") or ()) != DIMENSIONS:
        raise ValueError("The reopened CAD document has unexpected dimensions.")
    if document.get("fully_constrained") is not True:
        raise ValueError("The reopened CAD sketch is not fully constrained.")
    if document.get("shape_valid") is not True:
        raise ValueError("The reopened CAD shape is not valid.")

    exports = payload.get("exports")
    if not isinstance(exports, dict) or set(exports) != {"step", "stl"}:
        raise ValueError("The smoke report must contain STEP and STL evidence.")
    _checked_file(exports["step"], root, ".step")
    _checked_file(exports["stl"], root, ".stl")
    if exports["step"].get("shape_valid") is not True:
        raise ValueError("The STEP round-trip shape is not valid.")
    if int(exports["stl"].get("facet_count") or 0) <= 0:
        raise ValueError("The STL round-trip mesh has no facets.")
    return payload


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _make_provider(base_provider: type, provider_result: type):
    class DeterministicProvider(base_provider):
        model = "macos-clean-install-deterministic-v1"

        def run(
            self,
            prompt,
            context,
            tool_runner=None,
            cancellation_check=None,
            progress_callback=None,
        ):
            del prompt, cancellation_check, progress_callback
            if not callable(tool_runner):
                raise RuntimeError("The deterministic provider has no CAD tool runner.")

            def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                result = tool_runner(
                    name,
                    json.dumps(
                        arguments,
                        ensure_ascii=True,
                        separators=(",", ":"),
                    ),
                )
                if result.get("ok") is not True:
                    raise RuntimeError(f"{name} failed: {result}")
                return result

            body = call("partdesign.create_body", {"label": "Clean Machine Part"})
            body_name = body["mutation"]["body"]
            sketch = call(
                "partdesign.create_sketch",
                {
                    "body_name": body_name,
                    "label": "Clean Machine Profile",
                    "support": {"type": "origin_plane", "plane": "XY_Plane"},
                },
            )
            sketch_name = sketch["mutation"]["sketch"]
            call("partdesign.edit_sketch", {"sketch_name": sketch_name})
            call(
                "sketcher.draw_rectangle",
                {
                    "width": DIMENSIONS[0],
                    "height": DIMENSIONS[1],
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
                    "label": "Clean Machine Pad",
                    "extent": {"type": "length", "length": DIMENSIONS[2]},
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
                        "purpose": "A deterministic clean-package acceptance part.",
                        "units": "mm",
                        "critical_dimensions": [
                            {"name": "width", "value": DIMENSIONS[0], "unit": "mm"},
                            {"name": "depth", "value": DIMENSIONS[1], "unit": "mm"},
                            {"name": "height", "value": DIMENSIONS[2], "unit": "mm"},
                        ],
                    },
                },
            )
            return provider_result(
                "Created and accepted one deterministic native parametric part."
            )

    return DeterministicProvider()


def _bounds(shape_or_mesh: Any) -> tuple[float, float, float]:
    bound_box = shape_or_mesh.BoundBox
    return (
        round(float(bound_box.XLength), 6),
        round(float(bound_box.YLength), 6),
        round(float(bound_box.ZLength), 6),
    )


def _run_installed_smoke(root: Path, application: Path) -> dict[str, Any]:
    import FreeCAD as App
    import FreeCADGui as Gui
    import Mesh
    import Part

    import VibeCADAcceptance
    import VibeCADImportAssets
    import VibeCADProject
    import VibeCADSession
    import VibeCADStepValidator
    from VibeCADCore import VibeCADService
    from VibeCADProvider import BaseProvider, ProviderResult
    from VibeCADRevision import VibeCADRevisionStore
    from tool_impl.service import project_export, project_import_step

    root.mkdir(parents=True, exist_ok=True)
    document_path = root / "clean-machine-parametric.FCStd"
    if document_path.exists():
        raise RuntimeError(f"The clean smoke document already exists: {document_path}")
    Gui.activateWorkbench("PartDesignWorkbench")
    document = App.newDocument("VibeCADCleanMachineSmoke")
    document.saveAs(str(document_path))
    App.setActiveDocument(document.Name)
    service = VibeCADService()
    observed_states: list[str] = []

    def progress(event: dict[str, Any]) -> None:
        if event.get("event") == "run_state_changed":
            observed_states.append(str(event.get("state") or ""))

    response = VibeCADSession.run_prompt(
        "Create a 24 by 16 by 8 mm editable parametric box.",
        service=service,
        prefer_online=False,
        provider=_make_provider(BaseProvider, ProviderResult),
        progress_callback=progress,
    )
    if response.error:
        raise RuntimeError(response.error)
    decision = response.context.get("candidate_decision") or {}
    if decision.get("decision") != "accept" or decision.get("mode") != "automatic":
        raise RuntimeError(f"The deterministic candidate was not accepted: {decision}")
    if tuple(observed_states) != RUN_STATES:
        raise RuntimeError(f"The stable run states were not emitted in order: {observed_states}")
    scope = service.project_scope_snapshot()
    revisions = VibeCADRevisionStore(Path(str(scope["root"])), str(scope["project_id"]))
    records = revisions.list_records()
    if len(records) != 1 or revisions.head() is None:
        raise RuntimeError("The deterministic session did not create one accepted revision.")
    revision_id = str(revisions.head().get("revision_id") or "")
    if revision_id != str(decision.get("revision_id") or ""):
        raise RuntimeError("The accepted revision head does not match the session response.")

    App.closeDocument(document.Name)
    document = App.openDocument(str(document_path))
    App.setActiveDocument(document.Name)
    document.recompute()
    bodies = [item for item in document.Objects if item.TypeId == "PartDesign::Body"]
    sketches = [item for item in document.Objects if item.TypeId == "Sketcher::SketchObject"]
    pads = [item for item in document.Objects if item.TypeId == "PartDesign::Pad"]
    if len(bodies) != 1 or len(sketches) != 1 or len(pads) != 1:
        raise RuntimeError("The reopened document lost its native Body, Sketch, or Pad.")
    object_types = [item.TypeId for item in bodies + sketches + pads]
    sketch = sketches[0]
    pad = pads[0]
    if int(sketch.solve()) != 0 or bool(sketch.FullyConstrained) is not True:
        raise RuntimeError("The reopened native sketch is not fully constrained.")
    shape = pad.Shape
    if shape.isNull() or not shape.isValid() or _bounds(shape) != DIMENSIONS:
        raise RuntimeError("The reopened Pad is invalid or has incorrect dimensions.")

    reopened_service = VibeCADService()
    step_result = project_export.run(
        reopened_service,
        [pad.Name],
        "step",
        "clean-machine-parametric",
    )
    stl_result = project_export.run(
        reopened_service,
        [pad.Name],
        "stl",
        "clean-machine-parametric",
    )
    if step_result.get("ok") is not True or stl_result.get("ok") is not True:
        raise RuntimeError(
            f"The accepted document export failed: STEP={step_result}, STL={stl_result}"
        )
    step_record = dict(step_result["export"])
    stl_record = dict(stl_result["export"])
    step_path = Path(str(step_record["path"]))
    stl_path = Path(str(stl_record["path"]))
    step_shape = Part.read(str(step_path))
    stl_mesh = Mesh.Mesh(str(stl_path))
    if step_shape.isNull() or not step_shape.isValid() or _bounds(step_shape) != DIMENSIONS:
        raise RuntimeError("The STEP round trip is invalid or has incorrect dimensions.")
    if int(stl_mesh.CountFacets) <= 0 or _bounds(stl_mesh) != DIMENSIONS:
        raise RuntimeError("The STL round trip is empty or has incorrect dimensions.")
    App.closeDocument(document.Name)

    step_record["shape_valid"] = True
    step_record["dimensions"] = list(_bounds(step_shape))
    stl_record["facet_count"] = int(stl_mesh.CountFacets)
    stl_record["dimensions"] = list(_bounds(stl_mesh))
    return {
        "schema": SCHEMA,
        "ok": True,
        "application_path": str(application),
        "run_states": observed_states,
        "accepted_revision": revision_id,
        "revision_count": len(records),
        "module_paths": {
            "VibeCADAcceptance": str(Path(VibeCADAcceptance.__file__).resolve()),
            "VibeCADImportAssets": str(Path(VibeCADImportAssets.__file__).resolve()),
            "VibeCADProject": str(Path(VibeCADProject.__file__).resolve()),
            "VibeCADSession": str(Path(VibeCADSession.__file__).resolve()),
            "VibeCADStepValidator": str(Path(VibeCADStepValidator.__file__).resolve()),
            "project_export": str(Path(project_export.__file__).resolve()),
            "project_import_step": str(Path(project_import_step.__file__).resolve()),
        },
        "document": {
            "path": str(document_path),
            "size": document_path.stat().st_size,
            "sha256": _sha256(document_path),
            "dimensions": list(DIMENSIONS),
            "fully_constrained": True,
            "shape_valid": True,
            "object_types": object_types,
        },
        "exports": {"step": step_record, "stl": stl_record},
    }


def _schedule_application_quit() -> None:
    from PySide import QtCore, QtWidgets

    application = QtWidgets.QApplication.instance()
    if application is not None:
        QtCore.QTimer.singleShot(0, application.quit)


def _freecad_entry() -> None:
    root = Path(os.environ["VIBECAD_CLEAN_INSTALL_SMOKE_ROOT"]).resolve()
    application = Path(
        os.environ.get("VIBECAD_INSTALLED_APP_PATH", "/Applications/VibeCAD.app")
    ).resolve()
    report_path = root / "smoke-report.json"
    try:
        payload = _run_installed_smoke(root, application)
    except BaseException as exc:
        payload = {
            "schema": SCHEMA,
            "ok": False,
            "application_path": str(application),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    _atomic_json(report_path, payload)
    print(f"VibeCAD clean-install smoke report: {report_path}")
    _schedule_application_quit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-report", type=Path, required=True)
    parser.add_argument("--expected-root", type=Path, required=True)
    parser.add_argument(
        "--expected-application",
        type=Path,
        default=Path("/Applications/VibeCAD.app"),
    )
    args = parser.parse_args(argv)
    try:
        payload = verify_report(
            args.verify_report,
            args.expected_root,
            args.expected_application,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        "Installed VibeCAD package smoke passed: "
        f"revision {payload['accepted_revision']}."
    )
    return 0


if os.environ.get("VIBECAD_RUN_CLEAN_INSTALL_SMOKE") == "1":
    _freecad_entry()
elif __name__ == "__main__":
    raise SystemExit(main())
