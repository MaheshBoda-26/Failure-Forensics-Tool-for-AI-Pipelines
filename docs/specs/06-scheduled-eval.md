# SPEC: Scheduled Eval / Alerting

## Summary
Run eval on a cron schedule; alert on regression via webhook. Uses APScheduler with SQLite job store (zero Redis).

## Config (`.env`)
```bash
EVAL_SCHEDULE="0 2 * * *"           # cron: daily 2 AM UTC (default: none = disabled)
EVAL_SCHEDULE_TIMEZONE=UTC
SCHEDULER_DB_PATH=data/scheduler.db # SQLite job store (survives restarts)
ALERT_WEBHOOK_URL=                  # POST target for alerts
ALERT_WEBHOOK_SECRET=               # HMAC secret for signature verification
ALERT_REGRESSION_THRESHOLD=0.10     # 10% accuracy drop triggers alert
ALERT_ON_FIXED=true                 # alert when previously failing now passes
```

## New Module: `src/forensics/scheduler.py`
```python
"""Scheduled eval runner with APScheduler + SQLite job store."""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
import hmac
import hashlib
import json
import httpx
from forensics.storage import Storage
from forensics.eval_simple import run_eval
from forensics.config import get_settings


class EvalScheduler:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.settings = get_settings()
        self.scheduler = BackgroundScheduler(
            jobstores={"default": SQLAlchemyJobStore(url="sqlite:///data/scheduler.db")},
            timezone=self.settings.eval_schedule_timezone,
        )

    def start(self) -> None:
        if not self.settings.eval_schedule:
            return
        self.scheduler.add_job(
            self._run_scheduled_eval,
            CronTrigger.from_crontab(self.settings.eval_schedule),
            id="scheduled_eval",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()

    def stop(self) -> None:
        self.scheduler.shutdown(wait=False)

    async def _run_scheduled_eval(self) -> None:
        try:
            # Get previous accuracy
            prev_run = self.storage.list_eval_runs(limit=1)
            prev_accuracy = (
                prev_run[0].passed_count / prev_run[0].total_cases
                if prev_run and prev_run[0].total_cases > 0
                else None
            )

            # Run eval
            run = await run_eval()
            if not run:
                return

            current_accuracy = run.passed_count / run.total_cases if run.total_cases > 0 else 0

            # Check regression
            if prev_accuracy is not None:
                regression = prev_accuracy - current_accuracy
                if regression >= self.settings.alert_regression_threshold:
                    await self._send_alert(
                        "regression",
                        {
                            "run_id": run.run_id,
                            "previous_accuracy": prev_accuracy,
                            "current_accuracy": current_accuracy,
                            "regression_pct": regression,
                            "failed_cases": run.failed_count,
                        },
                    )

            # Check fixed (previously failing now passing)
            if self.settings.alert_on_fixed and run.passed_count > 0:
                # Could compare case-by-case; simplified for now
                await self._send_alert(
                    "improvement",
                    {
                        "run_id": run.run_id,
                        "current_accuracy": current_accuracy,
                        "passed_count": run.passed_count,
                    },
                )

        except Exception as e:
            # Log error, don't crash scheduler
            import logging

            logging.getLogger(__name__).error(f"Scheduled eval failed: {e}")

    async def _send_alert(self, alert_type: str, payload: dict) -> None:
        if not self.settings.alert_webhook_url:
            return
        body = json.dumps(
            {
                "alert_type": f"eval_{alert_type}",
                "timestamp": datetime.now(UTC).isoformat(),
                "service": "failure-forensics",
                **payload,
            }
        )
        headers = {"Content-Type": "application/json"}
        if self.settings.alert_webhook_secret:
            sig = hmac.new(
                self.settings.alert_webhook_secret.encode(),
                body.encode(),
                hashlib.sha256,
            ).hexdigest()
            headers["X-Signature"] = f"sha256={sig}"

        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(self.settings.alert_webhook_url, content=body, headers=headers)


# Global instance
_scheduler: EvalScheduler | None = None


def get_scheduler(storage: Storage | None = None) -> EvalScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = EvalScheduler(storage or Storage())
    return _scheduler
```

