# Decisions

## D-001: Extension-first fork architecture

Date: 2026-07-22. Status: Accepted.

Keep VibeCAD product logic in bounded modules and adapters. Keep changes to upstream FreeCAD core small and separately auditable. This reduces sync risk and keeps standard documents compatible.

## D-002: Candidate before accepted revision

Date: 2026-07-22. Status: Accepted.

An AI mutation must not become the accepted model until required validation passes. Failed candidates remain available only for diagnostics. Native and scripted paths must use one acceptance contract.

## D-003: Automatic strategy is the beginner default

Date: 2026-07-22. Status: Accepted.

The product selects a modeling strategy from intent, document structure, editability, selection, manufacturing need, and reliability. Advanced users can lock a strategy.

## D-004: ASD-STE100 project language

Date: 2026-07-22. Status: Accepted by owner directive.

New project control documents and user-facing reports use ASD-STE100 Simplified Technical English.

## D-005: Content-bound revision identity

Date: 2026-07-22. Status: Accepted.

Each accepted AI revision uses a SHA-256 identity that binds all required provenance fields. The store is append-only. A separate guarded head supports revision navigation and branches. A head change does not claim to restore CAD geometry. The caller must restore verified rollback data first.

## D-006: One multi-file acceptance boundary

Date: 2026-07-22. Status: Accepted by owner directive.

The validated CAD candidate, reopenable rollback document, revision record, project metadata, and accepted head are one acceptance boundary. Promotion uses a durable journal. The record is staged before the CAD file changes. The head changes only after the candidate CAD file is valid and durable. A synchronous failure restores the prior CAD file, live document, metadata, and head. Startup recovery rolls back an interrupted journal to the prior accepted state.

## D-007: Scripted artifacts are part of rollback state

Date: 2026-07-22. Status: Accepted.

The rollback artifact includes the accepted FCStd file and a snapshot of mutable project artifacts. It excludes append-only revisions, acceptance journals, and conversation history. A failed scripted mutation restores the source, parameters, generated files, project manifest, CAD document, and accepted revision head together.

## D-008: Design intent has a content-bound project record

Date: 2026-07-22. Status: Accepted.

The structured design brief uses a versioned JSON schema and a SHA-256 content revision. It stays separate from chat history and the generated Markdown design document. Migration copies active legacy intent statements without deleting the legacy source.

## D-009: CAD mutations and design intent move together

Date: 2026-07-22. Status: Accepted.

Each accepted CAD mutation must include a successful update to the durable design brief. The revision record binds the resulting design-brief revision. If the provider omits the update, the full candidate turn rolls back. This prevents accepted geometry from moving ahead of its recorded intent.

## D-010: Reconstruct official ancestry before upstream sync

Date: 2026-07-22. Status: Accepted.

The imported fork snapshot has no common Git ancestor with official FreeCAD. Do not merge unrelated histories. Build a new integration line from an official upstream commit. Replay extension, packaging, branding, and tested shared-core patches in separate groups. This creates auditable official ancestry and prevents a false mass merge.

## D-011: Managed policy fails closed at network boundaries

Date: 2026-07-22. Status: Accepted.

Organization policy is a versioned, validated record. Provider, model, endpoint, local-only, update-channel, and update-host rules are checked before network use. Invalid managed policy stops the action. It does not fall back to an unmanaged provider or endpoint.

## D-012: Each accepted revision owns a verified restore artifact

Date: 2026-07-22. Status: Accepted.

Each accepted revision record binds an immutable CAD file and an accepted project-state snapshot. The contract has a version, project identity, relative paths, and SHA-256 values. Restore verifies both artifacts before it changes project state. Restore uses a new acceptance journal and rollback snapshot. A synchronous failure or a later recovery returns the CAD file, live document, project state, metadata, and head to the revision that was current before restore.

## D-013: Update trust is inside the signed application

Date: 2026-07-22. Status: Accepted.

A production app contains its update public key, channel, and metadata endpoints before application code signing. The client accepts redirects only between HTTPS hosts in the managed allowlist. The Help-menu check verifies signed metadata in a worker. It does not download, execute, or install software. A production release fails verification if the trust configuration, public key, or detached signature is missing or invalid.

## D-014: Remote data policy is enforced twice

Date: 2026-07-22. Status: Accepted.

