import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandglass.accounts import load_accounts
from sandglass.serve import _quota_payload


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _env(root: Path) -> dict[str, str]:
    return {
        "CLAUDE_CONFIG_DIR": str(root / "claude"),
        "CODEX_HOME": str(root / "codex"),
        "GROK_HOME": str(root / "grok"),
        "SANDGLASS_HOME": str(root / "sandglass"),
    }


def _add_claude(root: Path) -> None:
    _write(
        root / "claude" / ".credentials.json",
        {"claudeAiOauth": {"accessToken": "fixture", "subscriptionType": "pro"}},
    )
    _write(
        root / "claude" / ".claude.json",
        {"oauthAccount": {"accountUuid": "claude-1", "emailAddress": "claude@example.test"}},
    )


def _add_codex(root: Path) -> None:
    _write(
        root / "codex" / "auth.json",
        {"auth_mode": "chatgpt", "tokens": {"account_id": "codex-1", "access_token": "fixture"}},
    )


def _add_grok(root: Path) -> None:
    _write(
        root / "grok" / "auth.json",
        {
            "https://auth.x.ai::grok-1": {
                "auth_mode": "oidc",
                "user_id": "grok-1",
                "email": "grok@example.test",
                "key": "fixture",
            }
        },
    )


class ColdStartMatrixTests(unittest.TestCase):
    def test_no_provider_files_returns_no_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, _env(root), clear=False):
                self.assertEqual(load_accounts(), [])
                payload = _quota_payload(live=False)
                self.assertEqual(payload["accounts"], [])
                self.assertEqual(set(payload["providers"]), {"claude", "codex", "grok"})
                for capabilities in payload["providers"].values():
                    self.assertFalse(capabilities["account_discovery"]["available"])
                    self.assertFalse(capabilities["local_usage"]["available"])
                    self.assertFalse(capabilities["official_quota"]["available"])

    def test_each_single_provider_is_discovered_without_the_others(self):
        cases = (
            ("claude", _add_claude, "official_claude_config"),
            ("codex", _add_codex, "official_auth"),
            ("grok", _add_grok, "official_grok_cli"),
        )
        for provider, create, source in cases:
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                create(root)
                with patch.dict(os.environ, _env(root), clear=False):
                    rows = _quota_payload(live=False)["accounts"]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["provider"], provider)
                self.assertEqual(rows[0]["account_source"], source)
                self.assertIs(rows[0]["account_source_official"], True)

    def test_all_supported_providers_are_discovered_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for create in (_add_claude, _add_codex, _add_grok):
                create(root)
            with patch.dict(os.environ, _env(root), clear=False):
                accounts = load_accounts()
            self.assertEqual([account.provider for account in accounts], ["claude", "codex", "grok"])
            self.assertTrue(all(account.extra.get("account_source_official") for account in accounts))

    def test_partial_auth_without_an_identity_never_creates_placeholder_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "claude" / ".credentials.json", {"claudeAiOauth": {"accessToken": "fixture"}})
            _write(root / "codex" / "auth.json", {"tokens": {"access_token": "fixture"}})
            _write(root / "grok" / "auth.json", {"unidentified-session": {"key": "fixture"}})
            with patch.dict(os.environ, _env(root), clear=False):
                self.assertEqual(load_accounts(), [])

    def test_grok_official_auth_key_can_supply_the_principal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "grok" / "auth.json",
                {"https://auth.x.ai::grok-from-key": {"auth_mode": "oidc", "key": "fixture"}},
            )
            with patch.dict(os.environ, _env(root), clear=False):
                accounts = load_accounts()
            self.assertEqual([account.account_id for account in accounts], ["grok-from-key"])


class BooksOpenWithoutTheObserverTests(unittest.TestCase):
    """A first install has to open its books even if the observer never lives.

    note_current_identity was reachable only from the observer's watch loop, and
    the observer is the component most likely to be stopped on a fresh machine:
    application control blocks an unsigned detached process and it dies without
    a word. The panel still starts and still shows usage, and until something
    records the current identity every one of those minutes is unattributed --
    after intervention, which is the case the promise does not allow.
    """

    def test_startup_records_the_current_identity_before_any_observer(self):
        source = (Path(__file__).resolve().parents[1] / "sandglass" / "desktop.py").read_text(
            encoding="utf-8"
        )
        body = source.split("def main() -> int:", 1)[1]
        self.assertIn("note_current_identity(observe_codex=False)", body,
                      "桌面启动必须自己开账，不能只依赖观测器")
        self.assertLess(body.index("note_current_identity(observe_codex=False)"),
                        body.index("ensure_observer_running()"),
                        "开账要在拉起观测器之前完成")

    def test_a_fresh_home_gains_its_ledgers_from_one_observation(self):
        """The behaviour that call is there for, driven directly."""
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "codex" / "auth.json",
                   {"tokens": {"access_token": "t", "account_id": "acct-new"}})
            _write(root / "codex" / "accounts" / "registry.json",
                   {"active_account_key": "k",
                    "active_account_activated_at_ms": 1788400000000,
                    "accounts": [{"key": "k", "account_id": "acct-new",
                                  "email": "new@example.com", "plan": "plus"}]})
            home = root / "sandglass"
            with patch.dict(os.environ, _env(root)):
                for name in ("_CODEX_RUNS", "_CODEX_RUNS_SRC", "_GROK_RUNS",
                             "_GROK_RUNS_SRC", "_GROK_OWNERS", "_CLAUDE_RUNS",
                             "_CLAUDE_RUNS_SRC"):
                    if hasattr(accounts, name):
                        setattr(accounts, name, None)
                self.assertFalse(home.exists())
                self.assertEqual(accounts.codex_identity_runs(), [])

                self.assertTrue(accounts.note_current_identity())

                runs = accounts.codex_identity_runs()
                self.assertEqual([who for _, who in runs], ["acct-new"])
                events = json.loads(
                    (home / "codex-official-identity-events-v2.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(
                    [event["kind"] for event in events["events"]], ["observed"]
                )


if __name__ == "__main__":
    unittest.main()
