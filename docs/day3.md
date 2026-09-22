# Benchmarks and persistent memory

The runtime supports meeting/email retrieval, approval-gated rescheduling, and a small
cross-task memory controller. Offline benchmark assertions are separate from the runtime's
completion evaluators. All examples use mock source data and synthetic identities.

## Reproduce the benchmark

From the repository root, with Python 3.12+ and the locked environment:

```sh
uv sync --frozen
uv run waypoint-agent benchmark --suite benchmarks/core.json --backend memory --model-mode deterministic --repeats 2 --output artifacts/benchmark
uv run waypoint-agent benchmark --scenario memory_disabled --scenario memory_structured --output artifacts/memory-demo
uv run python scripts/export_benchmark_example.py --input artifacts/benchmark --output docs/benchmark-example
```

On Windows, `.\.venv\Scripts\uv.exe` can replace `uv` if it is not on PATH.
The first command produces `report.json`, `report.md`, and a trajectory/world snapshot file
for every scenario/repeat. The exporter copies actual reports and extracts compact paired
evidence; it does not rerun or fabricate results. Full trajectory filenames in the copied
report refer to the original artifacts directory, which CI uploads in full.

With an available development PostgreSQL database:

```sh
docker compose up -d --wait postgres
uv run waypoint-agent init-db
uv run waypoint-agent benchmark --backend postgres --output artifacts/benchmark-postgres
```

The storage backend and model mode are separate flags. Benchmark mode currently supports
only deterministic scripted/demo execution; it cannot silently switch a live run to a demo.
Ordinary `run --model live` remains opt-in and requires credentials. No live-model benchmark
performance is claimed.

## Scenario methodology

`benchmarks/core.json` is a typed declarative suite. Each scenario has a stable ID,
description, complete source dataset, semantic clock, timezone/context, workflow, goal,
model mode or finite response sequence, and a separate expected result. Synthetic approval
policy and fault injection are harness-only fields. The runner passes only task inputs and
selected memory into `AgentState`; assertions and fault schedules never enter model state.
Scripted responses are explicitly the model-under-test, not a source for the offline oracle.

There are 16 scenarios: correct retrieval; exclusion of read/unrelated messages; missing
meeting; ambiguous meetings; no related unread messages; approved rescheduling; transient
read retry; interruption after commit before observation persistence; no destination slot;
approval denial; a newly occupied proposed slot; bounded malformed responses; memory disabled;
structured memory; duplicate/replaced preferences; and preference-induced no availability.

Every run/repeat creates fresh task and calendar-world UUIDs and a fresh memory namespace.
The paired cases declare an earlier retrieval task, share only their own namespace across
steps, and use independent identical calendars. No existing world is reset. PostgreSQL runs
leave uniquely named benchmark records in the development database for inspection; there is
no automatic deletion of user data.

Synthetic decisions call `approval.decide`, the same locked decision interface used by the
CLI. Artifacts identify the test harness as the actor. The occupied-slot scenario uses a
separate ledger operation for the competing actor. Recovery recreates runtime objects and
reconciles the existing operation. It is an in-process fault demonstration, not a process
restart or a durability test. The separate PostgreSQL tests retain actual subprocess exits,
concurrency, transactional rollback, and restart coverage.

The harness allows at most six resume iterations per task and a 180-second timeout per
scenario, in addition to persisted runtime limits. A failed assertion returns CLI exit code
2 while still writing reports. Infrastructure exceptions are reported separately and count
against the total pass denominator. Invalid suite/configuration input exits 1 before execution.
Unavailable database/live coverage is explicitly labeled; it is not counted as passing work.

## Independent assertions

The offline scorer compares findings and retrieved source IDs with the scenario answer key,
checks positive email relevance and actual reads, compares the entire authoritative final
calendar against explicitly allowed changes, and inspects approval bindings and ledger entries.
It checks destination bounds and overlap directly, and independently enumerates candidate
starts for no-availability outcomes. It never calls runtime `slots`, `validate_update`, or a
completion evaluator. Tests deliberately give it a completed task and an incorrect calendar
and require it to fail.

Success means all scenario assertions pass. A denied task with an unchanged calendar and an
expected bounded model failure are successful benchmark scenarios. Completion alone is never
the oracle. Positive scheduling expectations contain explicit start/end/revision values;
they do not require one exact valid action trace.

## Metric definitions

