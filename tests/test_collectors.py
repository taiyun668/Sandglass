import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sandglass.collectors import _parse_claude, _parse_codex, _parse_grok, collect_all
from sandglass.models import TokenUsage
from sandglass.paths import cache_db, meter_home
from datetime import datetime, timedelta, timezone

from sandglass.accounts import assign_codex_account
from sandglass.models import Account, SessionRecord
from sandglass.report import build_report


class CollectorTests(unittest.TestCase):
    def test_single_official_mode_assigns_claude_history_to_the_only_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude = root / "claude"
            project = claude / "projects" / "project-a"
            project.mkdir(parents=True)
            (claude / ".claude.json").write_text(
                json.dumps(
                    {
                        "oauthAccount": {
                            "emailAddress": "current@example.com",
                            "accountUuid": "current-account",
                        }
                    }
                ),
                encoding="utf-8",
            )
            (project / "session.jsonl").write_text(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-08-28T12:00:00Z",
                        "sessionId": "claude-history",
                        "message": {
                            "model": "claude-test",
                            "usage": {"input_tokens": 10, "output_tokens": 5},
                        },
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "CLAUDE_CONFIG_DIR": str(claude),
                "CODEX_HOME": str(root / "codex"),
                "GROK_HOME": str(root / "grok"),
                "SANDGLASS_HOME": str(root / "sandglass"),
            }

            with patch.dict(os.environ, env, clear=False):
                sessions = collect_all()

            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].provider, "claude")
            self.assertEqual(sessions[0].account_id, "")
            self.assertEqual(sessions[0].usage.total_tokens, 15)

            account = Account(
                provider="claude",
                account_id="current-account",
                email="current@example.com",
                active=True,
            )
            report = build_report(
                sessions,
                accounts=[account],
                live_quota=False,
                attribution_mode="single_official",
            )
            self.assertEqual(report["unassigned_by_provider"], [])
            self.assertEqual(report["accounts"][0]["local_usage"]["total_tokens"], 15)
            self.assertIn(
                "sandglass_policy:single_official_account",
                report["accounts"][0]["local_evidence_sources"],
            )

    def test_skill_mode_does_not_guess_identity_free_official_history(self):
        account = Account(provider="claude", account_id="only", email="only@example.com")
        usage = TokenUsage(input_tokens=80, output_tokens=20, calls=1)
        session = SessionRecord(
            provider="claude",
            session_id="official-history",
            path="session.jsonl",
            usage=usage,
            daily={"2026-08-29": usage},
            timeline=[("2026-08-29T12:00:00Z", usage)],
        )

        report = build_report(
            [session],
            accounts=[account],
            live_quota=False,
            attribution_mode="skill_assisted",
        )

        self.assertEqual(report["accounts"][0]["local_usage"]["total_tokens"], 0)
        self.assertEqual(
            report["unassigned_by_provider"][0]["usage"]["total_tokens"], 100
        )

    def test_single_mode_uses_current_account_and_rolls_back_without_mutating_sessions(self):
        current = Account(
            provider="claude", account_id="current", email="current@example.com", active=True
        )
        old = Account(
            provider="claude", account_id="old", email="old@example.com", active=False
        )
        usage = TokenUsage(input_tokens=80, output_tokens=20, calls=1)
        session = SessionRecord(
            provider="claude",
            session_id="official-history",
            path="session.jsonl",
            usage=usage,
            daily={"2026-08-29": usage},
            timeline=[("2026-08-29T12:00:00Z", usage)],
        )
        original = copy.deepcopy(session)

        single = build_report(
            [session],
            accounts=[old, current],
            live_quota=False,
            attribution_mode="single_official",
        )
        reverted = build_report(
            [session],
            accounts=[old, current],
            live_quota=False,
            attribution_mode="skill_assisted",
        )
        restored = build_report(
            [session],
            accounts=[old, current],
            live_quota=False,
            attribution_mode="single_official",
        )

        single_by_id = {row["account_id"]: row for row in single["accounts"]}
        restored_by_id = {row["account_id"]: row for row in restored["accounts"]}
        self.assertEqual(single_by_id["current"]["local_usage"]["total_tokens"], 100)
        self.assertEqual(single_by_id["old"]["local_usage"]["total_tokens"], 0)
        self.assertEqual(reverted["accounts"][0]["local_usage"]["total_tokens"], 0)
        self.assertEqual(
            reverted["unassigned_by_provider"][0]["usage"]["total_tokens"], 100
        )
        self.assertEqual(restored_by_id["current"]["local_usage"]["total_tokens"], 100)
        self.assertEqual(session, original)

    def test_claude_thinking_survives_the_iterations_sum(self):
        """An iteration carries no output_tokens_details.

        Summing them for the token counts is right, but it silently dropped every
        thinking token -- 41% of this machine's Claude output. The message level is
        what knows, so reasoning comes from there whatever the iterations say.
        """
        from sandglass.collectors import _claude_usage

        usage = _claude_usage(
            {
                "output_tokens": 810,
                "output_tokens_details": {"thinking_tokens": 400},
                "iterations": [
                    {"output_tokens": 810, "input_tokens": 1, "cache_read_input_tokens": 36073}
                ],
            }
        )
        self.assertEqual(usage.output_tokens, 810)
        self.assertEqual(usage.reasoning_tokens, 400)
        self.assertEqual(usage.cache_read_tokens, 36073)

    def test_claude_dedupes_request_and_prefers_iterations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sess.jsonl"
            rows = [
                {
                    "type": "assistant",
                    "requestId": "req1",
                    "timestamp": "2026-08-01T10:00:00Z",
                    "sessionId": "abc",
                    "message": {
                        "model": "claude-sonnet-4-6",
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "cache_read_input_tokens": 0,
                            "iterations": [
                                {
                                    "input_tokens": 10,
                                    "output_tokens": 20,
                                    "cache_read_input_tokens": 100,
                                    "cache_creation_input_tokens": 5,
                                }
                            ],
                        },
                    },
                },
                {
                    "type": "assistant",
                    "requestId": "req1",
                    "timestamp": "2026-08-01T10:00:01Z",
                    "sessionId": "abc",
                    "message": {
                        "model": "claude-sonnet-4-6",
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "iterations": [
                                {
                                    "input_tokens": 11,
                                    "output_tokens": 22,
                                    "cache_read_input_tokens": 110,
                                    "cache_creation_input_tokens": 6,
                                }
                            ],
                        },
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            rec = _parse_claude(path)
            self.assertEqual(rec.session_id, "abc")
            self.assertEqual(rec.usage.input_tokens, 11)
            self.assertEqual(rec.usage.output_tokens, 22)
            self.assertEqual(rec.usage.cache_read_tokens, 110)
            self.assertEqual(rec.usage.calls, 1)

    def test_claude_counts_distinct_messages_inside_one_user_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tool-turn.jsonl"
            rows = [
                {
                    "type": "assistant",
                    "requestId": "user-turn",
                    "timestamp": "2026-08-01T10:00:00Z",
                    "sessionId": "abc",
                    "message": {
                        "id": "provider-response-1",
                        "model": "claude-test",
                        "usage": {
                            "input_tokens": 3,
                            "output_tokens": 8,
                            "cache_creation_input_tokens": 62,
                        },
                    },
                },
                {
                    "type": "assistant",
                    "requestId": "user-turn",
                    "timestamp": "2026-08-01T10:02:00Z",
                    "sessionId": "abc",
                    "message": {
                        "id": "provider-response-2",
                        "model": "claude-test",
                        "usage": {
                            "input_tokens": 3,
                            "output_tokens": 11,
                            "cache_read_input_tokens": 62,
                        },
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            record = _parse_claude(path)
            timeline_total = sum(usage.total_tokens for _, usage in record.timeline)

            self.assertEqual(record.usage.total_tokens, 149)
            self.assertEqual(timeline_total, record.usage.total_tokens)
            self.assertEqual(record.usage.calls, 2)

    def test_codex_uses_last_cumulative_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-x.jsonl"
            rows = [
                {
                    "type": "session_meta",
                    "timestamp": "2026-08-02T00:00:00Z",
                    "payload": {
                        "session_id": "sid",
                        "cwd": "D:/proj",
                        "originator": "Codex Desktop",
                        "source": "vscode",
                    },
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-08-02T00:01:00Z",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 150,
                                "cached_input_tokens": 50,
                                "cache_write_input_tokens": 0,
                                "output_tokens": 10,
                                "reasoning_output_tokens": 4,
                            }
                        },
                    },
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-08-02T00:02:00Z",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 250,
                                "cached_input_tokens": 80,
                                "cache_write_input_tokens": 0,
                                "output_tokens": 30,
                                "reasoning_output_tokens": 8,
                            }
                        },
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            rec = _parse_codex(path)
            self.assertEqual(rec.client, "Codex Desktop")
            self.assertEqual(rec.usage.input_tokens, 170)  # 250-80
            self.assertEqual(rec.usage.cache_read_tokens, 80)
            self.assertEqual(rec.usage.output_tokens, 30)

    def test_codex_sums_last_token_usage_across_resets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-reset.jsonl"
            rows = [
                {
                    "type": "session_meta",
                    "timestamp": "2026-08-02T00:00:00Z",
                    "payload": {"session_id": "sid", "cwd": "D:/proj"},
                },
                {
                    "type": "turn_context",
                    "timestamp": "2026-08-02T00:00:01Z",
                    "payload": {"model": "gpt-5.6-sol"},
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-08-02T00:01:00Z",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 1000,
                                "cached_input_tokens": 0,
                                "output_tokens": 10,
                            },
                            "last_token_usage": {
                                "input_tokens": 1000,
                                "cached_input_tokens": 0,
                                "output_tokens": 10,
                            },
                        },
                    },
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-08-02T00:02:00Z",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 200,
                                "cached_input_tokens": 0,
                                "output_tokens": 5,
                            },
                            "last_token_usage": {
                                "input_tokens": 200,
                                "cached_input_tokens": 0,
                                "output_tokens": 5,
                            },
                        },
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            rec = _parse_codex(path)
            self.assertEqual(rec.usage.input_tokens, 1200)
            self.assertEqual(rec.usage.output_tokens, 15)

    def test_codex_repeated_last_token_usage_is_not_a_new_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-snapshot.jsonl"
            turn = {
                "type": "event_msg",
                "timestamp": "2026-08-02T00:01:00Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 40,
                            "cached_input_tokens": 0,
                            "output_tokens": 8,
                        },
                        "last_token_usage": {
                            "input_tokens": 40,
                            "cached_input_tokens": 0,
                            "output_tokens": 8,
                        },
                    },
                },
            }
            rows = [
                {
                    "type": "session_meta",
                    "timestamp": "2026-08-02T00:00:00Z",
                    "payload": {"session_id": "sid", "cwd": "D:/proj"},
                },
                turn,
                {**turn, "timestamp": "2026-08-02T00:01:01Z"},
            ]
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            rec = _parse_codex(path)
            self.assertEqual(rec.usage.input_tokens, 40)
            self.assertEqual(rec.usage.output_tokens, 8)
            self.assertEqual(len(rec.timeline), 1)

    def test_grok_sums_turn_completed_events(self):
        # Each turn_completed carries that turn's own totals -- numTurns/modelCalls reset
        # on every event and inputTokens moves up and down within one sessionId -- so the
        # session total is the sum of the events, not the last one.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "updates.jsonl"
            rows = [
                {
                    "timestamp": "2026-08-03T01:00:00Z",
                    "method": "session/update",
                    "params": {
                        "sessionId": "g1",
                        "update": {
                            "sessionUpdate": "turn_completed",
                            "usage": {
                                "inputTokens": 120,
                                "outputTokens": 10,
                                "cachedReadTokens": 20,
                                "cacheCreationTokens": 0,
                                "reasoningTokens": 5,
                                "modelCalls": 1,
                                "costUsdTicks": 2_000_000_000,
                                "modelUsage": {"grok-4.6": {}},
                            },
                        },
                    },
                },
                {
                    "timestamp": "2026-08-03T01:10:00Z",
                    "method": "session/update",
                    "params": {
                        "sessionId": "g1",
                        "update": {
                            "sessionUpdate": "turn_completed",
                            "usage": {
                                "inputTokens": 220,
                                "outputTokens": 40,
                                "cachedReadTokens": 50,
                                "cacheCreationTokens": 3,
                                "reasoningTokens": 9,
                                "modelCalls": 2,
                                "costUsdTicks": 4_000_000_000,
                                "modelUsage": {"grok-4.6": {}},
                            },
                        },
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            rec = _parse_grok(path)
            self.assertEqual(rec.session_id, "g1")
            self.assertEqual(rec.usage.input_tokens, 270)  # (120-20) + (220-50)
            self.assertEqual(rec.usage.output_tokens, 50)
            self.assertEqual(rec.usage.cache_read_tokens, 70)
            self.assertEqual(rec.usage.calls, 3)
            # Both turns land in their own minute, so any window can be summed exactly.
            self.assertEqual(len(rec.timeline), 2)
            self.assertEqual(sum(u.input_tokens for _, u in rec.timeline), 270)
            self.assertNotIn("vendor_cost_usd", rec.extra)
            self.assertNotIn("minute_cost", rec.extra)

    def test_report_splits_global_and_account(self):
        sessions = [
            SessionRecord(
                provider="claude",
                session_id="1",
                path="a",
                account_id="c1",
                account_label="claude-a",
                client="Claude Code",
                usage=TokenUsage(input_tokens=10, output_tokens=5, calls=1),
                daily={"2026-08-01": TokenUsage(input_tokens=10, output_tokens=5, calls=1)},
            ),
            SessionRecord(
                provider="codex",
                session_id="2",
                path="b",
                account_id="x1",
                account_label="codex-a",
                client="Codex Desktop",
                usage=TokenUsage(input_tokens=20, output_tokens=8, calls=1),
                daily={"2026-08-01": TokenUsage(input_tokens=20, output_tokens=8, calls=1)},
            ),
        ]
        report = build_report(sessions, accounts=[])
        self.assertEqual(report["totals"]["usage"]["input_tokens"], 30)
        self.assertEqual(len(report["by_account"]), 2)
        self.assertEqual(report["scope"], "this-machine")

    def test_report_propagates_evidence_sources_to_every_usage_projection(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        later = now + timedelta(seconds=1)
        official_usage = TokenUsage(input_tokens=10, output_tokens=5, calls=1)
        adapter_usage = TokenUsage(input_tokens=20, output_tokens=8, calls=1)
        account = Account(
            provider="claude",
            account_id="account-1",
            label="claude-a",
            active=True,
            extra={
                "windows": [
                    {
                        "label": "5h",
                        "used_percent": 20.0,
                        "window_minutes": 300,
                        "resets_at": (now.replace(microsecond=0) + timedelta(hours=1)).isoformat(),
                    }
                ]
            },
        )
        sessions = [
            SessionRecord(
                provider="claude",
                session_id="official",
                path="official.jsonl",
                account_id=account.account_id,
                account_label=account.label,
                client="Claude Code",
                started_at=now.isoformat(),
                ended_at=now.isoformat(),
                usage=official_usage,
                daily={now.date().isoformat(): official_usage},
                timeline=[(now.isoformat(), official_usage)],
            ),
            SessionRecord(
                provider="claude",
                session_id="adapter",
                path="adapter.jsonl",
                account_id=account.account_id,
                account_label=account.label,
                client="Claude Code",
                started_at=later.isoformat(),
                ended_at=later.isoformat(),
                usage=adapter_usage,
                daily={now.date().isoformat(): adapter_usage},
                timeline=[(later.isoformat(), adapter_usage)],
                extra={"evidence_sources": ["user_adapter:example.source"]},
            ),
        ]

        report = build_report(
            sessions,
            accounts=[account],
            live_quota=False,
            attribution_mode="single_official",
        )

        direct = ["official_builtin:claude", "user_adapter:example.source"]
        attributed = [
            "official_builtin:claude",
            "sandglass_policy:single_official_account",
            "user_adapter:example.source",
        ]
        self.assertEqual(report["totals"]["evidence_sources"], direct)
        self.assertEqual(report["by_provider"][0]["evidence_sources"], direct)
        self.assertEqual(report["by_account"][0]["evidence_sources"], attributed)
        self.assertEqual(report["by_client"][0]["evidence_sources"], direct)
        self.assertEqual(report["by_day"][0]["evidence_sources"], direct)
        self.assertEqual(report["by_provider_day"]["claude"][0]["evidence_sources"], direct)
        self.assertEqual(report["by_block"][-1]["evidence_sources"], direct)
        self.assertEqual(report["accounts"][0]["local_evidence_sources"], attributed)
        self.assertEqual(
            report["accounts"][0]["local_in_windows"][0]["evidence_sources"],
            attributed,
        )
        self.assertEqual(
            {tuple(row["evidence_sources"]) for row in report["sessions"]},
            {("official_builtin:claude",), ("user_adapter:example.source",)},
        )

    def test_report_keeps_provider_unassigned_usage_when_no_accounts_exist(self):
        session = SessionRecord(
            provider="claude",
            session_id="signed-out-history",
            path="session.jsonl",
            usage=TokenUsage(input_tokens=80, output_tokens=20, calls=1),
            timeline=[
                (
                    "2026-08-29T12:00:00Z",
                    TokenUsage(input_tokens=80, output_tokens=20, calls=1),
                )
            ],
        )

        report = build_report([session], accounts=[], live_quota=False)

        self.assertEqual(len(report["unassigned_by_provider"]), 1)
        row = report["unassigned_by_provider"][0]
        self.assertEqual(row["provider"], "claude")
        self.assertEqual(row["usage"]["total_tokens"], 100)

    def test_report_splits_cross_account_session_by_minute(self):
        old = Account(provider="codex", account_id="old", label="old")
        new = Account(provider="codex", account_id="new", label="new")
        session = SessionRecord(
            provider="codex",
            session_id="cross-switch",
            path="rollout.jsonl",
            account_id="new",
            account_label="new",
            started_at="2026-08-01T10:00:00Z",
            ended_at="2026-08-01T10:02:00Z",
            usage=TokenUsage(input_tokens=300, calls=2),
            timeline=[
                ("2026-08-01T10:00:00Z", TokenUsage(input_tokens=100, calls=1)),
                ("2026-08-01T10:02:00Z", TokenUsage(input_tokens=200, calls=1)),
            ],
        )
        runs = [
            ("2026-08-01T09:00:00Z", "old"),
            ("2026-08-01T10:01:00Z", "new"),
        ]
        with patch("sandglass.report.switch_runs_for", return_value=runs):
            report = build_report(
                [session],
                accounts=[old, new],
                attribution_mode="skill_assisted",
            )
        rows = {row["account_id"]: row for row in report["accounts"]}
        self.assertEqual(rows["old"]["local_usage"]["input_tokens"], 100)
        self.assertEqual(rows["new"]["local_usage"]["input_tokens"], 200)
        buckets = {row["key"]: row for row in report["by_account"]}
        self.assertEqual(buckets["old"]["usage"]["input_tokens"], 100)
        self.assertEqual(buckets["new"]["usage"]["input_tokens"], 200)

    def test_account_quota_is_current_usage(self):
        account = Account(
            provider="codex",
            account_id="x1",
            email="a@b.com",
            label="codex-a",
            plan="plus",
            extra={
                "windows": [
                    {"label": "5h", "used_percent": 42.0},
                    {"label": "7d", "used_percent": 61.0},
                ]
            },
        )
        report = build_report([], accounts=[account])
        row = report["accounts"][0]
        self.assertEqual(row["current_usage_kind"], "quota")
        self.assertEqual(row["current_usage"][0]["used_percent"], 42.0)
        self.assertEqual(row["current_usage"][1]["used_percent"], 61.0)

    def test_current_auth_snapshot_does_not_claim_rollout_history(self):
        old = Account(provider="codex", account_id="old", label="old", extra={"last_used_at": 1787700000})
        active = Account(
            provider="codex",
            account_id="cur",
            label="current",
            active=True,
            extra={"activated_at_ms": 1787762017908, "last_used_at": 1787762017},
        )
        current = SessionRecord(
            provider="codex",
            session_id="now",
            path="rollout-now.jsonl",
            ended_at="2026-08-27T04:00:00+00:00",
            usage=TokenUsage(input_tokens=100, calls=1),
        )
        older = SessionRecord(
            provider="codex",
            session_id="then",
            path="rollout-then.jsonl",
            ended_at="2026-08-25T00:00:00+00:00",
            usage=TokenUsage(input_tokens=50, calls=1),
        )
        now_assigned = assign_codex_account(current, [old, active])
        then_assigned = assign_codex_account(older, [old, active])
        self.assertEqual(now_assigned.account_id, "")
        self.assertEqual(then_assigned.account_id, "")

    def test_since_clips_daily_buckets(self):
        sessions = [
            SessionRecord(
                provider="claude",
                session_id="1",
                path="a",
                account_id="c1",
                account_label="claude-a",
                client="Claude Code",
                usage=TokenUsage(input_tokens=30, output_tokens=0, calls=2),
                daily={
                    "2026-08-01": TokenUsage(input_tokens=10, calls=1),
                    "2026-08-20": TokenUsage(input_tokens=20, calls=1),
                },
            )
        ]
        report = build_report(
            sessions,
            since=datetime(2026, 8, 10, tzinfo=timezone.utc),
            accounts=[],
        )
        self.assertEqual(report["totals"]["usage"]["input_tokens"], 20)
        self.assertEqual(report["totals"]["sessions"], 1)
        self.assertEqual(report["by_day"][0]["key"], "2026-08-20")

    def test_codex_subagent_keeps_own_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-child.jsonl"
            rows = [
                {
                    "type": "session_meta",
                    "timestamp": "2026-08-02T00:00:00Z",
                    "payload": {
                        "session_id": "parent-id",
                        "id": "child-id",
                        "parent_thread_id": "parent-id",
                        "thread_source": "subagent",
                        "cwd": "D:/proj",
                        "originator": "Codex Desktop",
                        "source": {"subagent": {"thread_spawn": {"agent_nickname": "Arendt"}}},
                    },
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-08-02T00:02:00Z",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"input_tokens": 40, "cached_input_tokens": 0, "output_tokens": 8}},
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            rec = _parse_codex(path)
            self.assertEqual(rec.session_id, "child-id")
            self.assertTrue(rec.extra["is_subagent"])
            self.assertEqual(rec.extra["root_session_id"], "parent-id")
            self.assertEqual(rec.client, "Codex subagent (Arendt)")
            self.assertEqual(rec.usage.input_tokens, 40)

    def test_codex_subagent_trims_replayed_parent_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-child-replay.jsonl"
            rows = [
                {
                    "type": "session_meta",
                    "timestamp": "2026-08-02T00:00:00Z",
                    "payload": {
                        "session_id": "parent-id",
                        "id": "child-id",
                        "parent_thread_id": "parent-id",
                        "thread_source": "subagent",
                        "cwd": "D:/proj",
                    },
                }
            ]
            usage = {
                "input_tokens": 10,
                "cached_input_tokens": 0,
                "output_tokens": 1,
            }
            for index in range(20):
                rows.append(
                    {
                        "type": "event_msg",
                        "timestamp": f"2026-08-02T00:00:{index:02d}Z",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {
                                    "input_tokens": 10 * (index + 1),
                                    "cached_input_tokens": 0,
                                    "output_tokens": index + 1,
                                },
                                "last_token_usage": usage,
                            },
                        },
                    }
                )
            rows.append(
                {
                    "type": "event_msg",
                    "timestamp": "2026-08-02T00:01:00Z",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 205,
                                "cached_input_tokens": 0,
                                "output_tokens": 25,
                            },
                            "last_token_usage": {
                                "input_tokens": 5,
                                "cached_input_tokens": 0,
                                "output_tokens": 5,
                            },
                        },
                    },
                }
            )
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            rec = _parse_codex(path)
            self.assertEqual(rec.session_id, "child-id")
            self.assertEqual(rec.usage.input_tokens, 5)
            self.assertEqual(rec.usage.output_tokens, 5)

    def test_claude_parent_skips_sidechain_but_subagent_file_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "abc.jsonl"
            child_dir = Path(tmp) / "abc" / "subagents"
            child_dir.mkdir(parents=True)
            child = child_dir / "agent-1.jsonl"
            parent_rows = [
                {
                    "type": "assistant",
                    "isSidechain": True,
                    "requestId": "side",
                    "timestamp": "2026-08-01T10:00:00Z",
                    "sessionId": "abc",
                    "message": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 99, "output_tokens": 1}},
                },
                {
                    "type": "assistant",
                    "isSidechain": False,
                    "requestId": "root",
                    "timestamp": "2026-08-01T10:00:01Z",
                    "sessionId": "abc",
                    "message": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 10, "output_tokens": 2}},
                },
            ]
            child_rows = [
                {
                    "type": "assistant",
                    "isSidechain": True,
                    "requestId": "side",
                    "timestamp": "2026-08-01T10:00:00Z",
                    "sessionId": "abc",
                    "message": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 99, "output_tokens": 1}},
                }
            ]
            parent.write_text("\n".join(json.dumps(r) for r in parent_rows), encoding="utf-8")
            child.write_text("\n".join(json.dumps(r) for r in child_rows), encoding="utf-8")
            root = _parse_claude(parent)
            sub = _parse_claude(child)
            self.assertEqual(root.usage.input_tokens, 10)
            self.assertFalse(root.extra.get("is_subagent"))
            self.assertEqual(sub.usage.input_tokens, 99)
            self.assertTrue(sub.extra.get("is_subagent"))

    def test_claude_resume_drops_copied_parent_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fork.jsonl"
            rows = [
                {"type": "custom-title", "customTitle": "resume", "sessionId": "fork"},
                {
                    "type": "queue-operation",
                    "timestamp": "2026-08-02T12:00:00Z",
                    "sessionId": "fork",
                    "operation": "resume",
                },
            ]
            for i in range(20):
                rows.append(
                    {
                        "type": "assistant",
                        "isSidechain": False,
                        "requestId": f"old{i}",
                        "timestamp": f"2026-08-01T10:00:{i:02d}Z",
                        "sessionId": "fork",
                        "message": {
                            "model": "claude-sonnet-4-6",
                            "usage": {"input_tokens": 5, "output_tokens": 100},
                        },
                    }
                )
            rows.append(
                {
                    "type": "assistant",
                    "isSidechain": False,
                    "requestId": "new1",
                    "timestamp": "2026-08-02T12:01:00Z",
                    "sessionId": "fork",
                    "message": {
                        "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 3, "output_tokens": 7},
                    },
                }
            )
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            rec = _parse_claude(path)
            self.assertEqual(rec.usage.output_tokens, 7)
            self.assertEqual(rec.usage.calls, 1)
            self.assertEqual(rec.started_at, "2026-08-02T12:00:00+00:00")

    def test_claude_keeps_history_when_older_events_are_below_replay_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skew.jsonl"
            rows = [
                {
                    "type": "queue-operation",
                    "timestamp": "2026-08-02T12:00:00Z",
                    "sessionId": "skew",
                }
            ]
            for i in range(19):
                rows.append(
                    {
                        "type": "assistant",
                        "requestId": f"old{i}",
                        "timestamp": f"2026-08-01T10:00:{i:02d}Z",
                        "sessionId": "skew",
                        "message": {
                            "model": "claude-sonnet-4-6",
                            "usage": {"input_tokens": 1, "output_tokens": 10},
                        },
                    }
                )
            rows.append(
                {
                    "type": "assistant",
                    "requestId": "new1",
                    "timestamp": "2026-08-02T12:01:00Z",
                    "sessionId": "skew",
                    "message": {
                        "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 1, "output_tokens": 3},
                    },
                }
            )
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            rec = _parse_claude(path)
            self.assertEqual(rec.usage.output_tokens, 19 * 10 + 3)
            self.assertEqual(rec.usage.calls, 20)

    def test_claude_original_session_is_not_trimmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "orig.jsonl"
            rows = [
                {
                    "type": "queue-operation",
                    "timestamp": "2026-08-01T10:00:00Z",
                    "sessionId": "orig",
                }
            ]
            for i in range(25):
                rows.append(
                    {
                        "type": "assistant",
                        "requestId": f"t{i}",
                        "timestamp": f"2026-08-01T10:{i:02d}:00Z",
                        "sessionId": "orig",
                        "message": {
                            "model": "claude-sonnet-4-6",
                            "usage": {"input_tokens": 1, "output_tokens": 4},
                        },
                    }
                )
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            rec = _parse_claude(path)
            self.assertEqual(rec.usage.output_tokens, 100)
            self.assertEqual(rec.usage.calls, 25)

    def test_codex_does_not_emit_dollar_estimates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-sol.jsonl"
            rows = [
                {
                    "type": "session_meta",
                    "timestamp": "2026-08-02T00:00:00Z",
                    "payload": {"session_id": "sid", "cwd": "D:/proj"},
                },
                {
                    "type": "turn_context",
                    "timestamp": "2026-08-02T00:00:01Z",
                    "payload": {"model": "gpt-5.6-sol", "cwd": "D:/proj"},
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-08-02T00:01:00Z",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 1_000_000,
                                "cached_input_tokens": 0,
                                "output_tokens": 0,
                            }
                        },
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            rec = _parse_codex(path)
            self.assertNotIn("vendor_cost_usd", rec.extra)
            self.assertNotIn("minute_cost", rec.extra)

    def test_grok_history_before_identity_evidence_is_unassigned(self):
        account = Account(provider="grok", account_id="current", email="current@example.com", active=True)
        session = SessionRecord(
            provider="grok",
            session_id="old",
            path="old",
            timeline=[("2026-08-01T00:00:00Z", TokenUsage(output_tokens=25))],
            usage=TokenUsage(output_tokens=25),
            daily={"2026-08-01": TokenUsage(output_tokens=25)},
        )
        runs = [("2026-08-02T00:00:00Z", "current")]
        with patch("sandglass.report.switch_runs_for", return_value=runs):
            report = build_report([session], accounts=[account], live_quota=False)
        unassigned = next(row for row in report["by_account"] if row["key"] == "未归属")
        self.assertEqual(unassigned["usage"]["output_tokens"], 25)

    def test_codex_history_without_identity_is_reported_by_provider(self):
        account = Account(provider="codex", account_id="known", email="known@example.com")
        session = SessionRecord(
            provider="codex",
            session_id="old",
            path="old.jsonl",
            account_id="",
            usage=TokenUsage(input_tokens=80, output_tokens=20),
            timeline=[("2026-08-01T00:00:00+00:00", TokenUsage(input_tokens=80, output_tokens=20))],
        )

        with patch("sandglass.report.switch_runs_for", return_value=[]):
            report = build_report([session], accounts=[account], live_quota=False)

        row = report["unassigned_by_provider"][0]
        self.assertEqual(row["provider"], "codex")
        self.assertEqual(row["usage"]["total_tokens"], 100)

    def test_public_report_has_no_dollar_fields(self):
        session = SessionRecord(
            provider="claude",
            session_id="s",
            path="s",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            daily={"2026-08-01": TokenUsage(input_tokens=10, output_tokens=5)},
        )
        report = build_report([session], accounts=[], live_quota=False)

        def keys(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield key
                    yield from keys(item)
            elif isinstance(value, list):
                for item in value:
                    yield from keys(item)

        self.assertFalse(any("usd" in key.lower() for key in keys(report)))

    def test_sandglass_home_does_not_import_another_products_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "sandglass"
            legacy = Path(tmp) / "localmeter"
            legacy.mkdir()
            (legacy / "cache.sqlite").write_bytes(b"legacy-cache")
            old = os.environ.get("SANDGLASS_HOME")
            try:
                os.environ["SANDGLASS_HOME"] = str(home)
                self.assertEqual(meter_home(), home)
                self.assertEqual(cache_db(), home / "cache.sqlite")
                self.assertFalse((home / "cache.sqlite").exists())
            finally:
                if old is None:
                    os.environ.pop("SANDGLASS_HOME", None)
                else:
                    os.environ["SANDGLASS_HOME"] = old


if __name__ == "__main__":
    unittest.main()
