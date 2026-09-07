# Sandglass · Claude Code project instructions

@AGENTS.md

The file above carries the project's boundaries — vendor directories are
read-only, the single user-visible run source, the three token calibres, the
attribution rules. **It outranks this file.** This one adds only the Claude Code
model and role routing, and does not replace any source, privacy, read-only,
security, release, or experiment governance. Where the two disagree, the
stricter rule wins.

The Codex-side equivalent is `docs/model-routing.md` and `.codex/`. The
authority boundaries there are the same ones; only the model names and the
config format differ. **Do not port the TOML across.**

**A new window reads `docs/handoff-2026-09-02.md` first** — what the last round did,
what was measured rather than assumed, **what is still not proven**, and the three
steps left before a release, none of which are in the code.

## Default routing

Opus owns decisions; Sonnet owns execution; Opus owns acceptance. This maps
role-for-role onto the Sol/Luna split in `docs/model-routing.md`.

| Role | Model | effort | Boundary |
| --- | --- | --- | --- |
| Root / Controller | `opus` | `high` | Decisions, scope, authorization, final gate |
| `planner` | `opus` | `high` | Architecture, root cause, plans, open choices |
| `construction` | `sonnet` | `high` | Approved, bounded implementation |
| `explorer` | `haiku` | `medium` | Read-only location and evidence collection |
| `auditor` | `opus` | `high` | Independent audit; never Owner acceptance |

`xhigh` for complex architecture, security, or a hard root cause only when the
Controller says so. **It is not an automatic escalation.**

Escalation conditions, the stop-at-the-boundary rule, and the worker result
labels are defined once in `docs/model-routing.md`. They apply here unchanged;
they are not restated here, because two copies drift and one does not.

## What is enforced, and what is only discipline

- **runtime-enforced** — `.claude/agents/*.md` frontmatter. `model`, `effort`
  and `tools` are parsed and applied. Measured in this checkout's sibling
  project: a parent session on opus spawned `explorer` onto haiku, so the
  frontmatter model is honoured rather than inherited. `explorer` is given only
  `Read, Glob, Grep`, so its read-only is a tool boundary, not a promise.
- **runtime default** — `.claude/settings.json` sets `model: "opus"` for new
  sessions. It sits at the bottom of the override chain: session `/model`,
  `--model`, agent frontmatter, `ANTHROPIC_MODEL`, then settings. It is not a
  hard lock and never switches a session already running.
  **In a Claude desktop tab this key does nothing.** The app launches sessions
  with an explicit `--model` (observed in the argv of desktop sessions), which
  outranks project settings; it also passes
  `--setting-sources=user,project,local`, so the file is read and the agents do
  load — the model key is simply outranked. To change a desktop session's main
  model, change it in the app. The CLI entrypoint is unaffected.
  **`effort` is inert on Haiku.** A probe declaring `max` on haiku recorded no
  effort field at all, while a sonnet probe declaring `low` recorded `low`. The
  field on `explorer` is kept for the day haiku takes it; do not read it as a
  working control today.
- **instruction-level policy** — Owner authority, the escalation gates, the
  auditor's independence, the receipt labels. `auditor` and `planner` hold
  `Bash`, and `construction` declares no `tools` at all and therefore inherits
  every one. **Their boundaries are discipline with no tool-layer backstop.**
  The frontmatter key list is `name / description / model / effort / tools /
  color`; there is no `disallowedTools`, so do not invent one.

This adds no sandbox, approval, MCP, provider, or permission setting, and
changes no product behavior.

## Delegation

Route bounded work with clear success conditions to `construction`; route file
location and ordinary evidence gathering to `explorer`. **A controller does not
take over construction merely because it could finish faster.**

A subagent does not inherit the parent's history. Its task must carry the
paths, the established facts, the decisions already made, the success
conditions, the prohibitions, and the verification commands.
