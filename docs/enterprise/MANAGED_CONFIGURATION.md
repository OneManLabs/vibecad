# Managed Configuration

VibeCAD reads organization policy from the macOS managed-preference domain `com.vibecad.desktop`. The system path is `/Library/Managed Preferences/com.vibecad.desktop.plist`. An MDM profile must write a dictionary that follows `managed-configuration.schema.json`.

Example payload:

```xml
<dict>
  <key>schema</key><string>vibecad-managed-policy-v1</string>
  <key>version</key><integer>1</integer>
  <key>local_only</key><false/>
  <key>allowed_providers</key><array><string>openai</string></array>
  <key>allowed_models</key><array><string>approved-model</string></array>
  <key>allowed_provider_hosts</key><array><string>ai-gateway.example.com</string></array>
  <key>allow_document_geometry</key><false/>
  <key>allow_images</key><false/>
  <key>allow_diagnostic_uploads</key><false/>
  <key>telemetry_enabled</key><false/>
  <key>external_plugins_enabled</key><false/>
  <key>export_enabled</key><true/>
  <key>update_channel</key><string>stable</string>
  <key>allowed_update_hosts</key><array><string>releases.example.com</string></array>
  <key>organization_id</key><string>engineering-example</string>
  <key>identity_mode</key><string>oidc</string>
  <key>oidc_issuer</key><string>https://login.example.com</string>
  <key>oidc_client_id</key><string>vibecad-desktop</string>
  <key>oidc_allowed_hosts</key><array><string>login.example.com</string></array>
  <key>oidc_allowed_algorithms</key><array><string>RS256</string></array>
  <key>oidc_role_claim</key><string>groups</string>
  <key>oidc_role_mapping</key>
  <dict><key>cad-designers</key><string>designer</string></dict>
</dict>
```

Host fields contain host names only. They do not contain schemes, paths, ports, user information, or wildcards. A local-only policy clears all provider and provider-host permissions even if the profile also supplies them.

The provider runtime enforces managed provider, model, endpoint, local-only, geometry, image, plugin, export, and role policy before it creates an online provider or performs a protected action. Invalid managed policy stops the action. It does not fall back silently to an unapproved endpoint.

OIDC mode accepts only HTTPS discovery and JWKS endpoints on the explicit host list. It accepts RS256 and ES256 only. It validates the signature, issuer, audience, authorized party, time claims, subject, and nonce. The ID token stays in macOS Keychain under `com.vibecad.desktop.identity`. An unmapped external role becomes the viewer role. `Sign In to Organization...` starts an authorization-code flow in the system browser. The public desktop client uses S256 PKCE and a short-lived local loopback callback. It does not contain a client secret.

Set `oidc_scopes` to an array that contains `openid`. Add `offline_access` only when the organization permits session renewal. VibeCAD stores the refresh token in the same versioned Keychain session record and accepts a rotated refresh token. `Sign Out of Organization` removes the record.

For SAML, set `identity_mode` to `saml`. Set `saml_idp_entity_id`, `saml_sp_entity_id`, `saml_sso_url`, `saml_acs_url`, `saml_allowed_hosts`, and `saml_idp_certificate`. The ACS URL must use a fixed local loopback port. Set `saml_role_attribute` and `saml_role_mapping` to map identity-provider groups to VibeCAD roles. VibeCAD requires one assertion signed by the pinned certificate. It does not accept encrypted assertions or IdPs that require a signed AuthnRequest.

Set `scim_enabled` to `true` to make SCIM the authoritative source for desktop roles. Set `scim_base_url` to the HTTPS SCIM 2.0 service root, `scim_allowed_hosts` to the exact approved hosts, and `scim_role_mapping` to map SCIM group display names to VibeCAD roles. Install one read-only bearer token in macOS Keychain under service `com.vibecad.desktop.scim` and account `<organization_id>`. VibeCAD looks up the signed-in user by `externalId`, then reads that user's groups. A missing token, user, assignment, or active status denies the managed session. The local cache stores only the hashed actor identity and mapped roles.

The signed update verifier enforces the managed update channel and host allowlist. The Help-menu action checks signed metadata but does not install software. Use MDM PKG deployment for managed updates.

To use central policy, set `policy_bundle_enabled` to `true`. Set `policy_bundle_url`, `policy_bundle_allowed_hosts`, and `policy_bundle_public_key` in the MDM bootstrap profile. The public key is one base64-encoded 32-byte Ed25519 public key. The signed JSON bundle uses schema `vibecad-policy-bundle-v1`. It contains `version`, `sequence`, `organization_id`, `issued_at`, `expires_at`, `policy`, and `signature`. VibeCAD rejects rollback, expiry, future activation, organization mismatch, signature failure, and different content with the same sequence. It uses the last valid, unexpired cache when the endpoint is unavailable. If no valid cache exists, managed actions fail closed.

Set `proxy_mode` to `system`, `direct`, or `explicit`. In explicit mode, set `proxy_url` and include its exact host in `proxy_allowed_hosts`. Do not put a user name or password in the URL. Use system proxy authentication or an organization gateway that authenticates the device. Set `custom_ca_path` to an absolute PEM file path and set `custom_ca_sha256` to the lowercase SHA-256 of the exact file. VibeCAD verifies the file identity before it adds the certificate to the system trust roots. These settings apply to provider SDKs, OIDC, SCIM, central policy, and update traffic.

Set `audit_live_retention_days` from 1 through 3650 and `audit_live_max_events` from 100 through 1000000. These values control individual live event files. Older or excess events move to a verified content-bound archive. They are not erased. The Tools menu can export one signed, redacted report. The signing key stays in Keychain. Pin the report public-key fingerprint in the organization verification process.

Set `audit_report_signer_fingerprint` to the enrolled device audit key's SHA-256 public fingerprint. After this value is set, VibeCAD rejects a missing or replacement signing key. For central collection, set `audit_collection_enabled`, `audit_collection_url`, `audit_collection_allowed_hosts`, and the base64 Ed25519 `audit_collection_receipt_public_key`. Put the collector bearer token in Keychain service `com.vibecad.desktop.audit-collector`, account `bearer:<organization_id>`. The collector must sign an accepted receipt for the exact report. Collection does not delete local evidence.
