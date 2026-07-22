# macOS Enterprise Deployment

## Supported package

Use the notarized `VibeCAD-*.pkg` for MDM or command-line deployment. The package identifier is `com.vibecad.desktop`. It installs `VibeCAD.app` in `/Applications`. The installer does not change user project files or provider credentials.

Verify before deployment:

```sh
pkgutil --check-signature VibeCAD-*.pkg
xcrun stapler validate VibeCAD-*.pkg
spctl --assess --type install --verbose VibeCAD-*.pkg
```

Silent installation:

```sh
sudo installer -pkg VibeCAD-*.pkg -target /
```

MDM products must deploy the PKG as a device package. Install it in the system context. Do not unpack and copy the application because that bypasses package and notarization checks.

## Update policy

Release evidence contains `vibecad-macos-update.json` and its detached signature. The signed application contains the matching public key, channel, and metadata endpoints. The Help-menu action verifies this metadata but does not install software. Managed deployments must use MDM package updates now.

## Uninstall

Quit VibeCAD. Remove only the installed application and its package receipt:

```sh
sudo /bin/rm -rf "/Applications/VibeCAD.app"
sudo pkgutil --forget com.vibecad.desktop
```

This operation preserves local CAD documents, project sidecars, Keychain items, preferences, logs, and recovery data. An administrator can remove those items only under a separate data-retention policy. Do not remove user project folders as part of application uninstall.

## Rollback

Keep the last approved notarized PKG in the MDM repository. To roll back, remove the application, install the prior approved PKG, and leave user files in place. Test schema compatibility before organization-wide rollback. A newer project schema might not open in an older application.

## Evidence retention

Retain the DMG, PKG, SHA-256 files, CycloneDX SBOM, in-toto provenance statement, update manifest, update signature, Git commit, workflow run URL, Apple notarization identifiers, and verification logs for each approved release.
