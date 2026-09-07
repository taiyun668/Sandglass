import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

from sandglass.models import Account, SessionRecord, TokenUsage, parse_ts
from sandglass.pace import annotate_window, annotate_windows, five_hour_blocks
from unittest.mock import patch

from sandglass.accounts import _codex_plan_name
from sandglass.quota import (
    CACHE_TTL_SECONDS_BY_PROVIDER,
    _send,
    attach_cached_quota,
    attach_live_quota,
    fetch_claude_quota,
    fetch_codex_quota,
    fetch_grok_quota_from_auth,
    _grok_plan,
    _grok_plan_name,
    next_quota_fetch_at,
    parse_claude_usage,
    parse_codex_wham,
    parse_grok_billing,
)


class _FixedDatetime(datetime):
    current = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        value = cls.current
        return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)


class UserAdapterQuotaTests(unittest.TestCase):
    def test_failed_builtin_fetch_marks_old_adapter_quota_stale(self):
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        account = Account(
            provider="claude",
            account_id="account-1",
            active=True,
            extra={
                "account_source": "official_claude_config",
                "account_source_official": True,
                "quota_source": "user_adapter:owner-ledger",
                "quota_fetched_at": old,
                "windows": [
                    {
                        "label": "5h",
                        "used_percent": 5,
                        "resets_at": old,
                        "window_minutes": 300,
                    }
                ],
            },
        )
        failure = {
            "claude": {
                "ok": False,
                "provider": "claude",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "error": "Rate limited (HTTP 429)",
            }
        }
        with patch("sandglass.quota.fetch_all_quotas", return_value=failure):
            result = attach_live_quota([account])

        self.assertEqual(result[0].extra["quota_source"], "stale")
        self.assertEqual(
            result[0].extra["quota_previous_source"],
            "user_adapter:owner-ledger",
        )
        self.assertIn("429", result[0].extra["quota_error"])

    @patch("sandglass.quota.note_quota_windows")
    @patch("sandglass.quota.fetch_all_quotas", return_value={})
    def test_targeted_live_attachment_forces_only_due_providers(
        self, fetch, _note
    ):
        attach_live_quota(
            [Account(provider="codex", account_id="account-1")],
            force=True,
            providers={"codex"},
        )

        fetch.assert_called_once_with(force=True, providers={"codex"})

    def test_old_cached_quota_is_marked_stale_for_display(self):
        account = Account(provider="codex", account_id="account-1", active=True)
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        snapshot = {
            "codex": {
                "ok": True,
                "account_id": "account-1",
                "fetched_at": old,
                "windows": [{"label": "5h", "used_percent": 0}],
            }
        }
        with patch("sandglass.quota._read_cache", return_value=snapshot):
            result = attach_cached_quota([account])

        self.assertEqual(result[0].extra["quota_source"], "stale")

    @patch("sandglass.quota.note_quota_windows")
    @patch("sandglass.quota.fetch_all_quotas", side_effect=AssertionError("must not fetch"))
    @patch(
        "sandglass.quota._read_cache",
        return_value={
            "codex": {
                "ok": True,
                "account_id": "account-1",
                "windows": [{"label": "7d", "used_percent": 40}],
            }
        },
    )
    def test_cached_attachment_never_fetches_or_records_an_observation(
        self, _cache, _fetch, note_windows
    ):
        account = Account(provider="codex", account_id="account-1", active=True)

        result = attach_cached_quota([account])

        self.assertEqual(result[0].extra["windows"][0]["used_percent"], 40)
        note_windows.assert_not_called()

    def setUp(self):
        """Isolate the state directory before anything can record into it.

        attach_live_quota records observations by default, which is right in
        production and means an unisolated test writes a fictional account into
        the real quota-observations.json. One of these left claude:adapter-account
        sitting in a real ledger until an audit noticed it.
        """
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        patcher = patch.dict(os.environ, {"SANDGLASS_HOME": self._home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_adapter_quota_is_kept_when_builtin_snapshot_has_no_matching_account(self):
        account = Account(
            provider="claude",
            account_id="adapter-account",
            extra={
                "account_source": "user_adapter:identity.timeline",
                "account_source_official": False,
                "quota_source": "user_adapter:identity.timeline",
                "windows": [{"label": "7d", "used_percent": 25}],
            },
        )
        with patch(
            "sandglass.quota.fetch_all_quotas",
            return_value={
                "claude": {
                    "ok": True,
                    "windows": [{"label": "7d", "used_percent": 90}],
                }
            },
        ):
            result = attach_live_quota([account])

        self.assertEqual(result[0].extra["windows"][0]["used_percent"], 25)
        self.assertEqual(
            result[0].extra["quota_source"], "user_adapter:identity.timeline"
        )

    def test_matching_account_keyed_official_snapshot_can_refresh_adapter_account(self):
        account = Account(
            provider="codex",
            account_id="adapter-account",
            extra={
                "account_source": "user_adapter:identity.timeline",
                "account_source_official": False,
                "quota_source": "user_adapter:identity.timeline",
                "windows": [{"label": "7d", "used_percent": 25}],
            },
        )
        with patch(
            "sandglass.quota.fetch_all_quotas",
            return_value={
                "codex": {
                    "ok": True,
                    "account_id": "adapter-account",
                    "windows": [{"label": "7d", "used_percent": 40}],
                }
            },
        ):
            result = attach_live_quota([account])

        self.assertEqual(result[0].extra["windows"][0]["used_percent"], 40)
        self.assertEqual(result[0].extra["quota_source"], "live")


class QuotaParseTests(unittest.TestCase):
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


    def test_forced_refresh_keeps_last_successful_snapshot_on_failure(self):
        from sandglass import quota

        fetched_at = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        cached = {
            "claude": {
                "ok": True,
                "provider": "claude",
                "fetched_at": fetched_at,
                "windows": [{"label": "5h", "used_percent": 97}],
            }
        }
        failure = {
            "ok": False,
            "provider": "claude",
            "error": "Rate limited (HTTP 429)",
        }
        with patch.object(quota, "_read_cache", return_value=cached), patch.object(
            quota, "_write_cache"
        ) as write, patch.object(quota, "fetch_claude_quota", return_value=failure):
            result = quota.fetch_all_quotas(force=True, providers={"claude"})

        self.assertTrue(result["claude"]["stale"])
        self.assertEqual(result["claude"]["windows"][0]["used_percent"], 97)
        self.assertEqual(result["claude"]["fetched_at"], fetched_at)
        # The point of the field: the reading is as old as it ever was, and
        # the attempt that failed to replace it is recent. Asserting only that
        # the key exists would pass with it copied from fetched_at, which is
        # the one value it must not be.
        self.assertGreater(result["claude"]["last_attempt_at"], fetched_at)
        write.assert_called_once()

    def test_an_unreadable_cache_is_not_replaced_with_one_provider(self):
        """A failed read is not an empty quota cache.

        A targeted refresh re-reads the cache to merge. `_read_json` treated
        every OSError as empty, so a locked quota-cache.json became `{}` and
        the merge wrote only the provider that had just been fetched. The
        other providers' snapshots were gone until they were fetched again.
        """
        from sandglass import quota

        path = Path(os.environ["SANDGLASS_HOME"]) / "quota-cache.json"
        original = json.dumps({
            "claude": {"ok": True, "provider": "claude", "windows": [{"label": "5h"}]},
            "grok": {"ok": True, "provider": "grok", "windows": [{"label": "7d"}]},
        })
        path.write_text(original, encoding="utf-8")
        reported = []
        real_read = Path.read_text

        def read_text(self, *args, **kwargs):
            if self.name == "quota-cache.json":
                raise PermissionError("quota-cache.json is locked")
            return real_read(self, *args, **kwargs)

        fresh = {"ok": True, "provider": "codex", "windows": [{"label": "5h"}]}
        with patch.object(Path, "read_text", read_text), \
                patch("sandglass.diagnostics.record_component_failure",
                      lambda name, exc: reported.append(name)), \
                patch.object(quota, "fetch_codex_quota", return_value=fresh):
            result = quota.fetch_all_quotas(force=True, providers={"codex"})

        self.assertEqual(result["codex"]["provider"], "codex")
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(set(reported), {"quota_cache_write"})

    def test_display_staleness_is_derived_from_each_provider_poll_interval(self):
        """Pin the rule, not the numbers.

        These were a second hard-coded table beside the poll intervals, with no
        stated relation to them: change a provider's cadence and the staleness
        limit silently stopped matching it. The limit is two refresh cycles, so
        one missed poll is not staleness, floored at five minutes so a
        ninety-second provider does not flash stale on an ordinary hiccup.
        """
        from datetime import datetime, timedelta, timezone

        from sandglass import quota

        for provider, ttl in quota.CACHE_TTL_SECONDS_BY_PROVIDER.items():
            limit = max(2 * ttl, quota.DISPLAY_STALE_FLOOR_SECONDS)
            def aged(seconds):
                stamp = datetime.now(timezone.utc) - timedelta(seconds=seconds)
                return quota._snapshot_display_stale(provider, {"fetched_at": stamp.isoformat()})
            self.assertFalse(aged(limit - 5), f"{provider} 限内不应判过期")
            self.assertTrue(aged(limit + 5), f"{provider} 超限应判过期")
            self.assertGreaterEqual(limit, 2 * ttl, f"{provider} 少于两个刷新周期")
            self.assertGreaterEqual(limit, quota.DISPLAY_STALE_FLOOR_SECONDS)

    def test_http_429_is_a_rate_limit_state_without_echoing_provider_body(self):
        error = HTTPError(
            "https://provider.invalid/quota",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b'{"error":"provider-private-detail"}'),
        )
        with patch("sandglass.quota.urlopen", side_effect=error):
            body, message = _send(Request("https://provider.invalid/quota"))
        self.assertEqual(body, {})
        self.assertEqual(message, "Rate limited (HTTP 429)")
        self.assertNotIn("private", message)

    def test_http_401_is_an_authentication_state_but_403_is_not_mislabeled(self):
        for code, expected in (
            (401, "Authentication expired or rejected (HTTP 401)"),
            (403, "HTTP 403"),
        ):
            with self.subTest(code=code):
                error = HTTPError(
                    "https://provider.invalid/quota",
                    code,
                    "denied",
                    {},
                    io.BytesIO(b""),
                )
                with patch("sandglass.quota.urlopen", side_effect=error):
                    _, message = _send(Request("https://provider.invalid/quota"))
                self.assertEqual(message, expected)

    def test_account_quota_cache_uses_provider_cadence(self):
        from sandglass import quota

        self.assertEqual(
            quota.CACHE_TTL_SECONDS_BY_PROVIDER,
            {"claude": 180, "codex": 90, "grok": 90},
        )
        self.assertEqual(quota._cache_ttl_seconds("grok:secondary-account"), 90)

    def test_quota_cache_merge_takes_the_interprocess_state_lock(self):
        from sandglass import quota

        source = Path(quota.__file__).read_text(encoding="utf-8")
        fetch = source.split("def _fetch_all_quotas(", 1)[1].split("def refresh_quota_provider", 1)[0]
        self.assertIn("state_file_lock(_cache_path())", fetch)
        self.assertLess(
            fetch.index("with state_file_lock(_cache_path()):"),
            fetch.index("ThreadPoolExecutor"),
        )
        self.assertIn("latest.update(out)", fetch)

    def test_next_quota_fetch_at_is_fetched_at_plus_provider_ttl(self):
        fetched = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        stamp = fetched.isoformat()
        for provider, ttl in CACHE_TTL_SECONDS_BY_PROVIDER.items():
            due = next_quota_fetch_at(provider, stamp)
            self.assertEqual(parse_ts(due), fetched + timedelta(seconds=ttl), provider)
        self.assertEqual(next_quota_fetch_at("codex", ""), "")
        self.assertEqual(next_quota_fetch_at("unknown", stamp), "")

    def test_all_provider_results_share_cache_even_when_error_or_planless(self):
        from sandglass import quota

        failure = {"ok": False, "error": "rate limited", "scope": "account-wide"}
        grok_without_plan = {"ok": True, "provider": "grok", "windows": [], "scope": "account-wide"}
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            quota, "meter_home", return_value=Path(tmp)
        ), patch(
            "sandglass.accounts.grok_auth_sources", return_value=[]
        ), patch.object(
            quota, "fetch_claude_quota", return_value={**failure, "provider": "claude"}
        ) as claude, patch.object(
            quota, "fetch_codex_quota", return_value={**failure, "provider": "codex"}
        ) as codex, patch.object(
            quota, "fetch_grok_quota", return_value=grok_without_plan
        ) as grok:
            quota.fetch_all_quotas()
            quota.fetch_all_quotas()

        self.assertEqual(claude.call_count, 1)
        self.assertEqual(codex.call_count, 1)
        self.assertEqual(grok.call_count, 1)

    def test_claude_cache_survives_100_seconds_but_codex_and_grok_refresh(self):
        from sandglass import quota

        fetched_at = (datetime.now(timezone.utc) - timedelta(seconds=100)).isoformat()
        cached = {
            provider: {"ok": True, "provider": provider, "fetched_at": fetched_at}
            for provider in ("claude", "codex", "grok")
        }
        fresh = lambda provider: {"ok": True, "provider": provider, "windows": []}
        with patch.object(quota, "_read_cache", return_value=cached), patch.object(
            quota, "_write_cache"
        ), patch(
            "sandglass.accounts.grok_auth_sources", return_value=[]
        ), patch.object(
            quota, "fetch_claude_quota", return_value=fresh("claude")
        ) as claude, patch.object(
            quota, "fetch_codex_quota", return_value=fresh("codex")
        ) as codex, patch.object(
            quota, "fetch_grok_quota", return_value=fresh("grok")
        ) as grok:
            quota.fetch_all_quotas()

        claude.assert_not_called()
        codex.assert_called_once_with()
        grok.assert_called_once_with()

    def test_legacy_community_grok_profile_cache_is_removed(self):
        from sandglass import quota

        fresh_at = datetime.now(timezone.utc).isoformat()
        cached = {
            "claude": {"ok": True, "provider": "claude", "fetched_at": fresh_at},
            "codex": {"ok": True, "provider": "codex", "fetched_at": fresh_at},
            "grok": {"ok": True, "provider": "grok", "fetched_at": fresh_at},
            "grok:community-profile": {
                "ok": True,
                "provider": "grok",
                "fetched_at": fresh_at,
            },
        }
        with patch.object(quota, "_read_cache", return_value=cached), patch.object(
            quota, "_write_cache"
        ) as write:
            result = quota.fetch_all_quotas()

        self.assertNotIn("grok:community-profile", result)
        self.assertEqual(set(result), {"claude", "codex", "grok"})
        write.assert_called_once()
        self.assertNotIn("grok:community-profile", write.call_args.args[0])

    def test_parse_codex_wham(self):
        windows = parse_codex_wham(
            {
                "account_id": "abc",
                "plan_type": "plus",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 81,
                        "limit_window_seconds": 18000,
                        "reset_after_seconds": 11880,
                        "reset_at": 1787844693,
                    },
                    "secondary_window": {
                        "used_percent": 65,
                        "limit_window_seconds": 604800,
                        "reset_at": 1788299079,
                    },
                },
            }
        )
        labels = {w.label: w for w in windows}
        self.assertEqual(labels["5h"].used_percent, 81)
        self.assertEqual(labels["5h"].window_minutes, 300)
        self.assertEqual(labels["7d"].used_percent, 65)
        self.assertEqual(labels["7d"].window_minutes, 10080)
        self.assertTrue(labels["5h"].resets_at)

    def test_codex_quota_fetch_treats_a_200_with_no_rate_limit_as_a_failure(self):
        # A provider incident can answer 200 with a body that carries no usage
        # data at all. That must not read as "this account has no windows" --
        # it is a fetch failure, and the panel needs a real reason to show.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            home.joinpath("auth.json").write_text(
                json.dumps({"tokens": {"account_id": "acc-1", "access_token": "secret"}}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_HOME": str(home)}, clear=False), \
                 patch("sandglass.quota._get", return_value=({"unrelated": True}, None)):
                result = fetch_codex_quota()
        self.assertFalse(result["ok"])
        self.assertIn("rate_limit", result["error"])

    def test_codex_plan_type_maps_pro_tiers(self):
        self.assertEqual(_codex_plan_name("prolite"), "pro 5x")
        self.assertEqual(_codex_plan_name("pro"), "pro 20x")
        self.assertEqual(_codex_plan_name("plus"), "plus")

    def test_grok_plan_from_subscription_tier_and_jwt(self):
        self.assertEqual(_grok_plan_name("SuperGrok"), "SuperGrok")
        self.assertEqual(_grok_plan_name("super_grok"), "SuperGrok")
        self.assertEqual(_grok_plan_name(1), "SuperGrok")
        self.assertEqual(_grok_plan({"config": {"subscriptionTier": "SuperGrok"}}), "SuperGrok")
        self.assertEqual(_grok_plan({"subscriptionTier": "SuperGrok"}), "SuperGrok")

    def test_parse_grok_billing(self):
        windows = parse_grok_billing(
            {
                "config": {
                    "creditUsagePercent": 82.0,
                    "currentPeriod": {
                        "type": "USAGE_PERIOD_TYPE_WEEKLY",
                        "end": "2026-08-29T01:52:27+00:00",
                    },
                    "productUsage": [{"product": "GrokBuild", "usagePercent": 82.0}],
                }
            }
        )
        self.assertEqual(windows[0].label, "7d")
        self.assertEqual(windows[0].used_percent, 82.0)
        self.assertEqual(windows[0].window_minutes, 10080)
        self.assertTrue(windows[0].resets_at)

    def test_parse_grok_billing_zero_when_period_has_no_percent(self):
        windows = parse_grok_billing(
            {
                "config": {
                    "currentPeriod": {
                        "type": "USAGE_PERIOD_TYPE_WEEKLY",
                        "end": "2026-09-02T11:23:29+00:00",
                    },
                    "billingPeriodEnd": "2026-09-02T11:23:29+00:00",
                }
            }
        )
        self.assertEqual(windows[0].label, "7d")
        self.assertEqual(windows[0].used_percent, 0.0)
        self.assertTrue(windows[0].resets_at)

    def test_parse_claude_usage(self):
        windows = parse_claude_usage(
            {
                "five_hour": {"utilization": 39, "resets_at": "2026-08-27T12:00:00Z"},
                "seven_day": {"utilization": 15, "resets_at": "2026-09-01T12:00:00Z"},
                "seven_day_sonnet": {"utilization": 40, "resets_at": "2026-09-01T12:00:00Z"},
            }
        )
        labels = {w.label: w.used_percent for w in windows}
        self.assertEqual(labels["5h"], 39)
        self.assertEqual(labels["7d"], 15)
        self.assertEqual(labels["7d sonnet"], 40)

    def test_claude_quota_fetch_treats_a_200_with_no_five_hour_window_as_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            home.joinpath(".credentials.json").write_text(
                json.dumps({"claudeAiOauth": {"accessToken": "secret"}}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(home)}, clear=False), \
                 patch("sandglass.quota._get", return_value=({"unrelated": True}, None)):
                result = fetch_claude_quota()
        self.assertFalse(result["ok"])
        self.assertIn("five_hour", result["error"])

    def test_expired_grok_auth_is_never_rewritten(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "auth.json"
            path.write_text(
                json.dumps(
                    {
                        "https://auth.x.ai::client": {
                            "email": "a@b.c",
                            "user_id": "u1",
                            "key": "old",
                            "refresh_token": "rt",
                            "oidc_issuer": "https://auth.x.ai",
                            "oidc_client_id": "client",
                            "expires_at": "2020-01-01T00:00:00Z",
                        }
                    }
                ),
                encoding="utf-8",
            )
            before = path.read_bytes()
            result = fetch_grok_quota_from_auth(str(path), "u1")
            self.assertFalse(result["ok"])
            self.assertIn("official Grok client", result["error"])
            self.assertEqual(path.read_bytes(), before)


class QuotaPayloadTests(unittest.TestCase):
    def test_quota_payload_keeps_provider_capabilities_without_accounts(self):
        from sandglass.serve import _quota_payload

        capabilities = {
            provider: {
                name: {"supported": True, "available": False}
                for name in ("account_discovery", "local_usage", "official_quota")
            }
            for provider in ("claude", "codex", "grok")
        }
        with (
            patch("sandglass.serve.load_accounts", return_value=[]),
            patch("sandglass.serve.provider_capabilities", return_value=capabilities),
        ):
            payload = _quota_payload(live=False)

        self.assertEqual(payload["accounts"], [])
        self.assertEqual(payload["providers"], capabilities)

    def test_quota_payload_exposes_last_used_fields(self):
        from sandglass.serve import _quota_payload

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ):
            data = _quota_payload(live=False)
        self.assertIn("accounts", data)
        if not data["accounts"]:
            return
        row = data["accounts"][0]
        self.assertIn("last_used_at", row)
        self.assertIn("last_usage_at", row)
        self.assertIn("quota_source", row)
        self.assertIn("windows", row)

    def test_quota_payload_exposes_account_discovery_provenance(self):
        from sandglass.serve import _quota_payload

        account = Account(
            provider="codex",
            account_id="acc-1",
            email="person@example.com",
            extra={
                "account_source": "official_auth",
                "account_source_grade": "C",
                "account_source_official": True,
            },
        )
        with patch("sandglass.serve.load_accounts", return_value=[account]):
            row = _quota_payload(live=False)["accounts"][0]
        self.assertEqual(row["account_source"], "official_auth")
        self.assertEqual(row["account_source_grade"], "C")
        self.assertIs(row["account_source_official"], True)

    def test_quota_payload_exposes_official_grok_cli_provenance(self):
        from sandglass.serve import _quota_payload

        account = Account(
            provider="grok",
            account_id="grok-1",
            email="person@example.com",
            extra={
                "account_source": "official_grok_cli",
                "account_source_grade": "C",
                "account_source_official": True,
            },
        )
        with patch("sandglass.serve.load_accounts", return_value=[account]):
            row = _quota_payload(live=False)["accounts"][0]
        self.assertEqual(row["account_source"], "official_grok_cli")
        self.assertEqual(row["account_source_grade"], "C")
        self.assertIs(row["account_source_official"], True)

    def test_quota_payload_exposes_live_vendor_refresh_deadline(self):
        from sandglass.serve import _quota_payload

        fetched = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        claude = Account(
            provider="claude",
            account_id="claude-1",
            extra={
                "quota_source": "live",
                "quota_fetched_at": fetched.isoformat(),
            },
        )
        codex = Account(
            provider="codex",
            account_id="codex-1",
            extra={
                "quota_source": "live",
                "quota_fetched_at": fetched.isoformat(),
            },
        )
        stale = Account(
            provider="grok",
            account_id="grok-1",
            extra={
                "quota_source": "stale",
                "quota_fetched_at": fetched.isoformat(),
            },
        )
        with (
            patch("sandglass.serve.load_accounts", return_value=[claude, codex, stale]),
            patch("sandglass.serve.attach_cached_quota", side_effect=lambda rows: rows),
        ):
            rows = {row["account_id"]: row for row in _quota_payload(live=False)["accounts"]}
        self.assertEqual(rows["claude-1"]["quota_fetched_at"], fetched.isoformat())
        self.assertEqual(
            parse_ts(rows["claude-1"]["quota_refresh_at"]),
            fetched + timedelta(seconds=CACHE_TTL_SECONDS_BY_PROVIDER["claude"]),
        )
        self.assertEqual(
            parse_ts(rows["codex-1"]["quota_refresh_at"]),
            fetched + timedelta(seconds=CACHE_TTL_SECONDS_BY_PROVIDER["codex"]),
        )
        self.assertEqual(rows["grok-1"]["quota_refresh_at"], "")


class ResetLabelTests(unittest.TestCase):
    def test_resets_in_from_resets_at(self):
        now = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
        window = annotate_window(
            {
                "label": "5h",
                "used_percent": 90,
                "window_minutes": 300,
                "resets_at": (now + timedelta(hours=4)).isoformat(),
            },
            now=now,
        )
        self.assertEqual(window["resets_in"], "4h")
        self.assertNotIn("pace", window)

    def test_five_hour_blocks_group_sessions(self):
        now = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)
        start = int(now.timestamp()) // (5 * 3600) * (5 * 3600)
        in_block = datetime.fromtimestamp(start + 60, tz=timezone.utc)
        sessions = [
            SessionRecord(
                provider="codex",
                session_id="a",
                path="a",
                ended_at=in_block.isoformat(),
                usage=TokenUsage(input_tokens=100, calls=1),
            )
        ]
        blocks = five_hour_blocks(sessions, now=now, limit=4)
        current = [row for row in blocks if row["current"]]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["usage"]["input_tokens"], 100)
        self.assertEqual(current[0]["sessions"], 1)


