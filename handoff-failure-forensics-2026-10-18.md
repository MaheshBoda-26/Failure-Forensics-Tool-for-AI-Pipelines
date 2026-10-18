# Handoff — Failure Forensics Tool (fix-and-finish plan)

**Date:** 2026-10-18
**Repo:** `/Users/maheshboda/Projects/Major project` (GitHub: `MaheshBoda-26/Failure-Forensics-Tool-for-AI-Pipelines`)
**Branch:** `main`, HEAD `9417a32`, working tree clean
**Remote state:** verified `origin/main == HEAD == 9417a32` via `git ls-remote origin main` (the commits are on GitHub; Render can use the repo directly).

---

## 1. What this session did

Executed `plan/forensics-fix-and-finish.md` end to end: unblock `make check`, then finish specs
05, 03/02 test debt, 04, 06, 07, the deploy config, and a documentation truth pass.

All 7 phases are complete and committed; **1 item (T17) is blocked on Render credentials.**

| Phase | Commit | Content |
|---|---|---|
| 0–3 | `489452f` | check-suite unblock (registry/cycle/mypy), specs 05, 03, 02, 04 (auth + multi-tenancy) |
| 4 | `8243440` | spec 06 — scheduled eval + webhook regression alerts |
| 5 | `16b1bd2` | spec 07 — SQLite worker queue + pipeline worker |
| 6 | `cb373e9` | deploy config + the six defects that made it dishonest |
| 7 | `9417a32` | docs truth pass, ADRs 0013–0015, idle config knobs made real |

**Evidence:** 49 files changed, +7736/−626. `make check`: **344 tests pass**, ruff clean, mypy
strict clean (was 86 pass / 1 fail / 2 uncollectable modules at `e6c0961`).

Do not re-read the whole diff first — read these instead, in order:

1. `tasks/todo.md` — phase-by-phase decisions, each noted defect, and the reasoning behind every
   deliberate divergence from the specs.
2. `docs/README.md` — documentation index (specs → test files, all ADRs, how to verify claims).
3. `docs/limitations.md` — the 17 real, current limits plus a "Resolved in this revision" table.
4. `git log -5` — the commit bodies carry the same rationale with file-level detail.

---

## 2. Verification commands (all currently green)

```bash
make check                     # lint + typecheck + test (deps: make install)
make test                      # pytest, live-model tests deselected
scripts/smoke.sh http://127.0.0.1:8000   # full loop over HTTP against a running server
```

Clean-checkout proof (the plan's gate — `.[dev]` only, no optional extras):

```bash
rm -rf /tmp/ff-clean-venv && uv venv --python 3.12 /tmp/ff-clean-venv
VIRTUAL_ENV=/tmp/ff-clean-venv uv pip install -q -e ".[dev]"
/tmp/ff-clean-venv/bin/ruff check src tests streamlit_app.py
/tmp/ff-clean-venv/bin/mypy
/tmp/ff-clean-venv/bin/python -m pytest -m "not live"
```

Manual end-to-end run (what was used repeatedly during the session; scratch data outside the repo):

```bash
export DATA_DIR=/tmp/ff TRACES_DIR=/tmp/ff/traces CACHE_DIR=/tmp/ff/cache \
       DB_PATH=/tmp/ff/forensics.db SPEND_FILE=/tmp/ff/spend.json \
       SCHEDULER_DB_PATH=/tmp/ff/scheduler.db DRY_RUN=true
.venv/bin/uvicorn forensics.api:app --host 127.0.0.1 --port 8099 &
./scripts/smoke.sh http://127.0.0.1:8099
```

`DRY_RUN=true` is mandatory for offline work — `.env` has `DRY_RUN=false`, so an unset demo
command will attempt real NIM calls and spend the owner's quota. (An `.env` with a real
`NVIDIA_API_KEY` exists in the repo; never print, copy, or commit it.)

---

## 3. The one blocked item: T17, live Render deploy

**Status:** not deployed. This environment has no Render account, CLI, or credentials
(`which render` → not found; no `RENDER_*` in env or `.env`).

