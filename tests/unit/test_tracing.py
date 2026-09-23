import json
from pathlib import Path
from uuid import uuid4

import pytest

from waypoint_agent import cli
from waypoint_agent.tracing import TraceError, load_export, render_export

ROOT = Path(__file__).resolve().parents[2]
DAY1 = ROOT / "docs/example-trajectory.json"
DAY2 = ROOT / "docs/day2-example-trajectory.json"


@pytest.fixture
def export(tmp_path):
    def write(payload):
        path = tmp_path / "trace.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    return write


def example(path=DAY1):
    return json.loads(path.read_text(encoding="utf-8"))


def append_event(payload, kind, data):
    payload["events"].append(
        {
            "task_id": payload["checkpoint"]["state"]["task"]["id"],
            "sequence": len(payload["events"]) + 1,
            "timestamp": "2026-09-23T12:00:00Z",
            "kind": kind,
            "data": data,
        }
    )
    payload["checkpoint"]["last_sequence"] = len(payload["events"])


def test_day1_rendering_is_compact_deterministic_and_read_only():
    before = DAY1.read_bytes()
    rendered = render_export(DAY1)
    assert rendered == render_export(DAY1)
    assert DAY1.read_bytes() == before
    for expected in (
        "Workflow: meeting_email (historical default)",
        "Recorded status: completed",
        "Termination reason: verified",
        "Model calls: 4",
        "Errors: 0",
        "meetings: 1 [cal-alice-001]",
        "emails: 1 [mail-alice-unread]",
        "Model proposed completion",
        "16. [completion_verified]",
    ):
        assert expected in rendered
    assert "Please review the launch checklist" not in rendered
    assert "Memory projection:" not in rendered


def test_day2_approval_and_execution_evidence():
    rendered = render_export(DAY2)
    state = example(DAY2)["checkpoint"]["state"]
    approval = state["approvals"][0]
    for expected in (
        approval["id"],
        approval["operation_id"],
        approval["event_id"],
        approval["before"]["start"],
        approval["before"]["end"],
        approval["after"]["start"],
        approval["after"]["end"],
        "decision: approved",
        "recorded outcome: applied",
        "slots: 15",
        "Export environment: in_memory",
        "explicit synthetic demo harness",
        "Approval decision recorded (not execution)",
        "Operation reconciliation lookup (not a new mutation or outcome)",
        "Recorded tool result: update_calendar_event",
    ):
        assert expected in rendered
    assert "successful recovery" not in rendered


@pytest.mark.parametrize("approved", [False, True])
def test_waiting_approval_does_not_claim_execution(export, approved):
    payload = example(DAY2)
    end = 17 if approved else 16
    payload["events"] = payload["events"][:end]
    payload["checkpoint"]["last_sequence"] = end
    state = payload["checkpoint"]["state"]
    state["task"].update(status="waiting_for_approval", termination_reason=None)
    state.update(answer=None, findings=None, executions=[])
    state["approvals"] = [payload["events"][-1]["data"]]
    rendered = render_export(export(payload))
    assert "Recorded status: waiting_for_approval" in rendered
    assert "recorded outcome: unknown (not recorded)" in rendered
    assert "Tool requested (proposal only): update_calendar_event" in rendered
    assert "recorded outcome: applied" not in rendered
    assert "Recorded tool result: update_calendar_event" not in rendered


def test_unresolved_reconciliation_has_no_invented_outcome(export):
    payload = example(DAY2)
    payload["events"] = payload["events"][:19]
    payload["checkpoint"]["last_sequence"] = 19
    state = payload["checkpoint"]["state"]
    state["task"].update(status="unresolved", termination_reason=None)
    state.update(answer=None, findings=None, executions=[])
    state["approvals"] = [payload["events"][16]["data"]]
    append_event(payload, "operation_unresolved", {"error": "TimeoutError"})
    rendered = render_export(export(payload))
    assert "Recorded status: unresolved" in rendered
    assert "execution outcome unknown" in rendered
    assert "TimeoutError" in rendered
    assert "recorded outcome: applied" not in rendered
    assert "successful recovery" not in rendered


def test_completed_task_with_failed_memory_projection(export):
    payload = example()
    payload["checkpoint"]["state"].update(
        memory_strategy="structured",
        memory_projection="failed",
        memory_projection_error="OSError",
    )
    append_event(payload, "memory_projection_failed", {"error": "OSError"})
    rendered = render_export(export(payload))
    assert "Recorded status: completed" in rendered
    assert "Memory projection: failed" in rendered
    assert "Memory projection error: OSError" in rendered
    assert "separate from task completion" in rendered


@pytest.mark.parametrize("payload", [[], {}, {"results": []}, {"checkpoint": {}, "events": {}}])
def test_unsupported_shapes(export, payload):
    with pytest.raises(TraceError, match="single-task export"):
        render_export(export(payload))


def test_invalid_json_and_schema(export):
    path = export({})
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(TraceError, match="Invalid JSON"):
        load_export(path)
    payload = example()
    payload["events"][0]["timestamp"] = "private-invalid-value"
    with pytest.raises(TraceError, match="timestamp") as exc:
        load_export(export(payload))
    assert "private-invalid-value" not in str(exc.value)


