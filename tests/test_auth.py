"""Auth and tenant isolation (spec 04).

One group per acceptance criterion. The API tests go through `TestClient` rather
than calling endpoint functions directly, because the behaviour under test is
partly FastAPI's header handling: a direct call can never prove that a missing
`Authorization` header is rejected.

The fixture pair matters: `auth_on`/`auth_off` reset the settings singleton so
`AUTH_ENABLED` actually takes effect, and both monkeypatch the API module's
cached `Storage`/`AuthManager` so no test touches the repository's database.
"""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import forensics.storage
from forensics import api
from forensics.auth import AuthManager
from forensics.cli import app as cli_app
from forensics.config import reset_settings
from forensics.models import (
    API_KEY_SCOPES,
    DEFAULT_TENANT_ID,
    Diagnosis,
    FailureCategory,
    Flag,
    Span,
    Trace,
)
from forensics.storage import Storage, reset_storage

# ─── Helpers ────────────────────────────────────────────────────────────────

API_PREFIX = "ffk_"
ROUNDS = 4  # bcrypt cost 4 keeps the suite fast; cost is a config value, not logic.


def _span(trace_id: str, step: str = "extraction") -> Span:
    return Span(
        trace_id=trace_id,
        step_name=step,
        input_json="{}",
        output_json="{}",
        model_id="test-model",
        provider="test",
        prompt="p",
        raw_response="r",
    )


def _trace(trace_id: str, tenant_id: str = DEFAULT_TENANT_ID) -> Trace:
    trace = Trace(trace_id=trace_id, document_id="doc_001", tenant_id=tenant_id)
    trace.spans = [_span(trace_id)]
    return trace


def _diagnosis(trace_id: str, tenant_id: str = DEFAULT_TENANT_ID) -> Diagnosis:
    return Diagnosis(
        trace_id=trace_id,
        root_cause_step="extraction",
        category=FailureCategory.EXTRACTION_HALLUCINATION,
        confidence=0.8,
        analyzer_version="0.1.0",
        model_id="test-model",
        provider="test",
        tenant_id=tenant_id,
    )


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def auth_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Dev mode: `AUTH_ENABLED=false` (the documented default)."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    reset_settings()
    yield
    # Other tests must rebuild settings from *their* environment, not this one's.
    reset_settings()


@pytest.fixture
def auth_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Auth mode with a cheap bcrypt cost."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_BCRYPT_ROUNDS", str(ROUNDS))
    reset_settings()
    yield
    reset_settings()


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> tuple[Storage, AuthManager]:
    """Point the API at the test's configured database.

    Uses the same env-derived paths as the CLI (`Storage()` with no arguments),
    so the `auth` CLI tests and the API tests are looking at one database, which
    is the deployment reality those criteria describe.
    """
    reset_settings()
    storage = Storage()
    manager = AuthManager(storage, prefix=API_PREFIX, rounds=ROUNDS)
    monkeypatch.setattr(api, "_storage", storage)
    monkeypatch.setattr(api, "_auth", manager)
    return storage, manager


@pytest.fixture
def client() -> TestClient:
    return TestClient(api.app)


def _seed_tenant(manager: AuthManager, name: str) -> tuple[str, str]:
    """Create a tenant plus one key. Returns `(tenant_id, plaintext_key)`."""
    tenant = manager.create_tenant(name)
    _, plaintext = manager.create_api_key(tenant.tenant_id, name=f"{name}-key")
    return tenant.tenant_id, plaintext


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# ─── Criterion 1: AUTH_ENABLED=false works as before ────────────────────────


