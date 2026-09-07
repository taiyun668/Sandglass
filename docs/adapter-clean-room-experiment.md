# Sandglass adapter clean-room experiment

## Purpose

Measure whether the packaged Sandglass adapter mechanism lets an otherwise blank
agent discover and reconstruct local evidence without being handed repository
answers. The current mechanism has three layers: core `SKILL.md`, generic
on-demand references, and deterministic scripts/product interfaces. Report its
sufficiency separately from the usability of Sandglass's inbox and four
reversible controls.

The first experiment is valid only for the known source shapes present on the
test machine. It is not a general product-performance claim.

## Version lineage and interpretation

Freeze the exact Skill bytes before a trial and record the commit, line count,
and SHA-256 with the result. A Skill changed in response to failures observed on
the test machine is fitted to that machine. Its score answers only how it behaves
in an environment it has already seen; it must not be presented as evidence of
general Skill quality or product performance.

For the first controlled comparison, keep the three histories distinct:

- `9590730` (82 lines) is the pre-experiment, unfitted baseline and primarily
  measures whether the minimal disclosure was sufficient;
- `45c64a6` (151 lines) is the experiment-definition version, is partially
  fitted, and must be labelled as such;
- `3ccba6e` (553 lines) is the fitted comparison version. It is fully fitted to
  failures observed on this machine and can only reveal what problems remained
  after those additions.

Run every frozen version against the same isolated input snapshot and record the
input snapshot hash. Scores from the fitted version become externally
meaningful only on a machine with independently different source shapes.

### Synthetic proxy holdout

When another real machine is unavailable, test reconstruction mechanics against
an unseen virtual proxy rather than copying this machine's fitted source shapes
into a VM. Generate the visible installation and evaluator truth into separate
roots:

```powershell
python -m tools.generate_adapter_holdout `
  D:\isolated\adapter-holdout-visible\trial-01 `
  <evaluator-root>\trial-01 `
  --seed 71531
```

Only the first root may be mapped into the clean-room machine. The evaluator
root contains event-level account and Token truth and must not be mapped,
mentioned, or otherwise made readable to the trial agent. Give the agent only
the owner's ordinary tool inventory (for example, that Lattice Relay is used),
not a path, field name, join rule, expected total, or evaluator result.

Run two or three seeds before changing the Skill. The visible proxy deliberately
separates settled usage, effective account transitions, quota snapshots,
non-billing retries and reset-prone process counters. The hidden scorer measures
whether the persisted Sandglass ledger reconstructed the event-level join,
preserved Token exactly, discovered accounts and quota, and left reversible
controls disabled until the user chooses them:

```powershell
python -m tools.score_adapter_holdout `
  <evaluator-root>\trial-01 `
  D:\isolated\adapter-holdout-state\trial-01
```

Stage each frozen Skill and visible seed into a separate lab. The lab keeps
controller metadata and the isolated Sandglass state outside the agent's
filesystem profile:

```powershell
python -m tools.prepare_adapter_holdout_lab `
  D:\isolated\sandglass-formal-comparison-20260831\bundles\82-unfitted `
  D:\isolated\adapter-holdout-v2-visible\seed-71531 `
  D:\isolated\adapter-holdout-v2-labs\82-unfitted-seed-71531-run1
```

Launch through the module entry point:

```powershell
python -m tools.run_adapter_holdout_trial `
  D:\isolated\adapter-holdout-v2-labs\82-unfitted-seed-71531-run1
