# Cross-Platform Strategy

Shared Python and C++ modules own product logic. Platform adapters own credentials, application lifecycle, dialogs, file associations, updates, and managed configuration.

CI must keep macOS, Windows, and Linux build or smoke coverage. Schemas, project records, capability contracts, and benchmark fixtures must be platform neutral. Paths use platform APIs and do not use hard-coded separators.

macOS work must not change common FreeCAD document formats. Platform features must have a documented unavailable state on other systems.
