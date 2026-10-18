"""The SPA catch-all route must not serve files outside `frontend/dist`.

The route is unauthenticated. Before the guard, `serve_frontend_spa` joined the
request path onto the dist directory and served the result whenever it was a
file, so a URL-encoded `..` segment reached `.env`, `data/forensics.db`, or any
file the process could read. These tests pin the containment check.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forensics import api


@pytest.fixture
def dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway `frontend/dist` plus a secret file one level above it."""
    root = tmp_path / "app"
    dist = root / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>ui</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('ui')", encoding="utf-8")

    (root / "secret.env").write_text("NVIDIA_API_KEY=nvapi-do-not-leak", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "forensics.db").write_text("sqlite-do-not-leak", encoding="utf-8")

    # The route reads the module global at call time, so pointing it at the
    # temp dist is enough — no reload, no import-order coupling.
    monkeypatch.setattr(api, "_dist_path", dist)
    return dist


@pytest.fixture
def client() -> TestClient:
    return TestClient(api.app)


def test_real_file_inside_dist_resolves(dist: Path) -> None:
    assert api._safe_static_target("index.html") == (dist / "index.html").resolve()
    assert api._safe_static_target("assets/app.js") == (dist / "assets" / "app.js").resolve()
    # A directory is not a file, and an absent path is not served.
    assert api._safe_static_target("assets") is None
    assert api._safe_static_target("nope.txt") is None


@pytest.mark.parametrize(
    "attack",
    [
        "/%2e%2e/secret.env",
        "/%2e%2e/%2e%2e/secret.env",
        "/%2e%2e/data/forensics.db",
        "/assets/%2e%2e/%2e%2e/secret.env",
        "/....//secret.env",
        "/%2e%2e%2fsecret.env",
    ],
)
def test_traversal_cannot_read_files_outside_dist(
    dist: Path, client: TestClient, attack: str
) -> None:
    response = client.get(attack)
    body = response.text
    assert "nvapi-do-not-leak" not in body, attack
    assert "sqlite-do-not-leak" not in body, attack


def test_unknown_route_falls_back_to_index(dist: Path, client: TestClient) -> None:
    response = client.get("/some-spa-route/extra")
    assert response.status_code == 200
    assert "ui" in response.text


def test_no_build_answers_404_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    missing = tmp_path / "no-frontend"
    monkeypatch.setattr(api, "_dist_path", missing)
    response = client.get("/anything")
    assert response.status_code == 404


def test_guard_rejects_symlink_escaping_dist(tmp_path: Path, dist: Path) -> None:
    """A symlink *inside* dist pointing out must not become a file-server hole."""
    link = dist / "escape.env"
    link.symlink_to(dist.parent.parent / "secret.env")

    assert api._safe_static_target("escape.env") is None
