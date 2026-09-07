import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from sandglass.models import Account, SessionRecord, TokenUsage
from sandglass.serve import _activity_days


class ActivityHistoryTests(unittest.TestCase):
    @patch("sandglass.serve._switch_runs_for", return_value=[])
    @patch("sandglass.serve._owns_minute", return_value=True)
    def test_all_range_keeps_proven_history_older_than_one_year(
            self, _owns, _runs):
        old = datetime.now(timezone.utc) - timedelta(days=400)
        account = Account(provider="claude", account_id="account-1")
        session = SessionRecord(
            provider="claude",
            session_id="session-1",
            path="fixture",
            account_id=account.account_id,
            timeline=[(old.isoformat(), TokenUsage(input_tokens=123, calls=1))],
        )

        activity = _activity_days(account, [session], None, [account])

        self.assertGreater(activity["span"], 365)
        self.assertEqual(activity["spent"], 123)
        self.assertEqual(activity["days"][0]["day"], old.astimezone().date().isoformat())

    @patch("sandglass.serve._switch_runs_for", return_value=[])
    @patch("sandglass.serve._owns_minute", return_value=True)
    def test_all_range_always_supplies_fixed_heatmap_span(self, _owns, _runs):
        account = Account(provider="codex", account_id="account-1")

        activity = _activity_days(account, [], None, [account])

        self.assertEqual(activity["span"], 182)
        self.assertEqual(len(activity["days"]), 182)

    def test_local_payload_keeps_activity_when_quota_windows_are_unavailable(self):
        """A rate-limited quota fetch must not hide independently parsed usage."""
        from sandglass import serve

        account = Account(provider="claude", account_id="account-1", extra={})
        activity = {"days": [{"day": "2026-08-31", "spent": 123}], "spent": 123}
        store = MagicMock()
        store.settings.return_value = {}
        old_cache = serve._local_cache
        serve._local_cache = {
            "at": 0.0,
            "payload": None,
            "identity_stamp": None,
            "source_stamp": None,
        }
        try:
            with patch(
                "sandglass.accounts.identity_source_stamp", return_value=("identity",)
            ), patch.object(
                serve, "_user_identity_source_stamp", return_value=("sources",)
            ), patch.object(
                serve, "load_accounts", return_value=[account]
            ), patch.object(
                serve, "SessionCache", return_value=MagicMock(close=MagicMock())
            ), patch.object(
                serve, "collect_all", return_value=[]
            ), patch.object(
                serve, "apply_user_evidence", side_effect=lambda sessions, settings: sessions
            ), patch.object(
                serve, "UserSourceStore", return_value=store
            ), patch.object(
                serve, "attribution_mode", return_value="single_official"
            ), patch.object(
                serve, "_single_account_id", return_value=account.account_id
            ), patch.object(
                serve, "_windows_for", return_value=[]
            ) as windows_for, patch.object(
                serve, "_activity_days", return_value=activity
            ) as activity_days:
                payload = serve._local_windows_payload(live=False)
        finally:
            serve._local_cache = old_cache

        self.assertEqual(payload["accounts"], [{
            "provider": "claude",
            "account_id": "account-1",
            "windows": [],
            "activity": activity,
        }])
        windows_for.assert_called_once()
        activity_days.assert_called_once()


if __name__ == "__main__":
    unittest.main()