## Integration

### `src/forensics/api.py`
```python
from forensics.scheduler import get_scheduler


@app.on_event("startup")
async def startup_scheduler():
    if settings.eval_schedule:
        get_scheduler().start()


@app.on_event("shutdown")
async def shutdown_scheduler():
    get_scheduler().stop()


@app.get("/scheduler/status")
def scheduler_status():
    sched = get_scheduler()
    jobs = sched.scheduler.get_jobs()
    return {
        "running": sched.scheduler.running,
        "next_eval": jobs[0].next_run_time.isoformat() if jobs else None,
        "schedule": settings.eval_schedule,
    }
```

### `src/forensics/cli.py`
```bash
forensics scheduler-start    # foreground (systemd / container PID 1)
forensics scheduler-status   # schedule, next run, recent runs
```

### `src/forensics/api.py`
`GET /scheduler/status` reports the same thing without the job-store path.

## Implementation notes

The sketch above is a design sketch; the shipped code differs in four places,
each for a concrete reason.

1. **The job function is module-level, not a bound method.** APScheduler pickles
   jobs into the SQLite store, and pickling `self._run_scheduled_eval` would drag
   the whole `EvalScheduler` — settings, HTTP transport, live connection — into
   the blob. `_scheduled_eval_job` resolves the process-wide scheduler at call time.
2. **The coroutine needs an explicit event loop.** APScheduler runs sync callables
   in a worker thread, so `run_now()` wraps `run_scheduled_eval()` in
   `asyncio.run`. Handing APScheduler an `async def` job returns an un-awaited
   coroutine and the eval silently never runs.
3. **`ALERT_ON_FIXED` compares case by case.** `passed_count > 0` is true after
   every healthy run, which would alert on every run. The shipped version diffs
   the previous run's failing case ids against the current run's passing ones.
4. **`regression_pct` is a percentage.** The accuracies beside it are fractions,
   so `0.90 → 0.70` reports `regression_pct: 20.0`. The comparison is rounded
   before testing the threshold: `1.0 - 0.9` is `0.09999999999999998` in binary
   floating point, so an exactly-10% drop would otherwise miss a 10% threshold.

Also: the spec's `datetime`/`UTC` names were missing its import, and the startup
hooks are wired into the existing FastAPI `lifespan` rather than the deprecated
`@app.on_event`, so plugin loading and scheduler start-up have one owner.
`scheduler_status` (the FastAPI route) is behind the same API-key check as every
other data route.

## Acceptance Criteria
1. `EVAL_SCHEDULE=""`: scheduler not started (and `stop()` on shutdown is safe)
2. `EVAL_SCHEDULE="0 2 * * *"`: runs eval daily at 2 AM UTC
3. On regression ≥ `ALERT_REGRESSION_THRESHOLD`: POST to webhook with
   `alert_type: eval_regression`
4. Webhook payload includes `run_id`, `previous_accuracy`, `current_accuracy`,
   `regression_pct`, `failed_cases`; the body is signed
   `X-Signature: sha256=<hmac>` when `ALERT_WEBHOOK_SECRET` is set
5. `ALERT_ON_FIXED=true`: a case that failed in the previous run and passes now
   produces an `eval_improvement` alert listing `fixed_cases`
6. `forensics scheduler-start` runs in the foreground (for systemd / a container)
7. `forensics scheduler-status` shows the schedule and the next run time
8. Scheduler survives a process restart (SQLite job store)

## Effort
- **Files**: 1 new (`scheduler.py`), 3 modified (`config.py`, `api.py`, `cli.py`)
- **Deps**: `apscheduler>=3.10`, `sqlalchemy>=2.0` (required by
  `SQLAlchemyJobStore`), `httpx` (already present)
- **Time**: ~3 hours
- **Risk**: Medium (background scheduler, async in sync context)
