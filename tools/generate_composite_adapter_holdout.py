"""Generate a high-dimensional fictional adapter holdout.

The visible profile deliberately requires composition across several tools.
Evaluator truth is written to a separate root and must never be exposed to the
trial agent.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _line(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_lines(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_line(row) for row in rows) + "\n", encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _rewrite_lines(
    path: Path,
    target_name: str,
    transform,
) -> None:
    target = path.with_name(target_name)
    _write_lines(target, [transform(row) for row in _read_lines(path)])
    if target != path:
        path.unlink()


def _apply_shape_variant_2(visible: Path) -> None:
    """Change source-native shapes without changing any hidden accounting truth."""

    def aster(row: dict[str, Any]) -> dict[str, Any]:
        if "usage" not in row:
            if row.get("type") == "fork_header":
                return {
                    "record_kind": "branch_origin",
                    "parent_thread": row["forked_from"],
                    "opened_on": row["created_at"],
                }
            return {"record_kind": row.get("type", "client_event"), **{k: v for k, v in row.items() if k != "type"}}
        usage = row["usage"]
        return {
            "record_kind": "usage_finalized"
            if row.get("type") == "turn_usage"
            else "usage_snapshot",
            "logged_on": row["recorded_at"],
            "billed_on": row["usage_at"],
            "request_id": row["turn_ref"],
            "thread_id": row["conversation_ref"],
            "is_final": row.get("type") == "turn_usage",
            "meter": {
                "prompt_tokens": usage["input"],
                "cache_hit_tokens": usage["cache_read"],
                "completion_with_reasoning_tokens": usage["output_including_reasoning"],
                "billed_tokens": usage["total"],
            },
            "steps": row.get("iterations", []),
            **(
                {"copied_request": row["replayed_from"]}
                if row.get("replayed_from")
                else {}
            ),
        }

    for path in list((visible / ".aster").rglob("transcript.jsonl")):
        _rewrite_lines(path, "events.logjson", aster)
    for path in list((visible / ".aster").rglob("forked-history.jsonl")):
        _rewrite_lines(path, "branch-journal.logjson", aster)
    for path in list((visible / ".aster").rglob("resume-copy.jsonl")):
        _rewrite_lines(path, "recovery-segment.logjson", aster)
    checkpoint = visible / ".aster" / "tmp" / "active-session" / "usage-checkpoints.jsonl"
    _rewrite_lines(checkpoint, "meter-snapshots.logjson", aster)

    switch_path = visible / "AppData" / "Local" / "SwitchDeck" / "history" / "switches.ndjson"
    _rewrite_lines(
        switch_path,
        "profile-changes.logjson",
        lambda row: {
            "change_ref": row["switch_id"],
            "vendor": row["provider"],
            "requested_on": row["requested_at"],
            "previous_profile": row["from_account"],
            "selected_profile": row["to_account"],
            "outcome": row["status"],
        },
    )
    switch_profiles = visible / "AppData" / "Local" / "SwitchDeck" / "profiles.json"
    value = json.loads(switch_profiles.read_text(encoding="utf-8"))
    _write_json(
        switch_profiles,
        {
            "local_profiles": [
                {
                    "vendor": row["provider"],
                    "profile_ref": row["account_id"],
                    "contact": row["email"],
                    "title": row["label"],
                    "tier": row["plan"],
                }
                for row in value["profiles"]
            ]
        },
    )

    def boreal(row: dict[str, Any]) -> dict[str, Any]:
        if "billing" not in row:
            return {"event_type": row.get("kind", "desktop_event"), **{k: v for k, v in row.items() if k != "kind"}}
        usage = row["billing"]
        return {
            "event_type": "billing_finalized"
            if row.get("kind") == "completed_generation"
            else "billing_update",
            "finished_on": row["completed_at"],
            "request_ref": row["generation_id"],
            "conversation_id": row["session"],
            "profile_ref": row["account"],
            "metering": {
                "prompt_tokens": usage["input"],
                "cache_hit_tokens": usage["cached"],
                "completion_tokens": usage["output"],
                "billed_tokens": usage["total"],
            },
        }

    boreal_root = visible / "AppData" / "Roaming" / "BorealDesktop"
    for path in list(boreal_root.rglob("events.jsonl")):
        _rewrite_lines(path, "journal.ndjson", boreal)
    profile = json.loads((boreal_root / "profile.json").read_text(encoding="utf-8"))
    _write_json(
        boreal_root / "profile.json",
        {
            "profile_ref": profile["account_id"],
            "contact": profile["email"],
            "title": profile["label"],
            "tier": profile["plan"],
        },
    )

    def cinder(row: dict[str, Any]) -> dict[str, Any]:
        usage = row.get("usage_final") or row.get("usage_so_far")
        if usage is None:
            return {"record_type": row.get("type", "client_event"), **{k: v for k, v in row.items() if k != "type"}}
        return {
            "record_type": "response_settled"
            if row.get("type") == "response_complete"
            else "response_snapshot",
            "event_id": row["event_ref"],
            "happened_on": row["occurred_at"],
            "run_id": row["session_ref"],
            "metering": {
                "prompt_tokens": usage["input"],
                "cache_hit_tokens": usage["cache_read"],
                "completion_tokens": usage["output"],
                "billed_tokens": usage["total"],
            },
            **({"copy_ref": row["mirror_id"]} if row.get("mirror_id") else {}),
        }

    for root in (
        visible / ".cinder",
        visible / "AppData" / "Roaming" / "CinderDesk",
    ):
        for path in list(root.rglob("stream.jsonl")):
            _rewrite_lines(path, "records.logjson", cinder)
    activation = visible / ".cinder" / "auth" / "activation-events.ndjson"
    _rewrite_lines(
        activation,
        "profile-selections.logjson",
        lambda row: {
            "observed_on": row["at"],
            "profile_ref": row["account_id"],
            "phase": row["state"],
        },
    )
    accounts_path = visible / ".cinder" / "accounts.json"
    account_value = json.loads(accounts_path.read_text(encoding="utf-8"))
    _write_json(
        accounts_path,
        {
            "profiles": [
                {
                    "profile_ref": row["account_id"],
                    "contact": row["email"],
                    "title": row["label"],
                    "tier": row["plan"],
                }
                for row in account_value["accounts"]
            ]
        },
    )

    relay_path = next((visible / "AppData" / "Local" / "LatticeRelay").rglob("settled-requests.jsonl"))
    _rewrite_lines(
        relay_path,
        "charges.logjson",
        lambda row: {
            "status": "charged",
            "charged_on": row["settled_at"],
            "relay_id": row["relay_request"],
            "provider_event": row["upstream_request"],
            "provider": row["upstream"],
            "conversation": row["thread"],
            "usage": {
                "prompt_tokens": row["meter"]["prompt"],
                "cache_hit_tokens": row["meter"]["cache"],
                "completion_tokens": row["meter"]["completion"],
                "billed_tokens": row["meter"]["charged"],
            },
        },
    )

    for path in (
        visible / ".aster" / "quota" / "responses.ndjson",
        boreal_root / "quota" / "responses.ndjson",
        visible / ".cinder" / "quota" / "responses.ndjson",
    ):
        _rewrite_lines(
            path,
            "periods.logjson",
            lambda row: {
                "vendor": row["provider"],
                "profile_ref": row["account_id"],
                "retrieved_on": row["observed_at"],
                "period": row["window"],
                "used_fraction": row["used_ratio"],
                "renews_on": row["resets_at"],
            },
        )


def _stable_folder(seed: int, label: str) -> str:
    return hashlib.sha256(f"{seed}:{label}".encode()).hexdigest()[:16]


def _partial(value: int, numerator: int = 2, denominator: int = 5) -> int:
    return max(0, value * numerator // denominator)


def _tree_hash(root: Path) -> str:
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rows.append(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()} "
            f"{path.stat().st_size} {path.relative_to(root).as_posix()}"
        )
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def _require_empty(path: Path, label: str) -> Path:
    path = path.resolve()
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"{label} output must be absent or empty: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _account_at(transitions: list[dict[str, Any]], at: datetime) -> str:
    eligible = [
        row
        for row in transitions
        if row.get("status") == "effective"
        and datetime.fromisoformat(str(row["effective_at"]).replace("Z", "+00:00")) <= at
    ]
    if not eligible:
        raise ValueError("event predates first effective identity")
    return str(eligible[-1]["to_account"])


def _usage(rng: random.Random, *, large: bool = False) -> dict[str, int]:
    factor = 15 if large else 1
    input_tokens = rng.randint(260, 3_200) * factor
    cache_read = rng.randint(0, max(1, input_tokens // 4))
    output = rng.randint(70, 1_900) * factor
    return {
        "input_tokens": input_tokens,
        "cache_read_tokens": cache_read,
        "output_tokens": output,
        "total_tokens": input_tokens + cache_read + output,
    }


def generate(
    visible: Path,
    evaluator: Path,
    *,
    seed: int,
    shape_variant: int = 1,
) -> dict[str, object]:
    visible = _require_empty(visible, "visible")
    evaluator = _require_empty(evaluator, "evaluator")
    if visible == evaluator or visible.is_relative_to(evaluator) or evaluator.is_relative_to(visible):
        raise ValueError("visible and evaluator roots must be separate")
    if shape_variant not in {1, 2}:
        raise ValueError("shape_variant must be 1 or 2")
    rng = random.Random(seed)
    base = datetime(2026, 4, 6, 9, 0, tzinfo=timezone.utc) + timedelta(
        days=rng.randint(0, 80), minutes=rng.randint(0, 40)
    )
    home = visible
    local = home / "AppData" / "Local"
    programs = local / "Programs"
    events: list[dict[str, Any]] = []
    raw_markers: set[str] = set()
    accounts: dict[tuple[str, str], dict[str, str]] = {}
    quotas: dict[tuple[str, str], dict[str, Any]] = {}

    def install(folder: str, product: str, component: str) -> None:
        _write_json(
            programs / folder / "install.json",
            {
                "product": product,
                "version": "1.0.0",
                "component": component,
                "channel": "stable",
            },
        )

    def add_event(
        provider: str,
        account_id: str,
        session_id: str,
        event_id: str,
        at: datetime,
        usage: dict[str, int],
    ) -> dict[str, Any]:
        event = {
            "provider": provider,
            "account_id": account_id,
            "session_id": session_id,
            "event_id": event_id,
            "timestamp": _iso(at),
            **usage,
        }
        events.append(event)
        raw_markers.add(event_id)
        return event

    shared_email = "same-person@synthetic.invalid"

    # Aster: official usage lacks identity; SwitchDeck carries effective account
    # transitions. Includes a same-session minute storm plus rapid A->B->A.
    install("AsterCLI", "Aster CLI", "official-executor")
    install("SwitchDeck", "SwitchDeck", "account-manager")
    aster_accounts = ["aster-red", "aster-blue", "aster-green"]
    for index, account_id in enumerate(aster_accounts):
        accounts[("aster", account_id)] = {
            "email": shared_email if index == 0 else f"aster-{index}@synthetic.invalid",
            "label": f"Aster workspace {index + 1}",
            "plan": "pro",
        }
    transitions: list[dict[str, Any]] = []
    active = aster_accounts[0]
    transitions.append(
        {
            "switch_id": "sw-aster-000",
            "provider": "aster",
            "requested_at": _iso(base - timedelta(minutes=5, seconds=12)),
            "effective_at": _iso(base - timedelta(minutes=5)),
            "from_account": None,
            "to_account": active,
            "status": "effective",
        }
    )
    switch_times = [
        base + timedelta(minutes=7, seconds=8),
        base + timedelta(minutes=18, seconds=12),
        base + timedelta(minutes=19, seconds=4),
        base + timedelta(minutes=20, seconds=3),
        base + timedelta(minutes=47, seconds=22),
    ]
    targets = [aster_accounts[1], aster_accounts[2], aster_accounts[0], aster_accounts[2], aster_accounts[1]]
    for index, (at, target) in enumerate(zip(switch_times, targets), start=1):
        requested = at - timedelta(seconds=rng.randint(5, 13))
        transitions.append(
            {
                "switch_id": f"sw-aster-{index:03d}",
                "provider": "aster",
                "requested_at": _iso(requested),
                "effective_at": _iso(at),
                "from_account": active,
                "to_account": target,
                "status": "effective",
            }
        )
        active = target
    transitions.insert(
        3,
        {
            "switch_id": "sw-aster-cancelled",
            "provider": "aster",
            "requested_at": _iso(base + timedelta(minutes=15)),
            "effective_at": None,
            "from_account": aster_accounts[1],
            "to_account": aster_accounts[0],
            "status": "cancelled",
        },
    )
    visible_transitions = [
        {
            "switch_id": row["switch_id"],
            "provider": row["provider"],
            "requested_at": row["requested_at"],
            "from_account": row["from_account"],
            "to_account": row["to_account"],
            "status": row["status"],
        }
        for row in transitions
    ]
    _write_lines(
        local / "SwitchDeck" / "history" / "switches.ndjson",
        visible_transitions,
    )
    _write_json(
        local / "SwitchDeck" / "profiles.json",
        {
            "profiles": [
                {"provider": provider, "account_id": account_id, **details}
                for (provider, account_id), details in accounts.items()
                if provider == "aster"
            ]
        },
    )
    aster_rows: list[dict[str, Any]] = []
    cursor = base
    for index in range(94):
        cursor += timedelta(seconds=rng.randint(24, 68))
        if index and index % 19 == 0:
            cursor = (cursor + timedelta(minutes=1)).replace(second=2, microsecond=0)
        # Keep real work outside account-transition seconds.
        for switch_at in switch_times:
            if abs((cursor - switch_at).total_seconds()) < 15:
                cursor = (switch_at + timedelta(minutes=1)).replace(second=2, microsecond=0)
        event_id = f"as-{seed:x}-{index:04d}"
        usage = _usage(rng, large=index in {33, 34})
        account_id = _account_at(transitions, cursor)
        add_event("aster", account_id, f"as-chat-{index // 19:02d}", event_id, cursor, usage)
        aster_rows.append(
            {
                "type": "turn_usage",
                "recorded_at": _iso(cursor + timedelta(milliseconds=rng.randint(20, 900))),
                "usage_at": _iso(cursor),
                "turn_ref": event_id,
                "conversation_ref": f"as-chat-{index // 19:02d}",
                "usage": {
                    "input": usage["input_tokens"],
                    "cache_read": usage["cache_read_tokens"],
                    "output_including_reasoning": usage["output_tokens"],
                    "total": usage["total_tokens"],
                },
                "iterations": [
                    {
                        "input": usage["input_tokens"],
                        "output_without_reasoning": max(0, usage["output_tokens"] - rng.randint(5, 60)),
                    }
                ],
            }
        )
    storm_minute = (cursor + timedelta(minutes=2)).replace(second=0, microsecond=0)
    storm_account = _account_at(transitions, storm_minute)
    for index in range(48):
        at = storm_minute + timedelta(seconds=5 + index, milliseconds=(index * 137) % 1000)
        event_id = f"as-storm-{seed:x}-{index:03d}"
        usage = _usage(rng, large=index % 11 == 0)
        add_event("aster", storm_account, "as-storm-session", event_id, at, usage)
        aster_rows.append(
            {
                "type": "turn_usage",
                "recorded_at": _iso(at + timedelta(milliseconds=50)),
                "usage_at": _iso(at),
                "turn_ref": event_id,
                "conversation_ref": "as-storm-session",
                "usage": {
                    "input": usage["input_tokens"],
                    "cache_read": usage["cache_read_tokens"],
                    "output_including_reasoning": usage["output_tokens"],
                    "total": usage["total_tokens"],
                },
                "iterations": [{"input": usage["input_tokens"], "output_without_reasoning": usage["output_tokens"] // 2}],
            }
        )
    # The complete official history is split by workspace and session. Each
    # stream mixes provisional usage, final billed usage and unrelated client
    # events. Shallow caches below are real but intentionally incomplete.
    aster_workspace = _stable_folder(seed, "aster-workspace")
    by_aster_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(aster_rows):
        usage = row["usage"]
        by_aster_session[str(row["conversation_ref"])].extend(
            [
                {
                    "type": "assistant_progress",
                    "recorded_at": row["recorded_at"],
                    "usage_at": row["usage_at"],
                    "turn_ref": row["turn_ref"],
                    "conversation_ref": row["conversation_ref"],
                    "final": False,
                    "usage": {
                        "input": _partial(int(usage["input"])),
                        "cache_read": _partial(int(usage["cache_read"])),
                        "output_including_reasoning": _partial(
                            int(usage["output_including_reasoning"])
                        ),
                        "total": _partial(int(usage["total"])),
                    },
                },
                row,
            ]
        )
        if index % 11 == 0:
            by_aster_session[str(row["conversation_ref"])].insert(
                -1,
                {
                    "type": "tool_status",
                    "recorded_at": row["recorded_at"],
                    "conversation_ref": row["conversation_ref"],
                    "state": "completed",
                },
            )
    for session, rows in by_aster_session.items():
        session_folder = _stable_folder(seed, session)
        _write_lines(
            home
            / ".aster"
            / "projects"
            / aster_workspace
            / "sessions"
            / "2026"
            / f"{base.month:02d}"
            / session_folder
            / "transcript.jsonl",
            rows,
        )

    _write_json(
        home / ".aster" / "state" / "stats-cache.json",
        {
            "kind": "dashboard_cache",
            "coverage": "recent_only",
            "updated_at": aster_rows[-1]["recorded_at"],
            "sessions": len(by_aster_session),
            "total_tokens": sum(int(row["usage"]["total"]) for row in aster_rows[-18:]),
        },
    )
    aster_checkpoint_rows = []
    for row in aster_rows[-7:]:
        checkpoint = dict(row)
        checkpoint["type"] = "turn_checkpoint"
        checkpoint["final"] = False
        checkpoint["usage"] = {
            key: _partial(int(value), 3, 4) for key, value in row["usage"].items()
        }
        aster_checkpoint_rows.append(checkpoint)
    _write_lines(
        home / ".aster" / "tmp" / "active-session" / "usage-checkpoints.jsonl",
        aster_checkpoint_rows,
    )
    _write_lines(
        home
        / ".aster"
        / "recovery"
        / aster_workspace
        / "snapshots"
        / "resume-copy.jsonl",
        aster_rows[21:37],
    )
    _write_lines(
        home / ".aster" / "logs" / "recent-results.jsonl",
        [
            {
                "type": "result_summary",
                "conversation_ref": session,
                "recorded_at": rows[-1]["recorded_at"],
                "total_tokens": sum(
                    int(row["usage"]["total"])
                    for row in rows
                    if row.get("type") == "turn_usage"
                ),
            }
            for session, rows in list(by_aster_session.items())[-2:]
        ],
    )
    replay = [
        {"type": "fork_header", "forked_from": "as-chat-01", "created_at": _iso(storm_minute + timedelta(minutes=4))}
    ]
    for index, row in enumerate(aster_rows[19:31]):
        copied = dict(row)
        copied["recorded_at"] = _iso(storm_minute + timedelta(minutes=4, seconds=index))
        copied["turn_ref"] = f"fork-copy-{index:03d}"
        copied["replayed_from"] = row["turn_ref"]
        replay.append(copied)
    _write_lines(
        home
        / ".aster"
        / "projects"
        / aster_workspace
        / "branches"
        / _stable_folder(seed, "fork")
        / "forked-history.jsonl",
        replay,
    )

    # Boreal: one provider account used through the official desktop and the
    # relay. Some rows overlap and some exist in only one tool.
    install("BorealDesktop", "Boreal Desktop", "official-desktop")
    install("LatticeRelay", "Lattice Relay", "local-proxy")
    boreal_id = "boreal-primary"
    accounts[("boreal", boreal_id)] = {
        "email": shared_email,
        "label": "Boreal personal",
        "plan": "plus",
    }
    _write_json(home / "AppData" / "Roaming" / "BorealDesktop" / "profile.json", {"account_id": boreal_id, **accounts[("boreal", boreal_id)]})
    boreal_official: list[dict[str, Any]] = []
    relay_rows: list[dict[str, Any]] = []
    cursor = base + timedelta(days=1)
    for index in range(108):
        cursor += timedelta(seconds=rng.randint(19, 91))
        if index and index % 23 == 0:
            cursor = (cursor + timedelta(minutes=1)).replace(second=2, microsecond=0)
        event_id = f"bo-{seed:x}-{index:04d}"
        usage = _usage(rng, large=index in {51, 52})
        session = f"bo-thread-{index // 23:02d}"
        add_event("boreal", boreal_id, session, event_id, cursor, usage)
        official = {
            "kind": "completed_generation",
            "completed_at": cursor.astimezone(timezone(timedelta(hours=-7))).isoformat(timespec="milliseconds"),
            "generation_id": event_id,
            "session": session,
            "account": boreal_id,
            "billing": {
                "input": usage["input_tokens"],
                "cached": usage["cache_read_tokens"],
                "output": usage["output_tokens"],
                "total": usage["total_tokens"],
            },
        }
        relay = {
            "kind": "settled",
            "settled_at": _iso(cursor),
            "relay_request": f"lr-bo-{seed:x}-{index:04d}",
            "upstream_request": event_id,
            "upstream": "boreal",
            "thread": session,
            "meter": {
                "prompt": usage["input_tokens"],
                "cache": usage["cache_read_tokens"],
                "completion": usage["output_tokens"],
                "charged": usage["total_tokens"],
            },
        }
        if index < 88:
            boreal_official.append(official)
        if index >= 28:
            relay_rows.append(relay)
    boreal_profile = _stable_folder(seed, "boreal-profile")
    by_boreal_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(boreal_official):
        billing = row["billing"]
        by_boreal_session[str(row["session"])].extend(
            [
                {
                    "kind": "generation_progress",
                    "completed_at": row["completed_at"],
                    "generation_id": row["generation_id"],
                    "session": row["session"],
                    "account": row["account"],
                    "billing": {
                        key: _partial(int(value)) for key, value in billing.items()
                    },
                },
                row,
            ]
        )
        if index % 13 == 0:
            by_boreal_session[str(row["session"])].insert(
                -1,
                {
                    "kind": "ui_checkpoint",
                    "at": row["completed_at"],
                    "session": row["session"],
                },
            )
    for session, rows in by_boreal_session.items():
        _write_lines(
            home
            / "AppData"
            / "Roaming"
            / "BorealDesktop"
            / "Profiles"
            / boreal_profile
            / "workspaces"
            / _stable_folder(seed, session)
            / "history"
            / "2026"
            / f"{base.month:02d}"
            / "events.jsonl",
            rows,
        )
    _write_json(
        home / "AppData" / "Roaming" / "BorealDesktop" / "Cache" / "usage-summary.json",
        {
            "kind": "recent_usage_cache",
            "updated_at": boreal_official[-1]["completed_at"],
            "total_tokens": sum(
                int(row["billing"]["total"]) for row in boreal_official[-12:]
            ),
            "coverage": "last_opened_threads",
        },
    )
    _write_lines(
        home
        / "AppData"
        / "Roaming"
        / "BorealDesktop"
        / "TempState"
        / "pending-generations.jsonl",
        [
            {
                "kind": "generation_pending",
                "generation_id": row["generation_id"],
                "session": row["session"],
                "estimated_tokens": int(row["billing"]["total"]) + rng.randint(10, 900),
                "at": row["completed_at"],
            }
            for row in boreal_official[-9:]
        ],
    )

    # Cinder: two accounts, two first-party clients, overlapping coverage and
    # provider-scoped identity that shares a human label with other providers.
    install("CinderCLI", "Cinder CLI", "official-cli")
    install("CinderDesk", "Cinder Desktop", "official-desktop")
    cinder_accounts = ["cinder-one", "cinder-two"]
    for index, account_id in enumerate(cinder_accounts):
        accounts[("cinder", account_id)] = {
            "email": shared_email if index == 0 else "cinder-two@synthetic.invalid",
            "label": f"Cinder {index + 1}",
            "plan": "team",
        }
    cinder_switches = [
        {"at": _iso(base + timedelta(days=2, minutes=-3)), "account_id": cinder_accounts[0], "state": "active"},
        {"at": _iso(base + timedelta(days=2, minutes=29, seconds=17)), "account_id": cinder_accounts[1], "state": "active"},
        {"at": _iso(base + timedelta(days=2, minutes=30, seconds=8)), "account_id": cinder_accounts[0], "state": "active"},
        {"at": _iso(base + timedelta(days=2, minutes=31, seconds=2)), "account_id": cinder_accounts[1], "state": "active"},
    ]
    _write_lines(home / ".cinder" / "auth" / "activation-events.ndjson", cinder_switches)
    _write_json(home / ".cinder" / "accounts.json", {"accounts": [{"account_id": aid, **accounts[("cinder", aid)]} for aid in cinder_accounts]})
    cinder_cli: list[dict[str, Any]] = []
    cinder_desk: list[dict[str, Any]] = []
    cursor = base + timedelta(days=2)
    effective_rows = [
        {"status": "effective", "effective_at": row["at"], "to_account": row["account_id"]}
        for row in cinder_switches
    ]
    for index in range(96):
        cursor += timedelta(seconds=rng.randint(22, 74))
        if index and index % 16 == 0:
            cursor = (cursor + timedelta(minutes=1)).replace(second=2, microsecond=0)
        for row in cinder_switches[1:]:
            at = datetime.fromisoformat(row["at"].replace("Z", "+00:00"))
            if abs((cursor - at).total_seconds()) < 12:
                cursor = (at + timedelta(minutes=1)).replace(second=2, microsecond=0)
        account_id = _account_at(effective_rows, cursor)
        event_id = f"ci-{seed:x}-{index:04d}"
        usage = _usage(rng, large=index == 71)
        session = f"ci-work-{index // 16:02d}"
        add_event("cinder", account_id, session, event_id, cursor, usage)
        common = {
            "event_ref": event_id,
            "occurred_at": _iso(cursor),
            "session_ref": session,
            "usage_final": {
                "input": usage["input_tokens"],
                "cache_read": usage["cache_read_tokens"],
                "output": usage["output_tokens"],
                "total": usage["total_tokens"],
            },
        }
        if index < 74:
            cinder_cli.append({"type": "response_complete", **common})
        if index >= 39:
            cinder_desk.append({"type": "response_complete", "mirror_id": f"desk-{event_id}", **common})
    def write_cinder_streams(
        root: Path, rows: list[dict[str, Any]], *, label: str
    ) -> None:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for index, row in enumerate(rows):
            usage = row["usage_final"]
            grouped[str(row["session_ref"])].extend(
                [
                    {
                        "type": "response_delta",
                        "event_ref": row["event_ref"],
                        "occurred_at": row["occurred_at"],
                        "session_ref": row["session_ref"],
                        "usage_so_far": {
                            key: _partial(int(value)) for key, value in usage.items()
                        },
                    },
                    row,
                ]
            )
            if index % 17 == 0:
                grouped[str(row["session_ref"])].insert(
                    -1,
                    {
                        "type": "tool_result",
                        "event_ref": f"tool-{row['event_ref']}",
                        "occurred_at": row["occurred_at"],
                        "session_ref": row["session_ref"],
                    },
                )
        for session, stream in grouped.items():
            _write_lines(
                root
                / "state"
                / "workspaces"
                / _stable_folder(seed, f"{label}:{session}")
                / "runs"
                / "2026"
                / f"{base.month:02d}"
                / _stable_folder(seed, session)
                / "stream.jsonl",
                stream,
            )

    write_cinder_streams(home / ".cinder", cinder_cli, label="cli")
    write_cinder_streams(
        home / "AppData" / "Roaming" / "CinderDesk" / "Profiles" / "default",
        cinder_desk,
        label="desktop",
    )
    _write_json(
        home / ".cinder" / "cache" / "recent-usage.json",
        {
            "kind": "recent_session_cache",
            "coverage": "partial",
            "events": [
                {
                    "event_ref": row["event_ref"],
                    "session_ref": row["session_ref"],
                    "total_tokens": row["usage_final"]["total"],
                }
                for row in cinder_cli[-10:]
            ],
        },
    )
    _write_json(
        home / ".cinder" / "tmp" / "last-response.json",
        {
            "type": "response_checkpoint",
            "event_ref": cinder_cli[-1]["event_ref"],
            "session_ref": cinder_cli[-1]["session_ref"],
            "usage_so_far": {
                key: _partial(int(value), 4, 5)
                for key, value in cinder_cli[-1]["usage_final"].items()
            },
        },
    )

    # Relay rows include the Boreal overlap/non-overlap plus failed estimates
    # and reset-prone process counters as deliberate non-billing decoys.
    _write_lines(
        local
        / "LatticeRelay"
        / "state"
        / "providers"
        / "boreal"
        / "ledgers"
        / "2026"
        / f"{base.month:02d}"
        / "settled-requests.jsonl",
        relay_rows,
    )
    attempts = []
    for index, row in enumerate(relay_rows[::9]):
        attempts.extend(
            [
                {"at": row["settled_at"], "request": row["relay_request"], "state": "failed", "estimated_tokens": row["meter"]["charged"]},
                {"at": row["settled_at"], "request": row["relay_request"], "state": "retrying", "estimated_tokens": row["meter"]["charged"]},
            ]
        )
    _write_lines(local / "LatticeRelay" / "diagnostics" / "attempts.ndjson", attempts)
    _write_json(
        local / "LatticeRelay" / "cache" / "dashboard-usage.json",
        {
            "kind": "dashboard_cache",
            "coverage": "recent_only",
            "token_total": sum(
                int(row["meter"]["charged"]) for row in relay_rows[-14:]
            ),
            "requests": len(relay_rows[-14:]),
        },
    )
    counter = local / "LatticeRelay" / "diagnostics" / "process-counters.csv"
    counter.parent.mkdir(parents=True, exist_ok=True)
    with counter.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["at", "process", "token_counter"])
        writer.writeheader()
        running = rng.randint(700_000, 1_100_000)
        for index, event in enumerate(events[::31]):
            if index in {3, 7}:
                running = rng.randint(20_000, 60_000)
            running += int(event["total_tokens"])
            writer.writerow({"at": event["timestamp"], "process": f"relay-{index // 3}", "token_counter": running})

    # First-party quota snapshots: latest observation wins per provider/account;
    # one account has a real grant/reset between samples.
    quota_files: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for account_index, ((provider, account_id), details) in enumerate(sorted(accounts.items())):
        used = 12.0 + account_index * 5
        for sample in range(4):
            observed = base + timedelta(days=sample * 2, hours=account_index)
            if sample == 3 and account_index in {1, 4}:
                used = 3.5 + account_index
            else:
                used = min(98.0, used + rng.uniform(4, 13))
            row = {
                "provider": provider,
                "account_id": account_id,
                "observed_at": _iso(observed),
                "window": "weekly",
                "used_ratio": round(used / 100, 6),
                "resets_at": _iso(base.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=7 + account_index)),
            }
            quota_files[provider].append(row)
            quotas[(provider, account_id)] = row
    _write_lines(home / ".aster" / "quota" / "responses.ndjson", quota_files["aster"])
    _write_lines(home / "AppData" / "Roaming" / "BorealDesktop" / "quota" / "responses.ndjson", quota_files["boreal"])
    _write_lines(home / ".cinder" / "quota" / "responses.ndjson", quota_files["cinder"])

    # Sparse product notes identify installed roles but do not disclose a state
    # root, ledger path, join, deduplication or authority solution.
    for folder, text in {
        "AsterCLI": "Aster CLI keeps workspace-scoped local state for offline continuity.\n",
        "SwitchDeck": "SwitchDeck manages local provider profiles and keeps transition history.\n",
        "BorealDesktop": "Boreal Desktop keeps profile-scoped local state and caches.\n",
        "LatticeRelay": "Lattice Relay is a local multi-provider proxy with usage and transport diagnostics.\n",
        "CinderCLI": "Cinder CLI keeps workspace state for recovery and history.\n",
        "CinderDesk": "Cinder Desktop keeps profile and workspace state.\n",
    }.items():
        (programs / folder / "README.txt").write_text(text, encoding="utf-8")

    if shape_variant == 2:
        _apply_shape_variant_2(visible)

    expected_accounts = [
        {"provider": provider, "account_id": account_id, **details}
        for (provider, account_id), details in sorted(accounts.items())
    ]
    truth = {
        "schema": 1,
        "level": "composite-1",
        "seed": seed,
        "events": sorted(events, key=lambda row: (row["timestamp"], row["event_id"])),
        "accounts": expected_accounts,
        "quotas": [quotas[key] for key in sorted(quotas)],
        "transitions": transitions,
        "raw_markers": sorted(raw_markers),
        "total_tokens": sum(int(row["total_tokens"]) for row in events),
        "difficulty": [
            "multi_provider",
            "multi_account",
            "single_account_multi_tool",
            "multi_account_multi_tool",
            "provider_scoped_same_identity",
            "official_usage_plus_switch_history",
            "approximate_switch_time_reanchoring",
            "rapid_switches",
            "minute_storm",
            "fork_replay_new_timestamps",
            "resume_replay_original_timestamps",
            "cross_tool_overlap",
            "failed_attempt_decoys",
            "reset_prone_counters",
            "incomplete_nested_usage",
            "deep_partitioned_history",
            "mixed_record_streams",
            "shallow_partial_usage_caches",
            "temporary_token_decoys",
            "recovery_duplicate_fragments",
            "quota_reset_or_grant",
            *(["structural_shape_variant_2"] if shape_variant == 2 else []),
        ],
    }
    _write_json(evaluator / "ground-truth.json", truth)
    manifest = {
        "schema": 1,
        "level": "composite-1",
        "seed": seed,
        "shape_variant": shape_variant,
        "visible_tree_sha256": _tree_hash(visible),
        "visible_files": sum(path.is_file() for path in visible.rglob("*")),
        "visible_max_depth": max(
            len(path.relative_to(visible).parts) - 1
            for path in visible.rglob("*")
            if path.is_file()
        ),
        "event_count": len(events),
        "account_count": len(accounts),
        "provider_count": len({row["provider"] for row in events}),
        "difficulty_count": len(truth["difficulty"]),
        "hidden_truth": str((evaluator / "ground-truth.json").resolve()),
    }
    _write_json(evaluator / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("visible_output", type=Path)
    parser.add_argument("evaluator_output", type=Path)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--shape-variant", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    print(
        json.dumps(
            generate(
                args.visible_output,
                args.evaluator_output,
                seed=args.seed,
                shape_variant=args.shape_variant,
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
