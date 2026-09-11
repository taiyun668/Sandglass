**English** | [简体中文](README.zh-CN.md)

# Sandglass

A read-only local AI usage dashboard. Sandglass discovers model tools and
accounts on this computer and shows Token usage, official plan remaining quota,
and reset times in one place.

Data stays on this computer and is not uploaded. Sandglass does not switch
accounts, refresh credentials, or modify provider directories.

**Multilingual UI — 10 languages.** Sandglass follows the Windows system
language on first use, and you can switch languages at any time from the app
menu: **English · 简体中文 · 繁體中文 · Español · Français · Deutsch · Português (Brasil) · Русский · 日本語 · 한국어**.

## Download for Windows 10/11 x64

### [Download the installer — recommended](https://github.com/taiyun668/Sandglass/releases/download/v0.1.10/Sandglass-0.1.10-windows-x64-unsigned-setup.exe)

Choose this for normal use. It installs Sandglass for the current Windows user,
creates Desktop and Start Menu shortcuts, launches the app, and supports future
in-app updates. **Start at login stays off unless you enable it in Sandglass.**

### [Download the portable ZIP](https://github.com/taiyun668/Sandglass/releases/download/v0.1.10/Sandglass-0.1.10-windows-x64-unsigned-portable.zip)

Choose this if you do not want to install. Extract the entire ZIP, open the
`Sandglass` folder, and run `Sandglass.exe`. Do not run the EXE from inside the
ZIP. A later in-app update intentionally moves a portable copy to the installed
channel.

> **Windows Smart App Control:** Sandglass is currently not
> Authenticode-signed. When Smart App Control is enforcing, Windows may block
> both the installer and the executable inside the portable ZIP, with no
> per-app **Run anyway** option. Do not disable Windows security just to install
> Sandglass. The Owner-signed checksum manifest proves release integrity, but
> it is not a Windows trusted-publisher signature.

