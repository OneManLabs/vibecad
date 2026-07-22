# Threat Model

CAD files contain sensitive intellectual property. Protected assets include geometry, prompts, images, credentials, provider policy, revisions, and audit records.

Main threats are credential theft, malicious CAD files, prompt injection in metadata or images, generated code execution, compromised plugins, path traversal, unsafe export, dependency or update compromise, excess transmission, sensitive logs, cross-project leakage, and policy bypass.

Required controls are Keychain storage, explicit provider consent, no ambient credential discovery, schema validation, least-privilege tools, isolated workers, file and network boundaries, time and resource limits, allowlists, signed updates, dependency and secret scans, SBOM data, audit records, and redaction.

Trust boundaries exist at file import, project metadata load, provider requests, worker processes, plugin calls, export paths, update downloads, and enterprise policy input. Each boundary requires negative tests before release.
