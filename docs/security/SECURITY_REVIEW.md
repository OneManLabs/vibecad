# Security Review

## Purpose

This review converts the threat model into release decisions. CAD documents,
project history, provider context, credentials, organization policy, update
metadata, and audit records are protected assets.

## Severity rule

- **Critical:** A practical path can expose credentials or proprietary CAD,
  execute untrusted code without an explicit boundary, bypass release trust, or
  corrupt accepted project data at broad scale.
- **High:** A practical path can bypass a managed policy, cross a project or
  organization boundary, replace accepted data, or perform an unauthorized
  export.
- **Medium:** Exploitation needs uncommon local access or has a bounded effect
  with a reliable recovery path.
- **Low:** The issue has small effect and does not cross a security or data-loss
  boundary.

An open critical or high item stops production. A medium item needs an owner,
mitigation, and review date. A low item can enter the normal backlog.

## Verified control evidence

- macOS credentials use Keychain. Headless reads are noninteractive, bounded,
  and redact failure output.
- Managed provider, model, endpoint, geometry, image, export, plugin, diagnostic,
  update, proxy, custom-CA, and local-only rules fail closed in focused tests.
- Native and scripted CAD mutations use one transactional acceptance boundary.
  Fault tests and a native GUI test preserve the prior accepted CAD and revision
  head when review or persistence fails.
- Worker requests use typed schemas, bounded time and resource rules, and
  isolated processes for candidate reopen validation.
- Updates bind name, size, SHA-256, source identity, SBOM, provenance, and signed
  metadata. VibeCAD does not silently install an update.
- Audit reports are redacted, content-bound, signed, and retained locally when
  remote collection fails.

The current representative security and release group has 125 passing tests.
This result is control evidence. It is not a complete vulnerability assessment.

## Release vulnerability gate

The macOS release job now creates the dependency input from `pixi list --json`.
The SBOM generator rejects malformed structured package data. Each resolved
conda dependency has a canonical package URL, build, channel, subdirectory,
archive type, source URL, and SHA-256. The local FreeCAD path package is the
application itself, so the CycloneDX application component represents it.

The release job uses Anchore Grype 0.116.0. The repository pins the official
Apple Silicon and Intel archive SHA-256 values. The job checks the archive
against the repository pin before it extracts or runs the scanner. It does not
use an unverified install script. It updates the vulnerability database and
then scans the generated CycloneDX 1.5 SBOM.

The versioned `vibecad-vulnerability-scan-evidence-v1` record binds these
items:

- Source commit.
- SBOM filename, SHA-256, serial number, application version, package count,
  and package-identity digest.
- Grype version and raw report SHA-256.
- Grype SBOM input identity.
- Database schema, build date, source URL checksum, and provider count.
- Each finding, severity, package identity, fix state, and release decision.

An unresolved critical or high finding stops the release job. Grype ignored
matches also stop the job because they are outside the VibeCAD decision
contract. A VibeCAD ignore decision must use the versioned decision schema. It
must match the source commit, SBOM SHA-256, vulnerability, package name, and
package version. It must also have a rationale, owner, reviewer, and future
expiry time. An expired, unused, duplicate, or mismatched decision fails.

The database must be no more than 72 hours old at evaluation time. A database
build time can be no more than 10 minutes in the future to allow a small clock
difference. The evidence records this fixed policy and the computed age. A
stale database, a future-dated database, or changed age evidence fails.

The focused vulnerability and source-scan group has 42 passing tests. It
includes missing-data, identity-tamper, expired-decision, unresolved-high,
unresolved-critical, scanner-pin, wrong-checksum, stale-database,
future-database, and unusable-SBOM tests.

A local compatibility probe used the repository Pixi environment on 2026-07-22.
The base source commit was
`b9aed54197199a6c9c3af304b2f5eb75d02b6738`, with current working changes. The
probe generated 282 dependency components. Grype ingested all 282 conda package
URLs and reported no package URL parse error. Grype 0.116.0 used database schema
v6.1.9, built at 2026-07-22T07:06:24Z, with source checksum
`sha256:8496f58655ba6b5d1ed133e8591629d729a53021e7f1b20063b0577ca7c0f02f`.
The local report had no matches. This probe proves scanner compatibility. It is
not the macOS release CI result and does not close SR-001.

The high-confidence tracked-source scan also passed locally for the same base
commit. It read 14,491 tracked files and 442,527,509 bytes. It reported no
finding and no read error. This result does not cover full Git history,
untracked working changes, generated logs, or release artifacts. It does not
close SR-002.

## Open review register

| ID | Area | Status | Release effect | Required evidence |
|---|---|---|---|---|
| SR-001 | Dependency vulnerabilities | Gate implemented; macOS CI evidence pending | Stops the no-high-risk claim | Run the macOS release job for the exact candidate. Review the raw Grype report and the versioned evidence. Record every finding and decision. Extend coverage to source, lock files, and built application where the SBOM does not give enough coverage. |
| SR-002 | Committed and generated secrets | Partial; tracked-source gate implemented | Stops the no-credential-exposure claim | Run the gate after all release changes are committed. Scan full Git history in scope, generated logs, and release artifacts with a documented high-confidence ruleset. |
| SR-003 | Malicious CAD and import files | Partial | Stops broad untrusted-file assurance | Add malformed and hostile fixtures, process limits, parser crash isolation, and path-boundary tests for important import types. |
| SR-004 | Prompt injection in metadata and images | Partial | Stops broad remote-context assurance | Prove that imported instructions are untrusted data, cannot change policy, and cannot expand tools or outbound context. |
| SR-005 | Plugin and generated-code execution | Partial | Stops general third-party-plugin assurance | Prove extension discovery denial, worker file and network boundaries, timeout, memory limit, and cleanup for hostile fixtures. |
| SR-006 | Production update chain | Externally blocked | Stops production distribution | Provide Developer ID, notarization, stapling, Gatekeeper, and signed update-manifest evidence for the exact artifacts. |

No item in this table has an assigned final severity until its required scan or
test is complete. Therefore, this review does not yet state that no critical or
high issue remains.

## Review output contract

The final review must record the source commit, artifact SHA-256 values, scanner
names and versions, ruleset or database dates, test commands, findings, risk
owner, decision, mitigation, expiry date, and reviewer. Scanner unavailability is
not a passing result.
