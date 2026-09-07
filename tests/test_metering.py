import json
import os
import tempfile
import threading
import unittest
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from sandglass.serve import Handler, api_payload
from sandglass.user_sources import UserSourceStore


def _metering_package(
    revision="meter-1",
    *,
    source="provider.api",
    observation_id="usage-1",
    tokens=150,
):
    return {
        "contract_version": 3,
        "source": source,
        "revision": revision,
        "receipts": [{"bucket": "official-usage-bucket-1"}],
        "entities": [
            {
                "provider": "openai",
                "entity_id": "org-1",
                "kind": "organization",
                "parent_entity_id": "",
                "label": "Example org",
                "plan": "api",
            },
            {
                "provider": "openai",
                "entity_id": "project-1",
                "kind": "project",
                "parent_entity_id": "org-1",
                "label": "Example project",
                "plan": "",
            },
        ],
        "observations": [
            {
                "observation_id": observation_id,
                "provider": "openai",
                "upstream_provider": "",
                "service": "api",
                "kind": "usage",
                "entity_id": "project-1",
                "start_at": "2026-08-31T00:00:00Z",
                "end_at": "2026-08-31T01:00:00Z",
                "granularity": "hour",
                "model": "gpt-example",
                "operation": "responses",
                "authority": "official_usage",
                "coverage": "complete",
                "resets_at": "",
                "dimensions": {"service_tier": "default"},
                "metrics": [
                    {"name": "gen_ai.tokens.input", "value": "100", "unit": "{token}"},
                    {"name": "gen_ai.tokens.cache_read", "value": "40", "unit": "{token}"},
                    {"name": "gen_ai.tokens.output", "value": "50", "unit": "{token}"},
                    {"name": "gen_ai.tokens.reasoning", "value": "20", "unit": "{token}"},
                    {"name": "gen_ai.tokens.total", "value": str(tokens), "unit": "{token}"},
                    {"name": "gen_ai.requests", "value": "3", "unit": "{request}"},
                ],
            }
        ],
        "expected": {
            "receipts": 1,
            "entities": 2,
            "observations": 1,
            "metrics": 6,
            "total_tokens": tokens,
        },
    }


class MeteringTests(unittest.TestCase):
    def test_preflight_keeps_provider_total_authoritative_and_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            result = UserSourceStore().preflight_import(_metering_package())

            self.assertTrue(result["ready_to_commit"])
            self.assertEqual(result["contract_version"], 3)
            self.assertEqual(result["summary"]["total_tokens"], 150)
            self.assertNotEqual(result["summary"]["total_tokens"], 100 + 40 + 50 + 20)
            self.assertFalse(Path(tmp, "user-sources.sqlite").exists())

    def test_commit_replace_and_rollback_switch_whole_metering_revision(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            store = UserSourceStore()
            first = _metering_package()
            second = _metering_package("meter-2", observation_id="usage-2", tokens=300)
            store.commit_import({"package": first})
            store.commit_import(
                {
                    "package": second,
                    "replace": True,
                    "expected_active_revision": "meter-1",
                }
            )
            self.assertEqual(store.metering()["source_total_tokens"], {"provider.api": 300})

            store.rollback_import(
                {
                    "source": "provider.api",
                    "target_revision": "meter-1",
                    "expected_active_revision": "meter-2",
                }
            )
            view = store.metering()
            self.assertEqual(view["source_total_tokens"], {"provider.api": 150})
            self.assertFalse(view["included_in_product_totals"])
            self.assertEqual(
                store.import_status("provider.api")["source"]["revisions"][0]["contract_version"],
                3,
            )

    def test_rejects_secret_fields_missing_total_and_overlapping_aggregate_buckets(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            secret = _metering_package()
            secret["receipts"][0]["api_key"] = "must-not-enter-state"
            with self.assertRaisesRegex(ValueError, "secret-free"):
                UserSourceStore().preflight_import(secret)

            missing_total = _metering_package()
            missing_total["observations"][0]["metrics"] = missing_total["observations"][0][
                "metrics"
            ][:-2]
            missing_total["expected"]["metrics"] = 4
            missing_total["expected"]["total_tokens"] = 0
            with self.assertRaisesRegex(ValueError, "source-defined"):
                UserSourceStore().preflight_import(missing_total)

            overlap = _metering_package()
            duplicate = json.loads(json.dumps(overlap["observations"][0]))
            duplicate["observation_id"] = "usage-overlap"
            duplicate["start_at"] = "2026-08-31T00:30:00Z"
            duplicate["end_at"] = "2026-08-31T01:30:00Z"
            overlap["observations"].append(duplicate)
            overlap["expected"]["observations"] = 2
            overlap["expected"]["metrics"] = 12
            overlap["expected"]["total_tokens"] = 300
            with self.assertRaisesRegex(ValueError, "overlaps an aggregate series"):
                UserSourceStore().preflight_import(overlap)

            cyclic = _metering_package()
            cyclic["entities"][0]["parent_entity_id"] = "project-1"
            with self.assertRaisesRegex(ValueError, "contains a cycle"):
                UserSourceStore().preflight_import(cyclic)

    def test_exact_observation_cannot_be_reintroduced_under_another_source(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            store = UserSourceStore()
            store.commit_import({"package": _metering_package()})
            disguised = _metering_package(source="renamed.api", observation_id="renamed")
            result = store.preflight_import(disguised)
            self.assertFalse(result["ready_to_commit"])
            self.assertIn(
                "active_metering_observation_collision",
                {row["code"] for row in result["blockers"]},
            )

    def test_overlapping_aggregate_sources_remain_visible_but_not_mergeable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            store = UserSourceStore()
            store.commit_import({"package": _metering_package()})
            partial = _metering_package(
                source="local.gateway", observation_id="local-bucket", tokens=90
            )
            partial["observations"][0]["authority"] = "client_observed"
            partial["observations"][0]["coverage"] = "partial"
            self.assertTrue(store.preflight_import(partial)["ready_to_commit"])
            store.commit_import({"package": partial})

            view = store.metering()
            self.assertEqual(
                view["source_total_tokens"],
                {"local.gateway": 90, "provider.api": 150},
            )
            self.assertGreater(len(view["aggregate_cross_source_overlaps"]), 0)
            self.assertEqual(view["accounting_candidate_total_tokens"], 0)
            self.assertFalse(view["included_in_product_totals"])

    def test_loopback_v3_and_metering_query(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(Handler, since=None, live_quota=False)
            )
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                package = _metering_package()
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/v3/user-source-imports/commit",
                    data=json.dumps({"package": package}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    result = json.load(response)
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{httpd.server_port}/api/model-api-metering",
                    timeout=3,
                ) as response:
                    view = json.load(response)

                self.assertTrue(result["persisted"])
                self.assertEqual(view["source_total_tokens"], {"provider.api": 150})
                self.assertEqual(api_payload("/api/model-api-metering")["contract_version"], 3)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