class QuotaResetObservationTests(unittest.TestCase):
    """Provider periods and same-period resets are different evidence shapes."""

    def _observe(self, provider, label, window_minutes, samples):
        from sandglass import quota

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            quota, "meter_home", lambda: Path(tmp)
        ), patch.object(quota, "datetime", _FixedDatetime):
            for at, used, resets_at in samples:
                _FixedDatetime.current = at
                quota.note_quota_windows(
                    provider,
                    "account-1",
                    [
                        {
                            "label": label,
                            "used_percent": used,
                            "window_minutes": window_minutes,
                            "resets_at": resets_at,
                        }
                    ],
                )
            return quota._read_observations()[f"{provider}:account-1:{label}"]

    def test_provider_windows_that_can_hide_a_reset_are_explicit(self):
        from sandglass.quota import _can_hide_reset

        self.assertTrue(_can_hide_reset("grok", "7d"))
        self.assertTrue(_can_hide_reset("grok", "30d"))
        self.assertTrue(_can_hide_reset("claude", "7d"))
        self.assertFalse(_can_hide_reset("claude", "5h"))
        self.assertFalse(_can_hide_reset("codex", "5h"))
        self.assertFalse(_can_hide_reset("codex", "7d"))

    def test_first_live_sample_replaces_legacy_ambiguous_anchor(self):
        from sandglass import quota

        now = datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            quota, "meter_home", lambda: Path(tmp)
        ), patch.object(quota, "datetime", _FixedDatetime):
            _FixedDatetime.current = now
            path = Path(tmp) / "quota-observations.json"
            path.write_text(
                json.dumps(
                    {
                        "claude:account-1:7d": {
                            "anchor": "2026-09-02T04:40:00+00:00",
                            "anchor_kind": "reset",
                            "reset_signal": "drop",
                            "resets_at": "2026-09-03T06:00:00+00:00",
                            "used": 92.0,
                            "seen_at": "2026-09-02T04:41:00+00:00",
                            "gap": False,
                        }
                    }
                ),
                encoding="utf-8",
            )
            quota.note_quota_windows(
                "claude",
                "account-1",
                [
                    {
                        "label": "7d",
                        "used_percent": 10.0,
                        "window_minutes": 10080,
                        "resets_at": "2026-09-03T06:00:00+00:00",
                    }
                ],
            )
            entry = quota._read_observations()["claude:account-1:7d"]

        self.assertEqual(entry["source"], "official_live")
        self.assertEqual(entry["anchor_kind"], "period")
        self.assertEqual(entry["anchor"], "")
        self.assertNotIn("reset_signal", entry)

    def test_stale_quota_recovers_local_rolling_window_but_keeps_report_cumulative(self):
        from sandglass.report import build_report
        from sandglass.serve import _windows_for

        account = Account(
            provider="claude",
            account_id="account-1",
            active=True,
            extra={
                "quota_source": "stale",
                "windows": [
                    {
                        "label": "5h",
                        "used_percent": 5,
                        "window_minutes": 300,
                        "resets_at": "2026-09-02T06:00:00+00:00",
                    }
                ],
            },
        )
        session = SessionRecord(
            provider="claude",
            session_id="one",
            path="one.jsonl",
            account_id="account-1",
            usage=TokenUsage(input_tokens=100),
            timeline=[
                ("2026-09-02T05:00:00+00:00", TokenUsage(input_tokens=100))
            ],
            extra={"evidence_sources": ["user_adapter:test"]},
        )

        _FixedDatetime.current = datetime(2026, 9, 2, 5, 10, tzinfo=timezone.utc)
        with patch("sandglass.serve.datetime", _FixedDatetime):
            windows = _windows_for(
                account, [account], [session], account.extra["windows"]
            )
        self.assertEqual(windows[0]["usage"]["total_tokens"], 100)
        self.assertEqual(windows[0]["boundary_source"], "local_token_timeline")
        self.assertFalse(windows[0]["full_window_inference_allowed"])
        self.assertTrue(windows[0]["observation_gap"])
        report = build_report([session], accounts=[account], live_quota=False)
        row = report["accounts"][0]
        self.assertEqual(row["local_usage"]["total_tokens"], 100)
        self.assertEqual(row["local_in_windows"], [])

    def test_stale_weekly_percentage_keeps_fixed_schedule_for_local_backfill(self):
        from sandglass.serve import _windows_for

        now = datetime(2026, 9, 2, 5, 10, tzinfo=timezone.utc)
        account = Account(
            provider="claude",
            account_id="account-1",
            active=True,
            extra={"quota_source": "stale"},
        )
        window = annotate_window(
            {
                "label": "7d",
                "used_percent": 92,
                "window_minutes": 10080,
                "resets_at": "2026-09-03T06:00:00+00:00",
            },
            now=now,
        )
        session = SessionRecord(
            provider="claude",
            session_id="week",
            path="week.jsonl",
            account_id="account-1",
            usage=TokenUsage(input_tokens=100),
            timeline=[
                ("2026-09-02T05:00:00+00:00", TokenUsage(input_tokens=100))
            ],
            extra={"evidence_sources": ["user_adapter:test"]},
        )

        _FixedDatetime.current = now
        with patch("sandglass.serve.datetime", _FixedDatetime):
            windows = _windows_for(account, [account], [session], [window])

        self.assertEqual(windows[0]["usage"]["total_tokens"], 100)
        self.assertEqual(
            windows[0]["boundary_source"], "retained_provider_schedule"
        )
        self.assertFalse(windows[0]["full_window_inference_allowed"])

    def test_official_new_period_replaces_the_old_anchor(self):
        first = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        second = first + timedelta(minutes=2)
        entry = self._observe(
            "codex",
            "7d",
            10080,
            [
                (first, 63.0, (first + timedelta(days=7)).isoformat()),
                (second, 0.0, (second + timedelta(days=7)).isoformat()),
            ],
        )

        self.assertEqual(entry["anchor_kind"], "period")
        self.assertEqual(entry["anchor"], "")
        self.assertNotIn("reset_signal", entry)
        self.assertFalse(entry["gap"])

    def test_same_period_full_refill_is_anchored_to_the_full_observation(self):
        first = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        observed_full = first + timedelta(minutes=2)
        end = (first + timedelta(days=7)).isoformat()
        entry = self._observe(
            "grok",
            "7d",
            10080,
            [(first, 42.0, end), (observed_full, 0.0, end)],
        )

        self.assertEqual(entry["anchor_kind"], "reset")
        self.assertEqual(entry["reset_signal"], "refilled")
        self.assertEqual(entry["anchor"], observed_full.isoformat())
        self.assertFalse(entry["gap"])

    def test_claude_full_refill_outranks_its_own_fractional_period_drift(self):
        before = datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc)
        observed_full = before + timedelta(minutes=2)
        entry = self._observe(
            "claude",
            "7d",
            10080,
            [
                (before, 95.0, "2026-09-04T13:17:59.700000+00:00"),
                (observed_full, 0.0, "2026-09-04T13:18:00.200000+00:00"),
            ],
        )

        self.assertEqual(entry["anchor_kind"], "reset")
        self.assertEqual(entry["reset_signal"], "refilled")
        self.assertEqual(entry["anchor"], observed_full.isoformat())
        self.assertFalse(entry["gap"])

    def test_claude_refill_makes_five_hour_and_weekly_local_usage_match(self):
        from sandglass import quota, serve
        from sandglass.serve import _windows_for

        before = datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc)
        observed_full = before + timedelta(minutes=2)
        after = observed_full + timedelta(minutes=1)
        account = Account(provider="claude", account_id="account-1", active=True)
        session = SessionRecord(
            provider="claude",
            session_id="same-shape",
            path="same-shape.jsonl",
            account_id="account-1",
            timeline=[
                ((before - timedelta(minutes=1)).isoformat(), TokenUsage(input_tokens=900)),
                (after.isoformat(), TokenUsage(input_tokens=100)),
            ],
        )
        windows = [
            {
                "label": "5h",
                "used_percent": 1,
                "window_start": observed_full.isoformat(),
                "resets_at": (observed_full + timedelta(hours=5)).isoformat(),
            },
            {
                "label": "7d",
                "used_percent": 1,
                "window_start": (observed_full - timedelta(days=6)).isoformat(),
                "resets_at": "2026-09-04T13:18:00.200000+00:00",
            },
        ]

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            quota, "meter_home", lambda: Path(tmp)
        ), patch.object(quota, "datetime", _FixedDatetime), patch.object(
            serve, "_switch_runs_for", return_value=None
        ):
            for at, used, reset in (
                (before, 95.0, "2026-09-04T13:17:59.700000+00:00"),
                (observed_full, 0.0, "2026-09-04T13:18:00.200000+00:00"),
            ):
                _FixedDatetime.current = at
                quota.note_quota_windows(
                    "claude",
                    "account-1",
                    [{"label": "7d", "used_percent": used,
                      "window_minutes": 10080, "resets_at": reset}],
                )
            rows = {row["label"]: row for row in _windows_for(
                account, [account], [session], windows,
                single_account_owner_id="account-1",
            )}

        self.assertEqual(rows["5h"]["usage"]["input_tokens"], 100)
        self.assertEqual(rows["7d"]["usage"]["input_tokens"], 100)
        self.assertTrue(rows["7d"]["reset_anchored"])
        self.assertEqual(rows["7d"]["counted_from"], observed_full.isoformat())

    def test_same_period_large_drop_is_a_reset_but_small_drift_is_not(self):
        first = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        end = (first + timedelta(days=7)).isoformat()
        reset = self._observe(
            "claude",
            "7d",
            10080,
            [(first, 42.0, end), (first + timedelta(minutes=2), 36.0, end)],
        )
        drift = self._observe(
            "claude",
            "7d",
            10080,
            [(first, 42.0, end), (first + timedelta(minutes=2), 39.0, end)],
        )

        self.assertEqual(reset["anchor_kind"], "reset")
        self.assertEqual(reset["reset_signal"], "drop")
        self.assertEqual(drift["anchor_kind"], "period")
        self.assertNotIn("reset_signal", drift)

    def test_long_blind_spot_only_invalidates_windows_that_can_hide_resets(self):
        first = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        end = (first + timedelta(days=7)).isoformat()
        samples = [(first, 40.0, end), (first + timedelta(minutes=61), 41.0, end)]

        grok = self._observe("grok", "7d", 10080, samples)
        codex = self._observe("codex", "7d", 10080, samples)

        self.assertTrue(grok["gap"])
        self.assertFalse(codex["gap"])


