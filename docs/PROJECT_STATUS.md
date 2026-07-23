# Project Status

Last update: 2026-07-22. Active work: Phase 1 packaging, Phase 4 live benchmark
hardening, Phase 7 release hardening, and Phase 8 portability evidence.

## P4-005 first live CAD-tool checkpoint: 2026-07-22

Milestone: P4-005 live Tier 1 benchmark execution.

Implemented: The exact-source attempt reached ChatGPT for all seven cases and
executed native CAD tools. Six cases then stopped because a valid
`tool_call_completed` progress event was larger than the 8,192-byte evidence
limit. Oversized progress events now retain the event name, tool name, result
state, exact byte count, and SHA-256 without retaining the large payload. This
is bounded evidence, not a dropped event. The export case produced a valid,
reopenable STL and one accepted provenance revision; its expected revision
contract now reflects that persistent design-brief update.

The user's Raspberry Pi enclosure attempt also exposed a GUI lifecycle defect.
The main request was submitted as steering while an accidental `c` run was
active. Stop cancelled that run, but stale question answers were later accepted
by its waiter. Question waiters are now bound to their run cancellation state,
finish only once, and reject answers after Stop.

Files changed: Live evidence monitor, live export acceptance checks, assistant
question lifecycle, focused tests, and project records.

Tests added: Oversized progress events become content-bound summaries without a
limit failure. A stopped question round cannot accept stale answers.

Tests run: The combined focused group passed 126 tests.

Results: All seven live cases made one real ChatGPT request. The cases executed
between 4 and 14 tool calls. The export produced a valid 20 mm STL. The attempt
became unscorable because the worktree changed during execution while this
user-visible lifecycle defect was fixed. No rate is claimed.

Benchmark impact: The provider and native tool path now runs. A new clean
exact-source attempt is required.

Known issues: The current developer process must restart to load the question
lifecycle fix.

Next milestone: Commit the fixes, rebuild the installed scripts, and rerun all
seven live cases from the exact clean commit.

External blockers: None.

## P4-005 controlled-surface wire checkpoint: 2026-07-22

Milestone: P4-005 live Tier 1 benchmark execution.

Implemented: The reduced-scope attempt failed before every provider request.
The session had approved and frozen a complete Part Design-to-Sketcher
transition, but the ChatGPT wire validator validated the names as a normal
single-workbench surface. The frozen contract now carries one explicit Boolean
transition declaration. The wire validator checks that declaration and applies
the same narrow union rule. The benchmark fixture now finds the rectangle width
dimension by its actual `Distance` type. Partial checkpoints now contain only
the three public fixture fields in their strict contract.

Files changed: Modeling-surface validation, session surface snapshots, ChatGPT
wire validation, live Tier 1 fixture and checkpoint handling, focused tests, and
project records.

Tests added: The ChatGPT dynamic-tool surface accepts a complete controlled
Part Design-to-Sketcher transition and retains the two provider namespaces.

Tests run: The focused ChatGPT, session, live-runner, and live-usage group passed
119 tests.

Results: The failed retained attempt sent zero provider requests and accepted no
CAD change. The three harness defects are corrected in the tested source.

Benchmark impact: No live-model pass rate is claimed.

Known issues: A new exact-source seven-case attempt is required.

Next milestone: Commit this checkpoint to OneManLabs and run the exact-source
live Tier 1 benchmark.

External blockers: None.

## Completed

- Read repository agent rules, root product documentation, contribution rules, privacy and security files, build entry point, VibeCAD module inventory, macOS workflow inventory, provider architecture, project persistence, intent memory, transaction code, worker code, and validation surfaces.
- Confirmed a clean `main` worktree at `b489a420` before changes.
- Completed the Apple Silicon baseline build with the repository environment.
- Fixed the macOS build entry point so it does not require Linux `nproc`.
- Fixed provider dependency installation so it uses the Python version that FreeCAD embeds.
- Added a portability self-test for the build entry point.
- Created the required Phase 0 living documents and machine-readable backlog.
- Added a versioned, content-bound AI revision record and append-only project revision store.
- Added the `vibecad-accepted-revision-artifact-v1` contract. Each accepted record now binds the reopenable CAD file and the accepted project-state snapshot by SHA-256.
- Added crash-safe revision restore, integrity checks, revision comparison, and project timeline service APIs.
- Connected interrupted-acceptance recovery to the FreeCAD restored-document event. A project now recovers before the next provider turn is required.
- Added a compact beginner-facing revision history to the assistant workspace. Users can refresh history, compare two accepted records, and restore one verified revision without losing later history.
- Added parent-chain, project-isolation, corruption, tamper, and guarded-restore tests.
- Changed native transactions so failed validation, recompute, and operation candidates abort instead of remaining in the accepted document.
- Added the `vibecad-rollback-artifact-v1` contract and crash-safe acceptance journal.
- Added staged revision records, atomic candidate CAD promotion, guarded head promotion, metadata rollback, whole-turn rejection, and interrupted-journal recovery.
- Split candidate review from promotion. `VibeCADAcceptanceCoordinator.validate_candidate(...)` now creates a durable pending candidate, and `accept_validated_candidate(...)` promotes that exact candidate. `promote(...)` remains as the automatic-acceptance compatibility path.
- Added the `vibecad-validated-candidate-v1` contract. It binds the validated CAD copy, the candidate project snapshot, the prior accepted CAD and project state, and the prior accepted head. Validation does not change the canonical CAD file, accepted head, project metadata, or accepted project state.
- Added a byte-exact canonical CAD backup to each new rollback artifact. FreeCAD can change FCStd ZIP bytes during `saveCopy()`. Reject, failed promotion, restore failure, and startup recovery now keep an unchanged canonical file in place or restore the verified byte-exact backup. Existing rollback artifacts can still use their prior FreeCAD rollback copy.
- Made project-context reads non-mutating for an existing version-2 manifest. Review refreshes no longer change `updated_at` or any accepted project metadata. A missing manifest is created once, and a legacy manifest is written only when migration is required.
- Added human and automatic acceptance provenance. Human rejection does not create an accepted revision. Reject and Stop during review restore the prior accepted CAD and project state.
- Added the stable assistant run states `Understanding`, `Inspecting design`, `Planning`, `Creating preview`, `Validating`, `Applying revision`, and `Complete`.
- Added accessible `Accept revision` and `Reject preview` controls. The GUI supplies the candidate decision callback. Headless callers can select the explicit automatic-acceptance path.
- Added real FreeCAD save, close, reopen, restore, recompute, geometry validity, and document comparison integration coverage.
- Routed successful native and scripted provider mutations through one acceptance coordinator.
- Added a project-side rollback snapshot for scripted source and generated artifacts.
- Added atomic accepted-revision metadata and provider-turn recovery of interrupted journals.
- Restored the missing Mesh OBJ test fixture that the general object-file ignore rule hid.
- Added the content-bound `vibecad-design-brief-v1` schema with atomic writes and loss-preserving migration from active intent-memory statements.
- Added bounded design-brief provider context and the typed `core.update_design_brief` tool.
- Required each accepted CAD mutation to include a successful design-brief update.
- Bound each revision record to the accepted design-brief content revision.
- Added persistent migration records and exact backups of legacy intent-memory data.
- Added the official FreeCAD `upstream` remote and fetched `upstream/main` at `2dc56d2e`.
- Proved that the fork has no common Git ancestor with official upstream because it imported FreeCAD as root snapshot `80fa22b1`.
- Added a machine-readable 1,014-file post-import patch inventory and a safe reconstruction-based upstream sync method.
- Added unsigned and Developer ID signed PKG construction for silent `/Applications` installation.
- Added PKG notarization and stapling, production CI keychain setup, and pre-upload signature gates.
- Added SHA-256, CycloneDX 1.5 SBOM, and in-toto SLSA provenance generation for macOS artifacts.
- Added versioned update metadata, detached production signing, and signature verification.
- Added an end-to-end macOS release verifier for DMG mount, bundle identity, code signature, launcher smoke, PKG payload, SBOM, and provenance.
- Added a secure update boundary with signed-manifest verification, channel and host allowlists, allowlist-constrained HTTPS redirects, size limits, and artifact SHA-256 enforcement.
- Added a standard Help-menu update check. It verifies pinned signed metadata in a worker, applies managed channel and host rules, and never downloads or installs software.
- Embedded the update public key, channel, and metadata endpoints inside production apps before code signing. The production release verifier now rejects missing or invalid update trust data.
- Added enterprise PKG deployment, rollback, evidence-retention, and data-preserving uninstall instructions.
- Fixed the application `CFBundleIdentifier` at `com.vibecad.desktop`. The app bundle, PKG receipt, verifier, and clean-install gate now use the same identifier.
- Added cleanup after package-cache restore. The workflow removes restored local package environments and stale build products before it builds a release package.
- Added an exact source-identity gate for all 256 VibeCAD Python files. The gate compares the source and installed package environment by relative path and SHA-256 value.
- Added a clean-machine workflow gate for the exact generated PKG. It installs `/Applications/VibeCAD.app`, runs one real automatic `VibeCADSession` transaction that creates a native Body, fully constrained Sketch, and Pad, accepts exactly one revision, saves, closes, reopens, and verifies STEP and STL round trips. Cleanup can remove only the exact application path and requires an ownership marker from the same job.
- Added a versioned macOS managed-configuration schema and policy loader.
- Enforced organization provider, model, endpoint, and local-only rules before online provider creation.
- Added one fail-closed managed data-action policy service for geometry, images, exports, external AI skills, and diagnostic uploads.
- Redacted geometry, selections, design parameters, screenshots, and reference images at the final remote-provider context boundary when policy denies them.
- Removed denied tools from the remote provider schema and repeated the policy check before tool execution.
- Kept full geometry and image capability for local/offline providers.
- Enforced export policy before the scripted-model file dialog and disabled external Codex skills when managed policy denies plugins.
- Added the content-bound `vibecad-audit-event-v1` contract and append-only per-project audit store.
- Added automatic redaction for credentials, secrets, tokens, prompts, geometry payloads, and image payloads.
- Added audit events for accepted and reverted AI revisions, revision restores, remote-context filtering, policy tool denials, scripted export attempts and results, and update checks.
- Excluded audit evidence from model rollback snapshots so recovery cannot erase security history.
- Added the provider-neutral `vibecad-principal-v1` identity contract with privacy-safe actor hashes.
- Added the required organization owner, administrator, CAD manager, designer, reviewer, and viewer roles with explicit permission sets.
- Enforced RBAC before AI use, CAD tool mutations, revision restore, and scripted export.
- Added managed organization, subject, and role fields to the macOS configuration-profile schema.
- Added strict managed OIDC discovery, JWKS, ID-token, role-mapping, and Keychain session contracts.
- Limited OIDC signatures to RS256 and ES256. Added issuer, audience, authorized-party, subject, nonce, expiry, issued-time, and not-before checks.
- Added fail-safe viewer mapping for unknown organization roles and enforced principal session expiry at each RBAC decision.
- Added a bounded one-hour OIDC discovery and JWKS cache.
- Added the public-client authorization-code and PKCE flow with a five-minute local loopback callback, strict state and nonce checks, and system-browser launch from a worker.
- Added the `Sign In to Organization...` application command for active managed OIDC policy.
- Added the versioned `vibecad-oidc-session-v1` Keychain record, refresh-token rotation, controlled `offline_access` scope, and explicit organization sign-out.
- Enforced managed export policy and export RBAC in the shared FreeCAD GUI export command before the file dialog and again before the selected export module runs.
- Enforced external-plugin policy before the Addon Manager starts network access. The idempotent runtime guard is owned by VibeCAD, so a clean recursive submodule checkout includes the control without an Addon Manager fork patch.
- Enforced external-plugin policy during application startup before user modules, additional module paths, legacy module paths, added Python packages, or extension packages enter discovery.
- Removed preexisting user-controlled module paths from the pending Python search path when managed policy disables external plugins.
- Added strict SAML 2.0 signed-assertion validation with a pinned identity-provider certificate.
- Limited SAML assertion signatures to RSA-SHA256 or ECDSA-SHA256 and the digest to SHA-256.
- Added audience, destination, issuer, request, relay-state, recipient, time, duplicate-ID, XML-wrapping, and subject checks.
- Added a system-browser SAML Redirect/POST flow with a fixed loopback assertion consumer, bounded request body, and Keychain session storage.
- Added the content-bound `vibecad-organization-membership-v1` record with atomic promotion, privacy-safe actor identity, role updates, and local reopening.
- Provisioned or updated the local organization membership after each validated managed principal resolution.
- Changed denial auditing so a missing or expired identity session writes an unresolved, privacy-safe actor event instead of losing the event.
- Added the content-bound `vibecad-audit-archive-v1` contract and archive chain.
- Added managed live-event retention by age and count. Retention promotes and verifies an archive before it removes individual event files.
- Made archive reads deduplicate live files that remain after interrupted cleanup without losing evidence.
- Added the signed `vibecad-audit-report-v1` export with Ed25519, a Keychain-held private key, public-key fingerprint, atomic write, portable verification, and optional external key pinning.
- Added a guarded Tools-menu action that exports signed audit reports off the UI thread.
- Added signed artifact byte size to the update manifest and release verifier.
- Changed update download promotion to use a unique temporary file and an exclusive atomic link so a concurrent file cannot be overwritten.
- Added exact signed-size, SHA-256, HTTPS host, redirect, and destination-name checks for downloaded packages.
- Added offline macOS package checks with `hdiutil verify`, Gatekeeper assessment, and stapled-ticket validation before handoff.
- Added two explicit user decisions: download and reveal in Finder. The app uses `open -R` only after approval and does not open or install the package.
- Removed a newly downloaded artifact if macOS package verification fails.
- Added an import-time export guard for every module registered through FreeCAD export types.
- Wrapped already loaded and late-loaded workbench `export` functions with managed export policy and RBAC.
- Refreshed export handler discovery after all application modules load and again after GUI initialization.

