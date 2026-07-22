# Upstream FreeCAD Synchronization

## Recorded state

- Official remote: `https://github.com/FreeCAD/FreeCAD.git` as read-only `upstream`.
- Fork head at audit: `b489a4209d7dda0f5b324b181f492d929816fdda`.
- Official upstream head at audit: `2dc56d2ea3a6128e61397c8f2c9acaff0777fc57`.
- Imported fork snapshot: `80fa22b1a1db769feb50c3737ef24d4bbcbafa58`.
- Common Git merge base: none.

The fork imported a complete FreeCAD source tree as a new root commit. It did not retain the official FreeCAD commit as an ancestor. An ordinary merge from `upstream/main` is therefore not safe. The older fork commit with the message “Merge remote-tracking branch 'upstream/main'” merged two fork branches. It did not establish official upstream ancestry.

The closest inspected official tree near the import date is `0d88b4ad4e221f05028ac623d15f49f2f2daf626` from 2026-06-23. This is an inference only. The imported snapshot differs from it in 522 paths, including 308 VibeCAD paths and 214 shared or upstream paths. Do not use this inferred commit as a proven merge base.

## Patch inventory

The machine-readable inventory is in `UPSTREAM_PATCH_INVENTORY.json`. The first audit found 1,014 paths changed after the imported snapshot:

- 540 VibeCAD extension paths.
- 131 build, packaging, and release paths.
- 12 documentation and governance paths.
- 331 upstream-core or shared paths.

The 331 shared paths are the main synchronization risk. They include document transactions, application lifecycle, dock management, provider integration hooks, Sketcher and Part Design behavior, Assembly, CAM, tests, and platform build files. Each shared patch needs an owner, a reason, and a regression test before replay.

## Safe synchronization method

1. Fetch and record `upstream/main`.
2. Create a new integration branch from the recorded official upstream commit. Do not use `--allow-unrelated-histories`.
3. Apply the VibeCAD module, product documents, provider workers, and platform adapters first.
4. Replay packaging and branding changes as separate logical patches.
5. Replay each shared-core patch separately. Record its purpose and test evidence.
6. Drop a shared patch when official upstream already supplies the required behavior.
7. Keep a compatibility adapter when replay would contaminate shared FreeCAD logic.
8. Build after each shared subsystem group.
9. Run VibeCAD tests, the complete CTest inventory, reopen and export tests, UI tests, and macOS packaging tests.
10. Replace the fork main line only after file-format, migration, and release acceptance pass.

## Future provenance rule

The reconstructed integration line must retain the official FreeCAD commit as an ancestor. Each later upstream update must use a normal merge on a dedicated integration branch. The release record must contain the official upstream commit, VibeCAD patch set, conflicts, test results, and unresolved core patches.
