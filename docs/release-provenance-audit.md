# Sandglass release provenance audit

Status: `PUBLIC_RELEASE_BLOCKED`  
Audited: 2026-08-31
Evidence base: official-only boundary plus local Windows candidate evidence

This document records what can and cannot currently be proven about the assets,
the removed price-estimate subsystem, and first-party quota endpoints shipped with Sandglass. The
project owner selected the MIT license on 2026-08-28; brand provenance remains a separate release concern.

## Release blockers

1. The future public repository must enable private vulnerability reporting.
2. The Windows installer and portable ZIP pipeline exists, but owner-controlled
   signing, signed updates and clean-machine gates remain incomplete; see
   `docs/release-checklist.md`.

The privacy, security, contribution and supported-scope policies now exist. All
three subscription quota URLs are explicitly documented as optional, observed
first-party client endpoints rather than stable public integration contracts;
each degrades to local usage without guessed quota data.

## Windows artifact pipeline

The first Windows shape is now a per-user installer plus a portable ZIP. Both
are built from the same PyInstaller one-directory bundle so the installed and
portable programs have identical application bytes. The build pins every
resolved Windows runtime dependency in `tools/windows-runtime-constraints.txt`,
pins the release interpreter to CPython 3.13.15, pins the wheel frontend to
`build==1.6.0` and backend to `setuptools==84.0.0`, and pins
`PyInstaller==6.22.2`. It prepares the pinned Microsoft WebView2 WPF files, scans
the bundle for credentials, runtime data and removed private-source markers,
runs a packaged dependency self-test, generates a reproducible CycloneDX 1.6
runtime SBOM with pinned `cyclonedx-bom==7.3.1`, and writes SHA-256 checksums.
The SBOM generator runs outside the clean target environment after bootstrap
`pip` is removed and before PyInstaller or other build-only tools are installed
there, so packaging tools cannot masquerade as shipped runtime dependencies.
The same pipeline now collects the exact license and notice files for every
SBOM runtime component plus CPython, the PyInstaller bootloader, WebView2 and
NSIS. `THIRD_PARTY_LICENSES/manifest.json` hashes every copied text, and the
bundle gate rejects a missing component, missing file, changed hash, or an
unreviewed runtime dependency/version before creating release archives.

Every Windows build now refuses a dirty tracked worktree and embeds
`Sandglass-build-provenance.json` in the application bundle. The manifest binds
the full Git commit, project version, clean-worktree state and SHA-256/byte size
of `index.html`, `i18n.js` and the native panel bridge into one build ID. Bundle
inspection recomputes those hashes and the build ID, so a stale copied resource
or relabeled executable fails before archives are created. The packaged runtime
uses the same manifest in `/api/attribution-diagnostics`, allowing an installed
build with no `.git` directory to self-identify.

The current local unsigned candidate was rebuilt from clean commit
`e7a2800b791774417b3a422a4be6a51d1dba2153` with build ID
`a34312bc1ca39e315c5ceab856b44c645725dd0e3f6d0f1f7969a9cc9115f6f2`.
Its artifact SHA-256 values are:

| Local candidate | SHA-256 |
| --- | --- |
| `Sandglass-0.1.0-windows-x64-unsigned-portable.zip` | `2F5BF625E27592BCD2E8F1C4FCAF293CCDB947099C052628004175AD401241FA` |
| `Sandglass-0.1.0-windows-x64-unsigned-setup.exe` | `4F14A2D11853200E7BFCD23D24E745AD39CF6369AE6BA4FFCBC6ECE544EF070E` |
| `Sandglass-0.1.0-windows-x64-runtime.cdx.json` | `556706D17C12F79586D16F6B441DABEADAC90848CCEB1C07D71382CF17CBFF84` |

This is local build evidence only. Both executable and installer remain
`NotSigned`, runtime/installer smoke was deliberately skipped under the enforced
SAC policy, and no artifact has been uploaded or released.

`proxy_tools==0.1.0` is the one upstream metadata conflict found in this pass:
its PyPI metadata declares MIT, while the official source repository ships a
BSD-form license text and omits that text from both the wheel and source archive.
Sandglass therefore preserves the official text byte-for-byte, labels the
conflict rather than guessing a replacement license, and pins source commit
`db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6` plus license SHA-256
`F96CF2B17C9B0CEDE77438165FDC6EA2F91BEDB248D1779538727A2B93D71E12`.

