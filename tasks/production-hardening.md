# Production Hardening Implementation Plan

## Overview
Address the documented limitations in `docs/limitations.md` related to production readiness:
1. **Job Reaper** - Reclaim jobs stuck in `running` state after worker crash
2. **Rate Limiting** - Per-tenant request throttling
3. **API Key Expiry** - Automatic key rotation with configurable TTL
4. **PostgreSQL Backend** - Replace SQLite for horizontal scalability

---

## Phase 1: Job Reaper (High Impact, Low Effort)
**Files to modify:**
- `src/forensics/queue.py` - Add `reclaim_stalled_jobs()` method
- `src/forensics/worker.py` - Periodic reaper in worker loop
- `src/forensics/cli.py` - New `queue reaper` command
- `src/forensics/config.py` - Add `QUEUE_STALL_TIMEOUT` setting

**Design:**
- Add `claim_expires_at` column to `job_queue` table
- Worker updates this timestamp on claim + heartbeat
- Reaper finds jobs where `claim_expires_at < now()` and `status = 'running'`
- Resets them to `pending` with `worker_id = NULL`

---

## Phase 2: Rate Limiting (Medium Effort)
**Files to modify:**
- `src/forensics/auth.py` - Extend `APIKey` with rate limit fields
- `src/forensics/api.py` - Add rate limit middleware
- `src/forensics/config.py` - Add rate limit settings
- `src/forensics/storage.py` - New rate limit bucket table (or Redis if available)

**Design:**
- Token bucket per tenant (stored in SQLite or Redis)
- Configurable: requests/minute, burst allowance
- Return 429 with `Retry-After` header
- Skip for health checks and static assets

---

## Phase 3: API Key Expiry (Medium Effort)
**Files to modify:**
- `src/forensics/auth.py` - Add `expires_at` to `APIKey` model and table
- `src/forensics/config.py` - Add `AUTH_KEY_TTL_DAYS` setting
- `src/forensics/cli.py` - Update `create-key` with `--ttl` option
- `src/forensics/api.py` - Check expiry in `validate_api_key()`

**Design:**
- Optional `expires_at` column on `api_keys` table
- `validate_api_key()` rejects expired keys
- CLI: `forensics auth create-key --ttl 90`
- Background cleanup of expired keys (optional)

---

## Phase 4: PostgreSQL Backend (High Effort)
**Files to modify:**
- `src/forensics/storage.py` - Abstract storage interface + PostgreSQL impl
- `src/forensics/queue.py` - PostgreSQL-compatible queries
- `src/forensics/auth.py` - PostgreSQL-compatible queries
- `src/forensics/config.py` - Add `DATABASE_URL` setting
- `pyproject.toml` - Add `asyncpg` dependency

**Design:**
- Keep SQLite as default (zero-config for demo)
- `DATABASE_URL=postgresql://...` switches backend
- Same API, different implementations behind interface
- Use `asyncpg` for async operations
- Migration script for schema

---

## Phase 5: Integration & Testing
- Add tests for each new feature
- Update `scripts/smoke.sh` to test reaper, rate limits, key expiry
- Update `render.yaml` for PostgreSQL option
- Update documentation

---

## Implementation Order (Dependencies)
1. **Job Reaper** - Independent, high value
2. **API Key Expiry** - Independent, uses existing auth infrastructure
3. **Rate Limiting** - Depends on auth tenant resolution
4. **PostgreSQL Backend** - Most complex, do last

---

## Configuration Additions (`.env.example`)

```bash
# Job Reaper
QUEUE_STALL_TIMEOUT=300          # seconds before a running job is considered stalled

# Rate Limiting
RATE_LIMIT_ENABLED=false
RATE_LIMIT_REQUESTS_PER_MINUTE=60
RATE_LIMIT_BURST=10

# API Key Expiry
AUTH_KEY_TTL_DAYS=90             # 0 = no expiry

# PostgreSQL
DATABASE_URL=                    # if set, use PostgreSQL instead of SQLite
```

---

## Acceptance Criteria

| Feature | Verification |
|---------|--------------|
| Job Reaper | Kill worker mid-job → job returns to pending within `QUEUE_STALL_TIMEOUT` |
| Rate Limiting | Burst requests → 429 with `Retry-After` header |
| Key Expiry | Create key with TTL → key stops working after TTL |
| PostgreSQL | Set `DATABASE_URL` → all operations work identically |
