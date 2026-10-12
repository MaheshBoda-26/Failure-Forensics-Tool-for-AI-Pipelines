# SPEC: Scheduled Eval / Alerting

## Summary
Run eval on a cron schedule; alert on regression via webhook. Uses APScheduler with SQLite job store (zero Redis).

## Config (`.env`)
```bash
EVAL_SCHEDULE="0 2 * * *"           # cron: daily 2 AM UTC (default: none = disabled)
EVAL_SCHEDULE_TIMEZONE=UTC
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
```python
@app.command()
def scheduler_start():
    """Start the eval scheduler (runs in foreground)."""
    from forensics.scheduler import get_scheduler
    from forensics.storage import Storage
    import signal

    sched = get_scheduler(Storage())
    sched.start()
    print(
        f"Scheduler started. Next eval: {sched.scheduler.get_job('scheduled_eval').next_run_time}"
    )
    signal.pause()


@app.command()
def scheduler_status():
    from forensics.scheduler import get_scheduler
    from forensics.storage import Storage

    sched = get_scheduler(Storage())
    jobs = sched.scheduler.get_jobs()
    for job in jobs:
        print(f"  {job.id}: next={job.next_run_time}")
```

## Acceptance Criteria
1. `EVAL_SCHEDULE=""`: scheduler not started
2. `EVAL_SCHEDULE="0 2 * * *"`: runs eval daily at 2 AM UTC
3. On regression ≥10%: POST to webhook with `alert_type: eval_regression`
3. Webhook payload includes run_id, accuracies, regression_pct, failed_cases
4. HMAC signature verified with `ALERT_WEBHOOK_SECRET`
5. `forensics scheduler_start` runs foreground (for systemd/container)
6. `forensics scheduler_status` shows next run time
7. Scheduler survives process restart (SQLite job store)

## Effort
- **Files**: 1 new (`scheduler.py`), 2 modified
- **Deps**: `apscheduler>=3.10`, `httpx>=0.27` (add to `pyproject.toml`)
- **Time**: ~3 hours
- **Risk**: Medium (background scheduler, async in sync context)