class LocalWindowTests(unittest.TestCase):
    def test_detected_reset_cuts_off_earlier_local_usage(self):
        from sandglass import serve
        from sandglass.serve import _windows_for

        now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        anchor = now - timedelta(hours=1)
        account = Account(provider="grok", account_id="u1", email="a@b.c")
        session = SessionRecord(
            provider="grok",
            session_id="s1",
            path="s1",
            account_id="u1",
            timeline=[
                ((now - timedelta(hours=2)).isoformat(), TokenUsage(input_tokens=100)),
                ((now - timedelta(minutes=30)).isoformat(), TokenUsage(input_tokens=50)),
            ],
        )
        windows = [
            {
                "label": "7d",
                "used_percent": 4,
                "window_start": (now - timedelta(days=6)).isoformat(),
                "resets_at": (now + timedelta(days=1)).isoformat(),
            }
        ]

        with patch.object(
            serve, "quota_anchor", return_value=(anchor.isoformat(), "reset", False)
        ), patch.object(serve, "_switch_runs_for", return_value=None):
            row = _windows_for(
                account, [account], [session], windows, single_account_owner_id="u1"
            )[0]

        self.assertEqual(row["usage"]["input_tokens"], 50)
        self.assertTrue(row["reset_anchored"])
        self.assertFalse(row["observation_gap"])

    def test_five_hour_and_seven_day_use_matching_slices(self):
        from sandglass.serve import _windows_for

        now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
        account = Account(provider="grok", account_id="u1", email="a@b.c")
        session = SessionRecord(
            provider="grok",
            session_id="s1",
            path="s1",
            account_id="u1",
            models=["grok-4.6"],
            timeline=[
                ((now - timedelta(hours=1)).isoformat(), TokenUsage(input_tokens=1000, output_tokens=100)),
                ((now - timedelta(days=3)).isoformat(), TokenUsage(input_tokens=5000, output_tokens=500)),
            ],
        )
        windows = annotate_windows(
            [
                {"label": "5h", "used_percent": 10, "window_minutes": 300, "resets_at": (now + timedelta(hours=4)).isoformat()},
                {"label": "7d", "used_percent": 20, "window_minutes": 10080, "resets_at": (now + timedelta(days=2)).isoformat()},
            ],
            now=now,
        )
        with patch("sandglass.serve._switch_runs_for", return_value=None):
            rows = {
                row["label"]: row
                for row in _windows_for(
                    account, [account], [session], windows, single_account_owner_id="u1"
                )
            }
        self.assertEqual(rows["5h"]["usage"]["input_tokens"], 1000)
        self.assertEqual(rows["7d"]["usage"]["input_tokens"], 6000)
        self.assertNotIn("cost_usd", rows["5h"])
        self.assertNotIn("cost_usd", rows["7d"])

    def test_grok_cli_session_is_split_at_the_switch(self):
        """The reason this exists: Grok used to own a session whole.

        A file opened under one account and still running after a switch handed
        every one of its minutes to whichever account the session was assigned to,
        including the ones produced while somebody else was signed in.
        """
        from sandglass import serve
        from sandglass.serve import _windows_for

        now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
        old = Account(provider="grok", account_id="old", email="old@example.com")
        new = Account(provider="grok", account_id="new", email="new@example.com", active=True)
        switch = now - timedelta(hours=3)
        session = SessionRecord(
            provider="grok",
            session_id="s1",
            path=r"C:\Users\me\.grok\sessions\abc\updates.jsonl",
            account_id="new",          # assigned whole to the account in use now
            models=["grok-4.6"],
            timeline=[
                ((switch - timedelta(hours=1)).isoformat(), TokenUsage(output_tokens=700)),
                ((switch + timedelta(hours=1)).isoformat(), TokenUsage(output_tokens=300)),
            ],
        )
        windows = annotate_windows(
            [{"label": "7d", "used_percent": 20, "window_minutes": 10080,
              "resets_at": (now + timedelta(days=2)).isoformat()}],
            now=now,
        )
        runs = [((now - timedelta(days=5)).isoformat(), "old"), (switch.isoformat(), "new")]
        with patch.object(serve, "_switch_runs_for", lambda provider: runs):
            for_old = _windows_for(
                old, [old, new], [session], windows, single_account_owner_id=""
            )
            for_new = _windows_for(
                new, [old, new], [session], windows, single_account_owner_id=""
            )
        self.assertEqual(for_old[0]["usage"]["output_tokens"], 700)
        self.assertEqual(for_new[0]["usage"]["output_tokens"], 300)

    def test_grok_does_not_inherit_other_account_cli_sessions(self):
        from sandglass.serve import _windows_for

        now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
        cli = Account(
            provider="grok",
            account_id="cli",
            email="cli@example.com",
            active=True,
            extra={"activated_at_ms": (now - timedelta(hours=1)).isoformat()},
        )
        other = Account(
            provider="grok",
            account_id="other",
            email="other@example.com",
            extra={"activated_at_ms": (now - timedelta(days=2)).isoformat()},
        )
        session = SessionRecord(
            provider="grok",
            session_id="s1",
            path=r"C:\Users\me\.grok\sessions\abc\updates.jsonl",
            account_id="cli",
            models=["grok-4.6"],
            timeline=[((now - timedelta(hours=2)).isoformat(), TokenUsage(input_tokens=9000, output_tokens=10))],
        )
        windows = annotate_windows(
            [{"label": "7d", "used_percent": 20, "window_minutes": 10080, "resets_at": (now + timedelta(days=2)).isoformat()}],
            now=now,
        )
        runs = [((now - timedelta(hours=3)).isoformat(), "cli")]
        with patch("sandglass.serve._switch_runs_for", return_value=runs):
            stolen = _windows_for(
                other, [cli, other], [session], windows, single_account_owner_id=""
            )
            owned = _windows_for(
                cli, [cli, other], [session], windows, single_account_owner_id=""
            )
        self.assertEqual(stolen[0]["usage"]["input_tokens"], 0)
        self.assertEqual(owned[0]["usage"]["input_tokens"], 9000)


