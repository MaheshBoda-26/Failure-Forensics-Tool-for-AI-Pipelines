# ADR 0005: Backend Framework — FastAPI

## Status
Accepted

## Context
The REST feedback API is required. FastAPI gives async, automatic OpenAPI, Pydantic integration, and can serve the React build.

## Decision
Use FastAPI for the entire backend: pipeline execution, trace queries, flagging, diagnosis, eval, analytics, and static file serving for the React frontend.

## Rationale
- The brief explicitly requires a REST API for feedback.
- FastAPI + Pydantic = zero-boilerplate request/response validation.
- Single service on Render: API + static assets, no separate frontend hosting.
- Async support needed for concurrent pipeline runs and OTel export.

## Consequences
- No separate API gateway or reverse proxy.
- Uvicorn as the ASGI server.
- OpenAPI spec at `/openapi.json` for frontend client generation.

## Verification
- `make dev` starts FastAPI on `127.0.0.1:8000`
- `/openapi.json` returns valid spec
- `pytest tests/test_api.py` exercises all endpoints
