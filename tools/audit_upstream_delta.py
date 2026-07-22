#!/usr/bin/env python3
"""Create a deterministic inventory of fork changes after the imported snapshot."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def category(path: str) -> str:
    if path.startswith("src/Mod/VibeCAD/"):
        return "vibecad_extension"
    if path.startswith((".github/", "package/", "tools/", "src/Tools/")):
        return "build_packaging_release"
    if path.startswith("docs/") or path in {"README.md", "CONTRIBUTING.md", "AGENTS.md"}:
        return "documentation_governance"
    if "MacAppBundle" in path or "Brand" in path or "brand" in path:
        return "branding_platform_adapter"
    return "upstream_core_or_shared"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--upstream", default="upstream/main")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    snapshot = git("rev-parse", args.snapshot)
    head = git("rev-parse", "HEAD")
    upstream = git("rev-parse", args.upstream)
    entries = []
    for line in git("diff", "--name-status", "--find-renames", snapshot, head).splitlines():
        fields = line.split("\t")
        status = fields[0]
        path = fields[-1]
        entries.append({"path": path, "status": status, "category": category(path)})
    counts: dict[str, int] = {}
    for item in entries:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    worktree_entries = []
    for line in git("status", "--porcelain=v1", "--untracked-files=all").splitlines():
        status = line[:2].strip() or "?"
        path = line[3:].split(" -> ")[-1]
        worktree_entries.append({"path": path, "status": status, "category": category(path)})
    merge_base = subprocess.run(
        ["git", "merge-base", "HEAD", args.upstream], text=True, capture_output=True
    ).stdout.strip() or None
    payload = {
        "schema": "vibecad-upstream-patch-inventory-v1",
        "version": 1,
        "snapshot_commit": snapshot,
        "fork_head": head,
        "upstream_head": upstream,
        "merge_base": merge_base,
        "history_related": merge_base is not None,
        "counts": counts,
        "file_count": len(entries),
        "files": entries,
        "worktree_file_count": len(worktree_entries),
        "worktree_files": worktree_entries,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
