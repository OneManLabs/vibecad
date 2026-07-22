# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "tools" / "security_source_scan.py"
SPEC = importlib.util.spec_from_file_location("vibecad_security_source_scan", SCRIPT)
assert SPEC and SPEC.loader
scan_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scan_module)
EVIDENCE_SCRIPT = ROOT / "tools" / "generate_release_evidence.py"
EVIDENCE_SPEC = importlib.util.spec_from_file_location(
    "vibecad_generate_release_evidence", EVIDENCE_SCRIPT
)
assert EVIDENCE_SPEC and EVIDENCE_SPEC.loader
evidence_module = importlib.util.module_from_spec(EVIDENCE_SPEC)
EVIDENCE_SPEC.loader.exec_module(evidence_module)
GRYPE_PIN_SCRIPT = ROOT / "tools" / "verify_grype_archive.py"
GRYPE_PIN_SPEC = importlib.util.spec_from_file_location(
    "vibecad_verify_grype_archive", GRYPE_PIN_SCRIPT
)
assert GRYPE_PIN_SPEC and GRYPE_PIN_SPEC.loader
grype_pin_module = importlib.util.module_from_spec(GRYPE_PIN_SPEC)
GRYPE_PIN_SPEC.loader.exec_module(grype_pin_module)
MACOS_WORKFLOW = ROOT / ".github" / "workflows" / "vibecad-macos.yml"


def test_clean_source_report_is_versioned_and_complete(tmp_path: Path) -> None:
    clean = tmp_path / "clean.txt"
    clean.write_text("The provider key comes from secure storage.\n", encoding="utf-8")

    report = scan_module.scan(tmp_path, [clean])

    assert report == {
        "schema": "vibecad-security-source-scan-v1",
        "version": 1,
        "status": "pass",
        "scope": "git-tracked-release-source",
        "source_sha": None,
        "rules_sha256": scan_module.hashlib.sha256(scan_module.PATTERN.pattern).hexdigest(),
        "scanned_file_count": 1,
        "scanned_byte_count": clean.stat().st_size,
        "finding_count": 0,
        "error_count": 0,
        "findings": [],
        "errors": [],
    }


def test_secret_finding_is_redacted_and_content_bound(tmp_path: Path) -> None:
    source = tmp_path / "settings.txt"
    token = "sk-proj-" + "A" * 32
    source.write_text(f"name=value\nprovider={token}\n", encoding="utf-8")

    report = scan_module.scan(tmp_path, [source])

    assert report["status"] == "fail"
    assert report["finding_count"] == 1
    finding = report["findings"][0]
    assert finding["rule"] == "openai_token"
    assert finding["path"] == "settings.txt"
    assert finding["line"] == 2
    assert finding["value_length"] == len(token)
    assert token not in str(report)


def test_unreadable_or_oversize_scope_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "large.bin"
    source.write_bytes(b"0")
    original_limit = scan_module.MAX_FILE_BYTES
    scan_module.MAX_FILE_BYTES = 0
    try:
        report = scan_module.scan(tmp_path, [source])
    finally:
        scan_module.MAX_FILE_BYTES = original_limit

    assert report["status"] == "fail"
    assert report["scanned_file_count"] == 0
    assert report["error_count"] == 1
    assert report["errors"][0]["error"] == "file_exceeds_scan_limit"


def test_symlink_target_is_scanned_without_following_it(tmp_path: Path) -> None:
    outside = tmp_path.parent / ("sk-proj-" + "B" * 32)
    link = tmp_path / "linked"
    link.symlink_to(outside)

    report = scan_module.scan(tmp_path, [link])

    assert report["status"] == "fail"
    assert report["finding_count"] == 1
    assert report["findings"][0]["path"] == "linked"


SOURCE_SHA = "a" * 40
NOW = datetime(2026, 7, 22, 20, 0, tzinfo=timezone.utc)


