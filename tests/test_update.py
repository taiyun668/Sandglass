"""What the update offer will and will not do.

The button is dark until a release offers something, and the offer is only
installable if it can be checked: a checksum published beside the installer, a
download that matches it, and either a trusted Windows signature or the
project's signed release manifest. The public unsigned installer route uses
the latter as its release identity.
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
        """States which world it is testing rather than inheriting the shipped key.

        This passed for as long as RELEASE_PUBLIC_KEY happened to be empty and
        broke the moment a real one was generated -- a test whose result came
        from the developer's machine rather than from what it asserts. The
        signed-manifest requirement has its own test; this one is about a
        release that carries a checksum, in a build that declares no key.
        """
        digest = "a" * 64
        with patch.object(update, "RELEASE_PUBLIC_KEY", ""), patch.object(
            update, "_get", return_value=(digest + "  " + self.ASSET).encode()
        ):
            offer = update.offer_from(self._release())
        self.assertEqual(offer["version"], "9.9.9")
        self.assertEqual(offer["sha256"], digest)
        self.assertEqual(offer["asset"], self.ASSET)

    def test_release_body_is_carried_as_plain_text_notes(self):
        release = self._release()
        release["body"] = "修复输入框\n\n- 保留本地数据"
        digest = "a" * 64
        with patch.object(update, "RELEASE_PUBLIC_KEY", ""), patch.object(
            update, "_get", return_value=(digest + "  " + self.ASSET).encode()
        ):
            offer = update.offer_from(release)
        self.assertEqual(offer["notes"], release["body"])
        self.assertIsInstance(offer["notes"], str)

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

    def test_a_pending_announcement_survives_a_fresh_update_check(self):
        state = {
            "pending_announcement": {"version": "9.9.9", "notes": "已安装"},
            "offer": {"version": "8.8.8"},
        }
        (self.home / "update-check.json").write_text(json.dumps(state), encoding="utf-8")
        with patch.object(update, "_get", side_effect=OSError("404")):
            update.available_update(force=True)
        stored = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["pending_announcement"], state["pending_announcement"])

    def test_announcement_is_only_visible_to_its_installed_version(self):
        state = {"pending_announcement": {
            "version": "9.9.9", "notes": "release notes", "published_at": "", "notes_url": "",
        }}
        (self.home / "update-check.json").write_text(json.dumps(state), encoding="utf-8")
        with patch.object(update, "__version__", "9.9.9"):
            self.assertEqual(update.update_announcement(), state["pending_announcement"])
        with patch.object(update, "__version__", "9.9.8"):
            self.assertEqual(update.update_announcement(), {})

    def test_dismiss_clears_only_the_exact_pending_version(self):
        pending = {"version": "9.9.9", "notes": "release notes"}
        (self.home / "update-check.json").write_text(
            json.dumps({"offer": {"version": "10.0.0"}, "pending_announcement": pending}),
            encoding="utf-8",
        )
        self.assertEqual(update.dismiss_update_announcement("9.9.8")["dismissed"], False)
        self.assertEqual(update.update_announcement(), {})
        stored = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["pending_announcement"], pending)
        self.assertTrue(update.dismiss_update_announcement("9.9.9")["dismissed"])
        stored = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertNotIn("pending_announcement", stored)


class ApplyUpdateStateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.dict(os.environ, {"SANDGLASS_HOME": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.home = Path(tmp.name)

    def test_verified_update_records_announcement_and_uses_update_parent_protocol(self):
        offer = {
            "asset": "Sandglass-9.9.9-windows-x64-setup.exe",
            "version": "9.9.9",
            "notes": "修复窗口",
            "published_at": "2026-09-08T00:00:00Z",
            "notes_url": "https://github.com/taiyun668/Sandglass/releases/tag/v9.9.9",
        }
        seen = []
        with patch.object(update, "download_verified", return_value=Path("setup.exe")), \
                patch("subprocess.Popen", lambda args, **kwargs: seen.append(args)):
            result = update.apply_update(offer)
        self.assertTrue(result["ok"])
        self.assertEqual(seen[0][1:3], ["/UPDATE", f"/PARENTPID={os.getpid()}"])
        self.assertEqual(seen[0][3], f"/RESTARTEXE={Path(update.sys.executable).resolve()}")
        stored = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["pending_announcement"], {
            "version": "9.9.9", "notes": "修复窗口",
            "published_at": offer["published_at"], "notes_url": offer["notes_url"],
        })


if __name__ == "__main__":
    unittest.main()


# A P-256 key pair generated once for these tests. The private half was thrown
# away after signing the vector below; nothing here is a release key.
_TEST_PUBLIC_KEY = (
    "CA3B30B13893B5CEFD938F7E6F2C407FCB3FCFCE8B60EA8192E8E01543BACE7D"
    "72D7C151EB817EF5F8A3B7CC04A88E584B47A09C88EA0AA7E13A11B855EB677B"
)
_TEST_PAYLOAD = b"sandglass release test"
_TEST_SIGNATURE = bytes.fromhex(
    "44E5C332103DB94148F732E1F426F531E8CF317AFC781AC34F39E0AFE36FC29F"
    "1EC50AD21ED5B4012924641D64A78548308B7A5C8CAA988A9D9D68BD04268630"
)


class ReleaseSignatureTests(unittest.TestCase):
    """A signature the Owner can make without owning a certificate.

    Authenticode answers whether Windows trusts the publisher, and that answer
    costs a certificate. This one answers whether the bytes came from the same
    place the last ones did, which is the question an updater actually has to
    ask, and it costs a key the Owner generates. Verified through Windows CNG,
    so no dependency and no hand-written crypto.
    """

    def test_the_key_verifies_what_it_signed(self):
        from sandglass.release_signature import verify_release_signature

        self.assertTrue(verify_release_signature(
            _TEST_PAYLOAD, _TEST_SIGNATURE, _TEST_PUBLIC_KEY))

    def test_everything_else_is_refused(self):
        from sandglass.release_signature import verify_release_signature

        tampered_signature = bytearray(_TEST_SIGNATURE)
        tampered_signature[0] ^= 1
        other_key = "aa" * 64
        cases = {
            "payload changed": (b"sandglass release TEST", _TEST_SIGNATURE, _TEST_PUBLIC_KEY),
            "signature changed": (_TEST_PAYLOAD, bytes(tampered_signature), _TEST_PUBLIC_KEY),
            "another key": (_TEST_PAYLOAD, _TEST_SIGNATURE, other_key),
            "no key at all": (_TEST_PAYLOAD, _TEST_SIGNATURE, ""),
            "key of the wrong size": (_TEST_PAYLOAD, _TEST_SIGNATURE, "ab" * 10),
            "signature of the wrong size": (_TEST_PAYLOAD, _TEST_SIGNATURE[:32], _TEST_PUBLIC_KEY),
        }
        for name, (payload, signature, key) in cases.items():
            with self.subTest(case=name):
                self.assertFalse(verify_release_signature(payload, signature, key))


class ManifestSignedUpdateTests(unittest.TestCase):
    """The half of the gate that does not need a certificate."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _serve(self, payload):
        class Response:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def read(self_inner, size=-1):
                chunk, Response.body = Response.body, b""
                return chunk

        Response.body = payload
        return lambda *a, **k: Response()

    def test_a_signed_manifest_stands_in_for_a_certificate(self):
        """Otherwise no build is installable until the certificate question is
        settled, and that question is not one the update channel needs answered.
        """
        payload = b"MZ" + bytes(32)
        target = self.root / "setup.exe"
        offer = {"url": "https://github.com/a/b/setup.exe", "asset": "setup.exe",
                 "sha256": hashlib.sha256(payload).hexdigest(), "manifest_signed": True}
        with patch("urllib.request.urlopen", self._serve(payload)), patch.object(
            update, "authenticode_valid", return_value=False
        ):
            kept = update.download_verified(offer, target)

        self.assertTrue(kept.exists())
        self.assertEqual(kept.read_bytes(), payload)

    def test_a_release_without_a_signed_manifest_is_not_offered_once_a_key_exists(self):
        """A published key means every later release carries a signed manifest.

        One that does not is either older than the key or not ours.
        """
        release = {
            "tag_name": "v9.9.9",
            "assets": [
                {"name": "Sandglass-9.9.9-windows-x64-setup.exe",
                 "browser_download_url": "https://github.com/a/b/setup.exe"},
                {"name": "SHA256SUMS.windows",
                 "browser_download_url": "https://github.com/a/b/SHA256SUMS.windows"},
            ],
        }
        sums = (b"a" * 64) + b" *Sandglass-9.9.9-windows-x64-setup.exe" + bytes((10,))
        with patch.object(update, "_get", return_value=sums):
            with patch.object(update, "RELEASE_PUBLIC_KEY", ""):
                self.assertTrue(update.offer_from(release), "没有密钥时行为不该改变")
            with patch.object(update, "RELEASE_PUBLIC_KEY", _TEST_PUBLIC_KEY):
                self.assertEqual(update.offer_from(release), {})
