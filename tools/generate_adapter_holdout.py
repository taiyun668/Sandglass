"""Generate a fictional local proxy installation with separately held truth.

The visible tree is what a clean-room adapter agent may inspect.  The hidden
truth is for the evaluator only and must never be placed in the agent's working
directory or virtual user profile.
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


APP_NAME = "Lattice Relay"
SOURCE_ID = "lattice-relay.local"
PROVIDER = "aster"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _json_line(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_lines(path: Path, values: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_json_line(value) for value in values) + "\n", encoding="utf-8")


def _tree_hash(root: Path) -> str:
    rows: list[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{digest} {path.stat().st_size} {relative}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _account_for(activations: list[dict[str, object]], at: datetime) -> str:
    eligible = [
        row
        for row in activations
        if datetime.fromisoformat(str(row["effective_at"]).replace("Z", "+00:00")) <= at
    ]
    if not eligible:
        raise ValueError("usage predates the first activation")
    return str(eligible[-1]["account_ref"])


def generate(
    visible: Path,
    evaluator: Path,
    *,
    seed: int,
    events: int = 240,
) -> dict[str, object]:
    """Create one deterministic holdout with visible evidence and hidden truth."""

    visible = visible.resolve()
    evaluator = evaluator.resolve()
    if visible == evaluator or visible.is_relative_to(evaluator) or evaluator.is_relative_to(visible):
        raise ValueError("visible and evaluator outputs must be separate roots")
    for path, label in ((visible, "visible"), (evaluator, "evaluator")):
        if path.exists() and any(path.iterdir()):
            raise ValueError(f"{label} holdout output must be absent or empty")
        path.mkdir(parents=True, exist_ok=True)
    install = visible / "AppData" / "Local" / "Programs" / "LatticeRelay"
    data = visible / "AppData" / "Local" / "LatticeRelay"
    rng = random.Random(seed)

    accounts = [
        {"account_ref": f"lr-{index}", "label": f"Workspace {index}"}
        for index in range(1, 4)
    ]
    start = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc) + timedelta(
        days=rng.randint(0, 120), minutes=rng.randint(0, 180)
    )
    activations: list[dict[str, object]] = [
        {
            "kind": "account_activated",
            "effective_at": _iso(start - timedelta(minutes=8)),
            "account_ref": accounts[0]["account_ref"],
            "previous_ref": None,
            "transition_id": "tr-000",
        }
    ]
    active = 0
    switch_points = sorted(rng.sample(range(18, max(19, events - 10)), 8))
    switch_by_index: dict[int, datetime] = {}
    cursor = start
    event_times: list[datetime] = []
    for index in range(events):
        cursor += timedelta(seconds=rng.randint(18, 155))
        if index in switch_points:
            outgoing = active
            active = (active + rng.randint(1, len(accounts) - 1)) % len(accounts)
            # The proxy completes activation inside a real no-usage gap.  Some
            # neighboring events still share the same UTC minute, forcing an
            # event-level join rather than a whole-minute owner guess.
            requested = cursor + timedelta(seconds=2)
            effective = requested + timedelta(seconds=rng.randint(4, 16))
            activations.append(
                {
                    "kind": "account_activated",
                    "requested_at": _iso(requested),
                    "effective_at": _iso(effective),
                    "account_ref": accounts[active]["account_ref"],
                    "previous_ref": accounts[outgoing]["account_ref"],
                    "transition_id": f"tr-{len(activations):03d}",
                }
            )
            cursor = effective + timedelta(seconds=rng.randint(3, 18))
            switch_by_index[index] = effective
        event_times.append(cursor)

    requests: list[dict[str, object]] = []
    attempts: list[dict[str, object]] = []
    expected_events: list[dict[str, object]] = []
    account_totals: defaultdict[str, int] = defaultdict(int)
    minute_totals: defaultdict[str, int] = defaultdict(int)
    for index, at in enumerate(event_times):
        request_id = f"req-{seed:08x}-{index:05d}"
        session_id = f"thread-{1 + index // 17:03d}"
        input_tokens = rng.randint(420, 8_600)
        cache_read_tokens = rng.randint(0, input_tokens // 3)
        output_tokens = rng.randint(80, 4_400)
        total_tokens = input_tokens + cache_read_tokens + output_tokens
        row = {
            "kind": "settled_usage",
            "settled_at": _iso(at),
            "request_ref": request_id,
            "thread_ref": session_id,
            "upstream": PROVIDER,
            "meter": {
                "prompt": input_tokens,
                "cache_hit": cache_read_tokens,
                "completion_including_thought": output_tokens,
                "charged_total": total_tokens,
            },
        }
        requests.append(row)
        account = _account_for(activations, at)
        account_totals[account] += total_tokens
        minute_key = at.replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")
        minute_totals[minute_key] += total_tokens
        expected_events.append(
            {
                "provider": PROVIDER,
                "account_ref": account,
                "session_id": session_id,
                "event_id": request_id,
                "timestamp": _iso(at),
                "input_tokens": input_tokens,
                "cache_read_tokens": cache_read_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            }
        )
        if index % 29 == 7:
            attempts.extend(
                [
                    {
                        "at": _iso(at - timedelta(seconds=9)),
                        "request_ref": request_id,
                        "state": "transport_failed",
                        "estimated_tokens": total_tokens,
                    },
                    {
                        "at": _iso(at - timedelta(seconds=3)),
                        "request_ref": request_id,
                        "state": "retry_scheduled",
                        "estimated_tokens": total_tokens,
                    },
                ]
            )

    quota_rows: list[dict[str, object]] = []
    period_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    for account_index, account in enumerate(accounts):
        used = 9.0 + account_index * 7.0
        for sample_index in range(4):
            sample_at = period_start + timedelta(days=sample_index * 2, hours=account_index)
            if sample_index == 3 and account_index == 1:
                used = 4.0  # a real provider-side reset/grant signal
            else:
                used = min(99.0, used + rng.uniform(3.0, 15.0))
            quota_rows.append(
                {
                    "observed_at": _iso(sample_at),
                    "account_ref": account["account_ref"],
                    "period": "weekly",
                    "used_ratio": round(used / 100.0, 6),
                    "renews_at": _iso(period_start + timedelta(days=7)),
                }
            )

    install.mkdir(parents=True, exist_ok=True)
    (install / "README.txt").write_text(
        "Lattice Relay is a local AI proxy.\n\n"
        "Evidence semantics:\n"
        "- usage/settled.ndjson is written by the billing path after a request settles.\n"
        "- control/identity.ndjson is written when an upstream account becomes effective.\n"
        "- attempts.ndjson is transport diagnostics; estimates are not billed usage.\n"
        "- status-counters.csv is process-local and may inherit or reset; it is not a ledger.\n"
        "- quota/snapshots.ndjson contains direct provider responses cached read-only.\n"
        "- completion_including_thought already includes reasoning.\n"
        "- no request is issued between requested_at and effective_at.\n"
        "All timestamps are UTC.\n",
        encoding="utf-8",
    )
    (install / "install.json").write_text(
        json.dumps(
            {
                "product": APP_NAME,
                "version": "0.7.0",
                "data_root": "%LOCALAPPDATA%\\LatticeRelay",
                "source_id": SOURCE_ID,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_lines(data / "usage" / "settled.ndjson", requests)
    _write_lines(data / "control" / "identity.ndjson", activations)
    _write_lines(data / "diagnostics" / "attempts.ndjson", attempts)
    _write_lines(data / "quota" / "snapshots.ndjson", quota_rows)
    counter_path = data / "diagnostics" / "status-counters.csv"
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    with counter_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["observed_at", "process", "token_counter"])
        writer.writeheader()
        inherited = rng.randint(500_000, 900_000)
        running = inherited
        for index, row in enumerate(expected_events[::20]):
            if index == 5:
                running = rng.randint(40_000, 80_000)
            running += int(row["total_tokens"])
            writer.writerow(
                {
                    "observed_at": row["timestamp"],
                    "process": f"relay-{1 + index // 5}",
                    "token_counter": running,
                }
            )

    truth = {
        "schema": 1,
        "seed": seed,
        "source": SOURCE_ID,
        "provider": PROVIDER,
        "events": expected_events,
        "account_totals": dict(sorted(account_totals.items())),
        "minute_totals": dict(sorted(minute_totals.items())),
        "total_tokens": sum(account_totals.values()),
        "latest_quota": {
            row["account_ref"]: row
            for row in sorted(quota_rows, key=lambda value: str(value["observed_at"]))
        },
    }
    (evaluator / "ground-truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "schema": 1,
        "seed": seed,
        "visible_tree_sha256": _tree_hash(visible),
        "visible_files": len([path for path in visible.rglob("*") if path.is_file()]),
        "event_count": len(expected_events),
        "switch_count": len(activations) - 1,
        "account_count": len(accounts),
        "hidden_truth": str((evaluator / "ground-truth.json").resolve()),
    }
    (evaluator / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("visible_output", type=Path)
    parser.add_argument("evaluator_output", type=Path)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--events", type=int, default=240)
    args = parser.parse_args()
    print(
        json.dumps(
            generate(
                args.visible_output,
                args.evaluator_output,
                seed=args.seed,
                events=args.events,
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