def _component() -> dict:
    package_sha256 = "d" * 64
    purl = (
        "pkg:conda/example@1.0?build=py_0&channel=conda-forge&subdir=noarch&type=conda"
    )
    return {
        "type": "library",
        "bom-ref": purl,
        "name": "example",
        "version": "1.0",
        "purl": purl,
        "hashes": [{"alg": "SHA-256", "content": package_sha256}],
        "externalReferences": [
            {
                "type": "distribution",
                "url": "https://prefix.dev/conda-forge/noarch/example-1.0-py_0.conda",
            }
        ],
        "properties": [
            {"name": "vibecad:package:archive-type", "value": "conda"},
            {"name": "vibecad:package:build", "value": "py_0"},
            {"name": "vibecad:package:channel", "value": "conda-forge"},
            {"name": "vibecad:package:kind", "value": "conda"},
            {"name": "vibecad:package:sha256", "value": package_sha256},
            {"name": "vibecad:package:source", "value": "https://prefix.dev/conda-forge"},
            {"name": "vibecad:package:subdir", "value": "noarch"},
        ],
    }


def _sbom(tmp_path: Path) -> Path:
    path = tmp_path / "vibecad-macos.cdx.json"
    path.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "serialNumber": "urn:uuid:11111111-2222-3333-4444-555555555555",
                "version": 1,
                "metadata": {
                    "timestamp": "2026-07-22T19:00:00Z",
                    "component": {
                        "type": "application",
                        "name": "VibeCAD",
                        "version": "26.3.2",
                    },
                },
                "components": [_component()],
            }
        ),
        encoding="utf-8",
    )
    return path


def _match(severity: str, vulnerability_id: str = "CVE-2026-0001") -> dict:
    return {
        "vulnerability": {
            "id": vulnerability_id,
            "severity": severity,
            "fix": {"state": "fixed", "versions": ["1.1"]},
        },
        "artifact": {
            "name": "example",
            "version": "1.0",
            "type": "conda",
            "purl": _component()["purl"],
        },
    }


