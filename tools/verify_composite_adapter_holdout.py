"""Binary verifier for the composite adapter holdout.

There is no weighted score: every gate must pass for the trial to pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sandglass.telemetry import TelemetryStore, candidate_user_source_ledger
from sandglass.user_sources import UserSourceStore


_DEFAULT_SETTING = {
    "display_enabled": False,
    "accounts_enabled": True,
    "identity_enabled": False,
    "mapped_provider": "",
    "mapped_account_id": "",
    "totals_enabled": False,
    "full_window_enabled": False,
}


def _instant(value: object) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).isoformat()


def _expected_key(row: dict[str, Any]) -> tuple[object, ...]:
    return (
        row["provider"],
        row["account_id"],
        row["session_id"],
        _instant(row["timestamp"]),
        row["input_tokens"],
        row["output_tokens"],
        row["cache_read_tokens"],
        row["total_tokens"],
    )


def _actual_key(row: dict[str, Any]) -> tuple[object, ...]:
    usage = row["usage"]
    return (
        row["provider"],
        row["account_id"],
        row["session_id"],
        _instant(row["event_at"]),
        usage["input_tokens"],
        usage["output_tokens"],
        usage["cache_read_tokens"],
        usage["total_tokens"],
    )


def _profile_unchanged(lab: Path) -> bool:
    manifest = json.loads((lab / "controller" / "lab.json").read_text(encoding="utf-8"))
    root = lab / "virtual-user"
    expected = {
        str(row["path"]): (int(row["size"]), str(row["sha256"]))
        for row in manifest["visible_profile"]
    }
    actual: dict[str, tuple[int, str]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        payload = path.read_bytes()
        actual[path.relative_to(root).as_posix()] = (
            len(payload),
            hashlib.sha256(payload).hexdigest(),
        )
    return actual == expected


def _agent_rollback_evidence(lab: Path) -> bool:
    """Require an executed rollback command, not rollback prose in a reference."""

    for path in sorted((lab / "controller").glob("events*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = value.get("item") if isinstance(value, dict) else None
            if not isinstance(item, dict) or item.get("type") != "command_execution":
                continue
            if item.get("status") != "completed" or item.get("exit_code") != 0:
                continue
            command = str(item.get("command") or "").lower()
            if any(
                marker in command
                for marker in (
                    "/v2/user-source-imports/rollback",
                    "--rollback-source",
                    "user-source rollback",
                )
            ):
                return True
    return False


def verify(
    evaluator: Path,
    state: Path,
    lab: Path,
    *,
    require_agent_rollback: bool = False,
) -> dict[str, object]:
    truth = json.loads((evaluator.resolve() / "ground-truth.json").read_text(encoding="utf-8"))
    state = state.resolve()
    telemetry = TelemetryStore(state / "telemetry.sqlite")
    store = UserSourceStore(state / "user-sources.sqlite")
    records = [
        row
        for row in telemetry.records()
        if str(row.get("source", "")).startswith("user_adapter:")
    ]
    expected = Counter(_expected_key(row) for row in truth["events"])
    actual = Counter(_actual_key(row) for row in records)
    expected_total = int(truth["total_tokens"])
    actual_total = sum(int(row["usage"]["total_tokens"]) for row in records)

    expected_accounts = {
        (str(row["provider"]), str(row["account_id"])) for row in truth["accounts"]
    }
    actual_accounts = {
        (str(row["provider"]), str(row["account_id"])) for row in store.accounts()
    }

    expected_quotas = {
        (str(row["provider"]), str(row["account_id"])): row
        for row in truth["quotas"]
    }
    quota_rows = store.quotas()
    actual_quota_keys = {
        (str(row["provider"]), str(row["account_id"])) for row in quota_rows
    }
    exact_quota_keys: set[tuple[str, str]] = set()
    for row in quota_rows:
        key = (str(row["provider"]), str(row["account_id"]))
        expected_quota = expected_quotas.get(key)
        if expected_quota is None:
            continue
        weekly = next(
            (window for window in row["windows"] if window["label"] == "weekly"),
            None,
        )
        if (
            _instant(row["fetched_at"]) == _instant(expected_quota["observed_at"])
            and weekly
            and round(float(weekly["used_percent"]), 6)
            == round(float(expected_quota["used_ratio"]) * 100, 6)
            and _instant(weekly["resets_at"])
            == _instant(expected_quota["resets_at"])
        ):
            exact_quota_keys.add(key)

    receipts = store.receipts()
    receipt_text = "\n".join(
        json.dumps(row["payload"], ensure_ascii=False, sort_keys=True)
        for row in receipts
    )
    raw_markers = set(str(value) for value in truth["raw_markers"])
    persisted_markers = {marker for marker in raw_markers if marker in receipt_text}

    settings = store.settings()
    contributing_sources = {
        str(row["source"]).removeprefix("user_adapter:") for row in records
    }
    effective_settings = {
        source: {**_DEFAULT_SETTING, **settings.get(source, {})}
        for source in contributing_sources
    }
    controls_ok = bool(contributing_sources) and all(
        not bool(setting["display_enabled"])
        and bool(setting["accounts_enabled"])
        and not bool(setting["identity_enabled"])
        and not str(setting["mapped_provider"])
        and not str(setting["mapped_account_id"])
        and not bool(setting["totals_enabled"])
        and not bool(setting["full_window_enabled"])
        for setting in effective_settings.values()
    )
    stored_only = candidate_user_source_ledger([], settings=settings, store=telemetry)

    receipt_count = len(receipts)
    telemetry_count = len(records)
    admission_ok = False
    round_trip_ok = False
    if contributing_sources and controls_ok:
        with tempfile.TemporaryDirectory() as temporary:
            copied_state = Path(temporary)
            for name in ("telemetry.sqlite", "user-sources.sqlite"):
                source_path = state / name
                if source_path.exists():
                    shutil.copy2(source_path, copied_state / name)
            copied_store = UserSourceStore(copied_state / "user-sources.sqlite")
            copied_telemetry = TelemetryStore(copied_state / "telemetry.sqlite")
            for source in contributing_sources:
                copied_store.configure(
                    source,
                    display_enabled=False,
                    accounts_enabled=True,
                    identity_enabled=False,
                    mapped_provider="",
                    mapped_account_id="",
                    totals_enabled=True,
                    full_window_enabled=False,
                )
            enabled_settings = copied_store.settings()
            enabled = candidate_user_source_ledger(
                [], settings=enabled_settings, store=copied_telemetry
            )
            admission_ok = (
                int(enabled["token_enabled_tokens"]) == expected_total
                and int(enabled["blocked_source_tokens"]) == 0
            )
            for source in contributing_sources:
                copied_store.configure(source, totals_enabled=False)
            disabled_settings = copied_store.settings()
            disabled = candidate_user_source_ledger(
                [], settings=disabled_settings, store=copied_telemetry
            )
            for source in contributing_sources:
                copied_store.configure(source, totals_enabled=True)
            restored_settings = copied_store.settings()
            restored = candidate_user_source_ledger(
                [], settings=restored_settings, store=copied_telemetry
            )
            round_trip_ok = (
                int(stored_only["token_enabled_tokens"]) == 0
                and int(enabled["token_enabled_tokens"]) == expected_total
                and int(disabled["token_enabled_tokens"]) == 0
                and int(restored["token_enabled_tokens"]) == expected_total
                and len(copied_store.receipts()) == receipt_count
                and len(copied_telemetry.records()) == telemetry_count
            )

    gates = {
        "event_set_exact_once": actual == expected,
        "token_total_conserved": actual_total == expected_total,
        "accounts_provider_scoped_exact": actual_accounts == expected_accounts,
        "quotas_latest_exact": (
            actual_quota_keys == set(expected_quotas)
            and exact_quota_keys == set(expected_quotas)
            and len(quota_rows) == len(expected_quotas)
        ),
        "all_native_event_evidence_persisted": persisted_markers == raw_markers,
        "owner_controls_remain_stored_only": controls_ok,
        "product_admits_all_after_explicit_enable": admission_ok,
        "totals_enable_disable_round_trip": round_trip_ok,
        "source_tree_byte_for_byte_unchanged": _profile_unchanged(lab.resolve()),
    }
    if require_agent_rollback:
        active_sources = [
            store.import_status(source).get("source") for source in contributing_sources
        ]
        gates["agent_exercised_retained_revision_rollback"] = (
            _agent_rollback_evidence(lab.resolve())
            and all(
                isinstance(source, dict)
                and bool(source.get("active_revision"))
                and len(source.get("revisions") or []) >= 2
                for source in active_sources
            )
        )
    failures = [name for name, passed in gates.items() if not passed]
    return {
        "schema": 1,
        "result": "PASS" if not failures else "FAIL",
        "gates": gates,
        "failed_gates": failures,
        "observed": {
            "expected_events": sum(expected.values()),
            "actual_events": sum(actual.values()),
            "expected_total_tokens": expected_total,
            "actual_total_tokens": actual_total,
            "expected_accounts": len(expected_accounts),
            "actual_accounts": len(actual_accounts),
            "expected_quotas": len(expected_quotas),
            "actual_quotas": len(quota_rows),
            "raw_markers": len(raw_markers),
            "persisted_raw_markers": len(persisted_markers),
            "stored_only_included_tokens": int(stored_only["token_enabled_tokens"]),
            "stored_only_blocked_tokens": int(stored_only["blocked_source_tokens"]),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evaluator", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("lab", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-agent-rollback", action="store_true")
    args = parser.parse_args()
    result = verify(
        args.evaluator,
        args.state,
        args.lab,
        require_agent_rollback=args.require_agent_rollback,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
