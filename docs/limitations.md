# Failure Forensics Tool — Known Limitations

This document enumerates what the tool does **not** do, as shipped. Every entry
is a scope decision or a known gap with its impact and mitigation stated. Nothing
here is presented as a bug.

Last verified against the working tree with `make check` (ruff, mypy strict, and
the test suite: 496 passing, 13 skipped, `-m "not live"`). The thirteen skipped are
the live-PostgreSQL tests, which run with `make test-postgres`; the always-on
`tests/test_schema_parity.py` still compares the two schemas without a server.
The count is stated as the current suite size rather than a commit-pinned one,
because the previous revision's "344 tests at `cb373e9`" drifted two layers behind
the code it described.

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

## 3. ~~The scheduled eval is tenant-agnostic~~ — **RESOLVED**

**Limitation**: the nightly run evaluated **every** tenant's eval cases in one
`EvalRun`, recorded under the `default` tenant, so a per-tenant accuracy history
did not exist and one tenant's cases moved another tenant's alerts.

**Resolution**: `EvalScheduler.run_scheduled_eval` now walks the active tenants
and runs one eval per tenant, scoped to that tenant's cases
(`run_eval(tenant_id=…)`). Each run is compared against *that tenant's* previous
completed run, and every alert carries the `tenant_id` it belongs to. A tenant
with no eval cases is skipped without creating an empty run; a database that has
never had a tenant row (auth never enabled) evaluates `default`, which is where
all of its data lives.

**Impact**: alert volume is per tenant rather than one instance-wide summary,
and a cron tick with N active tenants performs N evals. That is the point — the
number now means one tenant's accuracy.

## 4. With auth disabled, the API serves every tenant's data

**Limitation**: `AUTH_ENABLED=false` applies **no** tenant filter. That is
deliberate — the criterion is "works as before" — but it means switching auth off
re-exposes data that auth mode separates.

**Impact**: an operator who turns auth off on a database that already holds
several tenants' data can read all of it. Records are still *written* under a
concrete tenant (`default` in dev mode), so the separation is intact if auth is
turned back on.

**Knob**: `AUTH_OFF_MULTI_TENANT_POLICY` (`warn`|`refuse`, default `warn`). At
startup, with `AUTH_ENABLED=false`, the tool counts tenants via `AuthManager`;
when more than one exists it either warns (`warn`) or fails startup (`refuse`).
A single tenant starts cleanly either way, so tests and the public demo are
unaffected.

**Mitigation / audit**: treat `AUTH_ENABLED` as a deployment-wide switch, not a
per-user preference. Audit tenants on any database before switching auth off:

```bash
forensics auth list-tenants
```

The value is logged at startup through the resolved settings, and
`/scheduler/status` and `/jobs` are authenticated in both modes. `GET /runtime`
exposes `auth_enabled` and `tenant_isolation` (`"enforced"`|`"disabled"`).

---

## 5. An in-flight job's document text is on disk, unredacted

**Limitation**: `job_queue.payload` holds the document text the pipeline is about
to send to the model. PII redaction (spec 03) covers traces at the persistence
boundary; the queue is pipeline *input*, and redacting it would change what the
model sees.

**Impact**: while a job is pending or running, its raw document text sits in the
same SQLite file as the (redacted) traces. A finished job's payload is cleared to
`{}`, so the exposure window is the job's lifetime, not the retention window.

**Knob**: `QUEUE_PAYLOAD_MODE` (`inline`|`reference`, default `inline`).

* `inline` (default): `doc_text` is stored in the job payload, exactly as before,
  and cleared when the job reaches a terminal state.
* `reference`: `POST /pipeline/run` stores only `{doc_id, expected_type}` — no
  `doc_text`. The worker resolves the text via `read_document` at claim time, so
  the document must already be in the dataset or persisted as an upload. The
  endpoint fails synchronously at enqueue time when the document cannot be
  resolved, so the caller learns immediately instead of when a worker claims the
  job.

**Mitigation**: payloads are cleared on `completed`/`failed` and
`QUEUE_JOB_TTL` (default 24h) only ever deletes terminal rows. A deployment that
cannot accept even a short-lived plaintext window should use `reference` mode (or
reference documents by id) instead of inlining `doc_text`. Neither mode is secure
erasure: SQLite retains WAL and freelist pages, so a determined reader could
recover plaintext from the file.

