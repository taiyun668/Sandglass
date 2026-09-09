"""What the update offer will and will not do.

The button is dark until a release offers something, and the offer is only
installable if it can be checked: a checksum published beside the installer, a
download that matches it, and the Owner's detached signature over that
manifest. The public unsigned installer route uses the signed manifest as its
release identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
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

    def test_cached_offer_matching_this_process_is_cleared_not_shown(self):
        """0.1.1 cached a 0.1.3 offer; 0.1.3 must not badge it as available."""
        pending = {"version": "0.1.3", "notes": "已安装"}
        self._fresh_offer_state(
            {"version": "0.1.3", "asset": "cached.exe"},
            pending_announcement=pending,
            unrelated={"keep": True},
        )
        calls = []
        with patch.object(update, "__version__", "0.1.3"), patch.object(
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
                with patch.object(update, "__version__", "0.1.3"), patch.object(
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
        with patch.object(update, "__version__", "0.1.3"), patch.object(
            update, "_get", lambda *a, **k: calls.append(1)
        ):
            offer = update.available_update()
        self.assertEqual(offer, cached)
        self.assertIsNot(offer, cached)
        self.assertEqual(calls, [])
        stored = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertEqual(stored, original)

    def test_restoring_unfiltered_cached_return_fails_for_stale_badge(self):
        """The retired cached return is exactly the stale-badge bug.

        A 0.1.1 process wrote a still-fresh offer.version of 0.1.3. Restoring
        `return dict(cached) if isinstance(cached, dict) else {}` would show
        that offer as an update to the process that just became 0.1.3.
        """
        import inspect

        cached = {"version": "0.1.3", "asset": "cached.exe"}
        pending = {"version": "0.1.3", "notes": "已安装"}
        self._fresh_offer_state(
            cached,
            pending_announcement=pending,
            unrelated={"keep": True},
        )
        unfiltered = dict(cached) if isinstance(cached, dict) else {}
        self.assertEqual(
            unfiltered.get("version"),
            "0.1.3",
            "the unfiltered cached return is the stale-badge reason",
        )

        source = inspect.getsource(update.available_update)
        retired = "return dict(cached) if isinstance(cached, dict) else {}"
        self.assertNotIn(retired, source)
        self.assertIn("is_newer(version, __version__)", source)

        calls = []
        with patch.object(update, "__version__", "0.1.3"), patch.object(
            update, "_get", lambda *a, **k: calls.append(1)
        ):
            offer = update.available_update()
        self.assertEqual(offer, {})
        self.assertEqual(calls, [])
        stored = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["offer"], {})
        self.assertEqual(stored["pending_announcement"], pending)
        self.assertEqual(stored["unrelated"], {"keep": True})
        mutated = source.replace(
            "if isinstance(version, str) and is_newer(version, __version__):\n"
            "                    return dict(cached)",
            retired,
            1,
        )
        self.assertNotEqual(mutated, source)
        self.assertIn(retired, mutated)
        with self.assertRaises(AssertionError):
            self.assertNotIn(retired, mutated)
            self.assertIn("is_newer(version, __version__)", mutated)

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
        self.addCleanup(update._release_update_gate)

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
                patch.object(update.secrets, "token_hex", return_value="0123456789abcdef0123456789abcdef"), \
                patch("subprocess.Popen", lambda args, **kwargs: seen.append(args)):
            result = update.apply_update(offer)
        self.assertTrue(result["ok"])
        self.assertEqual(seen[0][1:3], ["/UPDATE", f"/PARENTPID={os.getpid()}"])
        self.assertEqual(seen[0][3], f"/RESTARTEXE={Path(update.sys.executable).resolve()}")
        self.assertEqual(seen[0][4], "/UPDATE_TOKEN=0123456789abcdef0123456789abcdef")
        stored = json.loads((self.home / "update-check.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["pending_announcement"], {
            "version": "9.9.9", "notes": "修复窗口",
            "published_at": offer["published_at"], "notes_url": offer["notes_url"],
        })

    def test_failed_handoff_releases_the_gate(self):
        offer = {"asset": "setup.exe", "version": "9.9.9"}
        with patch.object(update, "download_verified",
                          side_effect=ValueError("bad download")):
            with self.assertRaises(ValueError):
                update.apply_update(offer)
        self.assertIsNone(update._UPDATE_GATE)

    def test_popen_failure_removes_only_the_staged_announcement(self):
        original = {"offer": {"version": "8.8.8"}, "checked_at": "2026-09-08T00:00:00Z"}
        (self.home / "update-check.json").write_text(
            json.dumps(original), encoding="utf-8"
        )
        offer = {"asset": "setup.exe", "version": "9.9.9", "notes": "new"}
        with patch.object(update, "download_verified", return_value=Path("setup.exe")), \
                patch("subprocess.Popen", side_effect=OSError("launch failed")):
            with self.assertRaisesRegex(OSError, "launch failed"):
                update.apply_update(offer)
        stored = json.loads(
            (self.home / "update-check.json").read_text(encoding="utf-8")
        )
        self.assertEqual(stored, original)
        self.assertIsNone(update._UPDATE_GATE)

    def test_cancelled_quit_handoff_stops_installer_restores_state_and_releases_gate(self):
        original = {"offer": {"version": "8.8.8"}}
        (self.home / "update-check.json").write_text(
            json.dumps(original), encoding="utf-8"
        )

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
        offer = {"asset": "setup.exe", "version": "9.9.9"}
        with patch.object(update, "download_verified", return_value=Path("setup.exe")), \
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
        offer = {"asset": "setup.exe", "version": "9.9.9"}
        with patch.object(update, "download_verified", return_value=Path("setup.exe")), \
                patch("subprocess.Popen"):
            update.apply_update(offer)
            with self.assertRaises(update.UpdateBusyError):
                update.apply_update(offer)

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
