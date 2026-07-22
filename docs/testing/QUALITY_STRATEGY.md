# Quality Strategy

Every implementation unit adds or updates tests. Unit tests cover schemas, policy, migrations, revisions, providers, permissions, and validation. Integration tests cover candidate generation, rollback, save and reopen, cancellation, recovery, drawings, assemblies, and exports. UI tests cover first launch, conversation, stop, steer, accept, undo, restore, preferences, policy, relaunch, keyboard use, and accessibility.

Release tests install on a clean supported Mac and verify Gatekeeper, signature, notarization, launch, save, reopen, AI action, STEP export, STL export, and uninstall.

Failures remain visible. Tests are not removed or weakened to make a build pass. Baseline and milestone results go in `docs/PROJECT_STATUS.md`.
