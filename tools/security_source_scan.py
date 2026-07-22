#!/usr/bin/env python3
"""Scan tracked release source for high-confidence credential material."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qs, unquote, urlparse


SCHEMA = "vibecad-security-source-scan-v1"
VERSION = 1
MAX_FILE_BYTES = 8 * 1024 * 1024

VULNERABILITY_SCHEMA = "vibecad-vulnerability-scan-evidence-v1"
VULNERABILITY_VERSION = 1
DECISIONS_SCHEMA = "vibecad-vulnerability-decisions-v1"
DECISIONS_VERSION = 1
SUPPORTED_SEVERITIES = {
    "unknown",
    "negligible",
    "low",
    "medium",
    "high",
    "critical",
}
BLOCKING_SEVERITIES = {"high", "critical"}
PINNED_GRYPE_VERSION = "0.116.0"
DATABASE_MAX_AGE = timedelta(hours=72)
DATABASE_FUTURE_TOLERANCE = timedelta(minutes=10)

# These expressions intentionally use high-confidence token shapes. Broad words
# such as "password" create noise and can hide an actual release failure.
PATTERN = re.compile(
    rb"(?P<aws_access_key>(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9]))"
    rb"|(?P<github_token>(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{30,})(?![A-Za-z0-9_]))"
    rb"|(?P<openai_token>(?<![A-Za-z0-9_-])(?:sk-proj-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,})(?![A-Za-z0-9_-]))"
    rb"|(?P<anthropic_token>(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-]))"
    rb"|(?P<slack_token>(?<![A-Za-z0-9-])xox[baprs]-[A-Za-z0-9-]{20,}(?![A-Za-z0-9-]))"
    rb"|(?P<private_key>-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----)"
)


def _tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--recurse-submodules", "-z"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [root / os.fsdecode(item) for item in result.stdout.split(b"\0") if item]


def _source_sha(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    value = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise RuntimeError("Git returned an invalid source commit identity.")
    return value


def _line_number(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


def _finding(path: Path, root: Path, data: bytes, name: str, match: re.Match[bytes]) -> dict:
    value = match.group(0)
    return {
        "rule": name,
        "path": path.relative_to(root).as_posix(),
        "line": _line_number(data, match.start()),
        "value_sha256": hashlib.sha256(value).hexdigest(),
        "value_length": len(value),
    }


def scan(root: Path, files: Iterable[Path] | None = None) -> dict:
    """Return a stable report. The report never contains the matched secret."""

    root = root.resolve()
    findings: list[dict] = []
    errors: list[dict] = []
    scanned = 0
    byte_count = 0
    explicit_files = files is not None
    paths = list(files) if explicit_files else _tracked_files(root)
    for source in paths:
        path = source if source.is_absolute() else root / source
        try:
            resolved_parent = path.parent.resolve()
            resolved_parent.relative_to(root)
            if path.is_symlink():
                data = os.readlink(path).encode("utf-8", errors="surrogateescape")
            else:
                size = path.stat().st_size
                if size > MAX_FILE_BYTES:
                    errors.append(
                        {
                            "path": path.relative_to(root).as_posix(),
                            "error": "file_exceeds_scan_limit",
                            "size": size,
                        }
                    )
                    continue
                data = path.read_bytes()
        except (OSError, ValueError) as exc:
            errors.append(
                {
                    "path": str(path),
                    "error": "read_failed",
                    "detail": str(exc),
                }
            )
            continue
        scanned += 1
        byte_count += len(data)
        findings.extend(
            _finding(path, root, data, str(match.lastgroup), match)
            for match in PATTERN.finditer(data)
        )
    findings.sort(key=lambda item: (item["path"], item["line"], item["rule"]))
    errors.sort(key=lambda item: (item["path"], item["error"]))
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "status": "pass" if not findings and not errors else "fail",
        "scope": "git-tracked-release-source",
        "source_sha": None if explicit_files else _source_sha(root),
        "rules_sha256": hashlib.sha256(PATTERN.pattern).hexdigest(),
        "scanned_file_count": scanned,
        "scanned_byte_count": byte_count,
        "finding_count": len(findings),
        "error_count": len(errors),
        "findings": findings,
        "errors": errors,
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} could not be read: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object.")
    return value


def _utc_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is missing.")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid timestamp.") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a UTC offset.")
    return parsed.astimezone(timezone.utc)


def _sbom_identity(path: Path) -> dict[str, Any]:
    sbom = _json_object(path, "CycloneDX SBOM")
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.5":
        raise ValueError("The vulnerability scan requires a CycloneDX 1.5 SBOM.")
    serial = str(sbom.get("serialNumber") or "")
    if not serial.startswith("urn:uuid:"):
        raise ValueError("The CycloneDX SBOM has no valid serial identity.")
    metadata = sbom.get("metadata")
    component = metadata.get("component") if isinstance(metadata, dict) else None
    if not isinstance(component, dict):
        raise ValueError("The CycloneDX SBOM has no application component identity.")
    name = str(component.get("name") or "").strip()
    version = str(component.get("version") or "").strip()
    if name != "VibeCAD" or not version:
        raise ValueError("The CycloneDX SBOM does not identify a VibeCAD release.")
    components = sbom.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("The CycloneDX SBOM component list is missing or empty.")
    package_identities: list[str] = []
    for index, package in enumerate(components):
        if not isinstance(package, dict):
            raise ValueError(f"CycloneDX component {index} is not a JSON object.")
        package_name = str(package.get("name") or "").strip()
        package_version = str(package.get("version") or "").strip()
        purl = str(package.get("purl") or "").strip()
        if (
            package.get("type") != "library"
            or not package_name
            or not package_version
            or package.get("bom-ref") != purl
            or not re.fullmatch(r"pkg:(?:conda|pypi)/[^@]+@[^?]+(?:\?.+)?", purl)
        ):
            raise ValueError(f"CycloneDX component {index} has no usable package identity.")
        if purl in package_identities:
            raise ValueError(f"CycloneDX component {index} has a duplicate package identity.")

        hashes = package.get("hashes")
        sha256_values = []
        if isinstance(hashes, list):
            sha256_values = [
                str(value.get("content") or "").lower()
                for value in hashes
                if isinstance(value, dict) and value.get("alg") == "SHA-256"
            ]
        if len(sha256_values) != 1 or not re.fullmatch(r"[0-9a-f]{64}", sha256_values[0]):
            raise ValueError(f"CycloneDX component {index} has no unique SHA-256 identity.")

        properties = package.get("properties")
        if not isinstance(properties, list):
            raise ValueError(f"CycloneDX component {index} has no identity properties.")
        property_map: dict[str, str] = {}
        for item in properties:
            if not isinstance(item, dict) or set(item) != {"name", "value"}:
                raise ValueError(f"CycloneDX component {index} has a malformed property.")
            property_name = str(item.get("name") or "")
            property_value = str(item.get("value") or "")
            if not property_name or not property_value or property_name in property_map:
                raise ValueError(f"CycloneDX component {index} has an invalid property.")
            property_map[property_name] = property_value
        required_properties = {
            "vibecad:package:kind",
            "vibecad:package:source",
            "vibecad:package:sha256",
        }
        if not required_properties.issubset(property_map):
            raise ValueError(f"CycloneDX component {index} has incomplete identity properties.")
        if property_map["vibecad:package:sha256"].lower() != sha256_values[0]:
            raise ValueError(f"CycloneDX component {index} has conflicting SHA-256 identities.")
        package_kind = property_map["vibecad:package:kind"].lower()
        if package_kind == "conda":
            conda_purl = re.fullmatch(r"pkg:conda/([^/@?]+)@([^?]+)\?(.+)", purl)
            if conda_purl is None or not {
                "vibecad:package:archive-type",
                "vibecad:package:build",
                "vibecad:package:channel",
                "vibecad:package:subdir",
            }.issubset(property_map):
                raise ValueError(f"CycloneDX component {index} has incomplete conda identity.")
            try:
                qualifiers = parse_qs(conda_purl.group(3), strict_parsing=True)
            except ValueError as exc:
                raise ValueError(
                    f"CycloneDX component {index} has invalid conda qualifiers."
                ) from exc
            expected_qualifiers = {
                "build": [property_map["vibecad:package:build"]],
                "channel": [property_map["vibecad:package:channel"]],
                "subdir": [property_map["vibecad:package:subdir"]],
                "type": [property_map["vibecad:package:archive-type"]],
            }
            if (
                qualifiers != expected_qualifiers
                or unquote(conda_purl.group(1)) != package_name
                or unquote(conda_purl.group(2)) != package_version
                or property_map["vibecad:package:archive-type"]
                not in {"conda", "tar.bz2"}
            ):
                raise ValueError(f"CycloneDX component {index} has mismatched conda identity.")
        elif package_kind in {"pypi", "python"}:
            if not purl.startswith("pkg:pypi/"):
                raise ValueError(f"CycloneDX component {index} has a mismatched Python identity.")
        else:
            raise ValueError(f"CycloneDX component {index} has an unsupported package kind.")

        references = package.get("externalReferences")
        distributions = [
            str(item.get("url") or "")
            for item in references or []
            if isinstance(item, dict) and item.get("type") == "distribution"
        ]
        if len(distributions) != 1:
            raise ValueError(f"CycloneDX component {index} has no unique distribution source.")
        distribution = urlparse(distributions[0])
        if distribution.scheme != "https" or not distribution.netloc:
            raise ValueError(f"CycloneDX component {index} has an invalid distribution source.")
        package_identities.append(purl)
    package_identities.sort()
    return {
        "filename": path.name,
        "sha256": _file_sha256(path),
        "bom_format": "CycloneDX",
        "spec_version": "1.5",
        "serial_number": serial,
        "application_name": name,
        "application_version": version,
        "component_count": len(components),
        "scanner_ready_component_count": len(package_identities),
        "package_identity_sha256": hashlib.sha256(
            "\n".join(package_identities).encode("utf-8")
        ).hexdigest(),
    }


def _scanner_identity(
    path: Path,
    raw: Mapping[str, Any],
    sbom_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    descriptor = raw.get("descriptor")
    if not isinstance(descriptor, dict):
        raise ValueError("The scanner JSON has no descriptor.")
    name = str(descriptor.get("name") or "").strip().lower()
    version = str(descriptor.get("version") or "").strip()
    if name != "grype" or version != PINNED_GRYPE_VERSION:
        raise ValueError("The scanner JSON does not identify the repository-pinned Grype version.")
    scan_source = raw.get("source")
    if not isinstance(scan_source, dict) or scan_source.get("type") != "sbom-file":
        raise ValueError("The scanner JSON does not identify a CycloneDX file input.")
    source_target = scan_source.get("target")
    if not isinstance(source_target, str) or not source_target.strip():
        raise ValueError("The scanner JSON has no SBOM input target.")
    if Path(source_target).resolve() != sbom_path.resolve():
        raise ValueError("The scanner JSON SBOM target does not match the release SBOM.")
    database = descriptor.get("db")
    if not isinstance(database, dict):
        raise ValueError("The scanner JSON has no vulnerability database identity.")
    status = database.get("status")
    providers = database.get("providers")
    if not isinstance(status, dict) or not isinstance(providers, dict) or not providers:
        raise ValueError("The scanner JSON has no current vulnerability database status.")
    built = _utc_time(status.get("built"), "Vulnerability database build date")
    schema_version = str(status.get("schemaVersion") or "").strip()
    if not re.fullmatch(r"v?[1-9][0-9]*(?:\.[0-9]+){0,2}", schema_version):
        raise ValueError("The vulnerability database schema version is invalid.")
    if status.get("valid") is not True or status.get("error") not in (None, ""):
        raise ValueError("The vulnerability scanner reported an invalid database.")
    source = str(status.get("from") or "").strip()
    parsed_source = urlparse(source)
    if parsed_source.scheme != "https" or not parsed_source.netloc:
        raise ValueError("The vulnerability database source is invalid.")
    checksum_values = parse_qs(parsed_source.query).get("checksum", [])
    if len(checksum_values) != 1:
        raise ValueError("The vulnerability database source has no unique checksum.")
    checksum = str(checksum_values[0]).strip().lower()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", checksum):
        raise ValueError("The vulnerability database checksum is invalid.")
    return (
        {
            "name": name,
            "version": version,
            "report_filename": path.name,
            "report_sha256": _file_sha256(path),
            "input": {"type": "sbom-file", "filename": sbom_path.name},
        },
        {
            "built_at": built.isoformat(),
            "schema_version": schema_version,
            "checksum": checksum,
            "source": source,
            "provider_count": len(providers),
        },
    )


def _normalized_findings(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    ignored_matches = raw.get("ignoredMatches", [])
    if not isinstance(ignored_matches, list):
        raise ValueError("The scanner JSON ignored match data is malformed.")
    if ignored_matches:
        raise ValueError(
            "The scanner JSON has ignored matches outside the versioned decision contract."
        )
    matches = raw.get("matches")
    if not isinstance(matches, list):
        raise ValueError("The scanner JSON has no matches array.")
    findings: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str, str]] = set()
    for index, match in enumerate(matches):
        if not isinstance(match, dict):
            raise ValueError(f"Scanner match {index} is not a JSON object.")
        vulnerability = match.get("vulnerability")
        artifact = match.get("artifact")
        if not isinstance(vulnerability, dict) or not isinstance(artifact, dict):
            raise ValueError(f"Scanner match {index} has no vulnerability or package identity.")
        vulnerability_id = str(vulnerability.get("id") or "").strip()
        severity = str(vulnerability.get("severity") or "").strip().lower()
        package_name = str(artifact.get("name") or "").strip()
        package_version = str(artifact.get("version") or "").strip()
        package_type = str(artifact.get("type") or "unknown").strip() or "unknown"
        package_purl = str(artifact.get("purl") or "").strip()
        if not vulnerability_id or severity not in SUPPORTED_SEVERITIES:
            raise ValueError(f"Scanner match {index} has an invalid vulnerability identity or severity.")
        if (
            not package_name
            or not package_version
            or not re.fullmatch(r"pkg:(?:conda|pypi)/[^@]+@[^?]+(?:\?.+)?", package_purl)
        ):
            raise ValueError(f"Scanner match {index} has an invalid package identity.")
        identity = (vulnerability_id, package_name, package_version, package_purl)
        if identity in identities:
            raise ValueError(f"Scanner JSON has a duplicate finding identity: {identity}.")
        identities.add(identity)
        fix = vulnerability.get("fix")
        fix = fix if isinstance(fix, dict) else {}
        versions = fix.get("versions") or []
        if not isinstance(versions, list) or any(
            not isinstance(value, str) or not value.strip() for value in versions
        ):
            raise ValueError(f"Scanner match {index} has invalid fix versions.")
        findings.append(
            {
                "vulnerability_id": vulnerability_id,
                "severity": severity,
                "package": {
                    "name": package_name,
                    "version": package_version,
                    "type": package_type,
                    "purl": package_purl,
                },
                "fix": {
                    "state": str(fix.get("state") or "unknown"),
                    "versions": sorted(set(versions)),
                },
            }
        )
    findings.sort(
        key=lambda item: (
            item["severity"],
            item["vulnerability_id"],
            item["package"]["name"],
            item["package"]["version"],
        )
    )
    return findings


def _decision_map(
    path: Path | None,
    *,
    source_sha: str,
    sbom_sha256: str,
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    if path is None:
        return {}
    raw = _json_object(path, "Vulnerability decision record")
    if raw.get("schema") != DECISIONS_SCHEMA or raw.get("version") != DECISIONS_VERSION:
        raise ValueError("The vulnerability decision record has an unsupported schema.")
    if raw.get("source_sha") != source_sha or raw.get("sbom_sha256") != sbom_sha256:
        raise ValueError("The vulnerability decision record has an identity mismatch.")
    decisions = raw.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("The vulnerability decision record has no decisions array.")
    result: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    required = {
        "vulnerability_id",
        "package_name",
        "package_version",
        "package_purl",
        "decision",
        "rationale",
        "owner",
        "reviewer",
        "expires_at",
    }
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict) or set(decision) != required:
            raise ValueError(f"Vulnerability decision {index} is malformed.")
        for field in required - {"decision", "expires_at"}:
            if not isinstance(decision.get(field), str) or not decision[field].strip():
                raise ValueError(f"Vulnerability decision {index} field {field} is invalid.")
        if decision.get("decision") != "ignore":
            raise ValueError(f"Vulnerability decision {index} must use the ignore decision.")
        expires = _utc_time(decision.get("expires_at"), f"Vulnerability decision {index} expiry")
        identity = (
            str(decision["vulnerability_id"]),
            str(decision["package_name"]),
            str(decision["package_version"]),
            str(decision["package_purl"]),
        )
        if identity in result:
            raise ValueError(f"Vulnerability decision identity is duplicated: {identity}.")
        result[identity] = {**decision, "expires_at": expires.isoformat()}
    return result


def create_vulnerability_evidence(
    scanner_json: Path,
    sbom_path: Path,
    source_sha: str,
    *,
    decisions_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Normalize Grype JSON and make a content-bound release decision report."""
    if not re.fullmatch(r"[0-9a-f]{40}", str(source_sha or "")):
        raise ValueError("The vulnerability evidence source commit is invalid.")
    current = now or datetime.now(timezone.utc)
    if current.utcoffset() is None:
        raise ValueError("The vulnerability evidence evaluation time must include an offset.")
    current = current.astimezone(timezone.utc)
    raw = _json_object(scanner_json, "Grype scanner JSON")
    scanner, database = _scanner_identity(scanner_json, raw, sbom_path)
    database_built = _utc_time(database["built_at"], "Vulnerability database build date")
    database_age = current - database_built
    if database_age > DATABASE_MAX_AGE:
        raise ValueError("The vulnerability database is older than the release policy permits.")
    if database_age < -DATABASE_FUTURE_TOLERANCE:
        raise ValueError("The vulnerability database build date is too far in the future.")
    database["age_seconds"] = int(database_age.total_seconds())
    database["age_policy"] = {
        "max_age_seconds": int(DATABASE_MAX_AGE.total_seconds()),
        "future_tolerance_seconds": int(DATABASE_FUTURE_TOLERANCE.total_seconds()),
    }
    sbom = _sbom_identity(sbom_path)
    findings = _normalized_findings(raw)
    decisions = _decision_map(
        decisions_path,
        source_sha=source_sha,
        sbom_sha256=sbom["sha256"],
    )
    finding_ids = {
        (
            finding["vulnerability_id"],
            finding["package"]["name"],
            finding["package"]["version"],
            finding["package"]["purl"],
        )
        for finding in findings
        if finding["severity"] in BLOCKING_SEVERITIES
    }
    unused = sorted(set(decisions) - finding_ids)
    if unused:
        raise ValueError(f"Vulnerability decisions do not match scanner findings: {unused}.")

    unresolved: list[dict[str, str]] = []
    expired: list[dict[str, str]] = []
    counts = {severity: 0 for severity in sorted(SUPPORTED_SEVERITIES)}
    for finding in findings:
        severity = str(finding["severity"])
        counts[severity] += 1
        identity = (
            str(finding["vulnerability_id"]),
            str(finding["package"]["name"]),
            str(finding["package"]["version"]),
            str(finding["package"]["purl"]),
        )
        decision = decisions.get(identity)
        if severity not in BLOCKING_SEVERITIES:
            finding["resolution"] = {"status": "not_blocking"}
            continue
        if decision is None:
            finding["resolution"] = {"status": "unresolved"}
            unresolved.append(
                {
                    "vulnerability_id": identity[0],
                    "package_name": identity[1],
                    "package_version": identity[2],
                    "package_purl": identity[3],
                    "severity": severity,
                }
            )
            continue
        expires = _utc_time(decision["expires_at"], "Vulnerability decision expiry")
        resolution = {
            "status": "ignored" if expires > current else "expired",
            "rationale": decision["rationale"],
            "owner": decision["owner"],
            "reviewer": decision["reviewer"],
            "expires_at": expires.isoformat(),
        }
        finding["resolution"] = resolution
        if expires <= current:
            expired.append(
                {
                    "vulnerability_id": identity[0],
                    "package_name": identity[1],
                    "package_version": identity[2],
                    "package_purl": identity[3],
                    "severity": severity,
                }
            )

    blocking_count = len(unresolved) + len(expired)
    return {
        "schema": VULNERABILITY_SCHEMA,
        "version": VULNERABILITY_VERSION,
        "status": "pass" if blocking_count == 0 else "fail",
        "evaluated_at": current.isoformat(),
        "source": {"commit_sha": source_sha},
        "sbom": sbom,
        "scanner": scanner,
        "database": database,
        "finding_counts": counts,
        "finding_count": len(findings),
        "unresolved_critical_or_high_count": blocking_count,
        "unresolved_findings": unresolved,
        "expired_decisions": expired,
        "findings": findings,
    }