@pytest.mark.parametrize("problem", ["gap", "duplicate", "reversed", "tail", "task", "start"])
def test_inconsistent_sequences_and_task_ids(export, problem):
    payload = example()
    if problem == "gap":
        del payload["events"][2]
    elif problem == "duplicate":
        payload["events"][1]["sequence"] = 1
    elif problem == "reversed":
        payload["events"].reverse()
    elif problem == "tail":
        payload["checkpoint"]["last_sequence"] += 1
    elif problem == "task":
        payload["events"][0]["task_id"] = str(uuid4())
    else:
        payload["events"][0]["sequence"] = 0
    with pytest.raises(TraceError, match="sequence|different task"):
        render_export(export(payload))


def test_unknown_kinds_historical_defaults_and_absent_counters(export):
    payload = example()
    state = payload["checkpoint"]["state"]
    for key in ("model_calls", "steps", "errors"):
        del state[key]
    for event in payload["events"]:
        event.pop("usage", None)
        event.pop("latency_ms", None)
    payload["unrecognized_metadata"] = {"anything": "permitted"}
    append_event(payload, "future_kind", {"raw": "SECRET PAYLOAD"})
    path = export(payload)
    checkpoint, _, _ = load_export(path)
    assert checkpoint.state.memory_strategy == "disabled"
    rendered = render_export(path)
    assert "Model calls: unknown (not recorded)" in rendered
    assert "Steps: unknown (not recorded)" in rendered
    assert "Errors: unknown (not recorded)" in rendered
    assert "[future_kind] Unrecognized event kind" in rendered
    assert "SECRET PAYLOAD" not in rendered


def test_markdown_escapes_all_source_text(export):
    payload = example()
    source = "A|B\n`code` <script> & *bold* [link](target)"
    payload["checkpoint"]["state"]["task"]["goal"] = source
    append_event(payload, source, {})
    rendered = render_export(export(payload), "markdown")
    escaped = r"A&#124;B<br>\`code\` &lt;script&gt; &amp; \*bold\* \[link\]\(target\)"
    assert rendered.count(escaped) == 2
    assert "<script>" not in rendered
    assert "|" not in rendered


def test_memory_retrieval_projection_retries_and_harness_context(export):
    payload = example()
    payload["harness_actions"] = [{"actor": "test-harness", "decision": "approved"}]
    append_event(
        payload,
        "memory_retrieved",
        {
            "namespace": "demo",
            "records": [{"id": "preference-1"}],
            "effective_context": {
                "earliest_meeting_start": {"earliest": "15:00", "timezone": "UTC"}
            },
        },
    )
    append_event(payload, "read_retry", {"error": "TransientReadError"})
    append_event(payload, "memory_projection_stored", {"record_id": "episode-1", "revision": 1})
    rendered = render_export(export(payload))
    for expected in (
        "test-harness",
        "preference-1",
        "earliest: 15:00",
        "Read retry scheduled",
        "episode-1",
    ):
        assert expected in rendered


@pytest.fixture
def offline_cli(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline trace constructed runtime infrastructure")

    for name in (
        "Settings",
        "PostgresRepository",
        "MemoryRepository",
        "MemoryCalendar",
        "PostgresCalendar",
        "MemoryController",
        "InMemoryStore",
        "PostgresMemoryStore",
        "runtime_for",
        "OpenAICompatibleModel",
        "ScriptedModel",
        "RescheduleDemoModel",
    ):
        monkeypatch.setattr(cli, name, forbidden)

    def invoke(*args):
        monkeypatch.setattr("sys.argv", ["waypoint-agent", "trace", *map(str, args)])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        return exc.value.code

    return invoke


def test_cli_stdout_and_file_without_configuration(offline_cli, tmp_path, capsys):
    assert offline_cli("--input", DAY1) == 0
    assert "Recorded status: completed" in capsys.readouterr().out
    output = tmp_path / "nested" / "trace.md"
    assert offline_cli("--input", DAY2, "--format", "markdown", "--output", output) == 0
    assert output.read_text(encoding="utf-8") == render_export(DAY2, "markdown")
    assert capsys.readouterr().out == ""


def test_cli_failures_and_input_protection(offline_cli, export, tmp_path, capsys):
    path = export({})
    assert offline_cli("--input", path) == 1
    captured = capsys.readouterr()
    assert "single-task export" in captured.err
    assert captured.out == ""
    assert offline_cli("--input", tmp_path / "absent.json") == 1
    assert "Cannot read input" in capsys.readouterr().err
    path = export(example())
    before = path.read_bytes()
    assert offline_cli("--input", path, "--output", path) == 1
    assert "must not overwrite" in capsys.readouterr().err
    assert path.read_bytes() == before
    assert offline_cli("--input", path, "--output", tmp_path) == 1
    assert "Trace failed:" in capsys.readouterr().err


def test_empty_trace_and_missing_task_identity(export):
    payload = example()
    payload["events"] = []
    payload["checkpoint"]["last_sequence"] = 0
    assert "No events recorded" in render_export(export(payload))
    del payload["checkpoint"]["state"]["task"]["id"]
    with pytest.raises(TraceError, match="task ID must be recorded"):
        render_export(export(payload))
