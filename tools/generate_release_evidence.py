#!/usr/bin/env python3
"""Generate checksums, a CycloneDX SBOM, and in-toto release provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, ensure_ascii=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def packages(path: Path | None) -> list[dict]:
    if path is None or not path.is_file():
        return []
    result = []
    pattern = re.compile(r"^(?P<name>[A-Za-z0-9_.+-]+)\s+(?P<version>\S+)")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line.strip())
        if match:
            result.append({"type": "library", "name": match["name"], "version": match["version"]})
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", action="append", type=Path, required=True)
    parser.add_argument("--packages", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-uri", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--builder-id", required=True)
    parser.add_argument("--build-type", default="https://vibecad.dev/build-types/macos-release/v1")
    parser.add_argument("--application-name", default="VibeCAD")
    parser.add_argument("--version", default="development")
    parser.add_argument("--channel", default="development")
    parser.add_argument("--download-base-url", default="")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_sha):
        parser.error("--source-sha must contain 40 lowercase hex characters")
    artifacts = [path.resolve() for path in args.artifact]
    if any(not path.is_file() for path in artifacts):
        raise SystemExit("Each release artifact must be a file.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    subjects = [
        {"name": path.name, "digest": {"sha256": sha256(path)}, "size": path.stat().st_size}
        for path in artifacts
    ]
    checksum_path = args.output_dir / "SHA256SUMS"
    checksum_path.write_text("".join(f"{item['digest']['sha256']}  {item['name']}\n" for item in subjects), encoding="utf-8")
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sbom = {
        "bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "component": {
                "type": "application",
                "name": args.application_name,
                "version": args.version,
            },
        },
        "components": packages(args.packages),
    }
    provenance = {
        "_type": "https://in-toto.io/Statement/v1", "subject": subjects,
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": args.build_type,
                "externalParameters": {"source": {"uri": args.source_uri, "digest": {"sha1": args.source_sha}}},
                "internalParameters": {}, "resolvedDependencies": [],
            },
            "runDetails": {"builder": {"id": args.builder_id}, "metadata": {"invocationId": os.environ.get("GITHUB_RUN_ID", "local")}},
        },
    }
    atomic_json(args.output_dir / "vibecad-macos.cdx.json", sbom)
    atomic_json(args.output_dir / "vibecad-macos.intoto.jsonl", provenance)
    update = {
        "schema": "vibecad-update-manifest-v1",
        "version": 1,
        "release_version": args.version,
        "channel": args.channel,
        "published_at": timestamp,
        "artifacts": [
            {
                **item,
                "url": f"{args.download_base_url.rstrip('/')}/{item['name']}"
                if args.download_base_url
                else None,
            }
            for item in subjects
        ],
    }
    atomic_json(args.output_dir / "vibecad-macos-update.json", update)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