## Baseline results

- The unmodified build command failed before CMake: `nproc: command not found`.
- With `VIBECAD_BUILD_JOBS=8`, CMake configured with Clang 18.1.8, Qt 6.8.3, Python 3.11.14, OpenCASCADE 7.8.1, and `arm64`.
- CMake scheduled 4,012 initial build actions. The final incremental build and provider dependency smoke check passed.
- CMake reported missing optional LZMA, freetype package metadata, and Spnav during discovery. It later found Freetype 2.14.1.
- CMake selected deployment target 26.4 from the SDK in this local path. This target is not suitable as a general product minimum.
- The build tree is 1.2 GB. The small launcher files are 164 KB for `FreeCAD` and 144 KB for `FreeCADCmd`; shared modules hold most code.
- A command-line startup and VibeCAD import smoke test took 0.28 seconds.
- GUI behavior, installed bundle size, cold and warm GUI launch, and crash or warning counts are pending.

## Current architecture disposition

- Preserve: FreeCAD document and workbench capability, typed tool registry, provider abstraction, isolated scripted workers, atomic project writes, intent memory, conversation migration, and macOS CI foundation.
- Refactor: modeling strategy selection, structured design brief, native transaction acceptance, provider policy, revision records, beginner workspace, and platform adapters.
- Replace: manual engine selection as the default beginner workflow and incomplete revision provenance.
- Remove later with migration: obsolete VibeCAD-only paths that duplicate the selected architecture. No removal occurs before compatibility analysis.

## Tests run

