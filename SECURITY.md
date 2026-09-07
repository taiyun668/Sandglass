# Security policy

## Supported versions

Before the first public release, only the current `main` branch is supported.
After releases begin, the latest published version will receive security fixes;
older versions may be asked to upgrade before a report can be reproduced.

## Reporting a vulnerability

Do not open a public issue for a vulnerability or attach credentials, provider
logs, account identifiers or local databases.

Use the repository Security tab's **Report a vulnerability** action. Private
vulnerability reporting is enabled on `taiyun668/Sandglass`. No separate public
security contact address has been designated; the project will not invent one
in documentation.

Useful reports include the affected Sandglass version/commit, Windows and Python
versions, a minimal reproduction using synthetic data, and the expected impact.
Redact user names, home paths, emails and all authentication material.

## Security boundaries

High-priority issues include:

- any creation, modification, move or deletion under `~/.claude`, `~/.codex`, `~/.grok` or another provider directory;
- credential refresh, account switching or retention of a provider token under `SANDGLASS_HOME`;
- prompt, tool content or raw OTLP payload retention;
- dashboard/API exposure outside IPv4 loopback;
- arbitrary file access or code execution through local HTTP inputs;
- an unsigned or incorrectly verified update path once updates are implemented.

The dashboard is intentionally unauthenticated because it binds only to local
IPv4 loopback. Sandglass rejects non-loopback binds and non-loopback HTTP clients.
Do not weaken both controls in the same threat model.

Non-sensitive bugs can use the normal support process described in
[`SUPPORT.md`](SUPPORT.md).