Managed geometry and image rules apply at the final outbound provider-context boundary. Denied tools are not declared to the remote model. The tool runner checks the same policy again before execution. Local and offline providers keep local CAD capability because no data leaves the computer. Export policy is checked before a VibeCAD file dialog or write action. External Codex skills are disabled before the provider starts when plugin policy denies them.

## D-015: Project audit evidence is immutable model-independent state

Date: 2026-07-22. Status: Accepted.

Each audit event has a versioned schema and a SHA-256 identity bound to its content. Each event uses one atomic file. Project rollback snapshots exclude audit data, so a model rollback cannot erase security history. Sensitive keys, prompts, geometry payloads, and image payloads are redacted before the event identity is calculated. A later acceptance failure writes a compensating event instead of deleting the earlier event.

## D-016: RBAC uses a provider-neutral local principal

Date: 2026-07-22. Status: Accepted.

Authorization uses a versioned principal that is independent of an OIDC, SAML, local, or future identity provider. Managed configuration supplies organization identity, a device-resolved subject, and roles. Audit records use a SHA-256 actor identity instead of the raw subject. A local individual principal has owner permissions. A managed principal with no assigned role fails safe to viewer permissions.

## D-017: Managed OIDC uses strict local token validation

Date: 2026-07-22. Status: Accepted.

The desktop app validates an OIDC ID token locally before it creates a principal. Discovery and key endpoints must use HTTPS and an explicit managed host list. The validator accepts RS256 and ES256 only. It validates all identity, audience, time, and optional nonce claims. It reads session tokens only from macOS Keychain. Unknown external roles map to viewer access. An expired session cannot authorize an action.

## D-018: Desktop OIDC uses authorization code, PKCE, and loopback callback

Date: 2026-07-22. Status: Accepted.

VibeCAD is a public OIDC client. It does not contain an organization client secret. It opens the system browser with an S256 PKCE challenge and keeps the verifier, state, and nonce in memory. It binds a short-lived callback only to `127.0.0.1`. The callback must match the exact port, path, and state before code exchange. Token validation must pass before Keychain changes.

## D-019: OIDC renewal uses one versioned Keychain session

Date: 2026-07-22. Status: Accepted.

An organization can permit `offline_access` in managed scopes. VibeCAD stores the ID token and optional refresh token in one versioned Keychain record. Renewal uses the public client identifier and does not use a client secret. A renewed ID token must pass the full validation contract before it replaces the prior record. Sign-out deletes the record.

## D-020: Shared export and extension boundaries enforce managed policy

Date: 2026-07-22. Status: Accepted.

The standard FreeCAD export command checks managed export policy and RBAC before it opens a file dialog. The shared GUI export dispatcher repeats these checks before it imports an export module. Application startup checks external-plugin policy before it adds any user, legacy, additional, added-package, or extension-package path. A parent-owned VibeCAD adapter wraps the Addon Manager activation command after module initialization. It checks the external-plugin policy before Addon Manager network initialization. This design keeps the upstream Addon Manager submodule clean and unchanged. Invalid managed policy fails closed for external discovery and Addon Manager activation.

## D-021: SAML trusts only a pinned, signed assertion

Date: 2026-07-22. Status: Accepted.

The SAML adapter requires one signed assertion and a managed identity-provider certificate. It accepts RSA-SHA256 or ECDSA-SHA256 signatures and SHA-256 digests only. It rejects duplicate XML identities, multiple assertions, unsigned assertions, wrong request or relay state, wrong issuer, audience, destination, or recipient, and inactive assertions. It stores the validated response in Keychain so a later principal resolution repeats signature and condition validation.

## D-022: Organization provisioning stores privacy-safe local membership

Date: 2026-07-22. Status: Accepted.

Each validated managed principal provisions one content-bound local membership record. The record stores the organization, hashed actor identity, mapped roles, identity source, status, and provision time. It does not store the raw identity-provider subject. Atomic replacement preserves the prior valid record if a role update fails.

## D-023: Audit retention archives before cleanup

Date: 2026-07-22. Status: Accepted.

Managed retention limits the number and age of individual live event files. It does not erase evidence. VibeCAD writes and verifies a content-bound archive that links to the prior archive before it removes the selected live files. A crash can leave duplicate live and archived events. Readers deduplicate them by content identity and reject different content with the same identity.

## D-024: Audit report keys stay in Keychain

Date: 2026-07-22. Status: Accepted.