```

The launcher ignores the user's
ordinary Codex config and injects a least-privilege permission profile. The
agent may read the virtual profile, write its own working and temporary
directories, and reach the isolated loopback Sandglass service. It cannot read
the Sandglass source tree, sibling Skill bundles, controller metadata, the
isolated Sandglass state, the real user profile, or evaluator truth. Do not
replace this with `danger-full-access` plus a prompt asking the agent to stay in
scope.

On the current Windows CLI, a live access probe must also pass before any
formal score is accepted. Codex permission-profile configuration alone did not
enforce denied reads in the tested `codex exec` path. The launcher therefore
adds temporary inheritable read-deny ACEs for the dedicated
`CodexSandboxOffline` SID to the source tree, real provider state, sibling labs,
controller state and evaluator roots. It records the pre-run SDDL, verifies each
ACE, and removes only the ACEs it added in `finally`. If the sandbox identity
cannot be resolved, an ACE cannot be verified, or restoration fails, the trial
is invalid. These ACEs do not target the owner's user SID.

The initial user prompt contains no environment inventory. The controller holds
the genuinely used tool inventory, multi-account facts and switching behavior,
and answers them only after the Skill asks; paths, field names, join rules,
totals and evaluator information are never disclosed.

The complete external user prompt is exactly `执行 Sandglass Adapter Skill。`.
The Skill itself owns the objective, owner interview, discovery, persistence and
stopping conditions. Each trial runs the real Sandglass web product against its own hidden
`SANDGLASS_HOME`; the agent may inspect and operate that loopback product,
including its ordinary user-visible Help, but cannot read the source tree. This
therefore measures the end-to-end Sandglass product plus the selected frozen
Skill, not an abstract data-reconstruction puzzle detached from the product.

The launcher owns the isolated product lifecycle. It refuses to replace an
existing listener, waits for the product's runtime self-report before starting
the agent, and stops only the process it launched. Product stdout, stderr and
state remain controller-side. The task is invalid if product readiness,
shutdown, ACL verification or ACL restoration cannot be proved.

This holdout tests composition and resistance to misleading counters. It does
not prove discovery across real operating systems, undocumented vendor changes,
permissions, installation layouts, or damaged real-world histories. Keep those
claims for an independently different real machine.

## Composite promotion gate

The earlier Lattice-only holdout is a Level 0 instrument check. Its seeds vary
data, not mechanism, so they must not be used to rank frozen Skill versions.

The first promotion holdout is generated with
`tools.generate_composite_adapter_holdout`. One trial combines three providers,
five local tools, provider-scoped identities reused across platforms,
single-account/multi-tool and multi-account/multi-tool usage, official usage
joined with an outer tool's effective identity history, rapid switches, a
minute storm, fork and resume replay, cross-tool overlap, failed estimates,
reset-prone counters, incomplete nested usage and quota reset/grant signals.
Complete Token history is partitioned below project/workspace/session roots and
mixed with provisional records and ordinary client events. Shallower caches,
recent summaries, recovery fragments and temporary Token estimates contain real
but incomplete or non-billing values, so discovery must prove coverage and
native finality instead of trusting the shortest path or clearest filename.

It is verified by `tools.verify_composite_adapter_holdout`. There is no weighted
score or partial pass. A trial passes only when every gate passes: exact-once
events, exact Token buckets and total, exact provider-scoped accounts, exact
latest quotas, raw evidence persistence, owner controls remaining stored-only,
full product admission with zero blocked legitimate Token after the evaluator
explicitly enables it on a copied state, reversible totals, and a byte-for-byte
unchanged source tree.

Run three unseen seeds at the same level. Only three PASS results authorize a
harder mechanism set. A failure authorizes investigation of the failed gate,
not editing the hidden seed or accepting a percentage score. Before a frozen
Skill comparison, independently construct the known-perfect persisted ledger
for every seed and require the binary verifier itself to return PASS.

After a candidate has observed the default source schema, a later group must use
`--shape-variant 2`, not merely new numeric seeds. Variant 2 preserves the same
hidden evidence relations while changing native filenames, lifecycle labels,
Token field names, profile-transition fields, quota fields, and proxy-settlement
shape. This distinguishes general evidence rules from memorizing one generated
schema.

```powershell
python -m tools.generate_composite_adapter_holdout `
  D:\isolated\adapter-composite-l1-visible\seed-77123 `
  <composite-evaluator-hidden>\seed-77123 `
  --seed 77123

python -m tools.verify_composite_adapter_holdout `
  <composite-evaluator-hidden>\seed-77123 `
  D:\isolated\adapter-composite-l1-labs\82-seed-77123\sandglass-home `
  D:\isolated\adapter-composite-l1-labs\82-seed-77123 `
  --require-agent-rollback
```

Formal agent trials use `--require-agent-rollback`; instrument checks against a
controller-materialized reference omit it because no agent workflow ran. The
formal gate requires a successful executed rollback command, at least two
retained revisions, and a final active revision. Merely describing rollback in
the Skill or final answer cannot satisfy it.

## Inputs and exclusions

The blind agent receives only:

1. the byte-identical clean-room mechanism bundle: `SKILL.md`,
   `references/reconstruction.md`, `references/v2-interface.md`, and
   `scripts/v2_import.py`;
2. read-only access to the machine's provider and outer-tool records;
3. an isolated running Sandglass with the current v2 whole-revision import,
   official-minute export, status, and rollback surfaces.

