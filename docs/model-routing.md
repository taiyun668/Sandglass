# Model routing and decision authority

This is an additive routing policy for governed work in Sandglass. It preserves
the existing source, privacy, read-only, security, release, and experiment
governance. It does not replace an existing task card format, create a new
mandatory task-card schema, change product behavior, or authorize a release.

## Route by role and risk

| Role or work shape | Route | Reasoning default |
| --- | --- | --- |
| Controller, planner, or auditor | Sol (`gpt-5.6-sol`) | `high`; use `xhigh` for complex risk |
| Construction | Luna (`gpt-5.6-luna`) | `high` |
| Ordinary exploration | Luna (`gpt-5.6-luna`) | `medium` or `high` as scoped |

Luna may discover facts, prepare implementation, run in-scope checks, and
return evidence. Luna must not give final acceptance to its own implementation
or audit. A Sol controller/planner/auditor must make the independent acceptance
decision. A worker result is evidence for that decision, not the decision
itself.

For bounded exploration, code, UI, glue, tests, documentation, lint,
mechanical edits, validation, and evidence collection, route the work to Luna
when no escalation condition applies. A Sol controller does not take over
construction merely because it can complete it faster.

Root retains overall control and does not delegate Owner authority. Sol's
independent audit reviews the actual diff, tests, and evidence; the Sol
Controller makes the technical routing decision; then the Owner's existing
acceptance gates remain final. A model choice never substitutes for an Owner
gate.

The work must escalate to Sol before a decision, approval, or final claim when
it concerns any of the following: architecture, scope, API, schema,
authentication, security, persistence, migration, concurrency, transaction,
state machine, production behavior, a cross-module invariant, two consecutive
failures of the same class, a conflict between code/documentation/runtime
behavior, weakening a security gate, merge, release, or acceptance. The
existing fail-closed and “not proven is not passed” rules remain in force.

Escalate as well when the original plan may be wrong, the design or intended
contract is not knowable, the actual change materially exceeds the plan, tests
pass while the design remains suspect, or the work changes permissions,
contracts, infrastructure, deployment, or runtime ownership. These signals are
Sol gates even when the patch is small or the test suite is green. A production
readiness claim or any weakening of an acceptance mechanism is also a Sol gate.

When Luna encounters one of these gates, it stops at the decision boundary and
returns the facts, evidence, and open question; it must not widen the scope or
make the gated decision.

## Configuration versus session overrides

The policy above is the authority boundary. The intended configuration defaults
are a Sol root at `gpt-5.6-sol` with `high` reasoning, and Luna as the default
construction/exploration subagent with the role defaults above. Role-specific
`.codex/agents/{construction,planner,auditor}.toml` files and
`.codex/config.toml` are now present in this checkout with those intended
defaults. On 2026-09-02, Root's codex-cli 0.149.0 app-server `config/read`
loaded the project layer `.codex` in this checkout: Root Sol/high, agents enabled,
default Luna/high, and all three `config_file` references resolved to absolute
paths. This proves configuration parsing/loading only; it does not prove that
the three custom roles completed real model calls. The current collaboration
tool has no `agent_type`; Luna construction has run through explicit
model/effort; a separate Sol/high read-only audit completed with no blocking
findings and checked all 17 requested gate categories. Role execution through
the custom-role loader remains unverified. The global strict
check remains blocked by the pre-existing unknown `computer_use` field and is
not claimed as passed.

Configuration references: [official subagent configuration](https://learn.chatgpt.com/docs/agent-configuration/subagents)
and [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
Verification also checked TOML role mappings, preserved the original AGENTS.md
prefix byte-for-byte after line-ending normalization, and passed `git diff --check`.
Existing business-file changes and the global Codex configuration retained their
pre-task SHA-256 hashes. No product restart or product test run was needed for
this configuration-only change; neither is claimed as performed.

The current collaboration session may explicitly override `model` and
`reasoning_effort` for a bounded worker invocation only where the tool permits
it (a finite `fork_turns` value or `none`; `all` inherits the Root context and
must not be used to give a Luna worker the full history). Such an override is
session-scoped, is not a hard lock, and cannot bypass a Sol escalation or
acceptance requirement. Existing sessions do not hot-switch when configuration
changes. The current global Luna `xhigh` setting is outside this document and
is intentionally unchanged.

No routing default grants sandbox, approval, provider, or MCP permissions. Such
permissions remain controlled by the active environment and existing policy;
an explicit model override cannot widen them.

The blind clean-room/holdout experiments are an explicit exception in scope,
not in safety: their frozen Skill bundle, task prompt, task card, and recorded
model choice define the experiment. Development routing must not rewrite or
retroactively reinterpret those frozen trials.

## Worker result addendum

Keep every existing task-card layout and fields. If a worker returns a result,
append these labels without replacing the card format:

`TASK RESULT CURRENT_STATE FILES_CHANGED IMPORTANT_DIFF VALIDATION FAILURES INVARIANTS_CHECKED RISKS DEVIATIONS_FROM_PLAN OPEN_QUESTIONS RECOMMENDED_NEXT_ACTION`

If the result is a failure, also append:

`ROOT_CAUSE_IF_KNOWN ATTEMPTS_MADE WHY_BLOCKED WHAT_REQUIRES_SOL_DECISION`

There is no single unified task-card schema in the current Sandglass
repository: the existing material uses experiment-specific binary gates,
result tables, release checklists, and provenance audits. This addendum records
the requested worker evidence labels; it does not turn them into a replacement
or broader mandatory schema.

## Existing tensions to preserve and surface

- The repository's strict vendor-directory read-only rule is broader than any
  convenience suggested by a worker or model route. Routing does not authorize
  credential refresh, account switching, provider writes, or third-party data
  reads.
- The release checklist and provenance audit distinguish implemented gates,
  local unsigned-build evidence, and completed release acceptance. A model route
  cannot promote one into another; merge/release/acceptance remains a Sol
  decision with the existing gates.
- The clean-room documents require frozen inputs, isolation, binary scoring,
  rollback evidence, and no retroactive inheritance of a prior PASS. This can
  look different from ordinary development routing, but it is intentional and
  remains authoritative for those trials.
- “Worker” here means a bounded Codex collaboration role. It does not mean
  Grok Worker data or the prohibited Grok Worker integration named by the
  existing security/product boundary.

These are routing clarifications only. No original security clause is weakened
or edited by this document.
