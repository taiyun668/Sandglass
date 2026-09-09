# Windows release and update integrity

Sandglass uses the same lightweight split as other unsigned community desktop
applications: GitHub publishes a per-user NSIS installer and a portable ZIP,
while the application verifies a detached publisher signature before applying
an automatic update. No certificate, signing-service account or CI secret is
required.

## Release artifacts

`tools/build_windows_release.ps1` produces the unsigned installer, portable
ZIP, runtime SBOM and `SHA256SUMS.windows`. When the Owner's offline release key
is available on the release machine, the build also writes
`SHA256SUMS.windows.sig`. The private key is never committed and never copied
to a GitHub-hosted runner.

The GitHub release must contain the installer, portable ZIP, runtime SBOM,
checksum manifest and detached manifest signature. The checksum proves the
downloaded bytes match the release manifest; the ECDSA P-256 signature proves
that manifest was authorized by the same Owner key embedded in installed
copies.

## Automatic update decision

The updater offers only a newer non-prerelease version with the exact expected
installer and manifest assets. It verifies the manifest signature with Windows
CNG, requires exactly one matching SHA-256 entry, downloads the installer,
hashes it again and only then starts the visible `/UPDATE` handoff. A missing or
invalid signature, ambiguous checksum entry or changed installer is rejected.

This detached signature is release integrity, not Windows publisher identity.
The binaries remain unsigned community packages, so Windows may show an
unknown-publisher warning and an enforced application-control policy may block
them before Sandglass starts. That external policy result does not add a
certificate requirement to the Sandglass build or updater.

## Release boundary

A release is admitted by product lifecycle evidence: the published artifacts
match their signed manifest, the installer and portable bundle launch on a real
interactive Windows desktop, update and restart preserve user state, uninstall
cleans product-owned paths, and provider directories remain byte-for-byte
unchanged. Windows code signing can be reconsidered independently in the
future; it is not part of the current release gate.
