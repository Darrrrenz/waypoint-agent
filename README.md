# waypoint-agent

An evaluation-driven personal agent runtime built with Python 3.12+, Pydantic v2, and
PostgreSQL. The default read-only workflow runs against deterministic mock data:

> Find my meeting with Alice next week and tell me whether I have any related unread emails.

The model proposes actions; the runtime validates, executes, records, and verifies them
before accepting completion. Both a credential-free scripted adapter and an OpenAI-compatible
adapter use the same runtime. No agent framework is involved.

Day 2 adds **“Move my meeting with Alice to Friday afternoon.”** against an explicitly
seeded persistent mock calendar. It pauses for a human decision, resumes in a new process,
revalidates the exact approved operation, and verifies the result through an event read.

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

## Day 2 approval demo

Start PostgreSQL and initialize the database using the commands above, then:

```sh
uv run waypoint-agent seed-calendar
# Copy the printed calendar-world UUID into the next command.
uv run waypoint-agent run --workflow reschedule --world-id <world-id>
uv run waypoint-agent approvals <task-id>
uv run waypoint-agent approve <task-id> <approval-id>
uv run waypoint-agent resume <task-id> --export artifacts/reschedule-trajectory.json
uv run waypoint-agent inspect <task-id>
```

`run` exits with `waiting_for_approval` and leaves the calendar unchanged. `approve` records
the decision; only `resume` executes it. Use `deny` instead of `approve` to refuse the change,
then `resume` to finalize denial. Commands support JSON inspection with `--json`.

The fixture's target is **September 25, 2026, 13:00–13:30 America/Toronto**. Search scope
and destination scope are separate; scheduling preserves duration and picks the earliest
conflict-free slot in 13:00–17:00 at 15-minute increments. Identity/date options are explicit;
this is not general natural-language date parsing.

Approval bindings, event revisions, a transactional operation ledger, and task locks protect
the mock mutation. Restart preserves model progress, budgets, and ordered trajectory events.
Stale revisions or new conflicts require a new proposal and approval. These guarantees apply
to the transactional mock store, not future external calendar APIs.

Without PostgreSQL, preview the approval or generate the synthetic test example:

```sh
uv run waypoint-agent run --workflow reschedule --ephemeral
uv run python scripts/export_reschedule.py --memory --output artifacts/reschedule-example.json
```

Ephemeral state cannot be resumed after exit. The export harness supplies an explicit synthetic
decision for its own mock task; normal CLI runs never approve themselves. See the complete
[Day 2 guide](docs/day2.md) and [generated trajectory](docs/day2-example-trajectory.json).

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pre-commit install
```

Database tests require `WAYPOINT_TEST_DATABASE_URL`; otherwise they explicitly skip. Live
mode requires `WAYPOINT_API_KEY` and a compatible model/endpoint; see `.env.example`.

Local Day 2 validation: **79 tests passed, 10 PostgreSQL tests skipped**. Both credential-free
workflows, Ruff lint/format, pre-commit, and wheel/source builds passed. PostgreSQL initialization
was probed and failed with `ConnectionTimeout`; Docker/PostgreSQL infrastructure and model
credentials are unavailable. Database durability, subprocess integration tests, and live
inference therefore remain unverified locally. CI runs the database tests and exports both
completed workflows.

Read the [historical Day 1 guide](docs/day1.md) and [Day 1 example](docs/example-trajectory.json).
Real integrations, a web interface, advanced memory, and multiple agents remain deferred.
