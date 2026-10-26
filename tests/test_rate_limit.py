"""Rate limiting: the token bucket and the middleware that applies it.

`rate_limit.py` shipped as "resolved" in `docs/limitations.md` with **no test at
all** — the module was at 0% coverage, so nothing proved the limiter limited
anything, that buckets were per tenant, or that health checks stayed reachable
while a tenant was throttled.

The arithmetic is exercised against a real SQLite file (not a mock) because the
persistence is part of the contract: a bucket that lived only in memory would
reset on every process restart, and a per-process bucket is not a rate limit.

`time.time` is patched rather than slept, so refill behaviour is asserted exactly
instead of approximately.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forensics import api
from forensics import rate_limit as rate_limit_module
from forensics.auth import AuthManager
from forensics.config import reset_settings
from forensics.models import Span, Trace
from forensics.rate_limit import RateLimiter, get_rate_limiter, reset_rate_limiter
from forensics.storage import Storage, reset_storage

PREFIX = "ffk_"
ROUNDS = 4  # bcrypt cost 4 keeps the suite fast


class _Clock:
    """A controllable `time.time()` for the module under test."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(rate_limit_module.time, "time", fake)
    return fake


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Two requests in the bucket, refilling at one per minute."""
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
    monkeypatch.setenv("RATE_LIMIT_BURST", "1")
    reset_settings()
    try:
        yield
    finally:
        reset_settings()


@pytest.fixture
def server(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> Iterator[tuple[Storage, AuthManager, TestClient]]:
    """The real app, with auth on and a two-request bucket per tenant."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_BCRYPT_ROUNDS", str(ROUNDS))
    reset_settings()
    reset_storage()
    reset_rate_limiter()

    storage = Storage()
    manager = AuthManager(storage, prefix=PREFIX, rounds=ROUNDS)
    monkeypatch.setattr(api, "_storage", storage)
    monkeypatch.setattr(api, "_auth", manager)

    try:
        yield storage, manager, TestClient(api.app)
    finally:
        reset_rate_limiter()
        reset_storage()
        reset_settings()


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _trace(trace_id: str, tenant_id: str) -> Trace:
    trace = Trace(trace_id=trace_id, document_id="doc_001", tenant_id=tenant_id)
    trace.spans = [
        Span(
            trace_id=trace_id,
            step_name="extraction",
            input_json="{}",
            output_json="{}",
            model_id="test-model",
            provider="test",
            prompt="p",
            raw_response="r",
            tenant_id=tenant_id,
        )
    ]
    return trace


def _new_tenant(manager: AuthManager, name: str) -> str:
    tenant = manager.create_tenant(name)
    _, plaintext = manager.create_api_key(tenant.tenant_id, name=f"{name}-key")
    return plaintext


# ─── The bucket ─────────────────────────────────────────────────────────────


def test_fresh_bucket_allows_capacity_then_refuses(
    configured: None, clock: _Clock, tmp_path: Path
) -> None:
    limiter = RateLimiter(db_path=tmp_path / "limiter.db")

    assert limiter.try_consume("acme") == (True, 0.0)
    assert limiter.try_consume("acme") == (True, 0.0)

    allowed, retry_after = limiter.try_consume("acme")

    assert allowed is False
    # Empty bucket at one token per minute: a full minute until the next token.
    assert retry_after == pytest.approx(60.0, abs=0.5)


def test_bucket_refills_at_the_configured_rate(
    configured: None, clock: _Clock, tmp_path: Path
) -> None:
    limiter = RateLimiter(db_path=tmp_path / "limiter.db")
    limiter.try_consume("acme")
    limiter.try_consume("acme")

    clock.advance(30)
    allowed, retry_after = limiter.try_consume("acme")
    assert allowed is False, "half a token is not a token"
    assert retry_after == pytest.approx(30.0, abs=0.5)

    clock.advance(30)
    assert limiter.try_consume("acme") == (True, 0.0)


def test_buckets_are_per_tenant(configured: None, clock: _Clock, tmp_path: Path) -> None:
    limiter = RateLimiter(db_path=tmp_path / "limiter.db")
    limiter.try_consume("acme")
    limiter.try_consume("acme")
    assert limiter.try_consume("acme")[0] is False

    # Another tenant must not inherit the first tenant's exhaustion.
    assert limiter.try_consume("globex") == (True, 0.0)


def test_state_survives_a_new_instance(configured: None, clock: _Clock, tmp_path: Path) -> None:
    """The bucket lives in SQLite, not in the object: a restart must not reset it."""
    db = tmp_path / "limiter.db"
    first = RateLimiter(db_path=db)
    first.try_consume("acme")
    first.try_consume("acme")

    second = RateLimiter(db_path=db)

    assert second.try_consume("acme")[0] is False
    assert second.get_bucket_state("acme")["tokens"] == pytest.approx(0.0, abs=0.01)


def test_bucket_state_reports_capacity_and_refill_rate(
    configured: None, clock: _Clock, tmp_path: Path
) -> None:
    limiter = RateLimiter(db_path=tmp_path / "limiter.db")
    limiter.try_consume("acme")

    state = limiter.get_bucket_state("acme")

    assert state["tenant_id"] == "acme"
    assert state["capacity"] == 2.0  # 1/min + burst 1
    assert state["tokens"] == pytest.approx(1.0, abs=0.01)
    assert state["refill_rate_per_sec"] == pytest.approx(1 / 60, rel=1e-6)


