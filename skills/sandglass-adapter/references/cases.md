# Adapter evidence cases

These cases show one possible shape of each mechanism. They are not lookup
instructions, required filenames, or fields every machine must contain. The
clean-room evaluation excludes this file so that it tests the mechanism wording
in `SKILL.md` rather than an agent's ability to copy known answers.

## Inventory before discovery

On one official-client-only machine, first-party request logs already contained
the Token usage, the first-party quota channel supplied limits and resets, but
historical account ownership was incomplete. On machines that route work through
other tools, the missing cell can instead be Token usage itself. This is why the
adapter inventories all four facts and their coverage before assuming it is
rebuilding an identity ledger.

### A partial official source is not the official ledger

In one clean-room run, an agent found two result-like parent records and treated
the Token lane as complete. The official Claude execution history under
`~/.claude/projects/**/*.jsonl` instead contained many months of
`assistant.message.usage` rows. The small source was valid for its own interval,
but it represented only a tiny fraction of the local history.

The correction was not a Token multiplier. It was to inventory every record
kind and official data root, compare their oldest/latest timestamps with the
owner's stated long-term use, and keep the lane marked partial until the missing
interval was explained. In this shape, repeated `requestId` rows were cumulative
updates for one request, so the last value supplied its total while successive
values supplied timeline deltas. Resume replay still required the separate
intra-file rule described below.

## Follow the execution chain

One community desktop shortcut expanded into a web shell, a JavaScript launcher,
a packaged runtime, and finally a Codex CLI login backed by `~/.codex`. The outer
tool retained a real switch history, but the inner official client held the
credentials that actually executed and billed requests. The outer history was
useful secondary coverage; it was not the authority inside the official client's
covered period.

## Coverage beats timestamp adjustment

The same switch appeared as an outer click, an intermediate profile write, and
a later official authentication event. Moving one timestamp with a settle window
made rapid switches cross each other. Using the official event inside its
coverage removed the adjustment entirely.

In the same source family, the official log had rotated away an older interval
that an outer history still retained. The outer history could extend that older
interval. In the overlap, the official transition remained authoritative. An
official interval with no corresponding request event was evidence of no billed
work, not a hole to fill from click history.

### Re-anchor a Codex account-switch ledger

On one machine, `codex-auth` recorded an ordered history with the switch time,
the account switched from, and the account switched to. Those timestamps marked
the switch/restart workflow and did not exactly equal the official Codex usage
boundary. The records were still valuable: they authoritatively supplied the
account sequence and transition direction.

The reconstruction preserved each original `codex-auth` time, then searched the
official usage ledger around it. The last event continuous with the outgoing
execution and the first event continuous with the incoming execution exposed a
repeatable discontinuity. The reconstructed transition's effective time was
reset to that official boundary while its `from -> to` identity remained from
`codex-auth`. Usage between consecutive effective boundaries then belonged to
the account selected by that interval.

This was not a global clock offset or nearest-minute guess. Every transition had
to keep monotonic order and fit its neighboring official events; an unproved
pair would remain unresolved. Neither native ledger was modified.

## Identity-ledger reconstruction

- One executing client records an activation timestamp itself, while a polling
  panel notices the same state later. The poll is a lower-ranked observation even
  if its timestamp has more digits.
- A naive merge once discarded the earlier start of a run that existed in only
  one source. Coverage-aware merging retained the first supported transition in
  the extension and used the official source in the overlap.
- A prefix filter matched two neighboring account identifiers. Every calculation
  after that looked internally consistent while operating on the wrong account.
  Exact `(provider, account identity)` matching prevented the repair from deleting
  valid usage.
- `A -> B -> A` is not a duplicate A. Each transition remains, and each minute
  uses the last transition at or before that minute.

## Token replay and parent totals

### A minute storm is not automatically replay

One official execution file contained dozens of billed turns in the same
minute and session. Every row had its own native event identity and belonged to
one continuous execution; there was no parent-copy marker or repeated request.
All events counted once. Grouping by minute before deduplication would have
silently discarded legitimate usage.

Another minute contained billed events from two concurrent sessions. The shared
provider and minute did not make them a collision; both session partitions were
kept and summed during roll-up. By contrast, the same native event appearing in
an official ledger and an adapter ledger remained one accounting fact.

A fork replay may also appear as a dense burst, but density is not the reason to
remove it. Its parent reference, copied opening sequence, and source semantics
prove replay. The minute storm lacks that lineage, so it remains real usage.

Claude Code resume can copy parent events while keeping their original
timestamps. A Codex fork can write copied parent usage as a dense burst at the
beginning of a new record with new timestamps and a parent reference such as
`forked_from_id`. Timestamp equality catches the first shape but not the second.
Sandglass handles the latter with an opening-prefix analysis; adapters must look
for the source's equivalent semantics rather than that exact field name.

In one Claude usage format, iteration rows omitted `output_tokens_details` that
were present at the parent level. Summing children lost reasoning output, so the
complete parent usage was authoritative.

The same email address also appeared under Claude and Codex on one machine.
Email-only matching crossed provider boundaries; the provider dimension kept the
identities separate.
