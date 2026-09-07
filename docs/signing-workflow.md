# Windows signing workflow

Status: locally prepared, not connected to SignPath, no signing credentials.

## Why the workflow has two signing requests

The NSIS installer contains an application directory and an uninstaller, but an
NSIS executable is not a deep-signing container supported by SignPath. NSIS
3.08+ can instead export the generated uninstaller for an external signing
workflow and then import the signed file into the final installer.

The release sequence is therefore:

1. Build and inspect the unsigned Sandglass onedir bundle.
2. Run `tools/prepare_windows_signing.ps1`. It exports `Uninstall.exe` and
   creates a deterministic ZIP containing the whole bundle plus the uninstaller.
3. Submit that ZIP to SignPath. The artifact configuration must preserve and
   verify already-valid Microsoft/PSF signatures and Authenticode-sign every PE
   that is not already valid.
4. Run `tools/build_signed_nsis.ps1` on the returned ZIP. It refuses any bundle
   or uninstaller PE without a valid signature, verifies build provenance, and
   embeds the signed uninstaller into a new unsigned outer NSIS installer.
5. Submit the outer installer executable to SignPath for Authenticode signing.
6. Run `tools/smoke_windows_installer.ps1 -RequireSigned` on the returned
   installer. It requires valid installer, installed `Sandglass.exe`, and
   installed `Uninstall.exe` signatures before the ordinary install/uninstall
   and provider-directory invariance checks.

This uses two external signing requests, not three: application PE files and the
exported uninstaller are signed together in the first ZIP.

## Current local inventory

Measured 2026-09-07 on the `9e148f9` unsigned bundle: 145 PE files, 130 with
valid upstream Microsoft or Python Software Foundation signatures, 15 unsigned.
The unsigned set is `Sandglass.exe` plus native modules from cffi, protobuf,
Pillow, pythonnet, clr-loader and pywebview. Adding the exported uninstaller
makes 16 PE files that need an embedded signature in the first request. The
inner request ZIP built from that bundle contains 146 PE files (145 +
`Uninstall.exe`). The older 188/173 count was a previous bundle shape.

Do not blindly replace the 173 upstream signatures. The SignPath artifact
configuration must distinguish existing valid upstream PE files from unsigned
files that require the Foundation signature. Re-run the inventory whenever a
pinned dependency or bundle shape changes.

## Future GitHub/SignPath connection

Open Source Code Signing requires a SignPath project linked to the GitHub trusted
build system with origin verification. The signing workflow must use
GitHub-hosted runners, upload the unsigned input as a GitHub Actions artifact,
and pass its artifact ID to
`signpath/github-action-submit-signing-request@v2`. Organization ID, project
slug, signing policy slug, artifact configuration slug and API token are external
configuration and must not be invented or committed.

No SignPath request is authorized until the source repository is intentionally
published, the project satisfies the Foundation release/maintenance conditions,
and the owner approves the external connection. A local unsigned build or an
empty GitHub repository does not satisfy those gates.
