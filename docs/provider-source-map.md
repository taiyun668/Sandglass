# Provider source map and stitching plan

Status: official release source map plus isolated user-source inbox, OTLP mirror and candidate ledger
Audited: 2026-08-30
Locally inspected clients: Claude Code 2.1.196, Codex CLI 0.149.0, Grok 1.0.5

Sandglass should stitch evidence that provider clients already emit. It should not
invent an account owner when a source does not identify one. Its own ledger is a
receipt, deduplication and coverage store, never an identity oracle.

## Evidence grades

| Grade | Meaning | Permitted product claim |
| --- | --- | --- |
| A | The same provider event, or a deterministic provider correlation key, carries both token usage and authenticated account identity. | Account-attributed local usage. |
| B | A provider record carries exact local token usage but no account identity. | Local usage, shown as unassigned unless an A-grade correlation exists. |
| C | A provider record proves the authenticated account at a point in time but carries no usage. | Identity observation only; it may join usage only through an exact provider key and covered interval. |
| D | A first-party account endpoint reports quota or account-wide activity. | Official account state; never local per-turn attribution. |

Current-login snapshots are not historical evidence. Time proximity, a rollout
path, and the account that happens to be active now must not promote B or C
evidence to grade A. Sandglass does offer one explicit, user-selected product
policy outside that evidence grading: in single-account official mode, every
provider's built-in official local records are shown under that provider's current
official account, even if older accounts are still discoverable. Those minutes
remain labeled `sandglass_policy:single_official_account`; they never become grade
A. The policy is a reversible read-time view: changing modes restores direct
evidence and unassigned gaps without rewriting the cache or either identity ledger.

Every multi-account, multi-tool, ledger rebuild, completion or update path starts
from `skills/sandglass-adapter/SKILL.md`. Direct official identity evidence and
exact user-adapter identity evidence continue to attribute local Token normally;
the skill is for the remaining relationship gaps, not a reason to discard facts
already proved.

The built-in list is a release boundary, not a user-extension boundary. A user
may send the native shape of another local source to the independent inbox in
[`custom-source-contract.md`](custom-source-contract.md). Unknown fields are
stored and surfaced rather than rejected. Sandglass reports what it recognized
and the current product-use stage; it does not promote familiar-looking fields
to an official grade. Display, account mapping, totals and full-window inference are separate
user choices, remain reversible, and never create an official quota/reset claim.
The candidate ledger treats an exact match with a first-party local minute only
as possible identity evidence and never adds its Token again. A user minute that
is absent locally may be explicitly included as B-class Token evidence; cross-source claims
for the same provider/session/minute are blocked. Different session ids in the
same provider minute are conservatively marked ambiguous rather than added.
Report totals, accounts, daily activity, blocks, sessions and account-local quota
windows carry stable source labels. B-class inclusion is reversible at read time
and accepts only candidate minutes with no official cross-session, user
cross-session or cross-source collision. Exact A-class identity matches can
reassign existing local minutes after an explicit account mapping; they never
change the provider total. A numerator containing user-adapter evidence remains
ineligible for full-window inference until every contributing source is mapped,
admitted and separately authorized for that use. The derived value continues to
name those sources and disabling authorization does not alter the underlying Token.

## Capability summary

| Provider | Best post-intervention source | Historical local usage | Historical account attribution | Official quota |
| --- | --- | --- | --- | --- |
| Claude | Official OTLP `api_request` logs with account attributes | `~/.claude/projects/**/*.jsonl` | Generally unavailable before OTel; current OAuth identity is only a snapshot | Observed OAuth usage endpoint, undocumented integration contract |
| Codex | Official OTLP `codex.sse_event` / `response.completed` logs | CLI/Desktop rollout JSONL | Official `auth.json` observations cover only the period after Sandglass starts observing | Prefer the supported app-server read surface only after proving it does not mutate vendor state; current direct endpoint remains unstable |
| Grok | Official OTLP `grok_code.api_request` logs | `~/.grok/sessions/**/updates.jsonl` | Official CLI auth events and inference events sharing a process id can provide covered intervals | Observed CLI billing endpoint, undocumented integration contract |

