"""Score a persisted adapter trial against evaluator-only holdout truth."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sandglass.telemetry import TelemetryStore, candidate_user_source_ledger
from sandglass.user_sources import UserSourceStore


def _instant(value: object) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).isoformat()


def _expected_key(row: dict[str, Any], *, include_provider: bool = True) -> tuple[object, ...]:
    prefix: tuple[object, ...] = (row["provider"],) if include_provider else ()
    return prefix + (
        row["account_ref"],
        row["session_id"],
        _instant(row["timestamp"]),
        row["input_tokens"],
        row["output_tokens"],
        row["cache_read_tokens"],
        row["total_tokens"],
    )


def _actual_key(row: dict[str, Any], *, include_provider: bool = True) -> tuple[object, ...]:
    usage = row["usage"]
    prefix: tuple[object, ...] = (row["provider"],) if include_provider else ()
    return prefix + (
        row["account_id"],
        row["session_id"],
        _instant(row["event_at"]),
        usage["input_tokens"],
        usage["output_tokens"],
        usage["cache_read_tokens"],
        usage["total_tokens"],
    )


def score(evaluator: Path, state: Path) -> dict[str, object]:
    truth_path = evaluator.resolve() / "ground-truth.json"
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    source = str(truth["source"])
    provider = str(truth["provider"])
    telemetry_store = TelemetryStore(state.resolve() / "telemetry.sqlite")
    all_adapter_records = [
        row
        for row in telemetry_store.records()
        if str(row.get("source", "")).startswith("user_adapter:")
    ]
    source_records = [
        row
        for row in all_adapter_records
        if row["source"] == f"user_adapter:{source}"
    ]
    records = [row for row in source_records if row["provider"] == provider]
    expected = Counter(_expected_key(row) for row in truth["events"])
    actual = Counter(_actual_key(row) for row in records)
    exact = sum((expected & actual).values())
    missing = sum((expected - actual).values())
    unexpected = sum((actual - expected).values())
    expected_count = sum(expected.values())
    actual_count = sum(actual.values())
    content_expected = Counter(
        _expected_key(row, include_provider=False) for row in truth["events"]
    )
    content_actual = Counter(
        _actual_key(row, include_provider=False) for row in source_records
    )
    content_exact = sum((content_expected & content_actual).values())
    source_agnostic_content_actual = Counter(
        _actual_key(row, include_provider=False) for row in all_adapter_records
    )
    source_agnostic_content_exact = sum(
        (content_expected & source_agnostic_content_actual).values()
    )

    store = UserSourceStore(state.resolve() / "user-sources.sqlite")
    account_rows = [
        row
        for row in store.accounts()
        if row["source"] == source and row["provider"] == provider
    ]
    expected_accounts = set(truth["account_totals"])
    actual_accounts = {str(row["account_id"]) for row in account_rows}
    all_source_accounts = {
        str(row["account_id"])
        for row in store.accounts()
        if row["source"] == source
    }
    all_adapter_accounts = {
        str(row["account_id"])
        for row in store.accounts()
    }
    quota_rows = {
        str(row["account_id"]): row
        for row in store.quotas()
        if row["source"] == source and row["provider"] == provider
    }
    quota_exact = 0
    for account_id, expected_quota in truth["latest_quota"].items():
        actual_quota = quota_rows.get(account_id)
        if not actual_quota:
            continue
        weekly = next(
            (window for window in actual_quota["windows"] if window["label"] == "weekly"),
            None,
        )
        if (
            weekly
            and weekly["used_percent"] == round(expected_quota["used_ratio"] * 100, 6)
            and weekly["resets_at"] == expected_quota["renews_at"]
        ):
            quota_exact += 1

    receipts = [row for row in store.receipts() if row["source"] == source]
    all_receipts = store.receipts()
    all_settings = store.settings()
    settings = all_settings.get(source, {})
    candidate = candidate_user_source_ledger(
        [], settings={source: settings}, store=telemetry_store
    )
    source_agnostic_candidate = candidate_user_source_ledger(
        [], settings=all_settings, store=telemetry_store
    )
    expected_total = sum(int(row["total_tokens"]) for row in truth["events"])
    actual_total = sum(
        int(row["usage"]["total_tokens"]) for row in source_records
    )
    source_agnostic_total = sum(
        int(row["usage"]["total_tokens"]) for row in all_adapter_records
    )
    return {
        "schema": 1,
        "event_recall_percent": round(100 * exact / expected_count, 3)
        if expected_count
        else 100.0,
        "event_precision_percent": round(100 * exact / actual_count, 3)
        if actual_count
        else 0.0,
        "missing_event_count": missing,
        "unexpected_event_count": unexpected,
        "event_content_recall_percent": round(
            100 * content_exact / expected_count, 3
        )
        if expected_count
        else 100.0,
        "source_agnostic_event_content_recall_percent": round(
            100 * source_agnostic_content_exact / expected_count, 3
        )
        if expected_count
        else 100.0,
        "wrong_source_record_count": sum(
            1
            for row in all_adapter_records
            if row["source"] != f"user_adapter:{source}"
        ),
        "wrong_provider_record_count": sum(
            1 for row in source_records if row["provider"] != provider
        ),
        "token_conservation": actual_total == expected_total,
        "source_agnostic_token_conservation": (
            source_agnostic_total == expected_total
        ),
        "included_token_conservation": (
            int(candidate["token_enabled_tokens"]) == expected_total
        ),
        "included_token_count": int(candidate["token_enabled_tokens"]),
        "blocked_token_count": int(candidate["blocked_source_tokens"]),
        "source_agnostic_included_token_conservation": (
            int(source_agnostic_candidate["token_enabled_tokens"])
            == expected_total
        ),
        "source_agnostic_included_token_count": int(
            source_agnostic_candidate["token_enabled_tokens"]
        ),
        "source_agnostic_blocked_token_count": int(
            source_agnostic_candidate["blocked_source_tokens"]
        ),
        "account_discovery_percent": round(
            100 * len(expected_accounts & actual_accounts) / len(expected_accounts), 3
        )
        if expected_accounts
        else 100.0,
        "unexpected_account_count": len(actual_accounts - expected_accounts),
        "account_id_discovery_percent": round(
            100 * len(expected_accounts & all_source_accounts) / len(expected_accounts), 3
        )
        if expected_accounts
        else 100.0,
        "source_agnostic_account_id_discovery_percent": round(
            100 * len(expected_accounts & all_adapter_accounts) / len(expected_accounts), 3
        )
        if expected_accounts
        else 100.0,
        "quota_exact_percent": round(100 * quota_exact / len(expected_accounts), 3)
        if expected_accounts
        else 100.0,
        "native_receipt_present": bool(receipts),
        "any_native_receipt_present": bool(all_receipts),
        "wrong_source_receipt_count": sum(
            1 for row in all_receipts if row["source"] != source
        ),
        "source_controls_remain_disabled": not any(
            bool(settings.get(key))
            for key in (
                "display_enabled",
                "identity_enabled",
                "totals_enabled",
                "full_window_enabled",
            )
        ),
        "source_controls_match_owner_policy": (
            not bool(settings.get("display_enabled"))
            and bool(settings.get("accounts_enabled"))
            and bool(settings.get("totals_enabled"))
            and not bool(settings.get("full_window_enabled"))
        ),
        "any_source_controls_match_owner_policy": any(
            not bool(candidate_settings.get("display_enabled"))
            and bool(candidate_settings.get("accounts_enabled"))
            and bool(candidate_settings.get("totals_enabled"))
            and not bool(candidate_settings.get("full_window_enabled"))
            for candidate_settings in all_settings.values()
        ),
        "candidate_record_count": len(source_records),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evaluator", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = score(args.evaluator, args.state)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