| Field | Meaning |
| --- | --- |
| Pass rate | Successful scenario/repeat rows divided by all requested rows, including infrastructure failures |
| Tool proposals | Recorded `tool_requested` events, including unexecuted write proposals |
| Execution attempts | Read-dispatch events plus approved operation-dispatch events |
| Proposal validations | Separate validation reads; not calendar mutations |
| Retries | Read attempts after attempt one, plus repeated dispatches of the same write operation ID |
| Logical write operations | Distinct approval operation IDs; a denied or stale proposal still has an ID |
| Mutations | Authoritative committed ledger entries; `harness_mutations` identifies competing-actor changes |
| Invalid calls | Malformed model actions and tool schema/argument/permission rejections, excluding transient I/O failures |
| Model calls | Persisted model-call counter, including unsuccessful calls |
| Active seconds | Measured time around runtime run/resume calls, including their storage/projection work, excluding harness decisions/setup |
| Charged active seconds | Runtime's persisted budget charge, including conservative lost-call reservations after interruption |
| Harness wall seconds | Full per-scenario elapsed time, including setup, approvals, assertions and export |
| Recovery | Passing, exercised fault scenarios divided by all applicable fault scenarios, including infrastructure failures |
| Tool selection | Proposals with allowed tool names divided by proposals, only when allowed tools are declared |
| Arguments | Calls satisfying declared exact argument constraints divided by constrained calls |
| Unnecessary actions | Proposals beyond a declared maximum; null when no action-budget rule exists |
| Disallowed actions | Proposals outside a declared tool set; null without a rule |
| Preference satisfaction | Actual moved start satisfies explicit preference, independently of workflow/scenario success |

Counts and active latency include declared prior tasks. Per-step evidence and metrics remain
in the trajectory. Tool/argument ratios with no applicable calls have denominator zero and
value null, displayed as N/A. The disabled memory scenario intentionally passes with preference
satisfaction 0/1. This is a plumbing/constraint demonstration, not evidence of improved LLM
intelligence. Latency is measured around calls; overlapping trajectory latencies are never summed.

Provider usage is null unless every model call reports usage; only token fields known for
every response are totaled. Usage coverage records known responses over model calls. Multi-task
usage/cost totals are conservatively null. Inference cost remains null in all deterministic
examples. Optional `--pricing path.json` accepts a dated, explicit configuration with `as_of`,
`provider` (exact saved base URL), `model`, `input_per_million_usd`, and
`output_per_million_usd`. The estimator requires matching live identity and complete input/output
token counts. No guessed prices are supplied; cached-token discounts and complex provider billing
are outside this simple approximate estimator.

Reports include schema versions, suite/scenario/config SHA-256 hashes, code revision and dirty
status, model/storage/strategy labels, context/clock, execution limits, and repeat index.
There is no random seed because model actions and fault timing are deterministic. UUIDs,
wall timestamps, and latency vary; assertions and ordered event kinds are reproducible.

## Memory rules and scheduling semantics

`AgentState` remains working memory. `MemoryController` provides narrow `retrieve`, `prepare`,
`consider`, and `project` operations. Only `disabled` and `structured` strategies are implemented.
The CLI defaults new tasks to structured memory; historical checkpoints default to disabled.

Semantic records hold one typed preference: earliest local meeting start, with an IANA timezone.
Only explicit CLI input (or the labeled benchmark setup equivalent) creates it. Email bodies,
model suggestions, and prior episodes cannot create preferences. Repeating an identical current
preference returns the same record; a changed value adds a revision with `supersedes`, retaining
history. Changing back to an older value creates another revision. Namespaces isolate users/runs.

At task creation, relevant retrieval selects at most the latest single scheduling preference.
Retrieval tasks select none. The record ID, revision, provenance, and effective typed constraint
are saved in the checkpoint, made visible in `memory_retrieved`, and available in model state.
Later preference changes apply only to newly created tasks. Resume never retrieves a replacement
preference for an already approved operation.

The explicit destination window is preserved. Candidate starts remain anchored to its start
and slot increment; the preference filters candidates in its own timezone. The entire original
duration must fit the window. A 15:00 preference intersects 13:00–17:00 at 15:00; a 17:00
preference leaves no valid 30-minute slot. Availability, proposal validation, atomic calendar
validation, and completion evidence enforce the same persisted constraint.

Episodic records contain verified outcome, source IDs, approval decisions, operation IDs,
selected-memory provenance, and source task ID. They contain no email bodies or entire
trajectories. Only completed, evidence-verified tasks are projected. Proposed, denied, failed,
limited, and unresolved updates do not become successful rescheduling episodes. Projection
rechecks completion evidence and inserts idempotently under `task:<UUID>`.

