import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandglass import paths


class StateHomeAttestationTests(unittest.TestCase):
    def setUp(self):
        paths._clear_state_home_attestation_cache()

    def tearDown(self):
        paths._clear_state_home_attestation_cache()

    def _signals(self):
        return patch.object(paths, "_package_identity", return_value=(True, True, "unpackaged")), patch.object(
            paths, "_is_app_container", return_value=(True, False, "desktop_token")
        )

    def test_success_is_cached_and_diagnostic_is_redacted(self):
        with tempfile.TemporaryDirectory() as tmp, self._signals()[0], self._signals()[1], patch.object(
            paths, "_win32_probe", side_effect=lambda target: (True, "\\\\?\\" + target, "probe_ok")
        ) as probe, patch.object(paths.os, "name", "nt"):
            result = paths.state_home_attestation(path=Path(tmp), diagnostic=True)
            again = paths.state_home_attestation(path=Path(tmp), diagnostic=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result, again)
        probe.assert_called_once()
        self.assertNotIn("final_path", result)
        self.assertTrue(result["target_path_sha256"])
        self.assertTrue(result["final_path_sha256"])
        self.assertEqual(result["target_path_sha256"], result["final_path_sha256"])

    def test_win32_prefix_is_normalized_before_hashing(self):
        self.assertEqual(
            paths._normalise_final_path(r"\\?\C:\state\probe"),
            paths._normalise_final_path(r"C:\state\probe"),
        )
        self.assertEqual(
            paths._normalise_final_path(r"\\?\UNC\server\share\probe"),
            paths._normalise_final_path(r"\\server\share\probe"),
        )

    def test_foreign_package_fails_without_caching(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            paths, "_package_identity", return_value=(True, False, "package_identity_foreign")
        ), patch.object(paths, "_win32_probe") as probe, patch.object(paths.os, "name", "nt"):
            result = paths.state_home_attestation(path=Path(tmp), diagnostic=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "package_identity_foreign")
        probe.assert_not_called()
        self.assertEqual(paths._STATE_HOME_CACHE, {})

    def test_app_container_and_api_ambiguity_fail_closed(self):
        for signal in ((True, True, "app_container"), (False, False, "token_api_ambiguous")):
            with self.subTest(signal=signal), tempfile.TemporaryDirectory() as tmp, patch.object(
                paths, "_package_identity", return_value=(True, True, "unpackaged")
            ), patch.object(paths, "_is_app_container", return_value=signal), patch.object(
                paths, "_win32_probe"
            ) as probe, patch.object(paths.os, "name", "nt"):
                result = paths.state_home_attestation(path=Path(tmp), diagnostic=True)
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], signal[2])
            probe.assert_not_called()

    def test_probe_error_is_not_cached_and_success_cleans_portable_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch.object(paths.os, "name", "nt"), self._signals()[0], self._signals()[1], patch.object(
                paths, "_win32_probe", side_effect=OSError("CreateFileW")
            ):
                failed = paths.state_home_attestation(path=home, diagnostic=True)
            self.assertFalse(failed["ok"])
            self.assertEqual(paths._STATE_HOME_CACHE, {})

            with patch.object(paths.os, "name", "posix"):
                passed = paths.state_home_attestation(path=home, diagnostic=True)
            self.assertTrue(passed["ok"])
            self.assertEqual(list(home.iterdir()), [])

    def test_final_handle_path_mismatch_fails_with_hashes_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "redirected"
            other.mkdir()
            with self._signals()[0], self._signals()[1], patch.object(
                paths, "_win32_probe", return_value=(True, str(other / "probe"), "probe_ok")
            ), patch.object(paths.os, "name", "nt"):
                result = paths.state_home_attestation(path=Path(tmp) / "spelled", diagnostic=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "probe_final_path_mismatch")
        self.assertNotIn(str(other), str(result))
        self.assertTrue(result["final_path_sha256"])


if __name__ == "__main__":
    unittest.main()
