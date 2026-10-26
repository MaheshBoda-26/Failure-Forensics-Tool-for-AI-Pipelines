"""Rate limiting using token bucket algorithm stored in SQLite.

Each tenant gets a token bucket that refills at a fixed rate. When a request
arrives, we try to consume a token; if the bucket is empty, we return 429.

The bucket state is stored in a `rate_limit_buckets` table with:
- tenant_id
- tokens (real, can be fractional)
- last_refill (timestamp)

This is a simple, dependency-free implementation suitable for the demo scale.
For production, consider Redis for distributed rate limiting.
"""

import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from forensics.config import get_settings

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
        if db_path is None:
            db_path = get_settings().db_path
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _get_bucket(self, tenant_id: str) -> tuple[float, float]:
        """Get current tokens and last_refill for a tenant."""
        settings = get_settings()
        capacity = settings.rate_limit_burst + settings.rate_limit_requests_per_minute
        with self._conn() as conn:
            row = conn.execute(
                "SELECT tokens, last_refill FROM rate_limit_buckets WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            if row is None:
                # New tenant starts with full bucket
                return capacity, time.time()
            return row[0], row[1]

    def _update_bucket(self, tenant_id: str, tokens: float, last_refill: float) -> None:
        """Update bucket state."""
        with self._conn() as conn:
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
        """Try to consume tokens from the bucket.

        Returns (success, retry_after_seconds).
        """
        settings = get_settings()
        capacity = settings.rate_limit_burst + settings.rate_limit_requests_per_minute
        refill_rate = settings.rate_limit_requests_per_minute / 60.0

        current_tokens, last_refill = self._get_bucket(tenant_id)
        now = time.time()

        # Refill based on elapsed time
        elapsed = now - last_refill
        current_tokens = min(capacity, current_tokens + elapsed * refill_rate)

        if current_tokens >= tokens:
            current_tokens -= tokens
            self._update_bucket(tenant_id, current_tokens, now)
            return True, 0.0

        # Not enough tokens
        retry_after = (tokens - current_tokens) / refill_rate
        self._update_bucket(tenant_id, current_tokens, now)
        return False, retry_after

    def get_bucket_state(self, tenant_id: str) -> dict[str, str | float]:
        """Get current bucket state for debugging/monitoring."""
        settings = get_settings()
        capacity = settings.rate_limit_burst + settings.rate_limit_requests_per_minute
        current_tokens, last_refill = self._get_bucket(tenant_id)
        now = time.time()
        refill_rate = settings.rate_limit_requests_per_minute / 60.0
        elapsed = now - last_refill
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