Audit report export uses Ed25519. The private key stays in Keychain. The portable report contains the redacted events, archive identities, public key, key fingerprint, content identity, and signature. A verifier can pin an external public key. A self-contained report proves content integrity but does not by itself prove organization trust.

## D-025: Update handoff never opens or installs software

Date: 2026-07-22. Status: Accepted.

The user approves the download after signed metadata verification. The downloader enforces the signed name, size, SHA-256, host, and redirect rules. Exclusive atomic promotion cannot overwrite a different existing file. macOS verifies the DMG structure, Gatekeeper result, and stapled notarization ticket without mounting it. A second user decision can reveal the file in Finder with `open -R`. VibeCAD does not open or install the package.

## D-026: Registered workbench export functions share one guard

Date: 2026-07-22. Status: Accepted.

VibeCAD reads the FreeCAD export-handler registry after application modules load. It wraps each registered module-level `export` function before user code can call it. The wrapper enforces managed export policy and RBAC before the original function. An import hook applies the same wrapper to late-loaded handlers. The standard GUI dispatcher keeps its separate pre-dialog and pre-module checks.

## D-027: Managed SCIM role assignments fail closed

Date: 2026-07-22. Status: Accepted.

VibeCAD reads SCIM 2.0 users and groups only from a managed HTTPS endpoint on an explicit host list. The bearer token stays in macOS Keychain. The local assignment is versioned, content-bound, atomically replaced, and keyed by a SHA-256 actor identity. It does not store the raw identity subject. When SCIM is enabled, a missing, inactive, invalid, or mismatched assignment denies the managed session. SCIM roles replace identity-token roles so a stale token cannot keep access that the organization removed.

## D-028: Central policy bundles require pinned signatures and monotonic sequence

Date: 2026-07-22. Status: Accepted.

MDM supplies the organization identity, HTTPS endpoint allowlist, and pinned Ed25519 public key. A remote policy bundle binds its organization, issue time, expiry time, sequence, and complete policy to one signature. VibeCAD rejects expired, future, rolled-back, or conflicting same-sequence bundles. It promotes a verified bundle with an atomic, durable cache replacement. When bundle mode is enabled, no missing or invalid cache can fall back to local policy.

## D-029: All managed network adapters share proxy and TLS policy

Date: 2026-07-22. Status: Accepted.

Managed network policy selects system proxy, direct connection, or one explicit allowlisted proxy. An explicit proxy URL cannot contain credentials. A custom certificate-authority file must use an absolute path and match its managed SHA-256 identity before VibeCAD loads it. The application reads the pinned bytes into its TLS context, which removes a path replacement race. Identity, SCIM, policy, update, OpenAI, Anthropic, design-review, and intent-memory clients use this shared policy.

## D-030: Central audit collection requires two signed identities

Date: 2026-07-22. Status: Accepted.

The desktop signs each redacted audit report with its Keychain key. Organization policy can pin that key's public fingerprint. The collector must return an Ed25519 receipt signed by the public key pinned in managed policy. The receipt binds the report, organization, acceptance state, and receipt time. The report identity is the idempotency key. Upload failure or receipt failure cannot remove or change local events, archives, or the prior accepted receipt.

## D-031: Beginner sessions route modeling strategy before provider context

Date: 2026-07-22. Status: Accepted.

One deterministic router selects one available authoring surface before the provider receives tools. It preserves the engine of an established model, prefers native editable CAD for professional workflows, and can select build123d for a new functional Part Design object when its isolated runtime is available. OpenSCAD is never an automatic default. An advanced lock is explicit and fails if unavailable. The content-bound route is saved in project metadata and included in bounded provider context.

## D-032: Candidate validation and internal saves must not change the live document identity

Date: 2026-07-22. Status: Accepted.

Candidate reopen validation runs in an isolated `FreeCADCmd` process. It cannot replace the active GUI document or emit user-save events for the live document. Rollback and candidate `saveCopy` operations run inside a thread-local internal-save boundary. Document observers ignore these internal copies. After promotion, the acceptance coordinator restores the canonical file name and active document. A provider turn can make only the controlled Part Design-to-Sketcher and Sketcher-to-Part Design transitions that its named edit and close tools require. All other surface changes remain frozen and fail safely.

## D-033: First launch starts from intent, not implementation

Date: 2026-07-22. Status: Accepted.