- `python3 tools/build_vibecad_selftest.py`: passed.
- `bash -n tools/build_vibecad.sh`: passed.
- `./tools/build_vibecad.sh --incremental`: passed. It reports VibeCAD 26.3.2 and successful provider imports.
- `.pixi/envs/default/bin/python -m pytest src/Mod/VibeCAD/vibecad_tests -q`: 418 passed and 5 skipped in 4.72 seconds after revision-store implementation.
- Revision-store focused tests: 8 passed.
- `cmake --build build/release --target VibeCADScripts --parallel 8`: passed.
- Built-runtime `import VibeCADRevision`: passed.
- `build/release/bin/FreeCADCmd` VibeCAD import smoke: passed in 0.28 seconds.
- Initial `ctest --test-dir build/release -N`: failed during MeshPart discovery. Direct discovery passed in 2.01 seconds. An explicit 30-second CAD test discovery budget fixed the failure.
- Final `ctest --test-dir build/release -N`: passed and registered 1,687 tests.
- Representative CTest groups: 9 of 9 passed. Coverage included branding, MeshPart, Part Design pad, Sketcher document creation, and Spreadsheet property rules.
- Acceptance and transaction focused tests: 21 passed.
- Full VibeCAD Python suite: 432 passed and 5 skipped in 5.20 seconds.
- Real FreeCAD acceptance integration: passed. It saved, closed, reopened, recomputed, compared, injected a post-head failure, restored, and reopened the prior valid shape.
- Full VibeCAD Python suite: 434 passed and 5 skipped in 5.56 seconds.
- Acceptance, transaction, and revision focused tests: 24 passed.
- Complete CTest rerun: 1,687 registered; 1,680 enabled; zero failures in 87.03 seconds. Three enabled tests skipped: two backup deletion tests and one imperial schema test. Seven upstream tests are disabled: two backup format tests, four document-observer tests, and one expression parser test.
- Full VibeCAD Python suite after design-brief work: 437 passed and 5 skipped in 6.04 seconds.
- Full VibeCAD Python suite after provider and acceptance integration: 443 passed and 5 skipped in 5.33 seconds.
- Upstream patch inventory validation and Python compilation: passed.
- Release evidence self-test: passed.
- Update-manifest RSA signing and detached-signature verification self-test: passed.
- Synthetic DMG, ad-hoc app, PKG, launcher, SBOM, and provenance release smoke test: passed.
- Full VibeCAD Python suite after update security work: 445 passed and 5 skipped in 6.08 seconds.
- Managed policy and signed-update security tests: 5 passed.
- Managed configuration JSON Schema validation: passed.
- Full VibeCAD Python suite after managed-policy integration: 448 passed and 5 skipped in 5.75 seconds.
- macOS workflow YAML, Bash, and Zsh syntax checks: passed.
- Unsigned PKG construction and payload inspection with a synthetic app: passed.
- Acceptance, restore, interruption, and revision comparison focused tests: 33 passed in 0.74 seconds.
- Real FreeCAD acceptance integration: passed. It accepted two document states, saved, closed, reopened, recomputed, compared revisions, restored the first state, and recovered from an injected post-head provenance failure.
- Full VibeCAD Python suite after accepted-artifact and restore work: 458 passed and 5 skipped in 8.49 seconds.
- Representative persistence CTest group: 54 enabled tests passed in 10.01 seconds. Four registered `DocumentObserverTest` cases are disabled.
- Complete CTest rerun: 1,687 registered; 1,680 enabled; zero failures in 127.71 seconds. The skipped tests are `BackupPolicyTest.StandardWithZeroFilesDeletesExisting`, `BackupPolicyTest.TimestampWithZeroFilesDeletesExisting`, and `SchemaTest.imperial_building_special_function_length`. The disabled tests are `BackupPolicyTest.TimestampWithInvalidFormatStringThrows`, `BackupPolicyTest.TimestampWithAbsurdlyLongFormatStringThrows`, `DocumentObserverTest.hasSubObject`, `DocumentObserverTest.hasSubElement`, `DocumentObserverTest.normalize`, `DocumentObserverTest.normalized`, and `ExpressionParserTest.expressionsParseAsPyObjectWrapper`.
- Restored-document recovery observer tests: 3 passed and 99 deselected in 0.68 seconds.
- Full VibeCAD Python suite after document-open recovery: 459 passed and 5 skipped in 6.72 seconds.
- Revision workspace and transactional service tests: 40 passed and 98 deselected in 1.60 seconds.
- Full VibeCAD Python suite after revision workspace integration: 462 passed and 5 skipped in 7.91 seconds.
- Signed update UI, trust configuration, policy, and redirect tests: 9 passed in 0.70 seconds.
- Release evidence trust self-test: passed, including verification with the public key embedded in a synthetic app.
- Full VibeCAD Python suite after verified update UI: 466 passed and 5 skipped in 13.00 seconds.
- Synthetic DMG and PKG release smoke after update trust changes: passed.
- Managed outbound context and action tests: 35 passed and 30 deselected in 0.63 seconds.
- Full VibeCAD Python suite after managed data-boundary enforcement: 472 passed and 5 skipped in 8.24 seconds.
- Audit, acceptance, policy, and persistence focused tests: 41 passed in 0.62 seconds.
- Full VibeCAD Python suite after enterprise audit integration: 478 passed and 5 skipped in 9.00 seconds.
- RBAC role-matrix and no-side-effect denial tests: 29 passed in 0.17 seconds.
- Full VibeCAD Python suite after RBAC integration: 490 passed and 5 skipped in 5.27 seconds.
- Built-runtime `VibeCADIdentity` import: passed after the `VibeCADScripts` target copied the module.
- OIDC, RBAC, and managed-policy focused tests: 30 passed in 2.87 seconds.
- Full VibeCAD Python suite after OIDC validation: 503 passed and 5 skipped in 11.20 seconds.
- OIDC token, cache, PKCE, callback, Keychain, and loopback sign-in tests: 17 passed in 1.10 seconds.
- Full VibeCAD Python suite after interactive OIDC integration: 507 passed and 5 skipped in 12.35 seconds.
- `VibeCADScripts` build and built-runtime `VibeCADFederatedIdentity` import: passed.
- OIDC session renewal and managed-scope focused tests: 26 passed in 1.41 seconds.
- Full VibeCAD Python suite after OIDC renewal and sign-out: 509 passed and 5 skipped in 7.91 seconds.
- Managed configuration Draft 2020-12 schema validation: passed.
- Shared FreeCAD export and extension policy tests: 12 passed in 0.18 seconds.
- Incremental `FreeCADGui` build after shared export changes: passed.
- Application CTest group after shared export changes: 6 passed in 2.06 seconds.
- Full VibeCAD Python suite after shared export and Addon Manager enforcement: 512 passed and 5 skipped in 10.38 seconds.
- Incremental `FreeCAD` build and command-line managed-policy import smoke after startup discovery enforcement: passed.
- Signed SAML assertion, request, policy, and Keychain focused tests: 17 passed in 0.84 seconds.
- Full VibeCAD Python suite after SAML integration: 524 passed and 5 skipped in 7.67 seconds.
- Built-runtime `VibeCADSAMLIdentity` import: passed.
- SAML, organization provisioning, and unresolved-identity audit tests: 18 passed in 2.23 seconds.
- Full VibeCAD Python suite after organization provisioning: 528 passed and 5 skipped in 11.01 seconds.
- Built-runtime `VibeCADOrganization` import: passed.
- Audit archive promotion, retention, and interruption tests: 8 passed in 0.54 seconds.
- Audit retention, report signing, Keychain, policy, and atomic export tests: 23 passed in 0.46 seconds.
- Full VibeCAD Python suite after audit governance: 539 passed and 5 skipped in 13.34 seconds.
- Built-runtime `VibeCADAuditReport` import: passed.
- Verified update download, atomic promotion, package check, and consent tests: 10 passed in 0.23 seconds.
- Release evidence manifest-signing self-test after size binding: passed.
- Full VibeCAD Python suite after verified update handoff: 543 passed and 5 skipped in 7.77 seconds.
- Synthetic DMG, PKG, release evidence, and release verifier smoke after update handoff: passed.
- `VibeCADScripts` build after update UI integration: passed.
- Direct workbench export-guard unit and shared-boundary tests: 9 passed in 0.13 seconds.
- Incremental `FreeCAD` build after final module-order refresh: passed.
- Real FreeCAD runtime reported the native `Part.export` function as guarded.
- Real FreeCAD managed-denial integration blocked `Part.export` and created no output file.
- Full VibeCAD Python suite after direct export enforcement: 548 passed and 5 skipped in 13.30 seconds.
- SCIM schema, Keychain, allowlist, redirect, role-override, inactive-user, and atomic-cache focused tests: 16 passed in 0.10 seconds.
- Full VibeCAD Python suite after SCIM role provisioning: 553 passed and 5 skipped in 10.79 seconds.
- `VibeCADScripts` build and built-runtime `VibeCADSCIM` import: passed.
- Signed policy bundle signature, time, organization, rollback, equivocation, redirect, fail-closed cache, and atomic-promotion tests: 9 passed.
- Full VibeCAD Python suite after signed central policy integration: 562 passed and 5 skipped.
- `VibeCADScripts` build and built-runtime `VibeCADPolicyBundle` import: passed.
- Managed proxy, direct-network, content-pinned custom CA, SDK-client, identity, SCIM, policy, and update focused tests: 83 passed in 1.30 seconds.
- Full VibeCAD Python suite after shared managed network integration: 571 passed and 5 skipped in 6.95 seconds.
- Central audit receipt, upload idempotency, Keychain credential, local-retention, signer-pin, and policy focused tests: 27 passed in 0.25 seconds.
- Full VibeCAD Python suite after central audit collection and signer pinning: 575 passed and 5 skipped in 7.37 seconds.
- Automatic strategy routing, structure preservation, professional native selection, functional-part selection, advanced-lock, and bounded-context tests: 145 passed in 0.62 seconds.
- Full VibeCAD Python suite after automatic modeling strategy integration: 580 passed and 5 skipped in 7.23 seconds.
- Built-runtime `VibeCADCapabilityRouter`, `VibeCADNetwork`, and `VibeCADAuditCollector` imports: passed.
- Representative Application CTest group after router persistence: 64 passed in 2.29 seconds.
- Full VibeCAD Python suite after persistent route metadata: 580 passed and 5 skipped in 6.70 seconds.
- Deterministic offline Tier 1 CAD capability baseline: 7 of 7 passed. Each case created valid geometry, saved FCStd, closed, reopened, recomputed, and revalidated. The export case also produced STL. This is not a provider-driven conversational score.
- Deterministic provider transactional Tier 1 baseline: 3 of 3 independent three-turn trials passed. Each trial created an exact 40 by 30 by 20 mm editable Part Design model, used an explicit selected face to add a centered 6 mm through-hole, and used an explicit selected feature to add a 2 mm fillet. It updated the design brief, accepted three revisions, saved, closed, reopened, recomputed, and revalidated the final model. Each trial used 15 tool calls.
- Selection context and acceptance focused regression after the two-turn benchmark: 32 passed in 0.14 seconds.
- Transactional provider focused regression: 85 passed in 2.09 seconds.
- Full VibeCAD Python suite after transactional provider benchmark integration: 582 passed and 5 skipped in 7.14 seconds.
- `VibeCADScripts` build passed. The built application contains `VibeCADDocumentValidator` and `VibeCADSaveBoundary`.
- Beginner onboarding contract: 5 tests passed. Seven intent-based start choices contain no workbench or modeling-engine names. Versioned completion state uses atomic replacement. The built application contains `VibeCADOnboarding`.
- Beginner first-launch integration: 8 onboarding tests pass, including failed-workspace fault injection. The native offscreen FreeCAD GUI test created the dialog, found seven accessible intent choices, and verified provider/privacy status. The full VibeCAD Python suite passed 590 tests with 5 skips.
- First-launch keyboard and appearance gate: 8 focused tests and the native FreeCAD GUI test passed. All seven choices use strong keyboard focus, explicit tab order, accessible names, and native palette colors.
- Transactional provider baseline with hollow enclosure: 3 of 3 series trials passed. Each trial used 23 typed tool calls, accepted four revisions across two documents, validated an exact 2 mm open-top shell, and reopened both final CAD states successfully.
- Transactional provider dimension and export extension: 3 of 3 series trials passed. The provider changed the original sketch width from 40 mm to 55 mm, preserved centering, recomputed the downstream pocket and fillet, and created a non-overwriting STL project artifact through the typed export boundary.
- Typed project export and surface guardrails: 52 focused tests passed. The final full VibeCAD Python suite passed 597 tests with 5 skips.
- Complete deterministic-provider Tier 1 transaction baseline: all seven prompt classes passed in 3 of 3 repeated series trials. Each trial used 42 typed calls, accepted six CAD revisions, reopened three native documents, and verified one non-overwriting STL artifact. The mirror case created a source pocket and native mirrored feature with exact expected volume.
- Initial Tier 2 functional-part baseline: the native wall-bracket case passed 3 of 3 repeated provider transaction trials. Each trial used 13 typed calls to create a constrained L-profile, pad it, add two through mounting holes, accept one revision, and reopen the exact valid geometry.
- Tier 2 motor-adapter case: passed 3 of 3 repeated provider transaction trials. It creates an 80 mm disc, a 20 mm shaft bore, and four equal 5 mm holes on a 60 mm pitch circle. The two-case suite accepts two revisions and reopens two valid parametric documents per trial.
- Tier 2 battery-tray case: passed 3 of 3 repeated provider transaction trials. It creates a 100 by 60 by 20 mm open tray with 2.5 mm walls and four 4 mm through mounts. The three-case suite uses 39 typed calls, accepts three revisions, and reopens three valid parametric documents per trial.
- Tier 2 camera-mount case: passed 3 of 3 repeated provider transaction trials. It creates a 70 by 50 by 6 mm plate with one 6.5 mm center hole and two constrained 20 by 6 mm adjustment slots. The four-case suite uses 53 typed calls, accepts four revisions, and reopens four valid parametric documents per trial.
- Tier 2 pipe-clamp case: passed 3 of 3 repeated provider transaction trials. It creates a 40 mm bore, 60 mm outside ring, 4 mm radial split, and two 5 mm through mounts. The five-case suite uses 68 typed calls, accepts five revisions, and reopens five valid parametric documents per trial.
- Tier 2 ventilated-cover case: passed 3 of 3 repeated provider transaction trials. It creates an 80 by 50 by 3 mm cover with five constrained through-slots. The six-case suite accepts six revisions and reopens six valid parametric documents per trial.
- Tier 2 electronics-enclosure case: passed 3 of 3 repeated provider transaction trials. It creates a 120 by 80 by 35 mm open housing with 2.5 mm walls and a separate 3 mm lid body with four constrained 3.2 mm M3 clearance holes. The seven-case suite uses 103 typed calls, accepts seven revisions, and reopens seven valid native documents per trial.
- Bounded live-provider readiness: macOS credential reads now use a noninteractive `/usr/bin/security` process with a five-second limit. Nine focused authentication and preflight tests pass. One earlier built `FreeCADCmd` probe completed in 6.0 seconds with `process_timed_out: false`, `stage: complete`, and no prompt or document data sent. That probe recorded an invalid credential. It is historical evidence and is not a statement about the current credential state. The new Tier 1 live runner is under adversarial hardening. No live benchmark prompt has been sent, and no human-rated live score exists.
- Post-Keychain regression gate: the complete VibeCAD Python suite passed 606 tests with 5 skips in 7.30 seconds. The complete CTest registry contained 1,687 tests; all 1,680 enabled tests passed in 40.11 seconds, 3 platform tests skipped, and 7 upstream tests remained explicitly disabled. No CTest failed.
- Revision report and branch unit: added non-overwriting, content-bound JSON revision reports and exact FCStd branch copies with versioned lineage records. A branch gets a new project identity and migrates the accepted design brief, intent memory, design document, source settings, and current conversation history. Source CAD and project snapshot integrity are checked. Unsafe snapshot links are rejected. A project migration failure removes all new branch artifacts. Export and branch actions enforce RBAC and emit redacted audit events. The revision panel now has accessible Report and Branch controls. Seventeen focused contract tests passed, the full Python suite passed 614 tests with 5 skips in 7.08 seconds, the built modules imported, and the real FreeCAD integration reopened and recomputed the exact branch geometry.
- Image-assisted dimension safety: added a versioned, content-bound image-scale calibration for mm, cm, m, and inches. An uncalibrated image cannot return a numeric dimension. A calibrated pixel measurement remains an explicit estimate with a perspective and lens-distortion warning. The reference panel has an accessible Set scale control. Reference metadata writes are atomic, and a write failure restores prior in-memory state. Thirty-four focused calibration and context tests passed. The full Python suite passed 626 tests with 5 skips in 9.22 seconds, and the built modules imported.
- Guided FDM analysis: added the typed read-only `project.analyze_fdm` native capability. It reports native validity, watertight-solid state, bounds, cylindrical hole diameters, planar-spacing wall heuristic, unsupported downward area, and X/Y/Z orientation scores. Every non-native result is labeled as heuristic guidance and not certification. The tool uses exact object names, enforces project-view access, preserves document state, and is surfaced only on native modeling surfaces to keep provider schemas bounded. Forty-nine focused tool and surface tests passed. A real OpenCASCADE plate-with-hole integration passed. The full Python suite passed 630 tests with 5 skips in 8.68 seconds.
- Typed common-format export: extended `project.export` from STL and STEP to STL, 3MF, OBJ, STEP, and IGES. All formats keep exact object selection, managed policy and RBAC, project scope, exclusive non-overwrite promotion, SHA-256 identity, and redacted audit evidence. Fifty-seven focused export and surface tests passed. A real FreeCAD integration reopened STL, 3MF, and OBJ as nonempty meshes and STEP and IGES as valid geometry. The full Python suite passed 635 tests with 5 skips in 8.39 seconds.
- Typed drawing export: added `project.export_drawing` for one exact TechDraw page in PDF, DXF, or SVG. It enforces managed export policy and RBAC, writes only a new project artifact, returns a SHA-256 identity, and does not create a CAD revision. Forty-nine focused drawing and tool-surface tests passed. A real FreeCAD GUI test created a TechDraw page and verified `%PDF`, DXF `SECTION` and `EOF`, and SVG `<svg` output. The unittest reported `OK` for both offscreen and native macOS runs. The FreeCAD process then exited with code 134 during test-runner shutdown, after the successful result. The full Python suite passed 639 tests with 5 skips in 9.33 seconds.
- Drawing-package integration: the real FreeCAD GUI test now uses typed tools to create an A4 page, a live top view, one exact native linear dimension, and a revision annotation before it exports PDF, DXF, and SVG. It found that GUI hidden-line removal completes asynchronously and that a user preference can disable automatic page updates. The view tool now makes one bounded projection request, processes Qt events while the worker runs, restores the prior page and global update settings, and fails transactionally on timeout. Two focused preference and asynchronous-completion tests were added. Fifty-one focused tests passed. The full Python suite passed 641 tests with 5 skips in 10.86 seconds. The native FreeCAD unittest reported `OK`; its process retained the documented code-134 shutdown defect.
- Accepted drawing package: the complete typed page, live view, dimension, and revision-note turn now passes the same acceptance coordinator as native CAD mutations. The candidate validator reopens it in an isolated `FreeCADCmd` process and verifies valid source geometry, nonempty projected edges, dimension references, finite dimension readback, and nonempty annotations. The integration saves, closes, reopens, recomputes, compares revisions, exports PDF/DXF/SVG after reopen, restores the parent revision, and reopens the restored CAD file. Eight focused validator and drawing tests passed. The real FreeCAD unittest reported `OK`; the documented GUI shutdown defect then produced code 134. The full Python suite passed 643 tests with 5 skips in 10.78 seconds. Both registered TechDraw CTests passed in 0.98 seconds.
- Assembly analysis and replacement: added typed `assembly.extract_bom`, `assembly.analyze_interference`, and `assembly.replace_component`. BOM lines group equal native link sources and retain exact occurrence names, quantities, part numbers, materials, and descriptions. Interference uses OpenCASCADE common solids and reports only positive common volume. Replacement preserves occurrence identity, label, and placement, requires an equal topology signature, and re-solves existing joints before commit. Forty-eight focused assembly and tool-surface tests passed. A real FreeCAD integration created native links, verified a 500 mm3 overlap and zero-volume face contact, replaced one source, updated the BOM, and repeated interference analysis. The full Python suite passed 646 tests with 5 skips in 8.81 seconds.
- Accepted jointed assembly: isolated candidate validation now checks native link targets, one joint group, grounded targets, joint references, and a successful native solve. The real integration accepts source parts as the parent revision and a grounded, fixed, solved assembly as its child. It preserves joint records across topology-compatible replacement, reopens and validates the child, repeats BOM and interference analysis, compares revisions, restores the parent, and reopens the parent CAD file. Fifty-two focused assembly, validator, and surface tests passed. The full Python suite passed 648 tests with 5 skips in 8.44 seconds. The registered Assembly CTest passed in 1.74 seconds.
- Accepted spreadsheet variants: added typed `spreadsheet.bind_parameter`, which creates only direct `Sheet.alias` links to existing numeric CAD properties. The cell writer now accepts FreeCAD's exact text and quantity storage markers, touches expression dependents after a cell change, and recomputes them before commit. Isolated validation checks used cells, aliases, and direct expression targets. The real integration accepts a 40 by 30 by 20 mm spreadsheet-driven box, accepts a 55 by 30 by 25 mm variant, reopens both, exports STEP and STL, compares revisions, restores the first parameter revision, and reopens the restored geometry. Fifty-five focused validator, binding, and surface tests passed. The full Python suite passed 654 tests with 5 skips in 11.91 seconds. Five registered Spreadsheet CTests passed in 2.20 seconds.
- Accepted material assignment: assembly BOM extraction now reads an assigned native `ShapeMaterial` card when no explicit material string overrides it. Isolated validation requires an embedded material UUID and name but does not depend on a mutable external library after assignment. The real integration selects Aluminum-6061-T6 by catalog UUID, applies it through the typed tool, accepts it, reopens it, verifies the native card and BOM, compares revisions, restores the prior material state, and reopens again. Fifty-seven focused validator, BOM, and surface tests passed. The full Python suite passed 657 tests with 5 skips in 8.43 seconds. All 15 Material application tests passed in 0.246 seconds.
- Accepted mesh conversion and repair: isolated acceptance now analyzes every native mesh and rejects empty, incomplete, open, or defective meshes. The typed export tool accepts a ready native mesh for STL, 3MF, and OBJ without requiring a BREP shape. The real integration detects and repairs a duplicate facet, tessellates an editable solid, confirms watertight native topology, recovers a valid faceted solid, accepts and reopens both ready meshes, exports STL, 3MF, OBJ, and STEP, compares revisions, restores the parent, and reopens the source-only document. The full Python suite passed 661 tests with 5 skips in 9.98 seconds. Sixteen representative Mesh, MeshPart, and exporter CTests passed in 4.02 seconds.
- Accepted surface workflow: isolated validation now verifies native fill boundaries, section counts, thickening source links, fill mode, and an exact one-solid result. The real integration accepts an editable closed wire as its parent, creates a linked `Surface::Filling`, thickens it with a parametric `Part::Offset`, reopens the result, exports STEP and STL, compares revisions, restores the parent, and reopens the boundary-only document. The full Python suite passed 663 tests with 5 skips in 8.52 seconds. Nine representative offset, ruled-surface, and topology CTests passed in 3.00 seconds.
- Accepted CAM workflow: added typed project-scoped `cam.postprocess` for approved GRBL and LinuxCNC native processors, explicit units and output options, non-overwrite promotion, SHA-256 identity, managed export policy, RBAC, audit evidence, and restoration of temporary Job settings. Isolated acceptance validates model clones, one valid stock solid, tool links, operation membership, controller links, and nonempty native paths. The real integration creates explicit stock, a 6 mm endmill and controller, a collision-checked 1 mm facing operation, accepts and reopens the native job, post-processes GRBL, compares revisions, restores the parent, and reopens the model-only document. It also corrected simulator endpoint comparison for scale-bounded lower-precision conversion and stopped classifying native CAM object-property expressions as spreadsheet links. The full Python suite passed 667 tests with 5 skips in 9.87 seconds. Ninety-five representative native CAM tests passed; one documented CAM sanity case was an expected failure.
- Accepted FEM workflow: the reproducible developer environment now includes Gmsh 4.15.0 and CalculiX 2.23 on Apple Silicon. Asynchronous process status closes Gmsh standard input and refreshes completed child state without blocking. Solver readiness recognizes native constraint `TypeId` values. Isolated acceptance requires one solver, one nonempty volume mesh, material properties, fixed and load references, finalized Gmsh and CalculiX operations, nonempty solver output, and finite persisted result fields. Native VTK result archives now use safe relative paths, reject traversal entries, and register VTK Python wrappers on fresh reopen. The real integration creates a 40 by 10 by 10 mm cantilever, Aluminum-6061-T6, fixed and 100 N end-load constraints, a 4 mm Gmsh volume mesh, and a CalculiX static solve. It verifies displacement, stress, strain, and von Mises fields, accepts and reopens the result, compares revisions, restores the parent, and reopens the restored part. Forty-six focused tests passed. The full Python suite passed 671 tests with 5 skips in 8.86 seconds. Six native FEM result tests, three Gmsh transfinite tests, and both registered FEM-related CTests passed. The complete CTest registry then ran in 64.70 seconds: all 1,680 enabled tests passed, 3 platform-dependent tests skipped, 7 upstream tests remained disabled, and no test failed. The broad legacy `TestFemApp` aggregate was not used as a gate: several pre-existing golden input files differ from the current writers, and slow unrelated geometry cases were interrupted after those failures were recorded.
- Accepted multi-operation CAM workflow: the real integration creates a pocketed and drilled 40 by 30 by 10 mm part, a native CAM job with explicit stock, a 6 mm end mill, and a 4 mm drill. It finds the exact pocket and cylindrical hole faces from native geometry. It creates ordered outside-profile, pocket, and peck-drilling paths. Each path has a successful native generation state, nonempty cutting commands, and a complete collision analysis that does not remove protected model volume. The accepted document reopens with the same tool and operation order and command counts, produces controlled GRBL output with a verified SHA-256 digest, compares revisions, restores the parent, and reopens the source-only document. The operation guidance now points users to the typed `cam.postprocess` capability. Sixty-three focused CAM, validator, and tool-surface tests passed. The full VibeCAD Python suite passed 671 tests with 5 skips in 8.74 seconds.
- Accepted managed organization policy: added a versioned, redacted enterprise runtime-control record for provider mode, endpoint host, proxy, pinned CA identity, principal roles and permissions, context disclosure, provider tools, export, plugins, and update channel. The end-to-end integration loads a macOS managed-preference plist, permits only an approved model through an approved gateway, removes CAD geometry and images, limits remote tools to questions, applies an explicit proxy, loads a content-pinned CA, enforces reviewer RBAC, persists a denied export event, reopens it with prompt and geometry values redacted, verifies local-only operation, and rejects CA tampering. The audit found and closed a gateway bypass: a blank endpoint now resolves to the provider's official host and must pass the managed host allowlist. Eighty-two focused provider-session and enterprise tests passed. The full VibeCAD Python suite passed 675 tests with 5 skips in 8.57 seconds. Six representative Application CTests passed. The built application contains `VibeCADEnterpriseRuntime`.
- macOS release hardening: the release evidence self-test passed one positive case, 10 release-identity tamper cases, and 12 artifact tamper cases. The synthetic macOS smoke test passed ad-hoc app signing, DMG and unsigned PKG creation, PKG payload inspection, checksums, CycloneDX 1.5 SBOM, in-toto provenance, launcher verification, and exact DMG and PKG evidence checks.
- The macOS workflow now gives read-only repository access to preparation and build jobs. Only the publication job gets repository write access. The package recipe builds the bundled NavLib adapter and cannot download, mount, or install a vendor driver. Two regression tests prohibit privileged build-host changes.
- The Addon Manager policy guard now belongs to the parent VibeCAD source. It runs before Addon Manager network initialization, fails closed, and leaves the upstream Addon Manager submodule clean.
- Complete VibeCAD Python regression after release identity and Addon Manager hardening: 714 passed and 5 platform-dependent tests skipped in 17.61 seconds.
- The local production-sized Apple Silicon package build is not running. Session `14307` stopped at action 2,752 of 7,246. Its copied source is stale, only 16 GiB of local disk space is free, and it produced no full app, DMG, or PKG artifact. The credential-free synthetic release path passes, but P1-001 remains in progress until the tested checkpoint runs in Apple Silicon CI and the full artifacts pass verification.
- Candidate human-review acceptance boundary: 158 focused Python tests passed, and 87 of 87 representative CTests passed. The strict GUI test used a real saved FreeCAD document, native Part Design tools, the real session and acceptance stores, and the real Qt review controls. It proved the seven required states in order. It also proved that review does not change the canonical SHA-256 value, accepted head, manifest, accepted project entries, or accepted project tree SHA-256 value. Keyboard Reject and the Stop control each restored the empty prior CAD and project state and created no revision. Keyboard Accept created exactly one human-attributed revision. Close, reopen, and recompute preserved one valid 24 by 16 by 8 mm parametric Pad, the design brief, and the revision timeline.
- `QT_QPA_PLATFORM=offscreen ./build/release/bin/FreeCAD -t TestVibeCADCandidateReview` reported `Ran 1 test in 4.170s` and `OK`. All assertions passed. The process then exited with code 134 because the existing FreeCAD test runner raised `Base::SystemExitException` after unittest completion. This is a known test-runner shutdown defect, not a clean process pass.
- Complete VibeCAD Python regression after candidate-review changes: 708 passed and 5 skipped in 15.02 seconds. The skipped branding test requires FreeCAD GUI mode. Two memory-watchdog tests require `/proc` or Windows `psapi`. Two stub-executable watchdog tests require a POSIX shell and `/proc`.
- Complete CTest regression after candidate-review changes: 1,687 tests in the inventory, 1,680 enabled tests passed, 3 tests skipped, 7 tests disabled, and 0 tests failed in 104.87 seconds. `BackupPolicyTest.StandardWithZeroFilesDeletesExisting` and `BackupPolicyTest.TimestampWithZeroFilesDeletesExisting` skip because real file-system caching prevents a reliable assertion. `SchemaTest.imperial_building_special_function_length` skips because the upstream quantity parser crashes for the `1' 2" + 1/4"` input. `BackupPolicyTest.TimestampWithInvalidFormatStringThrows` and `BackupPolicyTest.TimestampWithAbsurdlyLongFormatStringThrows` are disabled because upstream code does not safely handle invalid or overlength format results. `DocumentObserverTest.hasSubObject`, `DocumentObserverTest.hasSubElement`, `DocumentObserverTest.normalize`, and `DocumentObserverTest.normalized` are disabled with their complete upstream fixture, which gives no source reason. `ExpressionParserTest.expressionsParseAsPyObjectWrapper` is disabled because the private internal wrapper cannot be tested directly.
- macOS package-source and clean-machine gates: 7 focused tests passed. The real local GUI smoke created the native parametric part, accepted one automatic revision, reopened it, and verified STEP and STL round trips. The process exited with code 0.
- Final VibeCAD Python regression before the Apple Silicon CI run: 719 passed and the same 5 platform-dependent tests skipped in 23.01 seconds.

