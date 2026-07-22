# Production Release Checklist

This checklist is the release acceptance record. A release owner must link each
`PASS` result to generated evidence. `BLOCKED` is permitted only for a named
external dependency. `NOT RUN` and `FAIL` stop a production release.

## Release identity

- [ ] The source commit is fixed and is present in release provenance.
- [ ] The application name, version, bundle identifier, package receipt, update
      channel, and source repository match the trusted release inputs.
- [ ] The installed VibeCAD source matches the fixed commit by relative path and
      SHA-256 value.
- [ ] The worktree and all required submodules are clean at the release commit.

## Product acceptance

- [ ] The complete enabled CTest suite passes. Each disabled or skipped test has
      one recorded reason.
- [ ] The complete VibeCAD Python suite passes. Each skip has one recorded reason.
- [ ] Tier 1 reaches at least 95 percent by case attempt for each claimed provider
      and model.
- [ ] Tier 2 reaches at least 85 percent by case attempt, or each failed attempt
      has a retained diagnostic and a prioritized work item.
- [ ] Save, close, reopen, recompute, restore, compare, STEP export, and STL export
      pass with the installed application.
- [ ] A failed provider or candidate operation does not change the last accepted
      CAD file, project state, or revision head.

## Security and privacy

- [ ] The formal threat review has no open critical or high risk.
- [ ] Dependency and source vulnerability scans pass, or each result has a signed
      risk decision and an expiry date.
- [ ] The secret scan passes on the complete release source and generated logs.
- [ ] The SBOM is CycloneDX 1.5 and binds the exact DMG and PKG.
- [ ] Managed local-only, endpoint, model, context, image, export, plugin, update,
      diagnostic, and audit rules pass their negative tests.
- [ ] Release logs contain no credential, complete sensitive prompt, or CAD
      geometry payload.
- [ ] The privacy review identifies every outbound data path and its user or
      organization control.

## Performance and accessibility

- [ ] Cold launch, warm launch, medium and large document open, deterministic
      first response, viewport interaction, revision apply, peak memory, worker
      cleanup, and quit meet the approved budgets.
- [ ] Core onboarding, conversation, Stop, review, revision, save, and export
      paths work with the keyboard.
- [ ] VoiceOver labels, focus order, status changes, and review controls pass a
      recorded review.
- [ ] Dark and light appearance pass the core workspace review.

## macOS artifacts

- [ ] The Apple Silicon application builds in a clean CI job.
- [ ] Each supported Intel or universal target builds, or its documented support
      decision is approved.
- [ ] The `.app` has a Developer ID Application signature and hardened runtime.
- [ ] The DMG is signed, notarized, stapled, and accepted by Gatekeeper.
- [ ] The PKG has a Developer ID Installer signature, is notarized, and has a
      valid stapled ticket.
- [ ] SHA-256 files, `SHA256SUMS`, the SBOM, provenance, update manifest, detached
      update signature, and release notes match the exact artifacts.
- [ ] Production verification passes `codesign`, `spctl`, `notarytool`, and
      `stapler` checks. The raw command evidence is retained.

## Clean-machine acceptance

- [ ] The exact generated PKG installs on each supported clean macOS target.
- [ ] The application starts without developer tools or a source checkout.
- [ ] A first-time user completes onboarding and creates an editable part by
      conversation without choosing a workbench or modeling engine.
- [ ] The project saves, closes, reopens, and keeps its conversation, design brief,
      accepted revision, and editable model.
- [ ] STEP and STL exports reopen and validate.
- [ ] File associations, Open With, drag and drop, recent files, full screen,
      window restore, clipboard image paste, and standard macOS menus pass.
- [ ] The PKG can be removed by the documented enterprise uninstall procedure.

## Enterprise acceptance

- [ ] A managed configuration profile applies and overrides user preferences.
- [ ] Approved provider and model, managed gateway, proxy, custom CA, update
      channel, and feature restrictions pass on a managed test device.
- [ ] RBAC and audit events pass for organization owner, administrator, CAD
      manager, designer, reviewer, and viewer roles.
- [ ] Local-only operation works without a provider or collaboration service.

## Current record

The checklist is not passed. The credential-free Apple Silicon workflow is in
progress for commit `65283cc28683852f3c3ee77ae156f73021ac7163`. Production
Developer ID and notarization evidence is blocked by the acknowledged Apple
credentials. Performance, full accessibility, formal security, and privacy
review evidence also remain open.