def verify_vulnerability_evidence(
    raw: Any,
    *,
    expected_source_sha: str,
    expected_sbom_sha256: str,
) -> dict[str, Any]:
    """Fail closed on incomplete identity data or unresolved high-risk findings."""
    if not isinstance(raw, dict):
        raise ValueError("The vulnerability evidence is not a JSON object.")
    if raw.get("schema") != VULNERABILITY_SCHEMA or raw.get("version") != VULNERABILITY_VERSION:
        raise ValueError("The vulnerability evidence has an unsupported schema.")
    source = raw.get("source")
    sbom = raw.get("sbom")
    scanner = raw.get("scanner")
    database = raw.get("database")
    if not all(isinstance(item, dict) for item in (source, sbom, scanner, database)):
        raise ValueError("The vulnerability evidence has missing identity data.")
    if source.get("commit_sha") != expected_source_sha:
        raise ValueError("The vulnerability evidence source identity does not match.")
    if sbom.get("sha256") != expected_sbom_sha256:
        raise ValueError("The vulnerability evidence SBOM identity does not match.")
    if (
        sbom.get("bom_format") != "CycloneDX"
        or sbom.get("spec_version") != "1.5"
        or sbom.get("application_name") != "VibeCAD"
        or not str(sbom.get("serial_number") or "").startswith("urn:uuid:")
        or not str(sbom.get("application_version") or "").strip()
    ):
        raise ValueError("The vulnerability evidence SBOM metadata is invalid.")
    component_count = sbom.get("component_count")
    scanner_ready_count = sbom.get("scanner_ready_component_count")
    if (
        isinstance(component_count, bool)
        or not isinstance(component_count, int)
        or component_count <= 0
        or scanner_ready_count != component_count
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(sbom.get("package_identity_sha256") or "")
        )
    ):
        raise ValueError("The vulnerability evidence has no scanner-ready SBOM components.")
    if (
        scanner.get("name") != "grype"
        or scanner.get("version") != PINNED_GRYPE_VERSION
    ):
        raise ValueError("The vulnerability evidence scanner identity is invalid.")
    if scanner.get("input") != {
        "type": "sbom-file",
        "filename": sbom.get("filename"),
    }:
        raise ValueError("The vulnerability evidence scanner input identity is invalid.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(scanner.get("report_sha256") or "")):
        raise ValueError("The vulnerability scanner report identity is invalid.")
    evaluated = _utc_time(raw.get("evaluated_at"), "Vulnerability evaluation date")
    database_built = _utc_time(database.get("built_at"), "Vulnerability database build date")
    database_age = evaluated - database_built
    expected_age_policy = {
        "max_age_seconds": int(DATABASE_MAX_AGE.total_seconds()),
        "future_tolerance_seconds": int(DATABASE_FUTURE_TOLERANCE.total_seconds()),
    }
    if database.get("age_policy") != expected_age_policy:
        raise ValueError("The vulnerability database age policy is invalid.")
    if database.get("age_seconds") != int(database_age.total_seconds()):
        raise ValueError("The vulnerability database age evidence does not match.")
    if database_age > DATABASE_MAX_AGE:
        raise ValueError("The vulnerability database is older than the release policy permits.")
    if database_age < -DATABASE_FUTURE_TOLERANCE:
        raise ValueError("The vulnerability database build date is too far in the future.")
    checksum = str(database.get("checksum") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", checksum):
        raise ValueError("The vulnerability database identity is invalid.")
    schema_version = str(database.get("schema_version") or "")
    if not re.fullmatch(r"v?[1-9][0-9]*(?:\.[0-9]+){0,2}", schema_version):
        raise ValueError("The vulnerability database schema identity is invalid.")
    source_url = str(database.get("source") or "")
    parsed_source = urlparse(source_url)
    source_checksums = parse_qs(parsed_source.query).get("checksum", [])
    if (
        parsed_source.scheme != "https"
        or not parsed_source.netloc
        or source_checksums != [checksum]
    ):
        raise ValueError("The vulnerability database source identity is invalid.")
    provider_count = database.get("provider_count")
    if (
        isinstance(provider_count, bool)
        or not isinstance(provider_count, int)
        or provider_count <= 0
    ):
        raise ValueError("The vulnerability database provider count is invalid.")
    counts = raw.get("finding_counts")
    findings = raw.get("findings")
    if not isinstance(counts, dict) or set(counts) != SUPPORTED_SEVERITIES:
        raise ValueError("The vulnerability evidence finding counts are malformed.")
    finding_count = raw.get("finding_count")
    if (
        not isinstance(findings, list)
        or isinstance(finding_count, bool)
        or not isinstance(finding_count, int)
        or finding_count != len(findings)
    ):
        raise ValueError("The vulnerability evidence finding list is malformed.")
    for severity in SUPPORTED_SEVERITIES:
        count = counts.get(severity)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("The vulnerability evidence has an invalid finding count.")
    if sum(counts.values()) != len(findings):
        raise ValueError("The vulnerability evidence finding counts do not match.")
    derived_counts = {severity: 0 for severity in SUPPORTED_SEVERITIES}
    derived_blocking = 0
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError(f"Vulnerability finding {index} is malformed.")
        severity = str(finding.get("severity") or "").lower()
        if severity not in SUPPORTED_SEVERITIES:
            raise ValueError(f"Vulnerability finding {index} has an invalid severity.")
        derived_counts[severity] += 1
        package = finding.get("package")
        resolution = finding.get("resolution")
        if (
            not str(finding.get("vulnerability_id") or "").strip()
            or not isinstance(package, dict)
            or not str(package.get("name") or "").strip()
            or not str(package.get("version") or "").strip()
            or not re.fullmatch(
                r"pkg:(?:conda|pypi)/[^@]+@[^?]+(?:\?.+)?",
                str(package.get("purl") or ""),
            )
            or not isinstance(resolution, dict)
        ):
            raise ValueError(f"Vulnerability finding {index} has incomplete identity data.")
        if severity in BLOCKING_SEVERITIES:
            if resolution.get("status") != "ignored":
                derived_blocking += 1
                continue
            expires = _utc_time(
                resolution.get("expires_at"),
                f"Vulnerability finding {index} decision expiry",
            )
            if expires <= evaluated:
                derived_blocking += 1
            for field in ("rationale", "owner", "reviewer"):
                if not isinstance(resolution.get(field), str) or not resolution[field].strip():
                    raise ValueError(
                        f"Vulnerability finding {index} decision field {field} is invalid."
                    )
        elif resolution.get("status") != "not_blocking":
            raise ValueError(f"Vulnerability finding {index} has an invalid resolution.")
    if derived_counts != counts:
        raise ValueError("The vulnerability evidence severities do not match their counts.")
    blocking = raw.get("unresolved_critical_or_high_count")
    if isinstance(blocking, bool) or not isinstance(blocking, int) or blocking < 0:
        raise ValueError("The vulnerability evidence blocking count is invalid.")
    unresolved = raw.get("unresolved_findings")
    expired = raw.get("expired_decisions")
    if not isinstance(unresolved, list) or not isinstance(expired, list):
        raise ValueError("The vulnerability evidence decision data is missing.")
    if blocking != len(unresolved) + len(expired):
        raise ValueError("The vulnerability evidence blocking count does not match.")
    if blocking != derived_blocking:
        raise ValueError("The vulnerability evidence derived blocking count does not match.")
    if blocking or raw.get("status") != "pass":
        raise ValueError(
            "The vulnerability gate has unresolved critical or high findings "
            "or expired decisions."
        )
    return dict(raw)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--scanner-json", type=Path)
    parser.add_argument("--sbom", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--vulnerability-output", type=Path)
    args = parser.parse_args(argv)
    vulnerability_mode = any(
        value is not None
        for value in (
            args.scanner_json,
            args.sbom,
            args.source_sha,
            args.decisions,
            args.vulnerability_output,
        )
    )
    if vulnerability_mode:
        if args.scanner_json is None or args.sbom is None or not args.source_sha:
            parser.error("vulnerability mode requires --scanner-json, --sbom, and --source-sha")
        if args.vulnerability_output is None:
            parser.error("vulnerability mode requires --vulnerability-output")
        try:
            report = create_vulnerability_evidence(
                args.scanner_json,
                args.sbom,
                args.source_sha,
                decisions_path=args.decisions,
            )
            _write_json(args.vulnerability_output, report)
            verify_vulnerability_evidence(
                report,
                expected_source_sha=args.source_sha,
                expected_sbom_sha256=_file_sha256(args.sbom),
            )
        except (OSError, TypeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
        return 0

    try:
        report = scan(args.root)
    except (OSError, subprocess.CalledProcessError) as exc:
        report = {
            "schema": SCHEMA,
            "version": VERSION,
            "status": "fail",
            "scope": "git-tracked-release-source",
            "source_sha": None,
            "rules_sha256": hashlib.sha256(PATTERN.pattern).hexdigest(),
            "scanned_file_count": 0,
            "scanned_byte_count": 0,
            "finding_count": 0,
            "error_count": 1,
            "findings": [],
            "errors": [{"path": str(args.root), "error": "scope_failed", "detail": str(exc)}],
        }
    if args.json_output:
        _write_json(args.json_output, report)
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