## Phase 2 milestone report

Milestone: Phase 2 product shell and beginner workflow.

Implemented: First launch now creates a saved local project or opens a different
saved file before it completes. It requires a visible assistant workspace and an
editable prompt. Cancelled and failed setup stays retryable. Committed tests cover
normal-run Stop, GUI-to-session steering, all seven stable states, human candidate
review, keyboard Accept and Reject, Stop during review, real Tab and Shift-Tab
navigation, keyboard start-choice activation, and light and dark contrast.

Files changed: `VibeCADGui.py`, `TestVibeCADOnboarding.py`, onboarding and GUI
contract tests, the implementation backlog, decisions, status, limitations, and
the accessibility review.

Tests added: Workspace readiness and fault cases, open cancellation, normal Stop,
steering consumption, native keyboard traversal, native keyboard activation, and
native light and dark contrast.

Tests run: 44 focused Phase 2 tests passed. The complete VibeCAD Python suite run
for this unit passed 798 tests with 5 platform skips. `TestVibeCADOnboarding` and
`TestVibeCADCandidateReview` each reported one passing native GUI test and `OK`.

Results: P2-001 acceptance criteria pass. A new user can create a persistent
project without selecting an engine. Candidate validation preserves accepted
state. Accept creates one revision. Reject and Stop create none.

