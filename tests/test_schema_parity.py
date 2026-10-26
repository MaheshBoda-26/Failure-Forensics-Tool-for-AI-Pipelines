"""Schema parity between the SQLite and PostgreSQL backends.

Both backends implement the same `StorageBase`, and every query names columns
explicitly, so a table or column that exists in one and not the other fails at
runtime on whichever deployment happens to use it. The PostgreSQL tests prove
this against a live server, but they skip without one — this module is the
always-on guard that catches drift in CI, where no PostgreSQL server exists.

It compares the schema SQLite actually creates against the DDL the PostgreSQL
backend declares, table by table, column by column.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from forensics.storage_postgres import PostgresStorage
from forensics.storage_sqlite import Storage as SQLiteStorage

_CREATE_TABLE = re.compile(
    r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\)\s*\"\"\"",
    re.DOTALL | re.IGNORECASE,
)


def _sqlite_schema(tmp_path: Path) -> dict[str, set[str]]:
    storage = SQLiteStorage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    try:
        with storage._conn() as conn:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            return {
                table: {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                for table in tables
            }
    finally:
        storage.close()


def _postgres_schema() -> dict[str, set[str]]:
    source = inspect.getsource(PostgresStorage._init_schema)
    schema: dict[str, set[str]] = {}
    for table, body in _CREATE_TABLE.findall(source):
        columns: set[str] = set()
        for line in body.splitlines():
            stripped = line.strip().rstrip(",")
            if not stripped:
                continue
            # Skip table constraints; keep the leading identifier of each column.
            if stripped.upper().startswith(("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK")):
                continue
            columns.add(stripped.split()[0])
        schema[table] = columns
    return schema


def test_postgres_ddl_declares_every_sqlite_table(tmp_path: Path) -> None:
    sqlite_tables = set(_sqlite_schema(tmp_path))
    postgres_tables = set(_postgres_schema())

    assert postgres_tables, "failed to parse the PostgreSQL DDL"
    assert sqlite_tables - postgres_tables == set(), (
        "tables missing from storage_postgres._init_schema: "
        f"{sorted(sqlite_tables - postgres_tables)}"
    )


def test_every_sqlite_column_exists_in_postgres(tmp_path: Path) -> None:
    sqlite_schema = _sqlite_schema(tmp_path)
    postgres_schema = _postgres_schema()

    problems: list[str] = []
    for table, columns in sorted(sqlite_schema.items()):
        if table not in postgres_schema:
            problems.append(f"{table}: table missing from PostgreSQL DDL")
            continue
        missing = sorted(columns - postgres_schema[table])
        if missing:
            problems.append(f"{table}: columns missing from PostgreSQL DDL: {missing}")

    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize(
    "column",
    ["tenant_id", "scopes"],
)
def test_widening_columns_are_present_in_both_backends(tmp_path: Path, column: str) -> None:
    """Columns added by a migration must exist in the PG DDL too.

    `scopes` was added to the SQLite `api_keys` table and to the PostgreSQL
    `CREATE TABLE`, but an existing deployment needs the `ALTER TABLE ... ADD
    COLUMN IF NOT EXISTS` as well; that statement is asserted separately by the
    live PostgreSQL tests.
    """
    sqlite_schema = _sqlite_schema(tmp_path)
    postgres_schema = _postgres_schema()

    for table in ("api_keys",):
        assert column in sqlite_schema[table], f"SQLite {table}.{column} missing"
        assert column in postgres_schema[table], f"PostgreSQL {table}.{column} missing"
