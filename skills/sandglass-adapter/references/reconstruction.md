# Evidence reconstruction

Read this reference when the task requires discovering sources, combining
multiple tools, rebuilding account ownership, or preserving Token exactly once.
It contains generic mechanisms, not machine-specific paths or provider answers.

## The four evidence lanes

Keep these facts independent until a relation is proved:

1. local `total_tokens` usage and its native event/session time;
2. provider-scoped account identity and account transitions;
3. official quota state and reset time; and
4. provenance, evidence level, and source coverage interval.

Missing identity does not erase proven Token. Missing quota does not erase
usage. An identity event without usage does not create Token.

`output_tokens` already includes reasoning. Never add reasoning again.
Cumulative counters may reset or inherit an earlier balance; do not manufacture
events from counter differences unless the source defines that operation.

## Build a column join

One native source need not contain a complete row. Different direct writers may
supply different columns:

- an execution or billing record supplies provider, event/session, time and
  Token buckets;
- an activation, authentication, switch or request record supplies identity;
- process ancestry, profiles and local homes supply execution context; and
- official account or quota responses supply account metadata and reset state.

Build the lanes independently. Join only on proved relations such as provider,
exact native event/session/request identity, UTC time, execution context,
account identity, and coverage. Nearby timestamps, similar names, matching
totals, or files on the same computer are not sufficient by themselves.

Treat this as a column join, not a contest between complete rows. Two sources
conflict only when direct writers make incompatible claims about the same fact,
event, and covered interval. One source supplying Token and another supplying
its missing identity are complementary.

Do not let a cached account, current-account projection, session-majority label,
or Sandglass candidate result defeat direct evidence. Reproduce any reported
conflict from the native writers; the candidate API is an instrument.

## Establish authority and coverage

Trace the real execution and billing chain. A named desktop app may be a shell
around a CLI, worker, proxy, or another client. Inspect entry points, process
relationships, credentials used for execution, and the records each layer
writes. Authority belongs to the component that performed and recorded the
fact, not automatically to the outermost tool or most precise timestamp.

Search the official execution layer first. For each evidence lane, record:

- native fields and record kinds;
- counter, reset, replay and aggregation semantics;
- earliest and latest supported time;
- whether coverage is continuous; and
- counts and totals from the raw usage-bearing inventory.

One positive record is not complete coverage. A selected parser that explains
only a fragment of a long-used tool has not completed the lane.

Secondary evidence may extend an interval where the stronger source cannot
speak. It may not contradict an interval where the stronger source continuously
covers the relevant event type and records no event. Preserve source rank and
coverage with every fact.

## Keep discovery bounded

Begin from the owner's inventory and the narrowest justified entry points.
Search names or content with limits before listing high-cardinality roots. Once
an exact file is known, read it in pages or compute a narrow read-only projection
of fields, counts, time bounds and totals.

On an oversized result, narrow the path, file shape, result count, or read range.
Do not repeat the broad operation, raise global output limits, or treat the first
narrow miss as proof that a source does not exist. Raw prompts, credentials and
unrelated record content must not enter projections or diagnostics.

## Create a reconstruction anchor table

Before attributing usage, create one provider-scoped anchor table. Each ordered
transition row records:

- stable transition identity and sequence;
- `from_account` and `to_account` from the source that performed or recorded the
  account change;
- its original `observed_at` and source coverage;
- the official execution events immediately before and after the corresponding
  discontinuity;
- the reconstructed `effective_at`; and
- whether the relation is proved or unresolved.

The identity ledger and execution ledger may each be incomplete as a row while
remaining authoritative for different columns. A switch ledger can prove the
ordered `from -> to` transitions even when its timestamps describe a request,
restart, or observation rather than the first billed work. The official usage
ledger can prove the effective execution boundary while carrying no account.

Re-anchor these two columns instead of choosing one whole ledger:

1. Preserve every original switch row and `observed_at`; never edit the native
   ledger.
2. Keep the switch sequence and `from_account -> to_account` relation fixed.
3. Around each observed transition, locate the official event discontinuity:
   the last event continuous with the outgoing execution and the first event
   continuous with the incoming execution.