The default first-launch dialog offers seven user-intent choices and does not name workbenches or modeling engines. It shows the active AI and data-boundary status before the user sends a request. A choice creates or opens the needed document, activates the native beginner workspace internally, opens the conversation surface, and inserts an editable prompt. Completion is written atomically only after workspace setup succeeds. A setup failure leaves onboarding incomplete so the user can recover on the next launch.

## D-034: Provider exports are project-scoped, explicit, and non-overwriting

Date: 2026-07-22. Status: Accepted.

`project.export` is a typed cross-workbench capability. It accepts exact object names, a bounded format, and a portable base name. It writes only inside the active project's `exports` directory, validates source shapes, enforces managed export policy and RBAC, and uses exclusive hard-link promotion so an existing file is never replaced. The result binds path, size, object names, and SHA-256 identity. Export is an external side effect and does not create a false CAD revision.

## D-035: Headless macOS credential reads are bounded and noninteractive

Date: 2026-07-22. Status: Accepted.

On macOS, provider credential reads use `/usr/bin/security` with no standard input and a five-second process limit. The read path does not print the credential or include command output in an error. A missing credential returns no value. A timeout or unexpected Keychain failure produces a redacted invalid-authentication state. Other platforms keep the existing keyring adapter. The outer readiness process has a separate bounded limit and must prove that it sent no prompt or document data before any live benchmark can run.

## D-036: Revision export and branch files never replace user files

Date: 2026-07-22. Status: Accepted.

A revision report contains immutable accepted records, the current head, and a SHA-256 identity for its canonical content. A branch copies the exact content-bound accepted FCStd artifact and writes a separate versioned lineage record. Both paths use exclusive promotion and fail if a target exists. If lineage promotion fails after the CAD copy, VibeCAD removes only the new copy that it created. These operations do not move the source revision head or change the open document. Managed roles must permit export, and branch creation also requires design-modify permission.

## D-037: Image measurements are estimates, even after scale calibration

Date: 2026-07-22. Status: Accepted.

An uncalibrated image can support only shape and feature observations. It cannot supply a numeric CAD dimension. A user can bind one known physical length to a measured pixel distance in a versioned, content-bound calibration. Every derived value remains marked as an estimate because perspective and lens distortion can change it. The provider instruction and image label state this rule. A user-supplied critical dimension always has higher authority than an image estimate.

## D-038: Manufacturing guidance separates native checks from heuristics

Date: 2026-07-22. Status: Accepted.

The FDM analyzer reports native shape validity and watertight solid state as direct kernel checks. Planar wall spacing, overhang area, hole-size advice, and build orientation are heuristics. The report marks them as guidance and states that it is not certification. The provider must name exact final objects. The capability is read-only and stays off scripted-engine turn-start surfaces so the bounded context does not grow for unrelated work.

## D-039: Common 3D exports share one non-overwriting project boundary

Date: 2026-07-22. Status: Accepted.

STL, 3MF, and OBJ use the FreeCAD mesh exporter. STEP and IGES use the native geometry exporter. The provider supplies exact source object names, a bounded format, and a portable file name. Every format writes a new project-scoped artifact, never replaces an existing file, and returns its size and SHA-256 identity. A real round-trip check is required for each supported format.

## D-040: Drawing export names one TechDraw page and creates one new artifact

Date: 2026-07-22. Status: Accepted.

`project.export_drawing` accepts one exact internal TechDraw page name, one bounded format, and one portable file name. PDF and SVG use the TechDraw GUI exporters. DXF uses the TechDraw page exporter. Managed policy and RBAC apply before output. Exclusive promotion prevents replacement of an existing file. The result contains the page, format, size, path, and SHA-256 identity. Export does not change geometry or create a false CAD revision.

## D-041: Typed drawing views wait for exact hidden-line geometry

Date: 2026-07-22. Status: Accepted.

TechDraw can calculate exact hidden-line geometry in a GUI worker. A new view is not valid until that worker supplies projected edges. The typed view operation processes bounded Qt events while it waits. It does not silently replace exact geometry with the coarse polygon mode. It temporarily enables page recompute only for this operation and restores the user’s page and global update settings. A timeout fails the transaction and removes the incomplete view.

## D-042: Accepted drawings require semantic reopen validation

Date: 2026-07-22. Status: Accepted.