Memory projection runs after durable task completion. A write failure is saved as
`memory_projection_failed`, displayed by the CLI, and returns a nonzero exit status without
undoing the calendar operation. `resume` of the completed task retries only projection. A lost
reply after an episode insert is deduplicated. If task storage itself is inaccessible, the
command fails; the durable completion remains retryable. Runtime and memory writes are separate
transactions. This is not an atomic transaction across every application component.

PostgreSQL initialization is additive (`CREATE TABLE IF NOT EXISTS`); namespaces serialize
record updates, with a `(namespace, key, revision)` primary key. There is no data-dropping
migration. The in-memory implementation uses the same replacement rules. This small store
currently reads a namespace's records before selecting one preference; large histories will
need indexed retrieval/retention policy.

## Copy-paste fresh-process demonstration (PowerShell)

This requires a reachable development PostgreSQL server. Each CLI invocation below is a
separate process, including preference creation, task creation, decision, resume, and inspection.

```powershell
docker compose up -d --wait postgres
uv run waypoint-agent init-db
$namespace = 'demo-' + [guid]::NewGuid().ToString()
$world = [guid]::NewGuid().ToString()
uv run waypoint-agent memory set --namespace $namespace --earliest 15:00 --timezone America/Toronto
uv run waypoint-agent memory list --namespace $namespace
uv run waypoint-agent run --memory-namespace $namespace
uv run waypoint-agent seed-calendar --world-id $world
$waiting = uv run waypoint-agent run --workflow reschedule --world-id $world --memory-namespace $namespace --json | ConvertFrom-Json
$task = $waiting.checkpoint.state.task.id
$approval = $waiting.checkpoint.state.pending_approval_id
uv run waypoint-agent approvals $task
uv run waypoint-agent inspect $task
# Inspect the exact 19:00Z (15:00 Toronto) request before approving.
uv run waypoint-agent approve $task $approval
uv run waypoint-agent resume $task --export artifacts/memory-trajectory.json
uv run waypoint-agent inspect $task
uv run waypoint-agent memory episodes --namespace $namespace
# Idempotent: no extra calendar mutation or episode.
uv run waypoint-agent resume $task
```

To compare without memory, seed a different world with the same fixture and run with
`--memory-strategy disabled` using the same namespace. The paired benchmark automates that
comparison with synthetic decisions. Changing a preference after approval does not change the
approved task; the subprocess integration test explicitly checks this behavior.

## Actual validation and limitations

Validation on September 22, 2026, Python 3.14.5:

| Check | Result |
| --- | --- |
| Baseline before implementation | 79 passed, 10 PostgreSQL skips |
| Full local pytest suite | 95 passed, 12 PostgreSQL skips |
| Deterministic benchmark, two repeats | 32/32 passed; applicable recovery 6/6 |
| Paired preference example | Disabled 13:00; structured 15:00, exact approved and verified change |
| Ruff lint / format and pre-commit | Passed |
| Source distribution and wheel | Built successfully |
| PostgreSQL subprocess/concurrency checks | Not run locally; no reachable local database |
| Docker/Compose and live inference | Not run; infrastructure/credentials unavailable |
| GitHub Actions | Configured, not locally verified as a successful CI run |

The [generated report](benchmark-example/report.md), [machine-readable report](benchmark-example/report.json),
and [compact paired evidence](benchmark-example/memory-pair.json) come from actual local in-memory
runs. They retain the code revision and dirty flag at generation, rather than claiming a later
commit generated them. Automatic approval review rejected a database initialization probe because
its destination had not yet been established. A subsequent read-only configuration/TCP check
identified `localhost:5432/waypoint` and found no reachable server; no database initialization
was executed locally.

CI retains existing subprocess/crash/concurrency tests, adds preference/episode subprocess
coverage, runs memory and PostgreSQL benchmarks, and uploads reports/trajectories even if a
preceding step fails. Supplying this configuration is not evidence that CI or database tests
have passed. Set `WAYPOINT_TEST_DATABASE_URL` and run `uv run pytest -q -m integration` to
execute the database checks on an available development server.

The next useful work is live-model evaluation with explicitly applicable scenarios, indexed
memory retention, and real external API reconciliation semantics. Real accounts, a frontend,
embeddings, model-authored memory summaries, multiple agents, and deployment remain deferred.
