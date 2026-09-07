"""What the update offer will and will not do.

The button is dark until a release offers something, and the offer is only
installable if it can be checked: a checksum published beside the installer, a
download that matches it, and a signature Windows itself trusts. The project's
release doctrine says an unsigned build is an internal candidate and not a
public artifact -- an updater that would run one anyway makes that decorative.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sandglass import update


class VersionComparisonTests(unittest.TestCase):
    def test_it_compares_numbers_not_strings(self):
        """"0.10.0" sorts below "0.9.0" as text and above it as a version."""
        self.assertTrue(update.is_newer("0.10.0", "0.9.0"))
        self.assertFalse(update.is_newer("0.9.0", "0.10.0"))

    def test_the_same_version_is_not_newer(self):
        self.assertFalse(update.is_newer("0.1.0", "0.1.0"))

    def test_shorter_and_longer_forms_compare(self):
        self.assertTrue(update.is_newer("1.0", "0.9.9"))
        self.assertFalse(update.is_newer("1.0", "1.0.0"))

    def test_nonsense_is_never_newer(self):
        for candidate in ("", "latest", "vNext", None):
            self.assertFalse(update.is_newer(candidate or ""))


class OfferTests(unittest.TestCase):
    ASSET = "Sandglass-9.9.9-windows-x64-setup.exe"

    def _release(self, *, checksums=True, tag="v9.9.9"):
        assets = [{"name": self.ASSET,
                   "browser_download_url": "https://github.com/a/b/releases/x/" + self.ASSET}]
        if checksums:
            assets.append({"name": update.CHECKSUMS_NAME,
                           "browser_download_url": "https://github.com/a/b/releases/x/sums"})
        return {"tag_name": tag, "assets": assets, "html_url": "https://github.com/a/b"}

    def test_a_release_without_a_checksum_is_not_offered(self):
        """An update that cannot be checked is not an update, it is a download."""
        self.assertEqual(update.offer_from(self._release(checksums=False)), {})

    def test_an_older_release_is_not_offered(self):
        self.assertEqual(update.offer_from(self._release(tag="v0.0.1")), {})

    def test_a_complete_release_is_offered_with_its_checksum(self):
        digest = "a" * 64
        with patch.object(update, "_get", return_value=(digest + "  " + self.ASSET).encode()):
            offer = update.offer_from(self._release())
        self.assertEqual(offer["version"], "9.9.9")
        self.assertEqual(offer["sha256"], digest)
        self.assertEqual(offer["asset"], self.ASSET)

    def test_a_checksum_file_that_omits_the_installer_offers_nothing(self):
        with patch.object(update, "_get", return_value=b"b" * 64 + b"  something-else.exe"):
            self.assertEqual(update.offer_from(self._release()), {})


class HostTests(unittest.TestCase):
    def test_only_the_published_hosts_are_accepted(self):
        for url in ("http://github.com/x", "https://example.com/x",
                    "https://github.com.evil.test/x", "", "ftp://github.com/x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                update._https(url)

    def test_the_release_hosts_are_accepted(self):
        for host in update.ALLOWED_HOSTS:
            self.assertTrue(update._https("https://" + host + "/x"))


class DownloadVerificationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def _serve(self, payload):
        class Response:
            def __init__(self):
                self._data = [payload]

            def read(self, _size=None):
                return self._data.pop(0) if self._data else b""

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return lambda *a, **k: Response()

    def _offer(self, payload, digest=None):
        return {"url": "https://github.com/a/b/setup.exe", "asset": "setup.exe",
                "sha256": digest or hashlib.sha256(payload).hexdigest()}

    def test_a_mismatched_download_is_refused_and_removed(self):
        payload = b"MZ" + b"\x00" * 32
        target = self.root / "setup.exe"
        with patch("urllib.request.urlopen", self._serve(payload)):
            with self.assertRaises(ValueError):
                update.download_verified(self._offer(payload, "c" * 64), target)
        self.assertFalse(target.exists(), "校验失败的文件不能留在磁盘上")

    def test_a_matching_but_unsigned_download_is_refused(self):
        """The checksum only proves it is the file they published.

        It says nothing about who published it, which is the question that
        decides whether this is safe to execute.
        """
        payload = b"MZ" + b"\x00" * 32
        target = self.root / "setup.exe"
        with patch("urllib.request.urlopen", self._serve(payload)), patch.object(
            update, "authenticode_valid", return_value=False
        ):
            with self.assertRaises(ValueError) as caught:
                update.download_verified(self._offer(payload), target)
        self.assertIn("signed", str(caught.exception))
        self.assertFalse(target.exists())

    def test_a_matching_signed_download_is_kept(self):
        """The counterpart: refusing everything would pass the tests above."""
        payload = b"MZ" + b"\x00" * 32
        target = self.root / "setup.exe"
        with patch("urllib.request.urlopen", self._serve(payload)), patch.object(
            update, "authenticode_valid", return_value=True
        ):
            kept = update.download_verified(self._offer(payload), target)
        self.assertTrue(kept.exists())
        self.assertEqual(kept.read_bytes(), payload)

    def test_an_offer_without_a_usable_checksum_never_downloads(self):
        called = []
        with patch("urllib.request.urlopen", lambda *a, **k: called.append(1)):
            with self.assertRaises(ValueError):
                update.download_verified({"url": "https://github.com/a/b", "sha256": "nope"},
                                         self.root / "setup.exe")
        self.assertEqual(called, [])


class SignatureTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Authenticode is a Windows check")
    def test_an_unsigned_file_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unsigned.exe"
            path.write_bytes(b"MZ" + b"\x00" * 4096)
            self.assertFalse(update.authenticode_valid(path))

    @unittest.skipUnless(os.name == "nt", "Authenticode is a Windows check")
    def test_a_signed_file_is_trusted(self):
        """Without this the check above passes by rejecting everything.

        python.exe carries an embedded signature. Files signed only through the
        Windows catalog do not, and are correctly rejected by a file-level
        check, so they cannot stand in here.
        """
        import sys

        self.assertTrue(update.authenticode_valid(Path(sys.executable)))


class CheckStateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.dict(os.environ, {"SANDGLASS_HOME": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.home = Path(tmp.name)

    def test_no_release_means_no_offer_and_no_crash(self):
        with patch.object(update, "_get", side_effect=OSError("404")):
            self.assertEqual(update.available_update(force=True), {})
        stored = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["offer"], {})
        self.assertTrue(stored["error"])

    def test_a_check_is_cached_rather_than_repeated(self):
        calls = []

        def once(*args, **kwargs):
            calls.append(1)
            raise OSError("404")

        with patch.object(update, "_get", once):
            update.available_update(force=True)
            update.available_update()
            update.available_update()
        self.assertEqual(len(calls), 1)

    def _stored_check(self, checked_at):
        state = {
            "checked_at": checked_at,
            # What a build before this fix left behind: seconds since that
            # machine's previous boot. After a restart it is larger than the
            # current reading, which is the whole failure.
            "checked_at_monotonic": 500_000.0,
            "current_version": "0.1.0",
            "offer": {"version": "9.9.9", "asset": "cached.exe"},
            "error": "",
        }
        (self.home / "update-check.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

    def test_a_check_time_that_cannot_be_trusted_is_checked_again(self):
        """A restart used to stop the checking entirely.

        The freshness test compared a persisted time.monotonic() against this
        process's own. That clock counts from boot, so after a restart the
        stored value was the larger of the two and the elapsed time came out
        negative -- and a negative number is younger than any TTL, so the cached
        answer was served and no check ran until the machine had been up longer
        than it was when the value was written.
        """
        now = datetime.now(timezone.utc)
        cases = (
            ("checked an hour ago", (now - timedelta(hours=1)).isoformat(), 0),
            ("checked past the ttl", (now - timedelta(hours=7)).isoformat(), 1),
            ("checked in the future", (now + timedelta(days=3)).isoformat(), 1),
            ("unreadable check time", "not-a-timestamp", 1),
            ("no check time at all", None, 1),
        )
        for name, checked_at, expected_calls in cases:
            with self.subTest(name):
                self._stored_check(checked_at)
                calls = []

                def counted(*_args, **_kwargs):
                    calls.append(1)
                    raise OSError("404")

                with patch.object(update, "_get", counted):
                    offer = update.available_update()

                self.assertEqual(len(calls), expected_calls)
                if not expected_calls:
                    self.assertEqual(offer.get("version"), "9.9.9")

    def test_the_unusable_monotonic_stamp_is_no_longer_written(self):
        with patch.object(update, "_get", side_effect=OSError("404")):
            update.available_update(force=True)
        stored = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertIn("checked_at", stored)
        self.assertNotIn("checked_at_monotonic", stored)

    def test_a_state_file_we_cannot_read_is_not_replaced_with_an_empty_offer(self):
        """A failed read is not an empty check.

        `_read_state` treated every OSError as "no file", so a locked
        update-check.json became `{}`. The check then ran, failed, and wrote
        `{offer: {}, error: ...}` over a cached offer. The next panel open
        had nothing to show until a check succeeded.
        """
        original = json.dumps({
            "checked_at": "2026-09-07T00:00:00+00:00",
            "offer": {"version": "9.9.9", "asset": "cached.exe"},
            "error": "",
        })
        path = self.home / "update-check.json"
        path.write_text(original, encoding="utf-8")
        reported = []
        real_read = Path.read_text

        def read_text(self, *args, **kwargs):
            if self.name == "update-check.json":
                raise PermissionError("update-check.json is locked")
            return real_read(self, *args, **kwargs)

        with patch.object(Path, "read_text", read_text), \
                patch("sandglass.update.record_component_failure",
                      lambda name, exc: reported.append(name)), \
                patch.object(update, "_get", side_effect=OSError("404")):
            offer = update.available_update(force=True)

        self.assertEqual(offer, {})
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(reported, ["update_state_write"])


if __name__ == "__main__":
    unittest.main()
