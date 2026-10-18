"""Scheduled eval with regression alerting (spec 06).

APScheduler runs the eval against a cron schedule; a regression (or a case that
started passing) is reported to an HTTP webhook, signed with HMAC-SHA256. State
lives in SQLite — no Redis, no broker — so a restart resumes the same schedule.

Design notes:

* The job function is module-level (`_scheduled_eval_job`, not a bound method).
  APScheduler serialises jobs into the SQLite store with pickle; a bound method
  would drag the whole `EvalScheduler` — settings, HTTP transport, a live
  database connection — into the pickled blob.
* APScheduler runs sync callables in a worker thread, so the coroutine has to be
  driven by an explicit `asyncio.run`. Handing APScheduler an `async def` job
  directly returns an un-awaited coroutine and the eval silently never runs.
* The alert decision is split from the alert delivery (`evaluate_run` returns a
  list of alerts; `send_alert` posts them), so the threshold logic can be tested
  without a scheduler, a thread, or a webhook.
"""

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from forensics.config import get_settings
from forensics.eval_simple import run_eval
from forensics.models import EvalRun
from forensics.storage import Storage

logger = logging.getLogger(__name__)

#: Job id in the store. Stable, so `replace_existing=True` updates rather than
#: accumulating a new job on every process start.
JOB_ID = "scheduled_eval"


def _scheduled_eval_job() -> None:
    """APScheduler entry point. Module-level so the job can be pickled."""
    get_scheduler().run_now()


def _accuracy(run: EvalRun) -> float | None:
    """Passed fraction, or None for a run with no cases (never divide by zero)."""
    if run.total_cases <= 0:
        return None
    return run.passed_count / run.total_cases