Do not provide the Sandglass source tree, repository history, project
instructions, provider source map, another product's repository, the original
identity ledgers, evaluator checks, `references/cases.md`, or the next-version
`references/metering-v3.md`.

Create the Skill input with:

```powershell
python -m tools.prepare_adapter_clean_room D:\isolated\sandglass-adapter-skill
```

The output directory contains exactly the four mechanism files above. The
command prints per-file hashes so the evaluator can prove they are the release
mechanism rather than a second experimental version.

An optimized candidate must be committed and frozen before its first formal
trial. Keep its results separate from the 82-, 151-, and 553-line histories;
those histories answer different questions and are never retroactively replaced
by the current entrypoint.

### Environment-assisted comparison

After at least one fully blind run, a separate comparison run may also receive
the user's own inventory of tools that are genuinely enabled and used on that
computer. Treat this inventory only as discovery routing. A named tool is not
thereby an execution layer, billing authority, identity source, quota source, or
proof that any particular record is complete.

For the current test machine, the user-provided inventory is:

- the providers' official tools;
- `codex-auth`;
- the non-official Grok desktop app;
- `grokwork` (whose installed entry or data directory may use another name).

The agent must still trace each named tool to its real process, executable,
credential, account-management, and observation layers. It must search for an
equivalent installed entry when the user's product name is not a PATH command,
without being given a path or field name. Record what the inventory shortened,
which named tools were actually found, and which claims still lacked evidence.

Do not compare an environment-assisted run directly with a fully blind run as
if they had identical inputs. Report discovery speed separately from evidence
quality, and reject any result that promotes the user's statement "I use this"
into accounting authority.

Launch the blind agent with that output directory as its actual working
directory. Do not launch it in a repository and merely tell it not to read the
repository. The launcher hashes the complete agent-visible directory and aborts
unless it exactly matches the four-file manifest; this integrity control is not
part of the user's prompt. Start the agent without this thread's history,
repository context, or inherited project instructions.

## State isolation

Never test against the live writable Sandglass state. Stop the normal test
instance during the scheduled experiment, copy `SANDGLASS_HOME` to an isolated
location, and start the experiment instance against that copy on the endpoint
expected by the Skill. Snapshot the original and verify it remains byte-for-byte
unchanged. Provider and outer-tool directories remain read-only throughout.

Do not run this experiment while the user relies on the normal instance. A clean
rollback consists of stopping the isolated instance and restarting the normal
checkout with its original `SANDGLASS_HOME`; no reconstructed result is copied
back automatically.

### Result retention and raw-state cleanup

An isolated state copy contains real account and usage data and is a temporary
experiment input, not an experiment artifact. Close every trial with all three
steps below:

1. extract the observations needed for review, including timings, action count,
   dead ends, guesses, decision points, and scores;
2. commit only a redacted result that removes email addresses, account IDs,
   session IDs, user-specific paths, and raw Token counts;
3. after verifying the redacted artifact is sufficient, delete the isolated raw
   state copy and record its cleanup in the result.

Do not delete an existing raw copy merely because this protocol changed. Resolve
its exact path and obtain the owner's authorization first. Cleanup must never
touch the live `SANDGLASS_HOME`, provider directories, or outer-tool directories.

## Blind run

Use a 35-minute wall-clock limit:

- discovery of sources and authority: at most 8 minutes;
- understanding fields, timestamps, counters, and coverage: at most 12 minutes;
- ingestion and first visible accounting result: at most 15 minutes.

Record separately the time to the first useful number and the time to complete
reconstruction. Also record action count, dead ends, guesses, and every decision
the agent had to make without guidance. On timeout, record the current phase,
what it was searching for, and the unresolved decision instead of flattening the
result into a generic failure.

## Hidden evaluation

Only after the blind run, the evaluator compares the result with:

- the preserved original ledger;
- the most complete known reconstruction;
- independent switch-boundary, ownership-partition, and session-versus-minute
  conservation checks.

The agent must never see these materials during the run. A successful result
must preserve official-covered intervals, extend only genuine coverage gaps,
keep rapid transitions in order, avoid replay double counting, and leave all
source directories and the original Sandglass state unchanged.

Run two or three blind trials before changing the Skill. Repeated failures at the
same decision identify missing or non-operational guidance; a single different
trajectory does not. After selecting which mechanisms need examples, run a
separate release-bundle trial with `references/cases.md` and check that the agent
searches for equivalent evidence rather than literal example strings.
