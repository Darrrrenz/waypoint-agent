# Day 2: approved calendar rescheduling

The runtime now supports “Move my meeting with Alice to Friday afternoon.” against a persistent
mock calendar. The model proposes an exact update; application code creates the authorization
record. A human decides through the local CLI. A fresh process reconciles and executes that
operation, reads the event back, and verifies completion. The default meeting/email workflow
still works. No real calendar, email account, authentication service, or model account is needed.

## Setup and a complete PowerShell demo

From the repository root, with Python 3.12+, uv, and Docker available:

```powershell
uv sync --frozen
docker compose up -d --wait postgres
uv run waypoint-agent init-db

$world = [guid]::NewGuid().ToString()
uv run waypoint-agent seed-calendar --world-id $world
$waiting = uv run waypoint-agent run --workflow reschedule --world-id $world --json | ConvertFrom-Json
$task = $waiting.checkpoint.state.task.id
$approval = $waiting.checkpoint.state.pending_approval_id

uv run waypoint-agent approvals $task
uv run waypoint-agent inspect $task
uv run waypoint-agent approve $task $approval
uv run waypoint-agent resume $task --export artifacts/reschedule-trajectory.json
uv run waypoint-agent inspect $task
```

The default database URL is `postgresql://waypoint:waypoint@localhost:5432/waypoint`, matching
Compose. Override it with `WAYPOINT_DATABASE_URL`. If uv is installed only in the project
environment, replace `uv` with `.\.venv\Scripts\python.exe -m uv`.

For denial, replace `approve` with `deny`, then run `resume`. Both decision commands only
record a decision. Repeating the same decision for the current request is harmless; conflicting
decisions, unrelated IDs, and superseded requests are rejected. A completed or denied task can
be resumed repeatedly without a new mutation. A competing active process gets a busy error
and can retry later.

`approvals`, `approve`, `deny`, `run`, and `resume` accept `--json`; `inspect` always emits JSON.
`run` and `resume` accept `--export`. Exit codes: 0 for verified completion, awaiting approval,
denial, or successful inspection/decision; 2 for limited, failed, or unresolved execution;
1 for configuration, storage, or invalid CLI operations. JSON logs go to stderr.

`seed-calendar` generates a world UUID if omitted. Existing worlds are never overwritten.
`run --workflow reschedule --world-id ...` requires a previously seeded world. Resume loads
the task's world and saved fixture snapshot; it never reads fixture files or reseeds a world.
Every calendar tool in a rescheduling task uses the same store and world. New demo runs should
use new world IDs unless they intentionally share calendar state.

Without a database:

```sh
uv run waypoint-agent run --workflow reschedule --ephemeral --export artifacts/approval-preview.json
uv run python scripts/export_reschedule.py --memory --output artifacts/reschedule-example.json
```

The first command exits awaiting approval; its in-memory state disappears on exit. The second
is an explicitly synthetic demo harness that provides a decision for its own mock task and
exports a completed trajectory plus independent before/after calendar snapshots. It does not
demonstrate cross-process durability. Ordinary runtime/model code cannot approve itself.

## Workflow and scheduling contract

```mermaid
flowchart TD
    P[Pending] --> R[Running: search and check availability]
    R --> W[Waiting for approval: exact proposal persisted]
    W --> A[CLI records approval]
    W --> D[CLI records denial]
    D --> X[Resume: denied, no mutation]
    A --> L[Resume: reconcile operation ledger]
    L --> V[Atomic revision and conflict check, update and ledger commit]
    L --> O[Recover previously committed result]
    V --> O
    O --> B[Read intended event back]
    B --> C[Verified completion]
    V --> N[Stale revision or conflict]
    N --> R
    L --> U[Unresolved when outcome cannot be established]
```

- Workflow selection is explicit: `--workflow reschedule`; the default is `meeting_email`.
  Typed context, completion validation, and evaluator selection follow the saved workflow.
- Fixture semantic clock: September 20, 2026, 12:00 in America/Toronto. The search window is
  September 21 at 00:00 through September 28 at 00:00, end exclusive. Search filters by event
  start and exact participant (`--participant`, default `alice@example.com`).
