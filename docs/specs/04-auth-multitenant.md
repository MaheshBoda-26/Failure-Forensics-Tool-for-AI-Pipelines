# SPEC: Auth / Multi-tenant (API Keys Only)

## Summary
API key authentication with tenant isolation. No JWT, no sessions. API keys are hashed (bcrypt), prefixed for identification. Tenant-scoped storage.

## Config (`.env`)
```bash
AUTH_ENABLED=true|false              # default: false
AUTH_API_KEY_PREFIX=ffk_             # prefix for generated keys (ffk_ = Failure Forensics Key)
AUTH_BCRYPT_ROUNDS=12                # cost factor
```

## Data Models

### `src/forensics/models.py` — New
```python
class Tenant(BaseModel):
    tenant_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    active: bool = True


class APIKey(BaseModel):
    key_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    tenant_id: str
    key_hash: str  # bcrypt hash
    prefix: str  # first 8 chars for identification (ffk_abc123)
    name: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
```

### Extend existing models with `tenant_id`
```python
# Trace, Span, Flag, Diagnosis, EvalCase, EvalRun, EvalResult
tenant_id: str
```

## Schema Migrations (SQLite)

```sql
-- New tables
CREATE TABLE tenants (
    tenant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    active INTEGER DEFAULT 1
);

CREATE TABLE api_keys (
    key_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    prefix TEXT NOT NULL,
    name TEXT,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    last_used_at TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);
CREATE INDEX idx_api_keys_prefix ON api_keys(prefix);

-- Add tenant_id to existing tables
ALTER TABLE traces ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE spans ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE flags ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE diagnoses ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE eval_cases ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE eval_runs ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE eval_results ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';

CREATE INDEX idx_traces_tenant ON traces(tenant_id);
CREATE INDEX idx_spans_tenant ON spans(tenant_id);
-- ... etc for all tables
```

## Changes

### `src/forensics/auth.py` — New
```python
"""API key authentication."""

import bcrypt
import secrets
from typing import Optional
from forensics.storage import Storage
from forensics.models import Tenant, APIKey


class AuthManager:
    def __init__(self, storage: Storage, prefix: str = "ffk_", rounds: int = 12):
        self.storage = storage
        self.prefix = prefix
        self.rounds = rounds

    def create_tenant(self, name: str) -> Tenant:
        tenant = Tenant(name=name)
        with self.storage._conn() as conn:
            conn.execute(
                "INSERT INTO tenants (tenant_id, name, created_at, active) VALUES (?, ?, ?, 1)",
                (tenant.tenant_id, tenant.name, tenant.created_at.isoformat()),
            )
        return tenant

    def create_api_key(self, tenant_id: str, name: str | None = None) -> tuple[APIKey, str]:
        """Returns (APIKey, plaintext_key). Plaintext shown only once."""
        raw_key = self.prefix + secrets.token_urlsafe(24)
        key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(self.rounds)).decode()
        key_id = secrets.token_hex(6)
        api_key = APIKey(
            key_id=key_id,
            tenant_id=tenant_id,
            key_hash=key_hash,
            prefix=raw_key[:12],  # ffk_abc123def4
            name=name,
        )
        with self.storage._conn() as conn:
            conn.execute(
                """INSERT INTO api_keys (key_id, tenant_id, key_hash, prefix, name, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
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

    def validate_api_key(self, raw_key: str) -> Optional[APIKey]:
        if not raw_key.startswith(self.prefix):
            return None
        prefix = raw_key[:12]
        with self.storage._conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM api_keys WHERE prefix = ? AND revoked_at IS NULL",
                (prefix,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            # Verify bcrypt
            if not bcrypt.checkpw(raw_key.encode(), row["key_hash"].encode()):
                return None
            # Update last_used_at
            conn.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
                (datetime.now(UTC).isoformat(), row["key_id"]),
            )
            cols = [d[0] for d in cursor.description]
            return APIKey.model_validate(dict(zip(cols, row, strict=False)))

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        with self.storage._conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM tenants WHERE tenant_id = ? AND active = 1", (tenant_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return Tenant.model_validate(dict(zip(cols, row, strict=False)))
```

### `src/forensics/api.py`
```python
from fastapi import Depends, HTTPException, Header
from forensics.auth import AuthManager

auth = AuthManager(get_storage())

async def get_current_tenant(
    authorization: str = Header(None),
) -> Tenant:
    if not settings.auth_enabled:
        # Dev mode: return default tenant
        return Tenant(tenant_id="default", name="default", created_at=datetime.now(UTC), active=True)

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")

    api_key = authorization[7:]  # strip "Bearer "
    key_data = auth.validate_api_key(api_key)
    if not key_data:
        raise HTTPException(401, "Invalid or revoked API key")

    tenant = auth.get_tenant(key_data.tenant_id)
    if not tenant:
        raise HTTPException(403, "Tenant not found or inactive")
    return tenant

# Apply to all mutating endpoints
@app.post("/traces/{trace_id}/flag")
def create_flag(trace_id: str, request: FlagRequest, tenant: Tenant = Depends(get_current_tenant)):
    # Verify trace belongs to tenant
    trace = get_storage().get_trace(trace_id)
    if not trace or trace.tenant_id != tenant.tenant_id:
        raise HTTPException(404, "Trace not found")
    ...

# List endpoints filter by tenant
@app.get("/traces")
def list_traces(tenant: Tenant = Depends(get_current_tenant), ...):
    # Storage.list_traces adds tenant_id filter
    ...
```

### `src/forensics/storage.py`
All `list_*` and `get_*` methods accept `tenant_id` and filter:
```python
def list_traces(self, tenant_id: str, status=None, limit=100, offset=0):
    sql = "SELECT trace_id FROM traces WHERE tenant_id = ?"
    params = [tenant_id]
    if status:
        sql += " AND status = ?"
        params.append(status.value)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    ...
```

### `src/forensics/cli.py`
New `auth` command group:
```bash
forensics auth create-tenant --name "My Team"
forensics auth create-key --tenant <id> --name "ci-cd"
forensics auth list-keys --tenant <id>
forensics auth revoke-key --key-id <id>
```

## Acceptance Criteria
1. `AUTH_ENABLED=false`: no auth checks, works as before (dev mode)
2. `AUTH_ENABLED=true`: all endpoints require `Authorization: Bearer ffk_...`
3. Invalid/revoked key → 401
4. Cross-tenant access → 404 (trace not found)
5. `forensics auth create-tenant` creates tenant + returns `tenant_id`
6. `forensics auth create-key` returns plaintext key **once** (never shown again)
7. Keys are bcrypt-hashed; prefix allows identification without hash
8. Existing data gets `tenant_id='default'` via migration

## Effort
- **Files**: 4 new/modified, 1 migration script
- **Time**: ~6 hours
- **Risk**: Medium (schema migrations, auth logic)
