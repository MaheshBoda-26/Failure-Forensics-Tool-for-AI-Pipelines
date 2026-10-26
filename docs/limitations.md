# Failure Forensics Tool — Known Limitations

This document enumerates what the tool does **not** do, as shipped. Every entry
is a scope decision or a known gap with its impact and mitigation stated. Nothing
here is presented as a bug.

Last verified against the working tree with `make check` (ruff, mypy strict, and
the test suite: 465 passing, `-m "not live"`). The count is stated as the current
suite size rather than a commit-pinned one, because the previous revision's
"344 tests at `cb373e9`" drifted two layers behind the code it described.

---

## Resolved in this revision

The previous revision of this file listed shipped work as missing. These eleven
are implemented, tested, and reachable by configuration:

| Previously listed as missing | Now |
|---|---|
| "No Judge LLM" | `JUDGE_ENABLED=true` runs a semantic judge; verdicts land in `Diagnosis.judge_analysis` (spec 02). Per-step spend is separable via the `judge` tag. |
| "No Multi-Tenant / Auth" | API keys (bcrypt, `ffk_` prefix) + tenant-scoped reads and writes (spec 04). `tenant_id` on all seven tables, with an idempotent migration for existing data. |
| "No Scheduled Eval / Alerting" | APScheduler cron job with HMAC-signed regression/improvement webhooks and a SQLite job store that survives restarts (spec 06). |
| "No Custom Check Plugins" | `CHECK_PLUGINS=module:attr,…` registers custom deterministic checks, validated at startup (spec 05). |
| "No PII Redaction" | Regex redaction at the persistence boundary, before the JSON and SQLite writes (spec 03). |
| "No React Frontend (Streamlit Only)" | `frontend/` is React + Vite + React Flow; `streamlit_app.py` is the legacy explorer. |
| "No Job Reaper" | `QUEUE_STALL_TIMEOUT` + `claim_expires_at` column + heartbeat loop + periodic reaper; `forensics queue reaper` CLI command for manual recovery. |
| "No Rate Limiting" | Per-tenant token bucket in SQLite with `RATE_LIMIT_ENABLED`, `RATE_LIMIT_REQUESTS_PER_MINUTE`, `RATE_LIMIT_BURST`; FastAPI middleware with 429 + `Retry-After` headers. |
| "No API Key Expiry" | `AUTH_KEY_TTL_DAYS` + `--ttl` CLI option + expiration check in `validate_api_key`. |
| "No Advanced Analytics" | Cost/token tracking, model performance comparison, failure correlation heatmap, cost trend charts, latency analysis (see `AnalyticsView.tsx`). |
| "The bundled UI cannot authenticate" | `frontend/` takes an API key from the settings panel, keeps it in `localStorage`, and sends `Authorization: Bearer` on every request (`api/client.ts`); a 401 surfaces as an error toast instead of silently empty views. |
| "No Custom Domain Checks" | 9 built-in custom checks: PII detection, financial validation, contract/legal, report structure, correspondence metadata, mixed document consistency (see `src/forensics/custom_checks.py`). |

Two further configuration gaps were closed at the same time, because a setting
that silently does nothing is worse than a missing one:

- `OTEL_*` environment variables were **ignored entirely** (pydantic-settings
  fills a nested model only from `OTEL__EXPORTER` and drops env vars matching no
  field), so spec 01's OTel feature was unreachable. `OTEL_EXPORTER=none|console|otlp`,
  `OTEL_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_SAMPLING_RATE` and
  `OTEL_MAX_PROMPT_CHARS` now bind.
- `OTEL_SAMPLING_RATE` was parsed but never applied, and `max_prompt_chars` was
  read with a `getattr` default for a setting that did not exist. Sampling is now
  a per-trace head-based decision, and the prompt-truncation limit is real.

---

## 1. ~~The bundled UI cannot authenticate~~ — **RESOLVED**

**Limitation**: with `AUTH_ENABLED=true`, every data route requires
`Authorization: Bearer ffk_…`, and the React client sent no such header. There
was no key-entry UI, so the bundled UI showed empty views.