def test_dev_mode_serves_requests_without_any_header(
    auth_off: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    storage, _ = state
    storage.save_trace(_trace("t_dev"))

    response = client.get("/traces")

    assert response.status_code == 200
    assert [t["trace_id"] for t in response.json()] == ["t_dev"]
    assert client.get("/traces/t_dev").status_code == 200
    assert client.get("/eval/cases").status_code == 200
    assert client.get("/analytics").status_code == 200


def test_dev_mode_does_not_hide_other_tenants_data(
    auth_off: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    """With auth off the API must "work as before", i.e. show everything.

    Filtering dev mode down to tenant `default` would hide the data of a
    deployment that had auth on and then turned it off.
    """
    storage, _ = state
    storage.save_trace(_trace("t_default", DEFAULT_TENANT_ID))
    storage.save_trace(_trace("t_acme", "acme"))

    listed = {t["trace_id"] for t in client.get("/traces").json()}

    assert listed == {"t_default", "t_acme"}
    assert client.get("/traces/t_acme").status_code == 200


def test_dev_mode_stamps_new_records_with_the_default_tenant(
    auth_off: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    storage, _ = state
    storage.save_trace(_trace("t_dev"))

    response = client.post("/traces/t_dev/flag", json={"note": "dev flag"})

    assert response.status_code == 200
    assert response.json()["tenant_id"] == DEFAULT_TENANT_ID
    assert storage.list_flags_for_trace("t_dev")[0].tenant_id == DEFAULT_TENANT_ID


# ─── Criterion 2: AUTH_ENABLED=true requires a bearer key ────────────────────

#: Every data endpoint, with a request body where one is required. `/health` is
#: deliberately absent: a platform liveness probe has no key to present.
PROTECTED_ENDPOINTS: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", "/traces", None),
    ("GET", "/traces/t1", None),
    ("GET", "/traces/t1/diagnosis", None),
    ("POST", "/traces/t1/flag", {"note": "n"}),
    ("POST", "/traces/t1/diagnose", None),
    ("POST", "/diagnoses/d1/confirm", {"confirmed": True}),
    ("GET", "/eval/cases", None),
    ("GET", "/eval/runs", None),
    ("GET", "/eval/runs/r1", None),
    ("GET", "/eval/runs/r1/results", None),
    ("POST", "/eval/run", None),
    ("GET", "/analytics", None),
    ("POST", "/pipeline/run-demo", None),
    ("POST", "/pipeline/run", {"doc_id": "d", "doc_text": "x"}),
    ("GET", "/jobs", None),
    ("GET", "/jobs/j1", None),
    ("GET", "/scheduler/status", None),
    ("GET", "/runtime", None),
]


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED_ENDPOINTS)
def test_every_data_endpoint_requires_a_key(
    auth_on: None,
    state: tuple[Storage, AuthManager],
    client: TestClient,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    response = client.request(method, path, json=body)

    assert response.status_code == 401, f"{method} {path} answered {response.status_code}"


@pytest.mark.parametrize(
    "header",
    [
        "",
        "ffk_somekey",  # no scheme
        "Basic ffk_somekey",
        "Bearer",  # scheme with no credential
        "Bearer ",  # empty credential
        "Bearer notprefixed_at_all",
    ],
)
def test_malformed_authorization_headers_are_rejected(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient, header: str
) -> None:
    response = client.get("/traces", headers={"Authorization": header})

    assert response.status_code == 401


def test_health_is_the_one_public_route(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_valid_key_is_accepted_and_reports_own_data(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    storage, manager = state
    tenant_id, key = _seed_tenant(manager, "acme")
    storage.save_trace(_trace("t_acme", tenant_id))

    response = client.get("/traces", headers=_bearer(key))

    assert response.status_code == 200
    assert [t["trace_id"] for t in response.json()] == ["t_acme"]


# ─── Criterion 3: invalid and revoked keys are 401 ──────────────────────────


def test_unknown_key_is_rejected(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    response = client.get("/traces", headers=_bearer(f"{API_PREFIX}this_key_was_never_minted"))

    assert response.status_code == 401


def test_revoked_key_is_rejected(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    storage, manager = state
    tenant = manager.create_tenant("acme")
    api_key, key = manager.create_api_key(tenant.tenant_id)
    storage.save_trace(_trace("t_acme", tenant.tenant_id))
    assert client.get("/traces", headers=_bearer(key)).status_code == 200

    assert manager.revoke_key(api_key.key_id) is True

    assert client.get("/traces", headers=_bearer(key)).status_code == 401


def test_key_with_unrecognised_prefix_is_rejected(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    storage, manager = state
    tenant_id, key = _seed_tenant(manager, "acme")
    storage.save_trace(_trace("t_acme", tenant_id))

    # Same secret, wrong prefix: identification must fail before any comparison.
    response = client.get("/traces", headers=_bearer("zzz_" + key[len(API_PREFIX) :]))

    assert response.status_code == 401


def test_over_long_key_is_rejected_without_a_server_error(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    """Bcrypt raises above 72 bytes; a hostile header must not become a 500."""
    response = client.get("/traces", headers=_bearer(API_PREFIX + "a" * 5000))

    assert response.status_code == 401


# ─── Criterion 4: cross-tenant access is 404 ────────────────────────────────


def test_cross_tenant_trace_read_is_404(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    storage, manager = state
    acme_id, acme_key = _seed_tenant(manager, "acme")
    other_id, other_key = _seed_tenant(manager, "other")
    storage.save_trace(_trace("t_acme", acme_id))

    assert client.get("/traces/t_acme", headers=_bearer(acme_key)).status_code == 200
    assert client.get("/traces/t_acme", headers=_bearer(other_key)).status_code == 404
    assert other_id != acme_id


def test_cross_tenant_lists_are_separated(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    storage, manager = state
    acme_id, acme_key = _seed_tenant(manager, "acme")
    _, other_key = _seed_tenant(manager, "other")
    storage.save_trace(_trace("t_acme", acme_id))

    assert [t["trace_id"] for t in client.get("/traces", headers=_bearer(acme_key)).json()] == [
        "t_acme"
    ]
    assert client.get("/traces", headers=_bearer(other_key)).json() == []


def test_cross_tenant_flag_is_404(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    storage, manager = state
    acme_id, _ = _seed_tenant(manager, "acme")
    _, other_key = _seed_tenant(manager, "other")
    storage.save_trace(_trace("t_acme", acme_id))

    response = client.post(
        "/traces/t_acme/flag", json={"note": "sneaky"}, headers=_bearer(other_key)
    )

    assert response.status_code == 404
    assert storage.list_flags_for_trace("t_acme") == []


def test_cross_tenant_diagnosis_confirm_is_404(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    storage, manager = state
    acme_id, acme_key = _seed_tenant(manager, "acme")
    _, other_key = _seed_tenant(manager, "other")
    storage.save_trace(_trace("t_acme", acme_id))
    diagnosis = storage.create_diagnosis(_diagnosis("t_acme", acme_id))

    response = client.post(
        f"/diagnoses/{diagnosis.diagnosis_id}/confirm",
        json={"confirmed": True},
        headers=_bearer(other_key),
    )

    assert response.status_code == 404
    reloaded = storage.get_diagnosis(diagnosis.diagnosis_id)
    assert reloaded is not None and reloaded.confirmed is False
    assert client.get("/traces/t_acme", headers=_bearer(acme_key)).status_code == 200


def test_deactivated_tenant_is_403_not_404(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    storage, manager = state
    tenant_id, key = _seed_tenant(manager, "acme")
    storage.save_trace(_trace("t_acme", tenant_id))

    assert manager.set_tenant_active(tenant_id, False) is True

    assert client.get("/traces", headers=_bearer(key)).status_code == 403


def test_new_records_are_stamped_with_the_calling_tenant(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    storage, manager = state
    tenant_id, key = _seed_tenant(manager, "acme")
    storage.save_trace(_trace("t_acme", tenant_id))

    response = client.post("/traces/t_acme/flag", json={"note": "mine"}, headers=_bearer(key))

    assert response.status_code == 200
    flag = storage.list_flags_for_trace("t_acme")[0]
    assert flag.tenant_id == tenant_id
    assert flag.tenant_id != DEFAULT_TENANT_ID


# ─── Criterion 5: create-tenant returns a tenant_id ─────────────────────────


def test_cli_create_tenant_prints_the_new_tenant_id(auth_on: None) -> None:
    runner = CliRunner()

    result = runner.invoke(cli_app, ["auth", "create-tenant", "--name", "My Team"])

    assert result.exit_code == 0, result.output
    tenant_id = result.stdout.strip().splitlines()[-1]
    assert len(tenant_id) == 12

    listed = runner.invoke(cli_app, ["auth", "list-tenants"])
    assert listed.exit_code == 0, listed.output
    assert "My Team" in listed.stdout
    assert tenant_id in listed.stdout


# ─── Criterion 6: create-key shows the plaintext exactly once ───────────────


def test_cli_create_key_prints_plaintext_once_and_never_again(auth_on: None) -> None:
    """The key is printed to stdout exactly once; the DB only keeps the hash."""
    reset_storage()  # Ensure fresh singleton for this test
    runner = CliRunner()
    created = runner.invoke(cli_app, ["auth", "create-tenant", "--name", "Acme"])
    tenant_id = created.stdout.strip().splitlines()[-1]

    minted = runner.invoke(cli_app, ["auth", "create-key", "--tenant", tenant_id, "--name", "ci"])
    assert minted.exit_code == 0, minted.output
    plaintext = minted.stdout.strip().splitlines()[-1]
    assert plaintext.startswith(API_PREFIX)
    assert len(plaintext) > len(API_PREFIX) + 20

    # `list-keys` identifies the key without ever reprinting the secret...
    listed = runner.invoke(cli_app, ["auth", "list-keys", "--tenant", tenant_id])
    assert listed.exit_code == 0, listed.output
    assert plaintext not in listed.stdout
    assert plaintext[:12] in listed.stdout
    assert "ci" in listed.stdout

    # ...and the secret is nowhere in the database either.
    from forensics.storage import get_storage

    storage = get_storage()
    with storage._conn() as conn:
        stored = conn.execute("SELECT key_hash, prefix FROM api_keys").fetchall()
    assert len(stored) == 1
    assert stored[0][0] != plaintext
    assert plaintext not in stored[0][0]
    assert stored[0][1] == plaintext[:12]


def test_cli_create_key_for_unknown_tenant_fails_cleanly(auth_on: None) -> None:
    result = CliRunner().invoke(cli_app, ["auth", "create-key", "--tenant", "nope", "--name", "x"])

    assert result.exit_code == 1
    assert "Unknown tenant" in result.output


def test_cli_revoke_key_stops_validation(auth_on: None, state: tuple[Storage, AuthManager]) -> None:
    """The CLI writes to the same database the API reads, so revoking works."""
    storage, manager = state
    # Ensure the CLI uses the same storage singleton
    reset_storage()
    forensics.storage._storage_instance = storage

    tenant = manager.create_tenant("Acme")
    api_key, key = manager.create_api_key(tenant.tenant_id)
    storage.save_trace(_trace("t_acme", tenant.tenant_id))

    assert TestClient(api.app).get("/traces", headers=_bearer(key)).status_code == 200
    revoked = CliRunner().invoke(cli_app, ["auth", "revoke-key", "--key-id", api_key.key_id])
    assert revoked.exit_code == 0, revoked.output
    assert TestClient(api.app).get("/traces", headers=_bearer(key)).status_code == 401
    assert (
        CliRunner().invoke(cli_app, ["auth", "revoke-key", "--key-id", api_key.key_id]).exit_code
        == 1
    )


# ─── Criterion 7: bcrypt hashing, prefix for identification ─────────────────


def test_keys_are_bcrypt_hashed(auth_on: None, state: tuple[Storage, AuthManager]) -> None:
    _, manager = state
    tenant = manager.create_tenant("Acme")

    api_key, plaintext = manager.create_api_key(tenant.tenant_id)

    assert api_key.key_hash.startswith("$2b$")
    assert len(api_key.key_hash) == 60
    assert api_key.key_hash != plaintext
    assert plaintext not in api_key.key_hash
    assert api_key.prefix == plaintext[:12]
    assert api_key.name is None


def test_prefix_and_revocation_state_are_reported_by_list_keys(
    auth_on: None, state: tuple[Storage, AuthManager]
) -> None:
    _, manager = state
    tenant = manager.create_tenant("Acme")
    api_key, plaintext = manager.create_api_key(tenant.tenant_id, name="ci")

    assert [k.key_id for k in manager.list_keys(tenant.tenant_id)] == [api_key.key_id]
    assert manager.list_keys(tenant.tenant_id)[0].prefix == plaintext[:12]

    assert manager.revoke_key(api_key.key_id) is True
    assert manager.revoke_key(api_key.key_id) is False  # already revoked
    assert manager.list_keys(tenant.tenant_id)[0].revoked_at is not None
    assert manager.validate_api_key(plaintext) is None


def test_validation_records_last_used_at(auth_on: None, state: tuple[Storage, AuthManager]) -> None:
    _, manager = state
    tenant = manager.create_tenant("Acme")
    api_key, plaintext = manager.create_api_key(tenant.tenant_id)

    assert manager.get_key(api_key.key_id) is not None
    assert manager.get_key(api_key.key_id).last_used_at is None  # type: ignore[union-attr]

    assert manager.validate_api_key(plaintext) is not None

    used = manager.get_key(api_key.key_id)
    assert used is not None and used.last_used_at is not None


def test_validation_does_not_leak_which_part_failed(
    auth_on: None, state: tuple[Storage, AuthManager]
) -> None:
    """Unknown prefix, wrong secret, and revoked all return None identically."""
    _, manager = state
    tenant = manager.create_tenant("Acme")
    api_key, plaintext = manager.create_api_key(tenant.tenant_id)

    unknown_prefix = manager.validate_api_key("zzz_" + plaintext[4:])
    wrong_secret = manager.validate_api_key(plaintext[:12] + "X" * (len(plaintext) - 12))
    manager.revoke_key(api_key.key_id)
    revoked = manager.validate_api_key(plaintext)

    assert unknown_prefix is None
    assert wrong_secret is None
    assert revoked is None


def test_auth_manager_rejects_an_empty_prefix(state: tuple[Storage, AuthManager]) -> None:
    storage, _ = state

    with pytest.raises(ValueError):
        AuthManager(storage, prefix="")


# ─── Criterion 8: existing data migrates to tenant_id='default' ─────────────


def _legacy_database(path: Path) -> None:
    """A database with the pre-multi-tenancy schema and one row in it."""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE traces (
            trace_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            total_latency_ms INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            total_cost_usd REAL DEFAULT 0.0,
            pipeline_version TEXT,
            schema_version INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        "INSERT INTO traces VALUES ('t_legacy', 'doc_001', 'success', "
        "'2026-01-01T00:00:00+00:00', NULL, 5, 7, 0.0, '0.1.0', 1)"
    )
    conn.commit()
    conn.close()


def test_legacy_rows_get_the_default_tenant(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite"
    traces_dir = tmp_path / "traces"
    _legacy_database(db_path)
    # A legacy deployment has both halves: the SQLite index row, and the JSON
    # payload it points at.
    traces_dir.mkdir(parents=True)
    (traces_dir / "t_legacy.json").write_text(
        json.dumps({"trace_id": "t_legacy", "document_id": "doc_001", "spans": []})
    )

    storage = Storage(traces_dir=traces_dir, db_path=db_path)

    with storage._conn() as conn:
        row = conn.execute(
            "SELECT tenant_id, document_id FROM traces WHERE trace_id = 't_legacy'"
        ).fetchone()
    # The pre-existing columns must survive the ALTER.
    assert row == (DEFAULT_TENANT_ID, "doc_001")

    migrated = storage.get_trace("t_legacy", tenant_id=DEFAULT_TENANT_ID)
    assert migrated is not None and migrated.document_id == "doc_001"
    # Reachable by its new owner, invisible to every other tenant.
    assert storage.get_trace("t_legacy", tenant_id="acme") is None


def test_migration_creates_the_auth_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite"
    _legacy_database(db_path)

    Storage(traces_dir=tmp_path / "traces", db_path=db_path)

    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    conn.close()

    assert {"tenants", "api_keys"} <= tables
    assert "idx_api_keys_prefix" in indexes
    assert "idx_traces_tenant" in indexes


def test_migration_is_idempotent(tmp_path: Path) -> None:
    from forensics.migrations import migrate

    db_path = tmp_path / "legacy.sqlite"
    _legacy_database(db_path)
    Storage(traces_dir=tmp_path / "traces", db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        # A second pass must report nothing to do: the columns already exist.
        assert migrate(conn) == []
    finally:
        conn.close()


def test_saving_a_new_trace_records_its_tenant(tmp_path: Path) -> None:
    """The finalize path (not just the schema) must persist tenant_id."""
    db_path = tmp_path / "db.sqlite"
    storage = Storage(traces_dir=tmp_path / "traces", db_path=db_path)
    storage.save_trace(_trace("t_acme", "acme"))

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT tenant_id FROM traces WHERE trace_id = 't_acme'").fetchone()
    finally:
        conn.close()

    assert row == ("acme",)


def test_default_tenant_can_be_seeded_for_the_cli(
    auth_on: None, state: tuple[Storage, AuthManager]
) -> None:
    """An operator turning auth on must be able to key their existing data."""
    _, manager = state

    tenant = manager.ensure_default_tenant()

    assert tenant.tenant_id == DEFAULT_TENANT_ID
    _, plaintext = manager.create_api_key(DEFAULT_TENANT_ID, name="migrated")
    assert manager.validate_api_key(plaintext) is not None
    # Idempotent: calling it again must not raise a UNIQUE violation.
    assert manager.ensure_default_tenant().tenant_id == DEFAULT_TENANT_ID


def test_flag_and_eval_case_inherit_the_writing_tenant(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    storage, manager = state
    tenant_id, key = _seed_tenant(manager, "acme")
    storage.save_trace(_trace("t_acme", tenant_id))
    diagnosis = storage.create_diagnosis(_diagnosis("t_acme", tenant_id))
    flag = Flag(trace_id="t_acme", user_id="api_user", tenant_id=tenant_id)
    storage.create_flag(flag)

    response = client.post(
        f"/diagnoses/{diagnosis.diagnosis_id}/confirm",
        json={"confirmed": True},
        headers=_bearer(key),
    )

    assert response.status_code == 200
    cases = storage.list_eval_cases(tenant_id=tenant_id)
    assert len(cases) == 1
    assert cases[0].tenant_id == tenant_id
    assert storage.list_eval_cases(tenant_id="other") == []


# ─── Scopes ─────────────────────────────────────────────────────────────────


def test_scope_semantics_on_the_model() -> None:
    """`*` is everything, `write` implies `read`, and nothing implies `write`."""
    from forensics.models import APIKey

    full = APIKey(tenant_id="t", key_hash="h", prefix="ffk_x", scopes=["*"])
    reader = APIKey(tenant_id="t", key_hash="h", prefix="ffk_x", scopes=["read"])
    writer = APIKey(tenant_id="t", key_hash="h", prefix="ffk_x", scopes=["write"])

    assert full.allows("read") and full.allows("write")
    assert reader.allows("read") and not reader.allows("write")
    assert writer.allows("write") and writer.allows("read")
    # A wildcard covers scopes it has never heard of; an explicit list does not.
    assert full.allows("admin")
    assert not reader.allows("admin")


def test_read_only_key_cannot_mutate(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    storage, manager = state
    tenant = manager.create_tenant("acme")
    _, key = manager.create_api_key(tenant.tenant_id, name="dashboard", scopes=["read"])
    storage.save_trace(_trace("t_acme", tenant.tenant_id))

    assert client.get("/traces", headers=_bearer(key)).status_code == 200
    assert client.get("/analytics", headers=_bearer(key)).status_code == 200

    response = client.post("/traces/t_acme/flag", json={}, headers=_bearer(key))

    assert response.status_code == 403
    assert "read" in response.json()["detail"]


def test_write_scope_can_mutate_and_read(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    """`write` implies `read`: a key that can start work must see its result."""
    storage, manager = state
    tenant = manager.create_tenant("acme")
    _, key = manager.create_api_key(tenant.tenant_id, name="ci", scopes=["write"])
    storage.save_trace(_trace("t_acme", tenant.tenant_id))

    assert client.get("/traces", headers=_bearer(key)).status_code == 200
    flagged = client.post("/traces/t_acme/flag", json={}, headers=_bearer(key))
    assert flagged.status_code == 200
    assert flagged.json()["trace_id"] == "t_acme"


def test_default_key_still_has_full_access(
    auth_on: None, state: tuple[Storage, AuthManager], client: TestClient
) -> None:
    """Omitting --scope must not quietly produce a crippled key."""
    storage, manager = state
    tenant = manager.create_tenant("acme")
    api_key, key = manager.create_api_key(tenant.tenant_id, name="legacy")
    storage.save_trace(_trace("t_acme", tenant.tenant_id))

    assert api_key.scopes == ["*"]
    assert client.get("/traces", headers=_bearer(key)).status_code == 200
    assert client.post("/traces/t_acme/flag", json={}, headers=_bearer(key)).status_code == 200


def test_unknown_scope_is_rejected_at_mint_time(
    auth_on: None, state: tuple[Storage, AuthManager]
) -> None:
    _, manager = state
    tenant = manager.create_tenant("acme")

    with pytest.raises(ValueError, match="Unknown scope"):
        manager.create_api_key(tenant.tenant_id, scopes=["read", "sudo"])

    assert frozenset({"read", "write", "*"}) == API_KEY_SCOPES


def test_scopes_survive_a_round_trip_through_sqlite(
    auth_on: None, state: tuple[Storage, AuthManager]
) -> None:
    _, manager = state
    tenant = manager.create_tenant("acme")
    api_key, _ = manager.create_api_key(tenant.tenant_id, scopes=["read"])

    stored = manager.get_key(api_key.key_id)
    assert stored is not None
    assert stored.scopes == ["read"]
    assert [k.scopes for k in manager.list_keys(tenant.tenant_id)] == [["read"]]


def test_migration_widens_an_existing_keys_table_without_breaking_it(
    tmp_path: Path, auth_on: None
) -> None:
    """A key minted before scopes existed must keep full access.

    The `api_keys` table is created by `CREATE TABLE IF NOT EXISTS`, so a
    deployed database never gains a new column from that statement; only the
    `ALTER TABLE` in `migrate()` does, and it has to default to `'*'`.
    """
    from forensics.migrations import migrate

    legacy = sqlite3.connect(tmp_path / "legacy.sqlite")
    legacy.execute(
        """
        CREATE TABLE api_keys (
            key_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            key_hash TEXT NOT NULL,
            prefix TEXT NOT NULL,
            name TEXT,
            created_at TEXT NOT NULL,
            revoked_at TEXT,
            last_used_at TEXT,
            expires_at TEXT
        )
        """
    )
    legacy.execute(
        "INSERT INTO api_keys (key_id, tenant_id, key_hash, prefix, created_at) "
        "VALUES ('k1', 'default', 'h', 'ffk_abc', '2024-01-01T00:00:00+00:00')"
    )
    legacy.commit()

    applied = migrate(legacy)
    legacy.commit()

    assert "api_keys.scopes" in applied
    row = legacy.execute("SELECT scopes FROM api_keys WHERE key_id = 'k1'").fetchone()
    assert row == ("*",)
    legacy.close()


def test_create_key_cli_accepts_scopes(auth_on: None, state: tuple[Storage, AuthManager]) -> None:
    storage, manager = state
    # The CLI builds its own AuthManager from the process-wide storage singleton,
    # so point that at the same database the test is inspecting.
    reset_storage()
    forensics.storage._storage_instance = storage

    tenant = manager.create_tenant("acme")
    runner = CliRunner()

    result = runner.invoke(
        cli_app, ["auth", "create-key", "--tenant", tenant.tenant_id, "--scope", "read"]
    )

    assert result.exit_code == 0, result.output
    stored = manager.list_keys(tenant.tenant_id)
    assert [k.scopes for k in stored] == [["read"]]

    invalid = runner.invoke(
        cli_app, ["auth", "create-key", "--tenant", tenant.tenant_id, "--scope", "sudo"]
    )
    assert invalid.exit_code == 1