**Verified without an account** (so the deploy is a copy-paste, not a guess):

- `render.yaml` parses (YAML), `Procfile` is one line, `scripts/smoke.sh` is executable.
- Build command half 1: `pip install ".[otel]"` — validated in a clean venv.
- Build command half 2: `frontend/npm ci && npm run build` — both succeed from a clean checkout.
- Start command: `make run-web` → API **and** compiled SPA served from `frontend/dist`, `/health` 200.
- `scripts/smoke.sh` passes the whole `health → seed → flag → diagnose → confirm → analytics`
  loop anonymously (as the blueprint deploys) **and** with `AUTH_ENABLED=true` + a real `ffk_` key;
  with no key, every data route answers 401 and only `/health` is open.
- Queue integration with a real process: `POST /pipeline/run` → 202 + `job_id`, `forensics worker
  start` claimed and completed it (trace + diagnosis written), exited cleanly on SIGTERM.
- Scheduler in the API lifespan: `/scheduler/status` reported `running: true` with a next run time,
  and `apscheduler_jobs` held the row.

**To finish (needs the owner or a Render API key):**

1. Render → New → Blueprint → this repo → set `NVIDIA_API_KEY` in the dashboard (`sync: false`).
2. `./scripts/smoke.sh https://<service>.onrender.com` — expect all checks to pass.
3. Flip `EVAL_SCHEDULE`/`ALERT_WEBHOOK_URL` only if a webhook receiver exists; the instance must
   stay awake for a nightly job (free tier spins down).

Caveats already documented in `README.md` (do not "fix" them silently): the persistent disk
requires a paid instance (`plan: starter` is declared for that reason — the free variant needs the
`disk:` block deleted and data re-seeded), and the bundled UI cannot send an API key, so the
public demo deploys with `AUTH_ENABLED=false` (`docs/limitations.md` §1).

---

## 4. Environment pitfalls that cost real time (read before editing)

- **pydantic-settings drops unknown env vars, and keys aliased values by the ALIAS string.**
  A nested `Settings` field is filled only from `OTEL__EXPORTER`; flat `OTEL_EXPORTER` is silently
  ignored unless a matching field with `validation_alias` exists, and then the raw `data` dict is
  keyed `"OTEL_EXPORTER"`, not `otel_exporter`. This was the cause of the "OTEL_* does nothing" bug.
  See `build_otel_settings` in `src/forensics/config.py` for the working pattern.
- **mypy only checks the package** (`packages = ["forensics"]`): test files are not type-checked, so
  `# type: ignore` comments in `tests/` are inert. Prefer explicit annotations over ignores.
- **ruff `B008` exempts `= typer.Option(...)` only for immutable annotations** (str/int/bool/…). A
  `Path`-annotated parameter must use `Annotated[Path, typer.Option(...)]` — see `queue_enqueue` in
  `src/forensics/cli.py`.
- **detect-secrets flags any line pairing a keyword (`API_KEY`, `SECRET`, …) with a quoted literal**,
  and pre-commit runs it on commit. Keep the keyword and its literal on separate lines (module-level
  constant) or add `# pragma: allowlist secret`; `# noqa` for `S608`-class rules would trip RUF100
  because the `S` (bandit) rules are not selected.
- **pre-commit hooks modify files during `git commit`** (ruff format/fix, EOF, whitespace). If a
  commit fails, re-stage and commit again — the first attempt's changes are in the working tree.
- **`npm ci` requires `package-lock.json` and `package.json` to agree exactly**; they did not at HEAD
  and were realigned. If you touch `frontend/package.json`, run `npm install` to resync the lock.
- **`make install` is now idempotent** (`uv venv --allow-existing`); it previously failed for anyone
  who already had a `.venv`.
- macOS has no GNU `timeout`; use background + `kill -TERM`, as done for worker/SIGTERM checks.
- **`tests/conftest.py` sets `TRACES_DIR`/`DB_PATH` per test but does not reset `api._storage`**;
  API tests monkeypatch it explicitly (`monkeypatch.setattr(api, "_storage", ...)`).
