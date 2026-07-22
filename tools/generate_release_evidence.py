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
from typing import Any
from urllib.parse import quote, urlencode, urlparse
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


def _required_text(item: dict[str, Any], field: str, index: int) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Package inventory item {index} has no valid {field}.")
    return value.strip()


def _purl_segment(value: str) -> str:
    return quote(value, safe=".-_~")


def _conda_channel(source: str, index: int) -> str:
    parsed = urlparse(source)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"Package inventory item {index} has no HTTPS source.")
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        raise ValueError(f"Package inventory item {index} has no conda channel.")
    return segments[-1]


def _structured_packages(raw: Any) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("The structured package inventory must be a nonempty JSON array.")
    result: list[dict] = []
    identities: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"Package inventory item {index} is not a JSON object.")
        if (
            item.get("name") == "freecad"
            and item.get("version") is None
            and item.get("kind") == "conda"
            and item.get("source") == "."
            and item.get("url") == "."
            and item.get("requested_spec") == "."
        ):
            # The local Pixi path package is the application represented by the
            # CycloneDX metadata component. It is not a resolved dependency.
            continue
        name = _required_text(item, "name", index)
        version = _required_text(item, "version", index)
        kind = _required_text(item, "kind", index).lower()
        source = _required_text(item, "source", index)
        package_sha256 = _required_text(item, "sha256", index).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", package_sha256):
            raise ValueError(f"Package inventory item {index} has an invalid SHA-256.")
        package_url = _required_text(item, "url", index)
        parsed_package_url = urlparse(package_url)
        if parsed_package_url.scheme != "https" or not parsed_package_url.netloc:
            raise ValueError(f"Package inventory item {index} has no HTTPS package URL.")

        properties = [
            {"name": "vibecad:package:kind", "value": kind},
            {"name": "vibecad:package:source", "value": source},
            {"name": "vibecad:package:sha256", "value": package_sha256},
        ]
        if kind == "conda":
            build = _required_text(item, "build", index)
            subdir = _required_text(item, "subdir", index)
            file_name = _required_text(item, "file_name", index)
            if file_name.endswith(".conda"):
                archive_type = "conda"
            elif file_name.endswith(".tar.bz2"):
                archive_type = "tar.bz2"
            else:
                raise ValueError(
                    f"Package inventory item {index} has an unsupported conda archive type."
                )
            channel = _conda_channel(source, index)
            qualifiers = urlencode(
                sorted(
                    {
                        "build": build,
                        "channel": channel,
                        "subdir": subdir,
                        "type": archive_type,
                    }.items()
                )
            )
            purl = (
                f"pkg:conda/{_purl_segment(name)}@"
                f"{_purl_segment(version)}?{qualifiers}"
            )
            properties.extend(
                [
                    {"name": "vibecad:package:archive-type", "value": archive_type},
                    {"name": "vibecad:package:build", "value": build},
                    {"name": "vibecad:package:channel", "value": channel},
                    {"name": "vibecad:package:subdir", "value": subdir},
                ]
            )
        elif kind in {"pypi", "python"}:
            normalized_name = re.sub(r"[-_.]+", "-", name).lower()
            purl = f"pkg:pypi/{_purl_segment(normalized_name)}@{_purl_segment(version)}"
        else:
            raise ValueError(f"Package inventory item {index} has unsupported kind {kind}.")
        if purl in identities:
            raise ValueError(f"Package inventory has a duplicate package identity: {purl}.")
        identities.add(purl)
        result.append(
            {
                "type": "library",
                "bom-ref": purl,
                "name": name,
                "version": version,
                "purl": purl,
                "hashes": [{"alg": "SHA-256", "content": package_sha256}],
                "externalReferences": [{"type": "distribution", "url": package_url}],
                "properties": sorted(properties, key=lambda value: value["name"]),
            }
        )
    if not result:
        raise ValueError("The structured package inventory has no resolved dependencies.")
    return sorted(result, key=lambda value: value["bom-ref"])


def _text_packages(path: Path) -> list[dict]:
    """Read the historical text inventory without claiming scanner-ready identity."""
    result = []
    pattern = re.compile(r"^(?P<name>[A-Za-z0-9_.+-]+)\s+(?P<version>\S+)")
    ignored_names = {"installed", "list", "name", "package"}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line.strip())
        if match and match["name"].lower() not in ignored_names:
            result.append(
                {
                    "type": "library",
                    "name": match["name"],
                    "version": match["version"],
                    "properties": [
                        {
                            "name": "vibecad:package:inventory-format",
                            "value": "legacy-text-unverified",
                        }
                    ],
                }
            )
    return result


def packages(path: Path | None) -> list[dict]:
    if path is None or not path.is_file():
        return []
    if path.suffix.lower() == ".json":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"The structured package inventory is invalid: {path}: {exc}") from exc
        return _structured_packages(raw)
    return _text_packages(path)


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
    application_purl = (
        f"pkg:generic/{_purl_segment(args.application_name.lower())}@"
        f"{_purl_segment(args.version)}"
    )
    sbom = {
        "bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "component": {
                "type": "application",
                "bom-ref": application_purl,
                "name": args.application_name,
                "version": args.version,
                "purl": application_purl,
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
