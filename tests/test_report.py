import unittest
from datetime import datetime, timezone

from sandglass.models import Account, SessionRecord, TokenUsage
from sandglass.report import build_report


class ReportProviderUnionTests(unittest.TestCase):
    def test_unassigned_provider_sessions_are_visible_without_a_peer_account(self):
        session = SessionRecord(
            provider="codex",
            session_id="codex-unassigned",
            path="codex.jsonl",
            usage=TokenUsage(input_tokens=7, calls=1),
            account_id="",
        )
        claude = Account(provider="claude", account_id="claude-account", active=True)

        report = build_report(
            [session], accounts=[claude], live_quota=False,
        )

        codex = next(
            row for row in report["unassigned_by_provider"] if row["provider"] == "codex"
        )
        self.assertEqual(codex["usage"]["total_tokens"], 7)
        self.assertIn(
            "未归属",
            {row["key"] for row in report["by_account"]},
        )

    def test_since_clips_the_timeline_attribution_walks(self):
        """Clipping only daily left timeline intact, so unassigned rebuilt
        the full history while totals followed the 30-day buckets."""
        session = SessionRecord(
            provider="codex",
            session_id="span",
            path="span.jsonl",
            usage=TokenUsage(input_tokens=30, calls=2),
            daily={
                "2026-08-01": TokenUsage(input_tokens=10, calls=1),
                "2026-08-20": TokenUsage(input_tokens=20, calls=1),
            },
            timeline=[
                ("2026-08-01T12:00:00+00:00", TokenUsage(input_tokens=10, calls=1)),
                ("2026-08-20T12:00:00+00:00", TokenUsage(input_tokens=20, calls=1)),
            ],
        )
        report = build_report(
            [session],
            since=datetime(2026, 8, 10, tzinfo=timezone.utc),
            accounts=[],
            live_quota=False,
        )
        self.assertEqual(report["totals"]["usage"]["input_tokens"], 20)
        self.assertEqual(report["unassigned_by_provider"][0]["usage"]["input_tokens"], 20)

    def test_pre_timeline_session_past_the_far_edge_stays_out_of_the_window(self):
        from datetime import timedelta

        from sandglass.report import _local_in_windows

        start = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        end = start + timedelta(hours=5)
        later = end + timedelta(hours=1)
        account = Account(provider="codex", account_id="a")
        session = SessionRecord(
            provider="codex",
            session_id="s",
            path="s",
            started_at=later.isoformat(),
            ended_at=later.isoformat(),
            usage=TokenUsage(input_tokens=99, calls=1),
            timeline=None,
        )
        rows = _local_in_windows(
            account,
            [session],
            [{
                "label": "5h",
                "window_start": start.isoformat(),
                "resets_at": end.isoformat(),
            }],
        )
        self.assertEqual(rows[0]["usage"]["input_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
