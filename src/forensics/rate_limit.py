"""Rate limiting using token bucket algorithm stored in SQLite or PostgreSQL.

Each tenant gets a token bucket that refills at a fixed rate. When a request
arrives, we try to consume a token; if the bucket is empty, we return 429.

The bucket state is stored in a `rate_limit_buckets` table with:
- tenant_id
- tokens (real, can be fractional)
- last_refill (timestamp)

Uses an atomic transaction (`BEGIN IMMEDIATE` on SQLite, `SELECT ... FOR UPDATE`
on PostgreSQL when `DATABASE_URL` is configured) so concurrent requests for the
same tenant cannot race between reading and updating the bucket.
"""

import sqlite3
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from forensics.config import get_operational_settings
from forensics.storage import _should_use_postgres, get_storage

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS rate_limit_buckets (
        tenant_id TEXT PRIMARY KEY,
        tokens REAL NOT NULL,
        last_refill REAL NOT NULL
    )
    """,
)


class RateLimiter:
    """Token bucket rate limiter per tenant."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._use_postgres = db_path is None and _should_use_postgres()
        if db_path is None:
            db_path = get_operational_settings().db_path
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        if not self._use_postgres:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _get_bucket(self, tenant_id: str) -> tuple[float, float]:
        """Get current tokens and last_refill for a tenant."""
        settings = get_operational_settings()
        capacity = float(settings.rate_limit_burst + settings.rate_limit_requests_per_minute)
        if self._use_postgres:
            storage: Any = get_storage()

            async def _pg_get() -> tuple[float, float]:
                async with storage._conn() as conn:
                    row = await conn.fetchrow(
                        "SELECT tokens, last_refill FROM rate_limit_buckets WHERE tenant_id = $1",
                        tenant_id,
                    )
                    if row is None:
                        return capacity, time.time()
                    refill_ts = (
                        row["last_refill"].timestamp()
                        if isinstance(row["last_refill"], datetime)
                        else float(row["last_refill"])
                    )
                    return float(row["tokens"]), refill_ts

            return cast(tuple[float, float], storage._run_coro(_pg_get()))

        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT tokens, last_refill FROM rate_limit_buckets WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            if row is None:
                # New tenant starts with full bucket
                return capacity, time.time()
            return float(row[0]), float(row[1])

    def _update_bucket(self, tenant_id: str, tokens: float, last_refill: float) -> None:
        """Update bucket state."""
        if self._use_postgres:
            storage: Any = get_storage()

            async def _pg_update() -> None:
                refill_dt = datetime.fromtimestamp(last_refill, tz=UTC)
                async with storage._conn() as conn:
                    await conn.execute(
                        """
                        INSERT INTO rate_limit_buckets (tenant_id, tokens, last_refill)
                        VALUES ($1, $2, $3)
                        ON CONFLICT(tenant_id) DO UPDATE SET
                            tokens = EXCLUDED.tokens,
                            last_refill = EXCLUDED.last_refill
                        """,
                        tenant_id,
                        tokens,
                        refill_dt,
                    )

            storage._run_coro(_pg_update())
            return

        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO rate_limit_buckets (tenant_id, tokens, last_refill)
                VALUES (?, ?, ?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    tokens = excluded.tokens,
                    last_refill = excluded.last_refill
                """,
                (tenant_id, tokens, last_refill),
            )

    def try_consume(self, tenant_id: str, tokens: int = 1) -> tuple[bool, float]:
        """Try to consume tokens from the bucket atomically.

        Returns (success, retry_after_seconds).
        """
        settings = get_operational_settings()
        capacity = float(settings.rate_limit_burst + settings.rate_limit_requests_per_minute)
        refill_rate = settings.rate_limit_requests_per_minute / 60.0

        if self._use_postgres:
            storage: Any = get_storage()

            async def _pg_consume() -> tuple[bool, float]:
                now = time.time()
                now_dt = datetime.fromtimestamp(now, tz=UTC)
                async with storage._conn() as conn, conn.transaction():
                    row = await conn.fetchrow(
                        "SELECT tokens, last_refill FROM rate_limit_buckets "
                        "WHERE tenant_id = $1 FOR UPDATE",
                        tenant_id,
                    )
                    if row is None:
                        current_tokens, last_refill = capacity, now
                    else:
                        current_tokens = float(row["tokens"])
                        last_refill = (
                            row["last_refill"].timestamp()
                            if isinstance(row["last_refill"], datetime)
                            else float(row["last_refill"])
                        )
                    elapsed = max(0.0, now - last_refill)
                    current_tokens = min(capacity, current_tokens + elapsed * refill_rate)
                    if current_tokens >= tokens:
                        current_tokens -= tokens
                        allowed, retry_after = True, 0.0
                    else:
                        allowed = False
                        retry_after = (tokens - current_tokens) / refill_rate
                    await conn.execute(
                        """
                        INSERT INTO rate_limit_buckets (tenant_id, tokens, last_refill)
                        VALUES ($1, $2, $3)
                        ON CONFLICT(tenant_id) DO UPDATE SET
                            tokens = EXCLUDED.tokens,
                            last_refill = EXCLUDED.last_refill
                        """,
                        tenant_id,
                        current_tokens,
                        now_dt,
                    )
                    return allowed, retry_after

            return cast(tuple[bool, float], storage._run_coro(_pg_consume()))

        with self._lock, self._conn() as conn:
            now = time.time()
            row = conn.execute(
                "SELECT tokens, last_refill FROM rate_limit_buckets WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            if row is None:
                current_tokens, last_refill = capacity, now
            else:
                current_tokens, last_refill = float(row[0]), float(row[1])

            # Refill based on elapsed time
            elapsed = max(0.0, now - last_refill)
            current_tokens = min(capacity, current_tokens + elapsed * refill_rate)

            if current_tokens >= tokens:
                current_tokens -= tokens
                allowed, retry_after = True, 0.0
            else:
                allowed = False
                retry_after = (tokens - current_tokens) / refill_rate

            conn.execute(
                """
                INSERT INTO rate_limit_buckets (tenant_id, tokens, last_refill)
                VALUES (?, ?, ?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    tokens = excluded.tokens,
                    last_refill = excluded.last_refill
                """,
                (tenant_id, current_tokens, now),
            )
            return allowed, retry_after

    def get_bucket_state(self, tenant_id: str) -> dict[str, str | float]:
        """Get current bucket state for debugging/monitoring."""
        settings = get_operational_settings()
        capacity = float(settings.rate_limit_burst + settings.rate_limit_requests_per_minute)
        current_tokens, last_refill = self._get_bucket(tenant_id)
        now = time.time()
        refill_rate = settings.rate_limit_requests_per_minute / 60.0
        elapsed = max(0.0, now - last_refill)
        current_tokens = min(capacity, current_tokens + elapsed * refill_rate)
        return {
            "tenant_id": tenant_id,
            "tokens": current_tokens,
            "capacity": capacity,
            "refill_rate_per_sec": refill_rate,
        }


# Process-wide rate limiter
_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Return the process-wide RateLimiter, creating it on first use."""
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def reset_rate_limiter() -> None:
    """Drop the cached limiter (used by tests)."""
    global _limiter
    _limiter = None


__all__ = ["RateLimiter", "get_rate_limiter", "reset_rate_limiter"]