### Machine-readable capability state

`GET /api/quota` returns a top-level `providers` map for Claude, Codex and Grok
even when no account is discovered. Each provider declares `account_discovery`,
`local_usage` and `official_quota` independently. `supported` means the public
adapter exists; `available` means its direct evidence is present on this machine
now. The response also names the source, evidence grade, coverage and explicit
degradation. A discovered account never implies local history, and local history
never implies an account or quota. Only accounts carrying an official source flag
can make discovery or official quota available.

This fast endpoint detects local-usage availability from the same official log
roots consumed by the collectors. It does not count the OTLP shadow ledger while
that ledger remains excluded from report totals.

## Quota reset evidence

Sandglass never initiates a provider reset. It polls first-party quota state while
the local service is running and stores observations only under `SANDGLASS_HOME`.
Provider period changes and same-period refills are different evidence shapes:

| Provider/window | Normal period evidence | Reset that may leave the period unchanged | Product handling |
| --- | --- | --- | --- |
| Claude 5h | `resets_at` moves with the rolling window | Not observed as a supported shape | Use the official period start; an unexpected large same-period drop still fails closed to a reset anchor. |
| Claude 7d | Account-specific fixed weekly `resets_at` | A granted refill can reduce utilization without moving the fixed schedule | Compare consecutive utilization observations and cut local usage at the earlier poll when a refill or large drop is observed. |
| Codex 5h/7d | `reset_at` and `limit_window_seconds` define the new period; first use re-anchors it | Not observed as a supported shape | Prefer the changed official period. An unexpected large same-period drop is treated conservatively as a reset, not explained as a specific cause. |
| Grok weekly/monthly | `currentPeriod.end` or `billingPeriodEnd` identifies the period | Reset cards and provider-issued refills can lower `creditUsagePercent` without moving the period | Compare consecutive observations, anchor at the earlier poll, and stop full-window extrapolation. |

The billing responses do not identify whether a same-period Grok drop came from a
reset card, a provider-issued refill, or another official adjustment. Sandglass
records only the directly observed `refilled` or `drop` signal and never invents a
cause. A long polling gap makes hidden-reset windows unfit for extrapolation; it
does not fabricate a reset time inside the gap.

Quota snapshots use a 180-second cache for Claude and a 90-second cache for Codex
and Grok. Official authentication changes, Grok's
first-party app-cache balance recovery or period change, and a previously returned
Claude `resets_at` becoming due invalidate only that provider's cache and immediately
reread its quota source. Each propagation result is
recorded without account identifiers or endpoint error bodies under
`SANDGLASS_HOME/quota-signal-events.jsonl`.

## Claude

### Official OpenTelemetry: grade A after opt-in

Claude Code can export metrics and log events over OTLP. Standard event
attributes include `user.account_uuid`, `user.account_id`, `user.email`,
`organization.id` and `session.id`. The `api_request` event includes a request id,
model, input, output, cache-read and cache-creation token counts. This is the
preferred account-attributed stream because identity and usage originate in the
same official client event.

The stream is off by default and must be enabled before Claude Code starts. The
provider config directory remains read-only, so Sandglass may listen for this
stream and explain setup, but must not silently edit `~/.claude/settings.json`.
Prompt and tool content remain disabled; Sandglass needs token and identity fields
only.

