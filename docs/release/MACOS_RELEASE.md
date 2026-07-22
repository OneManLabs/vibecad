# macOS Release

The macOS workflow builds Apple Silicon and Intel bundles. Development mode ad-hoc signs the app and creates an unsigned DMG and PKG. Production mode signs the app with hardened runtime, signs the PKG, submits both distribution artifacts to Apple notarization, staples them, and verifies them before upload.

The application uses the fixed `CFBundleIdentifier` and package receipt identifier `com.vibecad.desktop`.

Required GitHub secrets:

- `MACOS_CERTIFICATE_P12_BASE64`: Base64 PKCS#12 data that contains the Application and Installer identities.
- `MACOS_CERTIFICATE_PASSWORD`: PKCS#12 password.
- `MACOS_CI_KEYCHAIN_PASSWORD`: Ephemeral runner keychain password.
- `MACOS_SIGNING_KEY_ID`: Exact Developer ID Application identity.
- `MACOS_INSTALLER_SIGNING_KEY_ID`: Exact Developer ID Installer identity.
- `MACOS_NOTARY_APPLE_ID`: Apple account for `notarytool`.
- `MACOS_NOTARY_TEAM_ID`: Apple Developer team identifier.
- `MACOS_NOTARY_APP_PASSWORD`: App-specific notarization password.
- `MACOS_UPDATE_SIGNING_KEY_BASE64`: Base64 PEM private key for release update manifests.

Verification commands must include `codesign --verify --deep --strict`, `spctl --assess`, `xcrun notarytool`, and `xcrun stapler validate`. A release report must record command output. No document can claim notarization success without this evidence.

Each architecture artifact contains:

- `.dmg` and `.dmg-SHA256.txt`.
- `.pkg` and `.pkg-SHA256.txt`.
- `SHA256SUMS`.
- CycloneDX 1.5 SBOM with scanner-ready package URLs and package SHA-256 values.
- in-toto statement with SLSA provenance predicate.
- Versioned update manifest and detached signature for production builds.
- Raw Grype vulnerability report and versioned VibeCAD vulnerability evidence.

The signed update manifest binds each artifact name, byte size, SHA-256 value, and HTTPS URL. The desktop app asks before download. It downloads to a unique temporary file, verifies the signed size and digest, and promotes without overwriting another file. On macOS it then runs DMG verification, Gatekeeper assessment, and stapled-ticket validation. It asks again before it reveals the package in Finder. It does not mount, open, execute, or install the package.

Run the workflow with `sign_release=true` for a production candidate. The workflow stops if any required secret is absent. It verifies `codesign`, Gatekeeper assessment, stapling, and the Installer identity before artifact upload.

The workflow applies permissions per job. The preparation and macOS build jobs get `contents: read`. Only the publication job gets `contents: write`.

Local verification completed for shell syntax, workflow YAML, evidence generation, checksum generation, CycloneDX output, provenance output, and unsigned PKG construction. Seven focused package-source and clean-machine tests passed. In the complete VibeCAD Python suite, 719 tests passed and the same 5 platform-dependent tests were skipped in 23.01 seconds. Credential-backed signing and notarization were not attempted because production credentials are unavailable.

`tools/verify_macos_release.py` mounts the DMG read-only, checks that it contains one application, verifies the bundle identifier, version, and code signature, runs the launcher smoke mode, expands and inspects the PKG payload, and verifies the SBOM and provenance artifact digests. It compares the application name, release version, source repository, source commit, builder, build type, and update channel with trusted workflow values. The provenance and update manifest must describe exactly the DMG and PKG under test. Production mode also requires Gatekeeper, Developer ID Installer, and stapling checks.

The release workflow downloads Grype 0.116.0 from the official release. It
checks the archive against repository-pinned Apple Silicon or Intel SHA-256
data before extraction. Grype scans the generated SBOM after evidence creation.
An unresolved critical or high finding stops the job. The workflow uploads both
the raw scanner JSON and the content-bound VibeCAD decision record. The database
must be no more than 72 hours old and no more than 10 minutes in the future at
the recorded evaluation time.

The release evidence self-test passed one positive case, 10 release-identity tamper cases, and 12 artifact tamper cases. The local release smoke self-test created a synthetic ad-hoc signed app, DMG, unsigned PKG, SBOM, provenance statement, and update manifest. The same release verifier passed all synthetic artifacts.

The workflow removes restored package environments and stale local build products after it restores caches. It then compares all 256 VibeCAD Python files in the checkpoint source and the installed package environment by relative path and SHA-256 value. This gate rejects stale package source from a restored cache.

The clean-machine gate installs the exact generated PKG at `/Applications/VibeCAD.app`. The installed app runs one real automatic `VibeCADSession` transaction. It creates a native Body, a fully constrained Sketch, and a Pad, and it accepts exactly one revision. The gate saves, closes, reopens, and verifies STEP and STL round trips. The real local GUI smoke completed this path and exited with code 0. Cleanup removes only `/Applications/VibeCAD.app`, and only when the same job created its ownership marker.

The production-sized local Apple Silicon build is not running. Build session `14307` stopped at action 2,752 of 7,246. Its copied source is stale, only 16 GiB of local disk space is free, and no full app, DMG, or PKG artifact exists from that attempt. The next P1-001 step is a tested checkpoint commit and push, followed by the credential-free Apple Silicon CI path. Production Developer ID signing and Apple notarization remain blocked only by the required production credentials.
