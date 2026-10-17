"""API key authentication and tenant management (spec 04).

No JWT, no sessions. A tenant presents `Authorization: Bearer ffk_...`; the key
is looked up by its identifying prefix, verified against its bcrypt hash, and the
owning tenant is returned. Only the hash is ever stored — the plaintext key
exists exactly once, in the return value of `create_api_key`.

Design notes:

* Keys are looked up by `prefix` (the first 12 characters, e.g. `ffk_abc123def`)
  rather than by scanning every row, so validating a key costs one indexed read
  plus one bcrypt comparison regardless of how many tenants exist.
* Bcrypt refuses inputs longer than 72 bytes. A caller-supplied
  `Authorization` header is attacker-controlled, so an over-long key is rejected
  before it reaches bcrypt instead of raising a 500.
* `tenants` is the only table referenced by a foreign key (`api_keys.tenant_id`),
  and that foreign key is ON in every connection. `create_api_key` therefore
  checks the tenant up front and raises `ValueError` rather than surfacing an
  opaque `IntegrityError`.
"""

import secrets
from datetime import UTC, datetime

import bcrypt

from forensics.models import DEFAULT_TENANT_ID, APIKey, Tenant
from forensics.storage import Storage

#: Bcrypt truncates at 72 bytes and raises on longer input in bcrypt >= 4.1.
#: Our own keys are ~36 characters; this bound only ever rejects hostile input.
_MAX_KEY_BYTES = 200

#: Characters of the key retained for identification (`ffk_` + 8 chars).
_PREFIX_CHARS = 12


