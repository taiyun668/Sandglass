# Model and API metering contract

Read this reference only when the requested source is a model vendor API,
gateway, proxy, router, local model host, or another source whose native usage
is request- or time-bucket-based rather than a Sandglass product session.

## Preserve the native accounting scope

Keep these facts separate:

- `provider`: the service that issued the measurement or bill;
- `upstream_provider`: the model maker behind a router, when directly known;
- `service`: the measured product surface, such as `api` or `gateway`;
- entity hierarchy: account, organization, workspace, team, project, API-key
  ID, or endpoint;
- model, operation, and non-secret grouping dimensions;
- the original request or aggregate interval; and
- each independently reported metric.

An API-key entity contains only a provider-issued key ID or a local irreversible
fingerprint, never the key itself. Credentials, authorization headers, prompts,
completions, tool content, and raw user content are forbidden in the package.

## Package shape

Use one v3 package:

```json
{
  "contract_version": 3,
  "source": "stable.local-source",
  "revision": "immutable-revision",
  "receipts": [{"sanitized_native_bucket": "preserved"}],
  "entities": [{
    "provider": "provider-id",
    "entity_id": "project-id",
    "kind": "project",
    "parent_entity_id": "organization-id",
    "label": "Project label",
    "plan": "api"
  }],
  "observations": [{
    "observation_id": "stable-native-bucket-id",
    "provider": "provider-id",
    "upstream_provider": "",
    "service": "api",
    "kind": "usage",
    "entity_id": "project-id",
    "start_at": "2026-08-31T00:00:00Z",
    "end_at": "2026-08-31T01:00:00Z",
    "granularity": "hour",
    "model": "model-id",
    "operation": "responses",
    "authority": "official_usage",
    "coverage": "complete",
    "resets_at": "",
    "dimensions": {"service_tier": "default"},
    "metrics": [
      {"name": "gen_ai.tokens.total", "value": "150", "unit": "{token}"},
      {"name": "gen_ai.requests", "value": "3", "unit": "{request}"}
    ]
  }],
  "expected": {
    "receipts": 1,
    "entities": 1,
    "observations": 1,
    "metrics": 2,
    "total_tokens": 150
  }
}
```

All entity parents used by this revision must be included in `entities`.
Supported observation kinds are `usage`, `cost`, `balance`, and `quota`.
Usage and cost observations describe deltas over their interval; balance and
quota observations are gauges at the stated time. Preserve official request,
minute, hour, day, billing-period, or instant granularity instead of reshaping
it.

Metric values are non-negative integers or decimal strings. Currency uses its
three-letter unit. Do not calculate a monetary estimate from a price table and
label it official; `billing.cost` is official only when supplied by a billing
ledger. Keep cost observations separate from usage observations when the
provider exposes separate ledgers.

## Token invariant

Every observation with Token breakdown fields must also carry
`gen_ai.tokens.total` as the source-defined total. Sandglass uses that value and
does not add the breakdown fields. This is essential because some APIs include
cache-read Token inside input Token and reasoning Token inside output Token.

The expected `total_tokens` is the sum of only `gen_ai.tokens.total` on usage
observations. It is not the sum of input, output, cache, and reasoning metrics.

## Reconciliation

An exact observation repeated under a different source ID is rejected. Two
different sources may be stored when they overlap, because one may be an
official aggregate and the other a partial client observation; however,
Sandglass reports that overlap and excludes both from a merged accounting
candidate until their coverage relation is resolved. Per-source statistics
remain visible.

Never add a gateway bucket to an upstream official bucket merely because their
totals differ. First establish whether the gateway executed distinct work,
forwarded the same work, or covers only an interval the official source cannot
cover. Preserve the relation and source labels with the observation.

Use the ordinary file commands. They detect v3 from `contract_version`:

```text
sandglass user-source preflight package.json
sandglass user-source commit package.json
sandglass user-source status --source stable.local-source
```

The equivalent loopback transport is under `/v3/user-source-imports/*`, and the
active source-scoped view is `GET /api/model-api-metering`.
