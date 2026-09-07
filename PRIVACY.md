# Privacy

Sandglass is a local desktop usage recorder. It does not run a Sandglass cloud
service and does not upload local usage history, prompts or account lists to the
project owner.

## What Sandglass reads

Normal operation reads supported official-client records under:

- `~/.claude`
- `~/.codex`
- `~/.grok`

These records can contain account identifiers, email addresses, subscription
metadata, session identifiers, local file paths, timestamps, model names and Token
counts. Sandglass reads only the fields needed for discovery, usage, attribution
and quota display. It does not read community account managers as provider evidence.

Provider directories are read-only. Sandglass does not refresh or rewrite
credentials and does not switch accounts.

## What Sandglass stores

Sandglass writes only its own `SANDGLASS_HOME`, which defaults on Windows to
`%LOCALAPPDATA%\sandglass`. Local state includes parsed-session cache rows,
provider file paths and session identifiers, quota snapshots, secret-free identity
observations, UI/desktop state, reset-signal evidence and normalized OTLP usage.

Authentication tokens, prompts, tool input/output and raw OTLP request bodies are
not intentionally stored. Normalized OTLP storage is allowlisted to provider,
account/session identifiers, time, model and Token counters.

Local state remains until the user removes it. The Windows uninstaller removes
the application, shortcuts, registration and Sandglass's opt-in start-at-login
entry, but deliberately preserves `SANDGLASS_HOME` so uninstalling cannot erase
usage history by surprise. Provider directories are never uninstall targets.

## Network activity

The native Windows dashboard loads its packaged pages and API data in-process and
does not open a dashboard port. Its OTLP receiver is off by default; copying an
explicit precise-monitoring command enables an OTLP-only listener at
`127.0.0.1:7740/v1/logs`. That listener does not serve account, quota, report or
diagnostic APIs. The separately invoked `sandglass serve` command uses
`127.0.0.1:7740` for its browser dashboard and OTLP route. All HTTP modes reject
non-loopback binds and clients. Pages, fonts and icons are served from the
installed package rather than a CDN.

When live quota is enabled, Sandglass sends the current provider credential over
HTTPS only to the observed first-party account endpoint for that same provider:
Anthropic for Claude, OpenAI/ChatGPT for Codex and xAI's Grok CLI service for Grok.
Those requests retrieve account-wide quota state; provider privacy terms apply.
Use `--offline` to skip live quota requests.

Sandglass currently has no analytics, crash-reporting or automatic-update network
service. Any future updater must be documented here before release.

## User-controlled changes

The desktop writes the current-user Windows Run registry entry only after the user
explicitly enables start-at-login. Sandglass never enables it merely by starting.
The `SANDGLASS_HOME` environment variable can move Sandglass-owned state without
changing provider locations.
