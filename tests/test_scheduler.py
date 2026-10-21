"""Scheduled eval and regression alerting (spec 06).

The scheduler is driven through its seams rather than by waiting for a cron tick:
`evaluate_run` decides, `send_alert` delivers, and `run_scheduled_eval` composes
them. Nothing here sleeps, and nothing here reaches the network — the webhook is
exercised through `httpx.MockTransport`, which means the real request-building
code (body, JSON, HMAC header) is what runs.
"""

import asyncio
import hashlib
import hmac
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from apscheduler.triggers.cron import CronTrigger
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from forensics import api
from forensics import scheduler as scheduler_module
from forensics.cli import app as cli_app
from forensics.config import reset_settings
from forensics.models import EvalCase, EvalResult, EvalRun, FailureCategory
from forensics.scheduler import JOB_ID, EvalScheduler, get_scheduler, reset_scheduler
from forensics.storage import Storage

CRON = "0 2 * * *"
SECRET = "webhook-signing-secret"  # pragma: allowlist secret — a test fixture


# ─── Helpers ────────────────────────────────────────────────────────────────


def _trace_row(storage: Storage, trace_id: str) -> None:
    """Insert the minimum `traces` row that `eval_cases.trace_id` points at.

    Deliberately not `storage.save_trace`: these tests are about eval runs, not
    about trace persistence (which `test_auth.py` and `test_tracing_phase2.py`
    cover), and a full save per case would dominate the suite's runtime.
    """
    with storage._conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO traces (trace_id, document_id, status, created_at) "
            "VALUES (?, ?, 'success', ?)",
            (trace_id, f"doc_{trace_id}", datetime.now(UTC).isoformat()),
        )


def _suite(storage: Storage, size: int) -> list[str]:
    """Ensure `size` eval cases exist (reused across runs) and return their ids.

    One suite, many runs, is how the real system works: a regression case that
    keeps failing is evaluated in every run, which is what makes "previously
    failing, now passing" meaningful. `eval_cases.case_id` is a primary key, so
    creating them per run would be both wrong and a constraint violation.
    """
    case_ids: list[str] = []
    for index in range(size):
        case_id = f"case_{index:02d}"
        if storage.get_eval_case(case_id) is None:
            trace_id = f"trace_{case_id}"
            _trace_row(storage, trace_id)
            storage.create_eval_case(
                EvalCase(
                    case_id=case_id,
                    trace_id=trace_id,
                    failing_step="extraction",
                    bad_output={},
                    corrected_output={},
                    category=FailureCategory.EXTRACTION_HALLUCINATION,
                    pipeline_version="0.1.0",
                    prompt_version="test",
                )
            )
        case_ids.append(case_id)
    return case_ids


def _mem_run(run_id: str, passed: int, failed: int) -> EvalRun:
    """A run that is never persisted — for exercising the decision logic alone."""
    return EvalRun(
        run_id=run_id,
        pipeline_version="0.1.0",
        prompt_versions={},
        started_at=datetime.now(UTC) - timedelta(days=1),
        completed_at=datetime.now(UTC),
        total_cases=passed + failed,
        passed_count=passed,
        failed_count=failed,
    )


def _persisted_run(storage: Storage, *, run_id: str, passed: int, failed: int) -> EvalRun:
    """Persist a finished run and one result per case in the shared suite."""
    case_ids = _suite(storage, passed + failed)
    run = _mem_run(run_id, passed, failed)
    storage.create_eval_run(run)
    for index, case_id in enumerate(case_ids):
        storage.create_eval_result(
            EvalResult(
                run_id=run_id,
                case_id=case_id,
                step_name="extraction",
                score=1.0 if index < passed else 0.0,
                fixed=index < passed,
                details={},
            )
        )
    return run


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Build `EvalScheduler`s against a throwaway DB, and always stop them.

    A leaked `BackgroundScheduler` keeps a live thread and holds the job store
    open, which makes a later test's SQLite writes flaky.
    """
    created: list[EvalScheduler] = []

    def _factory(
        schedule: str = CRON,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        storage: Storage | None = None,
        **env: str,
    ) -> EvalScheduler:
        monkeypatch.setenv("EVAL_SCHEDULE", schedule)
        monkeypatch.setenv("EVAL_SCHEDULE_TIMEZONE", "UTC")
        monkeypatch.setenv("SCHEDULER_DB_PATH", str(tmp_path / "scheduler.db"))
        for key, value in env.items():
            monkeypatch.setenv(key.upper(), value)
        reset_settings()
        scheduler = EvalScheduler(
            storage or Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite"),
            transport=transport,
        )
        created.append(scheduler)
        return scheduler

    try:
        yield _factory
    finally:
        for scheduler in created:
            scheduler.stop()
        reset_scheduler()
        reset_settings()


@pytest.fixture
def webhook() -> tuple[list[httpx.Request], httpx.MockTransport]:
    """A capturing transport. Returns `(requests, transport)`."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    return captured, httpx.MockTransport(handler)