A drawing candidate is not valid only because its FCStd file opens. Isolated validation must recompute it and verify each view source, a nonempty exact projection, each dimension reference, a finite measured value, and nonempty annotation text. The accepted revision binds this validated CAD file and project snapshot. Restoring its parent removes the complete drawing package and restores the matching revision head.

## D-043: Assembly analysis uses occurrences, not source parts

Date: 2026-07-22. Status: Accepted.

A BOM groups component occurrences by their linked source and manufacturing metadata. Interference checks occurrence shapes after assembly placement and reports only positive OpenCASCADE common-solid volume. Face contact is not interference. Replacement changes the source behind one stable occurrence. It preserves the occurrence name, label, placement, and joint references, requires equal solid, face, edge, and vertex counts, and must pass the native solver before commit when joints exist.

## D-044: Accepted assemblies require hierarchy and solver validation

Date: 2026-07-22. Status: Accepted.

An assembly candidate is not valid only because all link shapes are valid. Isolated validation requires each component link to have a source, one native joint group, valid grounded targets, two valid component references for each constraint joint, and native solver code zero. The accepted artifact binds this solved hierarchy. Parent restore must remove the assembly and restore the matching revision head and source-only CAD file.

## D-045: Spreadsheet binding accepts names, not free-form expressions

Date: 2026-07-22. Status: Accepted.

The provider can bind one existing numeric CAD property to one existing spreadsheet alias. The tool constructs the direct expression itself as `Sheet.alias`; it does not accept arbitrary expression text or code. A cell update touches every direct dependent and recomputes it before commit. Isolated acceptance validates each used cell, alias, direct reference, and resulting CAD recompute. Revision restore must restore both parameter values and downstream geometry.

## D-046: Accepted material identity is embedded in the CAD document

Date: 2026-07-22. Status: Accepted.

Material selection uses an exact library UUID at assignment time. The accepted native `ShapeMaterial` card embeds its UUID, name, models, and properties in the CAD document. Reopen validation checks the embedded identity and does not require the current external library to contain the same card. BOM extraction reads the embedded card name unless an explicit manufacturing material string overrides it. Revision restore restores the complete prior material state.

## D-047: Accepted meshes require a complete native readiness verdict

Date: 2026-07-22. Status: Accepted.

A mesh candidate is not valid only because it has facets. Isolated acceptance requires nonempty points and facets, successful native topology checks, one connected component, no known defects, and a native watertight-solid result. A repair tool can run only when a complete baseline proves that the selected repair matches a real defect. Mesh export accepts only this ready state. Shape recovery requires the same evidence before it can create a BREP solid.

## D-048: Accepted surfaces retain native construction dependencies

Date: 2026-07-22. Status: Accepted.

A surface result must remain linked to its native boundary or section objects. Isolated acceptance checks those links and requires valid face geometry. A thickened surface must retain its source, use filled skin mode, and recompute as exactly one valid solid. Export uses the thickened solid. Revision restore returns the editable source boundary and removes all downstream surface artifacts.

## D-049: CAM output requires native path evidence and explicit generic limits

Date: 2026-07-22. Status: Accepted.

A CAM revision is accepted only when its job retains model clones, one valid stock solid, linked tool controllers, operation membership, exact controller links, and nonempty native paths. Native stock-removal analysis must complete without protected-model collision. The simulator can permit only scale-bounded endpoint drift caused by lower-precision coordinate storage. Post-processing names an approved native processor and exact output options, restores temporary Job settings, never overwrites a file, and reports that generic output has no machine configuration or machine-limit verification.

## D-050: Accepted FEM results must survive an isolated native reopen

Date: 2026-07-22. Status: Accepted.

A successful external solver exit is not sufficient. An accepted FEM analysis must retain one configured solver, one volume mesh, required material properties, valid support and load references, finalized process provenance, nonempty solver output, and finite result fields. Gmsh and CalculiX run asynchronously and status polling must work with or without a GUI event loop. VTK result archives use relative member paths and reject traversal. A fresh process must register the VTK Python data-model wrappers before it exposes restored native data. The acceptance boundary saves, closes, reopens, recomputes, validates result fields, compares revisions, and restores the exact parent CAD state.

## D-051: Multi-operation CAM acceptance uses exact native geometry and ordered tools

An accepted CAM workflow must use job-model clone geometry, exact face references, explicit compatible tool controllers, and native path operations. Profile, pocket, and drilling operations must each generate cutting commands and complete protected-model collision analysis. Tool and operation order must remain unchanged after an isolated reopen. Controlled post-processing, revision comparison, parent restoration, and a second reopen are part of the same evidence boundary.

