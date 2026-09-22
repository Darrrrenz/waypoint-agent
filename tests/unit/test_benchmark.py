import json
import subprocess
import sys
from pathlib import Path

from waypoint_agent.benchmark.assertions import score
from waypoint_agent.benchmark.reporting import summarize
from waypoint_agent.benchmark.runner import run_scenario, run_suite
from waypoint_agent.benchmark.scenarios import Suite

SUITE = Path(__file__).resolve().parents[2] / "benchmarks/core.json"


def scenarios():
    return Suite.model_validate_json(SUITE.read_text(encoding="utf-8")).scenarios


async def test_isolation_repeatability_and_unknown_usage():
    scenario = next(s for s in scenarios() if s.id == "approved_reschedule")
    first, second = await run_scenario(scenario), await run_scenario(scenario)
    assert first["checkpoint"].state.task.id != second["checkpoint"].state.task.id
    assert (
        first["checkpoint"].state.task.calendar_world_id
        != second["checkpoint"].state.task.calendar_world_id
    )
    assert first["before"] == second["before"]
    assert first["after"] == second["after"]
    assert [e.kind for e in first["events"]] == [e.kind for e in second["events"]]
    result = summarize(scenario, first)
    assert result["status"] == "passed"
    assert result["metrics"]["provider_usage"] is None
    assert result["metrics"]["inference_cost"] is None
    assert result["metrics"]["arguments"]["denominator"] == 0


async def test_oracle_rejects_completed_task_with_wrong_world():
    scenario = next(s for s in scenarios() if s.id == "approved_reschedule")
    run = await run_scenario(scenario)
    assert run["checkpoint"].state.task.status == "completed"
    checks = score(
        scenario, run["checkpoint"].state, run["before"], run["before"], run["operations"]
    )
    assert not next(c for c in checks if c["name"] == "authoritative_calendar")["passed"]


async def test_bounded_approval_loop():
    scenario = next(s for s in scenarios() if s.id == "occupied_after_approval")
    run = await run_scenario(scenario, max_resumes=1)
    assert run["checkpoint"].state.task.status == "waiting_for_approval"
    assert not all(c["passed"] for c in run["assertions"])


async def test_failed_report_and_denominators(tmp_path):
    scenario = scenarios()[0].model_copy(deep=True)
    scenario.expected.meeting_ids = ["wrong"]
    path = tmp_path / "suite.json"
    path.write_text(Suite(scenarios=[scenario]).model_dump_json(), encoding="utf-8")
    report = await run_suite(path, tmp_path / "output", repeats=2)
    assert report["summary"]["pass_rate"] == {"numerator": 0, "denominator": 2, "value": 0}
    assert report["summary"]["recovery"]["value"] is None
    assert (tmp_path / "output/report.md").exists()
    assert (
        json.loads((tmp_path / "output/report.json").read_text())["results"][0]["status"]
        == "failed"
    )


async def test_core_suite(tmp_path):
    report = await run_suite(SUITE, tmp_path)
    assert report["summary"]["pass_rate"]["value"] == 1


async def test_memory_pair_is_nonvacuous_and_isolated():
    cases = {s.id: s for s in scenarios()}
    disabled = await run_scenario(cases["memory_disabled"])
    structured = await run_scenario(cases["memory_structured"])
    assert disabled["before"] == structured["before"]
    assert disabled["memory"]["namespace"] != structured["memory"]["namespace"]
    assert disabled["memory"]["preference_satisfied"] is False
    assert structured["memory"]["preference_satisfied"] is True
    assert disabled["after"][0].start.hour == 17  # UTC 13:00 EDT
    assert structured["after"][0].start.hour == 19  # UTC 15:00 EDT
    assert structured["memory"]["episodes"] == 2
    selected = structured["checkpoint"].state.selected_memory
    assert len(selected) == 1 and selected[0].namespace == structured["memory"]["namespace"]
    assert structured["prior_task"]["checkpoint"]["state"]["selected_memory"] == []


def test_failed_scenario_cli_exit_and_report(tmp_path):
    scenario = scenarios()[0].model_copy(deep=True)
    scenario.expected.status = "failed"
    path = tmp_path / "suite.json"
    path.write_text(Suite(scenarios=[scenario]).model_dump_json(), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from waypoint_agent.cli import main; main()",
            "benchmark",
            "--suite",
            str(path),
            "--output",
            str(tmp_path / "result"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2, result.stderr
    assert (tmp_path / "result/report.json").exists()


async def test_infrastructure_error_is_reported(tmp_path, monkeypatch):
    import waypoint_agent.benchmark.runner as runner

    async def unavailable(*args, **kwargs):
        raise OSError("Synthetic infrastructure failure")

    monkeypatch.setattr(runner, "run_scenario", unavailable)
    report = await runner.run_suite(SUITE, tmp_path, selected=["retrieval"])
    assert report["summary"]["statuses"] == {"infrastructure_error": 1}
    assert report["summary"]["pass_rate"]["value"] == 0


async def test_partial_usage_and_explicit_cost():
    from waypoint_agent.benchmark.reporting import Pricing

    scenario = scenarios()[0]
    run = await run_scenario(scenario)
    state = run["checkpoint"].state
    state.model_kind = "live"
    state.model_config_values = {"model": "synthetic-test", "base_url": "test-provider"}
    pricing = Pricing(
        as_of="2026-09-22",
        model="synthetic-test",
        provider="test-provider",
        input_per_million_usd=1,
        output_per_million_usd=2,
    )
    responses = [e for e in run["events"] if e.kind == "model_response"]
    for response in responses:
        response.usage = {"prompt_tokens": 10, "completion_tokens": 5}
    report = summarize(scenario, run, pricing)
    assert report["metrics"]["inference_cost"]["approximate_usd"] == len(responses) * 20 / 1_000_000
    responses[-1].usage = {"prompt_tokens": 10}
    partial = summarize(scenario, run, pricing)["metrics"]
    assert partial["inference_cost"] is None
    assert "completion_tokens" not in partial["provider_usage"]
    responses[-1].usage = None
    assert summarize(scenario, run, pricing)["metrics"]["provider_usage"] is None
