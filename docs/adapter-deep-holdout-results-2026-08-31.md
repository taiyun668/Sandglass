# Adapter deep holdout results · 2026-08-31

This record covers synthetic clean-room trials only. No real account, path,
session, or Token data is included.

## Environment

- Three-layer mechanism bundle: `SKILL.md`,
  `references/reconstruction.md`, `references/v2-interface.md`, and
  `scripts/v2_import.py`.
- Visible profile per seed: 52 files, maximum depth 12, 14 deep history files,
  and 9 shallow cache/temp/recovery decoys.
- Hidden truth per seed: 346 billed events, 6 provider-scoped accounts, 6
  latest quota observations, rapid switches, minute storm, both replay shapes,
  cross-tool overlap, approximate-time re-anchoring, and provisional/final
  lifecycle records.
- Formal results are binary. Stored evidence and owner controls are scored
  separately; product admission is exercised only on a copied state.

## Frozen candidate `c8d0102`

| Seed | Result | Evidence |
| --- | --- | --- |
| 99521 | FAIL | Reconstructed the sources but probed POST-only v2 routes with GET, treated 404 as absence, and persisted 0 events. |
| 99533 | PASS | 346/346 events, exact Token, 6/6 accounts, 6/6 quotas, all native markers, stored-only controls, reversible explicit admission, source tree unchanged, and retained-revision rollback evidence. |
| 99547 | FAIL | Persisted 279/346 events. It selected provisional `response_delta` rows and omitted 67 later complete Cinder records. |

The first scorer revision incorrectly required account/Token controls to be
enabled without owner authorization. That result was withdrawn. The corrected
scorer requires stored-only initial state, then enables and reverses admission
on a copied state. Seed 99533 remained PASS after correction.

Observed corrections:

- `b5f98df` added a deterministic POST route probe and the lifecycle rule that a
  terminal complete/final/settled record outranks provisional delta/progress
  snapshots for the same native event.

## Frozen candidate `b5f98df`

| Seed | Result | Evidence |
| --- | --- | --- |
| 99601 | FAIL | Persisted all 346 normalized events with exact accounting, but receipts contained summaries rather than all native event markers and the HTTP fallback could not exercise retained-revision rollback. |

This run caused two additional instrument corrections. Native receipt coverage
is a distinct gate from normalized event correctness. Formal trials also require
executed rollback evidence, at least two retained revisions, and a final active
revision; prose about rollback cannot satisfy that gate.

Observed correction:

- `a1f9cde` requires event-bearing native receipts, adds deterministic
  replace/status/rollback/deactivate operations to the v2 script, and adds the
  formal rollback workflow gate.

## Current conclusion

At this checkpoint the deep holdout had produced one complete PASS and three
attributable FAIL trajectories. The candidate after `a1f9cde` could not inherit
the earlier PASS and required the fresh group recorded below.

## Minimal-invocation structural holdout

After `3ace37b`, the complete user prompt became exactly
`执行 Sandglass Adapter Skill。`. The launcher verifies the four-file mechanism
bundle outside the prompt. No tool inventory, product URL, file list, objective,
field name, path, join rule, or expected result is injected. Each agent must
find the Skill and ask the owner for environment information itself.

This group used structural shape variant 2, whose native filenames, lifecycle
labels, Token fields, account-transition fields, quota fields, and proxy
settlement schema were unseen by the earlier candidates.

| Seed | Result | Formal gates |
| --- | --- | --- |
| 99817 | PASS | 10/10 |
| 99829 | PASS | 10/10 |
| 99843 | PASS | 10/10 |

Every run reconstructed 346/346 exact-once events, 6/6 provider-scoped
accounts, 6/6 latest quota observations, all native event markers, stored-only
initial controls, reversible explicit admission, an unchanged source tree, and
executed retained-revision rollback with the intended revision restored.

This establishes the current promotion gate for the minimal invocation and
structural variant tested here. It does not prove compatibility with arbitrary
future vendor schemas, operating systems, permissions, or damaged histories.