Benchmark impact: No live-model score changed. The beginner workflow can now
reach a usable saved project for later benchmark prompts.

Known issues: Both native GUI test processes still have the documented code-134
FreeCAD shutdown defect after unittest reports `OK`. Full manual VoiceOver review
remains open.

Next milestone: Continue P1-001 artifact CI, the P4-001 case-attempt scoring
contract, and the P7-001 security and performance gates.

External blockers: Production Developer ID and notarization credentials only for
credential-backed signing and notarization. A provider that passes the readiness
check is a prerequisite for a live score, not an implementation blocker.

## Phase 4 and Phase 7 progress report

Milestone: Phase 4 conversational modeling and Phase 7 release hardening.

Implemented: The version-2 capability router uses a versioned structured request,
a closed capability-category set, explicit selection and manufacturing context,
existing document structure, available engines, an optional project strategy
lock, and deterministic reliability evidence. It routes professional requests to
native internal workbenches. It preserves a compatible established engine and
source workbench for follow-up edits. A safe native route is the default when no
reliability evidence is available. Version-1 route records migrate to the new
content-bound format.

Implemented: The version-2 benchmark contract stores one complete record for each
case and attempt. It requires geometry, dimensions, constraints, editability,
follow-up, reopen, and export evidence. It also records provider, model, executor,
question counts, retries, normalized usage, elapsed time, diagnostics, and
instruction-adherence status. Deterministic and live-model completion rates stay
separate. A live-model record requires a bounded human rating, reviewer identity,
and notes. A deterministic record is explicitly not rated.

