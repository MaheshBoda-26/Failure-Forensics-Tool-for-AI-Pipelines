"""Storage factory — unified interface for SQLite and PostgreSQL backends.

Usage:
    from forensics.storage import get_storage
    storage = get_storage()
    # Works with either backend transparently
"""

import os
from pathlib import Path

from forensics.config import get_settings
from forensics.storage_base import StorageBase
from forensics.storage_sqlite import Storage as SQLiteStorage

# Global storage instance (lazy initialization)
_storage_instance: StorageBase | None = None


def _should_use_postgres() -> bool:
    """Determine if PostgreSQL should be used based on configuration."""
    # Check environment variable first (Render sets DATABASE_URL)
    if os.environ.get("DATABASE_URL"):
        return True

    # Check settings
    try:
        settings = get_settings()
        if hasattr(settings, "database_url") and settings.database_url:
            return True
    except Exception:
        pass

    return False


def get_storage(
    traces_dir: Path | None = None,
    db_path: Path | None = None,
    force_backend: str | None = None,
) -> StorageBase:
    """Get the storage backend instance.

    Args:
        traces_dir: Directory for trace JSON files (both backends)
        db_path: SQLite database path (ignored for PostgreSQL)
        force_backend: Force 'sqlite' or 'postgres' (overrides auto-detection)

    Returns:
        StorageBase implementation (SQLite or PostgreSQL)
    """
    global _storage_instance

    # If already initialized with the right backend, return it
    if _storage_instance is not None:
        current_is_postgres = _storage_instance.__class__.__name__ == "PostgresStorage"
        requested_postgres = force_backend == "postgres" or (
            force_backend is None and _should_use_postgres()
        )
        if current_is_postgres == requested_postgres:
            return _storage_instance
        # Different backend requested, need to close and recreate
        _storage_instance.close()
        _storage_instance = None

    # Determine backend
    use_postgres = force_backend == "postgres" or (force_backend is None and _should_use_postgres())

    if use_postgres:
        try:
            from forensics.storage_postgres import PostgresStorage
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on install
            raise RuntimeError(
                "DATABASE_URL selects the PostgreSQL backend, but the 'asyncpg' "
                "driver is not installed. Install the extra with "
                '`pip install -e ".[postgres]"`, or unset DATABASE_URL to use SQLite.'
            ) from exc

        _storage_instance = PostgresStorage(traces_dir=traces_dir, db_path=db_path)
    else:
        _storage_instance = SQLiteStorage(traces_dir=traces_dir, db_path=db_path)

    return _storage_instance


def reset_storage() -> None:
    """Reset the storage instance (for testing or backend switching)."""
    global _storage_instance
    if _storage_instance is not None:
        _storage_instance.close()
        _storage_instance = None


def get_storage_for_testing(
    traces_dir: Path | None = None,
    db_path: Path | None = None,
) -> StorageBase:
    """Get a fresh storage instance for testing (bypasses singleton).

    Always uses SQLite for tests unless explicitly requested otherwise.
    """
    return SQLiteStorage(traces_dir=traces_dir, db_path=db_path)


# Export SQLiteStorage as Storage for backward compatibility
Storage = SQLiteStorage


__all__ = [
    "Storage",
    "StorageBase",
    "get_storage",
    "get_storage_for_testing",
    "reset_storage",
]