---

## 6. ~~The fault scorecard's `context_loss` fault needs a trace with entities~~ — **RESOLVED**

**Limitation**: `_inject_context_loss` summarizes without the grounded entities.
If the baseline trace extracted **no** entities, a summary that omits them is
indistinguishable from one that never had them, so `context_preservation` has
nothing to compare. The scorecard counted that as a *miss*, which turned a
property of the baseline into a fabricated detector failure and under-reported
accuracy on traces produced by `DRY_RUN=true`, whose canned extraction returns no
entities for every document but one.

The scorecard also picked its baseline as *whatever trace the store returned
first*, so the reported figure moved between 5/7 and 6/7 (71.4%–85.7%) with no
code change — it was measuring the baseline, not the detector.

**Resolution**: two independent changes.

- `Fault.scoreable` declares each fault's precondition.
  `context_loss` is scoreable only when the baseline extracted a *salient,
  grounded* entity, and `score_fault` reports `scoreable: False` with an
  explicit reason instead of a miss. `score_all_faults` and the CLI aggregate
  exclude unscoreable results from the accuracy denominators and print `n/a`
  for a fault no baseline could host, plus a count of what was skipped.
- `benchmark-analyzer` scores **every** eligible clean, non-mixed trace and
  aggregates per fault, showing the sample size per row, so one entity-free
  baseline no longer defines the whole number and the result does not depend on
  insertion order. Two consecutive runs now print identical numbers.

**Impact**: on the offline fixture the scorecard reports **100% step and
category accuracy over 150 injected faults** across six faults, with
`context_loss` marked unscoreable (25 skipped) rather than "0%". `context_loss`
is still exercised, on synthetic traces that do have entities, by
`tests/test_storage_eval_api.py`.

**Mitigation**: score against a real (non-dry-run) baseline trace, which has
entities to lose; in that configuration `context_loss` becomes scoreable and is
counted. The injector's own no-op class of bug — choosing a label equal to the
correct one — was fixed earlier and is regression-tested.

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

## 7b. Re-diagnosing a persisted trace under PII redaction — **MITIGATED**

**Limitation**: the write path analyzes the *unredacted* trace and redacts on
disk. `POST /traces/{trace_id}/diagnose` instead loads the persisted trace, which
is redacted by design, so with `PII_REDACTION_ENABLED=true` an extracted email,
phone, or IP is `█` in the span while `doc_text` is not — and `entity_grounding`
flags the correctly extracted entity as a hallucination.

**Resolution**: the endpoint now follows a 409/override contract:

* If redaction is on **and** a stored diagnosis exists → return the stored
  diagnosis (200) with `X-Diagnosis-Source: stored`. The write-time verdict is
  trustworthy because it was produced against the unredacted trace.
* If redaction is on, no stored diagnosis exists, and the caller does not opt in
  → `409` naming the cause and the two ways forward (confirm the stored
  diagnosis, or re-run with `?allow_redacted=true`).
* `?allow_redacted=true` (write scope) forces a fresh analysis, sets
  `X-Diagnosis-Source: redacted-recompute`, and populates `Diagnosis.degraded_reason`
  so the UI can show a banner ("diagnosis computed on redacted spans").
* Redaction off → unchanged fresh analysis.

The alternative — persisting the unredacted trace — would reopen the leak spec 03
closes, so it was rejected.

**Impact**: re-diagnosis never fabricates a hallucination under redaction.

---

## 8. ~~No pre-flight cost check~~ — **RESOLVED**

**Limitation**: spend caps were enforced *after* the response, using actual
token counts. Nothing rejected a request that was likely to exceed a cap before
sending it, so one request could overshoot the per-run cap and the global cap
could be overshot by one request.

**Resolution**: `SpendTracker.check_preflight` and
`NIMClient.estimate_call_cost` refuse a call *before* it is sent when its
worst-case cost would breach either cap. The estimate is deliberately an upper
bound — the measured prompt at ~4 characters per token, plus the full
`max_tokens` output budget at the configured prices — and it is skipped entirely
when the estimate is zero (the shipped zero prices). `SPEND_PREFLIGHT_ENABLED=false`
restores the old post-hoc-only behaviour.

