# SPDX-License-Identifier: LGPL-2.1-or-later
"""Non-overwriting revision report and branch artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Mapping

from VibeCADRevision import VibeCADRevisionStore


REPORT_SCHEMA = "vibecad-revision-report-v1"
BRANCH_SCHEMA = "vibecad-revision-branch-v1"
EXPORT_VERSION = 1
_REVISION_ID = re.compile(r"[0-9a-f]{64}")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.is_dir():
        raise RuntimeError("Accepted revision project snapshot is missing.")
    for item in sorted(path.rglob("*"), key=lambda value: value.relative_to(path).as_posix()):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        if item.is_symlink():
            digest.update(b"L\0" + relative + b"\0" + os.readlink(item).encode("utf-8") + b"\0")
        elif item.is_dir():
            digest.update(b"D\0" + relative + b"\0")
        else:
            digest.update(b"F\0" + relative + b"\0" + _sha256(item).encode("ascii") + b"\0")
    return digest.hexdigest()


def _exclusive_bytes(target: Path, content: bytes) -> None:
    """Promote new content without replacing an existing user file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise FileExistsError(f"Revision artifact already exists: {target}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _safe_project_artifact(project_root: Path, relative_value: Any) -> Path:
    relative = Path(str(relative_value or ""))
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise RuntimeError("Accepted revision document path is unsafe.")
    root = project_root.resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise RuntimeError("Accepted revision document path leaves the project.")
    return path


def create_revision_report(
    store: VibeCADRevisionStore,
    target: str | Path,
    *,
    revision_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Export selected immutable records and a content identity."""
    records = store.list_records()
    if revision_ids is not None:
        requested = [str(item).strip().lower() for item in revision_ids]
        if not requested or any(not _REVISION_ID.fullmatch(item) for item in requested):
            raise ValueError("Select one or more valid revisions for the report.")
        selected = set(requested)
        records = [record for record in records if record["revision_id"] in selected]
        if len(records) != len(selected):
            raise KeyError("A selected revision does not exist in this project.")
    if not records:
        raise RuntimeError("The project has no accepted revisions to report.")
    head = store.head()
    content = {
        "project_id": store.project_id,
        "head_revision": head["revision_id"] if head else None,
        "revision_ids": [record["revision_id"] for record in records],
        "records": records,
    }
    report = {
        "schema": REPORT_SCHEMA,
        "version": EXPORT_VERSION,
        **content,
        "content_sha256": hashlib.sha256(_canonical_json(content)).hexdigest(),
    }
    destination = Path(target)
    if destination.suffix.lower() != ".json":
        raise ValueError("Revision report file name must end with .json.")
    _exclusive_bytes(destination, json.dumps(report, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    return {**report, "path": str(destination), "size_bytes": destination.stat().st_size}


def create_revision_branch(
    store: VibeCADRevisionStore,
    revision_id: str,
    target: str | Path,
    *,
    target_project_id: str | None = None,
) -> dict[str, Any]:
    """Create a new CAD file and lineage record from one accepted artifact."""
    record = store.read(revision_id)
    artifact = record.get("accepted_artifact")
    if not isinstance(artifact, Mapping):
        raise RuntimeError("The selected revision has no accepted CAD artifact.")
    source = _safe_project_artifact(store.project_root, artifact.get("document"))
    expected = str(artifact.get("document_sha256") or "")
    if not source.is_file() or not _REVISION_ID.fullmatch(expected) or _sha256(source) != expected:
        raise RuntimeError("The accepted revision CAD artifact failed its integrity check.")
    destination = Path(target)
    if destination.suffix.lower() != ".fcstd":
        raise ValueError("Revision branch file name must end with .FCStd.")
    lineage_path = destination.with_suffix(destination.suffix + ".vibecad-branch.json")
    if destination.exists() or lineage_path.exists():
        raise FileExistsError("The branch CAD file or lineage record already exists.")
    lineage_content = {
        "source_project_id": store.project_id,
        "source_revision": record["revision_id"],
        "source_document_sha256": expected,
        "branch_document": destination.name,
        "target_project_id": str(target_project_id or "") or None,
    }
    lineage = {
        "schema": BRANCH_SCHEMA,
        "version": EXPORT_VERSION,
        **lineage_content,
        "content_sha256": hashlib.sha256(_canonical_json(lineage_content)).hexdigest(),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    document_created = False
    try:
        shutil.copyfile(source, temporary)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(temporary, destination)
        document_created = True
        _exclusive_bytes(
            lineage_path,
            json.dumps(lineage, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
    except Exception:
        if document_created:
            destination.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return {
        **lineage,
        "path": str(destination),
        "lineage_path": str(lineage_path),
        "size_bytes": destination.stat().st_size,
    }


def resolve_revision_project_snapshot(
    store: VibeCADRevisionStore, revision_id: str
) -> Path:
    """Return one verified accepted project-state snapshot."""
    record = store.read(revision_id)
    artifact = record.get("accepted_artifact")
    if not isinstance(artifact, Mapping):
        raise RuntimeError("The selected revision has no accepted project snapshot.")
    snapshot = _safe_project_artifact(store.project_root, artifact.get("project_snapshot"))
    expected = str(artifact.get("project_tree_sha256") or "")
    if not _REVISION_ID.fullmatch(expected) or _tree_sha256(snapshot) != expected:
        raise RuntimeError("The accepted revision project snapshot failed its integrity check.")
    snapshot_root = snapshot.resolve()
    for item in snapshot.rglob("*"):
        if item.is_symlink():
            resolved = item.resolve()
            if resolved != snapshot_root and snapshot_root not in resolved.parents:
                raise RuntimeError(
                    "The accepted revision project snapshot contains an unsafe link."
                )
    return snapshot
