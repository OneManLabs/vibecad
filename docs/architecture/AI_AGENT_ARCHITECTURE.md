# AI Agent Architecture

Each run uses this bounded loop:

1. Extract required constraints, preferences, assumptions, preserved geometry, and missing critical facts.
2. Inspect only relevant document state and explicit selections.
3. Create a concise structured plan.
4. Make a candidate in an isolated worker or reversible document transaction.
5. Validate geometry, recompute state, constraints, dependencies, dimensions, preservation rules, and exportability as applicable.
6. Keep the validated candidate in review storage. Keep the canonical CAD file, accepted head, project metadata, and accepted project state unchanged.
7. Get an `accept` or `reject` decision. Headless runs can use explicit automatic acceptance.
8. Promote only the stored candidate. Write its revision, CAD file, project state, metadata, and accepted head as one recoverable operation.
9. Diagnose and retry within a fixed limit when validation or promotion fails.

The provider receives a bounded context and only relevant tools. Provider adapters do not own CAD rules. Tool results use schemas and include changed objects, validation results, warnings, transaction identity, revision identity, and rollback availability.

## Versioned acceptance artifacts

`vibecad-rollback-artifact-v1` stores these items:

- The project and acceptance identities.
- The prior accepted revision head.
- A FreeCAD rollback copy for the live document.
- A byte-exact backup and SHA-256 value for the canonical CAD file.
- A snapshot and tree SHA-256 value for the prior accepted project state.

`vibecad-validated-candidate-v1` stores these items:

- The validated candidate CAD path and SHA-256 value.
- The candidate project snapshot and tree SHA-256 value.
- The canonical CAD identity and its pre-run SHA-256 value.
- The prior accepted head.
- The successful saved-document validation result.
- The complete, validated revision record that is ready for a decision.

The acceptance journal records each promotion state. FreeCAD `saveCopy()` is not a byte-stable backup operation, so the rollback artifact also stores an ordinary byte-exact copy of the canonical FCStd file. Rejection leaves an unchanged canonical file in place. Rollback and startup recovery restore and verify the byte-exact copy only when the canonical SHA-256 value changed. Human rejection records an audit event but does not create an accepted revision.

Project inspection is read-only. Reading an existing version-2 project manifest does not update its timestamp or rewrite its bytes. The project store writes only when it creates a missing manifest, migrates a legacy manifest, or applies an explicit project mutation. This rule keeps review-time project metadata and its accepted-state hash unchanged.