Source: [Claude Code monitoring documentation](https://code.claude.com/docs/en/monitoring-usage).

### Local transcripts: grade B

`~/.claude/projects/**/*.jsonl` contains per-assistant-message usage. It is a
strong local total source and can be rescanned, but its schema is a client
implementation surface rather than a published compatibility contract. Normal
usage rows do not carry a stable authenticated-account identity. Resume and
subagent replay also require source-level deduplication.

`~/.claude.json`, configured Claude homes and `.credentials.json` can identify the
currently authenticated account. They do not prove who owned older transcript
rows. Credentials may be read in memory only for a read-only first-party request;
they must never be copied into Sandglass storage.

Implemented boundary: transcript rows without same-event identity remain
provider-level unassigned usage even when exactly one Claude account is currently
discoverable. Identity-bearing official OTLP rows are kept separately as grade A
evidence; current-login state never rewrites transcript history.

### Account-wide usage: grade D

`https://api.anthropic.com/api/oauth/usage` is an observed first-party client
endpoint, not a documented third-party integration contract. It may provide
subscription windows for the current OAuth account. On failure, preserve local
usage and mark quota unavailable. Organization Usage/Cost or Claude Code Analytics
APIs are separate admin-scoped products and are not substitutes for a personal
subscription account.

## Codex

### Official OpenTelemetry logs: grade A after opt-in

Codex's official source emits a `codex.sse_event` with
`event.kind=response.completed`, `conversation.id`, `user.account_id`,
`user.email`, model and token counts. The completion event includes input,
output, cached input, cache-write input, reasoning and total-token fields.
Reasoning is already part of output for Sandglass semantics and must not be added
again.

Codex supports OTLP/HTTP binary and JSON as well as OTLP/gRPC. Logs are the
required source: some Codex entrypoints have emitted token-bearing log events
without the equivalent metrics.

Sources:

- [Codex completion telemetry](https://raw.githubusercontent.com/openai/codex/main/codex-rs/otel/src/events/session_telemetry.rs)
- [Codex common identity attributes](https://raw.githubusercontent.com/openai/codex/main/codex-rs/otel/src/events/shared.rs)
- [Codex OTel config schema](https://github.com/openai/codex/blob/main/codex-rs/core/config.schema.json)

The exporter is configured in `~/.codex/config.toml`; Sandglass must not edit that
file. It may receive already-enabled telemetry or provide explicit instructions.

### Rollouts and local databases: grades B and C

`~/.codex/sessions` and `~/.codex/archived_sessions` contain token usage and are
the historical local-total source. Most historical rollout records do not carry
an authenticated account id.

Codex's local logging database can contain structured auth-manager observations
such as reloading auth for a particular account. These are exact identity points,
not a complete switch interval. Private SQLite schemas are implementation details
and must be capability-tested by version.

`~/.codex/auth.json` identifies only the current official login. It cannot assign
older rollouts and its secrets must never leave memory.

### Official identity observation: grade C

Sandglass public releases do not read `~/.codex/accounts/registry.json` or any
third-party account switcher. The official `~/.codex/auth.json` proves only the
account visible at the instant Sandglass observes it. Sandglass stores a
secret-free account snapshot and observation timestamp under `SANDGLASS_HOME`;
subsequent official identity changes form a covered interval. It does not use the
file's old modification time to claim time before Sandglass intervened.

The observation ledger is `SANDGLASS_HOME/codex-official-identity-events-v2.json`.
It is a schema-2 stream of strict UTC events: `observed` carries an
`account_id`, while `unassigned` carries a required `reason`. Repeated events
with the same state are deduplicated; an observation after an explicit
unassigned event starts a new ownership interval. The legacy
`codex-official-identity-runs.json` list remains byte-for-byte roster evidence
only and never establishes ownership. A malformed v2 ledger is preserved and
read fail-closed.

Discovering exactly one current account does not assign unproven history to it.
Only identity-bearing official OTel or the post-intervention observation interval
may establish ownership; older rollout usage remains unassigned.

### Supported account reads and quota: grade D, side effects unresolved

The official Codex app-server documents `account/read` with
`refreshToken:false`, `account/rateLimits/read` and `account/usage/read`. These are
better-supported semantic surfaces than calling the backend URL directly.
However, launching app-server may create logs, state or refresh activity under
`~/.codex`. Sandglass must not spawn it until a runtime directory-invariance test
proves the exact read sequence does not modify provider state.

Source: [Codex app-server protocol](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md).

Until that gate passes, the existing
`https://chatgpt.com/backend-api/wham/usage` GET remains an observed first-party,
unstable quota capability and must fail closed.

## Grok

### Account discovery: grade C

Sandglass reads the current official Grok Build CLI login from `~/.grok/auth.json`
and the official `auth init user_info check` timeline retained in
`grok-official-identity-runs.json`. Every principal in that timeline is an account;
the current auth snapshot and later metadata only add email, plan and authentication
details. A historical principal can therefore appear as an id when the official
client no longer retains its email, without losing its proven usage ownership.

The `%APPDATA%/grokapp/grok-app` tree belongs to the community
[`RongleCat/grok-app`](https://github.com/RongleCat/grok-app) project, whose own
documentation states that it is not an official xAI product.
Sandglass does not read that tree, its profile switch logs or billing cache even
when they exist on the same machine. Public API rows use only the official Grok
CLI snapshot and its retained official identity timeline for built-in discovery.

### Official OpenTelemetry: grade A after opt-in

Grok's external OTel stream is off by default and requires a double opt-in.
Identity attributes include `user.id` and organization/team/deployment ids when
known. `grok_code.api_request` carries model, input, output, reasoning and
cache-read counts; `grok_code.token.usage` provides typed token metrics.

Sandglass should prefer log events because they retain prompt/session correlation.
Before implementation, a fixture from the installed client must establish whether
OTel `output_tokens` includes or excludes `reasoning_tokens`; normalization must
include reasoning exactly once.

Source: [Grok monitoring guide](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/24-monitoring-usage.md).

### Local sessions and logs: grades B, C and deterministic A joins

`~/.grok/sessions/**/updates.jsonl` contains per-turn usage with exact totals but
normally lacks an account id. In the currently observed shape, cached input is a
subset of prompt input and reasoning is a subset of output; neither may be added
twice.

`~/.grok/logs/unified.jsonl` contains:

- `auth init user_info check` identity events with process id and user id;
- `shell.turn.inference_done` token events with the same process id.

A token event may be attributed only when a preceding auth event in the same
provider process establishes the identity. Rows before the first such event stay
unassigned. The current Sandglass parser uses the identity events but does not yet
consume `shell.turn.inference_done` as the direct token source.

Community desktop profile-switch logs are not an admissible source. If the
official CLI log and Sandglass's own post-installation observation ledger cannot
prove an interval, that interval remains unassigned.

Sequential switches in one Grok home are attributable by this timeline. Two
accounts used concurrently in the same home are a complex scenario because the
session usage rows expose no identity join key; a user adapter must supply that
missing relationship.

### Quota: grade D

`https://cli-chat-proxy.grok.com/v1/billing?format=credits` is used by the official
client family but is not a stable public integration contract. It reports
account-wide billing windows and can include activity from other devices. Expired
credentials require login in the official client; Sandglass never refreshes them.

An `x.ai/billing` ACP extension has appeared in official-client integrations, but
availability varies by entrypoint/version. Spawning an agent process is not an
acceptable replacement until read-only side effects and protocol availability are
verified on supported versions.

## Reuse instead of reinvention

| Candidate | License | What to reuse | Decision |
| --- | --- | --- | --- |
| [OpenTelemetry Collector OTLP receiver](https://github.com/open-telemetry/opentelemetry-collector/tree/main/receiver/otlpreceiver) | Apache-2.0 | Stable OTLP HTTP/gRPC protocol handling | Reference implementation and optional sidecar candidate; measure Windows binary size and lifecycle cost before bundling. |
| [`opentelemetry-proto`](https://pypi.org/project/opentelemetry-proto/) | Apache-2.0 | Official generated protobuf message types | Preferred dependency for a small embedded OTLP/HTTP binary receiver; Sandglass would own only localhost HTTP, validation and persistence glue. |
| [`ccc-usage-dashboard`](https://github.com/cero-t/ccc-usage-dashboard) | Apache-2.0 | Claude/Codex OTLP event shapes, deduplication and append-oriented ingestion lessons | Study and test against the same fixtures; do not copy code without attribution and dependency review. |
| [`ccusage`](https://github.com/vikas9dev/ccusage) | MIT | Claude transcript discovery and message-id deduplication cases | Use as an independent parser oracle, not as sole truth. |
| [`CodexBar`](https://github.com/steipete/codexbar) | MIT | Quota-provider fallback ordering and failure semantics | Reuse narrowly after confirming each adapter's provenance and browser/cookie behavior. |
| [`AgentMeter`](https://github.com/LyleMi/AgentMeter) | Apache-2.0 | Incremental JSONL indexing and parser fixtures | Independent comparison source for local transcript parsing. |

No browser-cookie importer, credential refresher, provider-directory writer,
price table or account switcher is admissible in Sandglass even if a reusable
project contains one.

## Implementation gates

The first implementation is an isolated, fixture-driven OTLP/HTTP binary
receiver, not a new ownership heuristic. It may replace existing report
attribution only when all of the following are true:

1. It binds to localhost and persists only under `SANDGLASS_HOME`.
2. Provider prompt, tool input/output and file-path content is discarded before persistence.
3. A provider event id or deterministic request key prevents duplicate accounting.
4. Every stored usage row carries `source`, `source_version`, `evidence_grade`, account id if directly present, and an explicit coverage state.
5. Runtime tests snapshot provider directories before and after receiver startup, account discovery, collection and quota reads and prove byte-for-byte invariance.
6. Claude, Codex and Grok fixtures prove that cached input and reasoning are counted exactly once.
7. With telemetry disabled or the receiver stopped, the UI says precise monitoring was not active; it never backfills the gap from the current account.

Only after these gates pass should the existing attribution code be changed. Old
records remain global/unassigned unless their own provider evidence closes the
chain.

### Prototype status

Implemented locally: a loopback-only `/v1/logs` protobuf receiver using the
official `opentelemetry-proto` package, normalized append-only storage under
`SANDGLASS_HOME`, deterministic deduplication, content allowlisting, explicit
unassigned state, provider token-semantics fixtures, gzip support, payload limits
and a runtime vendor-directory invariance test.

A shadow reconciler now groups both sources by provider, official session id and
UTC minute. It reports an identity match only when every normalized token bucket
and call count agrees exactly and the OTLP group names exactly one account. It
does not alter sessions or totals; missing local rows, token differences and
multiple account ids are separate rejected states. This is the measurement
instrument for a future join, not activation of that join.

The native provider pages first distinguish whether the user-controlled receiver
is disabled, ready or unable to bind. When enabled, they expose four directly
provable evidence states: no event observed, provider-recognizable traffic observed without a supported usage event,
usage observed without account identity, and account-bearing evidence observed.
The receiver stores only aggregate receipt counts for the traffic-only state;
raw packets and attributes are not retained. The status includes first/last
event times and explicitly says that the prototype is not part of current report
totals. It does not infer that a client is configured merely because the receiver
is ready.

Each state opens an optional provider-specific PowerShell command derived from
the official Claude Code, Codex and Grok OTel opt-in mechanisms. The command
affects only the official client process it launches. Sandglass does not edit
`~/.claude/settings.json`, `~/.codex/config.toml`, `~/.grok` or any other provider
state to enable telemetry. A non-usage official event is sufficient to prove the
transport is connected, but never sufficient to claim token coverage or account
ownership.

Not yet claimed: live client fixture compatibility across all three providers,
gap-free coverage intervals, or replacement of the existing transcript/report
attribution. Until those are separately proven, even exact shadow matches remain
inspection evidence rather than report ownership, and OTLP is never a second
total added to current reports.
