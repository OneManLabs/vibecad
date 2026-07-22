# Project Status

Last update: 2026-07-22. Phase: 0, baseline and fork governance.

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
- Bounded live-provider readiness: macOS credential reads now use a noninteractive `/usr/bin/security` process with a five-second limit. Nine focused authentication and preflight tests pass. The built `FreeCADCmd` probe completed in 6.0 seconds with `process_timed_out: false`, `stage: complete`, and no prompt or document data sent. The configured OpenAI credential resolved as invalid, so live-model benchmark calls remain disabled.
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
credential-backed signing and notarization. Live-model scores require a provider
that passes the existing readiness check.

## Exact next task

Continue the active credential-free Apple Silicon package run for checkpoint
`65283cc28683852f3c3ee77ae156f73021ac7163`. Fix any failing gate. Then commit the
new Phase 2, performance, security, and benchmark-contract work and rerun the exact
workflow at that new commit. Keep deterministic and live-model benchmark rates
separate by provider and model. Production signing and notarization remain blocked
only by the acknowledged Apple credentials.
