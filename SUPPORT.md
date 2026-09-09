# Supported scope

Sandglass supports truthful local observation, not cloud account inventory or
account management.

| Capability | Claude | Codex | Grok |
| --- | --- | --- | --- |
| Local Token usage | Claude Code session logs | Official CLI/Desktop rollout logs | Official Grok Build CLI session logs |
| Account discovery | Official Claude config identity | Current official auth plus post-install Sandglass observations | Current official Grok Build CLI auth plus post-install observations |
| Official quota | Observed first-party subscription endpoint | Observed first-party subscription endpoint | Observed official CLI billing endpoint |

The detailed evidence grades and degradation rules live in
[`docs/provider-source-map.md`](docs/provider-source-map.md).

## Expected limitations

- Signed-out, cloud-only and never-recorded accounts cannot be discovered.
- Current login does not prove who owned older usage. Unprovable history remains unassigned.
- Claude transcript history normally lacks account identity; a single discovered account does not change that fact.
- First-party quota endpoints are not stable public integration contracts. Local Token usage remains available when quota is unavailable.
- Sandglass does not switch accounts, refresh credentials, import third-party account stores or estimate missing history.
- The supported desktop product is currently Windows. Other platforms are not release-tested yet.

## Getting help

Run these commands from the installed environment:

```powershell
sandglass --version
sandglass doctor
sandglass --offline --json accounts
```

Before sharing output, remove emails, account identifiers, user names and local
paths. Never share `auth.json`, `.credentials.json`, tokens, provider logs,
`cache.sqlite`, `telemetry.sqlite` or the full contents of `SANDGLASS_HOME`.

Once the public repository exists, use its issue tracker for reproducible,
non-sensitive bugs. Include the Sandglass version, Windows version, whether the
CLI or desktop shell was used, exact visible error text and synthetic reproduction
steps. Security reports follow [`SECURITY.md`](SECURITY.md).

Sandglass does not launch provider account-manager executables. If WDAC/App
Control, Smart App Control or AppLocker blocks an in-process native UI component,
the panel reports a Sandglass component failure rather than an account disconnect;
the same redacted status is available from `sandglass doctor` and
`/api/runtime-diagnostics`. Raw exception messages and local paths are not stored.

If policy blocks the main Sandglass executable before it starts, Sandglass cannot
display its own diagnostic. That case must be identified from the Windows policy
event or installer log. The release and update trust boundary is documented in
[`docs/release-integrity.md`](docs/release-integrity.md). Compatibility failures
that do not contain private account data may be reported as a public issue.
