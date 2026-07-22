# Enterprise Security

Managed policy has precedence over user preferences. Policy controls approved providers, models, endpoints, network domains, prompt geometry, image transmission, diagnostics, export, plugins, reasoning limits, and local-only mode.

Credentials stay in the operating-system credential store or in an approved managed gateway. Logs do not contain keys, complete sensitive prompts, or proprietary geometry. Project audit events contain actor type, action, result, policy decision, time, project identity, and privacy-safe actor identities. Their content-bound records remain outside CAD rollback snapshots. Managed retention archives evidence before live-file cleanup. Signed report export keeps its Ed25519 private key in Keychain.

The macOS managed-configuration schema, signed central policy bundles, managed proxy and pinned custom-CA transport, signed update channels, six-role RBAC matrix, privacy-safe actor identities, OIDC, SAML, local organization provisioning, read-only SCIM 2.0 role synchronization, audit retention, organization-pinned report signing, and receipt-verified central audit collection are implemented. Automated signing-key enrollment, SCIM write provisioning, and a hosted collector service remain Phase 6 work.
