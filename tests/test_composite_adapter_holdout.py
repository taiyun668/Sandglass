import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from sandglass.telemetry import TelemetryStore, ingest_user_source_records
from sandglass.user_sources import UserSourceStore
from tools.generate_composite_adapter_holdout import generate
from tools.prepare_adapter_clean_room import BUNDLE_FILES
from tools.prepare_adapter_holdout_lab import prepare_lab
from tools.verify_composite_adapter_holdout import _agent_rollback_evidence, verify


class CompositeAdapterHoldoutTests(unittest.TestCase):
    @staticmethod
    def _bundle(root: Path) -> Path:
        skill = root / "skill"
        for relative in BUNDLE_FILES:
            path = skill / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"synthetic frozen {relative.as_posix()}\n", encoding="utf-8"
            )
        return skill

    def _generate(self, root: Path, seed: int = 77123):
        visible = root / "visible"
        hidden = root / "hidden"
        generate(visible, hidden, seed=seed)
        return visible, hidden

    def test_composite_seed_is_deterministic_and_high_dimensional(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_visible, first_hidden = self._generate(root / "first")
            second_visible, second_hidden = self._generate(root / "second")
            first_manifest = json.loads((first_hidden / "manifest.json").read_text())
            second_manifest = json.loads((second_hidden / "manifest.json").read_text())
            self.assertEqual(
                first_manifest["visible_tree_sha256"],
                second_manifest["visible_tree_sha256"],
            )
            self.assertEqual(first_manifest["provider_count"], 3)
            self.assertEqual(first_manifest["account_count"], 6)
            self.assertGreaterEqual(first_manifest["difficulty_count"], 21)
            self.assertGreaterEqual(first_manifest["visible_files"], 45)
            self.assertGreaterEqual(first_manifest["visible_max_depth"], 9)
            truth = json.loads((first_hidden / "ground-truth.json").read_text())
            visible_switches = [
                json.loads(line)
                for line in (
                    first_visible
                    / "AppData"
                    / "Local"
                    / "SwitchDeck"
                    / "history"
                    / "switches.ndjson"
                ).read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(all("requested_at" in row for row in visible_switches))
            self.assertTrue(all("effective_at" not in row for row in visible_switches))
            self.assertTrue(
                all("effective_at" in row for row in truth["transitions"])
            )
            self.assertIn(
                "approximate_switch_time_reanchoring", truth["difficulty"]
            )
            self.assertEqual({row["provider"] for row in truth["events"]}, {"aster", "boreal", "cinder"})
            emails = Counter(row["email"] for row in truth["accounts"])
            self.assertGreaterEqual(max(emails.values()), 3)
            by_minute = Counter(row["timestamp"][:16] for row in truth["events"])
            self.assertGreaterEqual(max(by_minute.values()), 48)
            self.assertGreaterEqual(
                len(list((first_visible / ".aster" / "projects").rglob("transcript.jsonl"))),
                5,
            )
            self.assertEqual(
                len(list((first_visible / ".aster" / "projects").rglob("forked-history.jsonl"))),
                1,
            )
            self.assertTrue(
                (
                    first_visible
                    / ".aster"
                    / "recovery"
                ).is_dir()
            )
            self.assertTrue(
                (first_visible / ".aster" / "state" / "stats-cache.json").is_file()
            )
            self.assertTrue(
                (
                    first_visible
                    / "AppData"
                    / "Roaming"
                    / "BorealDesktop"
                    / "Cache"
                    / "usage-summary.json"
                ).is_file()
            )
            self.assertTrue(
                (first_visible / ".cinder" / "tmp" / "last-response.json").is_file()
            )
            self.assertTrue((first_visible / "AppData" / "Local" / "LatticeRelay" / "diagnostics" / "attempts.ndjson").is_file())
            install_rows = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in first_visible.rglob("install.json")
            ]
            self.assertTrue(install_rows)
            self.assertTrue(all("data_root" not in row for row in install_rows))
            aster_cache = json.loads(
                (first_visible / ".aster" / "state" / "stats-cache.json").read_text(
                    encoding="utf-8"
                )
            )
            boreal_cache = json.loads(
                (
                    first_visible
                    / "AppData"
                    / "Roaming"
                    / "BorealDesktop"
                    / "Cache"
                    / "usage-summary.json"
                ).read_text(encoding="utf-8")
            )
            cinder_cache = json.loads(
                (first_visible / ".cinder" / "cache" / "recent-usage.json").read_text(
                    encoding="utf-8"
                )
            )
            provider_totals = {
                provider: sum(
                    int(row["total_tokens"])
                    for row in truth["events"]
                    if row["provider"] == provider
                )
                for provider in ("aster", "boreal", "cinder")
            }
            self.assertGreater(provider_totals["aster"], aster_cache["total_tokens"])
            self.assertGreater(provider_totals["boreal"], boreal_cache["total_tokens"])
            self.assertLess(len(cinder_cache["events"]), 96)
            deep_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in first_visible.rglob("*.jsonl")
            )
            self.assertIn('"type":"assistant_progress"', deep_text)
            self.assertIn('"type":"turn_usage"', deep_text)
            self.assertIn('"kind":"generation_progress"', deep_text)
            self.assertIn('"kind":"completed_generation"', deep_text)
            self.assertIn('"type":"response_delta"', deep_text)
            self.assertIn('"type":"response_complete"', deep_text)
            aster_marker = next(
                row["event_id"]
                for row in truth["events"]
                if row["provider"] == "aster" and "storm" not in row["event_id"]
            )
            self.assertGreaterEqual(deep_text.count(aster_marker), 2)
            for difficulty in (
                "deep_partitioned_history",
                "mixed_record_streams",
                "shallow_partial_usage_caches",
                "temporary_token_decoys",
                "recovery_duplicate_fragments",
            ):
                self.assertIn(difficulty, truth["difficulty"])

    def test_perfect_persisted_ledger_passes_every_binary_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            visible, hidden = self._generate(root)
            skill = self._bundle(root)
            lab = root / "lab"
            prepare_lab(skill, visible, lab)
            state = lab / "sandglass-home"
            truth = json.loads((hidden / "ground-truth.json").read_text())
            source = "composite-reference"
            store = UserSourceStore(state / "user-sources.sqlite")
            for event in truth["events"]:
                store.append({"source": source, "payload": event})
            store.append_accounts(
                {"source": source, "accounts": truth["accounts"]}
            )
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
            ingest_user_source_records(
                {
                    "source": source,
                    "records": [
                        {
                            "provider": row["provider"],
                            "event_id": row["event_id"],
                            "timestamp": row["timestamp"],
                            "account_id": row["account_id"],
                            "session_id": row["session_id"],
                            "input_tokens": row["input_tokens"],
                            "output_tokens": row["output_tokens"],
                            "cache_read_tokens": row["cache_read_tokens"],
                            "total_tokens": row["total_tokens"],
                            "calls": 1,
                            "source_version": "reference",
                            "schema_version": "1",
                        }
                        for row in truth["events"]
                    ],
                },
                TelemetryStore(state / "telemetry.sqlite"),
            )
            result = verify(hidden, state, lab)
            self.assertEqual(result["result"], "PASS", result)
            self.assertTrue(all(result["gates"].values()))

    def test_shape_variant_two_changes_native_fields_without_changing_truth(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            visible = root / "visible"
            hidden = root / "hidden"
            manifest = generate(visible, hidden, seed=88117, shape_variant=2)
            truth = json.loads((hidden / "ground-truth.json").read_text(encoding="utf-8"))
            text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in visible.rglob("*")
                if path.is_file() and path.suffix in {".logjson", ".ndjson"}
            )
            self.assertEqual(manifest["shape_variant"], 2)
            self.assertIn("structural_shape_variant_2", truth["difficulty"])
            self.assertIn('"record_kind":"usage_finalized"', text)
            self.assertIn('"event_type":"billing_finalized"', text)
            self.assertIn('"record_type":"response_settled"', text)
            self.assertIn('"status":"charged"', text)
            self.assertNotIn('"type":"turn_usage"', text)
            self.assertNotIn('"kind":"completed_generation"', text)
            self.assertNotIn('"type":"response_complete"', text)
            switch_text = next(
                (visible / "AppData" / "Local" / "SwitchDeck" / "history").glob(
                    "*.logjson"
                )
            ).read_text(encoding="utf-8")
            self.assertIn('"previous_profile"', switch_text)
            self.assertNotIn('"from_account"', switch_text)
            visible_markers = text + "\n" + switch_text
            self.assertTrue(
                all(
                    str(row["event_id"]) in visible_markers
                    for row in truth["events"]
                    if row["provider"] in {"aster", "boreal", "cinder"}
                )
            )

    def test_empty_attempt_fails_instead_of_receiving_partial_credit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            visible, hidden = self._generate(root)
            skill = self._bundle(root)
            lab = root / "lab"
            prepare_lab(skill, visible, lab)
            result = verify(hidden, lab / "sandglass-home", lab)
            self.assertEqual(result["result"], "FAIL")
            self.assertIn("event_set_exact_once", result["failed_gates"])
            self.assertIn("quotas_latest_exact", result["failed_gates"])
            self.assertNotIn("source_tree_byte_for_byte_unchanged", result["failed_gates"])

    def test_formal_rollback_evidence_requires_executed_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            lab = Path(temporary)
            controller = lab / "controller"
            controller.mkdir()
            events = controller / "events.jsonl"
            events.write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": "/v2/user-source-imports/rollback",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertFalse(_agent_rollback_evidence(lab))
            events.write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "status": "completed",
                            "exit_code": 0,
                            "command": "python scripts/v2_import.py --rollback-source source",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertTrue(_agent_rollback_evidence(lab))


if __name__ == "__main__":
    unittest.main()
