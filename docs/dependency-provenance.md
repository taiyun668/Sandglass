# Shipped dependency provenance

This file records upstream identity for the few bundled assets whose package
metadata alone is insufficient. It is supply-chain information for Sandglass,
not a record of the maintainer's development environment.

## Geist variable font

- Upstream: Vercel [`geist-font`](https://github.com/vercel/geist-font)
- Release: [`v1.7.2`](https://github.com/vercel/geist-font/releases/tag/v1.7.2)
- Tag commit: `a73329da8fc62afc917f796555202e4997f79b7c`
- Asset: [`geist-font-v1.7.2.zip`](https://github.com/vercel/geist-font/releases/download/v1.7.2/geist-font-v1.7.2.zip)
- Asset SHA-256: `7FC800D2AC6B92844895196E5041ACA55D814C15DB70C44F79B3B83AB82B04E2`
- Font member: `geist-font/Geist/webfonts/Geist[wght].woff2`
- Font SHA-256: `A369FCF5628EA2AA4E1B9E2EC6A5B3624E365BDA588E1F0F2F12B564F728FBB8`
- License member: `geist-font/OFL.txt`
- License SHA-256: `C683BFBCC7E087F5D37A54EF628F10387C451A83DDC459B151403A164AC46C90`

The shipped font is covered by the SIL Open Font License 1.1 stored beside it.

## proxy_tools 0.1.0

PyPI metadata declares MIT, while the official source repository carries a
BSD-form license text and omits that text from its wheel and source archive.
Sandglass preserves the official license text byte-for-byte and records the
metadata conflict instead of guessing a replacement license.

- Source commit: `db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6`
- License SHA-256: `F96CF2B17C9B0CEDE77438165FDC6EA2F91BEDB248D1779538727A2B93D71E12`

The Windows bundle's complete dependency and license inventory is emitted as
the runtime CycloneDX SBOM and `THIRD_PARTY_LICENSES/manifest.json`.
