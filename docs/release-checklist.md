# Sandglass public release checklist

This is the ordered release route. A later item must not weaken an earlier source,
privacy or correctness boundary.

## Where this stands, and what to do next (2026-09-09)

The canonical checkout is `D:\Sandglass`, and the product is stopped. Public
`main` and tag `v0.1.3` resolve exactly to
`159c32ea08dacd596f98e44e8e998a718f1f3223`. `v0.1.3` is the current full
GitHub Release. Its five assets came from tag CI run `34381003563`; their
GitHub digests match the downloaded files, all three entries in
`SHA256SUMS.windows` match, and the Owner signature verifies with the public
key embedded in the updater. Both main run `34380591595` and the tag run
completed all four jobs successfully.

The dormant third-party Authenticode submission path has been removed from the
release gate. No certificate, signing-service account or sponsor is required;
the shipped binaries remain explicit unsigned community packages and automatic
updates use the Owner-signed checksum manifest.

The update state machine is accepted. Four NSIS register-family mismatches were
found and mutation-locked. A normal `v0.1.1 -> v0.1.3` smoke passed on the
maintenance machine and a second Windows machine; a fault injected after the
new desktop child was created also passed termination, old-version restart,
old-provenance restoration, residue cleanup, owner-state preservation and
provider-fixture byte invariance. Both wrappers recorded their own process exit
code 0. The full suite is 765 tests, test isolation exits 0, and a fresh
Grok-4.6 audit of `edf241e` returned ACCEPT with no worker file changes.

The production feed is also live: the actual public `v0.1.1` source reads
`/releases/latest` and receives a `v0.1.3` offer whose exact installer digest
matches and whose manifest signature is valid; a `v0.1.3` client receives no
offer. The remaining product observations are the Owner-visible in-app click
sequence and a real provider-state transition, not release construction.

A second LAN Windows 11 Pro machine supplied a real active desktop acceptance
surface. Earlier `v0.1.2` setup and portable candidates ran there, and equivalent
private `v0.1.3` builds completed install and update. The exact public
`v0.1.3` setup and portable hashes were later rejected before process creation
by that machine's enforced Smart App Control policy (`Code Integrity` 3033/3077,
policy `{0283ac0f-fff1-49ae-ada1-8a933130cad6}`). This per-hash cloud/policy
result is recorded as an unsigned-community compatibility limit; security was
not disabled and another rebuild was not used to manufacture a green hash.

Read this section, then work the open items in the order given; everything below
it is the evidence, not a second plan.

Two named audit failures must be interpreted at their capture time rather than
turned into work automatically. `运行本体一致` is expected only when a running
product reports an older HEAD than the checkout; there is no running-product
claim while the product is stopped. `codex 开账后归属` is the known ledger gap;
the checklist says do not make it green.

**What an agent can advance without the Owner, highest value first:**

- **The long-lived-process attribution drift (P2, 2026-08-30) is a monitored
  open item, not a release blocker.** Its historical mechanism is still
  unlocated. A 2026-09-07 restart reproduced the required measurement shape --
  the quota window began 2,308.611 seconds before the new process -- but both
  panel and observer recovered the same pre-start Codex total and per-day
  attribution. Commit `52e5435` now makes the panel compare its frozen Codex
  `total + days` attribution with a direct-disk v2 replay every 30 seconds and
  record `attribution_self_check` if they disagree. Provider window timestamps,
  including their observed one-second jitter, are deliberately not compared.
- **`test_desktop`'s lost state write.** Eight threads finish, none raises, one
  key is missing. `state_file_lock` is ruled out by measurement and the join
  race is fixed. The wipe-on-unread path is closed (`_load_state` no longer
  treats a locked file as empty). A 7-of-8 missing key under a lock that held
  is still open. The test prints which keys survived, what failures were
  reported and how many replaces ran -- **do not hunt it with a batch loop; it
  is about one run in forty-five and it will name itself in the next suite that
  catches it.**
- **The OTLP shadow disagreement.** Located, still undecided, and the
  billing measurement does not decide it. The vendor 7d used% was 88
  nine seconds before the omitted sol completion and 88 twelve minutes
  after; 5h, the window that could have shown ~13k tokens, has no row
  between 2026-09-05 and 2026-09-07. Do not move any number on a sample
  of two.

**Standing constraints.** Do not push. Do not restart the product unless the
Owner says so. Never write a byte into `~/.claude`, `~/.codex` or `~/.grok`;
point every probe's `SANDGLASS_HOME` at a temporary directory. Confirm no other
agent is writing this tree before starting -- two rounds of concurrent agents
here produced one false failure and one change swept into another session's
commit.

