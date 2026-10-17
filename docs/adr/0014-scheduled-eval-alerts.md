# ADR 0014: Scheduled Eval with Webhook Regression Alerts

## Status
Accepted

## Context
Eval runs were manual. A regression — a case that used to pass and now fails —
was only noticed when somebody ran `forensics eval` and read the table. The tool
measures accuracy over a growing suite; measuring it only on demand makes the
measurement worthless for catching drift.

The constraint is the deployment: one small web service on a free/starter tier,
with no Redis, no broker, and no separate worker host.

## Decision
- **APScheduler with a SQLite job store** (`SQLAlchemyJobStore`), in the web
  process, started from the FastAPI lifespan. No external scheduler, no broker.
- **The job function is module-level** (`_scheduled_eval_job`, not a bound
  method), because APScheduler pickles jobs into the store.
- **`asyncio.run` in the sync job wrapper.** APScheduler runs sync callables in a
  worker thread; handing it an `async def` returns an un-awaited coroutine and the
  eval silently never runs.
- **Decision logic is separated from delivery.** `evaluate_run` returns alert
  dicts; `send_alert` posts them. Thresholds are the part worth testing
  exhaustively, and they should not need a scheduler, a thread, or a webhook.
- **Alerts are HMAC-SHA256 signed** (`X-Signature: sha256=…`) when
  `ALERT_WEBHOOK_SECRET` is set, over the exact bytes posted.
- **`ALERT_ON_FIXED` compares case by case** — the previous run's failing case ids
  against the current run's passing ones — instead of alerting whenever
  `passed_count > 0`.
- **`regression_pct` is a percentage**, matching its name; the accuracies beside
  it stay fractions. The comparison is rounded to 10 decimal places before
  testing the threshold.

## Rationale
- A SQLite job store is the only way to satisfy "survives a process restart"
  without a broker, and `SQLAlchemyJobStore` is APScheduler's supported
  implementation of it — hence SQLAlchemy as a required dependency.
- `1.0 - 0.9` is `0.09999999999999998` in binary floating point, so an exactly
  10% drop missed a 10% threshold. Rounding is the fix; accuracies are ratios of
  integer counts, so a real difference is at least 1e-6.
- A missed run should be collapsed, not replayed in a burst after downtime:
  `coalesce=True`, `max_instances=1`.
- `scheduler-status` computes the next fire time from the cron trigger when the
  scheduler is not running in that process, so an operator can inspect the
  schedule without starting a background thread that might immediately catch up.

## Consequences
- SQLAlchemy and APScheduler are required, not optional. `make install` installs
  them; the package imports without the OTel exporter, but not without these.
- The eval runs inside the web process, so the instance must stay up for the job
  to fire. Render's free tier spins down when idle, which would silently stop
  nightly evals.
- The scheduled run is instance-wide, not per-tenant (`docs/limitations.md` §3).
- An alert whose webhook is down is logged and lost; the run itself is already
  persisted, so only the notification is missed.

## Verification
- `tests/test_scheduler.py` — 29 tests over all eight spec-06 criteria, including
  the exactly-at-threshold boundary, the payload's exact keys (passed through
  `httpx.MockTransport`, so the real request-building code runs), HMAC
  verification, per-case improvement detection, restart survival against a fresh
  `EvalScheduler` on the same database, and a SIGTERM/SIGINT path.
- Live check: `/scheduler/status` on a server started with `EVAL_SCHEDULE=*/5 * * * *`
  reports `running: true` with a next run time, and `apscheduler_jobs` holds the
  row.