- The cached-settings singleton (`get_settings`) is process-global: any test that varies settings
  must call `reset_settings()` (fixtures in `tests/test_auth.py`, `test_scheduler.py`, `test_queue.py`
  show the pattern).

---

## 5. Known-good decisions — do not re-litigate without reading the ADRs

- `bcrypt`, `apscheduler`, `sqlalchemy` are **required** dependencies (not extras); `otel` remains an
  extra with a mypy override. Rationale: `docs/adr/0013-auth-multitenant.md`,
  `docs/adr/0014-scheduled-eval-alerts.md`.
- Dev mode (`AUTH_ENABLED=false`) applies **no** tenant filter by design; auth mode filters in SQL so
  `limit`/`offset` stay meaningful, and cross-tenant reads 404 from the index.
- Queue payloads are cleared at terminal state so raw document text is not retained (spec 03 promise)
  — `docs/adr/0015-worker-queue.md` §"payload".
- Endpoints take `authorization: Annotated[str | None, Header()]` and call `get_current_tenant(...)`
  rather than using `Depends`, because tests call endpoint functions directly and a `Depends` default
  leaks a `Depends` object into those calls. Trade-off: no padlock in OpenAPI.
- Specs 04/06/07 each end with an "Implementation notes" section listing where the shipped code
  deliberately differs from the original sketch (with reasons). Keep that pattern when revising specs.

---

## 6. Open risks / things not yet true

- **No live-model verification was run in this session.** All pipeline checks used `DRY_RUN=true`;
  `pytest -m live` was deliberately not executed (quota). Real hallucination/refusal/structured-output
  behaviour is still only covered by mock-transport tests.
- **Render deploy unverified** (§3).
- **The bundled UI cannot authenticate** (`docs/limitations.md` §1) — with `AUTH_ENABLED=true` the SPA
  shows empty views; a key field in `frontend/src/api/client.ts` is a small, unstarted task.
- **Judge/model prices in `.env` are `0.0`**, so `total_cost_usd` and the per-step `judge` attribution
  read zero until real prices are filled in (`docs/limitations.md` §10).
- **No reaper for jobs whose worker died** (§2) — a SIGKILLed worker leaves its job `running`.
- The repo's gitignored `data/` was used for local demo runs: **149 traces, 15 eval cases, 1 stale
  `pending` job row, 0 API keys, 0 tenants.** Safe to leave, but a fresh agent may want
  `rm -rf data && make install`-style cleanup before a demo so numbers look intentional.
- No PR was created; work is direct on `main`. If a reviewer is wanted, the diff worth reviewing is
  the five commits above (a second-opinion review is a good idea before the deploy).

---

## 7. Suggested skills for the next session

| Skill | Why |
|---|---|
| `verification-before-completion` | The next milestone is a deploy claim; prove it before saying it works. |
| `setup-deploy` → `land-and-deploy` | Configure and drive the Render/CI deployment path for T17. |
| `browse` (or `qa` / `qa-only`) | Exercise the live URL in a real browser once deployed, including the SPA. |
| `code-review` (or `gpt-review` / `fable-review`) | Independent review of the five commits before shipping. |
| `github` | `gh` flows for pushing, PR creation, and Actions runs for this repo. |
| `systematic-debugging` / `test-fixing` | If the clean-checkout gate or CI goes red. |
| `handoff` | If the session ends mid-deploy, regenerate this document. |

---

## 8. Sensitive information

No secrets appear in this document, and none were written into the repository. Points to respect:

- `.env` holds the real `NVIDIA_API_KEY`; it is gitignored. Never print or copy it, and do not run
  live-model commands casually (prices/spend caps are configured, but the key is the owner's).
- API keys minted during verification were written to scratch databases under `/tmp`, which were
  deleted (`/tmp/ff-smoke`, `/tmp/ff-final`, `/tmp/ff-clean-venv`, `/tmp/ff-head` may still exist as
  venv/scratch dirs — safe to remove). The repository's `data/forensics.db` contains **no** API keys.
- `frontend/node_modules` and `data/` are runtime directories, not artifacts to commit.
