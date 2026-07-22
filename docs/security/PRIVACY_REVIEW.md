# Privacy Review

## Data flow

Local CAD files, design briefs, revisions, conversations, generated source, and
audit records stay usable without a hosted service. An online request can send
only the bounded context that the selected provider operation needs. Managed
policy can prohibit all provider traffic or separately prohibit geometry, images,
diagnostics, plugins, and external exports.

The user must see the selected provider and whether a request can leave the Mac
before the first request. Credentials stay in Keychain. Audit records use a
privacy-safe actor identity and must not contain API keys, full sensitive prompts,
or proprietary geometry.

## Required release evidence

- Enumerate each outbound host and the component that can contact it.
- Prove local-only mode with network denial tests.
- Prove that geometry, images, and diagnostics stay local when policy denies them.
- Prove that provider context is project-scoped and cannot include another
  project's prompt, image, selection, geometry, or revision.
- Prove that cancellation and provider failure do not add sensitive data to logs.
- Record conversation, revision, audit, crash, diagnostic, and identity retention
  behavior.
- Record the consent path for diagnostic or crash upload.
- Inspect the installed application and release logs for undeclared telemetry.

## Current status

Focused policy and audit tests cover local-only mode, context removal, endpoint
allowlists, diagnostic-upload denial, redaction, and retained local evidence.
There is no final installed-application network inventory or complete consent and
retention review. The production privacy review is not passed.

## Release vulnerability scan data

The release vulnerability scan runs on the macOS build runner. It sends no CAD
file, prompt, conversation, credential, user identity, or project metadata to
Grype. Its scan input is the generated package SBOM. Grype contacts its official
database service to update vulnerability data. The build runner downloads the
repository-pinned Grype archive from the official GitHub release.

The raw Grype JSON and the normalized VibeCAD evidence stay in the release
evidence artifact. The normalized record keeps the SBOM filename, not its build
runner directory. The raw scanner report can contain the build runner path to
the SBOM. Release evidence access and retention must follow the release artifact
policy. This release-only network flow does not add telemetry to the installed
VibeCAD application.