- The destination window is September 25, 2026, 13:00–17:00 local time. `--target-date YYYY-MM-DD`
  chooses another Friday without changing the meeting search window. `--slot-minutes` changes
  the default 15-minute increment (1–240 allowed). `WAYPOINT_TIMEZONE` selects the IANA zone.
  A freeform goal does not change these contracts. No general date/identity parser is provided.
- Candidate starts are anchored at destination start. Preserve the event's duration; the whole
  interval must fit. All events in the world block overlaps, irrespective of participant/topic.
  Exclude only the event being moved. Intervals are half-open: an event ending at a slot start,
  or starting at its end, does not conflict. Equality at destination end is permitted.
- The model must propose the earliest available candidate. Proposal validation checks the
  unique meeting in the search scope, revision, duration, bounds, increment, and conflicts.
  Zero or multiple matches and no availability produce verified no-write outcomes.
- Approval arguments normalize timestamps to UTC before hashing. The fixture chooses
  `2026-09-25T17:00:00Z`–`17:30:00Z`, equivalent to 13:00–13:30 EDT. All timestamps must be aware.
  Approval inspection includes before/after values, exact normalized arguments, revision,
  task/world/event identities, stable operation UUID, and SHA-256 hash.
- The update changes only start/end plus the store-managed revision. ID, title, participants,
  topic, extra source fields, and unrelated events are preserved.

## Persistence, authorization, and recovery

Database initialization is additive and repeatable. Existing task/event tables remain intact;
new calendar-world and operation-ledger tables are created if absent. New checkpoint fields
have defaults, so historical Day 1 checkpoints remain inspectable. Historical checkpoints
without saved model/fixture configuration are not promised resumable.

The pending action, complete approval request, and waiting status share one atomic checkpoint
and event transaction. Application code creates approval/operation IDs; tool input schemas
reject model-supplied authorization fields. Registry policy allows reads, gates the concrete
calendar update tool, and rejects unsupported permissions. The approved call must exactly
match the current pending action, task/world/event, revision, normalized arguments, and hash.
The hash binds content; it is not a signature or protection against an administrator rewriting
the database. This remains a trusted local single-user CLI.

Run, resume, and decisions acquire a per-task PostgreSQL session advisory lock (in-memory tests
use an asyncio lock). A dedicated connection holds it across checkpoint transactions. A
process exit releases its lock. Checkpoint sequence comparisons also reject stale writers.
Different tasks can run concurrently; writes within one calendar world serialize on its row.

The calendar store checks revision, duration, bounds, increment, and all conflicts while holding
that world row lock. It commits the updated event and operation result in the same transaction.
Reusing an operation UUID with identical normalized arguments returns the saved result;
different arguments are rejected. World locks serialize distinct operations too, preventing
two tasks from concurrently occupying the same slot. This is intentionally a small JSONB mock
store, not a scalable production calendar design.

Every approved execution first consults the ledger. If the process dies after the calendar
commit but before its observation checkpoint, a fresh process recovers the result without
applying the operation again. It then records the execution and reads the event back. A stale
revision or newly occupied slot invalidates the approval and triggers bounded replanning with
a new approval ID and operation ID. The runtime never silently moves the approved slot.

The evaluator requires a runtime execution record bound to the approved operation, its exact
write observation, and a later matching event read, including preserved fields and duration.
Model prose or a claimed operation ID alone cannot establish success. A write can be applied
but remain unverified if a later read or budget fails; such a task never reports completion.

## Retries, budgets, and limitations

Only reads explicitly classified as `TransientReadError` receive automatic retry: by default
two retries after the first attempt, with 0.05s then 0.1s backoff. Retry attempts are saved so
process restarts cannot reset them. Other validation/permission errors do not automatically
retry. Model-directed corrections consume the shared error/model/step budgets. Calendar
revision/conflict errors also consume the error budget before requesting fresh approval.

