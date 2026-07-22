# SPDX-License-Identifier: LGPL-2.1-or-later
"""Export one exact TechDraw page without replacing an existing file."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from VibeCADTools import unchanged_state


TOOL_SPEC = {
    "name": "project.export_drawing",
    "description": (
        "Export one exact TechDraw page as a new project PDF, DXF, or SVG file. "
        "Never overwrites an existing file."
    ),
    "contextual": True,
    "safety": "EXTERNAL",
    "workbench": "TechDrawWorkbench",
    "requires_document": True,
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "page_name": {
                "type": "string", "minLength": 1,
                "description": "Exact internal TechDraw page name.",
            },
            "format": {
                "type": "string", "enum": ["pdf", "dxf", "svg"],
                "description": "Drawing output format: pdf, dxf, or svg.",
            },
            "file_name": {
                "type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$",
                "description": "Portable new file name without a directory.",
            },
        },
        "required": ["page_name", "format", "file_name"],
        "additionalProperties": False,
    },
}


def _name(value: str, extension: str) -> str:
    clean = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", clean):
        raise ValueError("file_name must be a short portable file base name.")
    suffix = f".{extension}"
    return clean if clean.lower().endswith(suffix) else clean + suffix


def run(service: Any, page_name: str, format: str, file_name: str) -> dict[str, Any]:
    from VibeCADManagedPolicy import enforce_action, load_managed_policy

    enforce_action(load_managed_policy(), "export")
    service.authorize("export")
    document = service._active_document()
    if document is None:
        return {"ok": False, "error": "No active document.", "state_change": unchanged_state()}
    page = document.getObject(str(page_name or "").strip())
    if page is None or getattr(page, "TypeId", "") != "TechDraw::DrawPage":
        return {
            "ok": False,
            "error": f"TechDraw page not found by exact internal name: {page_name}",
            "state_change": unchanged_state(),
        }
    output_format = str(format or "").strip().lower()
    if output_format not in {"pdf", "dxf", "svg"}:
        return {"ok": False, "error": "format must be pdf, dxf, or svg.", "state_change": unchanged_state()}
    output_name = _name(file_name, output_format)
    root = Path(str(service.project_scope_snapshot().get("root") or "")).resolve()
    if not root.is_dir():
        raise RuntimeError("The active document does not have a durable project directory.")
    directory = root / "exports"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / output_name
    if target.exists():
        return {"ok": False, "error": f"Export file already exists: {target.name}", "state_change": unchanged_state()}
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".vibecad-drawing-", suffix=f".{output_format}", dir=directory
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        if output_format == "dxf":
            import TechDraw

            TechDraw.writeDXFPage(page, str(temporary))
        else:
            import TechDrawGui

            if output_format == "pdf":
                TechDrawGui.exportPageAsPdf(page, str(temporary))
            else:
                TechDrawGui.exportPageAsSvg(page, str(temporary))
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("The drawing exporter did not create a nonempty file.")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    recorder = getattr(service, "record_audit_event", None)
    if callable(recorder):
        recorder(
            category="project", action="export_drawing", outcome="success",
            actor_type="ai",
            details={"format": output_format, "sha256": digest, "page": page.Name},
        )
    return {
        "ok": True,
        "export": {
            "format": output_format, "path": str(target),
            "size": target.stat().st_size, "sha256": digest, "page": page.Name,
        },
        "state_change": unchanged_state(),
    }
