# User-source interface

Sandglass's release boundary decides which sources the project bundles and
supports by default. It does not decide what evidence a user may inspect or use
on their own computer.

The interface is therefore not a closed usage schema. It consists of:

1. an agent-facing briefing in
   [`../skills/sandglass-adapter/SKILL.md`](../skills/sandglass-adapter/SKILL.md);
2. a loopback-only inbox for native JSON evidence;
3. a strict, secret-free account-discovery manifest;
4. a strict normalized JSON view for exact minute reconciliation;
5. a mirror that reports what Sandglass recognized, what remains unknown, and
   the source's current product-use stage.

## Model and API metering import (v3)

The local product ledger and an API billing ledger are different objects. A
consumer account may have rolling subscription windows and minute-level local
sessions; an API platform may instead report an organization, team, workspace,
project or API-key ID using request, minute, hour, day or billing-period buckets.
Sandglass does not manufacture sessions or minutes to make those shapes look
alike.

The v3 package contains sanitized native receipts, a secret-free entity
hierarchy, native-granularity observations, and an independently counted
manifest. Observations distinguish the service issuing the measurement from an
upstream model provider and carry independent metrics for Token, requests,
official cost, balance, or quota. `gen_ai.tokens.total` is always supplied by
the source; Sandglass never derives it by adding breakdown fields, because
cache-read Token may already be part of input and reasoning Token may already
be part of output.

Use the same file commands as v2. The package's `contract_version: 3` selects
the metering validator. Equivalent HTTP routes are
`POST /v3/user-source-imports/preflight`,
`POST /v3/user-source-imports/commit`, and
`POST /v3/user-source-imports/rollback`. Active observations are exposed at
`GET /api/model-api-metering`.

Exact cross-source observations are rejected even when their source-native IDs
differ. Non-identical aggregate sources that overlap remain visible per source,
but are excluded from any merged accounting candidate until their coverage
relation is resolved. V3 observations are not silently mixed into the existing
consumer-product report.

The full field contract and an example live in
[`../skills/sandglass-adapter/references/metering-v3.md`](../skills/sandglass-adapter/references/metering-v3.md).

## Local ledger transactional import (v2)

An adapter must hand Sandglass one complete revision, not four independently
successful requests. The package contains the native receipts, secret-free
accounts, quota observations, normalized records, and the adapter's own expected
counts and Token total:

```json
{
  "source": "stable.local-source",
  "revision": "2026-08-31.1",
  "receipts": [{"native_shape": "preserved verbatim"}],
  "accounts": [{
    "provider": "provider-id",
    "account_id": "provider-scoped-account-id",
    "label": "Local account"
  }],
  "quotas": [],
  "records": [{
    "provider": "provider-id",
    "event_id": "stable-event-id",
    "timestamp": "2026-08-31T00:00:00Z",
    "account_id": "provider-scoped-account-id",
    "session_id": "source-session-id",
    "input_tokens": 7,
    "output_tokens": 3,
    "reasoning_tokens": 1,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "cache_write_1h_tokens": 0,
    "calls": 1,
    "total_tokens": 10
  }],
  "expected": {
    "receipts": 1,
    "accounts": 1,
    "quotas": 0,
    "records": 1,
    "total_tokens": 10
  }
}
```

Use the file interface so a shell cannot silently reserialize timestamps or
large integers:

```text
sandglass user-source preflight package.json
sandglass user-source commit package.json
sandglass user-source status --source stable.local-source
```

The equivalent loopback endpoints are
`POST /v2/user-source-imports/preflight`,
`POST /v2/user-source-imports/commit`, and
`GET /api/user-source-imports?source=<source>`. Preflight performs no writes.
Commit repeats the validation and stores the complete canonical package plus one
active-revision pointer in a single SQLite transaction.

`source + revision` is immutable. Repeating identical bytes is idempotent;
reusing a revision for different content is rejected. Replacing an active
revision requires both `replace: true` and its exact
`expected_active_revision`, so a stale agent cannot overwrite newer work. Old
revisions remain retained. `POST /v2/user-source-imports/rollback` or
`sandglass user-source rollback` switches every read view together—receipts,
accounts, quotas, and normalized Token—or deactivates the source without
deleting its evidence.

The expected manifest is an auditable self-claim, not proof that the adapter
found every hidden fact. Sandglass independently recomputes it and rejects a
mismatch, duplicate normalized events, exact replay-shaped duplicates, and
collisions with another active user source. The active package is read at report
time and is never baked into `cache.sqlite`.

The `/v1/user-sources*` endpoints below remain a compatibility path for older
adapters. New local-ledger adapters and the Sandglass Skill use the v2
whole-package contract; model/API metering adapters use v3. V1 cannot provide
atomic replacement or whole-revision rollback.

## Legacy inbox (v1 compatibility)

While `sandglass serve` is running, or while the desktop receiver is explicitly
enabled, send JSON to `POST /v1/user-sources`:

```json
{
  "source": "my-local-tool",
  "payload": {
    "any_native_field": "is preserved",
    "total_tokens": 120
  }
}
```

Only `source` and `payload` are envelope fields. The payload may have any JSON
shape. Unknown fields are accepted and stored verbatim under `SANDGLASS_HOME`
in `user-sources.sqlite`; they are not silently discarded. Identical source and
payload pairs are deduplicated.

