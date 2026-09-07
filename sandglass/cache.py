from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from sandglass.dbschema import apply_schema
from sandglass.models import RECORD_FORMAT, SessionRecord
from sandglass.paths import cache_db


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    path TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    payload TEXT NOT NULL
);
"""


class SessionCache:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or cache_db()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._open()
        except sqlite3.DatabaseError as exc:
            if not self._is_corruption(exc):
                raise
            self._quarantine_corrupt_files()
            self._open()

    def _open(self) -> None:
        # The desktop server and a CLI/audit can legitimately open a brand-new
        # cache together.  SQLite's default five-second wait is not consistently
        # applied while WAL/schema setup is taking the first write lock, so make
        # the contract explicit on both the connection and the database handle.
        self._conn = sqlite3.connect(self.path, timeout=10.0)
        self._conn.execute("PRAGMA busy_timeout=10000")
        try:
            checked = self._conn.execute("PRAGMA quick_check(1)").fetchone()
            if not checked or str(checked[0]).lower() != "ok":
                raise sqlite3.DatabaseError(
                    f"cache quick_check failed: {checked[0] if checked else 'no result'}")
            self._enable_wal()
            apply_schema(self._conn, SCHEMA)
            self._conn.commit()
        except Exception:
            self._conn.close()
            raise

    @staticmethod
    def _is_corruption(exc: sqlite3.DatabaseError) -> bool:
        message = str(exc).lower()
        return any(marker in message for marker in (
            "database disk image is malformed",
            "file is not a database",
            "cache quick_check failed",
        ))

    def _quarantine_corrupt_files(self) -> None:
        """Move a disposable broken cache aside and rebuild without data loss.

        Provider files remain the source of truth.  Keeping the broken SQLite
        files beside the replacement preserves diagnostic evidence while the
        normal collection path reconstructs every cache row.
        """
        stamp = time.time_ns()
        for suffix in ("", "-wal", "-shm"):
            source = Path(f"{self.path}{suffix}")
            if not source.exists():
                continue
            target = source.with_name(f"{source.name}.corrupt-{stamp}")
            try:
                source.replace(target)
            except FileNotFoundError:
                pass

    def _enable_wal(self) -> None:
        """Enable WAL even when another first opener briefly owns the write lock.

        SQLite's busy handler does not cover the journal-mode transition on every
        Windows build, so this one operation needs its own bounded lock wait.
        """
        deadline = time.monotonic() + 10.0
        while True:
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def get(self, path: Path) -> SessionRecord | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        row = self._conn.execute(
            "SELECT mtime_ns, size, payload FROM sessions WHERE path = ?",
            (str(path),),
        ).fetchone()
        if not row:
            return None
        mtime_ns, size, payload = row
        if int(mtime_ns) != getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)) or int(size) != stat.st_size:
            return None
        try:
            data = json.loads(payload)
            if data.get("format") != RECORD_FORMAT:
                # Parsed by older collector logic. Treat as a miss so it re-parses once.
                return None
            return SessionRecord.from_dict(data)
        except (TypeError, ValueError, AttributeError, json.JSONDecodeError, KeyError):
            return None

    def put(self, record: SessionRecord, stat: os.stat_result) -> None:
        """Store one parsed session under the stamp its content was read at.

        Stamping after the parse dates the row to a file that may have grown
        since: the vendors append to these while we read them, so the row would
        carry the shorter content under the longer file's size and mtime, and
        every later read would call it a hit. For a session still being written
        the next append covers it; for the last append a session ever gets, the
        tail is never parsed again.

        The stamp is required rather than defaulted for that reason. It used to
        fall back to stat()ing here, which is the same wrong moment by another
        route, and a caller that could not take a stamp of its own reached it by
        passing None. A caller without a stamp has nothing honest to store and
        must skip the row: a miss costs one re-parse, a wrong hit costs those
        tokens for as long as the file stays untouched.
        """
        path = Path(record.path)
        self._conn.execute(
            """
            INSERT INTO sessions(path, provider, mtime_ns, size, payload)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                provider=excluded.provider,
                mtime_ns=excluded.mtime_ns,
                size=excluded.size,
                payload=excluded.payload
            """,
            (
                str(path),
                record.provider,
                getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)),
                stat.st_size,
                json.dumps(record.as_dict(), ensure_ascii=False),
            ),
        )
        # A cache row is independently disposable. Holding hundreds of these
        # writes in one transaction blocks the desktop and an audit/CLI process
        # from rebuilding a new RECORD_FORMAT at the same time.
        self._conn.commit()

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()
