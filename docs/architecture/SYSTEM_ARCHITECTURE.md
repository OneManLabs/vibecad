# System Architecture

VibeCAD uses these bounded layers:

1. The FreeCAD kernel and document layer owns geometry, documents, recompute, transactions, and import or export.
2. The typed capability layer owns versioned schemas, safety classes, deterministic tool contracts, and structured results.
3. The AI orchestration layer owns intent, inspection, planning, execution, validation, recovery, cancellation, and provider use.
4. The design intelligence layer owns the current structured design brief.
5. The revision layer owns provenance, comparison, restore data, and accepted revision identity.
6. The provider layer normalizes streaming, tool calls, errors, cancellation, model data, and usage.
7. Narrow platform adapters own Keychain, menus, files, updates, crash reports, accessibility, and managed settings.

Existing `src/Mod/VibeCAD` modules are the initial extension boundary. Changes to upstream FreeCAD core must stay small and separately auditable.
