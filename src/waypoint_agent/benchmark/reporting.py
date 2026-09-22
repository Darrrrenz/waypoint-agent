"""Metrics have explicit applicability; unknown usage/cost is never zero."""

import hashlib
import json
import subprocess
from collections import Counter


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def metadata(scenario):
    return {
        "scenario_hash": digest(scenario.model_dump(mode="json")),
        "config_hash": digest(scenario.inputs.model_dump(mode="json")),
        "semantic_clock": scenario.inputs.dataset.clock.isoformat(),
        "context": scenario.inputs.context.model_dump(mode="json"),
        "seed": None,
    }


def ratio(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def summarize(scenario, run):
    state, events = run["checkpoint"].state, run["events"]
    counts = Counter(e.kind for e in events)
    proposals = [e.data for e in events if e.kind == "tool_requested"]
    expected = scenario.expected
    allowed = expected.allowed_tools
    selection = [p["name"] in allowed for p in proposals] if allowed is not None else []
    constraints = expected.argument_constraints
    arguments = [
        all(p["arguments"].get(k) == v for k, v in constraints[p["name"]].items())
        for p in proposals
        if constraints is not None and p["name"] in constraints
    ]
    unnecessary = (
        max(0, len(proposals) - expected.max_tool_proposals)
        if expected.max_tool_proposals is not None
        else None
    )
    assertions = list(run["assertions"])
    for name, values in (("allowed_tools", selection), ("argument_constraints", arguments)):
        if values:
            assertions.append(
                {
                    "name": name,
                    "passed": all(values),
                    "detail": "" if all(values) else "Scenario action rule violated",
                }
            )
    if unnecessary is not None:
        assertions.append(
            {
                "name": "action_budget",
                "passed": unnecessary == 0,
                "detail": "" if unnecessary == 0 else "Unnecessary tool proposals",
            }
        )
    passed = all(a["passed"] for a in assertions)
    responses = [e for e in events if e.kind == "model_response"]
    known = [e.usage for e in responses if e.usage is not None]
    usage = (
        dict(sum((Counter(u) for u in known), Counter()))
        if known and len(known) == state.model_calls
        else None
    )
    metrics = {
        "tool_proposals": len(proposals),
        "write_proposals": sum(p["name"] == "update_calendar_event" for p in proposals),
        "execution_attempts": counts["read_attempt"] + counts["operation_requested"],
        "read_attempts": counts["read_attempt"],
        "write_attempts": counts["operation_requested"],
        "retries": counts["read_retry"],
        "mutations": len(run["operations"]),
        "logical_write_operations": len({a.operation_id for a in state.approvals}),
        "invalid_calls": counts["invalid_action"] + counts["tool_error"],
        "model_calls": state.model_calls,
        "provider_usage": usage,
        "usage_coverage": ratio(len(known), state.model_calls),
        "inference_cost": None,
        "tool_selection": ratio(sum(selection), len(selection)),
        "arguments": ratio(sum(arguments), len(arguments)),
        "unnecessary_actions": unnecessary,
        "disallowed_actions": selection.count(False) if allowed is not None else None,
        "active_seconds": state.active_seconds,
        "recovery": ratio(int(passed and run["fault_triggered"]), 1)
        if scenario.fault
        else ratio(0, 0),
    }
    return {
        "status": "passed" if passed else "failed",
        "assertions": assertions,
        "runtime_status": state.task.status,
        "metrics": metrics,
        "execution_limits": state.limits,
    }


def revision():
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
        return {"revision": sha, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"revision": None, "dirty": None}


def write_reports(output, suite, results, backend, repeats):
    statuses = Counter(r["status"] for r in results)
    recoveries = [r["metrics"]["recovery"] for r in results if r["metrics"]]
    report = {
        "report_schema_version": 1,
        "scenario_schema_version": suite.schema_version,
        "suite_hash": digest(suite.model_dump(mode="json")),
        **revision(),
        "backend": backend,
        "model_mode": "deterministic",
        "repeats": repeats,
        "summary": {
            "pass_rate": ratio(statuses["passed"], len(results)),
            "statuses": dict(statuses),
            "recovery": ratio(
                sum(r["numerator"] for r in recoveries), sum(r["denominator"] for r in recoveries)
            ),
        },
        "results": results,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Deterministic benchmark",
        "",
        f"Passed: {statuses['passed']}/{len(results)}; storage: {backend}; repeats: {repeats}.",
        "",
        "Synthetic harness approvals; no live inference. Unknown usage/cost: N/A.",
        "",
        "| Scenario | Repeat | Result | Runtime | Calls | Mutations | Failures |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in results:
        metrics = row["metrics"] or {}
        failures = "; ".join(
            a["name"] + ": " + a["detail"] for a in row["assertions"] if not a["passed"]
        ) or row.get("error", "")
        lines.append(
            f"| {row['id']} | {row['repeat']} | {row['status']} | "
            f"{row.get('runtime_status', 'N/A')} | {metrics.get('model_calls', 'N/A')} | "
            f"{metrics.get('mutations', 'N/A')} | {failures.replace('|', '/')} |"
        )
    lines += [
        "",
        "Full assertions, denominators, hashes, execution limits, timing and usage "
        "coverage are in report.json. Per-run files contain trajectories and world snapshots.",
    ]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
