"""Materialize evaluator truth into a controller-only Sandglass state.

This is an instrument check, never an input to a clean-room trial agent.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sandglass.models import TokenUsage
from sandglass.telemetry import TelemetryRecord, TelemetryStore
from sandglass.user_sources import UserSourceStore


def materialize(evaluator: Path, state: Path) -> None:
    truth = json.loads(
        (evaluator.resolve() / "ground-truth.json").read_text(encoding="utf-8")
    )
    state = state.resolve()
    if state.exists() and any(state.iterdir()):
        raise ValueError(f"reference state must be absent or empty: {state}")
    state.mkdir(parents=True, exist_ok=True)
    source = "composite-reference"
    store = UserSourceStore(state / "user-sources.sqlite")
    for event in truth["events"]:
        store.append({"source": source, "payload": event})
    store.append_accounts({"source": source, "accounts": truth["accounts"]})
    store.append_quotas(
        {
            "source": source,
            "quotas": [
                {
                    "provider": row["provider"],
                    "account_id": row["account_id"],
                    "fetched_at": row["observed_at"],
                    "plan": "",
                    "windows": [
                        {
                            "label": "weekly",
                            "used_percent": round(row["used_ratio"] * 100, 6),
                            "resets_at": row["resets_at"],
                        }
                    ],
                }
                for row in truth["quotas"]
            ],
        }
    )
    TelemetryStore(state / "telemetry.sqlite").append(
        [
            TelemetryRecord(
                event_key=f"reference:{row['event_id']}",
                provider=row["provider"],
                event_name="sandglass.usage",
                event_at=row["timestamp"],
                received_at=row["timestamp"],
                account_id=row["account_id"],
                session_id=row["session_id"],
                model="",
                source=f"user_adapter:{source}",
                source_version="reference",
                schema_version="1",
                evidence_grade="U-A",
                coverage_state="user_attributed",
                usage=TokenUsage(
                    input_tokens=row["input_tokens"],
                    output_tokens=row["output_tokens"],
                    cache_read_tokens=row["cache_read_tokens"],
                    calls=1,
                ),
            )
            for row in truth["events"]
        ]
    )
    # Persisting evidence is not owner authorization to display it, supplement
    # identity, include Token, or infer a full window. The binary verifier tests
    # explicit enable/disable on a copied state and leaves this state stored-only.


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evaluator", type=Path)
    parser.add_argument("state", type=Path)
    args = parser.parse_args()
    materialize(args.evaluator, args.state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