**Resolution**: `frontend/src/components/Header.tsx` has a settings panel that
stores the key with `setApiKey` (persisted in `localStorage`, cleared by
`clearApiKey`), and `frontend/src/api/client.ts` attaches
`Authorization: Bearer <key>` to every request. A rejected key surfaces as a
401 the UI reports, rather than as an empty dashboard that looks like "no data".

**Impact**: the demo deployment still runs `AUTH_ENABLED=false`
(`render.yaml`), so the public instance needs no key; a locked-down deployment
can now use the bundled UI with a key instead of falling back to `curl`.

## 2. ~~A worker that dies mid-job leaves the job `running` forever~~ — **RESOLVED**

**Limitation**: jobs are claimed by an atomic `UPDATE … RETURNING`, but nothing
reclaims a job whose worker was killed (SIGKILL, OOM, deploy) after claiming it.
There is no visibility timeout and no reaper.

**Resolution**: Implemented in `src/forensics/queue.py` and `src/forensics/worker.py`:

- Added `claim_expires_at` column to `job_queue` table
- Worker sets expiration on claim (`QUEUE_STALL_TIMEOUT`, default 300s)
- Worker sends periodic heartbeats (every `stall_timeout/3` seconds) to extend claim
- Periodic reaper in worker loop reclaims stalled jobs from other workers
- `forensics queue reaper` CLI command for manual recovery
- `forensics queue reaper --stall-timeout N` for custom timeout

**Impact**: Jobs from crashed workers are automatically reclaimed and retried.

**Previous mitigation** (now obsolete): `SIGTERM` is handled cleanly (the loop finishes its current batch
and exits), which covers an ordinary deploy or restart. For a hard kill, requeue
by hand:
```sql
UPDATE job_queue SET status='pending', worker_id=NULL WHERE job_id='…';
```

---

## 3. The scheduled eval is tenant-agnostic

**Limitation**: the nightly run evaluates **every** tenant's eval cases in one
`EvalRun`, which is recorded under the `default` tenant.

**Impact**: a per-tenant accuracy history is not available from the scheduled
run. Per-tenant runs are available through `POST /eval/run` with that tenant's
key.

**Mitigation**: with a single-tenant deployment (the demo) this is exactly the
desired behaviour — one number for the whole instance. A per-tenant schedule is a
configuration shape (tenant → cron), not an algorithm.

---

## 4. With auth disabled, the API serves every tenant's data

**Limitation**: `AUTH_ENABLED=false` applies **no** tenant filter. That is
deliberate — the criterion is "works as before" — but it means switching auth off
re-exposes data that auth mode separates.

**Impact**: an operator who turns auth off on a database that already holds
several tenants' data can read all of it. Records are still *written* under a
concrete tenant (`default` in dev mode), so the separation is intact if auth is
turned back on.

**Mitigation**: treat `AUTH_ENABLED` as a deployment-wide switch, not a per-user
preference. The value is logged at startup through the resolved settings, and
`/scheduler/status` and `/jobs` are authenticated in both modes.

---

## 5. An in-flight job's document text is on disk, unredacted

**Limitation**: `job_queue.payload` holds the document text the pipeline is about
to send to the model. PII redaction (spec 03) covers traces at the persistence
boundary; the queue is pipeline *input*, and redacting it would change what the
model sees.

**Impact**: while a job is pending or running, its raw document text sits in the
same SQLite file as the (redacted) traces. A finished job's payload is cleared to
`{}`, so the exposure window is the job's lifetime, not the retention window.

**Mitigation**: this is why payloads are cleared on `completed`/`failed` and why
`QUEUE_JOB_TTL` (default 24h) only ever deletes terminal rows. A deployment that
cannot accept even a short-lived plaintext window should reference documents by
path (`doc_id`) instead of inlining `doc_text`.

---

## 6. The fault scorecard's `context_loss` fault needs a trace with entities

**Limitation**: `_inject_context_loss` summarizes without the grounded entities.
If the baseline trace extracted **no** entities, a summary that omits them is
indistinguishable from one that never had them, so `context_preservation` has
nothing to compare and the fault is scored as a miss.

**Impact**: the scorecard under-reports the analyzer's real accuracy on traces
produced by `DRY_RUN=true`, whose canned extraction returns no entities for every
document but one (the seeded `invoice_001`).
`forensics benchmark-analyzer` reports 85.7% step/category accuracy in that
configuration; the remaining miss is this fault, not a detector failure.

