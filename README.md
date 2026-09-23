# waypoint-agent

A small, evidence-verified personal agent runtime built with Python 3.12+, Pydantic v2,
and PostgreSQL. It supports meeting/email retrieval and approval-gated rescheduling against
deterministic mock data, with persistent preference memory and an independent benchmark.

The model proposes actions. The runtime validates, executes, records, and verifies them.
Calendar writes require an explicit decision bound to the exact change. There is no agent
framework, real account integration, web server, or vector database.

## Quick start

```sh
uv sync --frozen
uv run waypoint-agent run --ephemeral --export artifacts/trajectory.json
uv run waypoint-agent benchmark --suite benchmarks/core.json --backend memory --output artifacts/benchmark
```

On Windows, if `uv` is not on PATH, use `.\.venv\Scripts\uv.exe` in its place.
The default workflow finds the September 22, 2026 meeting with Alice (`cal-alice-001`) and
reads its related unread message (`mail-alice-unread`). Read and unrelated mail are excluded.
The semantic clock is fixed to September 20, 2026 in America/Toronto.

## What the runtime provides

- Typed tools and evidence-backed findings, including missing and ambiguous meetings.
- Persisted execution budgets, bounded read retries, checkpoints, and ordered trajectories.
- Exact approval bindings, revision checks, conflict checks, and an idempotent operation ledger.
- Recovery when a calendar write commits before its observation is persisted.
- Explicit earliest-meeting preferences and compact verified episodes in separate agent memory.
- A 16-scenario benchmark with independent calendar checks, JSON/Markdown reports, and CI artifacts.
- Credential-free scripted/demo models and an opt-in OpenAI-compatible adapter for ordinary runs.

## Offline trace viewer

Read a single-task trajectory export as text or Markdown, without a database, Docker,
model credentials, or network access:

```sh
uv run waypoint-agent trace --input docs/example-trajectory.json
uv run waypoint-agent trace --input docs/day2-example-trajectory.json --format markdown --output artifacts/reschedule-trace.md
```

Text goes to stdout by default; `--output` creates parent directories as needed. The viewer
validates the existing checkpoint/event schemas, task IDs, and contiguous sequence numbers.
Invalid or inconsistent exports exit nonzero. It keeps proposals, approval decisions,
operation attempts/results, and memory projection outcomes distinct, with source IDs and
counts instead of full tool/model payloads. `inspect` and JSON exports are unchanged.

See the [rendered Day 2 trace](docs/trace-example.md), generated from the existing **synthetic,
in-memory example**. To regenerate it, use the second command with `--output docs/trace-example.md`.
The viewer summarizes recorded evidence; it does not re-execute verification or authenticate
exports. Aggregated benchmark reports and `docs/benchmark-example/memory-pair.json` are not
supported. Unknown event kinds remain visible with a generic description.

## Persistent approval and memory demo

Start the development database and initialize its additive tables:

```sh
docker compose up -d --wait postgres
uv run waypoint-agent init-db
uv run waypoint-agent memory set --namespace demo --earliest 15:00 --timezone America/Toronto
uv run waypoint-agent memory list --namespace demo
uv run waypoint-agent seed-calendar
```

Use the printed world UUID. Each command runs in a fresh process:

```sh
uv run waypoint-agent run --workflow reschedule --world-id <world-id> --memory-namespace demo
uv run waypoint-agent approvals <task-id>
uv run waypoint-agent approve <task-id> <approval-id>
uv run waypoint-agent resume <task-id> --export artifacts/memory-trajectory.json
uv run waypoint-agent memory episodes --namespace demo
uv run waypoint-agent inspect <task-id>
```

The free Friday 13:00–17:00 window now yields **15:00–15:30 America/Toronto**, and approval
names that exact change. Use `deny` instead of `approve` to leave the calendar unchanged.
The default strategy is `structured`; `--memory-strategy disabled` selects 13:00 in the same
starting calendar. Existing worlds are never reseeded or overwritten.

Only explicit `memory set` input creates preferences. Tasks save the selected record ID,
revision, and effective constraint; later preference changes affect new tasks. Availability,
proposal validation, atomic writes, and completion evidence enforce the saved constraint.
Completed tasks produce compact episodes without email bodies. If memory projection fails,
the task stays completed, the failure is visible, and `resume` retries projection without
repeating the calendar mutation.

Without PostgreSQL, run the paired synthetic demonstration:

```sh
uv run waypoint-agent benchmark --scenario memory_disabled --scenario memory_structured --output artifacts/memory-demo
```

Both cases use identical source calendars and explicit preference setup. The benchmark supplies
labeled synthetic decisions through the normal approval interface; ordinary runs never approve
themselves. In-memory reconstruction demonstrates recovery logic, not process/database durability.

## Benchmarks and validation

```sh
uv run waypoint-agent benchmark --backend memory --repeats 2 --output artifacts/benchmark
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pre-commit run --all-files
uv build
```

Local validation: **118 tests passed, 12 PostgreSQL tests skipped**. The existing Day 3
benchmark evidence records **32/32 benchmark runs passed**.
See the [generated report](docs/benchmark-example/report.md),
[paired memory evidence](docs/benchmark-example/memory-pair.json), and
[benchmark/memory guide](docs/day3.md) for methodology, commands, results, and limitations.

Database tests require `WAYPOINT_TEST_DATABASE_URL`; persistent commands use
`WAYPOINT_DATABASE_URL`. Live runs require `WAYPOINT_API_KEY`, `WAYPOINT_MODEL`, and a compatible
`WAYPOINT_BASE_URL`; see `.env.example`. Benchmark model mode is deliberately deterministic.
No live inference or local PostgreSQL durability result is claimed. CI is configured to execute
database tests and both benchmark backends; configuration alone is not a passing CI result.

Historical [retrieval design notes](docs/day1.md) and [approval design notes](docs/day2.md)
remain available. Real integrations, natural-language date parsing, a frontend, embeddings,
multi-agent execution, and cloud deployment are outside the current scope.
