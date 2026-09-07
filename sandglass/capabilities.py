from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Callable

from sandglass.models import Account, PROVIDERS
from sandglass.paths import claude_projects, codex_home, grok_home


_SOURCES: dict[str, dict[str, dict[str, Any]]] = {
    "claude": {
        "account_discovery": {
            "source": "official_claude_config",
            "grade": "C",
            "coverage": "current_official_config_identity",
        },
        "local_usage": {
            "source": "official_claude_session_logs",
            "grade": "B",
            "coverage": "official_logs_present_on_this_machine",
        },
        "official_quota": {
            "source": "observed_first_party_endpoint",
            "grade": "D",
            "coverage": "current_provider_account_all_devices",
        },
    },
    "codex": {
        "account_discovery": {
            "source": "official_auth_and_post_install_observations",
            "grade": "C",
            "coverage": "current_identity_and_observed_identity_changes",
        },
        "local_usage": {
            "source": "official_codex_rollout_logs",
            "grade": "B",
            "coverage": "official_logs_present_on_this_machine",
        },
        "official_quota": {
            "source": "observed_first_party_endpoint",
            "grade": "D",
            "coverage": "current_provider_account_all_devices",
        },
    },
    "grok": {
        "account_discovery": {
            "source": "official_grok_cli_and_post_install_observations",
            "grade": "C",
            "coverage": "current_identity_and_observed_identity_changes",
        },
        "local_usage": {
            "source": "official_grok_cli_session_logs",
            "grade": "B",
            "coverage": "official_logs_present_on_this_machine",
        },
        "official_quota": {
            "source": "observed_official_cli_endpoint",
            "grade": "D",
            "coverage": "current_provider_account_all_devices",
        },
    },
}


def claude_project_roots() -> list[Path]:
    """The roots actually consumed by the current Claude collector."""

    return [claude_projects()]


def _has_file(roots: Iterable[Path], predicate: Callable[[Path], bool]) -> bool:
    for root in roots:
        if not root.is_dir():
            continue
        try:
            if any(path.is_file() and predicate(path) for path in root.rglob("*.jsonl")):
                return True
        except OSError:
            continue
    return False


def _local_usage_present(provider: str) -> bool:
    if provider == "claude":
        return _has_file(claude_project_roots(), lambda _path: True)
    if provider == "codex":
        home = codex_home()
        return _has_file(
            (home / "sessions", home / "archived_sessions"),
            lambda path: path.name.startswith("rollout-"),
        )
    if provider == "grok":
        return _has_file((grok_home() / "sessions",), lambda path: path.name == "updates.jsonl")
    return False


def _quota_status(accounts: list[Account]) -> tuple[bool, str]:
    """Whether a quota window exists, and where the one being shown came from.

    Availability was decided from any account with a window while the status
    string was taken from any account at all, so an account holding a window
    whose own refresh had failed could be reported as "live" on the strength of
    a second account that has no window to show. One name, two accounts.
    """
    with_windows = [account for account in accounts if account.extra.get("windows")]
    if with_windows:
        sources = [str(a.extra.get("quota_source") or "unknown") for a in with_windows]
        for status in ("live", "cached", "stale"):
            if status in sources:
                return True, status
        return True, "available"
    if any(str(a.extra.get("quota_source") or "") == "error" for a in accounts):
        return False, "error"
    return False, "unavailable"


def provider_capabilities(accounts: Iterable[Account]) -> dict[str, dict[str, dict[str, Any]]]:
    """Declare each provider capability independently from discovered account rows.

    ``supported`` describes the adapter shipped by Sandglass. ``available`` is
    direct evidence present on this machine now. In particular, an account does
    not imply local history, and local history does not imply an account or quota.
    """

    grouped = {provider: [] for provider in PROVIDERS}
    for account in accounts:
        if account.provider in grouped:
            grouped[account.provider].append(account)

    out: dict[str, dict[str, dict[str, Any]]] = {}
    for provider in PROVIDERS:
        provider_accounts = grouped[provider]
        official_accounts = [
            account
            for account in provider_accounts
            if account.extra.get("account_source_official") is True
        ]
        quota_available, quota_status = _quota_status(official_accounts)
        adapter_accounts = [
            account
            for account in provider_accounts
            if account.extra.get("account_source_official") is False
            and str(account.extra.get("account_source") or "").startswith(
                "user_adapter:"
            )
        ]
        if not quota_available:
            adapter_quota_available, adapter_quota_status = _quota_status(adapter_accounts)
            if adapter_quota_available:
                quota_available, quota_status = adapter_quota_available, adapter_quota_status
        discoverable_accounts = official_accounts + adapter_accounts
        local_available = _local_usage_present(provider)
        specs = _SOURCES[provider]
        out[provider] = {
            "account_discovery": {
                **specs["account_discovery"],
                "supported": True,
                "available": bool(discoverable_accounts),
                "status": (
                    "official_available"
                    if official_accounts
                    else "user_adapter_available"
                    if adapter_accounts
                    else "not_observed"
                ),
                "adapter_accounts": len(adapter_accounts),
                "degradation": "signed_out_or_unobserved_accounts_not_listed",
            },
            "local_usage": {
                **specs["local_usage"],
                "supported": True,
                "available": local_available,
                "status": "available" if local_available else "no_official_local_records",
                "degradation": "no_local_usage_reported",
            },
            "official_quota": {
                **specs["official_quota"],
                "supported": True,
                "available": quota_available,
                "status": quota_status,
                "degradation": "local_usage_only",
            },
        }
    return out
