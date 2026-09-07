import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandglass.product_mode import (
    attribution_mode,
    mode_payload,
    product_mode_path,
    set_attribution_mode,
)
from sandglass.serve import api_payload


class ProductModeTests(unittest.TestCase):
    def test_mode_is_unselected_until_the_user_chooses(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            self.assertEqual(attribution_mode(), "")
            self.assertEqual(
                mode_payload(),
                {
                    "selected": False,
                    "attribution_mode": "",
                    "single_account_official_fallback": False,
                    "skill_required": False,
                },
            )

    def test_explicit_mode_is_atomic_and_reported_by_the_real_api(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            selected = set_attribution_mode("single_official")
            stored = json.loads(product_mode_path().read_text(encoding="utf-8"))

            self.assertTrue(selected["selected"])
            self.assertEqual(stored["attribution_mode"], "single_official")
            self.assertEqual(
                api_payload("/api/product-mode", live_quota=False)["attribution_mode"],
                "single_official",
            )
            self.assertEqual(list(Path(tmp).glob(".*.tmp")), [])

    def test_invalid_mode_is_rejected_without_overwriting_the_choice(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            set_attribution_mode("skill_assisted")
            with self.assertRaises(ValueError):
                set_attribution_mode("guess_everything")
            self.assertEqual(attribution_mode(), "skill_assisted")

    def test_switching_modes_does_not_rewrite_accounting_ledgers(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            ledger_paths = [
                Path(tmp) / "claude-official-identity-runs.json",
                Path(tmp) / "codex-official-identity-runs.json",
                Path(tmp) / "grok-official-identity-runs.json",
                Path(tmp) / "quota-observations.json",
                Path(tmp) / "telemetry.sqlite",
                Path(tmp) / "user-sources.sqlite",
                Path(tmp) / "cache.sqlite",
            ]
            for index, path in enumerate(ledger_paths):
                path.write_bytes(f"ledger-{index}".encode("utf-8"))
            before = {path.name: path.read_bytes() for path in ledger_paths}

            set_attribution_mode("single_official")
            self.assertEqual(attribution_mode(), "single_official")
            set_attribution_mode("skill_assisted")
            self.assertEqual(attribution_mode(), "skill_assisted")

            after = {path.name: path.read_bytes() for path in ledger_paths}
            self.assertEqual(after, before)

    def test_a_mode_file_we_cannot_read_is_not_an_unselected_mode(self):
        """A failed read is not 'the user has not chosen'.

        `attribution_mode` treated every OSError as empty, so a locked
        product-mode.json became unselected. GET /api/product-mode then
        answered `selected: false`, the chooser appeared over a choice
        that was still on disk, and a save replaced the file. The panel
        already refuses to treat a failed GET as unselected; the server
        was the remaining half. Report still answers (the merge is
        fail-closed) and does not rewrite the file.
        """
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            set_attribution_mode("single_official")
            path = product_mode_path()
            original = path.read_text(encoding="utf-8")
            reported = []
            real_read = Path.read_text

            def read_text(self, *args, **kwargs):
                if self.name == "product-mode.json":
                    raise PermissionError("product-mode.json is locked")
                return real_read(self, *args, **kwargs)

            with patch.object(Path, "read_text", read_text), patch(
                "sandglass.diagnostics.record_component_failure",
                lambda name, exc: reported.append(name),
            ):
                self.assertEqual(attribution_mode(), "")
                self.assertEqual(reported, ["product_mode_write"])
                reported.clear()
                with self.assertRaises(PermissionError):
                    mode_payload()
                with self.assertRaises(PermissionError):
                    api_payload("/api/product-mode", live_quota=False)

            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(reported, ["product_mode_write", "product_mode_write"])

    def test_damaged_mode_bytes_are_not_an_unselected_mode(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            path = product_mode_path()
            path.write_text("not json", encoding="utf-8")
            reported = []
            with patch(
                "sandglass.diagnostics.record_component_failure",
                lambda name, exc: reported.append(name),
            ):
                self.assertEqual(attribution_mode(), "")
                self.assertEqual(reported, ["product_mode_write"])
                reported.clear()
                with self.assertRaises(json.JSONDecodeError):
                    mode_payload()

            self.assertEqual(path.read_text(encoding="utf-8"), "not json")
            self.assertEqual(reported, ["product_mode_write"])


if __name__ == "__main__":
    unittest.main()