Uncertain writes enter `unresolved`; a subsequent resume reconciles the ledger before any
possible retry. If the ledger is inaccessible, the runtime stays unresolved and never guesses
whether a write happened. These failures consume the error budget. Once the outcome is known
absent, another write is permitted only within the original error/time budgets. Reconciliation
alone can still establish a previously committed outcome after the execution deadline, under
the storage timeout; it cannot bypass the deadline to mutate, read for completion, or claim success.

Step/model/error limits and timeout configuration are persisted. Active execution seconds are
cumulative; human approval wait time is excluded. Before an external call the runtime persists
a timeout reservation. Successful checkpoints charge elapsed active time; restart charges a
lost in-flight reservation conservatively at its full amount. Calls consumed before a crash
remain consumed, and saved responses/pending actions are continued before another model call.
Storage operations have a separate timeout; diagnostic/final persistence may extend wall time
beyond the execution deadline. Any checkpoint failure is fatal to further work in that process.

The scripted adapter indexes its persisted finite script using the cumulative model-call
counter. The optional `fixtures/reschedule-script.json` demonstrates this with
`--script fixtures/reschedule-script.json`. Its `$last_operation_id` completion reference is
resolved only from an already confirmed runtime execution; it supplies evidence, not approval.
Default rescheduling uses a deterministic demo policy derived from saved observations, allowing
missing/ambiguous/conflict cases without hardcoded action indices. Live model name/endpoint
are persisted, while credentials must be supplied independently on each invocation. Fixture
snapshots and future script actions are omitted from the live model prompt.

No exactly-once claim is made for future external APIs. The mock guarantee depends on its
transactional calendar/ledger, unique operation identity, task locks, and trusted database.
After an event read, another task may subsequently change the calendar; verification describes
the observed outcome, not a permanent lock. External services will need their own idempotency,
reconciliation, permission, and retention designs. General natural-language scheduling,
real integrations, UI/server work, advanced memory, cloud deployment, and multi-agent execution
remain out of scope.

## Evidence and checks

The checked-in [generated example](day2-example-trajectory.json) is an actual in-memory run:
5 model calls, a persisted approval/decision, one operation, a matching event read, and verified
completion. It includes independent calendar before/after snapshots and labels its environment
and synthetic decision source. Regenerate with the `--memory` export command above; IDs and
measured timing vary.

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pre-commit run --all-files
uv build

# With the local development PostgreSQL server running:
$env:WAYPOINT_TEST_DATABASE_URL = 'postgresql://waypoint:waypoint@localhost:5432/waypoint'
uv run pytest -q -m integration
uv run python scripts/export_reschedule.py --output artifacts/reschedule-trajectory.json
```

Validation on September 21, 2026, Python 3.14.5:

| Check | Local result |
| --- | --- |
| Baseline before edits | 44 passed, 3 PostgreSQL tests skipped |
| Full pytest suite | 79 passed, 10 PostgreSQL tests skipped |
| Ruff lint / format | Passed |
| pre-commit | Passed |
| Wheel and source distribution build | Passed |
| Read-only workflow and reschedule approval preview | Passed |
| Completed in-memory reschedule export | Passed; linked above |
| PostgreSQL initialization probe | `ConnectionTimeout`; no local server |
| Real database transaction/concurrency/subprocess tests | Unexecuted locally |
| Docker / Compose | Unexecuted; Docker unavailable |
| Live model inference | Unexecuted; no credentials |

The seven added PostgreSQL tests cover concurrent operation deduplication, competing slot
writes, rollback after a failed ledger insertion, competing decisions/task locks, a subprocess
`run → approvals → inspect → approve → resume → inspect` flow, an actual process exit after
calendar commit followed by concurrent subprocess resumes, and repeatable initialization.
These join the three existing checkpoint transaction tests. CI supplies PostgreSQL on Python
3.12 and 3.14, runs all tests/hooks/build, and exports completed Day 1 and Day 2 trajectories.
The CI configuration does not itself constitute a passing result. Local in-memory tests are
not evidence of PostgreSQL durability. No dependency changes were needed; `uv.lock` is unchanged.

Day 3 can build on explicit persisted workflows, approval-bound writes, authoritative tool
state, restartable model execution, execution provenance, and trajectory-based evaluation.
