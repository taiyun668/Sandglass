import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandglass.dbschema import apply_schema
from sandglass.user_sources import SCHEMA as USER_SOURCE_SCHEMA, UserSourceStore


class DeclaredSchemaTests(unittest.TestCase):
    def test_a_column_added_since_the_database_was_written_is_filled_in(self):
        """CREATE TABLE IF NOT EXISTS is silent about a table that already exists."""

        old = "CREATE TABLE IF NOT EXISTS t (a TEXT PRIMARY KEY, b TEXT NOT NULL);"
        new = (
            "CREATE TABLE IF NOT EXISTS t ("
            "a TEXT PRIMARY KEY, b TEXT NOT NULL, c TEXT NOT NULL DEFAULT '');"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.sqlite"
            written_by_an_older_build = sqlite3.connect(path)
            written_by_an_older_build.executescript(old)
            written_by_an_older_build.commit()
            written_by_an_older_build.close()

            conn = sqlite3.connect(path)
            try:
                apply_schema(conn, new)
                self.assertEqual(
                    [str(row[1]) for row in conn.execute("PRAGMA table_info(t)")],
                    ["a", "b", "c"],
                )
                conn.execute("INSERT INTO t(a, b, c) VALUES(?, ?, ?)", ("1", "2", "3"))
            finally:
                conn.close()

    def test_a_column_sqlite_cannot_add_stops_startup_and_names_itself(self):
        """Better a refusal here than "no column named x" at some later INSERT.

        SQLite takes a NOT NULL column with no default while the table is still
        empty, and only refuses once there are rows that would violate it -- so
        this is the case where there is no honest value to write, and the right
        answer is to stop rather than to invent one.
        """

        old = "CREATE TABLE IF NOT EXISTS t (a TEXT PRIMARY KEY);"
        new = "CREATE TABLE IF NOT EXISTS t (a TEXT PRIMARY KEY, b TEXT NOT NULL);"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.sqlite"
            written_by_an_older_build = sqlite3.connect(path)
            written_by_an_older_build.executescript(old)
            written_by_an_older_build.execute("INSERT INTO t(a) VALUES('kept')")
            written_by_an_older_build.commit()
            written_by_an_older_build.close()

            conn = sqlite3.connect(path)
            try:
                with self.assertRaises(sqlite3.OperationalError) as caught:
                    apply_schema(conn, new)
                self.assertIn("t.b", str(caught.exception))
            finally:
                conn.close()

    def test_an_up_to_date_database_is_left_alone_and_repeats_are_a_no_op(self):
        schema = (
            "CREATE TABLE IF NOT EXISTS t (a TEXT PRIMARY KEY, b TEXT NOT NULL);"
            "CREATE INDEX IF NOT EXISTS t_b ON t(b);"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "current.sqlite"
            conn = sqlite3.connect(path)
            try:
                apply_schema(conn, schema)
                conn.execute("INSERT INTO t(a, b) VALUES(?, ?)", ("1", "2"))
                before = conn.execute(
                    "SELECT sql FROM sqlite_master ORDER BY name"
                ).fetchall()

                apply_schema(conn, schema)
                apply_schema(conn, schema)

                self.assertEqual(
                    conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall(),
                    before,
                )
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM t").fetchone()[0], 1)
            finally:
                conn.close()

    def test_a_real_store_opens_a_database_written_before_a_settings_column(self):
        """The case this actually happened on, through the real store.

        user_source_settings gained four columns over time and each one was
        reconciled by hand. Removing one from a database and using the store has
        to leave it usable, without that hand-written code.

        This goes through configure() on purpose. settings() opens the file
        read-only and already answers a default for every column it cannot find,
        so it stays happy against the old shape and proves nothing here; the
        write path is where a missing column actually breaks.
        """

        older = USER_SOURCE_SCHEMA.replace(
            "    full_window_enabled INTEGER NOT NULL DEFAULT 0,\n", ""
        )
        self.assertNotEqual(older, USER_SOURCE_SCHEMA)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            path = Path(tmp) / "user-sources.sqlite"
            written_by_an_older_build = sqlite3.connect(path)
            written_by_an_older_build.executescript(older)
            written_by_an_older_build.execute(
                "INSERT INTO user_source_settings("
                "source, display_enabled, accounts_enabled, identity_enabled,"
                " mapped_provider, mapped_account_id, totals_enabled, updated_at)"
                " VALUES(?, 1, 1, 0, '', '', 0, '2026-09-03T00:00:00Z')",
                ("adapter.example",),
            )
            written_by_an_older_build.commit()
            written_by_an_older_build.close()

            store = UserSourceStore()
            store.configure("adapter.example", full_window_enabled=True)

            settings = store.settings()
            self.assertTrue(settings["adapter.example"]["full_window_enabled"])
            reopened = sqlite3.connect(path)
            try:
                self.assertIn(
                    "full_window_enabled",
                    {
                        str(row[1])
                        for row in reopened.execute(
                            "PRAGMA table_info(user_source_settings)"
                        )
                    },
                )
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