class AtomicStateWriteTests(unittest.TestCase):
    """quota.py held the only two state writes that could truncate their target.

    Everything else in Sandglass stages a temporary file and replaces. These two
    wrote in place, and both of their files are read with a broad except that
    treats a damaged file as an empty one -- so an interrupted write to
    quota-observations.json silently discards every reset anchor ever caught and
    reads as a machine that had never seen a reset.
    """

    def setUp(self):
        from sandglass import quota

        self.quota = quota
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        patcher = patch.dict(os.environ, {"SANDGLASS_HOME": self._home.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = Path(self._home.name) / "state.json"

    def _write(self, payload, component="audit_probe"):
        return self.quota._write_json_atomic(self.path, payload, component=component)

    def test_a_failed_write_leaves_the_previous_file_intact(self):
        with patch("sandglass.diagnostics.record_component_failure"),                 patch("sandglass.diagnostics.clear_component_failure"):
            self._write({"kept": 1})
            self.assertEqual(
                json.loads(self.path.read_text(encoding="utf-8")), {"kept": 1}
            )

            with patch("sandglass.quota.os.replace", side_effect=OSError("disk full")):
                self._write({"kept": 2})

        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"kept": 1},
                         "写失败必须保留旧内容，而不是留下半个文件")

    def test_a_failed_write_leaves_no_temporary_behind(self):
        with patch("sandglass.diagnostics.record_component_failure"),                 patch("sandglass.diagnostics.clear_component_failure"):
            self._write({"kept": 1})
            with patch("sandglass.quota.os.replace", side_effect=OSError("disk full")):
                self._write({"kept": 2})
        leftovers = [p.name for p in Path(self._home.name).iterdir() if p.name != "state.json"]
        self.assertEqual(leftovers, [])

    def test_a_write_it_could_not_do_is_reported_rather_than_dropped(self):
        """Losing this write has the effect the class docstring describes.

        The reset anchors go missing and the machine reads as one that never
        saw a reset -- and it used to do that in silence, because the failure
        was caught, the temporary cleaned up, and nothing returned. Still not
        raised: both callers are bookkeeping inside a quota fetch, and a fetch
        that has an answer must not fail over a file it could not save.
        """
        recorded = []
        cleared = []
        with patch("sandglass.diagnostics.record_component_failure",
                   lambda name, exc: recorded.append(name)),                 patch("sandglass.diagnostics.clear_component_failure",
                      lambda name: cleared.append(name)):
            with patch("sandglass.quota.os.replace", side_effect=OSError("disk full")):
                self._write({"kept": 1}, component="quota_observation_write")
            self.assertEqual(recorded, ["quota_observation_write"])
            self.assertEqual(cleared, [])

            self._write({"kept": 2}, component="quota_observation_write")

        self.assertEqual(cleared, ["quota_observation_write"])

    def test_a_successful_write_replaces_the_content(self):
        with patch("sandglass.diagnostics.clear_component_failure"):
            self._write({"v": 1})
            self._write({"v": 2})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"v": 2})

    def test_neither_quota_state_file_is_written_in_place(self):
        """Read as source: the hazard is the call, not the result."""
        source = (Path(__file__).resolve().parents[1] / "sandglass" / "quota.py").read_text(
            encoding="utf-8"
        )
        writes = [line.strip() for line in source.splitlines()
                  if ".write_text(" in line and "temporary" not in line]
        self.assertEqual(writes, [], f"quota.py 仍有就地写入：{writes}")

    def test_an_unreadable_observations_file_is_not_replaced_with_one_window(self):
        """A failed read is not an empty observations ledger.

        `_read_json` treated every OSError as empty, so a locked
        quota-observations.json became `{}` and the next `note_quota_windows`
        wrote only the window it had just seen. That is the same erasure the
        atomic write closed for a truncated file -- every other reset anchor
        gone, and the machine reads as one that had never seen a reset.
        """
        path = Path(self._home.name) / "quota-observations.json"
        original = json.dumps({
            "grok:a1:7d": {"anchor": "keep", "anchor_kind": "reset", "used": 40.0},
            "claude:a1:7d": {"anchor": "keep-too", "anchor_kind": "reset", "used": 65.0},
        })
        path.write_text(original, encoding="utf-8")
        reported = []
        real_read = Path.read_text

        def read_text(self, *args, **kwargs):
            if self.name == "quota-observations.json":
                raise PermissionError("quota-observations.json is locked")
            return real_read(self, *args, **kwargs)

        with patch.object(Path, "read_text", read_text), \
                patch("sandglass.diagnostics.record_component_failure",
                      lambda name, exc: reported.append(name)):
            self.quota.note_quota_windows("codex", "a1", [
                {"label": "5h", "used_percent": 3.0, "resets_at": "2026-09-07T12:00:00+00:00",
                 "window_minutes": 300}])

        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(reported, ["quota_observation_write"])


