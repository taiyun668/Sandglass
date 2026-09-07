# Contributing to Sandglass

Sandglass is a read-only, local usage recorder. A contribution is acceptable only
when it preserves that product boundary: official local provider evidence in,
truthful local records out.

## Before changing code

Read [`AGENTS.md`](AGENTS.md) and the
[`provider source map`](docs/provider-source-map.md). Determine which provider
record directly proves the fact being added. Current-login state, quota percentage,
time proximity and a third-party account manager are not historical ownership
evidence.

Do not submit features that switch accounts, refresh credentials, import browser
cookies, estimate missing usage or write inside provider directories. Those belong
outside the Sandglass public product.

## Development checks

Use Python 3.10 or newer. From the repository root:

```powershell
python -m unittest discover -s tests
python -m sandglass serve --no-browser
$env:PYTHONUTF8='1'; python -m tools.audit
```

Windows native-panel tests also require:

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_native_shell.ps1
```

Parser changes must update fixtures and increment `sandglass.models.RECORD_FORMAT`.
Changes to identity, attribution or quota semantics need an independent test that
would fail under the old behavior.

## New providers and sources

A provider adapter must:

1. document account discovery, local usage and official quota as separate capabilities;
2. use official local records or a first-party quota channel;
3. state the evidence grade, coverage start and failure behavior;
4. keep unprovable history unassigned;
5. add a runtime test that snapshots provider directories and proves byte-for-byte invariance after discovery, collection and quota reads.

Update `docs/provider-source-map.md` before wiring the new source into the product.

## Issues and pull requests

Keep changes focused and explain the direct source plus the measurement that can
disprove the implementation. Include test output and note any skipped acceptance
step. Never attach real auth files, tokens, prompts, account emails, raw provider
logs or a user's `SANDGLASS_HOME` database. Report security issues through the
private process in [`SECURITY.md`](SECURITY.md), not a public issue.
