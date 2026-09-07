# Sandglass v2 user-source interface

Read this reference only when a candidate evidence ledger is ready to preflight,
persist, inspect, replace, or roll back. The product interface owns exact schema
validation and atomic state changes; do not reimplement them in prose or ad hoc
shell commands.

## Resolve Sandglass

Prefer the installed file interface. Resolve one Sandglass launcher, verify that
it is the intended product, and use the same launcher for the whole execution.

```text
sandglass user-source preflight package.json
sandglass user-source commit package.json
sandglass user-source status --source stable.local-source
```

The file interface avoids shell reserialization and does not require a dashboard
process. Use HTTP only when the same loopback base exposes the v2 import routes,
`/api/user-source-imports`, `/api/user-sources/official-minutes`, and
`/api/attribution-diagnostics`, and its runtime self-report identifies the
expected product.

If required, start only a temporary Sandglass-owned offline service and stop
only the process this execution started:

```text
sandglass --offline serve --host 127.0.0.1 --port 7740 --no-browser
```

Do not scan arbitrary ports, accept a look-alike local service, terminate a
process to claim port 7740, or stop a pre-existing Sandglass instance. If neither
the file interface nor a verified loopback API is available, persistence is
blocked.

When using the HTTP fallback, probe it deterministically before building the
package:

```text
python scripts/v2_import.py --probe --endpoint http://127.0.0.1:7740
```

The preflight and commit routes are POST-only. A GET 404 on
`/v2/user-source-imports/preflight` says nothing about whether its POST route
exists. Do not hand-probe it with GET, PUT, PATCH, or guessed path variants; the
script verifies the exact POST route with a no-write invalid-package preflight
and checks that import status is unchanged.

## One complete package

Write one UTF-8 JSON package with exactly these top-level fields:

```json
{
  "source": "stable.local-source",
  "revision": "immutable-revision-id",
  "receipts": [],
  "accounts": [],
  "quotas": [],
  "records": [],
  "expected": {
    "receipts": 0,
    "accounts": 0,
    "quotas": 0,
    "records": 0,
    "total_tokens": 0
  }
}
```

The package contains:

- sanitized native receipts that preserve authorized shape, stable native event
  identity, and unknown fields for every accounting event used by the package;
- secret-free provider-scoped accounts;
- already-authorized official quota results, never credentials;
- normalized identity-completion or Token records; and
- independently counted expected rows and `total_tokens`.

Each normalized record carries provider, stable event id, timezone-qualified
timestamp, session id, non-negative Token buckets, calls, and a positive
`total_tokens` equal to input + output + cache-read + both cache-write buckets.
Include an account only when evidence proves it. Unknown normalized fields are
rejected rather than discarded.

A receipt that contains only source name, row count, coverage, totals, or parser
decisions is an audit summary, not the native evidence. It may accompany the
receipts, but it cannot replace the event-bearing native projections. Every
normalized event must remain traceable to a persisted receipt carrying its
stable native event/request identity and the fields used to interpret it.

An adapter may use a locally available official credential to call the
provider's official quota endpoint only with owner authorization. It must not
refresh, rewrite, move, export or persist the credential. Persist only the
account-scoped result.

For A-class identity completion, page through
`/api/user-sources/official-minutes` and copy provider, session, UTC minute and
every Token bucket exactly; add only the proved identity. For B-class Token
evidence, submit direct billing events absent from the official ledger. Never
turn an existing official minute into additive Token.

## Required operation order

1. Build and independently count the complete package.
2. Run preflight. It performs no writes; every blocker is an unresolved package
   or evidence relation.
3. Commit the exact preflighted file atomically.
4. Read status and verify active revision, canonical hash, counts, accounts,
   quotas and Token totals.
5. Exercise retained-revision rollback or deactivation and verify every view
   moves together.
6. Restore the intended revision and recheck status.

`source + revision` is immutable. An identical retry is idempotent. Changed
content uses a new revision. Replacement and rollback use the exact active
revision reported by status as their compare-and-swap expectation. Never evade
a blocker by inventing another source id, write partial stages separately,
delete evidence to imitate rollback, or bake user-source rows into
`cache.sqlite`.

The HTTP fallback exposes the same deterministic lifecycle:

```text
python scripts/v2_import.py package.json --commit
python scripts/v2_import.py package-v2.json --commit --replace \
  --expected-active-revision revision-1
python scripts/v2_import.py --status-source stable.local-source
python scripts/v2_import.py --rollback-source stable.local-source \
  --expected-active-revision revision-2 --target-revision revision-1
python scripts/v2_import.py --rollback-source stable.local-source \
  --expected-active-revision revision-1 --target-revision revision-2
```

Deactivation uses `--deactivate` instead of `--target-revision`. A completed
rebuild exercises a retained-revision rollback and restores the intended
revision; leaving a single untested revision active is not rollback verification.

Use [`../scripts/v2_import.py`](../scripts/v2_import.py) only as the deterministic
HTTP fallback when the verified file interface is unavailable. It validates the
complete top-level v2 shape and loopback destination, then calls product
preflight or commit; Sandglass remains the schema and transaction authority.

## Product controls remain separate

Persistence does not enable product use. The owner separately chooses whether
the source is displayed, supplies verified identity, adds otherwise-unseen Token
to totals, or participates in full-window inference.

Identity completion may move an exactly reconciled existing minute from
unassigned to a proved account but cannot add Token. A Token candidate may be
included only when absent from the official ledger and free of official,
cross-session and cross-source collisions. Full-window inference is a separate
authorization. Every enabled stage remains source-labeled and reversible, and
every derived numerator continues to name its sources.