Files changed: Capability-router, project-routing, benchmark-contract, Tier 1 and
Tier 2 runner, benchmark aggregation, native routing integration, and focused test
files are in the current worktree. Performance, source-security, SBOM,
vulnerability, and release-gate files are also in progress.

Tests added: Router request, migration, structure preservation, strategy lock,
professional workbench, word-boundary, benchmark-contract, failed-evidence,
performance-report, source-scan, SBOM, scanner-integrity, and vulnerability-gate
tests.

Tests run: The combined router and benchmark-contract group passed 47 tests. The
native capability-router unittest reported one pass in 1.886 seconds and `OK`.
The candidate-review unittest reported one pass in 3.429 seconds and `OK`. Each
FreeCAD process then had the documented code-134 shutdown fault. One complete
deterministic Tier 2 attempt passed all 10 functional-part cases. It used 187
typed calls, created 10 accepted revisions, passed 10 close and reopen checks,
and kept all 26 sketches fully constrained at zero degrees of freedom.

Tests run: The complete VibeCAD Python suite passed 863 tests and skipped five
platform tests in 9.46 seconds. The complete CTest inventory still has 1,687
tests. All 1,680 enabled tests passed in 34.23 seconds. Three tests skipped and
seven upstream tests stayed disabled. The individual reasons are recorded in
the candidate-review report above. No CTest failed. The focused security and
performance group passed 92 tests. The release-evidence self-test passed one
positive case, 10 identity-tamper cases, and 12 artifact-tamper cases. The
synthetic ad-hoc macOS app, DMG, and PKG smoke test also passed.

Results: The deterministic Tier 2 result is 10 of 10 case attempts. This is
deterministic transaction evidence. It is not a live-provider conversational
score. No Tier 1 or Tier 2 live-provider human rating exists. P4-001 therefore
remains in progress.

Benchmark impact: The case-attempt denominator is now explicit and fail-closed.
One failed case keeps its structured evidence and counts as one failed attempt.
Deterministic results cannot satisfy a live-model target.

Known issues: Phase 7 has local implementation and unit-test evidence only. The
tracked-source scan passed locally with no finding and no read error. The exact
release commit still needs CI source and dependency scans. The performance gate
still needs a report from the application that the exact generated PKG installs.
The release checklist remains open.

Next milestone: Commit the tested router, benchmark, performance, security, SBOM,
and release-gate units. Run the complete regression gates. Then run the
credential-free Apple Silicon package workflow for that exact commit. Run live
benchmark attempts only after a provider passes the bounded readiness check and
human rating data is available.

External blockers: Production Developer ID and notarization credentials are the
only production release credential blocker. They block only credential-backed
signing and notarization. Provider access and human rating data are prerequisites
for a live-model score. They are not implementation blockers. They do not block
credential-free packaging, ad-hoc signing, deterministic benchmarks, security
scans, performance tests, or other implementation work.

## Phase 5 and Phase 8 progress report

Milestone: Native exploded views, content-bound STEP import, live Tier 1 runner,
and portable shared logic.

Implemented: The candidate-review acceptance boundary is complete. Validation
keeps the canonical CAD file, accepted head, project metadata, and accepted
project state unchanged. Accept creates one recorded revision. Reject and Stop
restore the prior accepted state.

Implemented: Typed assembly tools create and restore one native managed
exploded-view configuration for each assembly. Each move is translation-only.
The native graph and its content identity remain editable and inspectable. A
failed restore uses a compensating rollback. The rollback restores its exact
pre-call snapshot and verifies that the document has no unexpected change. An
injected create failure after assembly provenance also runs compensating cleanup
and leaves no created or changed object.

Implemented: A human or platform adapter registers one STEP file in the project.
The assistant panel now supplies an accessible file-selection control. File copy
and hashing run outside the GUI thread. The provider receives only an opaque
asset ID. The importer binds the bytes to a size and SHA-256 value and validates
solid geometry in a bounded `FreeCADCmd` worker. On macOS, a Seatbelt profile
denies network access, child execution, outside writes, and unrelated file
reads. The validator checks bounded worker output through stable no-follow
descriptors. It authenticates an open BREP descriptor. The pinned private path
passes identity checks before and after the parser creates one detached native
shape outside document-thread dispatch. The validator then removes all private
pathnames. Publication consumes that shape one time, revalidates the complete
evidence, and stores the canonical native BREP identity after recompute. It
creates one static native `Part::Feature`. A
downstream native cylinder and `Part::Cut` remain linked and editable. The
accepted revision passes save, close, reopen, recompute, compare, STEP and STL
export, and parent restore.

Implemented: Each provider mutation now needs one active prepared-acceptance
capability. The session issues it after acceptance preparation and revokes it
after provider execution. Direct, forged, wrong-service, and revoked uses fail
before mutation. The real `run_prompt` STEP test uses the provider runner and
acceptance coordinator, records one human-attributed revision, and reopens the
accepted part. STEP asset scanning is cancellable, reports path-free progress,
and uses a bounded identity-sensitive digest cache. Worker stdout and stderr use
a hard combined output limit.

Implemented: The Tier 1 live runner and its separate human-rating path are in
progress. The runner binds source, runtime, provider, model, and fresh readiness
evidence. Adversarial hardening is still in progress. It has not sent a live
benchmark prompt and has no human instruction-adherence score.

Implemented: The versioned portability contract checks named portable modules,
macOS adapter boundaries, compilation, and bounded tests. The local contract
group passed 102 tests. The Linux and Windows workflow exists, but real CI for
the exact source SHA is pending. The macOS release workflow also removes one
Bash 3.2 `set -u` failure by using one nonempty verifier-argument array. Exact-SHA
Apple Silicon CI is pending.

Files changed: Exploded-view contracts, typed create and restore tools,
document validation, native integration tests, STEP asset registration, isolated
STEP validation, typed STEP import, the provider runner, live benchmark runner,
portability contract and workflow, macOS workflow, and these living records.

Tests added: Managed exploded-view graph and rollback tests, one native
exploded-view integration, post-provenance create-fault cleanup, STEP registration
and fault tests, isolated parser tests, native STEP edit and acceptance tests,
real `run_prompt` one-revision coverage, prepared mutation capability tests,
bounded process-output tests, cancellable and cached asset-scan tests, macOS
Seatbelt adversarial tests, detached-parse binding and cleanup tests, live-runner
identity and rating tests, and portability contract tests.

Tests run: The exploded-view headless native CTest passed. Its GUI unittest
reported `OK`; the FreeCAD test process then had the known code-134 shutdown
fault. The final STEP-focused group passed 135 tests. The expanded sandbox,
STEP, asset, session, bounded-process, context, and transaction group passed 183
tests. The complete VibeCAD Python regression passed 1,093 tests with 5 platform
skips in 25.57 seconds. The registered exploded-view, STEP-import, and
live-geometry native CTest group passed 3 of 3 in 27.98 seconds. The local
portability contract group passed 102 tests and compiled 9 modules. The complete
1,690-test CTest inventory then passed with 1,683 enabled passes, 3 expected
platform-condition skips, 7 registered disabled tests, and no failure in 130.35
seconds. An earlier sequential run had isolated native crashes in
`Vector.TestScalar`, `TopoShapeExpansionTest.resetElementMapTest`, and
`SketchObjectTest.testDeleteExposeInternalGeometryOfParabola`. Each exact test
then passed alone, passed 10 consecutive stress runs, and passed in the clean
complete run. No VibeCAD test failed in either complete run.

Results: P5-013 and P5-014 are complete. P4-005 and P8-001 are in progress. The
main Tier 1 live-model target remains in progress.

Benchmark impact: One deterministic Tier 3 STEP import and native edit case has
complete accepted-revision evidence. It is not a live-model score. No Tier 1
live prompt or human score exists.

Known issues: STEP validation requires solid geometry with positive volume and
nonempty three-axis bounds. It rejects surface-only content. It does not preserve
a STEP assembly hierarchy. The imported source is one static `Part::Feature`; it
does not reconstruct source sketches or feature history. The validator uses a
separate safe-mode `FreeCADCmd` process with file, environment, time, and memory
bounds. On macOS, a fail-closed Seatbelt profile adds file, network, write, and
child-execution controls. A hard parent-process stop can leave a validator
temporary directory. Detached BREP parsing runs outside document-thread
dispatch. Native shape copy, assignment, recompute, and final identity checks
use the document thread. Linux STEP validation fails closed until it has an
equivalent OS sandbox. STEP attachment fails closed on Windows until a Win32 handle-relative
asset store exists; other provider turns remain available. Exploded views
support one managed configuration for each assembly and translation-only moves.
Real Linux, Windows, and exact-SHA Apple Silicon CI evidence is pending. The
FreeCAD GUI test runner still has the code-134 shutdown defect after unittest
success.

