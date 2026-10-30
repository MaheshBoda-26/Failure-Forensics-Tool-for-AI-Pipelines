# ADR 0017: Per-Tenant Queue Fairness and Dead-Lettering

## Status
Accepted

## Context
The worker queue (ADR 0015) is a single SQLite table with atomic claims. Any number
of `forensics worker start` processes can share it safely, but there is no
per-tenant fairness: a tenant that enqueues thousands of high-priority jobs delays
everyone else, and there is no dead-letter queue — a poison job that crashes every
attempt is retried up to `QUEUE_MAX_ATTEMPTS` and then left `failed`, which is
fine, but there is no distinct terminal state for "exhausted retries, do not retry"
versus "failed on the last attempt".

`docs/limitations.md` §17 captured this as "no orchestrator or fairness". This ADR
adds the two lightweight levers the plan calls for: an optional per-tenant inflight
cap and a dead-letter state. Both are opt-in and keep the existing default
behaviour unchanged.

## Decision
- **`QUEUE_MAX_INFLIGHT_PER_TENANT`** (`int`, default `0` = unlimited): when set,
  `Queue.dequeue` excludes tenants whose `pending`+`running` job count is `>= cap`
  from the atomic claim subquery (both SQLite and PostgreSQL). One tenant cannot
  starve the queue, and the exclusion is part of the same atomic `UPDATE … RETURNING`
  so two workers cannot both claim past a tenant's cap. No broker, no weighted fair
  queueing — a coarse cap is enough for a forensics tool.
- **`JobStatus.DEAD`**: a new terminal state. When `QUEUE_DEAD_LETTER_ENABLED` is
  `true` (default) and a job exhausts `QUEUE_MAX_ATTEMPTS`, `Queue.fail` marks it
  `dead` (payload cleared) instead of `failed`. A poison job stops retrying forever.
  Set `QUEUE_DEAD_LETTER_ENABLED=false` to keep the historical `failed` terminal
  state.
- **`Queue.stats()`**: counts by status, oldest pending age, and per-tenant
  in-flight (pending+running). Exposed via `GET /queue/stats`.
- **CLI**: `forensics queue stats` and `forensics queue requeue --dead` (bulk,
  explicit requeue of dead-lettered jobs after an outage is resolved).

## Rationale
- A queue that needs an orchestrator to be useful is not worth adding to a
  single-service demo; a coarse per-tenant cap is the smallest thing that prevents
  starvation.
- Dead-lettering only jobs that exhaust `max_attempts` preserves the retry
  behaviour for transient failures while stopping poison jobs.
- Both knobs default to current behaviour (`0` = unlimited, dead-letter on), so no
  existing deployment changes behaviour unless it opts in.

## Consequences
- Still no Redis/Celery/broker, no autoscaling, no health supervision (§17 remains
  "no orchestrator", now mitigated by the cap).
- One writer at a time (SQLite); throughput bounded by write frequency.
- `dead` is a new terminal state, so `JobStatus` and `counts()`/`stats()` include
  it; the `queue list --status` filter accepts `dead`.
- Dead-lettered jobs are not reclaimed by the reaper (only `running` jobs are).

## Consequences (verification)
- `tests/test_queue.py`: tenant-at-cap does not starve another tenant, cap=0 is
  unlimited, exhausting `max_attempts` → `dead` (and → `failed` when dead-letter
  is disabled), `stats()` counts match inserts.
- `docs/limitations.md` §17 updated from "no fairness" to coarse per-tenant cap +
  dead-letter.
