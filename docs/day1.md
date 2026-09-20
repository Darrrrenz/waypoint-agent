# Day 1: setup, contract, and design

## Install

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/). All dependencies, including development
tools, are pinned with hashes in `uv.lock`. Run commands from the repository root.

```sh
uv sync --frozen
uv run pre-commit install
```

`uv sync` creates the project-local `.venv`. You can also bootstrap uv inside it:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install uv==0.12.17
.\.venv\Scripts\uv.exe sync --frozen
```

For subsequent PowerShell commands, replace `uv` with `.\.venv\Scripts\uv.exe` if uv
is not on PATH. Copy `.env.example` to `.env` (`Copy-Item .env.example .env` in PowerShell,
`cp .env.example .env` in a Unix shell). Environment variables override `.env`.

Runtime dependencies are Pydantic for contracts, pydantic-settings for configuration,
httpx for one small async provider adapter, psycopg for PostgreSQL, and tzdata for portable
IANA timezone support on Windows. There is no agent framework, ORM, web server, or queue.

## Run and inspect

```sh
docker compose up -d --wait postgres
uv run waypoint-agent init-db
uv run waypoint-agent run
uv run waypoint-agent inspect <task-id>
```

The default command runs this goal with the scripted adapter:

> Find my meeting with Alice next week and tell me whether I have any related unread emails.

`run` persists to PostgreSQL by default. The local Compose database uses development-only
credentials and binds to localhost. `init-db` creates the two tables idempotently; it does
not drop data. `inspect` loads checkpoint and ordered events from a new process as JSON.

To run without a database and retain a JSON trajectory:

```sh
uv run waypoint-agent run --ephemeral --export artifacts/trajectory.json
```

An ephemeral run stores its trajectory in memory until exit; `--export` retains it in a file.
`inspect` operates on PostgreSQL, so it cannot load an ephemeral task. Exit codes are 0 for
verified completion (including missing/ambiguous outcomes), 2 for a bounded unsuccessful run,
and 1 for configuration or infrastructure failure. Structured JSON logs go to stderr; answers
or `--json` output go to stdout. Full inputs and outputs are in the saved trajectory.

Example answer:

```text
Status: completed (verified)
Meeting: Waypoint launch review with Alice, 2026-09-22T10:00:00-04:00 [cal-alice-001]. Related unread emails: Launch review agenda [mail-alice-unread]. Evidence: obs-1, obs-2, obs-3
Trajectory: 16 events; model calls: 4
```

See [example-trajectory.json](example-trajectory.json) for an actual credential-free run.
The fixed fixture clock makes semantic time deterministic; task UUIDs and measured latency vary.

## Mock-world semantics

- Clock: `2026-09-20T12:00:00-04:00`, injected from `fixtures/environment.json`.
- Timezone: `America/Toronto`, configurable with `WAYPOINT_TIMEZONE`.
- Next week: next Monday at local midnight through the following Monday, exclusive. On a
  Monday, this means the Monday seven days later. Calendar filtering uses meeting start time.
  Timezone-aware local date arithmetic handles daylight-saving transitions.
- Alice resolves to exact identity `alice@example.com` via the CLI's explicit `--participant`
  scope. Day 1 has no general natural-language identity or date parser. The selected workflow
  is meeting/email lookup next week; a freeform goal does not install a different evaluator.
- Relevance requires the task participant in the email's participants AND exact equality
  between the email topic and meeting topic. There is no fuzzy name, subject, or body matching.
- Search tools return complete, unpaginated results and an `exhaustive` marker. Absence can
  only be verified against a cited exhaustive search with the correct scope.
- `read_email` does not change the unread flag. Every reported positive email requires a
  corresponding read observation. Read/unrelated emails are excluded even if returned by an
  unfiltered email search.
- No matching meeting is a valid `missing_meeting` outcome. A single meeting without relevant
  unread messages yields `no_unread`. Multiple plausible meetings yield `ambiguous`, listing
  candidates and requesting clarification; the runtime does not silently pick one.

`fixtures/environment.json` contains source records only. `fixtures/script.json` is a mock model
response sequence for that environment. Neither the runtime nor its evaluator reads this script
or test expected answers. Benchmark assertions and adversarial variants live in `tests/`.
Changing fixture dates, scope, or identity may require a different `--script` for scripted mode.

## Architecture

```mermaid
flowchart LR
    CLI --> Runtime
    Runtime --> Model[Model interface]
    Model --> Scripted
    Model --> OpenAI[OpenAI-compatible HTTP adapter]
    Runtime --> Registry[Validated read-only tool registry]
    Registry --> Fixtures[Mock source records]
    Runtime --> Evaluator[Evidence verifier]
    Runtime --> Repository[Repository interface]
    Repository --> PostgreSQL
    Repository --> Memory[Memory repository for tests]