[Release notes and all files](https://github.com/taiyun668/Sandglass/releases/latest)
· [SHA-256 checksums](https://github.com/taiyun668/Sandglass/releases/download/v0.1.10/SHA256SUMS.windows)
· [Owner signature](https://github.com/taiyun668/Sandglass/releases/download/v0.1.10/SHA256SUMS.windows.sig)

<p align="center">
  <img src="docs/assets/sandglass-overview.png" alt="Sandglass overview with privacy-safe synthetic demo data" width="360">
</p>
<p align="center"><sub>Privacy-safe synthetic demo data. No real account or usage information is shown.</sub></p>

## Floating orb and magnetic panel

The compact orb can float freely or dock to a screen edge. Activating it opens
the slim dashboard beside the same edge; minimizing the dashboard returns to
the orb without stopping background observation.

<p align="center">
  <img src="docs/assets/sandglass-floating-orb-showcase.png" alt="Sandglass floating orb near the screen edge" width="900">
</p>

<p align="center">
  <img src="docs/assets/sandglass-magnetic-panel-showcase.png" alt="Sandglass magnetic dashboard attached to the floating orb" width="900">
</p>

<p align="center"><sub>Showcase compositions made from the real Sandglass UI and brand assets. Demo data only.</sub></p>

> **A complete evidence chain starts only after Sandglass begins observing.**
> History from before install is attributed to an account only when an official
> local source can prove it directly. Anything that cannot be proved stays
> unassigned as-is. Sandglass does not backfill that history from the currently
> signed-in account.

On first use you must choose a ledger path. After you choose one account and
official tools only, Sandglass attributes each platform's observed official
local usage to that platform's current account. Discovering older accounts does
not change that choice on its own. Whenever multiple accounts, multiple tools,
account switching, or rebuilding, completing, or updating the ledger are
involved, you can also choose the Adapter Skill path. That path does not erase
existing direct evidence: local Token that already proves an account remains
attributed; only evidence gaps wait for the skill to complete. Both modes take
effect at read time only. They do not rewrite the cache or the identity ledger,
and switching modes fully restores the previous view. Opening **Account and
tool mode** from the menu also lets you leave without saving; the read view
changes only when you explicitly select the other mode.

## Currently supported

| Provider | Local usage | Local account discovery | Official remaining quota |
| --- | --- | --- | --- |
| Claude | Claude Code session logs | Claude config directory | 5-hour, 7-day, and model windows |
| Codex | CLI / Desktop rollout logs | Official current sign-in data plus observations recorded after Sandglass starts running | 5-hour, 7-day, and credits |
| Grok | Grok CLI `turn_completed` | Official Grok Build CLI current sign-in data, official identity events, and Sandglass's persistent identity ledger | Period quota and product windows |

“Automatically discover all accounts” means accounts from supported providers
that have left recognizable sign-in data on this computer. Accounts that are
signed out, never landed on this computer, or have no provider log or quota
source cannot be inferred.

The public Sandglass build does not read `~/.codex/accounts/registry.json`,
community `grok-app`, or other third-party account-tool data. For Codex,
Sandglass continuously records official sign-in changes after it starts
observing. For Grok, it also merges identity events written by the official CLI
itself. The identity ledger decides which historical accounts exist; metadata
such as email and plan only supplements display.

### What you can see

| Visible | Not visible |
| --- | --- |
| Token usage written on this computer by supported official clients | Usage on other devices that was not synced into official local logs |
| Accounts and identity changes that official local sign-in data can prove | Accounts that are signed out, exist only in the cloud, or left no official local records |
| Provider account-level official remaining quota and reset times, which may include other devices | Third-party tools not connected by default, web-only chat, or activity that would require decrypting process traffic |
| Local sources the user explicitly connects; Sandglass accepts them as-is and marks recognized and unrecognized fields | Facts the user source did not provide directly; Sandglass will not use the three existing providers to adjudicate unknown data for the user |
| Calls the official client recorded locally | **Calls the official client did not record**: if the provider completed a call but did not write it into the local session file, nothing on this computer proves it happened |

The last row is not a hypothesis. On 2026-09-07 this machine measured a case
where the provider's own OTLP event stream contained a completed call, and the
same session's local record file had no trace of it — not recorded at another
time, simply absent. **Local totals are therefore a trustworthy lower bound,
not an upper bound.** That is one reason Sandglass can receive official OTLP:
it is the only local corroboration that can see this kind of omission.

Sandglass is a local observation dashboard, not a billing, cost-accounting, or
invoice-reconciliation tool.

## Sources

Sandglass trusts data in this order:

1. Per-turn session logs written by the official client;
2. The official client's account registry and authentication events;
3. The provider's first-party quota endpoint;
4. Observation timestamps and account-attribution ledgers recorded by Sandglass itself.

Local logs and quota endpoints are first-party client/service sources, but they
are not necessarily public APIs the provider commits to keep stable. If a quota
endpoint fails, local usage can still be counted; the dashboard marks remaining
quota as temporarily unavailable and does not guess numbers.

Sandglass now continuously monitors Grok accounts' official quota, reset times,
and local usage attribution. If there is no verifiable account sign-in or switch
event before observation starts, earlier usage is shown uniformly as Unassigned.
Sandglass does not guess or backfill it from the currently signed-in account.

Evidence grades for official OTel from the three providers, local logs, identity
events, quota endpoints, and third-party adapters are in
[`docs/provider-source-map.md`](docs/provider-source-map.md).
Official release packages bundle only sources the project has verified. For
multiple accounts, multiple tools, or rebuilding, completing, or updating the
ledger, users explicitly connect other local sources per
[`docs/custom-source-contract.md`](docs/custom-source-contract.md);
that data is always labeled user-adapter evidence and is never presented as
official account, quota, or reset state.
The full adapter mechanism is available in
[`skills/sandglass-adapter/`](skills/sandglass-adapter/)
for a local coding agent; `SKILL.md` owns the workflow and boundaries,
`references/` supplies reconstruction knowledge on demand, and `scripts/`
carries only deterministic operations. The agent first probes the tools and
direct sources this machine actually uses, then sends native shapes into an
independent inbox. Sandglass's mirror reports what it recognized, what it did
not, and the current product-use stage. Receiving data itself does not change
accounts or totals. After the user explicitly maps an account, only minutes that
match first-party local records exactly on provider, session, UTC minute, and
every Token bucket receive account attribution; Token is not added. Unmapping
reverses the change. For minutes that do not exist locally and have no
cross-session or cross-source collision, the user may separately choose to
include them in totals; the source follows every affected number, and turning
that off reverses it. After mapping an account produces admitted evidence, the
user may separately authorize that evidence for full-window inference. Derived
values continue to name every user source; revoking authorization only stops
inference and does not change already-admitted Token. No adapter data is written
to `cache.sqlite`. Sandglass does not automatically download or run adapters.
The `providers` field of `/api/quota` declares account discovery, local usage,
and official quota as separate capabilities. A platform with no discovered
account does not disappear from capability state.

Public collaboration and release boundaries are in
[`PRIVACY.md`](PRIVACY.md), [`SECURITY.md`](SECURITY.md),
[`SUPPORT.md`](SUPPORT.md), and [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Read-only boundary

Sandglass only reads provider directories:

```text
~/.claude
~/.codex
~/.grok
```

When sign-in expires, Sandglass only prompts the user to sign in again with the
corresponding official client. It does not exchange a refresh token for new
credentials, and it does not write back `auth.json` or `.credentials.json`.

Sandglass writes its own cache, quota snapshots, and attribution ledger under
`%LOCALAPPDATA%\sandglass`. `SANDGLASS_HOME` can move that location.

## Running

Python 3.12+ is required. The core's only extra dependency is OpenTelemetry's
officially generated protobuf message types, used to receive OTLP logs that
provider clients send of their own accord.

The unsigned Windows x64 build can be downloaded from
[GitHub Releases](https://github.com/taiyun668/Sandglass/releases)
as a per-user installer and a portable ZIP. Windows usually asks for
confirmation on first run. Preview builds are not stable releases, and the
shipped executables are still not Authenticode-signed. Windows with Smart App
Control enabled may block both the unsigned setup and the executable in the
portable ZIP outright. Smart App Control has no per-app bypass; do not turn off
system security policy just to install Sandglass.
Later updates are handed to the installer only after the download matches the
maintainer-signed `SHA256SUMS.windows` manifest. You can still run from a source
checkout:

```console
python -m pip install -e .
python -m sandglass doctor
python -m sandglass quota
python -m sandglass
python -m sandglass accounts
python -m sandglass serve --no-browser
```

When `sandglass serve` is invoked explicitly, the browser dashboard defaults to
`http://127.0.0.1:7740`. The native Windows dashboard loads pages and data
inside the desktop process and does not listen on a local port for the dashboard
by default.

The HTTP interface of `sandglass serve` accepts loopback addresses only, but it
does not authenticate clients. Other processes running as the current user on
the same computer can read the account, quota, and report data the browser
dashboard needs. The native desktop dashboard does not expose these interfaces.

The desktop shell already includes the dashboard and collector; you do not need
to run `sandglass serve` at the same time. If the desktop has already explicitly
enabled the precise-monitoring receiver occupying `127.0.0.1:7740`, do not also
let `serve` use the same port. To view the browser dashboard temporarily, use
for example `sandglass serve --port 7742 --no-browser`.

A regular wheel does not include the WebView2 WPF assemblies downloaded at build
time, so it falls back to pywebview. Official Windows release artifacts
separately assemble a version-pinned, provenance-checked Microsoft runtime so
native dashboard animation is preserved.

When native UI components are blocked by Windows application-control policy or
fail to load, Sandglass falls back to a compatible UI and writes redacted
component status in its own data directory. That is not shown as an account
sign-out. Use `sandglass doctor` or `/api/runtime-diagnostics` to inspect the
status. If policy blocks the main executable before it starts, the only way to
tell is from Windows policy events or install logs, because the program itself
is not running yet.

### Precise monitoring (optional, experimental)

Sandglass can provide an OTLP/HTTP protobuf receive endpoint at
`http://127.0.0.1:7740/v1/logs`. The native desktop build keeps that port off by
default; it is enabled only when the user copies a precise-monitoring command.
In that mode the port accepts only `/v1/logs` and does not serve dashboard,
account, quota, or report APIs. When `sandglass serve` is invoked explicitly,
the receive route is enabled with that local browser service. The receiver keeps
only account identifiers, session identifiers, time, model, and Token counts.
Prompts, tool input/output, and file paths are discarded before storage; raw
OTLP packets are not written to disk.

This capability is currently an independent evidence ledger and has not replaced
the overview page's existing totals. Official telemetry for Claude, Codex, and
Grok must be enabled by the user before the corresponding client starts;
Sandglass does not rewrite provider configuration. Evidence of this kind is not
created before enablement or while the receiver is not running, and gaps are
not backfilled from the current account. Each platform page distinguishes: no
events received yet; official client connected but no usage yet; usage received
without identity; and official account evidence received. These states only
describe what actually exists in the ledger. Clicking a status copies that
platform's officially supported one-shot PowerShell launch command. The command
affects only newly started client processes and does not modify provider
configuration. The experimental ledger is still not added into existing totals.
Source details and acceptance status are in
[`docs/provider-source-map.md`](docs/provider-source-map.md).

### Windows desktop shell

The desktop build keeps the floating orb, dashboard, and tray. In a development
tree, install desktop extras and prepare the official WebView2 WPF runtime:

```powershell
pip install -e ".[desktop]"
powershell -ExecutionPolicy Bypass -File tools/build_native_shell.ps1
pythonw sandglass-desktop.pyw
```

The dashboard uses WPF `WebView2CompositionControl` inside the `pythonw`
process so it expands and collapses continuously from the orb's original
position. The WebView stays at its final size; animation does not trigger
per-frame page reflow, and there is no need to run an unsigned EXE compiled by
Sandglass itself. Without the runtime prepared, it falls back to pywebview. The
orb is still a Win32 layered window. The registry is not written unless
**Start at login** is enabled.

### Platform support

| Part | Current scope |
| --- | --- |
| Core CLI and browser dashboard | Targeted at Windows, macOS, and Linux with Python 3.12+; what is actually visible still depends on whether the corresponding official client wrote supported sources on this computer |
| Floating orb, tray, and native desktop dashboard | Windows x64 |
| Current release and clean-machine acceptance | Windows x64; macOS and Linux desktop artifacts have not been accepted yet |

## Metrics

- **Usage**: `total_tokens`, used for local window statistics.
- **Output**: `output_tokens`, which already includes reasoning and is not added again.
- **Quota bar**: account-level `used_percent` returned by the provider, which may include other devices.

These numbers explain local activity and official account remaining quota. They
do not replace the provider's bill and must not be used for cost or invoice
reconciliation.

## Development checks

Full tests on Windows verify pinned official WebView2 WPF files. Prepare that
runtime before the first run, then run tests:

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_native_shell.ps1
python -m unittest discover -s tests
$env:PYTHONUTF8='1'; python -m tools.audit
```

Windows CI also builds a wheel, installs it into an empty virtual environment,
and runs `tests/smoke_installed.py` from outside the source tree. That gate uses
generic official-client directory fixtures to verify current-account discovery
for all three providers, isolation from third-party Codex / community Grok data,
and byte-for-byte invariance of provider directories.

Windows installer and portable builds use the same self-contained desktop
directory. Internal candidates can run:

```powershell
python -m pip install -r tools/wheel-build-requirements.txt
python -m pip install -r tools/windows-release-requirements.txt
.\tools\build_windows_release.ps1 -NsisCompiler C:\path\to\makensis.exe
```

The script writes a per-user NSIS installer, portable ZIP, and CycloneDX runtime
SBOM that are explicitly labeled `unsigned`, and records them together in
`SHA256SUMS.windows`. The current public preview is explicitly labeled
`unsigned`. Whether it enters a stable channel is decided by actual install,
update, and uninstall acceptance, not by waiting for a third-party code-signing
service.

Parser changes must increment `sandglass.models.RECORD_FORMAT` at the same time
so old caches do not keep returning old semantics.

## Release and update integrity

**Windows release binaries are not Authenticode-signed.** Installing one may
show an unknown-publisher warning, and Smart App Control may refuse it
outright. That is the honest state; nothing here claims otherwise.

The update path does not require a certificate or a third-party signing
account. Every release publishes
`SHA256SUMS.windows` alongside the installer, and the manifest carries an
ECDSA P-256 signature made with a key the maintainer holds offline. An
installed copy accepts an update only when the download matches the manifest
and the manifest carries that signature. Verification uses Windows CNG; there
is no additional dependency and no hand-written cryptography. The private key
is never in this repository and never on a CI runner, so publishing a release
is an act a person performs.

- Authors, committers and reviewers: [taiyun668](https://github.com/taiyun668)
- Release signing approver: [taiyun668](https://github.com/taiyun668)
- Privacy policy: [`PRIVACY.md`](PRIVACY.md)

Windows binaries are built from this repository's public `main` branch on
GitHub-hosted Actions runners. The release and updater trust sequence is
documented in [`docs/release-integrity.md`](docs/release-integrity.md).

## License

Sandglass source code is under the [MIT License](LICENSE). Bundled Geist fonts
remain under the
[SIL Open Font License 1.1](sandglass/web/fonts/LICENSE-Geist.txt).
Provider names and hand-drawn recognition icons in the UI are used only to
identify compatible products and do not imply affiliation or endorsement by
those providers.