# ─── Criterion 1: empty EVAL_SCHEDULE disables the scheduler ────────────────


def test_empty_schedule_does_not_start_the_scheduler(factory: Any) -> None:
    scheduler = factory("")

    scheduler.start()

    assert scheduler.scheduler.running is False
    assert scheduler.scheduler.get_jobs() == []
    assert scheduler.describe()["next_eval"] is None


def test_stopping_a_scheduler_that_never_started_is_safe(factory: Any) -> None:
    """The API calls `stop()` on shutdown, including with no schedule set.

    Unguarded, APScheduler raises `SchedulerNotRunningError` and the app fails to
    shut down cleanly.
    """
    scheduler = factory("")

    scheduler.stop()  # must not raise

    assert scheduler.scheduler.running is False


# ─── Criterion 2: a cron expression schedules a daily run ───────────────────


def test_cron_schedule_registers_a_daily_job(factory: Any) -> None:
    scheduler = factory(CRON)

    scheduler.start()
    try:
        job = scheduler.scheduler.get_job(JOB_ID)
        assert job is not None
        assert isinstance(job.trigger, CronTrigger)
        fields = {field.name: str(field) for field in job.trigger.fields}
        assert fields["hour"] == "2"
        assert fields["minute"] == "0"
        assert job.max_instances == 1
        assert job.coalesce is True
    finally:
        scheduler.stop()

    assert scheduler.scheduler.running is False


def test_next_run_time_is_the_next_2am_utc(factory: Any) -> None:
    scheduler = factory(CRON)

    next_run = scheduler.next_run_time()

    assert next_run is not None
    assert next_run.tzinfo is not None
    assert next_run.astimezone(UTC).hour == 2
    assert next_run.astimezone(UTC).minute == 0
    assert next_run > datetime.now(UTC)


def test_starting_twice_does_not_duplicate_the_job(factory: Any) -> None:
    """The API lifespan can run more than once in a test session."""
    scheduler = factory(CRON)

    scheduler.start()
    scheduler.start()
    try:
        assert len(scheduler.scheduler.get_jobs()) == 1
    finally:
        scheduler.stop()


# ─── Criteria 3 and 4: regression alerts and their payload ──────────────────


def _alert_payload(request: httpx.Request) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(request.content)
    return payload


