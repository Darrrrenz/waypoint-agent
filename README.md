# waypoint-agent

An evaluation-driven personal agent runtime built with Python 3.12+, Pydantic v2, and
PostgreSQL. Day 1 runs this workflow against deterministic mock data:

> Find my meeting with Alice next week and tell me whether I have any related unread emails.

The model proposes actions; the runtime validates, executes, records, and verifies them
before accepting completion. Both a credential-free scripted adapter and an OpenAI-compatible
adapter use the same runtime. No agent framework is involved.

## Quick start

From this directory, with Python and uv installed:

```sh
uv sync --frozen
uv run waypoint-agent run --ephemeral --export artifacts/trajectory.json
```

For persistent runs, with Docker running:

```sh
docker compose up -d --wait postgres
uv run waypoint-agent init-db
uv run waypoint-agent run
uv run waypoint-agent inspect <task-id>
```

The demo finds **Waypoint launch review with Alice**, September 22, 2026 at 10:00 EDT
(`cal-alice-001`), and **Launch review agenda** (`mail-alice-unread`). Read mail and
unrelated unread mail are excluded. The fixture clock is fixed to September 20, 2026;
“next week” means the next Monday-to-Sunday period in America/Toronto.

## What Day 1 includes

- Read-only calendar search, email search, and email read tools with validated schemas.
- Structured actions and evidence-backed completion, including absence and ambiguity.
- Step/call limits, per-call timeouts, execution deadline, and bounded error handling.
- Atomic PostgreSQL checkpoints, ordered trajectories, and an inspection CLI.
- Credential-free tests, Ruff, pre-commit, a dependency lockfile, Docker, and PostgreSQL CI.

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pre-commit install
```

Database tests require `WAYPOINT_TEST_DATABASE_URL`; otherwise they explicitly skip. Live
mode requires `WAYPOINT_API_KEY` and a compatible model/endpoint; see `.env.example`.

Local validation: **39 tests passed, 3 PostgreSQL tests skipped**. The scripted CLI demo,
Ruff, pre-commit hooks, and package build passed. Docker/PostgreSQL execution and live model inference remain unverified
locally because the infrastructure and credentials are unavailable.

Read [setup, architecture, decisions, and limitations](docs/day1.md) and inspect the
[actual example trajectory](docs/example-trajectory.json). Crash recovery, real integrations,
approvals, a web interface, and advanced memory are intentionally deferred.