**Impact**: a call that would in fact have fit under the cap can be refused when
the *bound* does not fit. That is the intended trade: a false refusal costs
nothing, while an overshoot is already billed. The heuristic chars-per-token
constant is a fixed 4; a very terse or non-Latin prompt could be under-counted,
so the post-hoc check remains the backstop.

## 9. Prompt templates are not stored with traces — **MITIGATED**

**Limitation**: `prompt_version` records a version identifier, and the span holds
the full rendered prompt, but the *template* that produced it is not archived.

**Resolution**: the five prompt builders were extracted from `llm.py` into
`src/forensics/prompts.py` without changing a character of prompt text
(byte-equality pinned by test). Each `Span` now records `prompt_template` (the
template name, e.g. `extraction-v1`), and `save_trace` upserts one
`PromptTemplate` row per (name, content-hash version) in both SQLite and
PostgreSQL. `GET /prompt-templates` and `GET /prompt-templates/{name}/{version}`
expose the archive, plus `forensics prompts list` / `forensics prompts show`.

**Impact**: a trace can now be replayed against the exact template revision that
produced it, not just the code in the tree.

**Mitigation**: `prompt_version` remains on every `VersionedModel`; the template
text itself is in the span and now also in the `prompt_templates` table.

---

## 10. Prices default to zero

**Limitation**: `.env.example` ships `*_PRICE_IN`/`*_PRICE_OUT` as `0.0` with a
`VERIFY BEFORE USE` banner, because hosted prices change and inventing them is
worse than leaving them blank.

**Impact**: `total_cost_usd`, the per-run/global caps, and the per-step spend
attribution (`judge` included) all compute correctly but read zero until real
prices are set.

**Mitigation**: set real per-1M prices in `.env`. The accounting code is
price-agnostic; only the values need filling in. The dashboard's cost panels go
through `tracing.span_cost_usd` — the same helper that writes
`trace.total_cost_usd` — so they read `$0.0000` until prices are configured
rather than falling back to an invented per-token table.

---

## 11. The propagation check is a heuristic, not a dataflow analysis — **MITIGATED (cascade suppression)**

**Limitation**: `propagation` detects the common case — terminology that appears
in one step's output and goes missing downstream. It does not build a dependency
graph of the pipeline's data flow.

**Resolution**: two changes reduce false cascade blame:

* `find_root_cause` still returns the *earliest* failing step as root cause, and
  now lists any later `propagation` failures as evidence "downstream of <root
  step>", so a cascade is named without blaming the wrong step.
* `check_propagation` skips (reports pass with an evidence note) when the
  upstream classification output carries no salient definitional terminology,
  mirroring how `context_loss` is unscoreable without salient entities. This
  prevents a false propagation failure when there is nothing to propagate.

**Impact**: a cascading failure is attributed to its earliest failing step, with
later propagation failures recorded as downstream evidence rather than
independent root causes.

**Mitigation**: the evidence chain cites the upstream span, so a human can follow
the cascade. The check remains cheap, deterministic, and explainable — still not
a dataflow graph.

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

## 13. ~~API keys never expire / have no scopes~~ — **RESOLVED**

**Limitation**: keys were bcrypt-hashed, prefixed for identification, and
revocable — but with no expiry and no scopes, so a leaked key was valid forever
and a dashboard key had the same power as a CI key that starts runs.

**Resolution**: two independent bounds.

- **Expiry**: `AUTH_KEY_TTL_DAYS` sets a default lifetime and
  `forensics auth create-key --ttl N` overrides it per key. An expired key fails
  validation and is indistinguishable from a revoked one (both 401).
- **Scopes**: `api_keys.scopes` (default `*`, added by migration with the same
  default, so existing keys keep their access) holds `read`, `write`, or `*`.
  `GET` routes require `read`; mutating routes require `write`; `write` implies
  `read`, because a key that can start a run but cannot fetch the result is a
  trap. `forensics auth create-key --scope read` mints a read-only key and
  `list-keys` shows the scopes.

**Impact**: authorization is per key, not per tenant: there are no per-resource
scopes (a `write` key can still do anything that tenant can), and an unknown
scope name is rejected at mint time rather than silently ignored. The scope
check defaults to the *narrow* scope, so a new route that forgets to declare
`scope="write"` fails closed for a read-only key.



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

## 16. Live-model behaviour is not exercised in CI — **MITIGATED (weekly smoke)**

