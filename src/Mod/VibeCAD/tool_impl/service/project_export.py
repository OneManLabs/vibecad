# SPDX-License-Identifier: LGPL-2.1-or-later
"""Export named accepted CAD objects into the project export directory."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from VibeCADTools import unchanged_state

from .mesh_analyze import analyze_mesh


TOOL_SPEC = {
    "name": "project.export",
    "description": (
        "Export named objects to a new file in the project exports directory. "
        "Never overwrites. STL, 3MF, and OBJ create meshes. STEP and IGES preserve neutral geometry."
    ),
    "contextual": True,
    "safety": "EXTERNAL",
    "requires_document": True,
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "object_names": {
                "type": "array", "items": {"type": "string", "minLength": 1},
                "minItems": 1, "maxItems": 64,
                "description": "Exact internal object names to export.",
            },
            "format": {
                "type": "string", "enum": ["stl", "3mf", "obj", "step", "iges"],
                "description": "Output format: stl, 3mf, obj, step, or iges.",
            },
            "file_name": {
                "type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$",
                "description": "New file base name without a directory. The format extension is added automatically.",
            },
        },
        "required": ["object_names", "format", "file_name"],
        "additionalProperties": False,
    },
}


def _clean_file_name(value: str, extension: str) -> str:
    name = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", name):
        raise ValueError("file_name must be a short portable file base name.")
    suffix = f".{extension}"
    return name if name.lower().endswith(suffix) else name + suffix


def run(service: Any, object_names: list[str], format: str, file_name: str) -> dict[str, Any]:
    from VibeCADManagedPolicy import enforce_action, load_managed_policy

    enforce_action(load_managed_policy(), "export")
    service.authorize("export")
    document = service._active_document()
    if document is None:
        return {"ok": False, "error": "No active document.", "state_change": unchanged_state()}
    output_format = str(format or "").lower()
    mesh_formats = {"stl", "3mf", "obj"}
    brep_formats = {"step", "iges"}
    if output_format not in mesh_formats | brep_formats:
        return {
            "ok": False,
            "error": "format must be stl, 3mf, obj, step, or iges.",
            "state_change": unchanged_state(),
        }
    unique_names = list(dict.fromkeys(str(name) for name in object_names or []))
    objects = [document.getObject(name) for name in unique_names]
    missing = [name for name, obj in zip(unique_names, objects) if obj is None]
    if missing:
        return {
            "ok": False, "error": "Export objects were not found: " + ", ".join(missing),
            "state_change": unchanged_state(),
        }
    invalid = []
    for obj in objects:
        shape = getattr(obj, "Shape", None)
        valid_shape = (
            shape is not None and not shape.isNull() and shape.isValid()
        )
        if output_format in brep_formats:
            valid_for_format = valid_shape
        else:
            mesh = getattr(obj, "Mesh", None)
            valid_mesh = False
            if mesh is not None:
                analysis = analyze_mesh(mesh)
                valid_mesh = analysis.get("verdict") == "ready"
            valid_for_format = valid_shape or valid_mesh
        if not valid_for_format:
            invalid.append(obj.Name)
    if invalid:
        return {
            "ok": False,
            "error": (
                "Export objects do not have valid geometry for "
                f"{output_format}: " + ", ".join(invalid)
            ),
            "state_change": unchanged_state(),
        }
    scope = service.project_scope_snapshot()
    root = Path(str(scope.get("root") or "")).resolve()
    if not root.is_dir():
        raise RuntimeError("The active document does not have a durable project directory.")
    output_name = _clean_file_name(file_name, output_format)
    directory = root / "exports"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / output_name
    if target.exists():
        return {
            "ok": False, "error": f"Export file already exists: {target.name}",
            "state_change": unchanged_state(),
        }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".vibecad-export-", suffix=f".{output_format}", dir=directory
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        if output_format in mesh_formats:
            import Mesh

            Mesh.export(objects, str(temporary))
        else:
            import Part

            Part.export(objects, str(temporary))
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("The exporter did not create a nonempty file.")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    recorder = getattr(service, "record_audit_event", None)
    if callable(recorder):
        recorder(
            category="project", action="export", outcome="success", actor_type="ai",
            details={"format": output_format, "sha256": digest, "object_count": len(objects)},
        )
    return {
        "ok": True,
        "export": {
            "format": output_format, "path": str(target), "size": target.stat().st_size,
            "sha256": digest, "objects": unique_names,
        },
        "state_change": unchanged_state(),
    }
