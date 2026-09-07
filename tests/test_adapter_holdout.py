import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from sandglass.telemetry import TelemetryRecord, TelemetryStore
from sandglass.models import TokenUsage
from sandglass.user_sources import UserSourceStore
from tools.generate_adapter_holdout import generate
from tools.score_adapter_holdout import score


class AdapterHoldoutTests(unittest.TestCase):
    def test_seed_is_deterministic_and_truth_is_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = generate(
                root / "first-visible", root / "first-hidden", seed=71531, events=90
            )
            second = generate(
                root / "second-visible", root / "second-hidden", seed=71531, events=90
            )
            self.assertEqual(first["visible_tree_sha256"], second["visible_tree_sha256"])
            self.assertEqual(first["event_count"], 90)
            self.assertEqual(first["account_count"], 3)
            visible = root / "first-visible"
            hidden = root / "first-hidden"
            self.assertFalse((visible / "ground-truth.json").exists())
            self.assertTrue((hidden / "ground-truth.json").exists())

    def test_visible_usage_requires_an_event_level_identity_join(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            visible = root / "visible"
            hidden = root / "hidden"
            generate(visible, hidden, seed=88421, events=160)
            data = visible / "AppData" / "Local" / "LatticeRelay"
            requests = [
                json.loads(line)
                for line in (data / "usage" / "settled.ndjson").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            identities = [
                json.loads(line)
                for line in (data / "control" / "identity.ndjson").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertTrue(requests)
            self.assertGreater(len(identities), 1)
            self.assertTrue(all("account_ref" not in row for row in requests))
            self.assertTrue(all("meter" in row for row in requests))
            truth = json.loads(
                (hidden / "ground-truth.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                sum(row["meter"]["charged_total"] for row in requests),
                truth["total_tokens"],
            )
            self.assertEqual(
                sum(truth["account_totals"].values()), truth["total_tokens"]
            )

    def test_diagnostic_estimates_are_deliberate_non_billing_decoys(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            visible = root / "visible"
            hidden = root / "hidden"
            generate(visible, hidden, seed=93211, events=120)
            data = visible / "AppData" / "Local" / "LatticeRelay"
            attempts = [
                json.loads(line)
                for line in (data / "diagnostics" / "attempts.ndjson").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertTrue(attempts)
            self.assertTrue(all(row["estimated_tokens"] > 0 for row in attempts))
            readme = (
                visible
                / "AppData"
                / "Local"
                / "Programs"
                / "LatticeRelay"
                / "README.txt"
            ).read_text(encoding="utf-8")
            self.assertIn("estimates are not billed usage", readme)
            self.assertNotIn("latest effective activation", readme)
            self.assertNotIn("assign a whole minute", readme)

    def test_hidden_scorer_measures_persisted_exact_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            visible = root / "visible"
            hidden = root / "hidden"
            state = root / "state"
            generate(visible, hidden, seed=44771, events=36)
            truth = json.loads(
                (hidden / "ground-truth.json").read_text(
                    encoding="utf-8"
                )
            )
            source = truth["source"]
            store = UserSourceStore(state / "user-sources.sqlite")
            store.append({"source": source, "payload": {"native": "preserved"}})
            store.append_accounts(
                {
                    "source": source,
                    "accounts": [
                        {
                            "provider": truth["provider"],
                            "account_id": account_id,
                            "label": "synthetic",
                        }
                        for account_id in truth["account_totals"]
                    ],
                }
            )
            telemetry = TelemetryStore(state / "telemetry.sqlite")
            rows = []
            for event in truth["events"]:
                usage = TokenUsage(
                    input_tokens=event["input_tokens"],
                    output_tokens=event["output_tokens"],
                    cache_read_tokens=event["cache_read_tokens"],
                    calls=1,
                )
                rows.append(
                    TelemetryRecord(
                        event_key=event["event_id"],
                        provider=event["provider"],
                        event_name="sandglass.usage",
                        event_at=event["timestamp"].replace(".000Z", "Z"),
                        received_at=event["timestamp"],
                        account_id=event["account_ref"],
                        session_id=event["session_id"],
                        model="",
                        source=f"user_adapter:{source}",
                        source_version="holdout",
                        schema_version="1",
                        evidence_grade="U-A",
                        coverage_state="user_attributed",
                        usage=usage,
                    )
                )
            telemetry.append(rows)
            result = score(hidden, state)
            self.assertEqual(result["event_recall_percent"], 100.0)
            self.assertEqual(result["event_precision_percent"], 100.0)
            self.assertEqual(result["event_content_recall_percent"], 100.0)
            self.assertEqual(
                result["source_agnostic_event_content_recall_percent"], 100.0
            )
            self.assertEqual(result["wrong_source_record_count"], 0)
            self.assertEqual(result["wrong_provider_record_count"], 0)
            self.assertTrue(result["token_conservation"])
            self.assertTrue(result["source_agnostic_token_conservation"])
            self.assertEqual(result["account_discovery_percent"], 100.0)
            self.assertEqual(
                result["source_agnostic_account_id_discovery_percent"], 100.0
            )
            self.assertTrue(result["native_receipt_present"])
            self.assertTrue(result["any_native_receipt_present"])
            self.assertEqual(result["wrong_source_receipt_count"], 0)

            wrong_state = root / "wrong-source-state"
            wrong_source = "relay-alias"
            wrong_store = UserSourceStore(wrong_state / "user-sources.sqlite")
            wrong_store.append(
                {"source": wrong_source, "payload": {"native": "preserved"}}
            )
            wrong_store.append_accounts(
                {
                    "source": wrong_source,
                    "accounts": [
                        {
                            "provider": truth["provider"],
                            "account_id": account_id,
                            "label": "synthetic",
                        }
                        for account_id in truth["account_totals"]
                    ],
                }
            )
            TelemetryStore(wrong_state / "telemetry.sqlite").append(
                [
                    replace(row, source=f"user_adapter:{wrong_source}")
                    for row in rows
                ]
            )
            wrong_result = score(hidden, wrong_state)
            self.assertEqual(wrong_result["event_recall_percent"], 0.0)
            self.assertEqual(
                wrong_result["source_agnostic_event_content_recall_percent"],
                100.0,
            )
            self.assertEqual(wrong_result["wrong_source_record_count"], len(rows))
            self.assertTrue(wrong_result["source_agnostic_token_conservation"])
            self.assertFalse(
                wrong_result["source_agnostic_included_token_conservation"]
            )
            self.assertEqual(
                wrong_result["source_agnostic_included_token_count"], 0
            )
            self.assertEqual(
                wrong_result["source_agnostic_account_id_discovery_percent"],
                100.0,
            )
            self.assertFalse(wrong_result["native_receipt_present"])
            self.assertTrue(wrong_result["any_native_receipt_present"])
            self.assertEqual(wrong_result["wrong_source_receipt_count"], 1)
            self.assertFalse(wrong_result["any_source_controls_match_owner_policy"])


if __name__ == "__main__":
    unittest.main()