`GET /api/user-sources` returns the mirror. It includes recent original
payloads, recognized and unknown field names, and the current product-use stage.
Receiving evidence alone changes no report total, account ownership, quota
window, or full-window estimate. That isolation
is not proved by a status flag from the inbox itself: tests and `tools.audit`
inject a merge-shaped receipt and compare report accounting before and after.

## Normalized records

After preserving the native payload, an adapter that directly identifies local
accounts posts `source` plus an `accounts` array to
`POST /v1/user-sources/accounts`. Account identity is keyed by provider and
account id; email alone never merges identities across providers. Credentials,
tokens and credential paths are outside this contract. These accounts enter the
normal product account list with user-adapter provenance. A matching official
account row takes precedence. Help exposes a reversible per-source account
discovery control; turning it off removes those adapter-only accounts from the
list and disables dependent record identity without deleting evidence.

User adapters may use locally available official credentials to call official
quota endpoints when the user authorizes it. They may not refresh, rewrite or
export those credentials. `POST /v1/user-sources/quotas` accepts only an admitted
provider/account id, timezone-qualified retrieval time, plan, source version and
standard quota windows. The result remains source-labeled and reversible with
the discovered account; no credential enters Sandglass state.

After preserving the native payload, an adapter can page through the prompt-free
official minute ledger at
`GET /api/user-sources/official-minutes?provider=<provider>&since=<UTC>&until=<UTC>&offset=0&limit=1000`.
The response copies the exact provider, session, UTC minute and Token buckets
that Sandglass already counts. It supplies no missing identity.

For identity completion, the adapter joins those rows to its direct identity
evidence, adds a stable event id and an exact currently discovered account id,
then posts batches to `POST /v1/user-sources/records`. The JSON record contract
is defined in the adapter Skill. Unknown normalized fields, invalid timestamps,
negative or inconsistent Token buckets, and records without provider, event id
or session id are rejected. Replaying the same records is idempotent.

The Help view exposes four reversible controls. A user can hide or show a
source. A one-account source can be mapped to one currently discovered account;
a multi-account source instead enables the exact verified account id carried by
each normalized record. The two forms cannot turn a seven-account timeline into
a whole-source single-account guess.
Mapping may move an already-existing first-party minute from unassigned to that
account only when provider, session, UTC minute and every Token bucket match
exactly. It never adds Token, never replaces an existing first-party identity,
and clearing the mapping restores the prior attribution. The normalized mirror is
grouped by source id and reports exact local-minute matches, minutes absent from
the official local ledger, numeric mismatches, and account conflicts. A user may
separately include B-class minutes in totals. Only minutes absent from the
official ledger and free of official cross-session, user cross-session and
cross-source collisions are read into the report. Turning the control off
restores the official report. Full-window inference is a fourth, separate
authorization. It is available only after the source is mapped to an account or
its verified record identities are enabled, and it contributes admitted A-class
identity evidence or B-class Token evidence.
Turning it off leaves the admitted Token unchanged and only removes permission
to derive a full-window estimate.

The mirror also builds a candidate ledger; classification alone changes no
report. An exact match against an official local minute can only become an
identity candidate; its Token are never added again. Distinct events may coexist
in the same provider minute. An exact timestamp-and-usage signature repeated by
different user sources stays blocked rather than being added twice or silently
choosing a winner. Candidate summaries retain every source id they depend on so
later totals cannot lose provenance.

This is a structural candidate list; the user still makes the admission decision.
Every current report projection carries stable `evidence_sources`, including
account-local quota-window numerators. Reversible A-class identity
supplementation, B-class Token inclusion and the separate full-window
authorization are wired. An
account-local numerator that contains user-adapter evidence remains ineligible
for extrapolation unless every contributing user source has that fourth stage
enabled.

The normalized `sandglass.usage` OTLP event on `POST /v1/logs` remains available
as one optional path for sources that naturally have event-level Token data. It
is not the required model for all user sources, and its exact reconciliation is
exposed as `normalized_shadow` in the mirror rather than used as an admission
gate.

## Product progression

User evidence can eventually progress through four independently chosen uses:

```text
display as its own source -> enable verified identity -> include in totals -> use in full-window inference
```

Sandglass must explain what it can and cannot confirm at each step. It must not
silently promote evidence merely because familiar field names are present.
Every enabled use remains labeled as user-provided and must be reversible at
read time. User evidence is never baked into `cache.sqlite`.

Full-window inference has a stricter display contract than the first three
stages. A derived value continuously names every user source used in its local
Token numerator; an enable-time notice is not enough. Its audit compares the
measured window Token, source labels and permission before, during and after
authorization rather than trusting a status flag returned by the source store.

## Hard guarantees

- Observation is read-only. Adapters and collectors must not refresh
  credentials, switch accounts, or write provider/tool-owned directories.
- Real emails, account ids, session ids, paths, prompts, credentials and tool
  content are removed before fixtures, reports, or diagnostics leave the PC.
- User evidence remains in its own store so removing or disabling it restores
  the official report exactly.

The Skill's `scripts/v2_import.py` is only a deterministic v2 loopback transport
fallback. It does not validate the truth or completeness of a source; product
preflight remains the schema and transaction authority.
