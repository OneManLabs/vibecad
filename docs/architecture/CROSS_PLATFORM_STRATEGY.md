# Cross-Platform Strategy

Shared Python and C++ modules own product logic. Platform adapters own credentials, application lifecycle, dialogs, file associations, updates, and managed configuration.

CI must keep macOS, Windows, and Linux build or smoke coverage. Schemas, project records, capability contracts, and benchmark fixtures must be platform neutral. Paths use platform APIs and do not use hard-coded separators.

macOS work must not change common FreeCAD document formats. Platform features must have a documented unavailable state on other systems.

The `vibecad-portability-contract-v1` record names the shared Python modules, the
approved macOS import adapters, the exact CPython version, and the bounded test
files. Its verifier rejects unsafe paths, duplicate entries, missing files,
macOS-framework imports outside an adapter, compile failures, test failures, and
source identity drift.

The local portability contract group passed 102 tests. This result proves the
local contract behavior. It does not prove Windows or Linux behavior. The
repository has an Ubuntu and Windows CI matrix, but P8-001 stays in progress
until that workflow passes for the exact source SHA.

The macOS release workflow must also work with the Bash 3.2 shell that ships with
macOS. The verifier step now starts with one nonempty argument array and appends
`--production` only for a production run. This removes the `set -u` failure that
occurred when Bash 3.2 expanded an empty array. The fix still needs an exact-SHA
Apple Silicon CI result.