Next milestone: Complete adversarial hardening for the live Tier 1 runner. Run
the portable contract in Linux and Windows CI. Run the credential-free Apple
Silicon package and release gates for the exact source SHA.

External blockers: Production Developer ID and notarization credentials block
only final credential-backed signing and notarization. They do not block ad-hoc
signing, packaging, CI, tests, or benchmark implementation.

## Exact next task

Complete the P4-005 adversarial review. Run P8-001 on Linux and Windows. Commit
the tested units, then rerun the credential-free Apple Silicon package,
source-security, dependency, performance, and clean-install gates for that exact
commit. Keep deterministic and live-model benchmark rates separate by provider
and model. Production signing and notarization remain blocked only by the
acknowledged Apple credentials.

## Restart checkpoint: 2026-07-22

Milestone: P4-005 live benchmark security and macOS developer runtime.

Implemented: The Apple Silicon developer executables build and start. A native
GUI smoke created one fully constrained Part Design Body, Sketch, and Pad through
the normal session. It emitted all seven run states, accepted exactly one
revision, saved and reopened the FCStd file, and completed STEP and STL round
trips. The pinned Codex app-server 0.144.5 is installed in the local developer
build. Its runtime health and account-read smoke pass. No ChatGPT account is
signed in to the private VibeCAD Codex home.

Implemented: Failed live-provider calls now retain all seven case records without
claiming unknown token use as zero. Incomplete usage makes the run unscorable.
Request-turn binding rejects duplicate, mixed, replayed, decreasing, nonfinite,
and oversized usage evidence. Provider text and reasoning deltas retain only
bounded length and SHA-256 evidence. Partial metrics use a bounded version-2
contract and throttled checkpoints.

Implemented: The live runner uses a minimal child environment, bounded output,
private run directories, and process-group cleanup. macOS process descendants
are removed after normal exit and timeout. Provider subprocess start and global
spawn state are serialized. Partial-start failures close pipes and terminate the
child. Session cleanup now starts immediately after acceptance preparation and
keeps rollback failures fail-closed.

Implemented: The score tool uses stable no-follow descriptors for the raw run,
human ratings, readiness record, runtime identity, and case artifacts. It rejects
symbolic links, duplicate keys, hard-linked artifacts, path escapes, aliases,
content changes, and existing output files. It keeps all evidence descriptors
open through exclusive publication.

Files changed: Live benchmark contracts and runner, provider and session process
boundaries, secure process and evidence helpers, focused tests, the Codex runtime
installer, and one partial provider-runtime attestation helper.

Tests added: Live usage-evidence tests, score-integrity tests, secure-process
tests, provider partial-start and global-state tests, and session cleanup fault
tests.

Tests run: The combined live-usage, live-runner, score-integrity, and readiness
group passed 87 tests. The secure-process and live evidence group passed 82
tests. The score-integrity group passed 21 tests. Provider and session focused
tests passed 55 tests. The macOS GUI developer smoke passed. The Codex app-server
execution smoke passed in 0.264 seconds.

Results: The developer executable is available for macOS testing. P4-005 remains
in progress. The complete Python and CTest regressions have not run after these
changes. No live model prompt was sent. No Tier 1 human score exists.

Known issues: `tools/provider_runtime_attestation.py` is a partial, untested
change. It is not yet connected to the versioned runtime identity. The score
tool validates the retained runtime identity file but does not yet re-open and
verify each declared runtime component. The normal GUI startup can report that
the Help and Tools menus are not available during early VibeCAD menu
registration. The portable Codex runtime installer now uses Python for SHA-256
verification on macOS, but its focused regression test is not yet added.

Exact next task: Finish the provider-runtime attestation integration. Bind the
selected provider Python executable, loaded provider dependencies, and native
libraries to the runtime identity. Add tamper tests. Then run all focused P4
tests, rebuild `VibeCADScripts`, run the complete VibeCAD Python suite, run
representative native CTests, and run the complete CTest inventory. Fix the
macOS Help and Tools menu registration timing after the security boundary is
green.

External blockers: ChatGPT browser sign-in needs the user to complete the OpenAI
account flow. Production signing and notarization need Apple credentials. These
items do not block developer executable tests or remaining implementation.

## P4-005 provider-runtime security checkpoint: 2026-07-22

Milestone: P4-005 live Tier 1 benchmark security.

Implemented: VibeCAD now attests the exact Python selected by
`VibeCADProvider` through the built `FreeCADCmd` process. The version-3 runtime
identity binds that Python executable, 947 loaded Python module files, the
OpenAI, HTTPX, and HTTPCore distributions, all other loaded distributions, and
41 loaded non-system native libraries. The current identity contains 3,365
unique content-bound files. Attestation runs with a minimal environment, a
private directory, finite time and output limits, and no ambient provider
credential or Python loader variables.

Implemented: The GUI runner reads the 668-KiB runtime identity from its protected
single-link evidence file instead of placing it in one environment variable. It
verifies the file digest and contract before execution. The launcher repeats
source and runtime attestation before and after the GUI run. The score tool
reopens and hashes every declared provider component before scoring and again
before exclusive report publication. It rejects path escapes, links, aliases,
size changes, and digest changes.

Implemented: The pinned Codex app-server 0.144.5 installer now uses the selected
Python for portable SHA-256 checks. Its real cached-archive regression installs
the arm64 runtime, checks metadata and executable mode, and proves that a second
run is stable. The user completed ChatGPT subscription sign-in. A private
runtime check reports `account_present: true`; no credential value was read or
printed.

Files changed: Provider runtime attestation, live benchmark runtime contract,
launcher, GUI runner, score verifier, provider process boundary, session cleanup,
secure process and evidence I/O helpers, the Codex runtime installer, focused
tests, and project records.

Tests added: Exact provider attestation and repeatability, ambient-variable
removal, symbolic-link rejection, runtime-component contract faults,
score-time component tamper rejection, portable Codex runtime installation,
live usage completeness, score integrity, provider process cleanup, and session
rollback faults.

Tests run: The focused benchmark and persistence security group passed 162
tests. Runtime attestation and installer tests passed 4 tests. After the final
runtime-contract edit, the complete VibeCAD Python suite passed 1,188 tests with
5 skips in 26.05 seconds. Three representative native VibeCAD CTests passed in
11.27 seconds. The final complete 1,690-test CTest inventory passed all 1,683
enabled tests in 36.56 seconds, with
3 expected skips and 7 registered disabled tests.

Results: The provider-runtime security boundary passes its local implementation
and regression gates. P4-005 remains in progress until the signed-in seven-case
live run has complete evidence, independent human ratings, and a valid score.
No live benchmark prompt has been sent at this checkpoint.

Benchmark impact: The signed-in provider is ready for the first exact-source
live Tier 1 run. No live-model pass rate is claimed yet.

Known issues: A direct offscreen GUI smoke could not create an OpenGL context.
Its unittest reported `OK`, but the process exited with code 134, so it is not
counted as a clean GUI process pass. Normal developer GUI use works. Early Help
and Tools menu registration can still report warnings.

Next milestone: Commit this tested security unit to the OneManLabs branch. Run
the seven live Tier 1 cases from the clean exact commit, retain all success and
failure evidence, apply an independent content-bound human rating set, and
score the result. Then fix the early macOS menu-registration warnings.

External blockers: Production Developer ID signing and Apple notarization still
need the acknowledged Apple credentials. They do not block the developer
executable or the live benchmark.

## P4-005 ChatGPT live-readiness checkpoint: 2026-07-22

Milestone: P4-005 live Tier 1 benchmark execution.

Implemented: The live contract now supports the normal
`ChatGPTSubscriptionProvider` as well as the OpenAI API adapter. A blank ChatGPT
model setting resolves through the bounded no-prompt model list to its exact
declared default. The current account resolves to `gpt-5.6-sol`. The GUI runner
then sets that exact model on the adapter and disables skills and web search.
The pinned Codex app-server executable is now a required content-bound runtime
component.

Implemented: The readiness child now imports its reviewed source from the exact
repository root when `FreeCADCmd` omits the current directory from `sys.path`.
It writes no prompt or CAD data. ChatGPT accounts that expose only an email use
a private, random, mode-0600 local account-binding key. Evidence contains only a
run-bound HMAC. It does not contain the email, the local key, or an OAuth token.

Files changed: ChatGPT subscription transport, live benchmark and runtime
contracts, readiness child and probe, GUI benchmark runner, focused tests, and
project records.

Tests added: Private account-binding key creation, repeatability, permissions,
symbolic-link rejection, email redaction, changed-key rejection, ChatGPT adapter
evidence, and Codex app-server runtime identity.

Tests run: The focused ChatGPT, readiness, benchmark, and score group passed 119
tests. The complete VibeCAD Python suite passed 1,192 tests with 5 skips in
25.12 seconds. The first parallel CTest run had one isolated inherited
FreeCAD abort. Two later parallel runs had unrelated native process crashes.
Each exact test passed alone and passed 10 consecutive stress runs. A sequential
run passed 1,682 enabled tests but had one isolated STEP validator result
failure; that exact VibeCAD test then passed alone. The final complete 1,690-test
parallel inventory passed all 1,683 enabled tests in 40.79 seconds, with 3
expected skips and 7 registered disabled tests.

Results: The no-prompt readiness gate passes for the signed-in ChatGPT account,
the exact `gpt-5.6-sol` model, and the content-bound local runtime. It reports
`prompt_sent: false` and `document_data_sent: false`. P4-005 remains in progress
until the seven live cases and independent rating set produce a valid score.

Benchmark impact: The exact-source live run can now use the user's ChatGPT
subscription without an API key. No live prompt has been sent at this
checkpoint.