def test_refill_never_exceeds_capacity(configured: None, clock: _Clock, tmp_path: Path) -> None:
    """A long idle period must not bank more than the bucket can hold."""
    limiter = RateLimiter(db_path=tmp_path / "limiter.db")
    clock.advance(10_000)

    assert limiter.get_bucket_state("acme")["tokens"] == pytest.approx(2.0)


def test_the_singleton_is_resettable(configured: None) -> None:
    first = get_rate_limiter()
    assert get_rate_limiter() is first

    reset_rate_limiter()

    assert get_rate_limiter() is not first


# ─── The middleware ─────────────────────────────────────────────────────────


def test_exhausted_tenant_gets_429_with_retry_after(
    server: tuple[Storage, AuthManager, TestClient],
) -> None:
    storage, manager, client = server
    key = _new_tenant(manager, "acme")
    storage.save_trace(_trace("t_acme", "acme"))

    assert client.get("/traces", headers=_bearer(key)).status_code == 200
    assert client.get("/traces", headers=_bearer(key)).status_code == 200

    limited = client.get("/traces", headers=_bearer(key))

    assert limited.status_code == 429
    assert limited.json()["detail"] == "Rate limit exceeded"
    assert int(limited.headers["Retry-After"]) >= 1


def test_successful_responses_carry_the_rate_limit_headers(
    server: tuple[Storage, AuthManager, TestClient],
) -> None:
    storage, manager, client = server
    key = _new_tenant(manager, "acme")
    storage.save_trace(_trace("t_acme", "acme"))

    first = client.get("/traces", headers=_bearer(key))
    second = client.get("/traces", headers=_bearer(key))

    assert first.headers["X-RateLimit-Limit"] == "2"
    assert float(first.headers["X-RateLimit-Remaining"]) > float(
        second.headers["X-RateLimit-Remaining"]
    )


def test_tenants_are_throttled_independently_through_the_api(
    server: tuple[Storage, AuthManager, TestClient],
) -> None:
    storage, manager, client = server
    acme = _new_tenant(manager, "acme")
    globex = _new_tenant(manager, "globex")
    storage.save_trace(_trace("t_acme", "acme"))
    storage.save_trace(_trace("t_globex", "globex"))

    for _ in range(3):
        client.get("/traces", headers=_bearer(acme))
    assert client.get("/traces", headers=_bearer(acme)).status_code == 429

    # A throttled tenant must not throttle anyone else.
    assert client.get("/traces", headers=_bearer(globex)).status_code == 200


def test_health_stays_reachable_while_a_tenant_is_throttled(
    server: tuple[Storage, AuthManager, TestClient],
) -> None:
    """Kubernetes and Render both probe /health; throttling it looks like an outage."""
    storage, manager, client = server
    key = _new_tenant(manager, "acme")
    storage.save_trace(_trace("t_acme", "acme"))

    for _ in range(3):
        client.get("/traces", headers=_bearer(key))

    assert client.get("/health").status_code == 200


def test_limiting_disabled_sends_no_bucket_headers(
    monkeypatch: pytest.MonkeyPatch, server: tuple[Storage, AuthManager, TestClient]
) -> None:
    storage, manager, client = server
    key = _new_tenant(manager, "acme")
    storage.save_trace(_trace("t_acme", "acme"))
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_settings()

    response = client.get("/traces", headers=_bearer(key))

    assert response.status_code == 200
    assert "X-RateLimit-Limit" not in response.headers


def test_auth_disabled_means_no_tenant_to_throttle(
    monkeypatch: pytest.MonkeyPatch, server: tuple[Storage, AuthManager, TestClient]
) -> None:
    storage, _, client = server
    storage.save_trace(_trace("t_dev", "default"))
    monkeypatch.setenv("AUTH_ENABLED", "false")
    reset_settings()

    responses = [client.get("/traces").status_code for _ in range(5)]

    assert responses == [200] * 5, "dev mode must not be rate limited"


def test_the_limiter_is_keyed_on_the_tenant_not_the_key(
    server: tuple[Storage, AuthManager, TestClient],
) -> None:
    """Rotating a key must not hand out a fresh bucket for the same tenant."""
    storage, manager, client = server
    tenant = manager.create_tenant("acme")
    _, first_key = manager.create_api_key(tenant.tenant_id, name="one")
    _, second_key = manager.create_api_key(tenant.tenant_id, name="two")
    storage.save_trace(_trace("t_acme", "acme"))

    for _ in range(3):
        client.get("/traces", headers=_bearer(first_key))

    assert client.get("/traces", headers=_bearer(second_key)).status_code == 429


def test_a_failed_statement_rolls_back_and_leaves_the_limiter_usable(
    configured: None, tmp_path: Path
) -> None:
    """The connection helper must not leave a half-written bucket behind."""
    limiter = RateLimiter(db_path=tmp_path / "limiter.db")

    with pytest.raises(sqlite3.OperationalError), limiter._conn() as conn:
        conn.execute("INSERT INTO no_such_table VALUES (1)")

    assert limiter.try_consume("acme") == (True, 0.0)
