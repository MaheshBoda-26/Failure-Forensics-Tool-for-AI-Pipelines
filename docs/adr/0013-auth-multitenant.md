# ADR 0013: API Keys with Tenant-Scoped Storage

## Status
Accepted

## Context
Every record (traces, spans, flags, diagnoses, eval cases, eval runs, eval
results) lived in one SQLite file with no ownership. Two things were needed
before the tool could be shown to anyone other than its author: a way to tell who
a request belongs to, and a way to keep one customer's data out of another's
reads. Sessions, JWT, and an identity provider were all heavier than the problem:
there is one deployment, no browser login, and no user directory.

## Decision
- **API keys only.** `Authorization: Bearer ffk_…`; no JWT, no sessions, no
  refresh tokens. A key is minted by `forensics auth create-key`, printed once,
  and stored only as a bcrypt hash.
- **Prefix lookup, hash verification.** The first 12 characters (e.g.
  `ffk_abc123def`) are stored in a plain column with an index. Validation is one
  indexed read plus one bcrypt comparison — constant work regardless of tenant
  count, without a full-table scan or a per-row comparison.
- **`tenant_id` on every record**, defaulting to `'default'`. The default is what
  makes the migration boring: no constructor, query, or test needed changing, and
  every row that predates multi-tenancy is reachable instead of orphaned.
- **One idempotent migration** (`migrations.py`) called from both schema creators
  (`Storage._init_schema` and `tracing.init_db`), rather than teaching two
  `CREATE TABLE` statements about every historical shape.
- **Auth off means no filter, not a `default`-only filter.** The acceptance
  criterion is "`AUTH_ENABLED=false` works as before", so dev mode shows every
  trace; writes still stamp `default`.
- **The tenant filter is applied in SQL**, including on reads, so `limit`/`offset`
  stay meaningful and a cross-tenant read answers 404 from the index before any
  payload is loaded.

## Rationale
- Bcrypt is deliberately slow, which is the property that matters for a
  low-entropy-once-compared secret; the prefix index keeps that cost to one
  comparison rather than one per stored key.
- A 404 for another tenant's record (rather than 403) avoids confirming that the
  id exists at all.
- Failing closed on unknown prefix, wrong secret, revoked key, and over-long input
  (bcrypt raises above 72 bytes — an attacker-controlled header must not 500)
  makes every rejection look identical.
- Endpoints take `authorization: Annotated[str | None, Header()]` and call
  `get_current_tenant(...)` instead of using `Depends`: this codebase's tests call
  endpoint functions directly, and a `Depends` default leaks a `Depends` object
  into those calls. The trade is that the OpenAPI schema has no padlock.

## Consequences
- Keys never expire and have no scopes (see `docs/limitations.md` §13). Rotation
  is "mint a new key, revoke the old one".
- Turning auth off re-exposes every tenant's data; `AUTH_ENABLED` is a
  deployment-wide switch.
- `bcrypt` had to move from an optional extra to a required dependency, because
  `forensics.auth` is imported by `forensics.api` and `forensics.cli`. A
  `.[dev]`-only install could not import the package otherwise.
- Deactivating a tenant is the kill switch: `get_tenant` hides inactive tenants,
  so a 403 needs no data-plane change.

## Verification
- `tests/test_auth.py` — 53 tests across all eight spec-04 criteria: no key → 401
  on every data route, invalid/revoked/over-long key → 401, cross-tenant read and
  write → 404, inactive tenant → 403, keys bcrypt-hashed and never reprintable,
  and a real before/after migration of a pre-multi-tenancy database.
- `scripts/smoke.sh` runs the whole loop with a real key against a server started
  with `AUTH_ENABLED=true`, and is refused without one.