def _scanner(tmp_path: Path, matches: list[dict]) -> Path:
    path = tmp_path / "grype.json"
    path.write_text(
        json.dumps(
            {
                "matches": matches,
                "source": {
                    "type": "sbom-file",
                    "target": str(tmp_path / "vibecad-macos.cdx.json"),
                },
                "descriptor": {
                    "name": "grype",
                    "version": "0.116.0",
                    "configuration": {},
                    "db": {
                        "status": {
                            "schemaVersion": "v6.0.2",
                            "from": (
                                "https://grype.anchore.io/databases/v6/"
                                "vulnerability-db.tar.zst?checksum=sha256%3A"
                                + "b" * 64
                            ),
                            "built": "2026-07-22T18:00:00Z",
                            "path": "/tmp/grype/vulnerability.db",
                            "valid": True,
                        },
                        "providers": {
                            "nvd": {
                                "captured": "2026-07-22T17:00:00Z",
                                "input": "sha256:" + "c" * 64,
                            }
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _decisions(
    tmp_path: Path,
    sbom: Path,
    *,
    source_sha: str = SOURCE_SHA,
    expiry: str = "2026-08-01T00:00:00+00:00",
) -> Path:
    path = tmp_path / "vulnerability-decisions.json"
    path.write_text(
        json.dumps(
            {
                "schema": "vibecad-vulnerability-decisions-v1",
                "version": 1,
                "source_sha": source_sha,
                "sbom_sha256": hashlib.sha256(sbom.read_bytes()).hexdigest(),
                "decisions": [
                    {
                        "vulnerability_id": "CVE-2026-0001",
                        "package_name": "example",
                        "package_version": "1.0",
                        "package_purl": _component()["purl"],
                        "decision": "ignore",
                        "rationale": "The affected code path is not present.",
                        "owner": "security-owner",
                        "reviewer": "release-reviewer",
                        "expires_at": expiry,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_clean_vulnerability_scan_records_all_release_identities(tmp_path: Path) -> None:
    sbom = _sbom(tmp_path)
    scanner = _scanner(tmp_path, [])

    evidence = scan_module.create_vulnerability_evidence(
        scanner,
        sbom,
        SOURCE_SHA,
        now=NOW,
    )

    assert evidence["schema"] == "vibecad-vulnerability-scan-evidence-v1"
    assert evidence["version"] == 1
    assert evidence["status"] == "pass"
    assert evidence["source"] == {"commit_sha": SOURCE_SHA}
    assert evidence["sbom"]["sha256"] == hashlib.sha256(sbom.read_bytes()).hexdigest()
    assert evidence["sbom"]["serial_number"].startswith("urn:uuid:")
    assert evidence["scanner"]["name"] == "grype"
    assert evidence["scanner"]["version"] == "0.116.0"
    assert evidence["scanner"]["report_sha256"] == hashlib.sha256(
        scanner.read_bytes()
    ).hexdigest()
    assert evidence["database"] == {
        "built_at": "2026-07-22T18:00:00+00:00",
        "schema_version": "v6.0.2",
        "checksum": "sha256:" + "b" * 64,
        "source": (
            "https://grype.anchore.io/databases/v6/"
            "vulnerability-db.tar.zst?checksum=sha256%3A" + "b" * 64
        ),
        "provider_count": 1,
        "age_seconds": 7200,
        "age_policy": {
            "max_age_seconds": 259200,
            "future_tolerance_seconds": 600,
        },
    }
    verified = scan_module.verify_vulnerability_evidence(
        evidence,
        expected_source_sha=SOURCE_SHA,
        expected_sbom_sha256=evidence["sbom"]["sha256"],
    )
    assert verified["unresolved_critical_or_high_count"] == 0


@pytest.mark.parametrize("severity", ["High", "Critical"])
def test_unresolved_high_or_critical_finding_fails_gate(
    tmp_path: Path,
    severity: str,
) -> None:
    sbom = _sbom(tmp_path)
    scanner = _scanner(tmp_path, [_match(severity)])
    evidence = scan_module.create_vulnerability_evidence(
        scanner,
        sbom,
        SOURCE_SHA,
        now=NOW,
    )

    assert evidence["status"] == "fail"
    assert evidence["unresolved_critical_or_high_count"] == 1
    assert evidence["findings"][0]["resolution"] == {"status": "unresolved"}
    with pytest.raises(ValueError, match="unresolved critical or high"):
        scan_module.verify_vulnerability_evidence(
            evidence,
            expected_source_sha=SOURCE_SHA,
            expected_sbom_sha256=evidence["sbom"]["sha256"],
        )


def test_exact_ignored_finding_with_future_expiry_passes(tmp_path: Path) -> None:
    sbom = _sbom(tmp_path)
    scanner = _scanner(tmp_path, [_match("High")])
    decisions = _decisions(tmp_path, sbom)
    evidence = scan_module.create_vulnerability_evidence(
        scanner,
        sbom,
        SOURCE_SHA,
        decisions_path=decisions,
        now=NOW,
    )

    assert evidence["status"] == "pass"
    resolution = evidence["findings"][0]["resolution"]
    assert resolution["status"] == "ignored"
    assert resolution["owner"] == "security-owner"
    assert resolution["reviewer"] == "release-reviewer"
    assert resolution["expires_at"] == "2026-08-01T00:00:00+00:00"
    scan_module.verify_vulnerability_evidence(
        evidence,
        expected_source_sha=SOURCE_SHA,
        expected_sbom_sha256=evidence["sbom"]["sha256"],
    )


def test_expired_ignore_decision_fails_gate(tmp_path: Path) -> None:
    sbom = _sbom(tmp_path)
    scanner = _scanner(tmp_path, [_match("Critical")])
    decisions = _decisions(
        tmp_path,
        sbom,
        expiry="2026-07-22T19:59:59+00:00",
    )
    evidence = scan_module.create_vulnerability_evidence(
        scanner,
        sbom,
        SOURCE_SHA,
        decisions_path=decisions,
        now=NOW,
    )

    assert evidence["status"] == "fail"
    assert evidence["findings"][0]["resolution"]["status"] == "expired"
    assert evidence["expired_decisions"][0]["vulnerability_id"] == "CVE-2026-0001"
    with pytest.raises(ValueError, match="expired decisions"):
        scan_module.verify_vulnerability_evidence(
            evidence,
            expected_source_sha=SOURCE_SHA,
            expected_sbom_sha256=evidence["sbom"]["sha256"],
        )


def test_decision_identity_mismatch_is_rejected(tmp_path: Path) -> None:
    sbom = _sbom(tmp_path)
    scanner = _scanner(tmp_path, [_match("High")])
    decisions = _decisions(tmp_path, sbom, source_sha="c" * 40)
    with pytest.raises(ValueError, match="identity mismatch"):
        scan_module.create_vulnerability_evidence(
            scanner,
            sbom,
            SOURCE_SHA,
            decisions_path=decisions,
            now=NOW,
        )


def test_decision_package_purl_mismatch_is_rejected(tmp_path: Path) -> None:
    sbom = _sbom(tmp_path)
    scanner = _scanner(tmp_path, [_match("High")])
    decisions = _decisions(tmp_path, sbom)
    raw = json.loads(decisions.read_text(encoding="utf-8"))
    raw["decisions"][0]["package_purl"] = "pkg:conda/other@1.0?build=0&channel=x&subdir=noarch&type=conda"
    decisions.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="do not match scanner findings"):
        scan_module.create_vulnerability_evidence(
            scanner,
            sbom,
            SOURCE_SHA,
            decisions_path=decisions,
            now=NOW,
        )


def test_ignore_decision_for_nonblocking_finding_is_rejected(tmp_path: Path) -> None:
    sbom = _sbom(tmp_path)
    scanner = _scanner(tmp_path, [_match("Low")])

    with pytest.raises(ValueError, match="do not match scanner findings"):
        scan_module.create_vulnerability_evidence(
            scanner,
            sbom,
            SOURCE_SHA,
            decisions_path=_decisions(tmp_path, sbom),
            now=NOW,
        )


@pytest.mark.parametrize("identity", ["source", "sbom"])
def test_evidence_identity_mismatch_is_rejected(tmp_path: Path, identity: str) -> None:
    sbom = _sbom(tmp_path)
    scanner = _scanner(tmp_path, [])
    evidence = scan_module.create_vulnerability_evidence(
        scanner,
        sbom,
        SOURCE_SHA,
        now=NOW,
    )
    expected_source = "d" * 40 if identity == "source" else SOURCE_SHA
    expected_sbom = "e" * 64 if identity == "sbom" else evidence["sbom"]["sha256"]
    with pytest.raises(ValueError, match="identity"):
        scan_module.verify_vulnerability_evidence(
            evidence,
            expected_source_sha=expected_source,
            expected_sbom_sha256=expected_sbom,
        )


@pytest.mark.parametrize(
    "missing",
    [
        "matches",
        "descriptor",
        "scanner_version",
        "source_target",
        "database",
        "database_providers",
        "database_date",
        "database_checksum",
    ],
)
def test_missing_scanner_data_fails_closed(tmp_path: Path, missing: str) -> None:
    sbom = _sbom(tmp_path)
    scanner = _scanner(tmp_path, [])
    raw = json.loads(scanner.read_text(encoding="utf-8"))
    if missing == "matches":
        del raw["matches"]
    elif missing == "descriptor":
        del raw["descriptor"]
    elif missing == "scanner_version":
        raw["descriptor"]["version"] = "0.115.0"
    elif missing == "source_target":
        raw["source"]["target"] = str(tmp_path / "different.cdx.json")
    elif missing == "database":
        del raw["descriptor"]["db"]
    elif missing == "database_providers":
        raw["descriptor"]["db"]["providers"] = {}
    elif missing == "database_date":
        del raw["descriptor"]["db"]["status"]["built"]
    else:
        raw["descriptor"]["db"]["status"]["from"] = (
            "https://grype.anchore.io/databases/v6/vulnerability-db.tar.zst"
        )
    scanner.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError):
        scan_module.create_vulnerability_evidence(
            scanner,
            sbom,
            SOURCE_SHA,
            now=NOW,
        )


def test_grype_ignored_matches_outside_decision_contract_fail_closed(
    tmp_path: Path,
) -> None:
    sbom = _sbom(tmp_path)
    scanner = _scanner(tmp_path, [])
    raw = json.loads(scanner.read_text(encoding="utf-8"))
    raw["ignoredMatches"] = [{"vulnerability": {}, "artifact": {}}]
    scanner.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="outside the versioned decision contract"):
        scan_module.create_vulnerability_evidence(
            scanner,
            sbom,
            SOURCE_SHA,
            now=NOW,
        )


@pytest.mark.parametrize(
    ("built_at", "message"),
    [
        ("2026-07-19T19:59:59Z", "older than the release policy"),
        ("2026-07-22T20:10:01Z", "too far in the future"),
    ],
)
def test_database_date_outside_fixed_release_policy_fails_closed(
    tmp_path: Path,
    built_at: str,
    message: str,
) -> None:
    sbom = _sbom(tmp_path)
    scanner = _scanner(tmp_path, [])
    raw = json.loads(scanner.read_text(encoding="utf-8"))
    raw["descriptor"]["db"]["status"]["built"] = built_at
    scanner.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        scan_module.create_vulnerability_evidence(
            scanner,
            sbom,
            SOURCE_SHA,
            now=NOW,
        )


def test_database_age_evidence_tamper_is_rejected(tmp_path: Path) -> None:
    sbom = _sbom(tmp_path)
    evidence = scan_module.create_vulnerability_evidence(
        _scanner(tmp_path, []),
        sbom,
        SOURCE_SHA,
        now=NOW,
    )
    evidence["database"]["age_seconds"] += 1

    with pytest.raises(ValueError, match="age evidence does not match"):
        scan_module.verify_vulnerability_evidence(
            evidence,
            expected_source_sha=SOURCE_SHA,
            expected_sbom_sha256=evidence["sbom"]["sha256"],
        )


def _pixi_inventory_item() -> dict:
    return {
        "name": "example",
        "version": "1.0",
        "build": "py311_0",
        "build_number": 0,
        "kind": "conda",
        "source": "https://prefix.dev/conda-forge",
        "sha256": "f" * 64,
        "subdir": "osx-arm64",
        "file_name": "example-1.0-py311_0.conda",
        "url": "https://prefix.dev/conda-forge/osx-arm64/example-1.0-py311_0.conda",
    }


def test_structured_pixi_inventory_produces_scanner_ready_component(
    tmp_path: Path,
) -> None:
    inventory = tmp_path / "packages.json"
    inventory.write_text(json.dumps([_pixi_inventory_item()]), encoding="utf-8")

    components = evidence_module.packages(inventory)

    assert len(components) == 1
    component = components[0]
    assert component["purl"] == (
        "pkg:conda/example@1.0?build=py311_0&channel=conda-forge&subdir=osx-arm64&type=conda"
    )
    assert component["bom-ref"] == component["purl"]
    assert component["hashes"] == [{"alg": "SHA-256", "content": "f" * 64}]
    properties = {item["name"]: item["value"] for item in component["properties"]}
    assert properties["vibecad:package:archive-type"] == "conda"
    assert properties["vibecad:package:build"] == "py311_0"
    assert properties["vibecad:package:source"] == "https://prefix.dev/conda-forge"
    assert properties["vibecad:package:subdir"] == "osx-arm64"
    assert properties["vibecad:package:sha256"] == "f" * 64


def test_structured_inventory_excludes_only_the_local_application_package(
    tmp_path: Path,
) -> None:
    local_application = {
        "name": "freecad",
        "version": None,
        "kind": "conda",
        "source": ".",
        "url": ".",
        "requested_spec": ".",
    }
    inventory = tmp_path / "packages.json"
    inventory.write_text(
        json.dumps([local_application, _pixi_inventory_item()]),
        encoding="utf-8",
    )

    components = evidence_module.packages(inventory)

    assert [component["name"] for component in components] == ["example"]


@pytest.mark.parametrize("tamper", ["sha256", "source", "kind", "duplicate"])
def test_structured_pixi_inventory_fails_closed_on_weak_identity(
    tmp_path: Path,
    tamper: str,
) -> None:
    item = _pixi_inventory_item()
    values = [item]
    if tamper == "sha256":
        item["sha256"] = "not-a-sha256"
    elif tamper == "source":
        item["source"] = "conda-forge"
    elif tamper == "kind":
        item["kind"] = "unknown"
    else:
        values.append(dict(item))
    inventory = tmp_path / "packages.json"
    inventory.write_text(json.dumps(values), encoding="utf-8")

    with pytest.raises(ValueError):
        evidence_module.packages(inventory)


def test_legacy_text_inventory_omits_headers_and_is_not_scanner_ready(
    tmp_path: Path,
) -> None:
    inventory = tmp_path / "packages.txt"
    inventory.write_text(
        "LIST OF PACKAGES:\nName Version Build\npython 3.11.14 py311_0\n",
        encoding="utf-8",
    )

    components = evidence_module.packages(inventory)

    assert [component["name"] for component in components] == ["python"]
    assert "purl" not in components[0]


@pytest.mark.parametrize("tamper", ["empty", "purl", "namespace", "hash", "property"])
def test_vulnerability_scan_rejects_unusable_sbom_components(
    tmp_path: Path,
    tamper: str,
) -> None:
    sbom = _sbom(tmp_path)
    raw = json.loads(sbom.read_text(encoding="utf-8"))
    if tamper == "empty":
        raw["components"] = []
    elif tamper == "purl":
        raw["components"][0].pop("purl")
    elif tamper == "namespace":
        invalid = raw["components"][0]["purl"].replace(
            "pkg:conda/example@", "pkg:conda/conda-forge/example@"
        )
        raw["components"][0]["purl"] = invalid
        raw["components"][0]["bom-ref"] = invalid
    elif tamper == "hash":
        raw["components"][0]["hashes"][0]["content"] = "0"
    else:
        raw["components"][0]["properties"] = []
    sbom.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError):
        scan_module.create_vulnerability_evidence(
            _scanner(tmp_path, []),
            sbom,
            SOURCE_SHA,
            now=NOW,
        )


def test_macos_workflow_uses_pinned_verified_grype_after_sbom_generation() -> None:
    workflow = MACOS_WORKFLOW.read_text(encoding="utf-8")
    create_index = workflow.index("- name: Create and validate VibeCAD DMG")
    install_index = workflow.index("- name: Install verified Grype vulnerability scanner")
    scan_index = workflow.index("- name: Scan macOS SBOM for release vulnerabilities")
    verify_index = workflow.index("- name: Verify macOS release contents")

    assert create_index < install_index < scan_index < verify_index
    install_block = workflow[install_index:scan_index]
    assert 'GRYPE_VERSION: "0.116.0"' in install_block
    assert "python3 tools/verify_grype_archive.py" in install_block
    assert '--architecture "${TARGET_ARCH}"' in install_block
    assert "brew install grype" not in install_block
    assert "install.sh" not in install_block
    scan_block = workflow[scan_index:verify_index]
    assert 'grype "sbom:${sbom}" --output json' in scan_block
    assert 'grype-vulnerability-scan.json' in scan_block
    assert '--scanner-json "${scanner_report}"' in scan_block
    assert '--source-sha "${VIBECAD_SOURCE_SHA}"' in scan_block
    assert 'vibecad-vulnerability-evidence.json' in scan_block
    for secret in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "APPLE_APP_PASSWORD"):
        assert secret not in scan_block


def test_macos_bundle_generates_structured_package_inventory() -> None:
    script = (ROOT / "package" / "rattler-build" / "osx" / "create_bundle.sh").read_text(
        encoding="utf-8"
    )

    assert 'pixi list -e default --json > "${app_name}/Contents/packages.json"' in script
    assert '--packages "${app_name}/Contents/packages.json"' in script


def test_grype_archive_pins_cover_both_macos_architectures() -> None:
    arm64 = grype_pin_module.release_pin("arm64", "0.116.0")
    x86_64 = grype_pin_module.release_pin("x86_64", "0.116.0")

    assert arm64["filename"] == "grype_0.116.0_darwin_arm64.tar.gz"
    assert arm64["sha256"] == (
        "9425c225d0d63d2b384baf2177d3aba713a2bfb800235848ce70169e78c9c5fa"
    )
    assert x86_64["filename"] == "grype_0.116.0_darwin_amd64.tar.gz"
    assert x86_64["sha256"] == (
        "92dc64f7f1c71f92f610b250d801837c75a3c7336cb44656e59c4f1a07939163"
    )


def test_grype_archive_wrong_checksum_fails_closed(tmp_path: Path) -> None:
    archive = tmp_path / "grype_0.116.0_darwin_arm64.tar.gz"
    archive.write_bytes(b"not the pinned Grype archive")

    with pytest.raises(ValueError, match="does not match the repository pin"):
        grype_pin_module.verify_archive(archive, "arm64", "0.116.0")