The installer compiler is NSIS 3.12, whose official release installer SHA-256
is `3BC2B06253A7E4957111BE152AC6A536E0C7478A706E19DA814038DB5D706495`.
NSIS was selected after an Inno Setup 7.1.0 trial build disclosed a
non-commercial-use compiler license; that restriction was incompatible with a
public MIT build route that should remain usable by commercial downstreams.
The Inno script is not part of the repository.

The current local candidates are intentionally named `unsigned`. On
2026-08-30 this machine's active Windows Code Integrity policy blocked the
newly built `Sandglass.exe` before process startup. Operational events 3033 and
3077 directly reported that the executable did not meet the Enterprise signing
level for policy `{0283ac0f-fff1-49ae-ada1-8a933130cad6}`. This proves both the
external diagnostic path and the remaining signing blocker; it is not a passed
runtime acceptance result.

A separate owner-supplied, read-only acceptance run on the same enforced Smart
App Control machine launched the official Flameshot 14.0.0 portable release
after its hash matched the upstream checksum and its SignPath Foundation OV
Authenticode signature validated. The file carried a browser-download Mark of
the Web and produced no new Code Integrity block event. This is direct evidence
that SignPath Foundation is a viable signing-route candidate for SAC on this
machine; it is not evidence that an unsigned Sandglass candidate passes, that a
future Sandglass signature has already been issued, or that a new project has
SmartScreen reputation. SignPath's published terms also require an already
public, actively maintained open-source project, so remote publication remains
a prerequisite rather than an action this local build can complete.

## Asset inventory

| Shipped files | Evidence | Current conclusion | Required before release |
| --- | --- | --- | --- |
| `web/assets/app-icon.png`, `app.ico`, `logo-mark.png`, `orb.ico`, `web/favicon.png` | Replaced on 2026-08-28 from artwork supplied by the project owner for Sandglass. The transparent mark, favicon and Windows size variants were derived locally from that source. | This is now the independent Sandglass mark; the inherited GOGO bitmaps are no longer shipped. Source and derivative hashes are recorded below; the untrimmed source is not shipped. | Complete. |
| `sandglass/web/fonts/Geist-Variable.woff2` | Byte-identical to `geist-font/Geist/webfonts/Geist[wght].woff2` in Vercel's official `v1.7.2` release asset. The bundled OFL text is also byte-identical to that asset's `geist-font/OFL.txt`. | Exact upstream release, commit, archive, archive hash, member path and binary hash are pinned below. The font remains OFL-1.1. | Complete; keep the hashes and license test as release gates. |
| `web/icons/ProviderIcon-claude.png`, `ProviderIcon-codex.png`, `ProviderIcon-grok.png` | Replaced on 2026-08-28 with hand-drawn artwork supplied by the project owner, cropped to transparent, consistently padded 256 px assets. | The inherited provider SVG files are no longer shipped. Source and derivative hashes are recorded below. These images are navigational provider references, not a claim of affiliation. | Complete; keep the public non-affiliation statement. |

Asset hashes captured during this audit:

| File | SHA-256 |
| --- | --- |
| `app-icon.png` | `F5E9001EA5212F8FF421B4980DB28C8C09C8D3D8875F511997435C9F183C701E` |
| `app.ico` | `D0B9AFC223616CC30C2064224A8D39430B0C7111972444698FD965996EB89E0F` |
| `logo-mark.png` | `FC91ADD1A3C1ED92294326150C58E45C75B190308A65D489625AFA5F1DDCB310` |
| `orb.ico` | `71E10A5D7842C93B2643B6567E589430C9A46531B2038BB279896E5141CD4580` |
| `favicon.png` | `DDC36DB09D3CC29548E05857999B384F90D36E260F14000A8EBB1E47B41933F8` |
| `Geist-Variable.woff2` | `A369FCF5628EA2AA4E1B9E2EC6A5B3624E365BDA588E1F0F2F12B564F728FBB8` |
| `ProviderIcon-claude.png` | `48F4D1B7DC2B2BD261F18EB5258F9216E11623714B6457C62F98FCD1EECC87B6` |
| `ProviderIcon-codex.png` | `EB00347C6A28A1374F3B4E5AA03E24C481A383540C4C016845AF9AAF6BF97059` |
| `ProviderIcon-grok.png` | `CF69635CDBD654C9D0FE1813E4EF5B6655E3806C830B326A5EE9FBACC305C7CB` |

Owner-supplied source-image evidence (original images are intentionally not
shipped in the public package):

