"""Smoke-test an installed Sandglass wheel outside the source checkout.

The caller must use the Python executable from the clean installation and set
the working directory outside the repository. All provider fixtures are local,
official-client-shaped files under a temporary directory; no real user profile
is inspected.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _grok_auth(email: str, account_id: str) -> dict:
    return {
        f"https://auth.x.ai::{account_id}": {
            "email": email,
            "user_id": account_id,
            "principal_id": account_id,
            "auth_mode": "oidc",
            "key": f"fixture-{account_id}",
        }
    }


def _snapshot(root: Path) -> dict[str, tuple[int, str]]:
    return {
        str(path.relative_to(root)): (
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        claude = root / "official-claude"
        codex = root / "official-codex"
        grok = root / "official-grok"
        community_grok_app = root / "community-grok-app" / "accounts"
        meter = root / "sandglass-home"

        _write_json(
            claude / ".credentials.json",
            {"claudeAiOauth": {"accessToken": "fixture-claude", "subscriptionType": "pro"}},
        )
        _write_json(
            claude / ".claude.json",
            {"oauthAccount": {"accountUuid": "claude-1", "emailAddress": "claude@example.test"}},
        )
        _write_json(codex / "auth.json", {"tokens": {"account_id": "codex-1"}})
        _write_json(
            codex / "accounts" / "registry.json",
            {"accounts": [{"chatgpt_account_id": "third-party-ghost"}]},
        )
        _write_json(grok / "auth.json", _grok_auth("grok-cli@example.test", "grok-cli-1"))

        app_profiles = []
        for number in range(1, 4):
            profile_id = f"profile-{number}"
            account_id = f"community-grok-{number}"
            email = f"community-grok-{number}@example.test"
            app_profiles.append({"id": profile_id, "email": email})
            _write_json(community_grok_app / profile_id / "auth.json", _grok_auth(email, account_id))
        _write_json(
            community_grok_app / "index.json",
            {"activeId": "profile-1", "profiles": app_profiles},
        )

        vendor_roots = (claude, codex, grok, community_grok_app)
        before = [_snapshot(path) for path in vendor_roots]
        env = os.environ.copy()
        env.update(
            {
                "CLAUDE_CONFIG_DIR": str(claude),
                "CLAUDE_PROJECTS": str(claude / "projects"),
                "CODEX_HOME": str(codex),
                "GROK_HOME": str(grok),
                # A legacy override is deliberately present: the installed
                # public package must ignore this community account store.
                "GROK_APP_ACCOUNTS": str(community_grok_app),
                "SANDGLASS_HOME": str(meter),
                "PYTHONUTF8": "1",
            }
        )

        check = subprocess.run(
            [sys.executable, "-m", "sandglass", "--offline", "--json", "accounts"],
            cwd=root,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        )
        report = json.loads(check.stdout)

        # Import after changing the working directory in the parent workflow:
        # this must resolve to the installed wheel, not the source checkout.
        from sandglass.accounts import load_accounts
        from sandglass.capabilities import provider_capabilities

        old = os.environ.copy()
        try:
            os.environ.update(env)
            accounts = load_accounts()
            capabilities = provider_capabilities(accounts)
        finally:
            os.environ.clear()
            os.environ.update(old)

        by_provider: dict[str, list] = {}
        for account in accounts:
            by_provider.setdefault(account.provider, []).append(account)
        assert len(by_provider.get("claude", [])) == 1
        assert [a.account_id for a in by_provider.get("codex", [])] == ["codex-1"]
        grok_accounts = by_provider.get("grok", [])
        assert [a.account_id for a in grok_accounts] == ["grok-cli-1"]
        assert grok_accounts[0].extra.get("account_source") == "official_grok_cli"
        assert all(a.extra.get("account_source_official") is True for a in grok_accounts)
        assert all(a.account_id != "third-party-ghost" for a in accounts)
        assert all(not a.account_id.startswith("community-grok-") for a in accounts)
        assert len(report.get("accounts") or []) == 3
        assert set(capabilities) == {"claude", "codex", "grok"}
        assert all(
            capabilities[provider]["account_discovery"]["available"]
            for provider in capabilities
        )
        assert all(
            set(capabilities[provider])
            == {"account_discovery", "local_usage", "official_quota"}
            for provider in capabilities
        )
        assert [_snapshot(path) for path in vendor_roots] == before
        assert (meter / "cache.sqlite").is_file()

        package = importlib.resources.files("sandglass")
        assert package.joinpath("web", "index.html").is_file()
        assert package.joinpath("web", "assets", "logo-mark.png").is_file()

    print("installed-wheel smoke: 1 Claude, 1 Codex, 1 Grok; community data ignored; provider files unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