```

The runtime owns state transitions. Each step requests one action, locally validates the
discriminated `NextAction` union, resolves a registered tool, validates input, executes it,
validates output, and records a successful observation. The model receives a copy of current
state, observations, error feedback, and tool descriptions with input/output schemas and risk.
It cannot write state or bypass the registry. A completion proposal carries structured outcome,
meeting IDs, email IDs, and observation IDs. The workflow evaluator checks these against cited
observations and renders the final answer from verified source fields, not model prose.

No plan objects are needed for this loop. The runtime and live adapter contain no Alice-specific
sequence or answer key. The evaluator is workflow-specific and parameterized by the task's
participant and date window; other workflows will need their own verifier and context contract.

`waypoint_tasks` stores the latest JSONB checkpoint; `waypoint_events` stores an ordered JSONB
trajectory keyed by `(task_id, sequence)`. Every event append and checkpoint update is one
transaction. Conditional sequence updates reject stale writers. Reads use a repeatable-read
transaction so state and events come from the same snapshot. Events include request/response
payloads, errors, measured call latency, and usage when reported. Unknown usage is JSON `null`.

Tasks move from `pending` to `running`, then `completed`, `failed`, or `limited`. Termination
reasons are `verified`, `error_budget`, `max_steps`, `max_model_calls`, and `deadline`. Defaults
are 12 steps, 12 model calls, 3 total errors, 30s/model call, 5s/tool call, and a 120s execution
deadline. Budgets include failed calls. There are no hidden provider retries. Repeated valid
actions exhaust the step/call budget. Invalid outputs and rejected completion proposals consume
the shared error budget and remain inspectable; the model may correct an error within the budget.

Execution time is measured with a monotonic clock; semantic timestamps use the injected clock.
No new model/tool work starts after the deadline. Each storage operation has its own 5s timeout;
bounded diagnostic/final checkpoint writes can extend wall time beyond the execution deadline.
Async cancellation requires cooperative adapters; Day 1 tools never block on synchronous I/O.
Storage failure stops execution immediately without returning an unpersisted success. The last
durable checkpoint may remain `running`; inspecting it is supported, resuming it is not.

## OpenAI-compatible adapter

Set `WAYPOINT_API_KEY`, `WAYPOINT_MODEL`, `WAYPOINT_BASE_URL`, and optionally
`WAYPOINT_MODEL_TIMEOUT`. API keys are secret settings and are never put in state or logs.
`.env` is ignored by Git and Docker. Do not enable HTTP wire logging with real credentials.

```sh
uv run waypoint-agent run --model live
```

For a credentialed smoke test without PostgreSQL:

```sh
uv run waypoint-agent run --model live --ephemeral --export artifacts/live-trajectory.json
```

The adapter uses Chat Completions function calling with `strict: true`, `tool_choice: required`,
and parallel tool calls disabled, following the
[official OpenAI function-calling documentation](https://developers.openai.com/api/docs/guides/function-calling).
It exposes the registered tools and a `propose_completion` function, maps the returned function
to `NextAction`, and validates locally. A compatible endpoint must support this mechanism;
there is no silent fallback to unconstrained text. Provider failures/refusals are bounded like
other model failures. The default model is configurable and availability is account-dependent.

HTTP mock tests verify request schemas, response conversion, usage, malformed responses, and
the full shared-runtime path. These are scripted transport tests, not evidence of a successful
live inference request. No credentials were available during implementation: live validation
remains unverified.

## Checks

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run pre-commit run --all-files
```

For database tests, start Compose and set the test connection explicitly:

```powershell
$env:WAYPOINT_TEST_DATABASE_URL = 'postgresql://waypoint:waypoint@localhost:5432/waypoint'
uv run pytest -q -m integration
```

```sh
WAYPOINT_TEST_DATABASE_URL=postgresql://waypoint:waypoint@localhost:5432/waypoint uv run pytest -q -m integration
```

Integration tests use fresh task UUIDs and test real transactions, reloads, event ordering,
stale writes, and rollback. Use a development/test database. With no test URL they explicitly
skip; with a URL but an unavailable server they fail. GitHub Actions supplies PostgreSQL and
runs the complete suite on Python 3.12 and 3.14, then exports a persisted example trajectory.
The CI workflow is supplied but has not been run on GitHub from this local workspace.

Local environment limitation: Python 3.14 is available, but Docker, PostgreSQL binaries, a
PostgreSQL service, a database URL, and model credentials are unavailable. The credential-free
checks and CLI demo were run; PostgreSQL integration tests and Docker build/Compose execution
were not. Their presence in CI does not constitute a local passing result.
An explicit `waypoint-agent init-db` attempt failed with `ConnectionTimeout` against the
default localhost connection. The local pytest result is **39 passed, 3 skipped**.

| Local check | Result |
| --- | --- |
| pytest, Python 3.14.5 | 39 passed; 3 PostgreSQL tests skipped |
| Scripted CLI workflow | Completed; 16-event export saved in this directory |
| Ruff lint and format | Passed |
| pre-commit hooks on all Python source/test files | Passed |
| `uv build` | Source distribution and wheel built successfully |
| PostgreSQL initialization probe | Failed with `ConnectionTimeout`; no local server |
| Docker build and Compose | Not run; Docker unavailable |
| Live model inference | Not run; credentials unavailable |
| GitHub Actions / Python 3.12 | Configured; not executed locally |

The local Windows sandbox and interactive account have different file owners. Pre-commit
was validated with a process-scoped `safe.directory` exception for this repository; no global
Git configuration was changed. A normal checkout owned by your account needs no exception.

## Scope and Day 2 foundation

Day 1 intentionally has no real inbox/calendar access, mutations, approval flow, web API,
frontend, advanced memory, Redis, vector search, cloud deployment, or multi-agent execution.
Tool data is deterministic and trusted as a source of truth after validation; schema validation
alone cannot establish the truthfulness of a future external integration. Saved trajectories
contain mock email content; a real-data deployment will need a retention/redaction policy.

The Day 2 foundation is a replaceable model/tool/repository/evaluator contract, explicit task
statuses, bounded execution, typed observations, and atomic versioned checkpoints with ordered
events. Full crash recovery, migration tooling, concurrent task scheduling, and real-data
integration semantics remain future work; there are no placeholder modules for them.