| Source role | SHA-256 |
| --- | --- |
| Sandglass hourglass artwork | `A69D9273F10C9CD2AAE4451C064B5BE816E17E7EE06FEF5349907C6593CCB742` |
| Hand-drawn provider artwork sheet | `4A625092F4624797965CB8F82F5CB8710CEBDD09CA60236AFCCD23CA824DEC53` |
| Owner-supplied Codex replacement artwork | `E3C5FCBF4EE510EAF0ACEB635C337D82F1C21638D7F91AD7AB84AFA3890DEAA0` |

### Pinned Geist source

- Upstream: Vercel [`geist-font`](https://github.com/vercel/geist-font)
- Release: [`v1.7.2`](https://github.com/vercel/geist-font/releases/tag/v1.7.2)
- Tag commit: `a73329da8fc62afc917f796555202e4997f79b7c`
- Asset: [`geist-font-v1.7.2.zip`](https://github.com/vercel/geist-font/releases/download/v1.7.2/geist-font-v1.7.2.zip)
- Asset SHA-256: `7FC800D2AC6B92844895196E5041ACA55D814C15DB70C44F79B3B83AB82B04E2`
- Font member: `geist-font/Geist/webfonts/Geist[wght].woff2`
- Font SHA-256: `A369FCF5628EA2AA4E1B9E2EC6A5B3624E365BDA588E1F0F2F12B564F728FBB8`
- License member: `geist-font/OFL.txt`
- License SHA-256: `C683BFBCC7E087F5D37A54EF628F10387C451A83DDC459B151403A164AC46C90`

The source audit downloaded all 25 ZIP assets exposed by the official GitHub
releases API on 2026-08-29 and compared 665 WOFF2 members. The bundled font had
exactly one byte match: the member above. This pins provenance without adding an
upstream archive to the Sandglass repository.

## Removed retail-price estimate inventory

Owner decision on 2026-08-28: the public product does not show dollar estimates.
The stale table and all dollar output fields were removed from collectors, cache
records, reports, local-window APIs, CLI output and the dashboard. Git history
retains the mismatch evidence that led to this decision.

Decision resolved: retail-dollar output was removed from the public product.

## Quota endpoint inventory

| Provider | Sandglass GET endpoint | What is directly proven | Contract status and degradation |
| --- | --- | --- | --- |
| Claude | `https://api.anthropic.com/api/oauth/usage` | The official client credential can currently return account quota windows. Sandglass reads the access token and does not refresh or write it. | No public Anthropic integration documentation for this exact endpoint was found. The documented organization Usage/Cost and Claude Code Analytics APIs require admin-style credentials and are not a replacement for an individual's subscription quota. Treat the current URL as an observed client endpoint: on failure, retain local token usage and mark official quota unavailable. |
| Codex | `https://chatgpt.com/backend-api/wham/usage` | The current official ChatGPT/Codex credential can return account windows and credits. Sandglass performs a GET and does not refresh or write auth state. | No official OpenAI developer documentation for this exact endpoint was found. Treat it as an observed first-party client endpoint, not a supported public API contract; fail closed to local usage only. |
| Grok | `https://cli-chat-proxy.grok.com/v1/billing?format=credits` | The current official Grok Build CLI session key can return billing windows. Sandglass reads only the official CLI auth file and does not refresh or write it. | No public xAI integration documentation for this exact endpoint was found. Treat it as an observed official-client endpoint; on expiry or schema failure, direct the user back to the official client and preserve local usage only. |

## Resolved source-boundary defect

Earlier code mislabeled the community `RongleCat/grok-app` account store as an
official desktop source. Account discovery, attribution, plan fallback, reverse
signals and legacy per-profile quota-cache rows have been removed. A new
`grok-official-identity-runs.json` ledger intentionally does not migrate the old
mixed ledger because official CLI observations cannot be separated reliably from
community profile events after the fact.

The absence of a public contract does not make these values fabricated: the
responses are first-party account data. It does mean availability and schema are
compatibility risks, so a public adapter must declare `official_quota` separately
from `local_usage` and `account_discovery`.

## Resolved owner decisions

The project owner selected the MIT license on 2026-08-28. `LICENSE` carries the
project grant and `sandglass/web/fonts/LICENSE-Geist.txt` preserves the Geist font's
OFL-1.1 terms.

The remaining narrow implementation order is Windows packaging/signing and a
clean-machine acceptance pass. The future public repository must also enable
private vulnerability reporting. Public release remains blocked until those
gates are closed.