class ExpiredPeriodTests(unittest.TestCase):
    """A period that has ended is not the current one, however fresh the read.

    Claude's quota is polled every three minutes. A reset landing between polls
    left the panel slicing local usage by the window that had just closed: it
    showed that window's whole total as the new period's usage, unchanged, for
    up to a full interval, and the tokens spent since the reset fell outside
    `end` and were counted nowhere at all. Seen live on 09-02: (figure
    withheld) held across four consecutive readings after a 16:19:59 reset,
    then dropping (figure withheld) the moment the poll caught up.
    """

    def _account(self):
        # A real snapshot always carries when it was fetched; the rollover
        # only applies to a period that was still running at that moment.
        return Account(provider="claude", account_id="a1", active=True,
                       extra={"quota_source": "live",
                              "quota_fetched_at": (datetime.now(timezone.utc)
                                                   - timedelta(minutes=4)).isoformat()})

    def _session(self, timeline):
        return SessionRecord(provider="claude", session_id="s", path="s.jsonl",
                             account_id="a1", timeline=timeline)

    def _window(self, ends_at):
        return {"label": "5h", "used_percent": 90.0, "window_minutes": 300,
                "window_start": (ends_at - timedelta(hours=5)).isoformat(),
                "resets_at": ends_at.isoformat()}

    def _rows(self, ends_at):
        from sandglass.serve import _windows_for

        now = datetime.now(timezone.utc)
        timeline = [
            ((ends_at - timedelta(minutes=30)).isoformat(),
             TokenUsage(input_tokens=1_000_000, calls=10)),
            ((now - timedelta(minutes=1)).isoformat(), TokenUsage(input_tokens=7, calls=1)),
        ]
        account = self._account()
        return _windows_for(account, [account], [self._session(timeline)],
                            [self._window(ends_at)], "a1")

    def test_usage_after_the_reset_is_what_the_new_period_counts(self):
        rows = self._rows(datetime.now(timezone.utc) - timedelta(minutes=3))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["usage"].total_tokens if hasattr(rows[0]["usage"], "total_tokens")
                         else rows[0]["usage"]["total_tokens"], 7)

    def test_the_rolled_period_starts_where_the_last_one_ended(self):
        ended = datetime.now(timezone.utc) - timedelta(minutes=3)
        rows = self._rows(ended)
        self.assertEqual(rows[0]["boundary_source"], "official_period_rollover")
        self.assertEqual(parse_ts(rows[0]["counted_from"]), ended)
        self.assertEqual(parse_ts(rows[0]["counted_to"]), ended + timedelta(hours=5))

    def test_a_rolled_period_offers_no_full_window_estimate(self):
        """used% belongs to the period that ended; folding it out is nonsense."""
        rows = self._rows(datetime.now(timezone.utc) - timedelta(minutes=3))
        self.assertFalse(rows[0]["full_window_inference_allowed"])

    def test_a_period_still_running_is_left_alone(self):
        from sandglass.serve import _windows_for

        now = datetime.now(timezone.utc)
        window = self._window(now + timedelta(hours=2))
        timeline = [((now - timedelta(minutes=30)).isoformat(),
                     TokenUsage(input_tokens=1_000_000, calls=30))]
        account = self._account()
        rows = _windows_for(account, [account], [self._session(timeline)], [window], "a1")
        self.assertEqual(rows[0]["boundary_source"], "official_quota_window")
        self.assertTrue(rows[0]["full_window_inference_allowed"])

    def test_a_period_that_had_already_ended_when_it_was_read_is_left_alone(self):
        """The boundary of the rule, and the reason it is drawn there.

        A reset between two polls is a period we watched end, and the schedule
        says exactly where the next one starts. A provider handing back a period
        that was already over before we fetched it is a different thing, and
        rolling periods forward from it would be covering for that rather than
        saying so -- so the old handling stands and nothing here invents a
        window on its behalf.
        """
        ended = datetime.now(timezone.utc) - timedelta(hours=12)
        rows = self._rows(ended)
        self.assertTrue(rows)
        self.assertNotEqual(rows[0]["boundary_source"], "official_period_rollover")