class EvalScheduler:
    """Cron-driven eval runner with a SQLite-backed job store."""

    def __init__(
        self,
        storage: Storage,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.storage = storage
        self.settings = get_settings()
        # `transport` is the seam that lets tests exercise the real httpx code
        # path (headers, signature, body) against `MockTransport` instead of a
        # live webhook.
        self.transport = transport

        db_path = self.settings.scheduler_db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.scheduler = BackgroundScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")},
            timezone=self.settings.eval_schedule_timezone,
        )

    # --- lifecycle ---

    def start(self) -> None:
        """Register the cron job and start the scheduler.

        A no-op when `EVAL_SCHEDULE` is empty (criterion 1). Jobs added while the
        scheduler is stopped are queued by APScheduler and submitted on start,
        which is what makes the persistent store's contents survive a restart.
        """
        if not self.settings.eval_schedule:
            return
        if self.scheduler.running:
            return

        self.scheduler.add_job(
            _scheduled_eval_job,
            CronTrigger.from_crontab(
                self.settings.eval_schedule, timezone=self.settings.eval_schedule_timezone
            ),
            id=JOB_ID,
            replace_existing=True,
            # One eval at a time, and a missed run is collapsed into the next
            # one rather than fired late in a burst after downtime.
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()
        logger.info(
            "Scheduled eval started: cron=%r timezone=%s next=%s",
            self.settings.eval_schedule,
            self.settings.eval_schedule_timezone,
            self.next_run_time(),
        )

    def stop(self) -> None:
        """Stop the scheduler if it is running.

        Guarded because `shutdown()` raises `SchedulerNotRunningError` when the
        scheduler never started — and the API calls this on every shutdown,
        including deployments with no schedule configured.
        """
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def next_run_time(self) -> datetime | None:
        """When the next eval fires, or None when nothing is scheduled.

        Computed from the cron trigger when the scheduler is not running in this
        process, so `scheduler-status` can report the schedule without starting a
        background thread (and without risking an immediate catch-up run).
        """
        if self.scheduler.running:
            job = self.scheduler.get_job(JOB_ID)
            if job is not None:
                # Annotated because APScheduler's objects are untyped; see the
                # mypy override in pyproject.toml.
                scheduled: datetime | None = job.next_run_time
                if scheduled is not None:
                    return scheduled

        if not self.settings.eval_schedule:
            return None
        trigger = CronTrigger.from_crontab(
            self.settings.eval_schedule, timezone=self.settings.eval_schedule_timezone
        )
        computed: datetime | None = trigger.get_next_fire_time(None, datetime.now(UTC))
        return computed

    def describe(self) -> dict[str, Any]:
        """Machine-readable status for the CLI and the `/scheduler/status` route."""
        next_run = self.next_run_time()
        return {
            "running": self.scheduler.running,
            "schedule": self.settings.eval_schedule,
            "timezone": self.settings.eval_schedule_timezone,
            "next_eval": next_run.isoformat() if next_run else None,
            "job_store": f"sqlite:///{self.settings.scheduler_db_path}",
            "alert_webhook_configured": bool(self.settings.alert_webhook_url),
            "alert_regression_threshold": self.settings.alert_regression_threshold,
            "alert_on_fixed": self.settings.alert_on_fixed,
        }

    # --- running an eval ---

    def run_now(self) -> None:
        """Run the scheduled eval synchronously. APScheduler calls this in a thread."""
        try:
            asyncio.run(self.run_scheduled_eval())
        except Exception:
            # A failing eval must never kill the scheduler thread: the next tick
            # has to be able to succeed.
            logger.exception("Scheduled eval failed")

    async def run_scheduled_eval(self) -> list[dict[str, Any]] | None:
        """Run the eval, compare against the previous run, and alert.

        Returns the alerts that were raised (empty when nothing was notable), so
        that both the caller and the tests can see what was decided. Returns None
        when there was nothing to evaluate at all.
        """
        previous = self._latest_completed_run()
        previous_accuracy = _accuracy(previous) if previous is not None else None

        alerts: list[dict[str, Any]] = []
        for tenant in self.storage.get_auth().list_tenants():
            if not tenant.active:
                continue
            run = await run_eval(tenant_id=tenant.tenant_id)
            if run is not None:
                current_accuracy = _accuracy(run)
                previous_accuracy = _accuracy(previous) if previous is not None else None
                alerts.extend(
                    self.evaluate_run(
                        previous=previous,
                        previous_accuracy=previous_accuracy,
                        run=run,
                        current_accuracy=current_accuracy,
                    )
                )
                previous = run
        if not alerts:
            pass
        await self._deliver_alerts(alerts)
        return alerts if alerts else None

    async def _deliver_alerts(self, alerts: list[dict[str, Any]]) -> None:
        """Send each alert independently.

        A regression alert and an improvement alert from the same run are
        separate notifications, so one unreachable webhook must not swallow the
        rest. Failures are logged per alert and the caller's `run_now` guard
        remains the backstop.
        """
        for alert in alerts:
            try:
                await self.send_alert(alert["alert_type"], alert)
            except Exception:
                logger.exception("Alert %s could not be delivered", alert["alert_type"])

    def _latest_completed_run(self) -> EvalRun | None:
        """Most recent eval run that actually evaluated something."""
        for run in self.storage.list_eval_runs(limit=10):
            if run.total_cases > 0:
                return run
        return None

    def evaluate_run(
        self,
        *,
        previous: EvalRun | None,
        previous_accuracy: float | None,
        run: EvalRun,
        current_accuracy: float | None,
    ) -> list[dict[str, Any]]:
        """Decide which alerts a finished run warrants.

        Pure decision logic, deliberately free of I/O: thresholds are the one
        thing here worth testing exhaustively, and they should not require a
        scheduler, a thread, or a webhook to exercise.
        """
        alerts: list[dict[str, Any]] = []

        # Criterion 3: a drop of at least `ALERT_REGRESSION_THRESHOLD` alerts.
        if previous_accuracy is not None and current_accuracy is not None:
            # Rounded before comparing: `1.0 - 0.9` is 0.09999999999999998 in
            # binary floating point, so an *exactly* 10% drop silently failed a
            # 10% threshold. Accuracies are ratios of integer counts, so a real
            # difference is at least 1e-6; 10 decimal places only erases
            # representation error.
            regression = round(previous_accuracy - current_accuracy, 10)
            if regression >= self.settings.alert_regression_threshold:
                alerts.append(
                    {
                        "alert_type": "eval_regression",
                        "run_id": run.run_id,
                        "previous_accuracy": previous_accuracy,
                        "current_accuracy": current_accuracy,
                        # Percentage, not a fraction: the key says `pct` and the
                        # accuracies next to it are already fractions.
                        "regression_pct": round(regression * 100.0, 6),
                        "failed_cases": run.failed_count,
                        "total_cases": run.total_cases,
                    }
                )

        # `ALERT_ON_FIXED`: a case that failed in the previous run and passes now.
        # The spec's sketch alerted on `passed_count > 0`, which fires after every
        # healthy run; this compares case by case instead.
        if self.settings.alert_on_fixed and previous is not None:
            fixed_cases = self._newly_fixed(previous, run)
            if fixed_cases:
                alerts.append(
                    {
                        "alert_type": "eval_improvement",
                        "run_id": run.run_id,
                        "current_accuracy": current_accuracy,
                        "fixed_cases": fixed_cases,
                        "fixed_count": len(fixed_cases),
                    }
                )

        return alerts

    def _newly_fixed(self, previous: EvalRun, run: EvalRun) -> list[str]:
        """Case ids that failed in `previous` and pass in `run`."""
        failed_before = {
            result.case_id
            for result in self.storage.get_eval_results_for_run(previous.run_id)
            if not result.fixed
        }
        passing_now = {
            result.case_id
            for result in self.storage.get_eval_results_for_run(run.run_id)
            if result.fixed
        }
        return sorted(failed_before & passing_now)

    # --- alerting ---

    async def send_alert(self, alert_type: str, payload: dict[str, Any]) -> bool:
        """POST `payload` to the configured webhook. Returns whether it was sent."""
        url = self.settings.alert_webhook_url
        if not url:
            return False

        body = json.dumps(
            {
                "alert_type": alert_type,
                "timestamp": datetime.now(UTC).isoformat(),
                "service": "failure-forensics",
                **{k: v for k, v in payload.items() if k != "alert_type"},
            }
        )
        headers = {"Content-Type": "application/json"}
        secret = self.settings.alert_webhook_secret
        if secret is not None:
            signature = hmac.new(
                secret.get_secret_value().encode(),
                body.encode(),
                hashlib.sha256,
            ).hexdigest()
            headers["X-Signature"] = f"sha256={signature}"

        async with httpx.AsyncClient(timeout=10, transport=self.transport) as client:
            response = await client.post(url, content=body, headers=headers)
            response.raise_for_status()
        return True


#: Process-wide scheduler, so the API lifespan, the CLI, and the APScheduler job
#: all share one instance (and therefore one job store).
_scheduler: EvalScheduler | None = None


def get_scheduler(
    storage: Storage | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EvalScheduler:
    """Return the process-wide scheduler, creating it on first use."""
    global _scheduler
    if _scheduler is None:
        _scheduler = EvalScheduler(storage or Storage(), transport=transport)
    return _scheduler


def reset_scheduler() -> None:
    """Drop the cached scheduler (used by tests)."""
    global _scheduler
    _scheduler = None


__all__ = ["JOB_ID", "EvalScheduler", "get_scheduler", "reset_scheduler"]
