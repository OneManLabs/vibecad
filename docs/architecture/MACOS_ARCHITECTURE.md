# macOS Architecture

Apple Silicon is the primary target. Intel is secondary while dependencies remain available. Shared CAD and agent logic must not import AppKit directly. Platform-specific services must use narrow adapters.

The release pipeline must make a correctly named application bundle, DMG, and PKG. Production artifacts require Developer ID signing, notarization, stapling, SHA-256 files, an SBOM, provenance, and update metadata. Development builds can use ad hoc signing.

The baseline CMake run selects `arm64` but incorrectly derives deployment target `26.4` from the installed SDK. The release pipeline specifies a lower target. All build paths must use one documented deployment-target policy.
