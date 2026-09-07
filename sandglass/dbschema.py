"""Make an existing database match the schema its module declares.

`CREATE TABLE IF NOT EXISTS` says nothing about a table that already exists
with fewer columns. Adding a column to a SCHEMA constant therefore left every
database written by an older build one INSERT away from "table X has no column
named Y" -- raised at whatever moment that statement first ran, not at startup,
and only on machines that had used the older build.

That was survivable while there was no way to ship a new build to an old
install. There is one now, so the gap is reachable.

The declared columns are read back from SQLite itself: the same SCHEMA text is
executed against an in-memory database and inspected with `PRAGMA table_info`.
Nothing here parses SQL, so there is no second description of the schema to
drift from the first. SCHEMA stays the only place a column is declared.

Only added columns can be reconciled, and SQLite decides which of those it will
take. Measured, not assumed: a NOT NULL column with no default is accepted while
the table is still empty and refused once there are rows it would violate --
which is exactly the case where there is no honest value to write. Everything it
refuses -- that, UNIQUE, PRIMARY KEY, renames, drops, type changes -- stops the
process at startup with the table and column named, rather than becoming a
puzzling failure at whatever INSERT reaches it first.
"""

from __future__ import annotations

import sqlite3
from functools import lru_cache


@lru_cache(maxsize=None)
def _declared(schema_sql: str) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    """Return ((table, ((column, ddl), ...)), ...) as SQLite itself reads SCHEMA."""

    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(schema_sql)
        tables = [
            str(row[0])
            for row in reference.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        declared = []
        for table in tables:
            columns = []
            for _, name, kind, notnull, default, _pk in reference.execute(
                f'PRAGMA table_info("{table}")'
            ):
                ddl = f'"{name}" {kind}'.strip()
                if notnull:
                    ddl += " NOT NULL"
                if default is not None:
                    ddl += f" DEFAULT {default}"
                columns.append((str(name), ddl))
            declared.append((table, tuple(columns)))
        return tuple(declared)
    finally:
        reference.close()


def apply_schema(conn: sqlite3.Connection, schema_sql: str) -> None:
    """Create anything missing, then add columns an older build never wrote."""

    conn.executescript(schema_sql)
    for table, columns in _declared(schema_sql):
        present = {
            str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')
        }
        for name, ddl in columns:
            if name in present:
                continue
            try:
                # The DDL came from SQLite's own read of this module's SCHEMA
                # constant, never from stored or user-supplied data.
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {ddl}')
            except sqlite3.OperationalError as exc:
                raise sqlite3.OperationalError(
                    f"cannot bring {table}.{name} up to the declared schema "
                    f"({exc}); this database was written by a build that did "
                    f"not have that column"
                ) from exc
