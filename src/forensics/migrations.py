"""Idempotent SQLite schema migrations.

Two modules create the `traces`/`spans` tables (`storage.Storage._init_schema`
and `tracing.init_db`), and a database written by either may already exist on
disk with the pre-multi-tenant shape. Teaching both CREATE TABLE statements
about every historical column shape would guarantee they drift apart, so this
module is the single place that closes the gap between an existing database and
the current schema.

Every operation here is safe to run repeatedly: it is called on every
`Storage()` construction and on every `finalize_trace`.
"""

import sqlite3

#: Tables that gained a `tenant_id` column in spec 04. Order matters only for
#: readable output; the migration is independent per table.
TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "traces",
    "spans",
    "flags",
    "diagnoses",
    "eval_cases",
    "eval_runs",
    "eval_results",
)

_DEFAULT_TENANT = "default"

#: Columns added to an existing table after its original `CREATE TABLE` shipped,
#: as `table -> {column: column DDL}`. `judge_analysis`/`analyzer_fallback` were
#: on the `Diagnosis` model but had no column, so a judge verdict was dropped the
#: moment a diagnosis was persisted and read back.
_COLUMN_ADDITIONS: dict[str, dict[str, str]] = {
    "diagnoses": {
        "judge_analysis": "TEXT",
        "analyzer_fallback": "BOOLEAN DEFAULT 0",
    },
}

_AUTH_TABLE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS tenants (
        tenant_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        active INTEGER DEFAULT 1
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_keys (
        key_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        key_hash TEXT NOT NULL,
        prefix TEXT NOT NULL,
        name TEXT,
        created_at TEXT NOT NULL,
        revoked_at TEXT,
        last_used_at TEXT,
        FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(prefix)",
    "CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id)",
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    # `table` always comes from TENANT_SCOPED_TABLES, never from user input, and
    # SQLite has no parameter binding for identifiers.
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Bring `conn`'s schema up to the current shape.

    Returns the changes actually applied, which makes the work observable in
    tests instead of being an invisible side effect of connecting.
    """
    applied: list[str] = []

    for statement in _AUTH_TABLE_STATEMENTS:
        conn.execute(statement)

    for table in TENANT_SCOPED_TABLES:
        if not _table_exists(conn, table):
            # `tracing.init_db` creates only traces and spans; a table that does
            # not exist yet is created with the column already in place.
            continue
        if "tenant_id" not in _columns(conn, table):
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT NOT NULL "
                f"DEFAULT '{_DEFAULT_TENANT}'"
            )
            applied.append(f"{table}.tenant_id")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_tenant ON {table}(tenant_id)")

    # Columns added after a table's original CREATE TABLE shipped. A database
    # created by an older revision has the table but not these; `ALTER TABLE ADD
    # COLUMN` is the only in-place widening SQLite offers.
    for table, additions in _COLUMN_ADDITIONS.items():
        if not _table_exists(conn, table):
            continue
        existing = _columns(conn, table)
        for column, ddl in additions.items():
            if column in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            applied.append(f"{table}.{column}")

    applied.extend(_dedupe_and_constrain_eval_cases(conn))

    conn.commit()
    return applied


def _dedupe_and_constrain_eval_cases(conn: sqlite3.Connection) -> list[str]:
    """Collapse duplicate `eval_cases` rows, then forbid new ones.

    `confirm_diagnosis` and `create_eval_cases_from_confirmed_flags` both used to
    insert an `EvalCase` per flag with no check for one that already existed, so
    a database in the field can hold many copies of the same
    `(trace_id, failing_step)`. The unique index is the backstop that makes the
    duplicate impossible; it can only be created once the existing rows are
    deduplicated, keeping the earliest of each group.
    """
    if not _table_exists(conn, "eval_cases"):
        return []
    if "idx_eval_cases_unique" in _indexes(conn, "eval_cases"):
        return []

    conn.execute(
        """
        DELETE FROM eval_cases
        WHERE rowid NOT IN (
            SELECT MIN(rowid) FROM eval_cases
            GROUP BY trace_id, failing_step, tenant_id
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_eval_cases_unique "
        "ON eval_cases(trace_id, failing_step, tenant_id)"
    )
    return ["eval_cases.unique_index"]


def _indexes(conn: sqlite3.Connection, table: str) -> set[str]:
    # `table` always comes from this module's own tuples, never from user input.
    return {row[1] for row in conn.execute(f"PRAGMA index_list({table})")}


__all__ = ["TENANT_SCOPED_TABLES", "migrate"]
