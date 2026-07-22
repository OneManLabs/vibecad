# SPDX-License-Identifier: LGPL-2.1-or-later

"""Post-process one exact accepted CAM job to a controlled project artifact."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from VibeCADTools import unchanged_state


TOOL_SPEC = {
    "name": "cam.postprocess",
    "description": (
        "Post-process one exact native CAM job to a new project-scoped G-code "
        "file. The processor and output options are explicit. The file is "
        "content-bound and never overwrites an existing artifact. Generic "
        "processor output is not machine certification; verify machine limits "
        "and work offsets before use."
    ),
    "contextual": True,
    "safety": "EXTERNAL",
    "workbench": "CAMWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "job_name": {
                "type": "string",
                "description": "Exact internal name of the native CAM job to post-process.",
            },
            "processor": {
                "type": "string", "enum": ["grbl", "linuxcnc"],
                "description": "Approved native generic postprocessor implementation.",
            },
            "units": {
                "type": "string", "enum": ["metric", "imperial"],
                "description": "Units written into the G-code artifact.",
            },
            "comments": {
                "type": "boolean",
                "description": "true retains generated comments; false removes them.",
            },
            "line_numbers": {
                "type": "boolean",
                "description": "true adds G-code line numbers; false omits them.",
            },
            "file_name": {
                "type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$",
                "description": "New portable file name; .nc is added when absent.",
            },
        },
        "required": [
            "job_name", "processor", "units", "comments", "line_numbers",
            "file_name",
        ],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    job_name: str,
    processor: str,
    units: str,
    comments: bool,
    line_numbers: bool,
    file_name: str,
) -> dict[str, Any]:
    from VibeCADManagedPolicy import enforce_action, load_managed_policy

    enforce_action(load_managed_policy(), "export")
    service.authorize("export")
    clean_processor = str(processor or "").strip().lower()
    if clean_processor not in {"grbl", "linuxcnc"}:
        return _invalid("processor must be grbl or linuxcnc.")
    clean_units = str(units or "").strip().lower()
    if clean_units not in {"metric", "imperial"}:
        return _invalid("units must be metric or imperial.")
    clean_file = str(file_name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", clean_file):
        raise ValueError("file_name must be a short portable file base name.")
    if not clean_file.lower().endswith(".nc"):
        clean_file += ".nc"
    job = service._get_cam_job(str(job_name or "").strip() or None)
    if job is None:
        return _invalid(f"CAM job not found: {job_name}.")
    operations = list(getattr(getattr(job, "Operations", None), "Group", []) or [])
    empty = [
        obj.Name for obj in operations
        if not list(getattr(getattr(obj, "Path", None), "Commands", []) or [])
    ]
    if not operations or empty:
        return _invalid(
            "Every CAM job operation must have a nonempty native path before post-processing.",
            empty_operations=empty,
        )
    scope = service.project_scope_snapshot()
    root = Path(str(scope.get("root") or "")).resolve()
    if not root.is_dir():
        raise RuntimeError("The active document does not have a durable project directory.")
    directory = root / "exports"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / clean_file
    if target.exists():
        return _invalid(f"Export file already exists: {target.name}")
    arguments = [
        "--no-show-editor", "--no-header",
        "--metric" if clean_units == "metric" else "--inches",
        "--comments" if comments else "--no-comments",
        "--line-numbers" if line_numbers else "--no-line-numbers",
    ]
    old_args = str(getattr(job, "PostProcessorArgs", "") or "")
    old_processor = str(getattr(job, "PostProcessor", "") or "")
    try:
        from Path.Post.Processor import PostProcessorFactory

        job.PostProcessorArgs = " ".join(arguments)
        available = list(job.getEnumerationsOfProperty("PostProcessor"))
        if clean_processor in available:
            job.PostProcessor = clean_processor
        native = PostProcessorFactory.get_post_processor(job, clean_processor)
        expected = {"grbl": ("grbl_post", "Grbl"), "linuxcnc": ("linuxcnc_post", "Linuxcnc")}
        if native is None or (
            native.__class__.__module__, native.__class__.__name__
        ) != expected[clean_processor]:
            raise RuntimeError("The native postprocessor identity is not approved.")
        sections = native.export()
    except Exception as exc:
        return _invalid(f"Native CAM post-processing failed: {exc}")
    finally:
        job.PostProcessorArgs = old_args
        if old_processor in list(job.getEnumerationsOfProperty("PostProcessor")):
            job.PostProcessor = old_processor
    if not isinstance(sections, list) or not sections:
        return _invalid("The native postprocessor returned no output sections.")
    payload_parts: list[str] = []
    section_evidence: list[dict[str, Any]] = []
    for index, section in enumerate(sections):
        if not isinstance(section, tuple) or len(section) != 2:
            return _invalid(f"Native postprocessor section {index} is malformed.")
        name, code = section
        if not isinstance(name, str) or not name or not isinstance(code, str) or not code or "\0" in code:
            return _invalid(f"Native postprocessor section {index} has invalid text.")
        encoded = code.encode("utf-8")
        section_evidence.append({
            "name": name, "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        })
        payload_parts.append(code if code.endswith("\n") else code + "\n")
    payload = "".join(payload_parts).encode("utf-8")
    line_count = len(payload.decode("utf-8").splitlines())
    if not payload or len(payload) > 64 * 1024 * 1024 or line_count > 2_000_000:
        return _invalid("The postprocessed G-code size is outside the allowed limits.")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".vibecad-cam-", suffix=".nc", dir=directory
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(payload).hexdigest()
    recorder = getattr(service, "record_audit_event", None)
    if callable(recorder):
        recorder(
            category="project", action="cam_postprocess", outcome="success",
            actor_type="ai", details={
                "processor": clean_processor, "sha256": digest,
                "operation_count": len(operations),
            },
        )
    return {
        "ok": True,
        "artifact": {
            "path": str(target), "size": len(payload), "sha256": digest,
            "line_count": line_count, "processor": clean_processor,
            "processor_module": native.__class__.__module__,
            "processor_class": native.__class__.__name__,
            "units": clean_units, "comments": bool(comments),
            "line_numbers": bool(line_numbers), "arguments": arguments,
            "sections": section_evidence, "machine_configured": False,
            "machine_limits_checked": False,
            "configuration_scope": "generic_postprocessor_defaults",
        },
        "state_change": unchanged_state(),
    }


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {
        "ok": False, "error": message, "retry_same_call": False,
        "state_change": unchanged_state(), **details,
    }
