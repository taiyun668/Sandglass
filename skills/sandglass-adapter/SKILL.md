---
name: sandglass-adapter
description: Discover, reconstruct, and reversibly persist local AI usage, account identity, and quota evidence in Sandglass. Use for multiple accounts or tools, account switching, incomplete ledgers, unsupported local clients, proxies, gateways, or exports that must complete the current Sandglass product ledger.
---

# Sandglass Adapter

Build the most complete evidence ledger this computer can support and persist it
through Sandglass's v2 user-source interface. The deliverable is a durable,
reversible ledger, not only a report or candidate.

When the owner says only to execute this Skill, cover every provider and
execution path named by the owner or discovered from those paths. A narrower
explicit request may reduce scope; otherwise one completed provider is not
completion.

## Core workflow

1. **Ask the owner first.** Inventory the official apps, CLIs and extensions;
   account switchers and authentication helpers; shells, workers, automations,
   proxies, gateways and exporters they actually use. Ask which are current or
   historical, what each one does, which providers/accounts it affects, and
   whether switching requires restart or reload. These answers route discovery;
   they do not manufacture Token or quota evidence.
2. **Discover before adapting.** Resolve named products to their real local
   entry points and trace the execution/billing chain. Search direct official
   writers first, then secondary sources for genuine uncovered intervals. Keep
   provider and outer-tool directories read-only.
3. **Build the candidate ledger before follow-up questions.** Separate Token,
   provider-scoped identity, official quota, and provenance/coverage. Combine
   complementary evidence by proved relations rather than demanding one
   complete source. Leave unresolved fields null and ask the owner only about
   remaining operational edges that local evidence cannot establish.
4. **Preflight one complete v2 revision.** Preserve sanitized native receipts,
   secret-free accounts, authorized quota results, safe normalized records and
   independent expected counts in one package. Use the product's preflight;
   never replace a blocker with a guess or a new source id.
5. **Commit and verify.** Commit the exact preflighted package atomically, inspect
   status, prove idempotence, exercise rollback/deactivation, and restore the
   intended revision. Verify every source directory read during observation is
   unchanged.
6. **Leave admission to the owner.** Persistence does not enable display,
   identity supplementation, Token inclusion, or full-window inference. Explain
   those source-labeled reversible controls and let the owner choose each stage.

## Judgment principles

- Authority belongs to the component that performed and directly recorded a
  fact, not automatically the outer shell, dashboard, latest timestamp, or
  Sandglass candidate view.
- Treat the ledger as a column join. One source may prove Token/time while
  another proves identity. They conflict only when direct writers disagree
  about the same fact, event, and covered interval.
- Distinguish “the stronger source has no coverage” from “it covers this interval
  and records no event.” Secondary evidence may extend the former, not overwrite
  the latter.
- Establish ownership on native events before minute aggregation. Transition
  logs may prove account order while official events prove effective time. Keep
  the observed transition time, re-anchor the reconstructed effective boundary
  to a proved execution discontinuity, and aggregate only afterward. A minute
  may contain slices from two accounts.
- Preserve Token exactly once. `output_tokens` already contains reasoning.
  A minute is an aggregation bucket, not a deduplication key. Counters, replay,
  fork, branch, retry and resume records require their native identity and
  semantics before aggregation.
- If a result needs settle windows, tolerances, caps, compensation or fuzzy
  identity matching, stop and re-check the writer, coverage, and join relation.

Read [`references/reconstruction.md`](references/reconstruction.md) when
discovering or joining sources, rebuilding ownership, or deduplicating Token.
It owns the detailed evidence, coverage, transition and replay rules.

Read [`references/v2-interface.md`](references/v2-interface.md) only after the
candidate ledger is ready to persist. It owns the exact package, calling order,
compare-and-swap replacement, rollback, and product-control contract. Prefer the
installed `sandglass user-source` commands; use
[`scripts/v2_import.py`](scripts/v2_import.py) only as the verified-loopback HTTP
fallback.

Consult [`references/cases.md`](references/cases.md) only after independent
discovery when a concrete native source shape remains unclear. Its examples are
not universal paths, fields, or answers.

## Safety and stopping conditions

Execution authorizes only Sandglass-owned writes required for the v2 revision.
Never refresh credentials, switch accounts, start provider tools, or create,
modify, move, or delete provider/outer-tool files. A live official quota call
requires owner authorization and may persist only the result, never credentials.

If neither the installed file interface nor a verified Sandglass loopback API is
available, report a persistence blocker. Do not relabel analysis as completion.

Completion requires every in-scope provider's Token, identity, quota and
provenance to be supported or explicitly unavailable with coverage stated; all
safe rows persisted; idempotence and rollback verified; and source-directory
snapshots unchanged.

Before anything leaves the computer, remove real emails, account ids, session
ids, paths, prompts, Token values, credentials, and tool content. Do not publish,
open a pull request, or contact a maintainer without separate approval.