def test_regression_posts_an_alert_with_the_full_payload(
    factory: Any,
    webhook: tuple[list[httpx.Request], httpx.MockTransport],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured, transport = webhook
    scheduler = factory(CRON, transport=transport, alert_webhook_url="https://hooks.test/alerts")
    storage = scheduler.storage
    _persisted_run(storage, run_id="prev", passed=9, failed=1)  # 90%

    async def fake_run_eval(tenant_id: str | None = None) -> EvalRun:
        return _persisted_run(storage, run_id="current", passed=7, failed=3)  # 70%

    monkeypatch.setattr(scheduler_module, "run_eval", fake_run_eval)

    alerts = asyncio.run(scheduler.run_scheduled_eval())

    assert alerts is not None and len(alerts) == 1
    assert len(captured) == 1

    payload = _alert_payload(captured[0])
    assert payload["alert_type"] == "eval_regression"
    assert payload["service"] == "failure-forensics"
    assert payload["run_id"] == "current"
    assert payload["previous_accuracy"] == pytest.approx(0.9)
    assert payload["current_accuracy"] == pytest.approx(0.7)
    assert payload["regression_pct"] == pytest.approx(20.0)
    assert payload["failed_cases"] == 3
    assert "timestamp" in payload
    assert captured[0].headers["content-type"] == "application/json"


def test_one_undeliverable_alert_does_not_swallow_the_next(
    factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alerts are delivered independently.

    A regression and an improvement from the same run are separate
    notifications, so a webhook that fails on the first must not prevent the
    second from being attempted.
    """
    scheduler = factory(CRON, alert_webhook_url="https://hooks.test/alerts")
    attempted: list[str] = []

    async def flaky(alert_type: str, payload: dict[str, Any]) -> bool:
        attempted.append(alert_type)
        if alert_type == "eval_regression":
            raise httpx.ConnectError("webhook unreachable")
        return True

    monkeypatch.setattr(scheduler, "send_alert", flaky)

    alerts = [{"alert_type": "eval_regression"}, {"alert_type": "eval_improvement"}]
    asyncio.run(scheduler._deliver_alerts(alerts))

    assert attempted == ["eval_regression", "eval_improvement"], (
        "a failed alert stopped the remaining deliveries"
    )


def test_regression_below_threshold_does_not_alert(factory: Any) -> None:
    scheduler = factory(CRON, alert_regression_threshold="0.10")
    previous = _mem_run("prev", passed=100, failed=0)
    current = _mem_run("current", passed=91, failed=9)  # 9% drop

    alerts = scheduler.evaluate_run(
        previous=previous, previous_accuracy=1.0, run=current, current_accuracy=0.91
    )

    assert alerts == []


def test_regression_exactly_at_threshold_alerts(factory: Any) -> None:
    scheduler = factory(CRON, alert_regression_threshold="0.10")
    previous = _mem_run("prev", passed=100, failed=0)
    current = _mem_run("current", passed=90, failed=10)  # exactly 10%

    alerts = scheduler.evaluate_run(
        previous=previous, previous_accuracy=1.0, run=current, current_accuracy=0.90
    )

    assert [a["alert_type"] for a in alerts] == ["eval_regression"]
    assert alerts[0]["regression_pct"] == pytest.approx(10.0)


def test_improvement_in_accuracy_never_alerts_as_regression(factory: Any) -> None:
    scheduler = factory(CRON)
    previous = _mem_run("prev", passed=5, failed=5)
    current = _mem_run("current", passed=10, failed=0)

    alerts = scheduler.evaluate_run(
        previous=previous, previous_accuracy=0.5, run=current, current_accuracy=1.0
    )

    assert alerts == []


def test_first_ever_run_cannot_regress(factory: Any) -> None:
    """With no history there is no baseline, so a low score is not an alert."""
    scheduler = factory(CRON)
    current = _mem_run("first", passed=0, failed=4)

    alerts = scheduler.evaluate_run(
        previous=None, previous_accuracy=None, run=current, current_accuracy=0.0
    )

    assert alerts == []


def test_run_with_no_cases_has_no_accuracy(factory: Any) -> None:
    """A guard against ZeroDivisionError when a run evaluated nothing."""
    scheduler = factory(CRON)
    empty = _mem_run("empty", passed=0, failed=0)

    alerts = scheduler.evaluate_run(
        previous=None, previous_accuracy=None, run=empty, current_accuracy=None
    )

    assert alerts == []


# ─── Criterion 4: HMAC signature ────────────────────────────────────────────


def test_alert_is_signed_with_the_configured_secret(
    factory: Any, webhook: tuple[list[httpx.Request], httpx.MockTransport]
) -> None:
    captured, transport = webhook
    scheduler = factory(
        CRON,
        transport=transport,
        alert_webhook_url="https://hooks.test/alerts",
        alert_webhook_secret=SECRET,
    )

    sent = asyncio.run(scheduler.send_alert("eval_regression", {"run_id": "r1"}))

    assert sent is True
    signature = captured[0].headers["x-signature"]
    expected = hmac.new(SECRET.encode(), captured[0].content, hashlib.sha256).hexdigest()
    assert signature == f"sha256={expected}"


def test_alert_carries_no_signature_without_a_secret(
    factory: Any, webhook: tuple[list[httpx.Request], httpx.MockTransport]
) -> None:
    captured, transport = webhook
    scheduler = factory(CRON, transport=transport, alert_webhook_url="https://hooks.test/alerts")

    asyncio.run(scheduler.send_alert("eval_regression", {"run_id": "r1"}))

    assert "x-signature" not in captured[0].headers


def test_empty_webhook_secret_is_treated_as_unset(factory: Any) -> None:
    """`ALERT_WEBHOOK_SECRET=` must not sign with a known-empty key."""
    scheduler = factory(
        CRON,
        alert_webhook_url="https://hooks.test/alerts",
        alert_webhook_secret="",
    )

    assert scheduler.settings.alert_webhook_secret is None


def test_alert_without_a_webhook_is_not_sent(factory: Any) -> None:
    scheduler = factory(CRON)

    sent = asyncio.run(scheduler.send_alert("eval_regression", {"run_id": "r1"}))

    assert sent is False


# ─── ALERT_ON_FIXED: a case that used to fail now passes ────────────────────


def test_improvement_alerts_only_for_newly_fixed_cases(factory: Any) -> None:
    scheduler = factory(CRON, alert_on_fixed="true")
    storage = scheduler.storage
    previous = _persisted_run(storage, run_id="prev", passed=1, failed=1)  # case_01 fails
    current = _persisted_run(storage, run_id="current", passed=2, failed=0)  # case_01 fixed

    alerts = scheduler.evaluate_run(
        previous=previous, previous_accuracy=0.5, run=current, current_accuracy=1.0
    )

    improvement = [a for a in alerts if a["alert_type"] == "eval_improvement"]
    assert len(improvement) == 1
    assert improvement[0]["fixed_cases"] == ["case_01"]
    assert improvement[0]["fixed_count"] == 1


def test_no_improvement_alert_when_nothing_was_fixed(factory: Any) -> None:
    """The spec's sketch alerted whenever `passed_count > 0` — i.e. every run."""
    scheduler = factory(CRON, alert_on_fixed="true")
    storage = scheduler.storage
    previous = _persisted_run(storage, run_id="prev", passed=2, failed=0)
    current = _persisted_run(storage, run_id="current", passed=2, failed=0)

    alerts = scheduler.evaluate_run(
        previous=previous, previous_accuracy=1.0, run=current, current_accuracy=1.0
    )

    assert alerts == []


def test_alert_on_fixed_can_be_disabled(factory: Any) -> None:
    scheduler = factory(CRON, alert_on_fixed="false")
    storage = scheduler.storage
    previous = _persisted_run(storage, run_id="prev", passed=1, failed=1)
    current = _persisted_run(storage, run_id="current", passed=2, failed=0)

    alerts = scheduler.evaluate_run(
        previous=previous, previous_accuracy=0.5, run=current, current_accuracy=1.0
    )

    assert [a["alert_type"] for a in alerts] == []


# ─── Robustness of the job wrapper ──────────────────────────────────────────


def test_run_now_survives_a_failing_eval(factory: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A blown eval must not take down the scheduler thread."""
    scheduler = factory(CRON)

    async def explode() -> None:
        raise RuntimeError("model provider is down")

    monkeypatch.setattr(scheduler, "run_scheduled_eval", explode)

    scheduler.run_now()  # must not raise


def test_run_now_survives_a_failing_webhook(factory: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    scheduler = factory(
        CRON,
        transport=httpx.MockTransport(handler),
        alert_webhook_url="https://hooks.test/alerts",
    )
    storage = scheduler.storage
    _persisted_run(storage, run_id="prev", passed=10, failed=0)

    async def fake_run_eval(tenant_id: str | None = None) -> EvalRun:
        return _persisted_run(storage, run_id="current", passed=0, failed=10)

    monkeypatch.setattr(scheduler_module, "run_eval", fake_run_eval)

    scheduler.run_now()  # the 500 is logged, not raised


def test_no_cases_means_no_run_and_no_alert(factory: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = factory(CRON, alert_webhook_url="https://hooks.test/alerts")

    async def no_cases(tenant_id: str | None = None) -> None:
        return None

    monkeypatch.setattr(scheduler_module, "run_eval", no_cases)

    assert asyncio.run(scheduler.run_scheduled_eval()) is None


# ─── Criterion 5: the CLI runs the scheduler in the foreground ──────────────


def test_cli_scheduler_start_refuses_without_a_schedule(
    factory: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SCHEDULER_DB_PATH", str(tmp_path / "cli-scheduler.db"))
    monkeypatch.setenv("EVAL_SCHEDULE", "")
    reset_settings()

    result = CliRunner().invoke(cli_app, ["scheduler-start"])

    assert result.exit_code == 1
    assert "EVAL_SCHEDULE is empty" in result.output


def test_cli_scheduler_start_runs_until_interrupted(
    factory: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Blocking on a shutdown signal is what a systemd unit relies on; interrupt it here."""
    monkeypatch.setenv("SCHEDULER_DB_PATH", str(tmp_path / "cli-scheduler.db"))
    monkeypatch.setenv("EVAL_SCHEDULE", CRON)
    reset_settings()
    reset_scheduler()

    def interrupt() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("forensics.cli._wait_for_shutdown_signal", interrupt)

    result = CliRunner().invoke(cli_app, ["scheduler-start"])

    assert result.exit_code == 0
    assert "Scheduler started" in result.output
    assert "Next eval:" in result.output
    # It stopped on the way out rather than leaving a thread behind.
    assert get_scheduler().scheduler.running is False


# ─── Criterion 6: status shows the next run time ────────────────────────────


def test_cli_scheduler_status_shows_next_run(
    factory: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SCHEDULER_DB_PATH", str(tmp_path / "cli-scheduler.db"))
    monkeypatch.setenv("EVAL_SCHEDULE", CRON)
    reset_settings()
    reset_scheduler()

    result = CliRunner().invoke(cli_app, ["scheduler-status"])

    assert result.exit_code == 0, result.output
    assert CRON in result.output
    assert "Next eval:" in result.output
    assert "T02:00:00" in result.output  # 2 AM UTC
    assert "No eval runs yet" in result.output


def test_cli_scheduler_status_lists_recent_runs(
    factory: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SCHEDULER_DB_PATH", str(tmp_path / "cli-scheduler.db"))
    monkeypatch.setenv("EVAL_SCHEDULE", CRON)
    reset_settings()
    reset_scheduler()
    _persisted_run(Storage(), run_id="run_abc", passed=7, failed=3)

    result = CliRunner().invoke(cli_app, ["scheduler-status"])

    assert result.exit_code == 0, result.output
    assert "run_abc" in result.output
    assert "70%" in result.output


def test_cli_scheduler_status_without_a_schedule(
    factory: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SCHEDULER_DB_PATH", str(tmp_path / "cli-scheduler.db"))
    monkeypatch.setenv("EVAL_SCHEDULE", "")
    reset_settings()
    reset_scheduler()

    result = CliRunner().invoke(cli_app, ["scheduler-status"])

    assert result.exit_code == 0, result.output
    assert "EVAL_SCHEDULE is empty" in result.output


def test_scheduler_status_endpoint_reports_the_schedule(
    factory: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SCHEDULER_DB_PATH", str(tmp_path / "api-scheduler.db"))
    monkeypatch.setenv("EVAL_SCHEDULE", CRON)
    reset_settings()
    reset_scheduler()
    monkeypatch.setattr(api, "_storage", Storage())

    response = TestClient(api.app).get("/scheduler/status")

    assert response.status_code == 200
    body = response.json()
    assert body["schedule"] == CRON
    assert body["timezone"] == "UTC"
    # 2 AM UTC, computed from the cron (this process never started the scheduler).
    assert "T02:00:00" in body["next_eval"]
    assert body["alert_webhook_configured"] is False
    # A filesystem path is not part of the HTTP contract.
    assert "job_store" not in body


# ─── Criterion 7: the schedule survives a process restart ───────────────────


def test_job_store_is_sqlite_backed(factory: Any, tmp_path: Path) -> None:
    scheduler = factory(CRON)
    scheduler.start()
    scheduler.stop()

    db_path = scheduler.settings.scheduler_db_path
    assert db_path.exists()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT id FROM apscheduler_jobs").fetchall()
    finally:
        conn.close()

    assert rows == [(JOB_ID,)]


def test_schedule_survives_a_new_scheduler_instance(
    factory: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A restart (new process, new object, same DB) must reload the job.

    `run_eval` is replaced with something that fails the test if it is ever
    called: this must be a *reload*, not an accidental immediate run.
    """
    first = factory(CRON)
    first.start()
    first_next_run = first.scheduler.get_job(JOB_ID).next_run_time
    first.stop()
    assert first_next_run is not None

    async def must_not_run(tenant_id: str | None = None) -> None:
        raise AssertionError("the reloaded job fired during the test")

    monkeypatch.setattr("forensics.scheduler.run_eval", must_not_run)

    second = EvalScheduler(Storage())
    try:
        second.start()
        reloaded = second.scheduler.get_job(JOB_ID)
        assert reloaded is not None
        assert reloaded.next_run_time == first_next_run
    finally:
        second.stop()