Known issues: Inherited native FreeCAD tests can crash nondeterministically when
many test processes run together. The failures were not repeatable alone or in
10-run stress checks. The final full run is clean. The STEP validator had one
nonrepeatable no-result failure at the end of a 193-second sequential inventory.

Next milestone: Commit this tested subscription path to OneManLabs. Run all
seven Tier 1 cases from that clean exact source commit. Retain complete failure
and usage evidence. Create an independent content-bound human rating set and
score the run.

External blockers: Production signing and notarization still need Apple
credentials. They do not block the developer executable or live benchmark.

## P4-005 live-launch import checkpoint: 2026-07-22

Milestone: P4-005 live Tier 1 benchmark execution.

Implemented: The first GUI launch stopped before it sent a provider request.
FreeCAD's GUI test runner did not put the repository root on `sys.path`. The
launcher now supplies its exact absolute repository root through the minimal
child environment. The runner validates that path and adds it before it imports
the content-bound readiness and evidence helpers.

Files changed: Live benchmark launcher, GUI runner, focused regression test, and
project records.

Tests added: The minimal-child-environment test now requires the exact repository
root and still rejects hostile ambient `PYTHONPATH` values.

Tests run: The focused launcher test passed 44 tests. The combined live
contract, launcher, and score-integrity group passed 89 tests. The installed
VibeCAD scripts target rebuilt successfully.

Results: The failed attempt created no raw case evidence and sent no model
prompt. It is not a benchmark attempt. The corrected code is ready for an
exact-clean-commit live run.

Benchmark impact: No live-model rate is claimed.

Known issues: The offscreen GUI still reports that it cannot create an OpenGL
context. This warning did not cause the import fault, but it can affect later
viewport work.

Next milestone: Commit and push this tested fix only to OneManLabs. Run all seven
live Tier 1 cases from that clean commit.

External blockers: None for the live run.

## P4-005 first retained live-attempt checkpoint: 2026-07-22

Milestone: P4-005 live Tier 1 benchmark execution.

Implemented: The exact-source run retained all seven failure records and did not
accept any CAD mutation. It exposed a ChatGPT adapter event fault. The adapter
sent real requests and emitted complete cumulative usage, but it did not emit
`provider_request_started`. The benchmark monitor could not bind the usage to
one request and cancelled each active turn. The adapter now emits one bounded
request event before `turn/start`, with one turn, zero SDK retries, and one
maximum API attempt.

Files changed: ChatGPT subscription adapter, GUI benchmark runner import fallback,
focused tests, and project records.

Tests added: Exact ChatGPT request-event fields and request-byte measurement.

Tests run: The ChatGPT, live-usage, and live-launch group passed 89 tests. The
installed VibeCAD scripts target rebuilt successfully.

Results: Four provider requests ran before the dependent follow-up fixtures
failed. No tool call ran. No CAD mutation was accepted. All canonical CAD files
stayed at their fixture hashes. The attempt failed honestly and is not scored.

Benchmark impact: The retained run is a zero-pass harness failure. It is not a
product pass-rate claim. A new exact-source attempt is required after commit.

Known issues: The offscreen GUI process exited with signal 6 after it retained
all seven records. Follow-up cases could not build fixtures because the earlier
creation cases accepted no geometry.

Next milestone: Commit and push the request-binding fix to OneManLabs. Rerun the
seven cases from the new clean commit.

External blockers: None for the next live attempt.

## P4-005 controlled sketch-surface checkpoint: 2026-07-22

Milestone: P4-005 live Tier 1 text-to-CAD execution.

Implemented: The first clean reduced-scope run sent no provider request because
the scope validator rejected future Sketcher tools while Part Design was
active. The session now permits one explicit controlled union only when the
scope contains both `partdesign.edit_sketch` and `sketcher.close_sketch`.
Unknown, unrelated, incomplete, unsafe, and unavailable cross-surface scopes
still fail before provider execution.

Files changed: Modeling-surface validation, scoped session context, focused
transition tests, decisions, status, and limitations.

Tests added: Complete controlled sketch transition acceptance and incomplete
transition rejection.

Tests run: The session acceptance, live usage, and tool-surface guardrail group
passed 86 tests.

Results: The failed run retained seven records, made zero provider requests, and
accepted no CAD changes. The next clean run can declare the bounded tools needed
to create and close an editable sketch in one provider turn.

Benchmark impact: No live text-to-CAD pass rate is claimed.

Known issues: A new exact-source run is required.

Next milestone: Commit to OneManLabs and rerun all seven live Tier 1 cases.

External blockers: None.

## P4-005 provider retry-ceiling checkpoint: 2026-07-22

Milestone: P4-005 live Tier 1 benchmark execution.

Implemented: The next retained attempt exposed a provider-specific retry
contract mismatch. ChatGPT correctly declared zero client retries and one
attempt. The monitor required the OpenAI SDK ceiling of two retries and three
attempts, so it stopped each turn before a result. The monitor now accepts a
consistent declared retry value from zero through the global maximum. It still
rejects Boolean, negative, excessive, and inconsistent values.

Files changed: Live GUI benchmark monitor, live usage tests, and project records.

Tests added: A zero-retry, one-attempt ChatGPT request binds to cumulative usage
without weakening the global maximum.

Tests run: The ChatGPT, live-usage, and live-launch group passed 90 tests. The
installed VibeCAD scripts target rebuilt successfully.

Results: Four requests started. No usage completed, no tool ran, and no CAD
mutation was accepted. All canonical fixture hashes stayed unchanged. The run
is retained and not scored.

Benchmark impact: No live-model pass rate is claimed.

Known issues: The GUI process again exited with signal 6 after evidence
retention. Dependent follow-up fixtures could not run.

Next milestone: Commit and push this tested provider-specific retry contract to
OneManLabs, then run a new exact-source attempt.

External blockers: None for the next attempt.

## P4-005 least-context tool-scope checkpoint: 2026-07-22

Milestone: P4-005 live Tier 1 benchmark execution.

Implemented: Each Tier 1 case now supplies one ordered, case-specific provider
tool scope. The session validates the names against the active workbench and
modeling surface, checks their provider safety classes, copies only the needed
schemas, and freezes a new content-bound surface record. General application
turns keep the full resolved surface when no scope is supplied.

Files changed: Session context construction, live benchmark runner, focused
session and benchmark tests, decisions, backlog, limitations, and status.

Tests added: Scope ordering, duplicate removal, frozen schema evidence, unknown
tool rejection, complete seven-case scope coverage, bounded scope size, and
worker-thread event pumping.

Tests run: The combined session acceptance, ChatGPT, live-usage, and live-launch
group passed 117 tests. The installed VibeCAD scripts target rebuilt
successfully.

Results: The next live run will receive only the tools needed for each case.
This reduces irrelevant context while preserving typed native CAD operations.

Benchmark impact: No new live-model pass rate is claimed yet.

Known issues: The previous run timed out with the full 40-tool surface while its
CAD dispatch was blocked. A clean exact-source rerun is required.

Next milestone: Commit the tested scope unit to OneManLabs and run all seven
live Tier 1 cases from that exact clean commit.

External blockers: None for the next live attempt.

## P4-005 GUI document-thread checkpoint: 2026-07-22

Milestone: P4-005 live Tier 1 benchmark execution.

Implemented: The fourth retained attempt passed request and usage accounting.
The model completed design-brief tools and requested native CAD tools. The
benchmark had called `run_prompt` on the GUI document thread. A dynamic CAD tool
could not dispatch to that blocked thread, so the four creation cases reached
their 120-second provider timeout. The benchmark now uses the production
pattern: provider work runs on one daemon worker while the GUI thread processes
queued document operations. `run_prompt` receives the same synchronous document
dispatcher that the assistant GUI uses.

Files changed: Live GUI benchmark runner, focused worker-thread test, and project
records.

Tests added: The provider operation runs off the caller thread while the caller
continues to pump events and receives the result.

Tests run: The ChatGPT, live-usage, and live-launch group passed 92 tests. The
installed VibeCAD scripts target rebuilt successfully.

Results: Four live creation requests timed out after valid reasoning, usage, and
tool-selection evidence. No geometry was accepted. All canonical CAD files and
revision heads stayed unchanged. Dependent cases failed closed. The full
attempt is retained and not scored.

Benchmark impact: No live-model pass rate is claimed.

Known issues: One creation case crossed the 200,000-token case limit while its
CAD tool was blocked. The GUI process exited with signal 10 after it retained
all records.

Next milestone: Commit and push the production-equivalent GUI threading fix to
OneManLabs. Run a new exact-source seven-case attempt.

External blockers: None for the next attempt.

## P4-005 cumulative-usage checkpoint: 2026-07-22

Milestone: P4-005 live Tier 1 benchmark execution.

Implemented: The third retained attempt reached real model reasoning and CAD
tool selection. Codex emitted several complete, nondecreasing cumulative usage
updates for the same request turn. The monitor treated the second update as a
duplicate and stopped the turn. It now permits repeated cumulative updates for
the same bound turn. It still rejects repeated incremental usage, unbound
turns, mode changes, incomplete fields, decreases, and token-limit breaches.

Files changed: Live GUI benchmark usage monitor, focused tests, and project
records.

Tests added: Two nondecreasing cumulative updates for one zero-retry request
produce one complete turn measurement and the latest total.

Tests run: The ChatGPT, live-usage, and live-launch group passed 91 tests. The
installed VibeCAD scripts target rebuilt successfully.

Results: Four live requests produced real usage. The model selected design-brief
and native CAD tools, but the monitor stopped before it accepted any geometry.
All canonical CAD hashes and revision counts stayed unchanged. The run is
retained and not scored.

Benchmark impact: No live-model pass rate is claimed.

Known issues: The offscreen GUI process exited with signal 11 after it retained
the seven records.

Next milestone: Commit and push the cumulative-usage fix to OneManLabs, then run
a new exact-source attempt.

External blockers: None for the next attempt.