4. Match transitions to discontinuities monotonically using account order,
   execution/session continuity, and neighboring transitions. Nearest timestamp
   alone is not proof.
5. Set only the reconstructed row's `effective_at` to the proved official
   boundary. Use that effective timeline to cut and attribute official usage.

This is a time-axis reset of the reconstructed composite ledger: identity/order
comes from the transition ledger; effective time comes from the authoritative
usage ledger. Both original observations remain attached as provenance.

If several discontinuities fit one transition, one discontinuity would consume
two transitions, order would change, or coverage cannot show the adjacent
events, keep that anchor unresolved. Do not hide the ambiguity with a tolerance
or global offset.

## Reconstruct transition boundaries

Use a switch or activation timestamp as a search anchor, not automatically as
the accounting cut. Inspect native usage and execution continuity around it:

- the last event continuous with the outgoing execution belongs to the outgoing
  account;
- the first event continuous with the incoming execution begins the new
  account; and
- an empty interval between them is the proved boundary.

Changing an auth file alone does not prove that an existing process reloaded it.
Use the owner's operational description together with restart/reload, process,
session and native usage evidence.

Assign native events before aggregating. One UTC minute may contain outgoing and
incoming slices; preserve both and roll them up afterward. Once adjacent
execution segments are proved, all usage between their discontinuities belongs
to that segment's account. Rapid `A -> B -> A` switches remain three transitions.

Do not invent settle windows, minimum run lengths, fixed-hour ownership,
tolerances, caps or compensation. If one seems necessary, re-check the direct
writer and execution boundary.

An identity-bearing source may extend an identity-silent source only after an
overlap proves that both describe the same provider-scoped execution: native
events reconcile bucket-for-bucket and execution/authentication context agrees.
Carry identity only through the contiguous segment until the next transition,
context change or coverage break.

Match identities as `(provider, complete account identity)`. The same email or
label at two providers does not identify the same account.

## Preserve Token exactly once

The UTC minute is an aggregation bucket, never the deduplication identity.

- A same-session minute storm containing many distinct native billed events is
  legitimate usage; count every event once even when their timestamps differ by
  milliseconds.
- Distinct sessions may bill concurrently in the same provider minute. Preserve
  each session's events and add them only during the final roll-up.
- The same native billing event observed through two sources remains one
  accounting fact; the second source may add identity or corroboration but not
  Token.
- A retry, progress update, estimate, or cumulative snapshot is not another
  billed event unless the direct writer's semantics say it is.

Deduplicate from stable native event/request identity first, then explicit
parent/replay lineage and source semantics. A matching minute, density, Token
total, or account is insufficient. When a source lacks stable event identity,
do not collapse look-alike records until their ordering, counter behavior and
execution lineage prove they represent the same billed fact.

Replay has at least two shapes:

- original events repeated with original timestamps; and
- parent history copied into a dense new prefix with new timestamps.

Timestamp equality detects only the first. For the second, inspect fork, branch,
retry, resume or parent semantics and the opening event sequence.

A detailed child array is not automatically the accounting authority. Check
whether the parent total contains fields absent from the children before
summing. Keep the native complete total when it is the only complete billed
representation.

Likewise, group lifecycle records by stable native event/request identity before
choosing an accounting row. A `delta`, `progress`, checkpoint or provisional
row may contain a cumulative partial snapshot whose child buckets do not yet
equal its eventual total. When the same event later has a terminal `complete`,
`final`, `settled` or equivalent record with complete buckets, that terminal
record is the accounting authority. Do not reject or omit the event because an
earlier provisional row is incomplete, and do not sum provisional and terminal
snapshots. Sum deltas only when the direct writer explicitly defines them as
incremental billing events.

For identity completion, preserve the official minute's provider, session, UTC
minute and every Token bucket exactly, then add only proved identity. For Token
evidence absent from Sandglass, normalize direct billing events. Never copy an
existing official minute and add its Token again.

## Minimum adversarial fixtures

Use sanitized fixtures for rapid switching, unequal coverage, authoritative
no-event intervals, identical identifiers at different providers, both replay
shapes, incomplete child totals, minute storms, and cross-session/cross-source
collisions. Replaying identical evidence must not multiply it.