## D-052: A blank managed provider endpoint still has an enforceable host

Managed endpoint policy applies to both custom gateways and default provider endpoints. When no override is set, VibeCAD resolves the official OpenAI, Anthropic, or ChatGPT API host and checks it against the organization allowlist. A gateway-only policy cannot fall back to a public provider endpoint. Enterprise runtime evidence contains only host names, policy decisions, role permissions, and content identities. It cannot contain CAD context, prompts, credentials, or image data.

## D-053: macOS package builds cannot install vendor drivers on the build host

The package recipe builds the source-tree 3Dconnexion NavLib adapter. It does not download, mount, or install the 3DxWare driver and does not use `sudo`. Driver installation is a user or MDM deployment choice outside the application build. This keeps local and CI packaging noninteractive, least privilege, and reproducible.

## D-054: A validated candidate remains separate until the acceptance decision

Date: 2026-07-22. Status: Accepted.

`VibeCADAcceptanceCoordinator.validate_candidate(...)` writes one durable `vibecad-validated-candidate-v1` record. The record binds the validated CAD copy, the candidate project snapshot, and the prior accepted state. Validation does not change the canonical CAD file, accepted head, project metadata, or accepted project state. `accept_validated_candidate(...)` can promote only that stored candidate through the existing crash-safe acceptance journal. `promote(...)` remains the compatibility wrapper for explicit automatic acceptance.

The session records whether acceptance is human or automatic. A human rejection creates no accepted revision. Reject and Stop during review restore the prior accepted CAD and project state. The GUI uses one candidate-decision callback and accessible Accept and Reject controls. The assistant emits seven stable run-state events in this order: Understanding, Inspecting design, Planning, Creating preview, Validating, Applying revision, and Complete.

FreeCAD `saveCopy()` can rewrite FCStd ZIP bytes even when the model does not change. Each new rollback artifact therefore includes a byte-exact copy of the canonical FCStd file and its SHA-256 value. Rejection leaves the canonical file in place when its SHA-256 value still matches. If it does not match, synchronous rollback and startup recovery restore and verify the byte-exact copy. The prior FreeCAD rollback copy remains a compatibility fallback for older artifacts.

## D-055: Project-context reads do not change accepted metadata

Date: 2026-07-22. Status: Accepted.

Reading an existing version-2 project manifest must not write it again. In particular, a review refresh must not change `updated_at`, the manifest bytes, or the accepted project-state hash. The store writes a manifest only when it creates a missing manifest, applies a required schema migration, or performs an explicit project mutation. This rule keeps inspection separate from promotion and makes the candidate-review invariants stable.

## D-056: Release evidence must match trusted build identity and the exact artifact set

Date: 2026-07-22. Status: Accepted.

The release verifier gets the expected application name, release version, source repository, source commit, builder, build type, and update channel from the trusted workflow. It does not learn these values from release evidence. The verifier checks these values across the CycloneDX SBOM, in-toto provenance, update manifest, and application bundle. Provenance and update records must name exactly the DMG and PKG under test. Missing, extra, duplicate, renamed, resized, or changed artifacts fail verification.

## D-057: Release workflow permissions are set for each job

Date: 2026-07-22. Status: Accepted.

The preparation and macOS build jobs get read-only repository access. Only the publication job gets repository write access. Production signing is an explicit workflow input. A production run fails when a required signing or notarization secret is absent. This keeps credential-free builds separate from production signing and applies least privilege to the release workflow.

## D-058: The clean-machine gate tests the exact built package

Date: 2026-07-22. Status: Accepted.

The application bundle, PKG receipt, release verifier, and uninstall check use `com.vibecad.desktop`. After cache restore, the workflow removes restored package environments and stale build products. It then compares all 256 VibeCAD Python files in the checkpoint source and the installed package environment by relative path and SHA-256 value. If a restored cache supplies stale product source, the gate fails.

The clean-machine gate installs the one generated PKG. It uses the installed application to run one real automatic session transaction. The transaction creates a native Body, a fully constrained Sketch, and a Pad, and it accepts exactly one revision. The gate saves, closes, reopens, and checks STEP and STL round trips. Cleanup uses one fixed application path and requires a job-owned marker before removal. If the job did not install the application, cleanup does not remove it.