**Two environment traps, both measured.** `python -m tools.audit` and
`tools/build_windows_release.ps1` cannot run inside the Claude app container:
state-home attestation fails closed there, so run them from a `schtasks` task
with output on `D:\` (`D:\build-tools\outside\` holds the scripts). And reads are
shadowed too -- a file present both in the real state home and in
`Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\sandglass` is served from the
shadow silently, with the same name, size and mtime. `observer-coverage.json`
is one; four rounds were spent chasing a bug that did not exist because of it.

**The bar for a change.** Say the mechanism and the measurement that would
falsify it before writing code. Mutation-verify every fix: put the defect back,
and the new test must fail for the reason it claims. Do not assert on source
text -- three tests here matched their own docstrings and comments. Read a
process's own exit code, never a pipeline's. Then
`python -m unittest discover -s tests -q` and `python -m tools.check_test_isolation`
(exit 0), and re-run the audit out of container for anything that touches
state, attribution or the installer.

## P0 - truthful official-source boundary

- [x] Ignore third-party Codex account registries.
- [x] Ignore community Grok App profiles, logs, billing cache and derived plan data.
- [x] Start a clean official-only Grok identity ledger; do not migrate the mixed legacy ledger.
- [x] Pass unit, installed-wheel and vendor-directory byte-invariance tests.
- [x] Re-run live HTTP/audit checks after restarting the Sandglass service.

## P1 - correctness and stability

- [x] Verify cold start with no providers, each single provider and all supported providers; partial auth must not create placeholder accounts.
- [x] Verify identity/auth changes, quota resets/refills, 429 rate limits and expired/rejected credentials with isolated provider-state tests.
- [x] Verify Codex and Grok cross-process identity-ledger changes invalidate a running HTTP panel without restart.
- [x] Show not-scanned, signed-out, unavailable, rate-limited, expired and unassigned states explicitly; never infer missing history.
  Verified 2026-09-07 on a machine with nothing on it -- empty `SANDGLASS_HOME`
  and all three vendor homes pointed at empty directories -- rather than taken
  from this box. `/api/quota` answers `accounts: []` with per-provider
  `available: false, status: not_observed` and a `degradation` line;
  `/api/report` is all zeros, which alone would read as "you used nothing", and
  the panel does not read it alone: `capability(p, "local_usage").available`
  picks between `notScannedTitle`, `noLocalLoginTitle`, `providerNoLoginTitle`
  and the offline copy. A grep for the API's own status strings in index.html
  finds none, because the UI keys are worded differently -- do not conclude
  from that grep that the states are unrendered, as I nearly did.
- [ ] Complete a non-destructive live provider-state acceptance pass when real account changes and reset/refill events are available.
  Attempted 2026-09-06 from this checkout (`f940645`, later the identity-write
  split). No login switch, credential expiry, 429 or reset/refill occurred
  during the session, and none was manufactured -- vendor directories stay
  read-only. `quota-observations.json` in the real state home (not the Claude
  package shadow; those two copies differ in mtime and size) holds historical
  100% windows, which is stored history, not a live event. Isolated provider
  tests already cover the shapes. This box stays open until a real change is
  watched.
- [ ] Validate clean Windows standard-user installation and uninstall without touching provider directories.
  Not the same as the 2026-09-06 P3 per-user smoke, which ran as this
  development account. This session has no second Windows standard user and
  did not create one. The remaining gap is a different user / clean machine.
- [x] The dropped closing gap write in `_end_codex_observation` is not a defect.
  Enumerated 2026-09-06. The premise it rested on is wrong: on Windows the panel
  does not observe Codex. `serve()` delegates to `ensure_observer_running()`
  rather than starting a watch of its own, and the desktop's startup call passes
  `observe_codex=False`, so every v2 writer is an observer process.
  Of the append's refusals, three are unreachable from this caller -- the
  timestamp, kind and reason are literals or already filtered. The reachable ones
  are a damaged file, which the reader already renders as an unassigned boundary
  at the malformed cutoff and which extending would destroy; a last event that is
  already unassigned; and a newer event, which only a handover observer can write
  and which means either that someone is observing (a gap would be wrong) or that
  the state is already unassigned. `tests/test_observer.py::ClosingBoundaryTests`
  pins the property that makes each refusal safe: after the attempt, now is
  either unowned or owned by an observation newer than the refused boundary.
- [x] Give the attribution audit something that can fail on unattributed volume.
  Closed in `50a5d0c`. `归属分区` now asserts only `duplicate_minutes == 0` — a
  minute claimed by more than one account. The coverage percentage is
  disclosure. Unowned volume is `开账后归属`'s job, which can go red (and does,
  on this machine, for pre-v2 Codex history the Owner has ruled out of scope).
- [x] Stop `开账后归属` silencing itself with the product's own projection.
  Closed in `50a5d0c`. Codex ownership is re-derived through
  `_identity_ledger_rows` against the v2 file and this module's own schema
  check. `codex_identity_runs()` can no longer reclassify an unattributed
  minute as a coverage gap. Claude and Grok keep their accessors; those
  ledgers cannot silence anything.
- [x] Reconcile at least one real OTLP event carrying account identity.
  Live 2026-09-06, canonical state home, outside an app container. After the
  ingest started preferring the `event.name` attribute over Codex 0.149's
  tracing-target `event_name`, two `official_otel` rows landed:
  `codex.sse_event/response.completed`, both with an account id. Codex
  receipts: 28 requests, 59 received, 2 accepted, 57 rejected (the rest are
  non-completion traffic). `telemetry_status` is `identity_observed`.
  `OTLP 影子匹配` ran instead of skipping: 身份分钟 1，精确匹配 0，数值不符 1，
  账号冲突 0；未并入总量. The line asserts conflicts, not exact token
  equality; the mismatch is recorded, not repaired. A fixture was not
  posted at the live 7740 receiver. Claude and Grok share the receiver,
  PATH launcher and event-name fix; their live identity-bearing events
  were not captured. Owner treated that as close enough.
- [x] The coverage ledger's extra open run is a scar from a fixed defect, not a
  live one. Read outside the app container -- the file has a shadow that froze on
  2026-09-02, and a read from inside is served from it silently -- the real ledger
  holds 49 runs with exactly one left open, started 2026-09-02T06:01:40. The run
  after it began 21 seconds later and was recorded as `observer_interrupted`, so
  the close was attempted and lost. That is the handover race
  `_heartbeat_locked`'s own comment describes, and until `e6e3ca8` the Windows
  file lock gave up after nine seconds and let both writers proceed. Split around
  that commit: 45 runs before it with one open, 4 runs after it with none. Four is
  consistent, not proof; the mechanism is the reason for the verdict. Nothing
  reads a mid-ledger open row -- `observer_status` reads only the last -- and the
  row is honest evidence of an interrupted run, so it stays.
- [x] `note_codex_identity()` wrapped both the vendor read and Sandglass's own
  writes in one `except`, and wrote the same coverage boundary for either. Those
  are different facts: not being able to see who is signed in leaves minutes with
  no owner anyone could name, while not being able to record what we did see is
  Sandglass being broken -- and the boundary it wrote went through the path that
  had just failed, so it usually did not land either. The read still becomes a
  gap, with its own reason; a failure of our own is recorded as a component
  failure where a broken state directory cannot swallow it. Without that, a
  permissions error or a redirected state home takes Codex attribution to zero
  and nothing anywhere says so.
- [x] `note_current_identity()` still swallowed Claude and Grok the old way:
  one `except` around the vendor read and the ledger write, then `pass`. A
  read-only state home looked like "this vendor said nothing". Split the same
  way Codex was split: vendor read stays isolated; a write failure is recorded
  as `claude_identity_write` / `grok_identity_write` and re-raised after the
  other providers have had their turn. Mutation: the new tests failed on the
  unsplit function with `OSError not raised`.
- [ ] The one OTLP shadow group on this machine disagrees with local parsing,
  and the vendor's own transcript is the reason. `OTLP 影子匹配` passes on `account_conflicts
  == 0` and prints `数值不符 1` without asserting it, so the disagreement is
  displayed and ignored. Measured out of container, one Codex session, one
  minute, two `codex.sse_event/response.completed` rows -- and they are
  different models. The OTLP side has both; the local minute has one of them.
  Figures redacted: they are a development machine's own usage. The shape is
  what matters and it is reproducible from `D:\build-tools\outside\otlpmatch.py`.

  **Located 2026-09-07, out of container.** The two rows are a `gpt-5.6-sol`
  completion with input tokens and no output, and a `gpt-5.6-luna` completion a
  few seconds later. The vendor transcript this product parses contains **no
  trace of the sol call at all**: its usage-bearing lines are the luna turn and
  the next day's turn, and its `turn_context` names luna seven seconds after the
  sol response completed. The sol call's input is not displaced to another
  minute in the local ledger -- it is absent from the conversation total
  entirely.

  So the transcript is not a complete record of completed responses, and
  Sandglass's totals come from the transcript. What is NOT established is
  whether that call was billed. No output tokens plus a `turn_context` switching
  model seven seconds later reads like a turn that was abandoned or re-routed --
  and a completed response usually bills its input either way. Both readings fit.

  **Do not change accounting on this.** One conversation, one occurrence, and an
  undecided premise; adding the OTLP tokens to totals would be ledger work built
  on a guess, and dropping the question would be worse. What it does settle: the
  audit is right not to gate on `token_mismatches`, and the OTLP receiver is the
  only local witness to a completion the transcript omits.

  **Billing measurement, 2026-09-07, from `quota-readings.jsonl` in the real
  state home (no package shadow, so it reads truthfully from here).**
  Inconclusive, and the reason is the instrument: the vendor reports 7d usage as
  an integer percent, and it did not change across the completion -- which a
  call of that size need not have moved either way. The 5h window, fine enough
  to have shown it, has no reading in the gap at all. The session's one
  `token_usage_record` that collectors do not parse is the next day's luna turn,
  already covered by `token_count`, not the missing sol call. This measurement
  cannot tell billed from unbilled. Still do not change accounting.

  Base rate, measured the same way: the telemetry store on this machine holds
  **two OTLP rows in total**, and this is one of them. So "one occurrence" is
  not a rare tail -- it is half of everything the receiver has ever collected
  here, and the other half matches. Which also says what `OTLP 影子匹配 PASS`
  is worth today: with n=2 it is not yet an instrument that can validate
  anything, and reading it as agreement between two independent sources would
  be reading a sample of two as a result.
- [x] A fresh clone shipped licence bytes nobody audited. No `.gitattributes`
  existed and this repository sets `core.autocrlf=true`, so a checkout rewrote
  LF to CRLF in the two files whose exact sha256 the provenance record and
  `test_release` pin. Measured with `git worktree add` at `3f3ba2d`:
  `LICENSE-Geist.txt` came out 4475 bytes against the audited 4383, and both
  licence tests failed. The working tree here passed the whole time because its
  copies predate the conversion -- the machine that would have shipped
  unreviewed bytes is a *clean* one, which is every new contributor and every
  build machine. Now declared `-text`, and a test asserts both halves: the
  declaration exists, and the bytes on disk carry no CRLF.
- [x] The two quota state writes dropped their failures in silence, which is
  the effect `_write_json_atomic`'s own docstring warns about: without the
  anchors, `quota-observations.json` reads as a machine that never saw a reset.
  It caught the OSError, cleaned up its temporary, and returned nothing. Now
  reported as `quota_observation_write` / `quota_cache_write`, and cleared on
  the next write that works. Still not raised -- both callers are bookkeeping
  inside a quota fetch, and a fetch that has an answer must not fail over a
  file it could not save; this is the same call the desktop state save makes.
  Mutation: drop the record and the test fails
  `[] != ['quota_observation_write']`.
- [x] `_note_quota_reading` was the same swallow on the jsonl history.
  It caught OSError and returned. quota-observations still stored the
  new used%, so the next poll saw the value as already current and never
  tried the append again -- a missing row used to mean the value had not
  moved, and the step was gone. Now reported as `quota_reading_write`,
  cleared on the next write that works, still not raised. Mutation: the
  new test failed `[] != ['quota_reading_write']`.
- [x] `record_quota_signal` was the same swallow on the only local
  witness that a quota signal fired. The watch loop had already
  refreshed; losing the jsonl row in silence looked like a quiet
  interval. Now `quota_signal_write`, same split. Mutation: the new test
  failed `[] != ['quota_signal_write']`.
- [x] `live_snapshot._read_own` treated every OSError as "no file", so a
  locked snapshot became `{}` and the next `record` wrote only the new
  reading over the top. The audit reconciles against that file instead of
  asking the panel; a truncated history, or a skip because the file no
  longer looks like a live writer, is skip-is-not-pass. `_write_own`
  swallowed the write the same way and `record` still stamped
  `_LAST_WRITE`, so a failed save was treated as stored. FileNotFoundError
  is still the first record. Any other OSError now reports
  `live_snapshot_write` and leaves the file; `_LAST_WRITE` is only
  stamped after a write that landed. Mutation: the new tests failed with
  the kept reading replaced by `{"new": 2}`, and `[] != ['live_snapshot_write']`.
- [x] `_read_payload` was the same swallow on `runtime-diagnostics.json`.
  A locked file became empty, and the next `record_component_failure`
  wrote only the new component over the top -- the erasure the file lock
  closed for concurrent writers, reached through a failed read. The
  recorder still does not raise (it is called from except handlers).
  Mutation: the new test failed with `kept` replaced by `new`.
- [x] `_read_coverage` treated every OSError as "no file", so a locked
  `observer-coverage.json` became `{runs: []}` and the next
  `_begin_coverage` wrote a single new run over the top. That is the
  erasure the file lock closed for overlapping observers, reached
  through a failed read -- the ledger the package shadow made
  untrustworthy for four rounds. FileNotFoundError is still the first
  observer. Any other OSError now reports `observer_coverage_write` and
  leaves the file; a later heartbeat puts the live run back on top of
  the history. `observer_status` on an unreadable file is `unreadable`
  with `active: true`, not `not_started`, so a healthy observer is not
  killed. An unreadable ledger is not treated as a first run for the
  Codex gap. Mutation: the new test failed with the two kept runs
  replaced by one new timestamp.
- [x] `_read_state` in the updater was the same swallow.
  A locked `update-check.json` became `{}`, the check ran, failed, and
  wrote `{offer: {}, error: ...}` over a cached offer. Now
  `update_state_write`, and the cached offer stays. Mutation: the new
  test failed with the cached `9.9.9` offer replaced by `{}`.
- [x] `_read_json` was the remaining half of the quota-state finding:
  a locked `quota-observations.json` became `{}` and `note_quota_windows`
  wrote only the window it had just seen -- every other reset anchor
  gone, the machine reading as one that had never seen a reset. Vendor
  files still go through `_read_json` (unreadable vendor is "said
  nothing"). Ours go through `_read_owned_object`: FileNotFoundError is
  empty, any other OSError reports `quota_observation_write` and leaves
  the file. Mutation: the new test failed with the grok and claude
  anchors replaced by a single new `codex:a1:5h` window.
- [x] The quota cache merge was the same swallow on a targeted refresh.
  A locked `quota-cache.json` became `{}` and the merge wrote only the
  provider that had just been fetched. Now `quota_cache_write`, and a
  targeted refresh that cannot re-read does not replace the file.
  Mutation: the new test failed with claude and grok replaced by only
  codex.
- [x] `runtime_provenance._write` was the same swallow on the file the
  audit and a second launch read. It caught OSError and returned, while
  `record_runtime_identity` had already stamped `_ACTIVE`, so in-process
  reads looked healthy and a later launch warned that a different version
  was already running. Now `runtime_provenance_write`, `_ACTIVE` is only
  stamped after a write that landed, still not raised. Mutation: the new
  test failed `[] != ['runtime_provenance_write']`.
- [x] `attribution_mode` treated every OSError as empty, so a locked
  `product-mode.json` became unselected. GET `/api/product-mode` then
  answered `selected: false`, the chooser appeared over a choice that was
  still on disk, and a save replaced the file. The panel already refuses
  to treat a failed GET as unselected; the server was the remaining half.
  FileNotFoundError is still the first run. Any other read failure reports
  `product_mode_write` and leaves the file; `mode_payload` raises so the
  GET is not a successful unselected payload; `attribution_mode` still
  returns empty so a report does not guess `single_official`. Mutation:
  the new tests failed `[] != ['product_mode_write']` and
  `PermissionError not raised` / `JSONDecodeError not raised`.
- [x] The pywebview `show()` path was the remaining half of the native
  dead-switch finding. The flag was already not set, so the next click
  retries; the orb still swallows the return, so a failed show looked like
  a quiet click. Now `fallback_panel_show`, still not raised. Mutation:
  the new test failed `[] != ['fallback_panel_show']`.
- [x] `_raise_panel` was the remaining dead switch, on the tray path.
  Tray click on an already-open panel is raise, not toggle. A failed
  pywebview `show()` was swallowed with `_panel_open` still True, so
  every later tray click took that branch and did nothing; the orb's
  toggle hides first and would have recovered. A native `activate()`
  that raised escaped the same way. Now recorded as `fallback_panel_show`
  / `native_panel_show` and the flag is cleared. Mutation: the new tests
  failed `True is not false : 唤起失败之后不能把面板记成开着的` and
  `OSError: dispatcher gone`.
- [x] `live_snapshot.record` reported OSError and ValueError, then a leftover
  `except Exception: return` dropped everything else. A broken process
  identity stamp looked like a quiet interval. One handler now records
  `live_snapshot_write` for any failure. Mutation: the new test failed
  `[] != ['live_snapshot_write']`.
- [x] The identity-write reporting could not see the write fail, so this
  morning's split was inert for the commonest real failure.
  `_write_json_if_changed` returned False both for "the content already
  matched" and for "the write raised", and all six callers read False as the
  first. With the state directory present and lockable but the write itself
  failing -- a full disk, or that one file locked while the directory stays
  writable -- every identity ledger stopped being written and nothing raised,
  so `note_current_identity`'s new `record_component_failure` never ran.
  Measured before the fix: returned False, recorded nothing, wrote nothing.
  The earlier tests patch `_append_run` to raise, which is why they passed
  either way -- a test standing next to the mechanism rather than on it.
  A failed write now raises. Two call sites decided explicitly: the receipt
  writer swallows its own failure, because the ledger write it describes has
  already succeeded and reporting it as a failed ledger write would be a lie;
  and the CLI merge inside `grok_identity_runs()` -- which is a read the panel
  makes -- records `grok_identity_merge_write` and carries on with the merge it
  already holds. Mutation: back to `return False` and the two new tests fail
  with `OSError not raised` and `[] != ['grok_identity_merge_write']`.
- [x] `_save_state` dropped the user's panel state in silence. `except OSError:
  pass` covered the whole write, so a read-only state directory, a locked file
  or a full disk discarded window position, size and which panel was open with
  nothing anywhere saying so -- and it is also the swallow that would hide a
  genuine lost write in the concurrency question below. Still not raised: these
  saves are opportunistic and must not take the panel down. Now recorded as
  `desktop_state_write` and cleared on the next good save, the same split the
  identity ledgers and the diagnostics file already have. Mutation: back to
  `pass` and the test fails `[] != ['desktop_state_write']`.
- [x] `test_concurrent_state_updates_keep_all_keys_and_replace_atomically` could
  not tell a lost write from a slow one. `thread.join(timeout=10)` returning is
  not the writer having finished, and the test read the state file straight
  after, so a writer still waiting on the file lock was reported as a vanished
  key -- which is the defect the test exists to catch. Seen once in a full
  suite on 2026-09-06. It now fails with "写入线程还没结束" instead. Whether
  `_save_state` can genuinely lose a write is still open: 20 solo runs and 12
  under six CPU burners did not reproduce it, and `except OSError: pass` there
  would swallow a real failure without a word. Next occurrence should now name
  which of the two it is.
- [x] `_load_state` treated every OSError as "no file". A locked or briefly
  unreadable `desktop.json` became `{}`, and the next `_save_state` wrote only
  the new keys over the top -- a wipe whose write succeeded, so
  `desktop_state_write` never fired. That is the "file wiped and rebuilt"
  shape the concurrent test could not distinguish from a silent failed write.
  FileNotFoundError is still empty (the first save). Any other OSError now
  raises into the existing reporter and leaves the file. Mutation: the new
  test failed with `'{"new": 3}' != '{"keep": 1, "also": 2}'`. This does not
  close a 7-of-8 missing key under a lock that held; that still names itself.
- [x] `attribution.sessions_without_identity()` deleted: dead, and a second
  definition of the product's most delicate concept sitting beside the live
  one. Referenced nowhere -- not in the package, tools, tests, docs or the web
  UI. What `report.py` actually uses is `sessions_unclaimed_by_accounts()`,
  which is the exact complement of `sessions_for_account()`: same `owns_minute`
  predicate, negated over every peer, so the partition is exact by
  construction. The dead one used a different rule -- "the provider timeline
  named nobody" -- and the two disagree precisely when the timeline names an
  account that is no longer discoverable, say one the user has since signed out
  of. Under the live rule those minutes are unassigned; under the dead one they
  would be neither owned nor unassigned, and would leave the breakdown while
  staying in the total. Nothing was wired to it, so nothing was wrong today;
  what was wrong is that the trap was loaded and looked like an API.
- [x] `commit_import` had one path left that acted on an unlocked read. A
  package the preflight found both retained and active returned
  `persisted=true, idempotent=true` straight from that preflight, without
  entering the transaction that every neighbouring path uses. Measured: commit
  rev-1, take the preflight, let another writer commit rev-2, then hand the
  earlier preflight back -- the caller is told `active_revision: rev-1` while
  the database says rev-2, and no error is raised, though "active import
  changed after preflight" is exactly what the same function raises two
  branches down. This is the other half of the fix that moved the
  retained-but-inactive case into the transaction. Now confirmed under
  `BEGIN IMMEDIATE`: still retained, still active, or the same refusal.
  Mutation: the confirmation removed, the test fails with `ValueError not
  raised`.
- [x] The OTLP ingest gate was reviewed and is correct; what was missing was
  a test that says why. A page in the user's browser is on loopback, so the
  loopback check does not exclude it, and a cross-origin POST goes out without
  the browser asking first only for text/plain,
  application/x-www-form-urlencoded, multipart/form-data, or no content type at
  all -- anything else needs a preflight, and this server implements no
  OPTIONS. The accepted set already excludes all four, so nothing was open.
  The existing test pinned application/json, which a page cannot send unasked
  either, so widening the set to text/plain would have kept it green. Now
  pinned: mutation adds text/plain and the empty type, and the forged body
  reaches the protobuf decoder -- the failure is `400 != 415`, which is the
  claim itself, not a status-code preference. Note for whoever reads this next:
  the gate is at `serve.py:921`, and it was nearly reported here as missing
  because a grep excluded `serve.py` from its own output.
- [x] Two docstrings said a read creates no state, and that is not what
  `mode=ro` means. Measured: reading a WAL database read-only creates a -shm
  and a -wal that outlive the connection. No row, table or database is created,
  which is what the callers actually need; the sentence now says that instead.
- [x] Out-of-container audit at `9dc1872`: **50/52**, exit 2, via `schtasks`
  with output on `D:\` (attestation ok, real state home, not the package
  shadow). `解析层 vs 厂商每轮` now passes -- the stale baseline was the fault,
  as `5c8be4b` said. The two remaining FAILs are both expected: `运行本体一致`
  reports running HEAD `5c8be4b` against tree `9dc1872`, because the product
  was left running on purpose and is not being restarted from inside the
  container, and `codex 开账后归属` is the known ledger gap that must not be
  made green. The build and installer smoke was NOT run this round: it
  installs, launches and uninstalls a packaged copy, and a copy of Sandglass
  is running. That one waits for the Owner to stop the product.
- [x] `runs left open: 2` re-checked at the source, read outside the container.
  Run #32 (2026-09-02) and run #60 (live, heartbeat current). #32 is the
  pre-`e6e3ca8` scar, and 27 runs have closed cleanly since, so nothing
  regressed. It also claims nothing: `_begin_coverage` only ever repairs
  `rows[-1]`, and every reader -- `observer_status`, `_open_run_last_heartbeat`,
  `_run_last_heartbeat`, the audit's book-opening -- looks at the last row or
  at one named `started_at`. No consumer unions the rows, so a stale open row
  in the middle is inert, not four days of coverage the observer did not have.
- [x] Runtime diagnostics were written by three processes with no lock. A
  recorded failure is a read, an edit and a replace, and only an in-process
  RLock guarded it -- the same defect the observer's coverage ledger had.
  Measured before the fix: two processes recording 60 components each left 58
  of 120 in the file, one side gone entirely, because whoever read first wrote
  the other's failures back out of existence. So a machine with two broken
  components could show one of them, or neither, and stay wrong until the next
  failure. `record_component_failure` and `clear_component_failure` now take
  `state_file_lock`. Reading stays lock-free, the way every other ledger here
  is read, so asking for diagnostics still creates no state; clearing a
  component that is not recorded returns before the lock, which is both the
  common case and the reason the isolation checker saw a lock file appear in
  three modules' homes. The lock file is diagnostics' own, not the shared state
  one: a failure is usually recorded from an except handler, sometimes one
  reached while another state file is held, and `state_file_lock` never gives
  up -- a report queued behind the write it reports on would hang. Mutation:
  with the lock removed both tests fail, 13 of 80 recorded and the second
  process not waiting.
- [x] The unwritable-state-home case is not the diagnostics channel's to carry.
  Measured: with `meter_home()` unwritable, `record_component_failure` returns
  an issue that goes nowhere and `runtime_diagnostics()` reports no components
  -- indistinguishable from health. It is unreachable in the product: the
  attestation probe opens its file with `GENERIC_WRITE | CREATE_NEW`, so cli,
  desktop, observer and serve all fail closed before any of this runs. Do not
  add a fallback channel for it; the backstop is attestation.
- [x] Two rounds of concurrent agents on one working tree. HEAD moved under a
  running audit once, producing a failure whose assertion exists in no commit,
  and an uncommitted change of mine was later swept into another session's
  commit. Nothing was lost either time, but neither is a reliable handover.
  **Confirm the other session has stopped, or use `git worktree add`, before
  taking over.**
- [x] Identify the intermittent suite error. **Found 2026-09-07.** It has a name:
  `test_panel_transport_parity.OtlpListenerSurfaceTests.test_it_still_ingests`,
  which binds an ephemeral port and posts to `/v1/logs`. It presented as an
  ERROR with no cause; a refused connection is now a FAIL that names the
  listener. It is **order-dependent**: 60 consecutive runs of that class
  alone did not reproduce it, and it has only ever appeared inside a full
  suite run. Something else in the suite interferes with it.
  A later 8-run full-suite loop reported 0 failures while `accounts.py` and its
  tests were being edited, so that loop does not count. A clean 10-run loop at
  HEAD `ff6e311` with an untouched tree — 602 tests each, 77.6s–95.8s, all
  `OK`, no `ERROR`/`FAIL` in any log — also did not reproduce it. Recorded as
  order-dependent, about 1-in-5 when it was seen, unseen in 60 isolated class
  runs plus 10 clean full suites; not treated as gone.
  The next occurrence is now a FAIL that names the listener, the wait, and
  the URLError cause, not an ERROR with no reason. `_post` also isolates
  `SANDGLASS_HOME` so a refused connection cannot be confused with a write to
  the real state directory. This does not fix the interference.
  2026-09-06 measurements at `f940645`, live desktop PID 27452 on 7740 left
  running, this process not in the Claude container (`meter_home` is
  `%LOCALAPPDATA%\sandglass`; the package shadow exists but mtimes differ):
  Windows completes TCP before `serve_forever` (50/50 connect-ok); `bind(0)`
  did not land in an excluded port range (0/200 concurrent); the class and
  the whole module did not fail (30 isolated, 40 module runs); all 363 tests
  that `discover` runs *before* it, then 30 hammers, did not fail. Three
  isolated-home full suites (`SANDGLASS_HOME` under `%TEMP%`, python exit
  read from `$LASTEXITCODE` not a pipe) were 641/OK in 77–81s. The
  interferer is still unnamed. No sleep, retry or timeout was added.
  Later the same day it failed **alone**, in `tools/check_test_isolation.py`,
  which runs each module by itself in a fresh process against an empty home --
  so "order-dependent, never reproduces solo" is now wrong, or that run was not
  as solo as it looks. The occurrence itself was thrown away: the checker only
  read the child's exit code. It now prints the last 25 lines of a failing
  module's output, so the next one leaves evidence. The run right after it was
  clean, and every module was clean on the two runs after that.
  A second occurrence, same shape, different module: `tests.test_telemetry`
  errored once (1 of 41) right after an unrelated file was restored, and the
  eight runs after it were clean. Both modules stand up a
  `ThreadingHTTPServer` on an ephemeral loopback port in a thread, which is the
  only thing they visibly share. The output of that one was destroyed by
  running it as `-q | tail -3` -- the same mistake the checker was just fixed
  for. Capture the whole run when hunting this.
  2026-09-07: **30 consecutive clean full suites** at `66d4e32`, each in its
  own process with a fresh `SANDGLASS_HOME`, run from a `git worktree` copy
  with the ignored native runtime copied in. No reproduction. That is not
  consistent with the "about one in five full suites" this was first recorded
  as, so one of two things is true: something that landed today removed the
  interference, or the worktree differs from the working tree in a way that
  suppresses it. **This does not clear the working tree** -- the tree itself
  was the variable I changed, to stop editing under a running loop. The same
  batch in the working tree is the next measurement, and it has to run with
  nobody editing.
  That measurement ran, and it **reproduced on run 5 of 20 in the working
  tree** at `589c88a` -- with nobody editing, and with 30 clean runs in the
  worktree copy behind it. So the working tree reproduces at roughly the
  originally recorded rate and the worktree copy did not, in 50 runs total.
  The failing test is `test_desktop`'s concurrent state update, this time
  `KeyError: 'concurrent_0'` where the earlier one was `concurrent_7`.
  It got past the `is_alive` check added the day before, so **it is not a
  writer that had not finished**: eight threads completed, none raised, and a
  key is missing from the file. `state_file_lock` was ruled out by
  measurement -- two threads in one process serialize on it, 0.000s/0.300s and
  0.301s/0.601s -- so a lost read-modify-write is not the explanation either.
  What is left is a write that failed and said nothing, or a file that was
  wiped and rebuilt. The test now reports which: it captures
  `record_component_failure` and, on failure, prints the keys actually present,
  the failures reported, and the replace count.

  **The listener half is solved, and it was the server, not the test.** Every
  refusal in `Handler.do_POST` happens before the request body is read -- wrong
  content type, no length, path not served -- and closing a socket that still
  has unread bytes in its receive buffer is an abortive close on Windows. The
  client's next read then fails with `ConnectionAbortedError [WinError 10053]`
  instead of seeing the status that was just written. That is exactly the error
  the captured run showed, in
  `test_the_telemetry_listener_routes_no_account_api`.

  Measured against the handler: a 4 MiB body to a path it answers 404 came back
  aborted **9 times in 10**, 1 MiB **3 in 10**, and the 2-byte body the suite's
  own `_post` sends almost always got through -- which is the whole reason this
  looked rare, order-dependent and unrelated to the code it appeared in. It was
  never order-dependent; it was size- and timing-dependent, and every full
  suite rolled the dice a few times.

  `send_error` now drains a body it would have been willing to read before
  answering, once, for every refusal present and future; a body larger than
  this server would ever accept is still not read and that connection is
  allowed to die, which is the honest outcome of refusing it. Mutation: remove
  the drain and the new test fails with the production error verbatim.

  This is a product defect, not only a test one: a vendor exporter posting to a
  refused endpoint got an aborted connection rather than the 415 or 404 that
  says what was wrong.

  **The `test_desktop` half is still open** -- it is a different failure with a
  different signature, and the instrumented assertion has not caught it yet.

  Confirmed: **20 clean full suites in the working tree** after the fix, where
  the two batches before it failed at run 5 and run 6. If the rate were still
  one in five and a half, twenty clean runs would happen about 2% of the time.
  Evidence, not proof, and the `test_desktop` one is rarer than that anyway --
  once in roughly forty-five runs so far.
- [x] Review `orb.py`, `desktop.py`, `capabilities.py` and `runtime_provenance.py`.
  Audited 2026-09-06; eight findings, three release-relevant, listed below. The
  claim first written here -- that no review evidence existed -- was wrong:
  `tests/test_desktop.py` is 874 lines and 57 tests. `orb.py` is the module with
  essentially none: three tests for 1043 lines, one of them a source-text grep
  that cannot fail if the code it names is commented out.
- [x] `--stop` reports success before the observer has released the program files.
  It discards `request_observer_stop()`'s result and returns 0 unconditionally,
  while the observer's exit is bounded by `_stop_quota_watchers` joining a thread
  that may be inside a 15-second vendor request. `packaging/sandglass.nsi` waits
  `Sleep 2000` and then gates on the **desktop** mutex, though its own comment two
  lines above names the observer as the process holding the files being replaced;
  `Local\Sandglass.Observer.SingleInstance` is never consulted, and `ExecWait`
  reads no exit code, so `--stop` returning 2 on a failed state-home attestation
  signals nothing. Verified by reading all four sites 2026-09-06. This is the one
  finding that can leave a user with a half-replaced install.
  Fixed 2026-09-06 and verified by a full build plus installer smoke run outside
  the app container, exit 0. `--stop` now waits for the mutex and reports; the
  wait is derived from HTTP_TIMEOUT_SECONDS and the provider count rather than
  chosen; both paths read the exit code and re-query Windows themselves.
  Two things surfaced only because the guard was exercised: NSIS's generic
  Abort code 2 could not say which gate refused, so each sets its own level; and
  every mutex probe read GetLastError with a second System::Call, which the
  System plugin's own Win32 calls clear in between -- that branch had never once
  run, so a mutex nobody held was reported as unverifiable. `? e` captures the
  error at the call.
- [x] `EdgeDock` re-derived the monitor from the tucked rect, which is 8px on its
  own monitor and the rest on the neighbour, so `MonitorFromRect` with
  DEFAULTTONEAREST selected the neighbour and a peeked panel slid to the wrong
  screen. Fixed in `cf344b3` by latching the monitor when the dock is decided.
  Confirmed on real hardware 2026-09-06, two adjacent 2560x1440 screens: driving
  the real `EdgeDock` against the real Win32 calls, the old code peeked back to
  x=0 on the primary monitor after docking to the secondary's left edge at
  x=2560; the fixed code returns to 2560. The tests that were there patched
  `monitor_work_area` to a constant, which removes exactly the selection that
  breaks.
- [x] Packaged provenance validated its own manifest's shape but never recomputed
  `build_id`; any 64 hex characters passed, so a hand-edited `git_head` kept a
  digest that no longer described it. `cec7c33`..`HEAD` recomputes it with the
  same rule `tools/windows_release.py` already applies to the same bytes. This
  proves the manifest is self-consistent, not authentic -- only a signature does
  that, and the release is not signed yet.
  The second half of that finding does not hold and is recorded so nobody
  re-opens it: the packaged tree really has no editable Python. `_internal/
  sandglass` contains the manifest plus `native/` and `web/`, because PyInstaller
  packs the modules into the PYZ, and `build_provenance` already binds those two
  resource trees separately. A digest there would restate them, not describe the
  Python.
- [x] `panel_x`/`panel_y` was written in physical pixels by the native shell and in
  logical DIPs by the pywebview fallback, and read as logical. The two coincide at
  100% scaling, which is why it survived. Confirmed on real hardware 2026-09-06
  with one screen at 150%: the same window is physical (1200, 300) and logical
  (800, 200), so a position written by one path and read by the other lands 600px
  away. Both writers now store physical -- absolute, and the same number means the
  same place on whichever screen it is restored -- and the one reader converts,
  reusing the `monitor_scale` that orb.py already had rather than adding a second
  way to ask.
- [x] `show_panel` set `_panel_open` before the native `show()` could fail, the orb
  swallows the exception, and the native `hide_panel` never resets it -- one failed
  show turned the orb into a dead switch for the rest of the session with nothing
  recorded. Fixed in `cf344b3`: the flag follows a successful show and a failure is
  recorded where it outlives the click, matching what the pywebview path already did.
- [x] `_packaged_self_test`'s state-home branch cannot fail through `main()`,
  which returns 2 first, and attestation successes are cached so a second call
  cannot fail after the first succeeded. Left in place and recorded rather than
  removed: it still guards a direct call, which the tests make, and nothing
  distinguishes the self-test codes -- both callers only compare against zero.
  **Do not build a gate that tells 2 from 5.**
- [x] `_quota_status` decided availability from one account and reported the
  status string from another, so an account whose own refresh had failed could be
  shown as "live" on the strength of a second account with no window at all. The
  status now comes from the accounts that actually hold a window.
  Also fixed alongside it: `_source_runtime_evidence` took `content_sha256` from
  `read_bytes()` and `bytes` from a later `stat()`, recording one row that
  described two versions of a file edited in between. Both now come from the one
  read.

Note for any acceptance run: `codex 开账后归属` is red on the development machine
by design -- (figure withheld) tokens whose pre-v2 observations the current code no
longer reads. `ae95966` is what made it visible; before that the window covered
(figure withheld) tokens and reported PASS. It is ledger completeness, which the Owner
has ruled out, and it is recorded in `docs/handoff-2026-09-02.md`. Do not treat
it as a release blocker, and do not make it green.

## P2 - public repository hygiene

- [x] Add CONTRIBUTING, SECURITY, privacy and supported-scope documents.
- [x] Enable GitHub private vulnerability reporting when the public repository is created.
  Enabled 2026-09-07 on `taiyun668/Sandglass`.
- [x] Pin the exact Geist font source archive/version and complete asset provenance notices.
- [x] Add provider non-affiliation and unstable-first-party-endpoint notices.
- [x] Modernize `pyproject.toml` license metadata to SPDX form and remove the current setuptools deprecation warning.
- [x] Add CI for tests, wheel build, clean installed-wheel smoke, source scan and artifact checksums.
- [x] Keep the test suite out of the real state directory. Eight tests reached
  `meter_home()`, and one of them started the real quota watch, which re-parsed
  every session file and forced live quota requests to all three vendors --
  writing real account ids, used% and reset times into the directory the running
  product uses. `tools/check_test_isolation.py` now runs every module against its
  own state home and fails on anything written, or on any module that cannot run.

## P3 - Windows distribution and trust

- [x] Choose installer plus portable ZIP as the first Windows artifact format;
  keep MSIX/Store as a later optional channel.
- [x] Add packaged-runtime CI gates for the built wheel and Windows bundle.
  Public run `34215238877` passed both. The earlier hosted-runner GUI launch
  check was removed because that environment has no authoritative interactive
  desktop; CI proves packaged startup and self-test behavior, not a visible orb,
  WPF panel, tray interaction, code-signing trust, SmartScreen behavior or
  clean-user-machine acceptance. Physical UI acceptance remains a separate gate.
  - [x] 2026-09-06: the installer smoke ran end to end for the first time
    against a real build -- per-user silent install, post-install launch, install
    over a running copy aborting with exit code 2 and leaving the program intact
    and running, packaged self-test, uninstall, and provider-directory byte
    invariance. `SHA256SUMS.windows` refreshed for the first time since 09-02,
    which the build only reaches after the smoke passes. The two earlier stalls
    were `Start-Process -Wait` waiting on the descendant Sandglass that the
    silent installer starts, not security software; the packaged self-test gate
    could not fail at all because PowerShell does not wait for a GUI-subsystem
    binary invoked directly.
  - [x] 2026-09-07 at `9e148f9`, out of container, Owner had stopped 7740.
    Same smoke path, `build exit=0`. Provenance `9e148f9d0b7c`. Checksums
    `dist/SHA256SUMS.windows`. Still unsigned.
- [x] Embed clean-commit build provenance and exact web/i18n/native-bridge hashes
  in every Windows bundle; make bundle inspection reject a dirty, malformed or
  resource-mismatched manifest, and expose it through runtime diagnostics.
- [x] The public README claimed a sponsorship that does not exist. It said
  "Free code signing provided by SignPath.io, certificate by SignPath
  Foundation" -- the attribution SignPath asks for once it sponsors a project,
  published before any account, application or certificate existed, and it was
  live on the public repository. Corrected to what is true: the binaries are
  not Authenticode-signed, an installer shows an unknown publisher, and what is
  protected is the update path. A sponsor's name goes up if and when that
  sponsor exists. `docs/release-integrity.md` states the current certificate-free
  release boundary.
  Nothing caught this because no two files had to agree, so now they do:
  `SigningClaimsAgreeTests` fails if the README names a signing sponsor while
  the workflow doc says there is no account, and fails the other way once there
  is one. Mutation: put the sponsorship line back and it fails
  `['SignPath'] != []`.
  The public branch is a separate, squashed history. It still carried the old
  wording when this finding was written; the correction was later published
  without exposing the private maintenance history.
- [x] The update channel no longer waits on a certificate. `download_verified()`
  once required an Authenticode signature Windows trusts, so with no certificate
  no build was installable at all. That deadlock was self-inflicted: what this
  updater has to know is that these bytes were authorized by the same release
  identity as the installed copy. It now requires an ECDSA P-256 signature over
  the checksum manifest, made with a key the Owner generates and keeps, and
  verified through Windows CNG -- no new dependency and no hand-written crypto.
  The manifest names the installer's digest, so signing it covers the installer.
  `RELEASE_PUBLIC_KEY` was populated when the Owner generated the release key.
  A release whose manifest is not signed with it is not offered -- an unsigned
  manifest is either older than the key or not ours. **The Owner's part was one
  command, once:**
  `tools/sign_release_manifest.ps1 -NewKey`. It generates the key, stores it at
  `%USERPROFILE%\.sandglass\release-key.txt` -- outside the repository and outside
  the build tree -- and writes the matching public key into `sandglass/update.py`
  itself, because asking someone to paste a hex string into a source file is a
  step that goes wrong silently. From then on `build_windows_release.ps1` signs
  each manifest by itself when the key is present, and says plainly when it is
  not. `-NewKey` is refused while a key is already declared and leaves no second
  key behind: rotating one stops every installed copy from updating, so it has
  The backup is taken by the setup, not asked for -- and only called a backup
  when it is one. A cloud folder existing proves nothing: Windows ships the
  OneDrive client and creates `%USERPROFILE%\OneDrive` whether or not anyone
  signed in, and on this machine it is there, blank in the registry, syncing
  nowhere. Each candidate is therefore admitted on its account -- OneDrive's
  `UserEmail`/`UserFolder`, Google Drive's `CurrentAccountToken` plus its
  mounted drive -- never on a directory being present.
  `tools/release_key_locations.ps1` is the single place that decides, shared
  by the setup and the release build, because two copies of that rule would
  drift and one of them would be the one telling the Owner their key is safe.
  On this machine it resolves to `G:\My Drive` (signed in) ahead of Documents
  (which it labels as not leaving the disk). `-NoBackup` opts out.
  to be deliberate. Verified end to end in a scratch copy -- the key it wrote
  into update.py verifies a signature made with the private half it kept, and
  the verifier refuses a changed manifest. The private key stays off this
  repository and off CI, which also keeps every release a deliberate act.
  **What it does not do:** nothing for SmartScreen, nothing for Smart App
  Control, and nothing for a first-time installer, who may still see an unknown
  publisher warning or policy block. Mutation: remove the Owner signature and
  the update must be refused for that exact reason; change the installer after
  the manifest is signed and the digest check must refuse it.
- [x] Keep the Windows distribution certificate-free and remove the dormant
  SignPath/Authenticode release branch. **Decided 2026-09-09:** Sandglass follows
  the lightweight unsigned-community pattern: per-user NSIS installer plus
  portable ZIP, Owner detached manifest signature, visible update installer and
  restart. CI needs no signing-service credentials. The earlier SignPath route
  investigation remains historical evidence in `docs/release-provenance-audit.md`,
  not a release task or dependency.
- [x] Sign `SHA256SUMS.windows` with the Owner-held ECDSA P-256 release key;
  verify the embedded public key, manifest signature and installer digest before
  launch, and reject a missing signature or mismatched payload.
- [ ] Test the unsigned package's actual SmartScreen/SAC behavior as a separate
  compatibility observation. A Windows policy block is not an internal signing
  gate and must not be made green by weakening either Windows or update integrity.
- [x] Distinguish an in-process native UI component blocked by Windows policy from a provider account disconnect in the UI, loopback API and `sandglass doctor`; persist only redacted Sandglass-owned diagnostics.
- [x] Validate the main-executable-blocked path through Windows policy logs; a
  process blocked before startup cannot emit an in-app diagnostic. On
  2026-08-30 an unsigned PyInstaller candidate was blocked before startup and
  Code Integrity Operational events 3033/3077 identified the exact executable,
  Enterprise signing-level failure and active policy ID.
- [x] Publish checksums, dependency/SBOM evidence and reproducible release provenance with the GitHub release (completed for `v0.1.0-preview.1`).
  - [x] `v0.1.0-preview.1` is tagged and published at public commit
    `8e366dcc48787a5299a5bf7b96f098c061e30c00`; the outside build exited 0 from
    that clean commit, and its embedded `Sandglass-build-provenance.json` binds
    the exact Git commit, version, clean-worktree state and web/native resource
    hashes. The five verified release assets are
    `Sandglass-0.1.0-windows-x64-unsigned-setup.exe`,
    `Sandglass-0.1.0-windows-x64-unsigned-portable.zip`,
    `Sandglass-0.1.0-windows-x64-runtime.cdx.json`, `SHA256SUMS.windows` and
    `SHA256SUMS.windows.sig`; the manifest signature is valid.
  - [x] Generate and validate a reproducible CycloneDX runtime SBOM separately
    from build-only tools; include it in Windows artifact checksums.
  - [x] Ship exact third-party license/notice texts with the Windows bundle;
    hash every text and fail the build on missing or unreviewed components.
- [ ] Monitor the long-lived-process attribution drift reproduced on 2026-08-30.
  A running source panel counted only post-start Codex minutes while a fresh
  process using the same disk ledger recovered the earlier directly evidenced
  interval. The mechanism remains unlocated; `/api/attribution-diagnostics`
  now exposes non-identifying live ledger and cache digests for the next capture.
  - [x] Do not cache a transient or malformed identity-ledger read as an empty
    authoritative timeline; write Sandglass-owned ledgers by atomic replacement
    and compare live-memory versus direct-disk timeline digests in diagnostics.
    This closes one reproducible failure mechanism, but does not retroactively
    prove it caused the lost-process incident.
  - [x] Refuse to replace an existing Sandglass identity/account ledger when its
    JSON bytes or row schema are damaged; preserve the original bytes for recovery.
  - [x] Add a live HTTP regression for the observed drift shape: a running panel
    first sees a partial Codex identity history, retains its last valid timeline
    through a malformed external write, then accepts an atomically replaced
    earlier history without restart. The panel recovers both account windows and
    `/api/attribution-diagnostics` proves live-memory/disk agreement. This guards
    the failure class but does not identify the lost 2026-08-30 process state
    retroactively, so the parent investigation remains open until a real process
    capture or an independently different reproduction closes it.
  - [x] Add a panel-only self-check that compares the frozen Codex attribution
    projection with a stable, direct-disk replay of the v2 identity ledger every
    30 seconds (`52e5435`). The comparison key is provider, account and window;
    the compared values are only total tokens and per-day totals. It ignores
    provider window start/end timestamps, reset times, boundary labels and the
    whole projection digest, so the observed one-second provider boundary jitter
    cannot manufacture an attribution alarm. A mismatch or an unreadable replay
    records `attribution_self_check`; agreement clears it. The observer cannot
    execute or clear this panel result, and the check does not rescan logs or call
    provider APIs. A formal-checkout restart with a window beginning 2,308.611
    seconds before process start recovered the same total and days in panel and
    observer. This is one high-quality negative result, not proof that the
    historical incident was fixed or that its cause is known.
  - [x] 2026-09-08: `4016c7c` exposes the in-memory `last_ok_at` and
    `check_count` heartbeat. The live API advanced `1 -> 2`, then held at
    `2 -> 2` after the watcher stopped, without writing the diagnostics file.
    This does not close 564 or make the release ready.

Relevant Microsoft guidance:

- [Smart App Control overview](https://learn.microsoft.com/windows/apps/develop/smart-app-control/overview)
- [Windows app publishing and Store signing](https://learn.microsoft.com/windows/apps/publish/get-started)
- [App Control for Business troubleshooting](https://learn.microsoft.com/windows/security/application-security/application-control/app-control-for-business/operations/appcontrol-debugging-and-troubleshooting)

## P4 - product polish and release acceptance

- [x] Replace the silent port-based second-instance exit with a per-session
  Windows named mutex and a dedicated orb activation message. A second launch
  now reveals the existing panel without toggling an already-open panel closed,
  and the desktop no longer occupies port 7741.
- [x] Assign the version-independent `Ayun.Sandglass.Desktop` AppUserModelID
  before UI creation and embed an explicit as-invoker Windows 10/11 manifest
  with long-path awareness in the PyInstaller executable.
- [x] Load the native dashboard and its data API in-process with no default
  listener. Bind 7740 only after explicit precise-monitoring enablement, expose
  only `/v1/logs` there, and remove the receiver again when the user disables it.
- [x] Preserve `SANDGLASS_HOME` on uninstall while removing the app's own
  start-at-login registry value, shortcuts and application registration.
- [x] Give the signed-manifest update path a complete desktop lifecycle: show
  the top-left update control only when an offer exists, expose its action on
  hover and keyboard focus, require an accessible confirmation, then hand off
  to a visible progress-only NSIS update mode. The installer waits for the
  exact parent process, stages before replacement, restores the prior install
  on failure, restarts on success, and the new version shows its release notes
  once. Startup checks use the six-hour cache; a running panel performs a fresh
  metadata-only check every six hours and never downloads before confirmation.
  Portable copies intentionally update into the current-user installed channel;
  their original directory is not removed.
- [ ] Complete accessibility, localization, DPI/multi-monitor and keyboard-navigation checks.
  - [x] Preserve keyboard focus across dynamic tab and account-picker renders;
    expose the active provider with `aria-current` and return focus on Escape.
  - [x] Simulate non-primary-monitor coordinates for orb clamping, edge docking,
    panel placement and native-panel work-area selection.
  - [x] Move the live desktop panel and orb from a 150% primary monitor to a
    100% secondary monitor; verify the WPF frame, WebView2 content and orb all
    switch to the secondary monitor DPI without clipping.
  - [x] Run the native WPF panel and live orb across physical 150% and 100%
    monitors; confirm WPF and `WM_DPICHANGED` resize them in logical units.
  - [ ] Re-run physical startup acceptance after `19e46c0`: restore the orb on
    the 100% secondary screen, recover from a removed-screen coordinate, and
    exercise the pywebview fallback height cap against that screen's work area.
- [ ] Verify tray/orb/panel lifecycle, auto-start opt-in and signed-update recovery.
- [ ] Produce a clean-machine acceptance record before making a public-release-ready claim.

Cross-platform ports and new providers stay after the Windows public release unless
real user demand changes the priority. Any adapter must pass the same official-source
and provider-directory invariance gates before it enters the product.