**Limitation**: the automated suite never calls a model on `push` or
`pull_request`. `httpx.MockTransport`, `DRY_RUN`, and recorded fixtures stand in.
Tests that would call NIM are marked `live` and deselected by default.

**Resolution**: a weekly scheduled CI job (`schedule: cron "0 6 * * 1"`, Monday
06:00 UTC) plus `workflow_dispatch` runs one document (`invoice_001`) against the
real model with bounded spend (`SPEND_CAP_PER_RUN_USD=0.05`,
`SPEND_CAP_GLOBAL_USD=0.20`), skips-with-success on quota `429`, uploads the
trace JSON as a workflow artifact, and writes a job summary (model, tokens, cost,
root cause). PRs never spend quota, and a live run is not required to merge.

**Impact**: real hallucination patterns, refusal styles, structured-output
compliance, and retry behaviour under 429/5xx now get recurring evidence instead
of only manual validation.

**Mitigation**: the client's three structured-output tiers, repair round-trip, and
retry ladder are unit-tested against realistic response shapes; `pytest -m live`
runs the real path when a key is available.

---

## 17. Multi-worker operation has no orchestrator or fairness — **MITIGATED (coarse cap + dead-letter)**

**Limitation**: any number of `forensics worker start` processes can share the
queue safely (claims are atomic), but there is no autoscaling, no health
supervision, and no dead-letter queue — a poison job that crashes every attempt
is retried up to `QUEUE_MAX_ATTEMPTS` and then left `failed`.

**Resolution**: two opt-in knobs (ADR 0017):

* `QUEUE_MAX_INFLIGHT_PER_TENANT` (default `0` = unlimited): `Queue.dequeue`
  excludes tenants whose `pending`+`running` count is `>= cap` from the atomic
  claim, so one tenant cannot starve the queue. No broker, no weighted fair
  queueing.
* `QUEUE_DEAD_LETTER_ENABLED` (default `true`): `Queue.fail` marks a job
  `dead` instead of `failed` once it exhausts `QUEUE_MAX_ATTEMPTS`, so a poison
  job stops retrying forever. `Queue.stats()` reports counts by status, oldest
  pending age, and per-tenant in-flight; exposed via `GET /queue/stats`, `forensics
  queue stats`, and `forensics queue requeue --dead`.

**Impact**: starvation is test-proven prevented, poison jobs terminate, and the
queue has an operator-facing stats view. Still no autoscaling, health supervision,
or broker (§17 remains "no orchestrator" in that narrower sense).

**Mitigation**: `QUEUE_WORKER_CONCURRENCY`, `QUEUE_JOB_TTL` and the `priority`
column remain the other levers, and `forensics queue list --status pending` is
the queue-depth view.

---

## 18. ~~The PostgreSQL backend is typechecked but not exercised~~ — **RESOLVED**

**Limitation**: `storage_postgres` implemented the `StorageBase` contract but no
test ever connected to a server, so three defects were invisible: the schema was
never created (the first query failed on a fresh database), the trace index was
never written (`save_trace` only called `finalize_trace`, which writes a *SQLite*
index), and every synchronous wrapper used `asyncio.run()`, whose per-call event
loop left the `asyncpg` pool bound to a loop that had already closed — so the
*second* operation failed.

**Resolution**: `tests/test_storage_postgres.py` runs the backend against a real
server (`make test-postgres`, `FORENSICS_TEST_POSTGRES_URL`), covering trace
save/load/list/get with tenant scoping, span replacement on re-save, flags,
diagnoses and confirmation, eval cases/runs/results, tenant and API-key
operations including scopes and revocation, backend selection through
`DATABASE_URL`, repeated operations on one event loop, and shutdown (the pool
and its loop thread are closed, so rebuilding the backend does not leak threads). Writing those tests is
what found the three defects above; all are fixed.

`tests/test_schema_parity.py` compares the SQLite and PostgreSQL schemas table by
table and column by column on every run, so drift is caught even where no
PostgreSQL server exists (CI).

**Impact**: the backend is no longer unproven. `tests/test_postgres_ops.py` adds
operational coverage against a live server: connection-pool sizing under load
(`PG_POOL_MIN`/`PG_POOL_MAX`), recovery from a `pg_terminate_backend` kill,
idempotent `close()` and rebuild-after-close, and that a long-running
transaction does not permanently wedge the pool. What remains unverified is
failover and behaviour during a `pg_ctl` restart of a managed instance — those
are out of scope for a single-service demo.