The scorecard also picks its baseline as *whatever trace the store returns
first*, which is the most recently written one. On a store that has accumulated
several runs, the reported figure therefore moves between 5/7 and 6/7
(71.4%–85.7%) without any analyzer change: it is measuring the baseline, not the
detector. Fixing the baseline (or scoring every trace and averaging) is the
honest version and is not yet done.

**Mitigation**: score against a real (non-dry-run) baseline trace, which has
entities to lose, and read the score next to the baseline trace ID the scorecard
prints. The injector's own no-op class of bug — choosing a label equal to the
correct one — was fixed and is regression-tested.

---

## 7. DRY_RUN's canned responses are a fixture, not model behaviour

**Limitation**: the offline fixture returns hand-written output keyed on the
document id (`<type>_NNN`, the dataset's own shape), with a prompt-keyword
heuristic only as the fallback for an id with no dataset prefix. It stands in for
a model; it does not emulate one.

**Impact**: `forensics demo` in dry-run mode reproduces every label in
`dataset/labels.json` — the five seeded failures at their declared step and
category, and the ten clean documents — and reports 13/15 classifications. The
two misses are the two *seeded* misclassifications (`contract_001`,
`mixed_001`), so they are the fixture working, not a pipeline result. A real
accuracy figure still requires a real API key.

**Mitigation**: treat dry-run output as evidence about the analyzer and the eval
loop, which is what the seeded failures exist to test, and never as a model
benchmark. `tests/test_dry_run_dataset.py` asserts the label reproduction
directly, so this claim cannot silently rot again the way the prompt-keyword
version did (which reported 3/15 and reproduced none of the five failures).

---

## 7b. Re-diagnosing a persisted trace under PII redaction sees redacted spans

**Limitation**: the write path analyzes the *unredacted* trace and redacts on
disk. `POST /traces/{trace_id}/diagnose` instead loads the persisted trace, which
is redacted by design, so with `PII_REDACTION_ENABLED=true` an extracted email,
phone, or IP is `█` in the span while `doc_text` is not — and `entity_grounding`
flags the correctly extracted entity as a hallucination.

**Impact**: re-diagnosis of an old trace is unreliable when redaction is on. The
original analysis, produced at write time, is correct and already stored.

**Mitigation**: read the stored diagnosis rather than re-deriving it, or run with
redaction off if re-diagnosis of historical traces is required. The alternative —
persisting the unredacted trace — would reopen the leak spec 03 closes.

---

## 8. No pre-flight cost check

**Limitation**: spend caps are enforced *after* the response, using actual token
counts. Nothing rejects a request that is likely to exceed a cap before sending
it.

**Impact**: one request can overshoot the per-run cap, and the global cap can be
overshot by one request. With the default prices (0.0, see item 10) the counters
read as no spend at all.

**Mitigation**: caps are generous for the demo ($0.50/run, $20 global) and
post-hoc accounting prevents repeated overages. Pre-flight estimation from
`max_tokens × price` is a small addition when real prices are configured.

---

## 9. Prompt templates are not stored with traces

**Limitation**: `prompt_version` records a version identifier, and the span holds
the full rendered prompt, but the *template* that produced it is not archived.

**Impact**: a trace can be replayed and diffed against the code in the tree, but
not against the template revision that was live when it ran.

**Mitigation**: `prompt_version` is on every `VersionedModel`, and the prompt text
itself is in the span, so the evidence for "what the model actually saw" is
complete. Template archiving is a storage feature, not a forensics one.

---

## 10. Prices default to zero

**Limitation**: `.env.example` ships `*_PRICE_IN`/`*_PRICE_OUT` as `0.0` with a
`VERIFY BEFORE USE` banner, because hosted prices change and inventing them is
worse than leaving them blank.

**Impact**: `total_cost_usd`, the per-run/global caps, and the per-step spend
attribution (`judge` included) all compute correctly but read zero until real
prices are set.

**Mitigation**: set real per-1M prices in `.env`. The accounting code is
price-agnostic; only the values need filling in.

---

## 11. The propagation check is a heuristic, not a dataflow analysis

**Limitation**: `propagation` detects the common case — terminology that appears
in one step's output and goes missing downstream. It does not build a dependency
graph of the pipeline's data flow.

**Impact**: a cascading failure can be reported at several steps rather than as
one root cause. Local fidelity and global fidelity coincide for most steps.

**Mitigation**: the evidence chain cites the upstream span, so a human can follow
the cascade. The check is cheap, deterministic, and explainable, which is the
property that made it worth shipping over a graph analysis.

---

## 12. Single-writer SQLite

**Limitation**: SQLite in WAL mode allows concurrent readers and one writer. The
queue's claims are atomic and `busy_timeout=5000` makes a second writer wait
instead of failing, but writers are still serialised.

**Impact**: the API, the CLI, a worker fleet, and the scheduler share one writer
slot. Throughput is bounded by write frequency, not by CPU. Not horizontally
scalable beyond one file.

**Mitigation**: traces are written once and read many times, which is the shape
WAL handles best. `storage_postgres.PostgresStorage` implements the same
`StorageBase` contract, so switching backends is configuration
(`DATABASE_URL`) rather than a rewrite — though that backend is unproven in
practice; see item 18.

---

## 13. API keys have no scopes (expiry is ~~resolved~~)

**Limitation**: keys are bcrypt-hashed, prefixed for identification, revocable,
and now expirable — `AUTH_KEY_TTL_DAYS` sets a default lifetime and
`forensics auth create-key --ttl N` overrides it per key; an expired key fails
validation and is indistinguishable from a revoked one (both 401). What remains
missing is **scopes**: every key of a tenant still has that tenant's full read
and write access.

**Impact**: a leaked key is bounded by its TTL rather than being valid forever,
but a dashboard or CI key that should only read can still submit traces and
confirm diagnoses.

**Mitigation**: mint short-TTL keys for machine callers
(`--ttl 7`), keep `--ttl 0` for nothing, and revoke on suspicion —
`revoke-key` takes effect on the next request and revoked rows stay for audit.
Scoped keys would mean an `scopes` column plus a per-route check; that is a
deliberate omission, not an oversight.

---

## 14. ~~No rate limiting~~ — **RESOLVED**

**Limitation**: the API authenticated but did not throttle, so one tenant could
issue requests as fast as the process could serve them and consume the whole
instance's capacity.

**Resolution**: `rate_limit.py` implements a per-tenant token bucket persisted in
SQLite (so limits survive a restart and apply across workers):
`RATE_LIMIT_ENABLED`, `RATE_LIMIT_REQUESTS_PER_MINUTE`, `RATE_LIMIT_BURST`. A
FastAPI middleware rejects over-limit requests with `429` and a `Retry-After`
header, deriving the tenant from the same API key the endpoint will use.

**Impact**: the limiter is process-local in its accounting only in the sense
that the bucket rows live in the shared database; the demo stays
`RATE_LIMIT_ENABLED=false`. The spend caps remain per-process, which is a
separate limitation (item 8).

---

## 15. Small demo dataset

**Limitation**: 15 documents, 5 seeded failures (one per type, plus a mixed one).

**Impact**: the committed 15-document corpus is illustrative, not statistically
meaningful; failure-rate trends have too few points to be read as a trend.

**Mitigation**: `forensics generate-dataset generate` builds a larger corpus. It
is seeded and clock-free — the same `--seed` reproduces the output byte-for-byte,
which is pinned by `tests/test_generate_dataset.py` — and `dataset_large/`
ships 150 documents (30 per type) with 43 seeded failures at seed 12345.
`labels.json` remains the only input to ground-truth checks, and the fault
injector derives arbitrarily many labelled failures from a single clean trace.

---

## 16. Live-model behaviour is not exercised in CI

**Limitation**: the automated suite never calls a model. `httpx.MockTransport`,
`DRY_RUN`, and recorded fixtures stand in. Tests that would call NIM are marked
`live` and deselected by default.

**Impact**: real hallucination patterns, refusal styles, structured-output
compliance, and retry behaviour under 429/5xx are validated manually, not on every
change. Cost accounting is validated against mock token counts.

**Mitigation**: the client's three structured-output tiers, repair round-trip, and
retry ladder are unit-tested against realistic response shapes; `pytest -m live`
runs the real path when a key is available.

---

## 17. Multi-worker operation has no orchestrator or fairness

**Limitation**: any number of `forensics worker start` processes can share the
queue safely (claims are atomic), but there is no autoscaling, no health
supervision, no dead-letter queue, and no per-tenant fairness — a tenant that
enqueues 10,000 jobs at priority 10 delays everyone else.

**Impact**: fine for one process and a demo; a burst needs manual capacity.

**Mitigation**: `QUEUE_WORKER_CONCURRENCY`, `QUEUE_JOB_TTL` and the `priority`
column are the levers available today, and `forensics queue list --status pending`
is the queue-depth view.

---

## 18. The PostgreSQL backend is typechecked but not exercised

**Limitation**: `storage_postgres.PostgresStorage` implements the full
`StorageBase` contract — traces, flags, diagnoses, eval data, tenants and API
keys — but no test in the suite ever connects to a PostgreSQL server. `asyncpg`
is an opt-in extra (`pip install -e ".[postgres]"`), so CI never installs it,
and the backend is covered by mypy strict and the import path only.

**Impact**: the first real deployment is the first real execution. Schema
drift between `migrations.py` (SQLite) and `_init_schema` (PostgreSQL) would
surface there rather than in CI.

**Mitigation**: the two backends share `StorageBase`, `AuthManager` no longer
reaches into SQLite internals, and the SQLite implementation is the tested
reference for every method. Adding a `pytest.mark.postgres` job against a
throwaway container is the next step; until then, treat `DATABASE_URL` as
unproven in production.

---

## Summary

| # | Limitation | Severity | Mitigation available today |
|---|---|---|---|
| 1 | Bundled UI cannot send an API key | Medium | Deploy with `AUTH_ENABLED=false`; use the API directly otherwise |
| 2 | ~~No reaper for jobs whose worker died~~ — **RESOLVED** | ~~Medium~~ | `QUEUE_STALL_TIMEOUT` + heartbeat + periodic reaper + CLI command |
| 3 | Scheduled eval is instance-wide | Low | Per-tenant runs via `POST /eval/run` |
| 4 | Auth off = all tenants visible | Medium | Deployment-wide switch; writes still stamp a tenant |
| 5 | In-flight job payload holds raw text | Medium | Cleared at terminal state; reference documents by id if unacceptable |
| 6 | `context_loss` fault needs entities | Low | Score against a real trace, not a dry-run one |
| 7 | DRY_RUN's canned output is a fixture, not model behaviour | Low | Reproduces all 15 labels; still needs a real key for accuracy |
| 7b | Re-diagnosing a persisted trace sees redacted spans | Low | Read the stored diagnosis, or run with redaction off |
| 8 | No pre-flight cost check | Low | Post-hoc caps bound repeated overshoot |
| 9 | Prompt templates not archived | Low | `prompt_version` + full prompt text per span |
| 10 | Prices default to 0.0 | Medium | Set real prices in `.env` |
| 11 | Single-writer SQLite | Low | WAL + `busy_timeout`; `db_path` is substitutable |
| 12 | Propagation check is a heuristic | Low | Evidence chain cites the upstream span |
| 13 | ~~Keys never expire, no scopes~~ — **RESOLVED** | ~~Medium~~ | `AUTH_KEY_TTL_DAYS` + `--ttl` CLI option + expiration check in `validate_api_key` |
| 14 | ~~No rate limiting~~ — **RESOLVED** | ~~Low~~ | Per-tenant token bucket in SQLite with `RATE_LIMIT_ENABLED`, `RATE_LIMIT_REQUESTS_PER_MINUTE`, `RATE_LIMIT_BURST` |
| 15 | Small dataset (15 documents) | Low | Schema is size-agnostic; fault injection scales failures |
| 16 | Live model calls not in CI | Medium | `pytest -m live`; mock-transport coverage of the ladders |
| 17 | No orchestrator or tenant fairness | Low | Atomic claims, concurrency/priority levers |

---

**Philosophy**: this is a *forensics* tool, not a monitoring tool. Its job is to
answer "which step broke and why?" after a failure, and to keep that answer
auditable: deterministic checks first, an optional judge second, and every claim
traceable to a span. The limits above are the price of a system that can be
explained end to end — not omissions.
