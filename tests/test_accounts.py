import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from datetime import datetime, timedelta, timezone

from sandglass.accounts import (
    _claude_owner_at,
    _claude_plan_name,
    _codex_plan_name,
    _grok_owner_at,
    assign_codex_account,
    assign_grok_account,
    grok_auth_sources,
    grok_identity_runs,
    load_claude_accounts,
    load_codex_accounts,
    load_grok_accounts,
)
from sandglass.models import Account, SessionRecord, TokenUsage, parse_ts
from sandglass.report import build_report


def _cli_auth(email: str, user_id: str, key: str = "cli-key") -> dict:
    return {
        f"https://auth.x.ai::{user_id}": {
            "email": email,
            "user_id": user_id,
            "principal_id": user_id,
            "auth_mode": "oidc",
            "key": key,
        }
    }


class GrokAccountMergeTests(unittest.TestCase):
    def test_community_grok_app_profiles_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            grok_home = Path(tmp) / "cli"
            grok_home.mkdir()
            (grok_home / "auth.json").write_text(
                json.dumps(_cli_auth("primary@example.com", "uid-cli")),
                encoding="utf-8",
            )
            app = Path(tmp) / "app"
            same = "prof-same"
            other = "prof-other"
            (app / same).mkdir(parents=True)
            (app / other).mkdir()
            (app / same / "auth.json").write_text(
                json.dumps(_cli_auth("primary@example.com", "uid-cli", "app-key")),
                encoding="utf-8",
            )
            (app / other / "auth.json").write_text(
                json.dumps(_cli_auth("secondary@example.com", "uid-other", "other-key")),
                encoding="utf-8",
            )
            (app / "index.json").write_text(
                json.dumps(
                    {
                        "activeId": same,
                        "profiles": [
                            {"id": same, "email": "primary@example.com"},
                            {"id": other, "email": "secondary@example.com"},
                            {"id": "prof-third", "email": "third@example.com"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (app / "prof-third").mkdir()
            env = {
                "GROK_HOME": str(grok_home),
                "GROK_APP_ACCOUNTS": str(app),
                "SANDGLASS_HOME": str(Path(tmp) / "meter"),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                sources = grok_auth_sources()
                accounts = load_grok_accounts()
            self.assertEqual(len(sources), 1)
            self.assertEqual([a.email for a in accounts], ["primary@example.com"])
            self.assertEqual(accounts[0].account_id, "uid-cli")
            self.assertTrue(accounts[0].active)
            self.assertEqual(accounts[0].extra.get("clients"), ["cli"])
            self.assertEqual(accounts[0].extra.get("account_source"), "official_grok_cli")
            self.assertEqual(accounts[0].extra.get("account_source_grade"), "C")
            self.assertIs(accounts[0].extra.get("account_source_official"), True)

    def test_identity_ledger_defines_historical_grok_accounts_and_ownership(self):
        from sandglass import accounts as accmod

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grok_home = root / "grok"
            grok_home.mkdir()
            meter = root / "meter"
            meter.mkdir()
            (grok_home / "auth.json").write_text(
                json.dumps(_cli_auth("current@example.com", "uid-current")),
                encoding="utf-8",
            )
            ledger = [
                {"at": "2026-08-30T00:00:00Z", "account_id": "uid-history"},
                {"at": "2026-08-30T01:00:00Z", "account_id": "uid-current"},
            ]
            (meter / "grok-official-identity-runs.json").write_text(
                json.dumps(ledger), encoding="utf-8"
            )
            env = {"GROK_HOME": str(grok_home), "SANDGLASS_HOME": str(meter)}
            accmod._GROK_RUNS = accmod._GROK_RUNS_SRC = None
            try:
                with mock.patch.dict(os.environ, env, clear=False):
                    peers = load_grok_accounts()
                    runs = grok_identity_runs()
                    session = SessionRecord(
                        provider="grok",
                        session_id="historical",
                        path="updates.jsonl",
                        started_at="2026-08-30T00:30:00Z",
                        ended_at="2026-08-30T00:30:00Z",
                        usage=TokenUsage(output_tokens=100),
                        timeline=[
                            ("2026-08-30T00:30:00Z", TokenUsage(output_tokens=100))
                        ],
                    )
                    with mock.patch("sandglass.report.switch_runs_for", return_value=runs):
                        report = build_report(
                            [session], accounts=peers, live_quota=False
                        )
            finally:
                accmod._GROK_RUNS = accmod._GROK_RUNS_SRC = None

            self.assertEqual(
                {account.account_id for account in peers},
                {"uid-current", "uid-history"},
            )
            historical = next(
                account for account in peers if account.account_id == "uid-history"
            )
            self.assertEqual(
                historical.extra.get("account_source"), "official_identity_ledger"
            )
            self.assertTrue(historical.extra.get("account_metadata_incomplete"))
            self.assertEqual(report["unassigned_by_provider"], [])
            owned = next(
                row for row in report["accounts"] if row["account_id"] == "uid-history"
            )
            self.assertEqual(owned["local_usage"]["total_tokens"], 100)

    def test_parse_ts_accepts_extra_fractional_digits(self):
        dt = parse_ts("2026-08-27T19:25:31.623828200Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_grok_sessions_follow_cli_switch(self):
        older = datetime.now(timezone.utc) - timedelta(days=10)
        newer = datetime.now(timezone.utc) - timedelta(hours=1)
        cli = Account(
            provider="grok",
            account_id="cli-user",
            email="cli@example.com",
            active=True,
            extra={"last_used_at": newer.isoformat(), "activated_at_ms": newer.isoformat()},
        )
        other = Account(
            provider="grok",
            account_id="app-user",
            email="app@example.com",
            extra={"last_used_at": older.isoformat(), "activated_at_ms": older.isoformat()},
        )
        recent_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        recent = SessionRecord(
            provider="grok",
            session_id="new",
            path="new.jsonl",
            ended_at=recent_at.isoformat(),
            timeline=[(recent_at.isoformat(), TokenUsage(output_tokens=10))],
        )
        ancient_at = older + timedelta(minutes=5)
        ancient = SessionRecord(
            provider="grok",
            session_id="old",
            path="old.jsonl",
            ended_at=ancient_at.isoformat(),
            timeline=[(ancient_at.isoformat(), TokenUsage(output_tokens=10))],
        )
        runs = [(newer.isoformat(), "cli-user")]
        with mock.patch("sandglass.accounts.grok_identity_runs", return_value=runs):
            self.assertEqual(assign_grok_account(recent, [cli, other]).account_id, "cli-user")
            self.assertEqual(assign_grok_account(ancient, [cli, other]).account_id, "")

class ClaudePlanTests(unittest.TestCase):
    def test_max_and_team_use_rate_limit_tier(self):
        self.assertEqual(
            _claude_plan_name({"subscriptionType": "max", "rateLimitTier": "default_claude_max_5x"}),
            "max 5x",
        )
        self.assertEqual(
            _claude_plan_name({"subscriptionType": "max", "rateLimitTier": "default_claude_max_20x"}),
            "max 20x",
        )
        self.assertEqual(
            _claude_plan_name({"subscriptionType": "team", "rateLimitTier": "default_claude_max_5x"}),
            "max 5x",
        )
        self.assertEqual(
            _claude_plan_name({"subscriptionType": "max"}),
            "max",
        )

    def test_pro_keeps_subscription_type(self):
        self.assertEqual(
            _claude_plan_name({"subscriptionType": "pro", "rateLimitTier": "default_claude_ai"}),
            "pro",
        )
        self.assertEqual(_claude_plan_name({"subscriptionType": "pro"}), "pro")

    def test_tier_alone_and_profile_fallback(self):
        self.assertEqual(
            _claude_plan_name({"rateLimitTier": "default_claude_max_5x"}),
            "max 5x",
        )
        self.assertEqual(
            _claude_plan_name(
                {},
                {"seatTier": "max", "organizationRateLimitTier": "default_claude_max_20x"},
            ),
            "max 20x",
        )

    def test_load_accounts_reads_rate_limit_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".credentials.json").write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "subscriptionType": "max",
                            "rateLimitTier": "default_claude_max_5x",
                            "accessToken": "x",
                        }
                    }
                ),
                encoding="utf-8",
            )
            (home / ".claude.json").write_text(
                json.dumps(
                    {
                        "oauthAccount": {
                            "emailAddress": "max@example.com",
                            "accountUuid": "uuid-max",
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "CLAUDE_CONFIG_DIR": str(home),
                "SANDGLASS_HOME": str(home / "sandglass"),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                accounts = load_claude_accounts()
            self.assertEqual(len(accounts), 1)
            self.assertEqual(accounts[0].plan, "max 5x")
            self.assertEqual(accounts[0].extra.get("rate_limit_tier"), "default_claude_max_5x")


class CodexPlanTests(unittest.TestCase):
    def test_prolite_is_pro_5x_and_pro_is_pro_20x(self):
        self.assertEqual(_codex_plan_name("prolite"), "pro 5x")
        self.assertEqual(_codex_plan_name("pro_lite"), "pro 5x")
        self.assertEqual(_codex_plan_name("pro 5x"), "pro 5x")
        self.assertEqual(_codex_plan_name("pro"), "pro 20x")
        self.assertEqual(_codex_plan_name("pro_20x"), "pro 20x")
        self.assertEqual(_codex_plan_name("plus"), "plus")
        self.assertEqual(_codex_plan_name("team"), "team")

    def test_third_party_registry_is_ignored_when_official_auth_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "accounts").mkdir()
            (home / "auth.json").write_text(
                json.dumps({"tokens": {"account_id": "official", "access_token": "secret"}}),
                encoding="utf-8",
            )
            (home / "accounts" / "registry.json").write_text(
                json.dumps(
                    {
                        "active_account_key": "k1",
                        "accounts": [
                            {
                                "account_key": "k1",
                                "chatgpt_account_id": "third-party-ghost",
                                "email": "ghost@example.com",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {"CODEX_HOME": str(home), "SANDGLASS_HOME": str(home / "sandglass")},
                clear=False,
            ):
                accounts = load_codex_accounts()
            self.assertEqual([account.account_id for account in accounts], ["official"])
            self.assertEqual(accounts[0].extra.get("account_source"), "official_auth")
            self.assertTrue(accounts[0].extra.get("account_source_official"))

    def test_single_discovered_account_does_not_claim_unproven_history(self):
        account = Account(
            provider="codex",
            account_id="only",
            email="only@example.com",
            active=True,
            extra={"account_source": "official_auth"},
        )
        record = SessionRecord(
            provider="codex",
            session_id="historical",
            path="unlinked-rollout.jsonl",
            started_at="2026-01-01T00:00:00Z",
            ended_at="2026-01-01T00:05:00Z",
            usage=TokenUsage(input_tokens=100, calls=1),
        )
        with mock.patch("sandglass.accounts.codex_identity_runs", return_value=[]):
            assigned = assign_codex_account(record, [account])
        self.assertEqual(assigned.account_id, "")

    def test_single_account_still_accepts_direct_identity_timeline(self):
        account = Account(
            provider="codex",
            account_id="only",
            email="only@example.com",
            active=True,
        )
        at = "2026-08-29T10:00:00Z"
        record = SessionRecord(
            provider="codex",
            session_id="observed",
            path="rollout.jsonl",
            started_at=at,
            ended_at=at,
            timeline=[(at, TokenUsage(input_tokens=100, calls=1))],
        )
        with mock.patch("sandglass.accounts.codex_identity_runs", return_value=[(at, "only@example.com")]):
            assigned = assign_codex_account(record, [account])
        self.assertEqual(assigned.account_id, "only")
        self.assertEqual(
            assigned.extra.get("account_identity_scope"),
            "session_timeline_majority",
        )
        self.assertEqual(
            assigned.extra.get("account_identity_source"),
            "sandglass_official_observation_runs",
        )

    def test_missing_or_empty_auth_does_not_invent_local_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with mock.patch.dict(
                os.environ,
                {"CODEX_HOME": str(home), "SANDGLASS_HOME": str(home / "sandglass")},
                clear=False,
            ):
                self.assertEqual(load_codex_accounts(), [])
                (home / "auth.json").write_text("{}", encoding="utf-8")
                self.assertEqual(load_codex_accounts(), [])
                (home / "auth.json").write_text(
                    json.dumps({"tokens": {"access_token": "present"}}),
                    encoding="utf-8",
                )
                self.assertEqual(load_codex_accounts(), [])


class GrokIdentityRunsTests(unittest.TestCase):
    def test_identity_runs_refresh_when_cli_principal_changes(self):
        import sandglass.accounts as accmod

        accmod._GROK_RUNS = None
        accmod._GROK_RUNS_SRC = None
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cli = home / "cli"
            logs = cli / "logs"
            logs.mkdir(parents=True)
            app = home / "app"
            app.mkdir()
            meter = home / "meter"
            meter.mkdir()

            def write_cli(email: str, uid: str, at: str):
                (cli / "auth.json").write_text(json.dumps(_cli_auth(email, uid)), encoding="utf-8")
                row = {
                    "ts": at,
                    "msg": "auth init user_info check",
                    "ctx": {"user_id": uid},
                }
                with (logs / "unified.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row) + "\n")

            write_cli("old@example.com", "uid-old", "2026-08-28T10:00:00Z")
            env = {
                "GROK_HOME": str(cli),
                "SANDGLASS_HOME": str(meter),
            }
            later = parse_ts("2026-08-28T12:20:00Z")
            with mock.patch.dict(os.environ, env, clear=False):
                runs = grok_identity_runs()
                self.assertEqual(_grok_owner_at(runs, later), "uid-old")
                write_cli("new@example.com", "uid-new", "2026-08-28T12:13:47Z")
                runs = grok_identity_runs()
                self.assertEqual(_grok_owner_at(runs, later), "uid-new")
        accmod._GROK_RUNS = None
        accmod._GROK_RUNS_SRC = None


class SelfSampledIdentityTests(unittest.TestCase):
    """The switch history should not depend on another program having run."""

    def test_claude_switches_persist_accounts_and_minute_owners(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "claude"
            meter = root / "sandglass"
            home.mkdir()

            def write_profile(account_id: str, email: str) -> None:
                (home / ".claude.json").write_text(
                    json.dumps(
                        {
                            "oauthAccount": {
                                "accountUuid": account_id,
                                "emailAddress": email,
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                (home / ".credentials.json").write_text(
                    json.dumps({"claudeAiOauth": {"subscriptionType": "pro"}}),
                    encoding="utf-8",
                )

            with mock.patch.object(accounts, "claude_homes", lambda: [home]), \
                    mock.patch.object(accounts, "meter_home", lambda: meter), \
                    mock.patch.object(accounts, "_codex_signed_in", lambda: ("", "")), \
                    mock.patch.object(accounts, "_grok_signed_in", lambda: ("", "")), \
                    mock.patch.object(accounts, "codex_identity_runs", lambda: []), \
                    mock.patch.object(accounts, "grok_identity_runs", lambda: []):
                accounts._CLAUDE_RUNS = accounts._CLAUDE_RUNS_SRC = None
                write_profile("claude-a", "a@example.com")
                self.assertTrue(accounts.note_current_identity())
                first = accounts.claude_identity_runs()[0][0]
                time.sleep(0.002)
                write_profile("claude-b", "b@example.com")
                self.assertTrue(accounts.note_current_identity())
                runs = accounts.claude_identity_runs()
                discovered = accounts.load_claude_accounts()

            stored = (meter / "claude-observed-accounts.json").read_text(encoding="utf-8")
            self.assertEqual({account.account_id for account in discovered}, {"claude-a", "claude-b"})
            self.assertEqual([who for _, who in runs], ["claude-a", "claude-b"])
            self.assertEqual(_claude_owner_at(runs, parse_ts(first)), "claude-a")
            self.assertNotIn("subscriptionType", stored)
        accounts._CLAUDE_RUNS = accounts._CLAUDE_RUNS_SRC = None

    def test_official_switches_preserve_secret_free_observed_accounts(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "codex"
            meter = root / "sandglass"
            home.mkdir()
            env = {"CODEX_HOME": str(home), "SANDGLASS_HOME": str(meter)}

            def write_auth(account_id: str, secret: str) -> None:
                (home / "auth.json").write_text(
                    json.dumps(
                        {
                            "auth_mode": "chatgpt",
                            "tokens": {"account_id": account_id, "access_token": secret},
                        }
                    ),
                    encoding="utf-8",
                )

            with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
                accounts, "_grok_signed_in", lambda: ("", "")
            ), mock.patch.object(accounts, "grok_identity_runs", lambda: []):
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                write_auth("official-a", "secret-a")
                self.assertTrue(accounts.note_current_identity())
                write_auth("official-b", "secret-b")
                self.assertTrue(accounts.note_current_identity())
                discovered = accounts.load_codex_accounts()
                ledger = accounts.codex_identity_runs()

            stored = (meter / "codex-observed-accounts.json").read_text(encoding="utf-8")
            self.assertEqual({account.account_id for account in discovered}, {"official-a", "official-b"})
            self.assertEqual([who for _, who in ledger], ["official-a", "official-b"])
            self.assertNotIn("secret-a", stored)
            self.assertNotIn("secret-b", stored)
        accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None

    def test_official_auth_is_stamped_at_the_observation(self):
        """auth.json mtime is not an ownership boundary."""
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex"
            home.mkdir()
            auth = home / "auth.json"
            auth.write_text(
                json.dumps({"tokens": {"account_id": "official-new"}}),
                encoding="utf-8",
            )
            signed_in = datetime.now(timezone.utc) - timedelta(hours=3)
            os.utime(auth, (signed_in.timestamp(), signed_in.timestamp()))
            before = datetime.now(timezone.utc)
            with mock.patch.object(accounts, "codex_home", lambda: home), mock.patch.object(
                accounts, "codex_identity_runs", list
            ):
                at, who = accounts._codex_signed_in()
        self.assertEqual(who, "official-new")
        self.assertGreaterEqual(parse_ts(at), before)

    def test_the_stamp_never_runs_ahead_of_the_observation(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex"
            home.mkdir()
            auth = home / "auth.json"
            auth.write_text(
                json.dumps({"tokens": {"account_id": "official-new"}}),
                encoding="utf-8",
            )
            ahead = (datetime.now(timezone.utc) + timedelta(days=2)).timestamp()
            os.utime(auth, (ahead, ahead))
            before = datetime.now(timezone.utc)
            with mock.patch.object(accounts, "codex_home", lambda: home), mock.patch.object(
                accounts, "codex_identity_runs", list
            ):
                at, _ = accounts._codex_signed_in()
            after = datetime.now(timezone.utc)
        self.assertGreaterEqual(parse_ts(at), before)
        self.assertLessEqual(parse_ts(at), after)

    def test_a_new_signed_in_account_is_appended(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "codex-official-identity-runs.json"
            events_path = Path(tmp) / "codex-official-identity-events-v2.json"
            path.write_text(
                json.dumps(
                    [{"at": "2026-08-01T00:00:00+00:00", "to": "old@example.com"}]
                ),
                encoding="utf-8",
            )
            with patch.object(accounts, "meter_home", lambda: Path(tmp)),                  patch.object(accounts, "_claude_signed_in", lambda: ("", "", None)),                  patch.object(accounts, "claude_identity_runs", lambda: []),                  patch.object(accounts, "codex_identity_runs",
                              lambda: [("2026-08-01T00:00:00+00:00", "old@example.com")]),                  patch.object(accounts, "grok_identity_runs", lambda: []),                  patch.object(accounts, "_codex_signed_in",
                              lambda: ("2026-08-05T09:00:00+00:00", "new@example.com")),                  patch.object(accounts, "_official_codex_account", lambda: None),                  patch.object(accounts, "_grok_signed_in", lambda: ("", "")):
                self.assertTrue(accounts.note_current_identity())
                rows = json.loads(
                    (Path(tmp) / "codex-official-identity-runs.json").read_text(encoding="utf-8")
                )
            self.assertEqual([r["to"] for r in rows], ["old@example.com"])
            events = json.loads(events_path.read_text(encoding="utf-8"))
            self.assertEqual(
                events["events"],
                [{"at": "2026-08-05T09:00:00+00:00", "kind": "observed", "account_id": "new@example.com"}],
            )

    def test_the_same_account_writes_nothing(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "codex-official-identity-events-v2.json"
            path.write_text(
                json.dumps(
                    {"schema": 2, "events": [{"at": "2026-08-01T00:00:00+00:00", "kind": "observed", "account_id": "same@example.com"}]}
                ),
                encoding="utf-8",
            )
            with patch.object(accounts, "meter_home", lambda: Path(tmp)),                  patch.object(accounts, "_claude_signed_in", lambda: ("", "", None)),                  patch.object(accounts, "claude_identity_runs", lambda: []),                  patch.object(accounts, "codex_identity_runs",
                              lambda: [("2026-08-01T00:00:00+00:00", "same@example.com")]),                  patch.object(accounts, "grok_identity_runs", lambda: []),                  patch.object(accounts, "_codex_signed_in",
                              lambda: ("2026-08-05T09:00:00+00:00", "same@example.com")),                  patch.object(accounts, "_official_codex_account", lambda: None),                  patch.object(accounts, "_grok_signed_in", lambda: ("", "")):
                self.assertFalse(accounts.note_current_identity())
                self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["events"]), 1)

    def test_a_stamp_older_than_the_ledger_is_refused(self):
        """A stale observation must not rewrite history behind the last entry."""
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "codex-official-identity-events-v2.json"
            path.write_text(
                json.dumps(
                    {"schema": 2, "events": [{"at": "2026-08-10T00:00:00+00:00", "kind": "observed", "account_id": "new@example.com"}]}
                ),
                encoding="utf-8",
            )
            with patch.object(accounts, "meter_home", lambda: Path(tmp)),                  patch.object(accounts, "_claude_signed_in", lambda: ("", "", None)),                  patch.object(accounts, "claude_identity_runs", lambda: []),                  patch.object(accounts, "codex_identity_runs",
                              lambda: [("2026-08-10T00:00:00+00:00", "new@example.com")]),                  patch.object(accounts, "grok_identity_runs", lambda: []),                  patch.object(accounts, "_codex_signed_in",
                              lambda: ("2026-08-01T00:00:00+00:00", "old@example.com")),                  patch.object(accounts, "_official_codex_account", lambda: None),                  patch.object(accounts, "_grok_signed_in", lambda: ("", "")):
                self.assertFalse(accounts.note_current_identity())

    def test_codex_cache_reloads_when_own_ledger_changes(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "codex"
            home.mkdir(parents=True)
            ledger = root / "meter" / "codex-official-identity-events-v2.json"
            ledger.parent.mkdir()
            ledger.write_text(
                json.dumps(
                    {"schema": 2, "events": [{"at": "2026-08-01T00:00:00+00:00", "kind": "observed", "account_id": "old@example.com"}]},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            with mock.patch.object(accounts, "codex_home", lambda: home), mock.patch.object(
                accounts, "meter_home", lambda: ledger.parent
            ), mock.patch.object(accounts, "grok_home", lambda: root / "grok"):
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                first_stamp = accounts.identity_source_stamp()
                self.assertEqual(accounts.codex_identity_runs()[-1][1], "old@example.com")
                unchanged_mtime = ledger.stat().st_mtime_ns

                rows = json.loads(ledger.read_text(encoding="utf-8"))
                rows["events"].append({"at": "2026-08-02T00:00:00+00:00", "kind": "observed", "account_id": "new@example.com"})
                ledger.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

                self.assertNotEqual(accounts.identity_source_stamp(), first_stamp)
                self.assertEqual(accounts.codex_identity_runs()[-1][1], "new@example.com")
                after_reload = ledger.stat().st_mtime_ns
                accounts.codex_identity_runs()
                self.assertEqual(ledger.stat().st_mtime_ns, after_reload)
                self.assertLessEqual(unchanged_mtime, after_reload)
        accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None

    def test_codex_cache_reloads_same_size_same_mtime_content_change(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meter = root / "meter"
            meter.mkdir()
            ledger = meter / "codex-official-identity-events-v2.json"
            old = json.dumps(
                {"schema": 2, "events": [{"at": "2026-08-01T00:00:00+00:00", "kind": "observed", "account_id": "account-a"}]}
            )
            new = old.replace("account-a", "account-b")
            self.assertEqual(len(old), len(new))
            ledger.write_text(old, encoding="utf-8")
            original_mtime = ledger.stat().st_mtime_ns

            with mock.patch.object(accounts, "meter_home", lambda: meter), mock.patch.object(
                accounts, "codex_home", lambda: root / "codex"
            ):
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                self.assertEqual(accounts.codex_identity_runs()[-1][1], "account-a")
                ledger.write_text(new, encoding="utf-8")
                os.utime(ledger, ns=(original_mtime, original_mtime))
                self.assertEqual(accounts.codex_identity_runs()[-1][1], "account-b")
        accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None

    def test_codex_identity_read_never_rewrites_ledger_bytes(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            meter = Path(tmp)
            ledger = meter / "codex-official-identity-events-v2.json"
            original = (
                '{"schema":2,"events":[{"at":"2026-08-01T00:00:00+00:00","kind":"observed","account_id":"account-a"},'
                '{"at":"2026-08-02T00:00:00+00:00","kind":"observed","account_id":"account-b"}]}'
            ).encode()
            ledger.write_bytes(original)
            with mock.patch.object(accounts, "meter_home", lambda: meter), mock.patch.object(
                accounts, "codex_home", lambda: meter / "codex"
            ):
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                self.assertEqual(len(accounts.codex_identity_runs()), 2)
                self.assertEqual(ledger.read_bytes(), original)
        accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None

    def test_codex_does_not_cache_a_transient_ledger_read_failure(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            meter = Path(tmp)
            ledger = meter / "codex-official-identity-events-v2.json"
            ledger.write_text(
                json.dumps({"schema": 2, "events": [{"at": "2026-08-01T00:00:00+00:00", "kind": "observed", "account_id": "account-a"}]}),
                encoding="utf-8",
            )
            real_read = accounts._read_owned_json
            reads = 0

            def fail_once(path):
                nonlocal reads
                if path == ledger:
                    reads += 1
                    if reads == 1:
                        return accounts._OWNED_JSON_INVALID
                return real_read(path)

            with mock.patch.object(accounts, "meter_home", lambda: meter), mock.patch.object(
                accounts, "codex_home", lambda: meter / "codex"
            ), mock.patch.object(accounts, "_read_owned_json", fail_once):
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                self.assertEqual(accounts.codex_identity_runs(), [])
                self.assertIsNone(accounts._CODEX_RUNS)
                self.assertEqual(accounts.codex_identity_runs()[-1][1], "account-a")
                self.assertEqual(reads, 2)
        accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None

    def test_legacy_third_party_identity_ledger_is_not_migrated(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            meter = Path(tmp)
            (meter / "codex-identity-runs.json").write_text(
                json.dumps([{"at": "2026-08-01T00:00:00Z", "to": "third-party"}]),
                encoding="utf-8",
            )
            with mock.patch.object(accounts, "meter_home", lambda: meter), mock.patch.object(
                accounts, "codex_home", lambda: meter / "codex"
            ):
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                self.assertEqual(accounts.codex_identity_runs(), [])
                self.assertFalse((meter / "codex-official-identity-runs.json").exists())
        accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None

    def test_grok_cache_reloads_when_own_ledger_changes(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meter = root / "meter"
            meter.mkdir()
            ledger = meter / "grok-official-identity-runs.json"
            ledger.write_text(
                json.dumps(
                    [{"at": "2026-08-01T00:00:00+00:00", "account_id": "old"}],
                    indent=2,
                ),
                encoding="utf-8",
            )
            with mock.patch.object(accounts, "meter_home", lambda: meter), mock.patch.object(
                accounts, "grok_home", lambda: root / "grok"
            ):
                accounts._GROK_RUNS = accounts._GROK_RUNS_SRC = None
                self.assertEqual(accounts.grok_identity_runs()[-1][1], "old")
                rows = json.loads(ledger.read_text(encoding="utf-8"))
                rows.append({"at": "2026-08-02T00:00:00+00:00", "account_id": "new"})
                ledger.write_text(json.dumps(rows, indent=2), encoding="utf-8")
                self.assertEqual(accounts.grok_identity_runs()[-1][1], "new")
        accounts._GROK_RUNS = accounts._GROK_RUNS_SRC = None

    def test_grok_does_not_cache_a_transient_ledger_read_failure(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meter = root / "meter"
            meter.mkdir()
            ledger = meter / "grok-official-identity-runs.json"
            ledger.write_text(
                json.dumps(
                    [{"at": "2026-08-01T00:00:00+00:00", "account_id": "grok-a"}]
                ),
                encoding="utf-8",
            )
            real_read = accounts._read_owned_json
            reads = 0

            def fail_once(path):
                nonlocal reads
                if path == ledger:
                    reads += 1
                    if reads == 1:
                        return accounts._OWNED_JSON_INVALID
                return real_read(path)

            with mock.patch.object(accounts, "meter_home", lambda: meter), mock.patch.object(
                accounts, "grok_home", lambda: root / "grok"
            ), mock.patch.object(accounts, "_read_owned_json", fail_once):
                accounts._GROK_RUNS = accounts._GROK_RUNS_SRC = None
                self.assertEqual(accounts.grok_identity_runs(), [])
                self.assertIsNone(accounts._GROK_RUNS)
                self.assertEqual(accounts.grok_identity_runs()[-1][1], "grok-a")
                self.assertEqual(reads, 2)
        accounts._GROK_RUNS = accounts._GROK_RUNS_SRC = None

    def test_legacy_mixed_grok_identity_ledger_is_not_migrated(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            meter = Path(tmp)
            (meter / "grok-identity-runs.json").write_text(
                json.dumps([{"at": "2026-08-01T00:00:00Z", "account_id": "community-app"}]),
                encoding="utf-8",
            )
            with mock.patch.object(accounts, "meter_home", lambda: meter), mock.patch.object(
                accounts, "grok_home", lambda: meter / "grok"
            ):
                accounts._GROK_RUNS = accounts._GROK_RUNS_SRC = None
                self.assertEqual(accounts.grok_identity_runs(), [])
                self.assertFalse((meter / "grok-official-identity-runs.json").exists())
        accounts._GROK_RUNS = accounts._GROK_RUNS_SRC = None

    def test_append_run_merges_newer_disk_rows_before_writing(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "codex-identity-runs.json"
            path.write_text(
                json.dumps(
                    [
                        {"at": "2026-08-01T00:00:00+00:00", "to": "old@example.com"},
                        {"at": "2026-08-02T00:00:00+00:00", "to": "middle@example.com"},
                    ],
                    indent=2,
                ),
                encoding="utf-8",
            )
            changed = accounts._append_run(
                path,
                "to",
                "2026-08-03T00:00:00+00:00",
                "new@example.com",
                [("2026-08-01T00:00:00+00:00", "old@example.com")],
            )
            rows = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(changed)
        self.assertEqual(
            [row["to"] for row in rows],
            ["old@example.com", "middle@example.com", "new@example.com"],
        )

    def test_append_run_does_not_resurrect_stale_process_rows(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "codex-official-identity-runs.json"
            disk_rows = [
                {"at": "2026-08-02T00:00:00+00:00", "to": "disk-account"}
            ]
            path.write_text(json.dumps(disk_rows), encoding="utf-8")
            stale_process_rows = [
                ("2026-08-01T00:00:00+00:00", "stale-account"),
                ("2026-08-02T00:00:00+00:00", "disk-account"),
            ]

            changed = accounts._append_run(
                path,
                "to",
                "2026-08-03T00:00:00+00:00",
                "new-account",
                stale_process_rows,
            )
            rows = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(changed)
        self.assertEqual(
            [row["to"] for row in rows], ["disk-account", "new-account"]
        )

    def test_append_run_records_nonidentifying_writer_evidence(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "codex-official-identity-runs.json"
            self.assertTrue(
                accounts._append_run(
                    path,
                    "to",
                    "2026-08-03T00:00:00+00:00",
                    "private-account-id",
                    [],
                )
            )
            evidence = accounts.identity_write_evidence(path)
            encoded = json.dumps(evidence)
            file_sha = hashlib.sha256(path.read_bytes()).hexdigest()

        self.assertNotIn("private-account-id", encoded)
        self.assertEqual(evidence["process_id"], os.getpid())
        self.assertEqual(evidence["file_sha256"], file_sha)

    def test_identity_lock_blocks_another_process_writer(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "codex-official-identity-runs.json"
            code = (
                "import sys; from pathlib import Path; "
                "from sandglass.accounts import _append_run; "
                "ok=_append_run(Path(sys.argv[1]),'to',"
                "'2026-08-03T00:00:00+00:00','child-account',[]); "
                "raise SystemExit(0 if ok else 2)"
            )
            process = None
            with accounts.state_file_lock(path):
                process = subprocess.Popen(
                    [sys.executable, "-c", code, str(path)],
                    cwd=Path(__file__).resolve().parents[1],
                )
                time.sleep(0.25)
                self.assertIsNone(process.poll())
            self.assertEqual(process.wait(timeout=10), 0)
            rows = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual([row["to"] for row in rows], ["child-account"])

    def test_identity_lock_waits_out_a_holder_that_keeps_it_for_a_while(self):
        """A quarter-second hold never reaches the cliff the old lock had.

        msvcrt.locking(LK_LOCK) is not a blocking lock: the CRT retries once a
        second and raises OSError(36, "Resource deadlock avoided") on the tenth
        failure. Measured on this machine, a thirteen-second holder made the
        next acquirer fail at 9.07 -- so any hold past nine seconds turned every
        other writer's lock into an exception nobody catches, while the test
        above went on passing.
        """
        from sandglass import accounts

        hold = 11.0
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "codex-official-identity-runs.json"
            code = (
                "import sys; from pathlib import Path; "
                "from sandglass.accounts import _append_run; "
                "ok=_append_run(Path(sys.argv[1]),'to',"
                "'2026-08-03T00:00:00+00:00','patient-child',[]); "
                "raise SystemExit(0 if ok else 2)"
            )
            started = time.monotonic()
            with accounts.state_file_lock(path):
                process = subprocess.Popen(
                    [sys.executable, "-c", code, str(path)],
                    cwd=Path(__file__).resolve().parents[1],
                )
                while time.monotonic() - started < hold:
                    time.sleep(0.25)
                    self.assertIsNone(process.poll(), "子进程在锁还被持有时就退出了")
            self.assertEqual(process.wait(timeout=30), 0, "等锁的写入者被拒绝了")
            waited = time.monotonic() - started
            rows = json.loads(path.read_text(encoding="utf-8"))

        self.assertGreater(waited, 9.5, "没有真正跨过旧实现放弃的那一刻")
        self.assertEqual([row["to"] for row in rows], ["patient-child"])

    def test_a_switch_written_during_the_read_is_not_cached_away(self):
        """These ledgers have a second writer, and it is not this process.

        note_current_identity() appends a switch from the observer while the
        panel is reading here. The run list was stamped with a source snapshot
        taken *after* the read, so the rows from before that append got stored
        under the appended file's identity: every later call was a cache hit,
        the switch never arrived, and every minute after it kept being credited
        to the account that came before -- for as long as the ledger stayed
        untouched.
        """
        from sandglass import accounts

        cases = [
            ("claude-official-identity-runs.json", "account_id",
             accounts.claude_identity_runs, "_CLAUDE_RUNS"),
        ]
        for name, key, runs_for, cache_name in cases:
            with self.subTest(ledger=name), tempfile.TemporaryDirectory() as tmp:
                meter = Path(tmp)
                ledger = meter / name
                before = [{"at": "2026-08-01T00:00:00+00:00", key: "account-A"}]
                after = before + [{"at": "2026-08-02T00:00:00+00:00", key: "account-B"}]
                ledger.write_text(json.dumps(before), encoding="utf-8")

                real_read = accounts._read_identity_rows

                def read_then_a_writer_lands(path, row_key, _ledger=ledger, _after=after):
                    rows = real_read(path, row_key)
                    if path == _ledger:
                        _ledger.write_text(json.dumps(_after), encoding="utf-8")
                    return rows

                empty = meter / "no-such-home"
                with mock.patch.object(accounts, "meter_home", lambda: meter),                      mock.patch.object(accounts, "codex_home", lambda: empty),                      mock.patch.object(accounts, "claude_homes", lambda: [empty]):
                    setattr(accounts, cache_name, None)
                    setattr(accounts, cache_name + "_SRC", None)
                    with mock.patch.object(accounts, "_read_identity_rows",
                                           read_then_a_writer_lands):
                        runs_for()
                    self.assertEqual(
                        [who for _at, who in runs_for()],
                        ["account-A", "account-B"],
                        "读取期间落地的切号被缓存挡掉了",
                    )
                    setattr(accounts, cache_name, None)
                    setattr(accounts, cache_name + "_SRC", None)

    def test_an_unreadable_provider_becomes_a_gap(self):
        """Not knowing who is signed in really is a coverage gap."""
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp,              mock.patch.object(accounts, "meter_home", lambda: Path(tmp)),              mock.patch.object(accounts, "_codex_signed_in",
                               side_effect=OSError("auth.json unreadable")),              mock.patch.object(accounts, "note_codex_identity_gap") as gap:
            accounts.note_codex_identity("2026-09-01T00:00:00+00:00")

        gap.assert_called_once_with("2026-09-01T00:00:00+00:00",
                                    reason="identity_read_failed")

    def test_a_failure_of_our_own_is_reported_not_written_as_a_gap(self):
        """Being unable to record what we saw is not the same as not seeing it.

        One except used to cover both, and wrote the same coverage boundary for
        either. A permissions error or a redirected state home then took Codex
        attribution to zero while every check stayed green -- and the gap it
        wrote goes through the very path that just failed, so it usually does
        not land either.
        """
        from sandglass import accounts

        recorded = []
        with tempfile.TemporaryDirectory() as tmp,              mock.patch.object(accounts, "meter_home", lambda: Path(tmp)),              mock.patch.object(accounts, "_codex_signed_in",
                               return_value=("2026-09-01T00:00:00+00:00", "acct-a")),              mock.patch.object(accounts, "_official_codex_account", return_value=None),              mock.patch.object(accounts, "_append_codex_identity_event",
                               side_effect=OSError("state home is read-only")),              mock.patch.object(accounts, "note_codex_identity_gap") as gap,              mock.patch("sandglass.diagnostics.record_component_failure",
                        lambda name, exc: recorded.append(name)),              mock.patch("sandglass.diagnostics.clear_component_failure", lambda _n: None):
            with self.assertRaises(OSError):
                accounts.note_codex_identity("2026-09-01T00:00:00+00:00")

        gap.assert_not_called()
        self.assertEqual(recorded, ["codex_identity_write"],
                         "自己写不进去必须报出来,而不是写成一条覆盖边界")

    def test_a_claude_write_failure_is_reported_not_swallowed(self):
        """Codex split read from write. Claude's watcher path still had one except."""
        from sandglass import accounts

        recorded = []

        def boom(path, *args, **kwargs):
            if path == accounts._claude_runs_path():
                raise OSError("state home is read-only")
            return False

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            accounts, "meter_home", lambda: Path(tmp)
        ), mock.patch.object(
            accounts, "_claude_signed_in",
            return_value=("2026-09-01T00:00:00+00:00", "claude-a", None),
        ), mock.patch.object(
            accounts, "_append_run", boom
        ), mock.patch(
            "sandglass.diagnostics.record_component_failure",
            lambda name, exc: recorded.append(name),
        ), mock.patch(
            "sandglass.diagnostics.clear_component_failure", lambda _n: None
        ):
            with self.assertRaises(OSError):
                accounts.note_current_identity(record_codex_attribution=False)

        self.assertEqual(recorded, ["claude_identity_write"])

    def test_a_grok_write_failure_is_reported_not_swallowed(self):
        from sandglass import accounts

        recorded = []

        def boom(path, *args, **kwargs):
            if path == accounts._grok_runs_path():
                raise OSError("state home is read-only")
            return False

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            accounts, "meter_home", lambda: Path(tmp)
        ), mock.patch.object(
            accounts, "_claude_signed_in", return_value=("", "", None)
        ), mock.patch.object(
            accounts, "_grok_signed_in",
            return_value=("2026-09-01T00:00:00+00:00", "grok-a"),
        ), mock.patch.object(
            accounts, "_append_run", boom
        ), mock.patch(
            "sandglass.diagnostics.record_component_failure",
            lambda name, exc: recorded.append(name),
        ), mock.patch(
            "sandglass.diagnostics.clear_component_failure", lambda _n: None
        ):
            with self.assertRaises(OSError):
                accounts.note_current_identity(record_codex_attribution=False)

        self.assertEqual(recorded, ["grok_identity_write"])

    def test_a_write_that_failed_is_not_reported_as_nothing_to_write(self):
        """The reporting above this could not see the write fail.

        `_write_json_if_changed` returned False both for "the content already
        matched" and for "the write raised", and every caller reads False as
        the first. So with the state directory present and lockable but the
        write itself failing -- a full disk, or this one file locked -- every
        identity ledger stopped being written, `note_current_identity`
        returned False, and nothing was recorded anywhere. Measured before the
        fix: returned False, recorded [], ledger absent.

        The earlier tests here patch `_append_run` to raise, so they pass
        either way. This one fails at the real writer.
        """
        from sandglass import accounts

        def no_space(*args, **kwargs):
            raise OSError(28, "No space left on device")

        recorded = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            accounts, "meter_home", lambda: Path(tmp)
        ), mock.patch.object(
            accounts, "_claude_signed_in",
            return_value=("2026-09-01T00:00:00+00:00", "claude-a", None),
        ), mock.patch.object(
            accounts, "_grok_signed_in", return_value=("", "")
        ), mock.patch.object(
            accounts.tempfile, "NamedTemporaryFile", no_space
        ), mock.patch(
            "sandglass.diagnostics.record_component_failure",
            lambda name, exc: recorded.append(name),
        ), mock.patch(
            "sandglass.diagnostics.clear_component_failure", lambda _n: None
        ):
            with self.assertRaises(OSError):
                accounts.note_current_identity(record_codex_attribution=False)
            self.assertFalse(
                (Path(tmp) / "claude-official-identity-runs.json").exists()
            )

        self.assertIn("claude_identity_write", recorded)

    def test_a_read_that_merges_keeps_answering_when_it_cannot_persist(self):
        """grok_identity_runs() merges the vendor's rotating log and saves it.

        That save is inside a read the panel makes. Raising there would take a
        panel request down over a ledger it did not need saved to answer, so
        this one call site records the failure and carries on with the merge it
        already has in memory.
        """
        from sandglass import accounts

        def no_space(*args, **kwargs):
            raise OSError(28, "No space left on device")

        recorded = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            accounts, "meter_home", lambda: Path(tmp)
        ), mock.patch.object(
            accounts.tempfile, "NamedTemporaryFile", no_space
        ), mock.patch(
            "sandglass.diagnostics.record_component_failure",
            lambda name, exc: recorded.append(name),
        ):
            (Path(tmp) / "grok-official-identity-runs.json").write_text(
                "[]", encoding="utf-8"
            )
            accounts._GROK_RUNS = accounts._GROK_RUNS_SRC = None
            rows = accounts.grok_identity_runs()
            accounts._GROK_RUNS = accounts._GROK_RUNS_SRC = None

        self.assertEqual(recorded, ["grok_identity_merge_write"])
        self.assertIsInstance(rows, list)

    def test_corrupt_identity_ledger_is_never_replaced_by_a_new_run(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "codex-official-identity-runs.json"
            damaged = b'[{"at":"2026-08-01T00:00:00Z","to":"old"}'
            path.write_bytes(damaged)

            changed = accounts._append_run(
                path,
                "to",
                "2026-08-03T00:00:00+00:00",
                "new@example.com",
                [],
            )

            self.assertFalse(changed)
            self.assertEqual(path.read_bytes(), damaged)

    def test_wrong_identity_ledger_shape_is_never_normalized_destructively(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "grok-official-identity-runs.json"
            damaged = b'[{"at":"not-a-time","account_id":"old"}]'
            path.write_bytes(damaged)

            changed = accounts._append_run(
                path,
                "account_id",
                "2026-08-03T00:00:00+00:00",
                "new",
                [],
            )

            self.assertFalse(changed)
            self.assertEqual(path.read_bytes(), damaged)

    def test_corrupt_observed_account_ledger_is_never_replaced(self):
        from sandglass import accounts
        from sandglass.models import Account

        with tempfile.TemporaryDirectory() as tmp:
            meter = Path(tmp)
            path = meter / "codex-observed-accounts.json"
            damaged = b"not-json"
            path.write_bytes(damaged)
            account = Account(
                provider="codex",
                account_id="account-a",
                label="account-a",
                email="a@example.com",
            )

            with mock.patch.object(accounts, "meter_home", lambda: meter):
                changed = accounts._remember_codex_account(
                    account, "2026-08-03T00:00:00+00:00"
                )

            self.assertFalse(changed)
            self.assertEqual(path.read_bytes(), damaged)


class CodexIdentityEventTests(unittest.TestCase):
    def setUp(self):
        from sandglass import accounts

        accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None

    def tearDown(self):
        from sandglass import accounts

        accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None

    def test_observed_gap_observed_projection_clears_owner_and_conserves_usage(self):
        from sandglass import accounts
        from sandglass.attribution import sessions_for_account, sessions_unclaimed_by_accounts

        with tempfile.TemporaryDirectory() as tmp:
            meter = Path(tmp)
            events_path = meter / "codex-official-identity-events-v2.json"
            events_path.write_text(
                json.dumps(
                    {
                        "schema": 2,
                        "events": [
                            {"at": "2026-08-01T00:00:00Z", "kind": "observed", "account_id": "account-a"},
                            {"at": "2026-08-01T00:01:00Z", "kind": "unassigned", "reason": "observer_coverage_gap"},
                            {"at": "2026-08-01T00:02:00Z", "kind": "observed", "account_id": "account-b"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            session = SessionRecord(
                provider="codex",
                session_id="gap",
                path="gap.jsonl",
                timeline=[
                    ("2026-08-01T00:00:00Z", TokenUsage(input_tokens=10)),
                    ("2026-08-01T00:01:00Z", TokenUsage(input_tokens=20)),
                    ("2026-08-01T00:02:00Z", TokenUsage(input_tokens=30)),
                ],
            )
            account_a = Account(provider="codex", account_id="account-a")
            account_b = Account(provider="codex", account_id="account-b")
            with mock.patch.object(accounts, "meter_home", lambda: meter):
                runs = accounts.codex_identity_runs()
                self.assertEqual([owner for _at, owner in runs], ["account-a", "", "account-b"])
                self.assertEqual(
                    accounts._codex_owner_at(runs, parse_ts("2026-08-01T00:01:00Z")), ""
                )
                owned_a = sessions_for_account(account_a, [account_a, account_b], [session], runs=runs)
                owned_b = sessions_for_account(account_b, [account_a, account_b], [session], runs=runs)
                unknown = sessions_unclaimed_by_accounts(
                    "codex", [account_a, account_b], [session], runs=runs
                )

        self.assertEqual(owned_a[0].usage.total_tokens, 10)
        self.assertEqual(owned_b[0].usage.total_tokens, 30)
        self.assertEqual(unknown[0].usage.total_tokens, 20)
        self.assertEqual(
            owned_a[0].usage.total_tokens
            + owned_b[0].usage.total_tokens
            + unknown[0].usage.total_tokens,
            60,
        )

    def test_same_state_refresh_is_deduplicated_and_cached_reason_survives(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            meter = Path(tmp)
            with mock.patch.object(accounts, "meter_home", lambda: meter):
                self.assertTrue(
                    accounts._append_codex_identity_event(
                        "2026-08-01T00:00:00Z", account_id="account-a"
                    )
                )
                self.assertFalse(
                    accounts._append_codex_identity_event(
                        "2026-08-01T00:01:00Z", account_id="account-a"
                    )
                )
                self.assertTrue(
                    accounts.note_codex_identity_gap(
                        "2026-08-01T00:02:00Z", reason="observer_stopped"
                    )
                )
                self.assertFalse(
                    accounts.note_codex_identity_gap(
                        "2026-08-01T00:03:00Z", reason="different_reason"
                    )
                )
                self.assertTrue(
                    accounts._append_codex_identity_event(
                        "2026-08-01T00:04:00Z", account_id="account-a"
                    )
                )
                first = accounts.codex_identity_events()
                second = accounts.codex_identity_events()

        self.assertEqual(len(first), 3)
        self.assertEqual(first, second)
        self.assertEqual(first[1]["reason"], "observer_stopped")
        self.assertEqual(first[-1]["account_id"], "account-a")

    def test_event_validation_and_malformed_state_fail_closed_without_mutation(self):
        from sandglass import accounts

        invalid = [
            {"at": "2026-08-01T00:00:00", "kind": "observed", "account_id": "a"},
            {"at": "2026-08-01T00:00:00+08:00", "kind": "observed", "account_id": "a"},
            {"at": "2026-08-01T00:00:00Z", "kind": "unknown", "account_id": "a"},
            {"at": "2026-08-01T00:00:00Z", "kind": "observed"},
            {"at": "2026-08-01T00:00:00Z", "kind": "unassigned"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            meter = Path(tmp)
            path = meter / "codex-official-identity-events-v2.json"
            with mock.patch.object(accounts, "meter_home", lambda: meter):
                for event in invalid:
                    self.assertFalse(accounts._append_codex_identity_event(**{
                        "at": event["at"],
                        "kind": event["kind"],
                        "account_id": event.get("account_id", ""),
                        "reason": event.get("reason", ""),
                    }))
                damaged = b'{"schema":2,"events":[{"at":"2026-08-01T00:00:00Z","kind":"unassigned"}]}'
                path.write_bytes(damaged)
                self.assertEqual(accounts.codex_identity_events(), [])
                self.assertEqual(accounts.codex_identity_runs(), [])
                self.assertFalse(
                    accounts._append_codex_identity_event(
                        "2026-08-01T00:01:00Z", account_id="account-a"
                    )
                )
            self.assertEqual(path.read_bytes(), damaged)

    def test_v2_event_validator_rejects_extra_top_level_keys(self):
        from sandglass import accounts

        self.assertFalse(
            accounts._valid_codex_identity_events(
                {"schema": 2, "events": [], "extra": "reject"}
            )
        )

    def test_malformed_v2_state_adds_stable_in_memory_unassigned_boundary(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            meter = Path(tmp)
            path = meter / "codex-official-identity-events-v2.json"
            valid = {
                "schema": 2,
                "events": [{
                    "at": "2026-08-01T00:00:00Z",
                    "kind": "observed",
                    "account_id": "old-account",
                }],
            }
            path.write_text(json.dumps(valid), encoding="utf-8")
            with mock.patch.object(accounts, "meter_home", lambda: meter):
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                accounts._CODEX_MALFORMED_CUTOFF = None
                self.addCleanup(
                    lambda: setattr(accounts, "_CODEX_RUNS", None)
                )
                self.addCleanup(
                    lambda: setattr(accounts, "_CODEX_RUNS_SRC", None)
                )
                self.addCleanup(
                    lambda: setattr(accounts, "_CODEX_MALFORMED_CUTOFF", None)
                )
                self.assertEqual(accounts.codex_identity_events(), valid["events"])

                damaged = b'{"schema":2,"events":[],'
                path.write_bytes(damaged)
                first = accounts.codex_identity_events()
                second = accounts.codex_identity_events()
                cutoff = first[-1]["at"]

                self.assertEqual(first, second)
                self.assertEqual(first[-1]["kind"], "unassigned")
                self.assertEqual(first[-1]["reason"], "identity_ledger_invalid")
                self.assertEqual(
                    accounts._codex_owner_at(
                        accounts.codex_identity_runs(), parse_ts("2026-08-01T00:00:01Z")
                    ),
                    "old-account",
                )
                self.assertEqual(
                    accounts._codex_owner_at(
                        accounts.codex_identity_runs(),
                        parse_ts(cutoff) + timedelta(microseconds=1),
                    ),
                    "",
                )
                self.assertGreater(cutoff, "2026-08-01T00:00:00+00:00")
                self.assertEqual(path.read_bytes(), damaged)

                replacement = {
                    "schema": 2,
                    "events": [
                        valid["events"][0],
                        {
                            "at": "2026-08-02T00:00:00Z",
                            "kind": "observed",
                            "account_id": "new-account",
                        },
                    ],
                }
                path.write_text(json.dumps(replacement), encoding="utf-8")
                restored = accounts.codex_identity_events()

        self.assertEqual(restored, replacement["events"])
        self.assertEqual(restored[-1]["account_id"], "new-account")

    def test_missing_or_empty_v2_baseline_clears_malformed_cutoff(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            meter = Path(tmp)
            path = meter / "codex-official-identity-events-v2.json"
            with mock.patch.object(accounts, "meter_home", lambda: meter):
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                accounts._CODEX_MALFORMED_CUTOFF = "2099-01-01T00:00:00+00:00"
                self.assertEqual(accounts.codex_identity_events(), [])
                self.assertIsNone(accounts._CODEX_MALFORMED_CUTOFF)

                path.write_text(json.dumps({"schema": 2, "events": []}), encoding="utf-8")
                accounts._CODEX_MALFORMED_CUTOFF = "2099-01-01T00:00:00+00:00"
                self.assertEqual(accounts.codex_identity_events(), [])
                self.assertIsNone(accounts._CODEX_MALFORMED_CUTOFF)

    def test_legacy_v1_is_retained_as_roster_only_and_never_owns_usage(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            meter = Path(tmp)
            legacy = meter / "codex-official-identity-runs.json"
            original = b'[{"at":"2026-08-01T00:00:00Z","to":"legacy-account"}]'
            legacy.write_bytes(original)
            with mock.patch.object(accounts, "meter_home", lambda: meter), mock.patch.object(
                accounts, "codex_home", lambda: meter / "codex"
            ):
                peers = accounts.load_codex_accounts()
                self.assertEqual(accounts.codex_identity_runs(), [])
                self.assertEqual(
                    accounts._codex_owner_at(
                        accounts.codex_identity_runs(), parse_ts("2026-08-01T01:00:00Z")
                    ),
                    "",
                )
            self.assertEqual(legacy.read_bytes(), original)
        self.assertIn("legacy-account", {peer.account_id for peer in peers})

    def test_legacy_v1_roster_merges_into_observed_metadata_without_ownership(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            meter = Path(tmp)
            legacy = meter / "codex-official-identity-runs.json"
            original = b'[{"at":"2026-08-01T00:00:00Z","to":"same-account"}]'
            legacy.write_bytes(original)
            (meter / "codex-observed-accounts.json").write_text(
                json.dumps([{
                    "account_id": "same-account",
                    "email": "same@example.com",
                    "plan": "prolite",
                    "auth_mode": "oauth",
                    "first_observed_at": "2026-08-02T00:00:00Z",
                    "last_observed_at": "2026-08-02T00:00:00Z",
                }]),
                encoding="utf-8",
            )
            with mock.patch.object(accounts, "meter_home", lambda: meter), mock.patch.object(
                accounts, "codex_home", lambda: meter / "codex"
            ):
                peers = accounts.load_codex_accounts()
                self.assertEqual(accounts.codex_identity_runs(), [])

            self.assertEqual(len(peers), 1)
            peer = peers[0]
            self.assertEqual(peer.account_id, "same-account")
            self.assertEqual(peer.email, "same@example.com")
            self.assertEqual(peer.label, "sa***@example.com · same-acc")
            self.assertEqual(peer.plan, "pro 5x")
            self.assertEqual(peer.auth_mode, "oauth")
            self.assertFalse(peer.active)
            self.assertEqual(
                peer.extra["account_source"], "official_identity_ledger_roster"
            )
            self.assertEqual(
                peer.extra["account_sources"],
                ["official_identity_ledger_roster", "official_observation"],
            )
            self.assertEqual(legacy.read_bytes(), original)

    def test_malformed_legacy_v1_cannot_create_roster_accounts(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            meter = Path(tmp)
            legacy = meter / "codex-official-identity-runs.json"
            legacy.write_text(
                json.dumps(
                    [
                        {"at": "2026-08-01T00:00:00Z", "to": "legacy-account"},
                        {"at": "not-a-time", "to": "partial-account"},
                    ]
                ),
                encoding="utf-8",
            )
            with mock.patch.object(accounts, "meter_home", lambda: meter), mock.patch.object(
                accounts, "codex_home", lambda: meter / "codex"
            ):
                peers = accounts.load_codex_accounts()

        self.assertEqual(peers, [])


class GrokSwitchStartTests(unittest.TestCase):
    """A request bills whoever the CLI holds credentials for, so the CLI decides."""

    def test_the_cli_decides_inside_its_own_coverage(self):
        """The app's switch line marks the click; its flow took 1.5 minutes here."""
        from sandglass.accounts import _merge_runs

        other = {"2026-08-28T05:02:03+00:00": "new"}          # app: started switching
        cli = {
            "2026-08-28T04:00:00+00:00": "old",               # CLI was already watching
            "2026-08-28T05:03:45+00:00": "new",               # CLI: now signed in
        }
        self.assertEqual(
            _merge_runs(other, cli),
            [("2026-08-28T04:00:00+00:00", "old"),
             ("2026-08-28T05:03:45+00:00", "new")],
        )

    def test_switching_and_not_using_it_starts_no_run(self):
        from sandglass.accounts import _merge_runs

        other = {"2026-08-28T05:02:03+00:00": "idle"}
        cli = {"2026-08-28T04:00:00+00:00": "busy"}
        # The app switched at 05:02 but the CLI never signed in as that account,
        # so nothing billed it and no run begins.
        self.assertEqual(_merge_runs(other, cli), [("2026-08-28T04:00:00+00:00", "busy")])

    def test_two_switches_in_a_row_are_just_two_changes(self):
        from sandglass.accounts import _merge_runs

        cli = {
            "2026-08-28T05:00:00+00:00": "a",
            "2026-08-28T05:01:00+00:00": "b",
            "2026-08-28T05:02:00+00:00": "a",
        }
        self.assertEqual(
            _merge_runs({}, cli),
            [("2026-08-28T05:00:00+00:00", "a"),
             ("2026-08-28T05:01:00+00:00", "b"),
             ("2026-08-28T05:02:00+00:00", "a")],
        )

    def test_history_before_the_cli_log_survives(self):
        """The CLI log rotates; here it reaches back only to 08-24."""
        from sandglass.accounts import _merge_runs

        other = {
            "2026-08-23T01:46:47+00:00": "old",
            "2026-08-23T02:20:07+00:00": "new",
        }
        cli = {"2026-08-24T04:17:17+00:00": "new"}
        # The run started at 02:20 by the only evidence covering that time; the
        # CLI line merely repeats it, so it collapses away.
        self.assertEqual(
            _merge_runs(other, cli),
            [("2026-08-23T01:46:47+00:00", "old"),
             ("2026-08-23T02:20:07+00:00", "new")],
        )

class CodexObservationTimeTests(unittest.TestCase):
    """The observation ledger dates identity only when Sandglass sees it."""

    def _home(self, tmp, activated_ms, account_id="acct-b"):
        root = Path(tmp)
        (root / "codex" / "accounts").mkdir(parents=True, exist_ok=True)
        (root / "codex" / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": "t", "account_id": account_id}}),
            encoding="utf-8")
        if activated_ms is not None:
            when = activated_ms / 1000.0
            os.utime(root / "codex" / "auth.json", (when, when))
        return {"CODEX_HOME": str(root / "codex"),
                "SANDGLASS_HOME": str(root / "sandglass")}

    def _signed_in(self, env, existing=None):
        from sandglass import accounts

        with mock.patch.dict(os.environ, env), mock.patch.object(
            accounts, "codex_identity_runs", lambda: existing or []
        ):
            return accounts._codex_signed_in()

    def test_auth_mtime_is_ignored_for_ownership(self):
        earlier = datetime.now(timezone.utc) - timedelta(hours=2)
        with tempfile.TemporaryDirectory() as tmp:
            env = self._home(tmp, int(earlier.timestamp() * 1000))
            before = datetime.now(timezone.utc)
            at, who = self._signed_in(env)
        self.assertEqual(who, "acct-b")
        self.assertGreaterEqual(datetime.fromisoformat(at), before)

    def test_a_file_written_just_now_reads_as_just_now(self):
        """The counterpart: an mtime that is current must not look historical."""
        with tempfile.TemporaryDirectory() as tmp:
            env = self._home(tmp, None)
            at, _ = self._signed_in(env)
        self.assertLess(
            abs((datetime.fromisoformat(at) - datetime.now(timezone.utc)).total_seconds()), 5)

    def test_previous_owner_does_not_date_new_observation(self):
        """A prior owner projection cannot be carried into a new observation."""
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            env = self._home(tmp, int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp() * 1000))
            before = datetime.now(timezone.utc)
            with mock.patch.dict(os.environ, env), mock.patch.object(
                accounts, "codex_identity_runs", side_effect=AssertionError("legacy owner consulted")
            ):
                at, who = accounts._codex_signed_in()
        self.assertEqual(who, "acct-b")
        self.assertGreaterEqual(datetime.fromisoformat(at), before)

    def test_a_future_activation_time_is_refused(self):
        ahead = datetime.now(timezone.utc) + timedelta(days=1)
        with tempfile.TemporaryDirectory() as tmp:
            env = self._home(tmp, int(ahead.timestamp() * 1000))
            at, _ = self._signed_in(env)
        self.assertLess(datetime.fromisoformat(at), ahead)