## Summary

| # | Limitation | Severity | Mitigation available today |
|---|---|---|---|
| 1 | ~~Bundled UI cannot send an API key~~ — **RESOLVED** | ~~Medium~~ | Key entry in the header; `Authorization: Bearer` on every request |
| 2 | ~~No reaper for jobs whose worker died~~ — **RESOLVED** | ~~Medium~~ | `QUEUE_STALL_TIMEOUT` + heartbeat + periodic reaper + CLI command |
| 3 | ~~Scheduled eval is instance-wide~~ — **RESOLVED** | ~~Low~~ | One run per active tenant, compared against that tenant's own history |
| 4 | Auth off = all tenants visible | Medium | `AUTH_OFF_MULTI_TENANT_POLICY` warn/refuse; deployment-wide switch; writes still stamp a tenant |
| 5 | In-flight job payload holds raw text | Medium | `QUEUE_PAYLOAD_MODE=reference` stores only `{doc_id, expected_type}`; fails at enqueue if unresolved; cleared at terminal state |
| 6 | ~~`context_loss` fault needs entities~~ — **RESOLVED** | ~~Low~~ | Unscoreable faults are reported as `n/a`, not as misses; score against a real trace to exercise it |
| 7 | DRY_RUN's canned output is a fixture, not model behaviour | Low | Reproduces all 15 labels; still needs a real key for accuracy |
| 7b | Re-diagnosing a persisted trace sees redacted spans | Low | 409/override contract: stored diagnosis returned with `X-Diagnosis-Source: stored`; `?allow_redacted=true` sets `degraded_reason` |
| 8 | ~~No pre-flight cost check~~ — **RESOLVED** | ~~Low~~ | Worst-case estimate refused before the call; `SPEND_PREFLIGHT_ENABLED` to disable |
| 9 | Prompt templates not archived | Low | Templates archived in `prompt_templates` (one row per name/version); `Span.prompt_template` recorded; `GET /prompt-templates`, `forensics prompts list/show` |
| 10 | Prices default to 0.0 | Medium | `prices_configured` announced at startup, `/runtime`, and `validate-config --strict-prices`; set real prices in `.env` |
| 11 | Single-writer SQLite | Low | WAL + `busy_timeout`; `db_path` is substitutable |
| 12 | Propagation check is a heuristic | Low | Cascade suppression: earliest failing step is root cause, later `propagation` failures listed as downstream evidence; skipped (pass-with-note) when upstream has no salient terminology |
| 13 | ~~Keys never expire, no scopes~~ — **RESOLVED** | ~~Medium~~ | `--ttl` + `AUTH_KEY_TTL_DAYS` for expiry; `--scope read` for a read-only key, enforced per route |
| 14 | ~~No rate limiting~~ — **RESOLVED** | ~~Low~~ | Per-tenant token bucket in SQLite with `RATE_LIMIT_ENABLED`, `RATE_LIMIT_REQUESTS_PER_MINUTE`, `RATE_LIMIT_BURST` |
| 15 | Small demo dataset (15 documents) | Low | `generate-dataset` is seeded and reproducible; `dataset_large/` ships 150 documents / 43 seeded failures |
| 16 | Live model calls not in CI | Medium | Weekly scheduled live smoke job (`cron "0 6 * * 1"` + `workflow_dispatch`), one document, spend caps, artifact + job summary |
| 17 | No orchestrator or tenant fairness | Low | `QUEUE_MAX_INFLIGHT_PER_TENANT` coarse per-tenant cap; `QUEUE_DEAD_LETTER_ENABLED` dead-letter; `queue stats` + `GET /queue/stats` |
| 18 | ~~PostgreSQL backend unexercised~~ — **RESOLVED** | ~~High~~ | 12 live-server tests + schema-parity test; `test_postgres_ops.py` covers pool sizing, connection kill, close idempotency, long-running transaction |

---

**Philosophy**: this is a *forensics* tool, not a monitoring tool. Its job is to
answer "which step broke and why?" after a failure, and to keep that answer
auditable: deterministic checks first, an optional judge second, and every claim
traceable to a span. The limits above are the price of a system that can be
explained end to end — not omissions.