class AuthManager:
    """Tenant and API key operations over the shared SQLite database."""

    def __init__(self, storage: Storage, prefix: str = "ffk_", rounds: int = 12) -> None:
        if not prefix:
            raise ValueError("AUTH_API_KEY_PREFIX must be non-empty")
        if not 4 <= rounds <= 16:
            raise ValueError("AUTH_BCRYPT_ROUNDS must be between 4 and 16")
        self.storage = storage
        self.prefix = prefix
        self.rounds = rounds

    # --- Tenants ---

    def create_tenant(self, name: str) -> Tenant:
        """Create a tenant and return it (with its generated `tenant_id`)."""
        if not name.strip():
            raise ValueError("Tenant name must be non-empty")
        tenant = Tenant(name=name.strip())
        with self.storage._conn() as conn:
            conn.execute(
                "INSERT INTO tenants (tenant_id, name, created_at, active) VALUES (?, ?, ?, 1)",
                (tenant.tenant_id, tenant.name, tenant.created_at.isoformat()),
            )
        return tenant

    def ensure_default_tenant(self) -> Tenant:
        """Create the `default` tenant if it does not exist.

        Dev mode (auth disabled) and every row written before multi-tenancy
        belong to this tenant, and the CLI must be able to add a key to it even
        though nothing ever "created" it.
        """
        existing = self.get_tenant(DEFAULT_TENANT_ID, active_only=False)
        if existing is not None:
            return existing
        tenant = Tenant(name="default")
        tenant.tenant_id = DEFAULT_TENANT_ID
        with self.storage._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tenants (tenant_id, name, created_at, active) "
                "VALUES (?, ?, ?, 1)",
                (tenant.tenant_id, tenant.name, tenant.created_at.isoformat()),
            )
        return tenant

    def get_tenant(self, tenant_id: str, active_only: bool = True) -> Tenant | None:
        """Get a tenant. Inactive tenants are hidden unless `active_only=False`."""
        sql = "SELECT * FROM tenants WHERE tenant_id = ?"
        params: list[object] = [tenant_id]
        if active_only:
            sql += " AND active = 1"
        with self.storage._conn() as conn:
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return Tenant.model_validate(dict(zip(cols, row, strict=False)))

    def list_tenants(self) -> list[Tenant]:
        """List every tenant, active or not."""
        with self.storage._conn() as conn:
            cursor = conn.execute("SELECT * FROM tenants ORDER BY created_at")
            rows = cursor.fetchall()
            cols = [d[0] for d in cursor.description]
            return [Tenant.model_validate(dict(zip(cols, r, strict=False))) for r in rows]

    def set_tenant_active(self, tenant_id: str, active: bool) -> bool:
        """Activate or deactivate a tenant. Returns False if it does not exist.

        Deactivating a tenant is the multi-tenant kill switch: `get_tenant`
        hides it, so `GET`/`POST` on its data answer 403 without touching a
        single trace row.
        """
        with self.storage._conn() as conn:
            cursor = conn.execute(
                "UPDATE tenants SET active = ? WHERE tenant_id = ?",
                (int(active), tenant_id),
            )
            return cursor.rowcount > 0

    # --- API keys ---

    def create_api_key(self, tenant_id: str, name: str | None = None) -> tuple[APIKey, str]:
        """Mint a key. Returns `(APIKey, plaintext)`; the plaintext is not stored."""
        if self.get_tenant(tenant_id, active_only=False) is None:
            raise ValueError(f"Unknown tenant: {tenant_id}")

        raw_key = self.prefix + secrets.token_urlsafe(24)
        key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(self.rounds)).decode()
        api_key = APIKey(
            key_id=secrets.token_hex(6),
            tenant_id=tenant_id,
            key_hash=key_hash,
            prefix=raw_key[:_PREFIX_CHARS],
            name=name,
        )
        with self.storage._conn() as conn:
            conn.execute(
                """
                INSERT INTO api_keys (key_id, tenant_id, key_hash, prefix, name, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    api_key.key_id,
                    api_key.tenant_id,
                    api_key.key_hash,
                    api_key.prefix,
                    api_key.name,
                    api_key.created_at.isoformat(),
                ),
            )
        return api_key, raw_key

    def validate_api_key(self, raw_key: str) -> APIKey | None:
        """Return the key record for a valid key, else None.

        Fails closed: an unknown prefix, a revoked key, an over-long key, and a
        hash mismatch are indistinguishable to the caller (all None → 401).
        """
        if not raw_key or not raw_key.startswith(self.prefix):
            return None
        if len(raw_key.encode()) > _MAX_KEY_BYTES:
            return None

        with self.storage._conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM api_keys WHERE prefix = ? AND revoked_at IS NULL",
                (raw_key[:_PREFIX_CHARS],),
            )
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            data = dict(zip(cols, row, strict=False))

            try:
                matched = bcrypt.checkpw(raw_key.encode(), str(data["key_hash"]).encode())
            except ValueError:
                # A malformed stored hash is a data problem, not a valid key.
                return None
            if not matched:
                return None

            used_at = datetime.now(UTC).isoformat()
            conn.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
                (used_at, data["key_id"]),
            )
            data["last_used_at"] = used_at
            return APIKey.model_validate(data)

    def list_keys(self, tenant_id: str) -> list[APIKey]:
        """List a tenant's keys, including revoked ones.

        The caller (the CLI) is responsible for not printing `key_hash`.
        """
        with self.storage._conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM api_keys WHERE tenant_id = ? ORDER BY created_at", (tenant_id,)
            )
            rows = cursor.fetchall()
            cols = [d[0] for d in cursor.description]
            return [APIKey.model_validate(dict(zip(cols, r, strict=False))) for r in rows]

    def get_key(self, key_id: str) -> APIKey | None:
        """Get a single key record by ID."""
        with self.storage._conn() as conn:
            cursor = conn.execute("SELECT * FROM api_keys WHERE key_id = ?", (key_id,))
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return APIKey.model_validate(dict(zip(cols, row, strict=False)))

    def revoke_key(self, key_id: str) -> bool:
        """Revoke a key. Returns False if it does not exist or was already revoked."""
        with self.storage._conn() as conn:
            cursor = conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE key_id = ? AND revoked_at IS NULL",
                (datetime.now(UTC).isoformat(), key_id),
            )
            return cursor.rowcount > 0


__all__ = ["AuthManager"]
