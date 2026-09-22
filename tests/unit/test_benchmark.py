import json
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
