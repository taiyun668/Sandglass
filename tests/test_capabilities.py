import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandglass import capabilities
from sandglass.capabilities import provider_capabilities
from sandglass.models import Account, QuotaWindow


class ProviderCapabilityTests(unittest.TestCase):
    def test_each_provider_declares_three_independent_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_projects = root / "claude" / "projects"
            codex_home = root / "codex"
            grok_home = root / "grok"
            claude_projects.mkdir(parents=True)
            (claude_projects / "session.jsonl").write_text("{}\n", encoding="utf-8")
            (codex_home / "sessions").mkdir(parents=True)
            (codex_home / "sessions" / "rollout-1.jsonl").write_text("{}\n", encoding="utf-8")

            account = Account(
                provider="claude",
                account_id="claude-1",
                active=True,
                extra={
                    "account_source": "official_claude_config",
                    "account_source_grade": "C",
                    "account_source_official": True,
                    "quota_source": "live",
                    "windows": [QuotaWindow(label="5h", used_percent=12).as_dict()],
                },
            )
            with (
                patch("sandglass.capabilities.claude_project_roots", return_value=[claude_projects]),
                patch("sandglass.capabilities.codex_home", return_value=codex_home),
                patch("sandglass.capabilities.grok_home", return_value=grok_home),
            ):
                rows = provider_capabilities([account])

        self.assertEqual(set(rows), {"claude", "codex", "grok"})
        for provider in rows.values():
            self.assertEqual(
                set(provider),
                {"account_discovery", "local_usage", "official_quota"},
            )
        self.assertTrue(rows["claude"]["account_discovery"]["available"])
        self.assertTrue(rows["claude"]["local_usage"]["available"])
        self.assertTrue(rows["claude"]["official_quota"]["available"])
        self.assertFalse(rows["codex"]["account_discovery"]["available"])
        self.assertTrue(rows["codex"]["local_usage"]["available"])
        self.assertFalse(rows["codex"]["official_quota"]["available"])
        self.assertFalse(rows["grok"]["account_discovery"]["available"])
        self.assertFalse(rows["grok"]["local_usage"]["available"])
        self.assertFalse(rows["grok"]["official_quota"]["available"])

    def test_quota_failure_is_not_reported_as_quota_availability(self):
        account = Account(
            provider="codex",
            account_id="codex-1",
            extra={
                "account_source": "official_auth",
                "account_source_grade": "C",
                "account_source_official": True,
                "quota_source": "error",
                "quota_error": "Rate limited (HTTP 429)",
            },
        )
        with (
            patch("sandglass.capabilities.claude_project_roots", return_value=[]),
            patch("sandglass.capabilities.codex_home", return_value=Path("Z:/missing-codex")),
            patch("sandglass.capabilities.grok_home", return_value=Path("Z:/missing-grok")),
        ):
            rows = provider_capabilities([account])

        quota = rows["codex"]["official_quota"]
        self.assertFalse(quota["available"])
        self.assertEqual(quota["status"], "error")
        self.assertEqual(quota["degradation"], "local_usage_only")
        self.assertNotIn("Rate limited", str(quota))

    def test_third_party_account_does_not_enable_public_capabilities(self):
        account = Account(
            provider="grok",
            account_id="community-account",
            extra={"account_source": "community_store", "account_source_official": False},
        )
        with (
            patch("sandglass.capabilities.claude_project_roots", return_value=[]),
            patch("sandglass.capabilities.codex_home", return_value=Path("Z:/missing-codex")),
            patch("sandglass.capabilities.grok_home", return_value=Path("Z:/missing-grok")),
        ):
            grok = provider_capabilities([account])["grok"]

        self.assertFalse(grok["account_discovery"]["available"])
        self.assertFalse(grok["official_quota"]["available"])

    def test_admitted_user_adapter_account_enables_labeled_product_capability(self):
        account = Account(
            provider="grok",
            account_id="adapter-account",
            extra={
                "account_source": "user_adapter:identity.timeline",
                "account_source_official": False,
                "quota_source": "user_adapter:identity.timeline",
                "windows": [{"label": "7d", "used_percent": 30}],
            },
        )
        with (
            patch("sandglass.capabilities.claude_project_roots", return_value=[]),
            patch("sandglass.capabilities.codex_home", return_value=Path("Z:/missing-codex")),
            patch("sandglass.capabilities.grok_home", return_value=Path("Z:/missing-grok")),
        ):
            grok = provider_capabilities([account])["grok"]

        self.assertTrue(grok["account_discovery"]["available"])
        self.assertEqual(grok["account_discovery"]["status"], "user_adapter_available")
        self.assertEqual(grok["account_discovery"]["adapter_accounts"], 1)
        self.assertTrue(grok["official_quota"]["available"])


class QuotaStatusAttributionTests(unittest.TestCase):
    """The status shown must belong to the account whose window is shown."""

    def test_a_live_account_without_a_window_cannot_lend_its_status(self):
        holder = Account(provider="codex", account_id="has-window",
                         extra={"windows": [{"label": "7d"}], "quota_source": "error"})
        other = Account(provider="codex", account_id="no-window",
                        extra={"quota_source": "live"})

        available, status = capabilities._quota_status([holder, other])

        self.assertTrue(available)
        self.assertNotEqual(status, "live",
                            "窗口来自一个账号,状态来自另一个")
        self.assertEqual(status, "available")

    def test_the_status_still_comes_from_the_account_that_has_the_window(self):
        holder = Account(provider="codex", account_id="has-window",
                         extra={"windows": [{"label": "7d"}], "quota_source": "cached"})
        other = Account(provider="codex", account_id="no-window",
                        extra={"quota_source": "error"})

        self.assertEqual(capabilities._quota_status([holder, other]), (True, "cached"))

    def test_no_window_anywhere_still_reports_the_error(self):
        broken = Account(provider="codex", account_id="a", extra={"quota_source": "error"})
        self.assertEqual(capabilities._quota_status([broken]), (False, "error"))


if __name__ == "__main__":
    unittest.main()