class QuotaReadingLogTests(unittest.TestCase):
    """What the provider said, and when, kept as a step function.

    quota-observations.json holds one current value per window and overwrites
    it, so after the fact there was no way to ask what the vendor reported an
    hour ago or exactly when a period rolled over. The panel's own recorded
    answers are what made the reset bug findable; the provider's side of those
    same minutes had no equivalent.

    Not a check. Quota is charged in weighted units the provider does not
    publish, so a ratio against local tokens moves for honest reasons and any
    threshold over it would be fitted rather than derived.
    """

    def setUp(self):
        from sandglass import quota

        self.quota = quota
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        patcher = patch.dict(os.environ, {"SANDGLASS_HOME": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _observe(self, used, resets="2026-09-03T00:00:00+00:00"):
        self.quota.note_quota_windows("codex", "a1", [
            {"label": "5h", "used_percent": used, "resets_at": resets,
             "window_minutes": 300}])

    def _rows(self):
        path = self.home / "quota-readings.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_only_a_changed_reading_is_written(self):
        self._observe(10)
        self._observe(10)
        self._observe(10)
        self.assertEqual([r["used_percent"] for r in self._rows()], [10.0])

    def test_each_step_is_kept(self):
        for used in (10, 10, 23, 23, 41):
            self._observe(used)
        self.assertEqual([r["used_percent"] for r in self._rows()], [10.0, 23.0, 41.0])

    def test_a_new_period_is_a_step_even_at_the_same_percentage(self):
        """A reset back to the same number still moved the window."""
        self._observe(0, "2026-09-03T00:00:00+00:00")
        self._observe(0, "2026-09-03T05:00:00+00:00")
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["resets_at"], rows[1]["resets_at"])

    def test_it_carries_no_secret_and_names_the_window(self):
        self._observe(10)
        row = self._rows()[0]
        self.assertEqual(
            sorted(row), ["account_id", "at", "label", "provider", "resets_at", "used_percent"])

    def test_an_unwritable_home_does_not_break_observation(self):
        """Bookkeeping must never stop the observation it accompanies."""
        with patch.object(self.quota, "quota_readings_path",
                          side_effect=OSError("no such device")):
            self._observe(10)
        self.assertEqual(self._rows(), [])

    def test_a_reading_write_it_could_not_do_is_reported_rather_than_dropped(self):
        """A missing jsonl row used to mean the value had not moved.

        `_note_quota_reading` caught OSError and returned. quota-observations
        still updated, so the next poll saw the new used% as already stored
        and never tried the append again. The step was gone, and nothing
        said so. Still not raised: this is bookkeeping beside an observation.
        """
        recorded = []
        cleared = []
        self._observe(10)
        with patch("sandglass.diagnostics.record_component_failure",
                   lambda name, exc: recorded.append(name)
                   if name == "quota_reading_write" else None), \
                patch("sandglass.diagnostics.clear_component_failure",
                      lambda name: cleared.append(name)
                      if name == "quota_reading_write" else None):
            with patch.object(self.quota, "quota_readings_path",
                              side_effect=OSError("disk full")):
                self._observe(23)
            self.assertEqual([r["used_percent"] for r in self._rows()], [10.0])
            self.assertEqual(recorded, ["quota_reading_write"])
            self.assertEqual(cleared, [])
            # Observations already stored 23, so a retry of 23 would look
            # unchanged and never append. A later real step still writes.
            self._observe(41)
        self.assertEqual(cleared, ["quota_reading_write"])
        self.assertEqual([r["used_percent"] for r in self._rows()], [10.0, 41.0])


if __name__ == "__main__":
    unittest.main()
