"""What the update offer will and will not do.

The button is dark until a release's installer has been prepared and verified.
Confirmation refreshes the signed release identity and rehashes those local
bytes, but does not download the large file again.
"""

from __future__ import annotations

import errno
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
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
    ASSET = "Sandglass-9.9.9-windows-x64-unsigned-setup.exe"

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

    def _signed_release(self):
        release = self._release()
        release["assets"].append({
            "name": update.CHECKSUMS_SIGNATURE_NAME,
            "browser_download_url": "https://github.com/a/b/releases/x/sums.sig",
        })
        return release

    def test_manifest_fetch_failures_propagate_to_the_check(self):
        for error_type, error in (
            (urllib.error.URLError, urllib.error.URLError("offline")),
            (OSError, OSError("offline")),
            (ValueError, ValueError("bad manifest")),
        ):
            with self.subTest(error=error_type.__name__), patch.object(
                update, "RELEASE_PUBLIC_KEY", "11" * 64
            ), patch.object(update, "_get", side_effect=error):
                with self.assertRaises(error_type):
                    update.offer_from(self._signed_release())

    def test_signature_fetch_failures_propagate_to_the_check(self):
        digest = "a" * 64
        sums = (
            b"# Sandglass-Version: 9.9.9\n"
            + digest.encode()
            + b"  "
            + self.ASSET.encode()
            + b"\n"
        )
        for error_type, error in (
            (urllib.error.URLError, urllib.error.URLError("offline")),
            (OSError, OSError("offline")),
            (ValueError, ValueError("bad signature")),
        ):
            with self.subTest(error=error_type.__name__), patch.object(
                update, "RELEASE_PUBLIC_KEY", "11" * 64
            ), patch.object(update, "_get", side_effect=[sums, error]):
                with self.assertRaises(error_type):
                    update.offer_from(self._signed_release())

    def test_an_older_release_is_not_offered(self):
        self.assertEqual(update.offer_from(self._release(tag="v0.0.1")), {})

    def test_a_complete_release_is_offered_with_its_checksum(self):
        """A release carrying the required signed manifest is offered."""
        digest = "a" * 64
        with patch.object(
            update, "_get", return_value=(digest + "  " + self.ASSET).encode()
        ), patch.object(update, "_manifest_signature_ok", return_value=True):
            offer = update.offer_from(self._release())
        self.assertEqual(offer["version"], "9.9.9")
        self.assertEqual(offer["sha256"], digest)
        self.assertEqual(offer["asset"], self.ASSET)

    def test_release_body_is_carried_as_plain_text_notes(self):
        release = self._release()
        release["body"] = "修复输入框\n\n- 保留本地数据"
        digest = "a" * 64
        with patch.object(
            update, "_get", return_value=(digest + "  " + self.ASSET).encode()
        ), patch.object(update, "_manifest_signature_ok", return_value=True):
            offer = update.offer_from(release)
        self.assertEqual(offer["notes"], release["body"])
        self.assertIsInstance(offer["notes"], str)

    def test_a_checksum_file_that_omits_the_installer_offers_nothing(self):
        with patch.object(update, "_get", return_value=b"b" * 64 + b"  something-else.exe"):
            self.assertEqual(update.offer_from(self._release()), {})

    def test_duplicate_checksum_entries_for_the_installer_are_rejected(self):
        digest = "a" * 64
        sums = "\n".join((
            f"{digest}  {self.ASSET}",
            f"{'b' * 64}  {self.ASSET}",
        ))
        self.assertEqual(update.checksum_for(sums, self.ASSET), "")

    def test_release_tool_unsigned_setup_name_is_selected_but_portable_is_not(self):
        release = self._release()
        release["assets"] = [
            {"name": "Sandglass-9.9.9-windows-x64-unsigned-portable.zip",
             "browser_download_url": "https://github.com/a/b/portable.zip"},
            {"name": "Sandglass-9.9.9-windows-x64-unsigned-setup.exe",
             "browser_download_url": "https://github.com/a/b/setup.exe"},
            {"name": update.CHECKSUMS_NAME,
             "browser_download_url": "https://github.com/a/b/sums"},
        ]
        sums = b"a" * 64 + b"  Sandglass-9.9.9-windows-x64-unsigned-setup.exe\n"
        with patch.object(update, "_get", return_value=sums), patch.object(
            update, "_manifest_signature_ok", return_value=True
        ):
            offer = update.offer_from(release)
        self.assertEqual(offer["asset"],
                         "Sandglass-9.9.9-windows-x64-unsigned-setup.exe")

    def test_installer_from_another_version_is_not_selected(self):
        release = self._release()
        release["assets"][0]["name"] = "Sandglass-8.8.8-windows-x64-unsigned-setup.exe"
        with patch.object(update, "_get", return_value=b"a" * 64):
            self.assertEqual(update.offer_from(release), {})

    def test_retired_signing_variant_is_not_selected(self):
        release = self._release()
        unsigned = "Sandglass-9.9.9-windows-x64-unsigned-setup.exe"
        outer = "Sandglass-9.9.9-windows-x64-unsigned-outer-setup.exe"
        self.assertFalse(
            update._installer_name(outer, "9.9.9"),
            "retired signing-pipeline variants must not re-enter the update channel",
        )
        release["assets"] = [
            {"name": unsigned, "browser_download_url": "https://github.com/a/b/u.exe"},
            {"name": outer, "browser_download_url": "https://github.com/a/b/o.exe"},
            {"name": update.CHECKSUMS_NAME,
             "browser_download_url": "https://github.com/a/b/sums"},
        ]
        sums = (b"a" * 64 + b"  " + unsigned.encode() + b"\n" +
                b"b" * 64 + b"  " + outer.encode() + b"\n")
        with patch.object(update, "_get", return_value=sums), patch.object(
            update, "_manifest_signature_ok", return_value=True
        ):
            offer = update.offer_from(release)
        self.assertEqual(offer["asset"], unsigned)


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

    def test_a_matching_download_without_a_signed_manifest_is_refused(self):
        """The checksum only proves it is the file they published.

        It says nothing about who published it, which is the question that
        decides whether this is safe to execute.
        """
        payload = b"MZ" + b"\x00" * 32
        target = self.root / "setup.exe"
        # A Windows-trusted publisher is deliberately not an alternate update
        # identity. `create=True` makes this a mutation shield: restoring the
        # retired authenticode_valid() OR branch would consume the fake True
        # and wrongly accept this unsigned manifest.
        with patch("urllib.request.urlopen", self._serve(payload)), patch.object(
            update, "authenticode_valid", return_value=True, create=True
        ):
            with self.assertRaises(ValueError) as caught:
                update.download_verified(self._offer(payload), target)
        self.assertIn("signed", str(caught.exception))
        self.assertFalse(target.exists())

    def test_a_matching_signed_download_is_kept(self):
        """The counterpart: refusing everything would pass the tests above."""
        payload = b"MZ" + b"\x00" * 32
        target = self.root / "setup.exe"
        offer = self._offer(payload)
        offer["manifest_signed"] = True
        with patch("urllib.request.urlopen", self._serve(payload)):
            kept = update.download_verified(offer, target)
        self.assertTrue(kept.exists())
        self.assertEqual(kept.read_bytes(), payload)

    def test_an_offer_without_a_usable_checksum_never_downloads(self):
        called = []
        with patch("urllib.request.urlopen", lambda *a, **k: called.append(1)):
            with self.assertRaises(ValueError):
                update.download_verified({"url": "https://github.com/a/b", "sha256": "nope"},
                                         self.root / "setup.exe")
        self.assertEqual(called, [])


class CheckStateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.dict(os.environ, {"SANDGLASS_HOME": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.home = Path(tmp.name)

    def _as_started(self):
        # Existing cache tests model a process that has already made its first
        # check. ``create=True`` keeps these regression tests runnable against
        # the old production module before the in-memory flag exists.
        return patch.object(update, "_FIRST_AVAILABLE_UPDATE_DONE", True, create=True)

    def test_no_release_means_no_offer_and_no_crash(self):
        with patch.object(update, "_get", side_effect=OSError("404")):
            self.assertEqual(update.available_update(force=True), {})
        stored = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertNotIn("offer", stored)
        self.assertEqual(stored, {"check_generation": 1, "error": "OSError"})

    def test_a_check_is_cached_rather_than_repeated(self):
        calls = []

        def once(*args, **kwargs):
            calls.append(1)
            return b'{"tag_name":"v0.0.1","assets":[]}'

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

                with self._as_started(), patch.object(update, "_get", counted):
                    offer = update.available_update()

                self.assertEqual(len(calls), expected_calls)
                if not expected_calls:
                    self.assertEqual(offer.get("version"), "9.9.9")

    def test_a_fresh_process_checks_once_before_using_a_fresh_disk_cache(self):
        self._fresh_offer_state(
            {"version": "9.9.9", "asset": "cached.exe"},
            checked_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        )
        child_code = (
            "import json,sys\n"
            "from sandglass import update\n"
            "calls=[]\n"
            "def stub(*_args,**_kwargs):\n"
            "    calls.append(1)\n"
            "    return b'{\"tag_name\":\"v0.0.1\",\"assets\":[]}'\n"
            "update._get=stub\n"
            "first=update.available_update()\n"
            "after_first_call=len(calls)\n"
            "second=update.available_update()\n"
            "after_second_call=len(calls)\n"
            "print(json.dumps({'after_first_call':after_first_call,'after_second_call':after_second_call,'first':first,'second':second}))\n"
        )
        child_env = os.environ.copy()
        child_env["SANDGLASS_HOME"] = str(self.home)
        child = subprocess.run(
            [sys.executable, "-c", child_code],
            cwd=Path(__file__).resolve().parents[1],
            env=child_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        result = json.loads(child.stdout)
        self.assertEqual(
            [result["after_first_call"], result["after_second_call"]], [1, 1]
        )
        self.assertEqual(result["first"], {})
        self.assertEqual(result["second"], {})

    def test_a_state_read_failure_does_not_consume_startup_bypass(self):
        self._fresh_offer_state(
            {"version": "9.9.9", "asset": "cached.exe"},
            checked_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        )
        child_code = (
            "import json\n"
            "from sandglass import update\n"
            "real_read=update._read_state\n"
            "read_attempts=[]\n"
            "def read_once():\n"
            "    read_attempts.append(1)\n"
            "    if len(read_attempts)==1:\n"
            "        raise OSError('transient state read')\n"
            "    return real_read()\n"
            "update._read_state=read_once\n"
            "calls=[]\n"
            "def stub(*_args,**_kwargs):\n"
            "    calls.append(1)\n"
            "    return b'{\"tag_name\":\"v0.0.1\",\"assets\":[]}'\n"
            "update._get=stub\n"
            "first=update.available_update()\n"
            "after_first_call=len(calls)\n"
            "second=update.available_update()\n"
            "after_second_call=len(calls)\n"
            "print(json.dumps({'after_first_call':after_first_call,'after_second_call':after_second_call,'first':first,'second':second}))\n"
        )
        child_env = os.environ.copy()
        child_env["SANDGLASS_HOME"] = str(self.home)
        child = subprocess.run(
            [sys.executable, "-c", child_code],
            cwd=Path(__file__).resolve().parents[1],
            env=child_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        result = json.loads(child.stdout)
        self.assertEqual(
            [result["after_first_call"], result["after_second_call"]], [0, 1]
        )
        self.assertEqual(result["first"], {})
        self.assertEqual(result["second"], {})

    def _fresh_offer_state(self, offer, **extra):
        """A still-fresh cache, as left on disk by an older process."""
        state = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "current_version": "0.1.1",
            "offer": offer,
            "error": "",
        }
        state.update(extra)
        (self.home / "update-check.json").write_text(
            json.dumps(state), encoding="utf-8"
        )
        return state

    def test_failed_feed_manifest_and_signature_checks_preserve_cached_offer(self):
        cached = {
            "version": "9.9.9",
            "asset": "cached.exe",
            "url": "https://github.com/a/b/cached.exe",
            "sha256": "a" * 64,
            "manifest_signed": True,
        }
        asset = "Sandglass-9.9.9-windows-x64-unsigned-setup.exe"
        sums_url = "https://github.com/a/b/releases/x/sums"
        signature_url = "https://github.com/a/b/releases/x/sums.sig"
        release = {
            "tag_name": "v9.9.9",
            "assets": [
                {"name": asset, "browser_download_url": "https://github.com/a/b/releases/x/setup.exe"},
                {"name": update.CHECKSUMS_NAME, "browser_download_url": sums_url},
                {"name": update.CHECKSUMS_SIGNATURE_NAME, "browser_download_url": signature_url},
            ],
        }
        sums = (
            b"# Sandglass-Version: 9.9.9\n"
            + b"a" * 64
            + b"  "
            + asset.encode()
            + b"\n"
        )
        cases = (
            ("feed", urllib.error.URLError, 2),
            ("feed", OSError, 2),
            ("feed", ValueError, 2),
            ("feed", http.client.IncompleteRead, 2),
            ("manifest", urllib.error.URLError, 4),
            ("manifest", OSError, 4),
            ("manifest", ValueError, 4),
            ("signature", urllib.error.URLError, 6),
            ("signature", OSError, 6),
            ("signature", ValueError, 6),
        )
        for phase, error_type, expected_calls in cases:
            with self.subTest(phase=phase, error=error_type.__name__):
                original = self._fresh_offer_state(
                    cached,
                    pending_announcement={"version": "8.8.8", "notes": "keep"},
                    unrelated={"keep": True},
                )
                calls = []

                def failing_get(url, *_args, **_kwargs):
                    calls.append(url)
                    if phase == "feed":
                        if error_type is http.client.IncompleteRead:
                            raise error_type(b"partial", 100)
                        raise error_type("offline")
                    if url == update.FEED_URL:
                        return json.dumps(release).encode()
                    if phase == "manifest":
                        raise error_type("offline")
                    if url == sums_url:
                        return sums
                    if phase == "signature":
                        raise error_type("offline")
                    raise AssertionError(f"unexpected request: {url}")

                with patch.object(update, "_get", side_effect=failing_get):
                    if phase == "signature":
                        with patch.object(update, "RELEASE_PUBLIC_KEY", "11" * 64):
                            shown_after_force = update.available_update(force=True)
                            shown_after_retry = update.available_update()
                    else:
                        shown_after_force = update.available_update(force=True)
                        shown_after_retry = update.available_update()

                stored = json.loads(
                    (self.home / "update-check.json").read_text(encoding="utf-8")
                )
                expected = dict(original)
                expected["check_generation"] = 2
                expected["error"] = error_type.__name__
                with self.subTest(assertion="request_count"):
                    self.assertEqual(
                        len(calls), expected_calls,
                        "each failed check must issue a real feed request before retrying",
                    )
                with self.subTest(assertion="force_returned_offer"):
                    self.assertEqual(
                        shown_after_force,
                        cached,
                        "a failed force/display check must retain the still-newer badge",
                    )
                with self.subTest(assertion="retry_returned_offer"):
                    self.assertEqual(
                        shown_after_retry,
                        cached,
                        "the natural retry must retain the still-newer badge",
                    )
                with self.subTest(assertion="persisted_state"):
                    self.assertEqual(
                        stored,
                        expected,
                        "a failed check may record only its error; cached offer and timestamps must stay intact",
                    )

    def test_a_completed_check_with_an_invalid_signature_clears_cached_offer(self):
        cached = {
            "version": "9.9.9",
            "asset": "cached.exe",
            "url": "https://github.com/a/b/cached.exe",
            "sha256": "a" * 64,
            "manifest_signed": True,
        }
        original = self._fresh_offer_state(
            cached,
            checked_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            current_version="0.1.1",
            error="OSError",
            pending_announcement={"version": "8.8.8"},
            unrelated={"keep": True},
        )
        asset = "Sandglass-9.9.9-windows-x64-unsigned-setup.exe"
        sums_url = "https://github.com/a/b/releases/x/sums"
        signature_url = "https://github.com/a/b/releases/x/sums.sig"
        release = {
            "tag_name": "v9.9.9",
            "assets": [
                {"name": asset, "browser_download_url": "https://github.com/a/b/releases/x/setup.exe"},
                {"name": update.CHECKSUMS_NAME, "browser_download_url": sums_url},
                {"name": update.CHECKSUMS_SIGNATURE_NAME, "browser_download_url": signature_url},
            ],
        }
        sums = (
            b"# Sandglass-Version: 9.9.9\n"
            + b"a" * 64
            + b"  "
            + asset.encode()
            + b"\n"
        )
        calls = []

        def stub(url, *_args, **_kwargs):
            calls.append(url)
            if url == update.FEED_URL:
                return json.dumps(release).encode()
            if url == sums_url:
                return sums
            if url == signature_url:
                return b"\x00" * 64
            raise AssertionError(f"unexpected request: {url}")

        with patch.object(update, "RELEASE_PUBLIC_KEY", "11" * 64), patch.object(
            update, "_get", side_effect=stub
        ), patch(
            "sandglass.release_signature.verify_release_signature",
            return_value=False,
        ):
            result = update.available_update(force=True)

        stored = json.loads(
            (self.home / "update-check.json").read_text(encoding="utf-8")
        )
        self.assertEqual(calls, [update.FEED_URL, sums_url, signature_url])
        self.assertEqual(result, {})
        self.assertEqual(stored["offer"], {})
        self.assertEqual(stored["error"], "")
        self.assertEqual(stored["current_version"], update.__version__)
        self.assertNotEqual(stored["checked_at"], original["checked_at"])
        self.assertEqual(stored["pending_announcement"], original["pending_announcement"])
        self.assertEqual(stored["unrelated"], original["unrelated"])

    def test_required_fresh_failure_keeps_disk_offer_but_returns_nothing(self):
        cached = {"version": "9.9.9", "asset": "cached.exe"}
        original = self._fresh_offer_state(
            cached,
            pending_announcement={"version": "8.8.8"},
        )
        with patch.object(update, "_get", side_effect=OSError("offline")):
            self.assertEqual(
                update.available_update(force=True, require_fresh=True), {}
            )
        stored = json.loads(
            (self.home / "update-check.json").read_text(encoding="utf-8")
        )
        expected = dict(original)
        expected["check_generation"] = 1
        expected["error"] = "OSError"
        self.assertEqual(stored, expected)

    def test_require_fresh_bypasses_cache_even_without_force(self):
        self._fresh_offer_state(
            {"version": "9.9.9", "asset": "cached.exe"},
        )
        calls = []

        def once(*_args, **_kwargs):
            calls.append(1)
            return b'{"tag_name":"v0.0.1","assets":[]}'

        with self._as_started(), patch.object(update, "_get", once):
            result = update.available_update(force=False, require_fresh=True)
        self.assertEqual(calls, [1])
        self.assertEqual(result, {})

    def test_cached_offer_matching_this_process_is_cleared_not_shown(self):
        """0.1.1 cached a 0.1.3 offer; 0.1.3 must not badge it as available."""
        pending = {"version": "0.1.3", "notes": "已安装"}
        self._fresh_offer_state(
            {"version": "0.1.3", "asset": "cached.exe"},
            pending_announcement=pending,
            unrelated={"keep": True},
        )
        calls = []
        with self._as_started(), patch.object(update, "__version__", "0.1.3"), patch.object(
            update, "_get", lambda *a, **k: calls.append(1)
        ):
            offer = update.available_update()
        self.assertEqual(offer, {})
        self.assertEqual(calls, [])
        stored = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["offer"], {})
        self.assertEqual(stored["pending_announcement"], pending)
        self.assertEqual(stored["unrelated"], {"keep": True})
        self.assertEqual(stored["current_version"], "0.1.1")

    def test_older_or_malformed_cached_offers_are_cleared_not_shown(self):
        pending = {"version": "0.1.3", "notes": "已安装"}
        cases = (
            ("older", {"version": "0.1.2", "asset": "old.exe"}),
            ("empty version", {"version": "", "asset": "empty.exe"}),
            ("nonsense", {"version": "latest", "asset": "tag.exe"}),
            ("missing version", {"asset": "no-version.exe"}),
            ("non-string version", {"version": 13, "asset": "int.exe"}),
        )
        for name, cached in cases:
            with self.subTest(name):
                self._fresh_offer_state(
                    cached,
                    pending_announcement=pending,
                    unrelated={"keep": name},
                )
                calls = []
                with self._as_started(), patch.object(update, "__version__", "0.1.3"), patch.object(
                    update, "_get", lambda *a, **k: calls.append(1)
                ):
                    offer = update.available_update()
                self.assertEqual(offer, {})
                self.assertEqual(calls, [])
                stored = json.loads(
                    (self.home / "update-check.json").read_text(encoding="utf-8")
                )
                self.assertEqual(stored["offer"], {})
                self.assertEqual(stored["pending_announcement"], pending)
                self.assertEqual(stored["unrelated"], {"keep": name})

    def test_a_newer_cached_offer_is_returned_without_a_network_call(self):
        cached = {"version": "0.1.4", "asset": "newer.exe", "sha256": "a" * 64}
        pending = {"version": "0.1.3", "notes": "已安装"}
        original = self._fresh_offer_state(
            cached,
            pending_announcement=pending,
            unrelated={"keep": True},
        )
        calls = []
        with self._as_started(), patch.object(update, "__version__", "0.1.3"), patch.object(
            update, "_get", lambda *a, **k: calls.append(1)
        ):
            offer = update.available_update()
        self.assertEqual(offer, cached)
        self.assertIsNot(offer, cached)
        self.assertEqual(calls, [])
        stored = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertEqual(stored, original)

    def test_a_failed_check_does_not_write_a_freshness_stamp(self):
        with patch.object(update, "_get", side_effect=OSError("404")):
            update.available_update(force=True)
        stored = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertNotIn("checked_at", stored)
        self.assertNotIn("checked_at_monotonic", stored)
        self.assertEqual(stored["error"], "OSError")

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
            with self.assertRaises(update.UpdateStateLockError):
                update.available_update(force=True)

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

    def test_fresh_check_merges_state_written_while_network_request_was_in_flight(self):
        initial = {"offer": {"version": "8.8.8"}}
        (self.home / "update-check.json").write_text(json.dumps(initial), encoding="utf-8")
        entered = []

        def slow_feed(*_args, **_kwargs):
            # A second process must be able to take the short state lock while
            # this request is in flight. If the network path held that lock,
            # this bounded child would time out instead of writing the update.
            child_code = (
                "import json,os,sys\n"
                "os.environ['SANDGLASS_HOME']=sys.argv[1]\n"
                "from sandglass import update\n"
                "with update._state_transaction():\n"
                "    state=update._read_state(); state['pending_announcement']={'version':'9.9.9'}\n"
                "    update._write_state(state)\n"
            )
            child = subprocess.run(
                [sys.executable, "-c", child_code, str(self.home)],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True, text=True, timeout=3,
            )
            self.assertEqual(child.returncode, 0, child.stderr)
            entered.append(True)
            raise OSError("offline")

        with patch.object(update, "_get", slow_feed):
            update.available_update(force=True)
        stored = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertTrue(entered)
        self.assertEqual(stored["pending_announcement"], {"version": "9.9.9"})

    def test_later_started_check_cannot_be_overwritten_by_older_completion(self):
        old_started = threading.Event()
        release_old = threading.Event()
        old_offer = {"version": "9.9.8", "asset": "old.exe"}
        new_offer = {"version": "9.9.9", "asset": "new.exe"}
        results = {}

        def get(url, limit, timeout=20.0):
            return json.dumps({"which": threading.current_thread().name}).encode()

        def parsed(release):
            if release["which"] == "old-check":
                old_started.set()
                self.assertTrue(release_old.wait(5))
                return old_offer
            return new_offer

        def run(name):
            results[name] = update.available_update(force=True)

        with patch.object(update, "_get", side_effect=get), patch.object(
            update, "offer_from", side_effect=parsed
        ):
            old = threading.Thread(target=run, args=("old",), name="old-check")
            new = threading.Thread(target=run, args=("new",), name="new-check")
            old.start()
            self.assertTrue(old_started.wait(2), "older check never reached network")
            new.start()
            new.join(timeout=5)
            self.assertFalse(new.is_alive(), "newer check did not commit")
            release_old.set()
            old.join(timeout=5)
            self.assertFalse(old.is_alive(), "older check did not finish")

        stored = json.loads(update.state_path().read_text(encoding="utf-8"))
        self.assertEqual(stored["offer"], new_offer)
        self.assertEqual(results, {"new": new_offer, "old": new_offer})
        self.assertEqual(stored["check_generation"], 2)

    def test_two_process_state_transactions_do_not_lose_an_update(self):
        """The lock must serialize a real cross-process read/modify/write."""
        child_code = (
            "import os,sys,time\n"
            "os.environ['SANDGLASS_HOME']=sys.argv[1]\n"
            "from sandglass import update\n"
            "with update._state_transaction():\n"
            "    state=update._read_state(); time.sleep(.20)\n"
            "    state[sys.argv[2]]='written'; update._write_state(state)\n"
        )
        children = [subprocess.Popen(
            [sys.executable, "-c", child_code, str(self.home), key],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) for key in ("first", "second")]
        results = [child.communicate(timeout=10) for child in children]
        self.assertEqual([child.returncode for child in children], [0, 0], results)
        state = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertEqual(state.get("first"), "written")
        self.assertEqual(state.get("second"), "written")

    def test_gate_initialization_failure_is_not_reported_as_busy(self):
        with patch.object(Path, "open", side_effect=PermissionError("cannot create lock")):
            with self.assertRaises(update.UpdateGateError):
                update._acquire_update_gate()
        self.assertIsNone(update._UPDATE_GATE)

    def test_state_lock_initialization_failure_has_its_own_error(self):
        with patch.object(Path, "open", side_effect=PermissionError("cannot create lock")):
            with self.assertRaises(update.UpdateStateLockError):
                with update._state_transaction():
                    self.fail("state transaction unexpectedly acquired a lock")

    @unittest.skipUnless(os.name == "nt", "Windows msvcrt lock contract")
    def test_state_lock_contention_keeps_its_single_bounded_os_wait(self):
        import msvcrt

        locked = OSError(errno.EACCES, "held by another process")
        with patch.object(msvcrt, "locking", side_effect=locked) as locking:
            with self.assertRaises(update.UpdateStateLockError):
                with update._state_transaction():
                    self.fail("contended state lock unexpectedly opened")
        self.assertEqual(
            locking.call_count, 1,
            "only the background prepare gate may repeat a contention wait",
        )

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


class PreparedUpdateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.dict(os.environ, {"SANDGLASS_HOME": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.home = Path(tmp.name)
        update._PREPARE_WORKER = None
        update._PREPARE_PENDING = False
        update._PREPARE_PENDING_FORCE = False
        self.addCleanup(update._release_update_gate)

    @staticmethod
    def _offer(payload=b"prepared installer", version="9.9.9"):
        asset = f"Sandglass-{version}-windows-x64-unsigned-setup.exe"
        return {
            "asset": asset,
            "version": version,
            "url": f"https://github.com/example/project/releases/download/v{version}/{asset}",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "manifest_signed": True,
        }

    def _join_worker(self):
        while True:
            worker = update._PREPARE_WORKER
            if worker is None:
                return
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive(), "update preparation worker did not stop")

    def test_request_arriving_at_worker_return_is_coalesced_and_prepared(self):
        old_payload = b"ready before refresh"
        old_offer = self._offer(old_payload, version="9.9.8")
        self._seed_ready(old_offer, old_payload)
        new_payload = b"successor requested at return"
        new_offer = self._offer(new_payload, version="9.9.9")
        worker_at_return = threading.Event()
        release_worker = threading.Event()
        real_clear = update.clear_component_failure
        checks = []
        downloads = []

        def checked(force=False):
            checks.append(force)
            stored = json.loads(update.state_path().read_text(encoding="utf-8"))
            return stored.get("offer") or {}

        def clear(component):
            if component == "update_stage_download" and not worker_at_return.is_set():
                worker_at_return.set()
                self.assertTrue(release_worker.wait(5))
            return real_clear(component)

        def download(found, into):
            downloads.append(found["version"])
            into.parent.mkdir(parents=True, exist_ok=True)
            into.write_bytes(new_payload)
            return into

        with patch.object(update, "available_update", side_effect=checked), \
             patch.object(update, "clear_component_failure", side_effect=clear), \
             patch.object(update, "download_verified", side_effect=download):
            initial = update.request_update_check()
            self.assertTrue(initial["preparing"])
            self.assertTrue(worker_at_return.wait(2), "worker did not reach return window")
            stored = json.loads(update.state_path().read_text(encoding="utf-8"))
            stored["offer"] = new_offer
            update.state_path().write_text(json.dumps(stored), encoding="utf-8")
            queued = update.request_update_check(force=True)
            self.assertTrue(queued["preparing"])
            self.assertTrue(update._PREPARE_PENDING)
            release_worker.set()
            self._join_worker()

        final = update.prepared_update_status()
        self.assertEqual(checks, [False, True])
        self.assertEqual(downloads, ["9.9.9"])
        self.assertTrue(final["ready"])
        self.assertEqual(final["version"], "9.9.9")
        self.assertFalse(update._PREPARE_PENDING)

    def _seed_ready(self, offer, payload=b"prepared installer"):
        path = update.staged_installer_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        update.state_path().write_text(json.dumps({
            "offer": offer,
            update._STAGED_FIELD: {
                **update._offer_identity(offer),
                "status": "ready",
                "error": "",
            },
        }), encoding="utf-8")
        return path

    def test_detection_downloads_in_background_without_executing(self):
        payload = b"prepared installer"
        offer = self._offer(payload)
        download_started = threading.Event()
        release_download = threading.Event()
        calls = []

        def checked(force=False):
            update.state_path().write_text(
                json.dumps({
                    "offer": offer,
                    "error": "",
                    "pending_announcement": {"version": "8.8.8"},
                }),
                encoding="utf-8",
            )
            return offer

        def download(found, into):
            calls.append((found, into))
            download_started.set()
            self.assertTrue(release_download.wait(5))
            into.parent.mkdir(parents=True, exist_ok=True)
            into.write_bytes(payload)
            return into

        with patch.object(update, "available_update", side_effect=checked), \
             patch.object(update, "download_verified", side_effect=download), \
             patch("subprocess.Popen") as popen:
            status = update.request_update_check()
            self.assertTrue(status["preparing"])
            self.assertFalse(status["ready"])
            self.assertTrue(download_started.wait(2), "background download never started")
            popen.assert_not_called()
            self.assertFalse(update.staged_installer_path().exists())
            release_download.set()
            self._join_worker()
            ready = update.prepared_update_status()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], update.staged_partial_path())
        self.assertTrue(ready["ready"])
        self.assertFalse(ready["preparing"])
        self.assertEqual(ready["version"], offer["version"])
        self.assertEqual(update.staged_installer_path().read_bytes(), payload)
        self.assertFalse(update.staged_partial_path().exists())
        stored = json.loads(update.state_path().read_text(encoding="utf-8"))
        self.assertEqual(stored["pending_announcement"], {"version": "8.8.8"})

    def test_status_keeps_polling_while_metadata_check_is_still_running(self):
        payload = b"installer after slow metadata"
        offer = self._offer(payload)
        check_started = threading.Event()
        release_check = threading.Event()

        def checked(force=False):
            check_started.set()
            self.assertTrue(release_check.wait(5))
            update.state_path().write_text(
                json.dumps({"offer": offer, "error": ""}), encoding="utf-8"
            )
            return offer

        def download(_found, into):
            into.parent.mkdir(parents=True, exist_ok=True)
            into.write_bytes(payload)
            return into

        with patch.object(update, "available_update", side_effect=checked), \
             patch.object(update, "download_verified", side_effect=download):
            initial = update.request_update_check()
            self.assertTrue(check_started.wait(2), "metadata check never started")
            during = update.prepared_update_status()
            self.assertTrue(initial["preparing"])
            self.assertTrue(during["preparing"])
            self.assertFalse(during["ready"])
            release_check.set()
            self._join_worker()
            final = update.prepared_update_status()

        self.assertTrue(final["ready"])
        self.assertFalse(final["preparing"])
        self.assertEqual(final["version"], offer["version"])

    def test_status_is_local_and_never_starts_or_retries_work(self):
        payload = b"prepared installer"
        offer = self._offer(payload)
        update.state_path().write_text(json.dumps({
            "offer": offer,
            update._STAGED_FIELD: {
                **update._offer_identity(offer),
                "status": "downloading",
                "error": "",
            },
        }), encoding="utf-8")
        with patch.object(update, "available_update", side_effect=AssertionError), \
             patch.object(update, "download_verified", side_effect=AssertionError):
            self.assertEqual(
                update.prepared_update_status(),
                {"preparing": True, "ready": False},
            )
        self.assertIsNone(update._PREPARE_WORKER)

    def test_a_ready_identity_is_reused_without_downloading_the_exe_again(self):
        payload = b"prepared installer"
        offer = self._offer(payload)
        staged = self._seed_ready(offer, payload)
        before = staged.read_bytes()
        with patch.object(update, "available_update", return_value=offer), \
             patch.object(update, "download_verified", side_effect=AssertionError):
            first = update.request_update_check(force=True)
            self.assertTrue(first["ready"])
            self._join_worker()
        self.assertEqual(staged.read_bytes(), before)
        self.assertTrue(update.prepared_update_status()["ready"])

    def test_ready_file_disappearing_after_queue_probe_is_downloaded_again(self):
        payload = b"replacement for missing ready bytes"
        offer = self._offer(payload)
        staged = self._seed_ready(offer, payload)
        calls = []
        real_acquire = update._acquire_update_gate

        def acquire(*, blocking=False):
            staged.unlink(missing_ok=True)
            return real_acquire(blocking=blocking)

        def download(found, into):
            calls.append(found["version"])
            into.parent.mkdir(parents=True, exist_ok=True)
            into.write_bytes(payload)
            return into

        with patch.object(update, "_acquire_update_gate", side_effect=acquire), \
             patch.object(update, "download_verified", side_effect=download):
            update._reconcile_prepared_update()

        self.assertEqual(calls, ["9.9.9"])
        self.assertTrue(update.prepared_update_status()["ready"])
        self.assertEqual(staged.read_bytes(), payload)

    def test_gate_initialization_failure_turns_queued_into_failed(self):
        offer = self._offer()
        update.state_path().write_text(
            json.dumps({"offer": offer, "error": ""}), encoding="utf-8"
        )
        with patch.object(
            update, "_acquire_update_gate",
            side_effect=update.UpdateGateError("gate unavailable"),
        ):
            update._reconcile_prepared_update()

        stored = json.loads(update.state_path().read_text(encoding="utf-8"))
        staged = stored[update._STAGED_FIELD]
        self.assertEqual(staged["status"], "failed")
        self.assertEqual(staged["error"], "UpdateGateError")
        self.assertEqual(
            update.prepared_update_status(),
            {"preparing": False, "ready": False},
        )

    def test_old_ready_offer_keeps_polling_until_new_identity_is_ready(self):
        old_payload = b"old prepared installer"
        old_offer = self._offer(old_payload, version="9.9.8")
        self._seed_ready(old_offer, old_payload)
        new_payload = b"new prepared installer"
        new_offer = self._offer(new_payload, version="9.9.9")
        check_started = threading.Event()
        release_check = threading.Event()

        def checked(force=False):
            check_started.set()
            self.assertTrue(release_check.wait(5))
            stored = json.loads(update.state_path().read_text(encoding="utf-8"))
            stored.update({"offer": new_offer, "error": ""})
            update.state_path().write_text(json.dumps(stored), encoding="utf-8")
            return new_offer

        def download(found, into):
            self.assertEqual(found, new_offer)
            into.parent.mkdir(parents=True, exist_ok=True)
            into.write_bytes(new_payload)
            return into

        with patch.object(update, "available_update", side_effect=checked), \
             patch.object(update, "download_verified", side_effect=download):
            initial = update.request_update_check(force=True)
            self.assertTrue(check_started.wait(2), "metadata refresh never started")
            self.assertTrue(initial["ready"])
            self.assertTrue(initial["preparing"])
            self.assertEqual(initial["version"], old_offer["version"])
            release_check.set()
            self._join_worker()
            final = update.prepared_update_status()

        self.assertTrue(final["ready"])
        self.assertFalse(final["preparing"])
        self.assertEqual(final["version"], new_offer["version"])
        self.assertEqual(update.staged_installer_path().read_bytes(), new_payload)

    def test_waiting_contender_prepares_successor_committed_after_owner_ready(self):
        payloads = {
            "9.9.8": b"first candidate",
            "9.9.9": b"successor candidate",
        }
        first = self._offer(payloads["9.9.8"], version="9.9.8")
        successor = self._offer(payloads["9.9.9"], version="9.9.9")
        update.state_path().write_text(
            json.dumps({"offer": first, "error": ""}), encoding="utf-8"
        )
        first_ready_committed = threading.Event()
        release_owner = threading.Event()
        successor_queued = threading.Event()
        calls = []
        active = 0
        maximum_active = 0
        calls_lock = threading.Lock()
        real_set_stage_state = update._set_stage_state

        def set_stage_state(identity, status, **kwargs):
            result = real_set_stage_state(identity, status, **kwargs)
            if identity["version"] == "9.9.8" and status == "ready" and result:
                first_ready_committed.set()
                self.assertTrue(release_owner.wait(5))
            if identity["version"] == "9.9.9" and status == "queued" and result:
                successor_queued.set()
            return result

        def download(found, into):
            nonlocal active, maximum_active
            version = found["version"]
            with calls_lock:
                calls.append(version)
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                into.parent.mkdir(parents=True, exist_ok=True)
                into.write_bytes(payloads[version])
                return into
            finally:
                with calls_lock:
                    active -= 1

        with patch.object(update, "download_verified", side_effect=download), \
             patch.object(update, "_set_stage_state", side_effect=set_stage_state):
            owner = threading.Thread(target=update._prepare_offer, args=(first,))
            contender = None
            try:
                owner.start()
                first_committed = first_ready_committed.wait(2)
                stored = json.loads(update.state_path().read_text(encoding="utf-8"))
                stored["offer"] = successor
                update.state_path().write_text(json.dumps(stored), encoding="utf-8")
                contender = threading.Thread(
                    target=update._prepare_offer, args=(successor,)
                )
                contender.start()
                queued_visible = successor_queued.wait(2)
                during = update.prepared_update_status()
                contender.join(timeout=0.2)
                contender_waited = contender.is_alive()
            finally:
                release_owner.set()
                owner.join(timeout=5)
                if contender is not None:
                    contender.join(timeout=5)

        self.assertTrue(
            first_committed,
            "first ready state was not committed while the gate was held",
        )
        self.assertTrue(queued_visible, "waiting successor was not exposed as queued")
        self.assertEqual(during, {"preparing": True, "ready": False})
        self.assertTrue(
            contender_waited,
            "successor contender returned while the previous owner held the gate",
        )
        self.assertFalse(owner.is_alive(), "first gate owner did not return")
        self.assertFalse(
            contender.is_alive(), "waiting contender did not prepare successor"
        )

        final = update.prepared_update_status()
        self.assertEqual(calls, ["9.9.8", "9.9.9"])
        self.assertEqual(maximum_active, 1)
        self.assertTrue(final["ready"])
        self.assertEqual(final["version"], "9.9.9")
        self.assertEqual(
            update.staged_installer_path().read_bytes(), payloads["9.9.9"]
        )
        self.assertFalse(update.staged_partial_path().exists())

    def test_waiting_reconciler_clears_ready_cache_after_completed_no_update(self):
        payload = b"candidate to clear"
        offer = self._offer(payload, version="9.9.8")
        update.state_path().write_text(
            json.dumps({"offer": offer, "error": ""}), encoding="utf-8"
        )
        ready_committed = threading.Event()
        release_owner = threading.Event()
        real_set_stage_state = update._set_stage_state

        def set_stage_state(identity, status, **kwargs):
            result = real_set_stage_state(identity, status, **kwargs)
            if status == "ready" and result:
                ready_committed.set()
                self.assertTrue(release_owner.wait(5))
            return result

        def download(_found, into):
            into.parent.mkdir(parents=True, exist_ok=True)
            into.write_bytes(payload)
            return into

        with patch.object(update, "download_verified", side_effect=download), \
             patch.object(update, "_set_stage_state", side_effect=set_stage_state):
            owner = threading.Thread(target=update._prepare_offer, args=(offer,))
            owner.start()
            self.assertTrue(ready_committed.wait(2))
            stored = json.loads(update.state_path().read_text(encoding="utf-8"))
            stored.update({"offer": {}, "error": ""})
            update.state_path().write_text(json.dumps(stored), encoding="utf-8")
            cleaner = threading.Thread(target=update._reconcile_prepared_update)
            cleaner.start()
            cleaner.join(timeout=0.2)
            self.assertTrue(cleaner.is_alive(), "cache cleaner did not wait for gate")
            release_owner.set()
            owner.join(timeout=5)
            cleaner.join(timeout=5)

        self.assertFalse(owner.is_alive())
        self.assertFalse(cleaner.is_alive())
        stored = json.loads(update.state_path().read_text(encoding="utf-8"))
        self.assertNotIn(update._STAGED_FIELD, stored)
        self.assertFalse(update.staged_installer_path().exists())
        self.assertFalse(update.staged_partial_path().exists())

    def test_waiting_reconciler_does_not_clear_a_new_successor_offer(self):
        first_payload = b"first ready candidate"
        successor_payload = b"successor after no-update"
        first = self._offer(first_payload, version="9.9.8")
        successor = self._offer(successor_payload, version="9.9.9")
        update.state_path().write_text(
            json.dumps({"offer": first, "error": ""}), encoding="utf-8"
        )
        ready_committed = threading.Event()
        release_owner = threading.Event()
        real_set_stage_state = update._set_stage_state
        calls = []

        def set_stage_state(identity, status, **kwargs):
            result = real_set_stage_state(identity, status, **kwargs)
            if identity["version"] == "9.9.8" and status == "ready" and result:
                ready_committed.set()
                self.assertTrue(release_owner.wait(5))
            return result

        def download(found, into):
            calls.append(found["version"])
            into.parent.mkdir(parents=True, exist_ok=True)
            into.write_bytes(
                first_payload if found["version"] == "9.9.8" else successor_payload
            )
            return into

        with patch.object(update, "download_verified", side_effect=download), \
             patch.object(update, "_set_stage_state", side_effect=set_stage_state):
            owner = threading.Thread(target=update._prepare_offer, args=(first,))
            owner.start()
            self.assertTrue(ready_committed.wait(2))
            stored = json.loads(update.state_path().read_text(encoding="utf-8"))
            stored.update({"offer": {}, "error": ""})
            update.state_path().write_text(json.dumps(stored), encoding="utf-8")
            reconciler = threading.Thread(target=update._reconcile_prepared_update)
            reconciler.start()
            reconciler.join(timeout=0.2)
            self.assertTrue(reconciler.is_alive())
            stored = json.loads(update.state_path().read_text(encoding="utf-8"))
            stored.update({"offer": successor, "error": ""})
            update.state_path().write_text(json.dumps(stored), encoding="utf-8")
            release_owner.set()
            owner.join(timeout=5)
            reconciler.join(timeout=5)

        self.assertFalse(owner.is_alive())
        self.assertFalse(reconciler.is_alive())
        final = update.prepared_update_status()
        self.assertEqual(calls, ["9.9.8", "9.9.9"])
        self.assertTrue(final["ready"])
        self.assertEqual(final["version"], "9.9.9")
        self.assertEqual(update.staged_installer_path().read_bytes(), successor_payload)

    def test_failed_background_download_is_not_ready_and_leaves_no_partial(self):
        offer = self._offer()

        def checked(force=False):
            update.state_path().write_text(
                json.dumps({"offer": offer, "error": ""}), encoding="utf-8"
            )
            return offer

        with patch.object(update, "available_update", side_effect=checked), \
             patch.object(
                 update, "download_verified",
                 side_effect=urllib.error.URLError("offline"),
             ):
            status = update.request_update_check()
            self.assertTrue(status["preparing"])
            self._join_worker()

        self.assertEqual(
            update.prepared_update_status(),
            {"preparing": False, "ready": False},
        )
        stored = json.loads(update.state_path().read_text(encoding="utf-8"))
        self.assertEqual(stored[update._STAGED_FIELD]["status"], "failed")
        self.assertEqual(stored[update._STAGED_FIELD]["error"], "URLError")
        self.assertFalse(update.staged_partial_path().exists())
        self.assertFalse(update.staged_installer_path().exists())

    def test_incomplete_download_removes_old_ready_and_partial_bytes(self):
        old_payload = b"old installer"
        old_offer = self._offer(old_payload, version="9.9.8")
        old_path = self._seed_ready(old_offer, old_payload)
        new_offer = self._offer(b"new installer", version="9.9.9")
        stored = json.loads(update.state_path().read_text(encoding="utf-8"))
        stored["offer"] = new_offer
        update.state_path().write_text(json.dumps(stored), encoding="utf-8")

        def incomplete(found, into):
            into.parent.mkdir(parents=True, exist_ok=True)
            into.write_bytes(b"partial new bytes")
            raise http.client.IncompleteRead(b"partial new bytes", 100)

        with patch.object(update, "download_verified", side_effect=incomplete):
            update._prepare_offer(new_offer)

        stored = json.loads(update.state_path().read_text(encoding="utf-8"))
        self.assertEqual(stored[update._STAGED_FIELD]["status"], "failed")
        self.assertEqual(stored[update._STAGED_FIELD]["error"], "IncompleteRead")
        identity = update._offer_identity(new_offer)
        self.assertTrue(update._stage_matches(stored[update._STAGED_FIELD], identity))
        self.assertFalse(old_path.exists())
        self.assertFalse(update.staged_partial_path().exists())

    def test_a_successful_no_update_check_clears_only_the_fixed_cache(self):
        offer = self._offer()
        self._seed_ready(offer)
        update.staged_partial_path().write_bytes(b"partial residue")
        pending = {"version": "8.8.8", "notes": "keep"}

        def checked(force=False):
            stored = json.loads(update.state_path().read_text(encoding="utf-8"))
            stored.update({"offer": {}, "error": "", "pending_announcement": pending})
            update.state_path().write_text(json.dumps(stored), encoding="utf-8")
            return {}

        with patch.object(update, "available_update", side_effect=checked):
            update.request_update_check(force=True)
            self._join_worker()

        stored = json.loads(update.state_path().read_text(encoding="utf-8"))
        self.assertNotIn(update._STAGED_FIELD, stored)
        self.assertEqual(stored["pending_announcement"], pending)
        self.assertFalse(update.staged_partial_path().exists())
        self.assertFalse(update.staged_installer_path().exists())


class ApplyUpdateStateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.dict(os.environ, {"SANDGLASS_HOME": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.home = Path(tmp.name)
        self.addCleanup(update._release_update_gate)

    def _offer(self, payload=b"prepared installer", **changes):
        version = str(changes.pop("version", "9.9.9"))
        offer = {
            "asset": f"Sandglass-{version}-windows-x64-unsigned-setup.exe",
            "version": version,
            "url": (
                f"https://github.com/taiyun668/Sandglass/releases/download/"
                f"v{version}/Sandglass-{version}-windows-x64-unsigned-setup.exe"
            ),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "manifest_signed": True,
            "notes": "",
            "published_at": "2026-09-08T00:00:00Z",
            "notes_url": (
                f"https://github.com/taiyun668/Sandglass/releases/tag/v{version}"
            ),
        }
        offer.update(changes)
        return offer

    def _seed_ready(self, offer, payload=b"prepared installer", **stored_fields):
        path = update.staged_installer_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        stored = dict(stored_fields)
        stored["offer"] = dict(offer)
        stored[update._STAGED_FIELD] = {
            **update._offer_identity(offer),
            "status": "ready",
            "error": "",
        }
        update.state_path().write_text(json.dumps(stored), encoding="utf-8")
        return path

    def test_verified_update_records_announcement_and_uses_update_parent_protocol(self):
        offer = self._offer(notes="修复窗口")
        staged = self._seed_ready(offer)
        seen = []
        with patch.object(update, "download_verified", side_effect=AssertionError), \
                patch.object(update.secrets, "token_hex", return_value="0123456789abcdef0123456789abcdef"), \
                patch("subprocess.Popen", lambda args, **kwargs: seen.append(args)):
            result = update.apply_update(offer)
        self.assertTrue(result["ok"])
        self.assertEqual(Path(seen[0][0]), staged)
        self.assertEqual(seen[0][1:3], ["/UPDATE", f"/PARENTPID={os.getpid()}"])
        self.assertEqual(seen[0][3], f"/RESTARTEXE={Path(update.sys.executable).resolve()}")
        self.assertEqual(seen[0][4], "/UPDATE_TOKEN=0123456789abcdef0123456789abcdef")
        stored = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["pending_announcement"], {
            "version": "9.9.9", "notes": "修复窗口",
            "published_at": offer["published_at"], "notes_url": offer["notes_url"],
        })

    def test_failed_handoff_releases_the_gate(self):
        offer = self._offer()
        with patch.object(update, "download_verified", side_effect=AssertionError):
            with self.assertRaises(update.UpdateNotReadyError):
                update.apply_update(offer)
        self.assertIsNone(update._UPDATE_GATE)

    def test_popen_failure_removes_only_the_staged_announcement(self):
        offer = self._offer(notes="new")
        self._seed_ready(offer, checked_at="2026-09-08T00:00:00Z")
        original = json.loads(update.state_path().read_text(encoding="utf-8"))
        with patch.object(update, "download_verified", side_effect=AssertionError), \
                patch("subprocess.Popen", side_effect=OSError("launch failed")):
            with self.assertRaisesRegex(OSError, "launch failed"):
                update.apply_update(offer)
        stored = json.loads(
            (self.home / "update-check.json").read_text(encoding="utf-8")
        )
        self.assertEqual(stored, original)
        self.assertIsNone(update._UPDATE_GATE)

    def test_cancelled_quit_handoff_stops_installer_restores_state_and_releases_gate(self):
        class Process:
            def __init__(self):
                self.running = True
                self.terminated = 0
            def poll(self):
                return None if self.running else 20
            def terminate(self):
                self.terminated += 1
                self.running = False
            def wait(self, timeout):
                self.running = False
                return 20

        process = Process()
        offer = self._offer()
        self._seed_ready(offer)
        original = json.loads(update.state_path().read_text(encoding="utf-8"))
        with patch.object(update, "download_verified", side_effect=AssertionError), \
                patch("subprocess.Popen", return_value=process):
            result = update.apply_update(offer)
        self.assertTrue(update.cancel_update_handoff(result))
        self.assertEqual(process.terminated, 1)
        self.assertEqual(
            json.loads((self.home / "update-check.json").read_text(encoding="utf-8")),
            original,
        )
        self.assertIsNone(update._UPDATE_GATE)

    def test_second_apply_is_busy_while_first_process_is_leaving(self):
        offer = self._offer()
        self._seed_ready(offer)
        with patch.object(update, "download_verified", side_effect=AssertionError), \
                patch("subprocess.Popen"):
            update.apply_update(offer)
            with self.assertRaises(update.UpdateBusyError):
                update.apply_update(offer)

    def test_changed_ready_file_is_refused_without_download_or_launch(self):
        offer = self._offer()
        staged = self._seed_ready(offer)
        staged.write_bytes(b"changed after preparation")
        with patch.object(update, "download_verified", side_effect=AssertionError), \
             patch("urllib.request.urlopen", side_effect=AssertionError), \
             patch("subprocess.Popen") as popen:
            with self.assertRaisesRegex(
                update.UpdateNotReadyError, "checksum changed"
            ):
                update.apply_update(offer)
        popen.assert_not_called()
        self.assertFalse(staged.exists())
        self.assertIsNone(update._UPDATE_GATE)

    def test_same_version_with_a_different_sha_is_not_ready(self):
        old_offer = self._offer()
        self._seed_ready(old_offer)
        replacement = self._offer(payload=b"replacement installer")
        with patch.object(update, "download_verified", side_effect=AssertionError), \
             patch("subprocess.Popen") as popen:
            with self.assertRaisesRegex(
                update.UpdateNotReadyError, "not prepared"
            ):
                update.apply_update(replacement)
        popen.assert_not_called()
        self.assertIsNone(update._UPDATE_GATE)

    def test_cross_process_gate_is_busy_and_released_when_owner_exits(self):
        child_code = (
            "import os,sys; "
            "os.environ['SANDGLASS_HOME']=sys.argv[1]; "
            "from sandglass import update; "
            "update._acquire_update_gate(); "
            "print('locked', flush=True); "
            "sys.stdin.readline()"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, str(self.home)],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        def cleanup_child():
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None and not stream.closed:
                    stream.close()

        self.addCleanup(cleanup_child)
        self.assertEqual(child.stdout.readline().strip(), "locked")
        with self.assertRaises(update.UpdateBusyError):
            update._acquire_update_gate()
        self.assertIsNone(child.poll(), "busy probing must not terminate the owner")
        child.stdin.close()
        self.assertEqual(child.wait(timeout=10), 0)
        update._acquire_update_gate()
        self.assertIsNotNone(update._UPDATE_GATE)

    def test_background_reconcile_waits_for_a_real_cross_process_gate(self):
        payload = b"cross process successor"
        offer = self._offer(payload)
        update.state_path().write_text(
            json.dumps({"offer": offer, "error": ""}), encoding="utf-8"
        )
        child_code = (
            "import os,sys; "
            "os.environ['SANDGLASS_HOME']=sys.argv[1]; "
            "from sandglass import update; "
            "update._acquire_update_gate(); "
            "print('locked', flush=True); "
            "sys.stdin.readline()"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, str(self.home)],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def cleanup_child():
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None and not stream.closed:
                    stream.close()

        self.addCleanup(cleanup_child)
        self.assertEqual(child.stdout.readline().strip(), "locked")
        calls = []

        def download(found, into):
            calls.append(found["version"])
            into.parent.mkdir(parents=True, exist_ok=True)
            into.write_bytes(payload)
            return into

        with patch.object(update, "download_verified", side_effect=download):
            waiter = threading.Thread(target=update._reconcile_prepared_update)
            waiter.start()
            waiter.join(timeout=0.2)
            self.assertTrue(waiter.is_alive(), "prepare did not wait on process gate")
            self.assertEqual(calls, [])
            child.stdin.close()
            self.assertEqual(child.wait(timeout=10), 0)
            waiter.join(timeout=15)

        self.assertFalse(waiter.is_alive(), "prepare did not resume after gate release")
        self.assertEqual(calls, ["9.9.9"])
        self.assertTrue(update.prepared_update_status()["ready"])
        self.assertEqual(update.staged_installer_path().read_bytes(), payload)


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
        with patch("urllib.request.urlopen", self._serve(payload)):
            kept = update.download_verified(offer, target)

        self.assertTrue(kept.exists())
        self.assertEqual(kept.read_bytes(), payload)

    def test_a_release_without_a_signed_manifest_is_never_offered(self):
        """A checksum alone does not identify who authorized the release."""
        release = {
            "tag_name": "v9.9.9",
            "assets": [
                {"name": "Sandglass-9.9.9-windows-x64-unsigned-setup.exe",
                 "browser_download_url": "https://github.com/a/b/setup.exe"},
                {"name": "SHA256SUMS.windows",
                 "browser_download_url": "https://github.com/a/b/SHA256SUMS.windows"},
            ],
        }
        sums = ((b"a" * 64) +
                b" *Sandglass-9.9.9-windows-x64-unsigned-setup.exe" + bytes((10,)))
        with patch.object(update, "_get", return_value=sums):
            self.assertEqual(update.offer_from(release), {})

    def test_signed_manifest_must_bind_version_and_installer_name(self):
        installer = "Sandglass-9.9.9-windows-x64-unsigned-setup.exe"
        sums = (b"# Sandglass-Version: 9.9.9\n" + b"a" * 64 + b"  " +
                installer.encode() + b"\n")
        assets = {
            update.CHECKSUMS_SIGNATURE_NAME: "https://github.com/a/b/sig",
        }
        with patch.object(update, "RELEASE_PUBLIC_KEY", _TEST_PUBLIC_KEY), patch.object(
            update, "_get", return_value=b"00"
        ), patch("sandglass.release_signature.verify_release_signature", return_value=True):
            self.assertTrue(update._manifest_signature_ok(
                sums, assets, "9.9.9", installer))
            high_installer = "Sandglass-10.0.0-windows-x64-unsigned-setup.exe"
            high_tag_old_metadata = (
                b"# Sandglass-Version: 9.9.9\n" + b"a" * 64 + b"  " +
                high_installer.encode() + b"\n"
            )
            # Keep the high-tag installer and checksum unchanged; only the
            # signed metadata remains from the old release. This must fail for
            # version binding, not because the asset/checksum lookup changed.
            self.assertTrue(update._installer_name(high_installer, "10.0.0"))
            self.assertEqual(update.checksum_for(
                high_tag_old_metadata.decode("ascii"), high_installer), "a" * 64)
            self.assertFalse(update._manifest_signature_ok(
                high_tag_old_metadata, assets, "10.0.0", high_installer))
            self.assertFalse(update._manifest_signature_ok(
                sums, assets, "10.0.0", installer))

    def test_manifest_version_parser_accepts_sha256sum_comment_metadata(self):
        raw = (b"# Sandglass-Version: 9.9.9\n" +
               b"a" * 64 + b"  Sandglass-9.9.9-windows-x64-unsigned-setup.exe\n")
        self.assertEqual(update._manifest_version(raw), "9.9.9")
        self.assertEqual(update.checksum_for(raw.decode("ascii"),
                                             "Sandglass-9.9.9-windows-x64-unsigned-setup.exe"),
                         "a" * 64)
        self.assertEqual(update._manifest_version(
            b"Sandglass-Version: 9.9.9\n" + raw.split(b"\n", 1)[1]), "")

        # Exercise the parser that release consumers use too. Windows build
        # hosts do not ship sha256sum, so retain the direct parser assertions
        # above and run this extra check wherever the tool is available.
        sha256sum = shutil.which("sha256sum")
        if sha256sum:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                payload = root / "Sandglass-9.9.9-windows-x64-unsigned-setup.exe"
                payload.write_bytes(b"payload")
                digest = hashlib.sha256(payload.read_bytes()).hexdigest()
                manifest = root / update.CHECKSUMS_NAME
                manifest.write_text(
                    f"# Sandglass-Version: 9.9.9\n{digest}  {payload.name}\n",
                    encoding="ascii",
                    newline="\n",
                )
                self.assertNotIn(b"\r\n", manifest.read_bytes())
                checked = subprocess.run(
                    [sha256sum, "--strict", "-c", str(manifest)],
                    cwd=root, capture_output=True, text=True,
                )
                self.assertEqual(checked.returncode, 0, checked.stderr)
