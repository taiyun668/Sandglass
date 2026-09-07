import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from sandglass.quota import refresh_quota_provider
from sandglass.signals import QuotaSignalMonitor, record_quota_signal


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _codex_registry(account: str, used: float, reset: int = 2000) -> dict:
    return {
        "active_account_key": account,
        "active_account_activated_at_ms": 1787932800000,
        "accounts": [
            {
                "account_key": account,
                "chatgpt_account_id": account,
                "last_usage": {
                    "primary": {"used_percent": used, "resets_at": reset},
                    "secondary": {"used_percent": used, "resets_at": reset + 1000},
                },
            }
        ],
    }


def _codex_auth(account: str, token: str = "present") -> dict:
    return {"tokens": {"account_id": account, "access_token": token}}


def _grok_auth(account: str, token: str = "present") -> dict:
    return {
        f"https://auth.x.ai::{account}": {
            "auth_mode": "oidc",
            "user_id": account,
            "key": token,
        }
    }


class QuotaSignalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.codex = root / "codex"
        self.claude = root / "claude"
        self.grok = root / "grok"
        self.community_grok = root / "community-grok-app" / "data" / "accounts"
        self.env = patch.dict(
            os.environ,
            {
                "CODEX_HOME": str(self.codex),
                "CLAUDE_CONFIG_DIR": str(self.claude),
                "GROK_HOME": str(self.grok),
                "SANDGLASS_HOME": str(root / "sandglass"),
            },
            clear=False,
        )
        self.env.start()
        _write(
            self.claude / ".credentials.json",
            {"claudeAiOauth": {"accessToken": "present", "subscriptionType": "max"}},
        )
        _write(
            self.claude / ".claude.json",
            {"oauthAccount": {"accountUuid": "a1", "emailAddress": "a1@example.com"}},
        )
        _write(self.codex / "auth.json", _codex_auth("c1"))
        _write(self.codex / "accounts" / "registry.json", _codex_registry("c1", 40))
        _write(self.grok / "auth.json", _grok_auth("g1"))
        _write(
            self.community_grok / "index.json",
            {"activeId": "profile-1", "profiles": [{"id": "profile-1"}]},
        )
        _write(self.community_grok / "profile-1" / "auth.json", _grok_auth("g1"))
        _write(
            self.community_grok.parent / "account_billing_cache.json",
            {
                "creditUsagePercent": 60,
                "remainingPercent": 40,
                "billingPeriodStart": "2026-08-26T00:00:00Z",
                "billingPeriodEnd": "2026-09-02T00:00:00Z",
                "resetsAt": "2026-09-02T00:00:00Z",
            },
        )

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def test_third_party_registry_changes_do_not_wake_provider(self):
        monitor = QuotaSignalMonitor()
        _write(self.codex / "accounts" / "registry.json", _codex_registry("c1", 41))
        billing = json.loads((self.community_grok.parent / "account_billing_cache.json").read_text())
        billing.update({"creditUsagePercent": 61, "remainingPercent": 39})
        _write(self.community_grok.parent / "account_billing_cache.json", billing)
        self.assertEqual(monitor.poll(), set())

    def test_community_grok_billing_recovery_is_not_a_signal(self):
        monitor = QuotaSignalMonitor()
        _write(self.codex / "accounts" / "registry.json", _codex_registry("c1", 5))
        self.assertEqual(monitor.poll_events(), {})

        billing = json.loads((self.community_grok.parent / "account_billing_cache.json").read_text())
        billing.update({"creditUsagePercent": 10, "remainingPercent": 90})
        _write(self.community_grok.parent / "account_billing_cache.json", billing)
        self.assertEqual(monitor.poll_events(), {})

    def test_identity_and_authentication_changes_are_distinguished(self):
        monitor = QuotaSignalMonitor()
        _write(
            self.claude / ".claude.json",
            {"oauthAccount": {"accountUuid": "a2", "emailAddress": "a2@example.com"}},
        )
        self.assertEqual(monitor.poll_events(), {"claude": {"identity_changed"}})

        _write(self.codex / "auth.json", _codex_auth("c1", "refreshed"))
        self.assertEqual(monitor.poll_events(), {"codex": {"authentication_changed"}})

        _write(self.codex / "auth.json", _codex_auth("c2", "refreshed"))
        self.assertEqual(monitor.poll_events(), {"codex": {"identity_changed"}})

        _write(self.grok / "auth.json", _grok_auth("g2"))
        self.assertEqual(monitor.poll_events(), {"grok": {"identity_changed"}})

    def test_third_party_registry_period_is_ignored(self):
        monitor = QuotaSignalMonitor()
        _write(self.codex / "accounts" / "registry.json", _codex_registry("c1", 40, reset=3000))
        self.assertEqual(monitor.poll_events(), {})

    def test_known_claude_reset_time_wakes_once(self):
        now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
        deadline = (now + timedelta(seconds=5)).isoformat()
        sandglass = Path(os.environ["SANDGLASS_HOME"])
        _write(
            sandglass / "quota-cache.json",
            {"claude": {"windows": [{"label": "5h", "resets_at": deadline}]}},
        )
        clock = [now]
        monitor = QuotaSignalMonitor(now=lambda: clock[0])
        self.assertEqual(monitor.poll(), set())
        clock[0] = now + timedelta(seconds=5)
        self.assertEqual(monitor.poll_events(), {"claude": {"scheduled_reset"}})
        self.assertEqual(monitor.poll(), set())

    def test_signal_wait_sleeps_until_the_published_reset(self):
        now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
        deadline = (now + timedelta(seconds=5)).isoformat()
        sandglass = Path(os.environ["SANDGLASS_HOME"])
        _write(
            sandglass / "quota-cache.json",
            {"claude": {"windows": [{"label": "5h", "resets_at": deadline}]}},
        )
        monitor = QuotaSignalMonitor(now=lambda: now)
        self.assertEqual(monitor.next_poll_delay(15.0), 5.0)
        monitor.poll()
        clock = [now + timedelta(seconds=5)]
        monitor._now = lambda: clock[0]
        self.assertEqual(monitor.next_poll_delay(15.0), 0.05)
        monitor.poll_events()
        self.assertEqual(monitor.next_poll_delay(15.0), 15.0)

    def test_signal_wait_uses_the_idle_cap_when_no_reset_is_due(self):
        monitor = QuotaSignalMonitor(
            now=lambda: datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(monitor.next_poll_delay(15.0), 15.0)


class TargetedRefreshTests(unittest.TestCase):
    def setUp(self):
        """Give the whole class its own state home.

        e15f12f gave the quota cache merge the interprocess file lock it needed,
        and the lock file is created in meter_home() -- so these tests, which
        stub _read_cache and _write_cache but not the path, began creating
        .identity-ledgers.lock in the directory the running product uses.
        Isolating per class rather than per test keeps the next one added here
        from reaching it again.
        """
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        patcher = patch.dict(os.environ, {"SANDGLASS_HOME": home.name})
        patcher.start()
        self.addCleanup(patcher.stop)


    def test_codex_signal_bypasses_only_codex_cache(self):
        cached = {
            "claude": {"ok": True, "provider": "claude"},
            "grok": {"ok": True, "provider": "grok"},
        }
        fresh = {"ok": True, "provider": "codex", "windows": []}
        with patch("sandglass.quota._read_cache", return_value=cached), patch(
            "sandglass.quota._write_cache"
        ) as write, patch("sandglass.quota.fetch_codex_quota", return_value=fresh) as codex, patch(
            "sandglass.quota.fetch_claude_quota"
        ) as claude, patch("sandglass.quota.fetch_grok_quota") as grok:
            result = refresh_quota_provider("codex")
        codex.assert_called_once_with()
        claude.assert_not_called()
        grok.assert_not_called()
        self.assertEqual(result, {"codex": {**fresh, "fetched_at": result["codex"]["fetched_at"]}})
        write.assert_called_once_with({**cached, "codex": result["codex"]})

    def test_signal_propagates_to_immediate_provider_scans(self):
        from sandglass.serve import _refresh_quota_signals

        monitor = Mock()
        monitor.poll_events.return_value = {
            "grok": {"quota_recovered"},
            "codex": {"identity_changed"},
        }
        with patch("sandglass.accounts.note_current_identity") as identity, patch(
            "sandglass.accounts.note_codex_identity"
        ) as codex_identity, patch(
            "sandglass.serve.refresh_quota_provider", return_value={}
        ) as refresh, patch("sandglass.serve.record_quota_signal") as record:
            changed = _refresh_quota_signals(monitor)
        self.assertEqual(changed, {"codex", "grok"})
        identity.assert_called_once_with(observe_codex=False)
        codex_identity.assert_called_once_with()
        self.assertEqual([call.args[0] for call in refresh.call_args_list], ["codex", "grok"])
        self.assertEqual([call.args[0] for call in record.call_args_list], ["codex", "grok"])

    def test_codex_observation_survives_other_provider_observation_failure(self):
        from sandglass.serve import _refresh_quota_signals

        monitor = Mock()
        monitor.poll_events.return_value = {"claude": {"identity_changed"}}
        with patch(
            "sandglass.accounts.note_current_identity",
            side_effect=RuntimeError("claude read failed"),
        ), patch("sandglass.accounts.note_codex_identity") as codex_identity, patch(
            "sandglass.serve.refresh_quota_provider", return_value={}
        ), patch("sandglass.serve.record_quota_signal"):
            _refresh_quota_signals(monitor)

        codex_identity.assert_called_once_with()

    def test_signal_evidence_is_privacy_minimal(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            record_quota_signal(
                "grok",
                {"quota_recovered"},
                result={
                    "grok": {"ok": True, "fetched_at": "2026-08-29T12:00:00Z"},
                    "grok:second": {"ok": False, "error": "secret-bearing upstream body"},
                },
            )
            path = Path(tmp) / "quota-signal-events.jsonl"
            event = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(event["provider"], "grok")
        self.assertEqual(event["official_refresh_status"], "partial")
        self.assertEqual(event["official_refresh_attempted"], 2)
        self.assertEqual(event["official_refresh_succeeded"], 1)
        self.assertEqual(event["error"], "refresh_failed")
        self.assertNotIn("secret-bearing", json.dumps(event))

    def test_a_signal_write_it_could_not_do_is_reported_rather_than_dropped(self):
        """A missing evidence row used to mean no signal fired.

        `record_quota_signal` caught OSError and returned. The watch loop
        that called it had already refreshed quota, so the event was the
        only local witness, and losing it in silence looked like a quiet
        interval. Still not raised: the refresh itself must not fail over
        a file it could not save.
        """
        recorded = []
        cleared = []
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ), patch("sandglass.diagnostics.record_component_failure",
                 lambda name, exc: recorded.append(name)), patch(
            "sandglass.diagnostics.clear_component_failure",
            lambda name: cleared.append(name),
        ):
            real_open = Path.open

            def open_fail(self, *args, **kwargs):
                if self.name == "quota-signal-events.jsonl":
                    raise OSError("disk full")
                return real_open(self, *args, **kwargs)

            with patch.object(Path, "open", open_fail):
                record_quota_signal("codex", {"identity_changed"})
            self.assertEqual(recorded, ["quota_signal_write"])
            self.assertEqual(cleared, [])
            self.assertFalse((Path(tmp) / "quota-signal-events.jsonl").exists())
            record_quota_signal("codex", {"identity_changed"})
            self.assertEqual(cleared, ["quota_signal_write"])
            self.assertTrue((Path(tmp) / "quota-signal-events.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
