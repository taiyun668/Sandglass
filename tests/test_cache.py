import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sandglass.cache import SessionCache
from sandglass.models import SessionRecord, TokenUsage


class CacheTests(unittest.TestCase):
    def test_malformed_disposable_cache_is_quarantined_and_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "cache.sqlite"
            source = root / "session.jsonl"
            database.write_bytes(b"not a sqlite database")
            source.write_text("session", encoding="utf-8")

            cache = SessionCache(database)
            try:
                cache.put(
                    SessionRecord(
                        provider="claude",
                        session_id="rebuilt",
                        path=str(source),
                        usage=TokenUsage(input_tokens=3),
                    ),
                    stat=source.stat(),
                )
                self.assertEqual(cache.get(source).session_id, "rebuilt")
            finally:
                cache.close()

            quarantined = list(root.glob("cache.sqlite.corrupt-*"))
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(quarantined[0].read_bytes(), b"not a sqlite database")

    def test_each_cache_put_releases_the_write_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "cache.sqlite"
            first_source = root / "first.jsonl"
            second_source = root / "second.jsonl"
            first_source.write_text("first", encoding="utf-8")
            second_source.write_text("second", encoding="utf-8")
            first = SessionCache(database)
            second = SessionCache(database)
            second._conn.execute("PRAGMA busy_timeout=100")
            try:
                first.put(
                    SessionRecord(
                        provider="claude",
                        session_id="first",
                        path=str(first_source),
                        usage=TokenUsage(input_tokens=1),
                    ),
                    stat=first_source.stat(),
                )
                second.put(
                    SessionRecord(
                        provider="claude",
                        session_id="second",
                        path=str(second_source),
                        usage=TokenUsage(input_tokens=2),
                    ),
                    stat=second_source.stat(),
                )
                self.assertEqual(first.get(second_source).session_id, "second")
            finally:
                first.close()
                second.close()

    def test_initialization_waits_for_a_short_write_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.sqlite"
            holder = sqlite3.connect(path, timeout=1.0, check_same_thread=False)
            holder.execute("CREATE TABLE bootstrap(value INTEGER)")
            holder.commit()
            holder.execute("BEGIN IMMEDIATE")
            holder.execute("INSERT INTO bootstrap VALUES (1)")

            def release() -> None:
                time.sleep(0.2)
                holder.commit()
                holder.close()

            thread = threading.Thread(target=release)
            thread.start()
            try:
                cache = SessionCache(path)
                cache.close()
            finally:
                thread.join()


class AppendDuringParseTests(unittest.TestCase):
    """The vendors append to these files while Sandglass is reading them.

    put() used to stat the file after the parse, so a line appended in between
    was stamped as already accounted for: the row held the shorter content
    under the longer file's size and mtime, and every later read called it a
    hit. A session still being written recovers on its next append. The last
    append a session ever gets does not -- those tokens are never parsed again.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cache = SessionCache(self.root / "cache.sqlite")
        self.addCleanup(self.cache.close)

    def _record(self, path, tokens):
        return SessionRecord(provider="codex", session_id=path.stem, path=str(path),
                             usage=TokenUsage(input_tokens=tokens), timeline=[])

    def test_a_line_appended_during_the_parse_forces_a_reparse(self):
        path = self.root / "rollout.jsonl"
        path.write_text("first\n", encoding="utf-8")
        stamp = path.stat()
        with path.open("a", encoding="utf-8") as handle:
            handle.write("second\n")

        self.cache.put(self._record(path, 100), stat=stamp)

        self.assertIsNone(self.cache.get(path), "追加过的文件不该算缓存命中")

    def test_a_file_that_did_not_change_is_still_cached(self):
        """The counterpart: never caching would also pass the test above."""
        path = self.root / "quiet.jsonl"
        path.write_text("only\n", encoding="utf-8")

        self.cache.put(self._record(path, 5), stat=path.stat())

        cached = self.cache.get(path)
        self.assertIsNotNone(cached)
        self.assertEqual(cached.usage.input_tokens, 5)

    def test_the_collector_reparses_a_file_that_grew_while_it_read(self):
        """Drive the real path, not its wording.

        An earlier version of this read collectors.py and checked that the stat
        came before the parse. It did, and the stamp was then thrown away --
        removing `stat=stamp` from the put left that assertion perfectly happy
        while the bug came straight back.
        """
        from sandglass.collectors import _parse_files

        path = self.root / "growing.jsonl"
        path.write_text("first\n", encoding="utf-8")

        def parse(target):
            # the vendor appends while this "read" is in progress
            with target.open("a", encoding="utf-8") as handle:
                handle.write("second\n")
            return self._record(target, 100)

        _parse_files("codex", [path], self.cache, None, parse)

        self.assertIsNone(self.cache.get(path),
                          "解析期间长大的文件不该被当成已缓存")

    def test_the_collector_still_caches_a_file_that_stayed_still(self):
        from sandglass.collectors import _parse_files

        path = self.root / "still.jsonl"
        path.write_text("only\n", encoding="utf-8")

        _parse_files("codex", [path], self.cache, None, lambda t: self._record(t, 5))

        cached = self.cache.get(path)
        self.assertIsNotNone(cached, "没变化的文件仍应命中缓存")
        self.assertEqual(cached.usage.input_tokens, 5)

    def test_a_parse_with_no_usable_stamp_is_not_cached_at_all(self):
        """No stamp, no row.

        The stamp used to be optional: a caller that could not take one passed
        None and put() stat()ed the file itself, after the parse -- the same
        wrong moment by another route. The collector reaches that by way of a
        stat that raises, so a file that grows during the parse would be stored
        under the size it grew to and never read again.
        """
        from sandglass.collectors import _parse_files

        path = self.root / "unstattable.jsonl"
        path.write_text("first\n", encoding="utf-8")
        real_stat = Path.stat
        # Only the stamp is allowed to fail. get() stats this file first and the
        # collector's own stamp is the second call, so put()'s old fallback --
        # the third -- still found a file, and found the grown one.
        stats: list[int] = []

        def refuse_only_the_stamp(self_path, *args, **kwargs):
            if self_path == path:
                stats.append(1)
                if len(stats) == 2:
                    raise OSError("stat refused")
            return real_stat(self_path, *args, **kwargs)

        def parse(target):
            with target.open("a", encoding="utf-8") as handle:
                handle.write("second\n")
            return self._record(target, 100)

        with patch.object(Path, "stat", refuse_only_the_stamp):
            _parse_files("codex", [path], self.cache, None, parse)

        self.assertGreaterEqual(len(stats), 2, "前置 stat 必须真的被走到")
        self.assertIsNone(self.cache.get(path), "拿不到戳就不该写缓存行")


if __name__ == "__main__":
    unittest.main()
