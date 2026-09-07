import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from sandglass.serve import Handler
from tools.prepare_adapter_clean_room import prepare_bundle


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "sandglass-adapter"
SKILL = SKILL_ROOT / "SKILL.md"
RECONSTRUCTION = SKILL_ROOT / "references" / "reconstruction.md"
V2_INTERFACE = SKILL_ROOT / "references" / "v2-interface.md"
CASES = SKILL_ROOT / "references" / "cases.md"
V2_SCRIPT = SKILL_ROOT / "scripts" / "v2_import.py"


class AdapterSkillTests(unittest.TestCase):
    def test_entrypoint_contains_only_core_workflow_routing_and_boundaries(self):
        text = SKILL.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        self.assertNotIn("TODO", text)
        self.assertLess(len(text.splitlines()), 150)
        self.assertLess(len(text.split()), 1200)
        self.assertIn("Ask the owner first", text)
        self.assertIn("Build the candidate ledger before follow-up questions", text)
        self.assertIn("Preflight one complete v2 revision", text)
        self.assertIn("Commit and verify", text)
        self.assertIn("Leave admission to the owner", text)
        self.assertIn("Keep provider and outer-tool directories read-only", normalized)
        self.assertIn("references/reconstruction.md", text)
        self.assertIn("references/v2-interface.md", text)
        self.assertIn("scripts/v2_import.py", text)

    def test_detailed_reconstruction_knowledge_is_in_reference(self):
        entrypoint = SKILL.read_text(encoding="utf-8")
        reference = RECONSTRUCTION.read_text(encoding="utf-8")
        normalized = " ".join(reference.split())
        for invariant in (
            "Build a column join",
            "One positive record is not complete coverage",
            "Keep discovery bounded",
            "Create a reconstruction anchor table",
            "time-axis reset of the reconstructed composite ledger",
            "Nearest timestamp alone is not proof",
            "Reconstruct transition boundaries",
            "Rapid `A -> B -> A`",
            "Preserve Token exactly once",
            "minute is an aggregation bucket, never the deduplication identity",
            "same-session minute storm",
            "Distinct sessions may bill concurrently",
            "group lifecycle records by stable native event/request identity",
            "terminal record is the accounting authority",
            "Minimum adversarial fixtures",
        ):
            self.assertIn(invariant, normalized)
        self.assertNotIn("Minimum adversarial fixtures", entrypoint)
        self.assertNotIn("same email or label at two providers", entrypoint)

    def test_exact_v2_contract_is_in_reference_and_product_interface(self):
        entrypoint = SKILL.read_text(encoding="utf-8")
        reference = V2_INTERFACE.read_text(encoding="utf-8")
        normalized = " ".join(reference.split())
        for invariant in (
            "sandglass user-source preflight package.json",
            "/api/user-sources/official-minutes",
            "One complete package",
            "Required operation order",
            "source + revision` is immutable",
            "compare-and-swap expectation",
            "Product controls remain separate",
            "cache.sqlite",
        ):
            self.assertIn(invariant, normalized)
        self.assertNotIn("sandglass user-source preflight package.json", entrypoint)
        self.assertNotIn("cache.sqlite", entrypoint)

    def test_current_mechanism_has_no_v3_routing(self):
        for path in (SKILL, RECONSTRUCTION, V2_INTERFACE, V2_SCRIPT):
            text = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("metering-v3", text, path)
            self.assertNotIn('"contract_version": 3', text, path)
            self.assertNotIn("v3 package", text, path)

    def test_v2_import_script_self_test(self):
        result = subprocess.run(
            [sys.executable, str(V2_SCRIPT), "--self-test"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("v2 import self-test: PASS", result.stdout)

    def test_v2_import_probe_uses_post_without_writing(self):
        spec = importlib.util.spec_from_file_location("adapter_v2_import", V2_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"SANDGLASS_HOME": temporary}
        ), patch("sandglass.serve.collect_all", return_value=[]), patch(
            "sandglass.serve._attribution_diagnostics", return_value={"runtime": "test"}
        ):
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(Handler, since=None, live_quota=False)
            )
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                result = module.probe(f"http://127.0.0.1:{httpd.server_port}")
                endpoint = f"http://127.0.0.1:{httpd.server_port}"
                first = {
                    "source": "probe.adapter",
                    "revision": "revision-1",
                    "receipts": [{"event_id": "native-1"}],
                    "accounts": [],
                    "quotas": [],
                    "records": [],
                    "expected": {
                        "receipts": 1,
                        "accounts": 0,
                        "quotas": 0,
                        "records": 0,
                        "total_tokens": 0,
                    },
                }
                second = {**first, "revision": "revision-2", "receipts": [{"event_id": "native-2"}]}
                module.send(first, endpoint, commit=True)
                module.send(
                    second,
                    endpoint,
                    commit=True,
                    replace=True,
                    expected_active_revision="revision-1",
                )
                self.assertEqual(
                    module.status(endpoint, "probe.adapter")["source"]["active_revision"],
                    "revision-2",
                )
                module.rollback(
                    endpoint,
                    source="probe.adapter",
                    expected_active_revision="revision-2",
                    target_revision="revision-1",
                )
                self.assertEqual(
                    module.status(endpoint, "probe.adapter")["source"]["active_revision"],
                    "revision-1",
                )
                module.rollback(
                    endpoint,
                    source="probe.adapter",
                    expected_active_revision="revision-1",
                    target_revision="revision-2",
                )
                final_status = module.status(endpoint, "probe.adapter")
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2)
            self.assertTrue(result["ready"])
            self.assertTrue(result["state_unchanged"])
            self.assertNotEqual(result["preflight_http_status"], 404)
            self.assertEqual(
                final_status["source"]["active_revision"],
                "revision-2",
            )

    def test_clean_room_bundle_contains_the_three_layers_without_cases(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bundle"
            result = prepare_bundle(output)
            files = [
                path.relative_to(output).as_posix()
                for path in output.rglob("*")
                if path.is_file()
            ]

            self.assertEqual(
                files,
                [
                    "SKILL.md",
                    "references/reconstruction.md",
                    "references/v2-interface.md",
                    "scripts/v2_import.py",
                ],
            )
            self.assertEqual(Path(result["working_directory"]), output.resolve())
            self.assertTrue(result["byte_identical"])
            for relative in files:
                self.assertEqual(
                    (output / relative).read_bytes(),
                    (SKILL_ROOT / relative).read_bytes(),
                )
            self.assertFalse((output / "references" / "cases.md").exists())
            self.assertFalse((output / "references" / "metering-v3.md").exists())

    def test_vendor_specific_cases_are_excluded_from_mechanism_bundle(self):
        mechanism = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (SKILL, RECONSTRUCTION, V2_INTERFACE)
        )
        cases = CASES.read_text(encoding="utf-8")
        for answer in (
            "Claude",
            "Codex",
            "Grok",
            "forked_from_id",
            "output_tokens_details",
            "~/.codex",
            "codex-auth",
            "grokwork",
        ):
            self.assertNotIn(answer, mechanism)
        self.assertIn("Codex", cases)
        self.assertIn("forked_from_id", cases)
        self.assertIn("codex-auth", cases)
        self.assertIn("A minute storm is not automatically replay", cases)

    def test_entrypoint_preserves_write_and_secret_boundaries(self):
        text = " ".join(SKILL.read_text(encoding="utf-8").split())
        self.assertIn("only Sandglass-owned writes", text)
        self.assertIn("Never refresh credentials, switch accounts", text)
        self.assertIn("live official quota call requires owner authorization", text)
        self.assertIn("If neither the installed file interface nor a verified", text)
        self.assertIn("Do not publish, open a pull request", text)
        for owner_fact in ("Ayun", "icloud.com", "C:\\Users"):
            self.assertNotIn(owner_fact, text)

    def test_clean_room_staging_refuses_a_nonempty_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bundle"
            output.mkdir()
            (output / "cases.md").write_text("answer-bearing file", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "absent or empty"):
                prepare_bundle(output)


if __name__ == "__main__":
    unittest.main()
