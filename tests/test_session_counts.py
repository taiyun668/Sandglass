"""What the word "sessions" counts, and that it counts the same thing everywhere.

Four places built this number. Two of them wrote `len(roots) or len(every
session id)` -- a count of records that were not subagents, falling back to a
different quantity in whichever rows the first one came out zero. The other two
had no fallback and simply reported zero beside a real token total.

None of it was covered: replacing all four definitions at once broke no
existing test.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from sandglass.models import Account, SessionRecord, TokenUsage
from sandglass.pace import five_hour_blocks


def _session(session_id, *, provider="codex", root="", subagent=False,
             at="2026-08-01T01:00:00+00:00", tokens=100):
    return SessionRecord(
        provider=provider,
        session_id=session_id,
        path=f"/sessions/{session_id}.jsonl",
        started_at=at,
        ended_at=at,
        usage=TokenUsage(input_tokens=tokens),
        timeline=[(at, TokenUsage(input_tokens=tokens))],
        extra={"is_subagent": subagent, "root_session_id": root},
    )


class FiveHourBlockSessionCountTests(unittest.TestCase):
    def test_two_subagents_of_one_conversation_count_once(self):
        """Codex gives each subagent its own id and names the parent.

        The old fallback counted the ids, so a block holding nothing but the
        two helpers one conversation spawned reported two sessions.
        """
        blocks = five_hour_blocks(
            [
                _session("sub-a", root="root-1", subagent=True),
                _session("sub-b", root="root-1", subagent=True),
            ],
            now=datetime(2026, 8, 1, 3, tzinfo=timezone.utc),
        )
        current = [block for block in blocks if block["usage"]["total_tokens"]]
        self.assertEqual([block["sessions"] for block in current], [1])

    def test_a_subagent_whose_root_transcript_ended_elsewhere_still_counts(self):
        """The root filter dropped the conversation and kept its tokens.

        Records land in one block each, by the time they end. A conversation
        that ends in a later block leaves only its subagents behind here, and
        those were subtracted from the count while their usage was added to it.
        """
        blocks = five_hour_blocks(
            [
                _session("root-1"),
                _session("sub-b", root="root-2", subagent=True),
            ],
            now=datetime(2026, 8, 1, 3, tzinfo=timezone.utc),
        )
        current = [block for block in blocks if block["usage"]["total_tokens"]]
        self.assertEqual([block["sessions"] for block in current], [2])

    def test_a_block_with_tokens_never_reports_no_sessions(self):
        blocks = five_hour_blocks(
            [_session("sub-a", root="root-1", subagent=True)],
            now=datetime(2026, 8, 1, 3, tzinfo=timezone.utc),
        )
        for block in blocks:
            if block["usage"]["total_tokens"]:
                self.assertGreater(block["sessions"], 0, "有用量却报 0 个会话")


class DailyRowAgreementTests(unittest.TestCase):
    """The day row above has to be the day rows below, added up.

    The per-provider daily rows had the roots-or-everything fallback; the
    overall daily row above them had neither -- it counted plain session ids.
    Two answers to the same question, stacked on one screen, disagreeing on 17
    of this machine's 85 days.
    """

    def _sessions(self):
        at = "2026-08-01T01:00:00+00:00"
        return [
            _session("claude-root", provider="claude", at=at),
            # a Claude subagent transcript carries the root's own sessionId
            _session("claude-root", provider="claude", root="claude-root",
                     subagent=True, at=at),
            _session("codex-root", provider="codex", at=at),
            _session("codex-sub", provider="codex", root="codex-root",
                     subagent=True, at=at),
            _session("codex-orphan", provider="codex", root="", subagent=True, at=at),
        ]

    def test_the_overall_day_row_is_the_provider_rows_added_up(self):
        from sandglass.report import _bucket_daily, _bucket_provider_daily

        sessions = self._sessions()
        daily = _bucket_daily(sessions)
        per_provider = _bucket_provider_daily(sessions)

        for day, row in daily.items():
            below = sum(
                entry["sessions"]
                for rows in per_provider.values()
                for entry in rows
                if entry["key"] == day
            )
            self.assertEqual(row["sessions"], below,
                             f"{day} 总体日行和分 provider 行对不上")

    def test_the_day_row_counts_conversations_not_transcripts(self):
        from sandglass.report import _bucket_daily

        (row,) = _bucket_daily(self._sessions()).values()

        # claude-root, codex-root, and the orphan Codex subagent nobody can
        # attribute. The two linked subagents belong to conversations already
        # counted.
        self.assertEqual(row["sessions"], 3)


class WindowSessionCountTests(unittest.TestCase):
    """The same word, in the rows beside the quota windows.

    These two had no fallback at all, so a window whose only activity was
    subagent work read "0 sessions" next to the tokens that work spent.
    """

    def _windows(self, now):
        start = now - timedelta(minutes=30)
        return [{
            "label": "five_hour",
            "window_start": start.isoformat(),
            "resets_at": (now + timedelta(hours=4)).isoformat(),
            "window_minutes": 300,
        }]

    def test_a_window_of_only_subagent_work_reports_its_conversation(self):
        from sandglass import report

        now = datetime.now(timezone.utc)
        at = (now - timedelta(minutes=5)).isoformat()
        account = Account(provider="codex", account_id="acct-1", label="acct-1")
        sessions = [
            _session("sub-a", root="root-1", subagent=True, at=at),
            _session("sub-b", root="root-1", subagent=True, at=at),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(report, "quota_anchor",
                                   lambda *_: ("", "period", False)), \
                 mock.patch.object(report, "quota_anchor_source", lambda *_: ""), \
                 mock.patch("sandglass.paths.meter_home", lambda: Path(tmp)):
                rows = report._local_in_windows(account, sessions, self._windows(now))

        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0]["usage"]["total_tokens"], 0)
        self.assertEqual(rows[0]["sessions"], 1, "只有子代理活动的窗口报了 0 个会话")


if __name__ == "__main__":
    unittest.main()
