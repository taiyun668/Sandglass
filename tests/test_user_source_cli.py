import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from sandglass import cli


def _package():
    return {
        "source": "cli.adapter",
        "revision": "rev-1",
        "receipts": [{"native": True}],
        "accounts": [{"provider": "claude", "account_id": "claude-1"}],
        "quotas": [],
        "records": [
            {
                "provider": "claude",
                "event_id": "event-1",
                "timestamp": "2026-08-30T00:00:00Z",
                "account_id": "claude-1",
                "session_id": "session-1",
                "input_tokens": 3,
                "output_tokens": 2,
                "total_tokens": 5,
                "calls": 1,
            }
        ],
        "expected": {
            "receipts": 1,
            "accounts": 1,
            "quotas": 0,
            "records": 1,
            "total_tokens": 5,
        },
    }


class UserSourceCliTests(unittest.TestCase):
    def test_file_based_preflight_commit_and_status(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": str(Path(tmp) / "state")}, clear=False
        ):
            package_path = Path(tmp) / "package.json"
            package_path.write_text(json.dumps(_package()), encoding="utf-8")

            output = io.StringIO()
            with redirect_stdout(output):
                code = cli.main(["user-source", "preflight", str(package_path)])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(output.getvalue())["ready_to_commit"])
            self.assertFalse((Path(tmp) / "state" / "user-sources.sqlite").exists())

            output = io.StringIO()
            with redirect_stdout(output):
                code = cli.main(["user-source", "commit", str(package_path)])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(output.getvalue())["persisted"])

            output = io.StringIO()
            with redirect_stdout(output):
                code = cli.main(
                    ["user-source", "status", "--source", "cli.adapter"]
                )
            self.assertEqual(code, 0)
            self.assertEqual(
                json.loads(output.getvalue())["source"]["active_revision"], "rev-1"
            )

    def test_invalid_package_returns_machine_safe_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_path = Path(tmp) / "bad.json"
            package_path.write_text("not-json", encoding="utf-8")
            errors = io.StringIO()
            with patch("sandglass.cli.require_canonical_state_home"), redirect_stderr(errors):
                code = cli.main(["user-source", "preflight", str(package_path)])
            self.assertEqual(code, 2)
            self.assertIn("UTF-8 JSON", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
