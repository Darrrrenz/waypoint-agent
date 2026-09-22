"""Metrics have explicit applicability; unknown usage/cost is never zero."""

import hashlib
import json
import subprocess
from collections import Counter
from datetime import date

from pydantic import Field

from waypoint_agent.schemas import Schema


class Pricing(Schema):
    as_of: date
    provider: str
    model: str
    input_per_million_usd: float = Field(ge=0)
    output_per_million_usd: float = Field(ge=0)


def inference_cost(state, responses, pricing):
    if (
        pricing is None
        or state.model_kind != "live"
        or state.model_config_values.get("model") != pricing.model
        or state.model_config_values.get("base_url") != pricing.provider
        or len(responses) != state.model_calls
        or not responses
    ):
        return None
    if any(
        e.usage is None or not {"prompt_tokens", "completion_tokens"} <= e.usage.keys()
        for e in responses
    ):
        return None
    amount = (
        sum(
            e.usage["prompt_tokens"] * pricing.input_per_million_usd
            + e.usage["completion_tokens"] * pricing.output_per_million_usd
            for e in responses
        )
        / 1_000_000
    )
    return {"approximate_usd": amount, "pricing": pricing.model_dump(mode="json")}


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


def summarize(scenario, run, pricing=None):
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
    common = set.intersection(*(set(u) for u in known)) if known else set()
    usage = (
        {k: sum(u[k] for u in known) for k in common}
        if known and len(known) == state.model_calls and common
        else None
    )
    metrics = {
        "tool_proposals": len(proposals),
        "write_proposals": sum(p["name"] == "update_calendar_event" for p in proposals),
        "execution_attempts": counts["read_attempt"] + counts["operation_requested"],
        "read_attempts": counts["read_attempt"],
        "write_attempts": counts["operation_requested"],
        "retries": sum(e.kind == "read_attempt" and e.data["attempt"] > 1 for e in events)
        + counts["operation_requested"]
        - len({e.data["operation_id"] for e in events if e.kind == "operation_requested"}),
        "proposal_validations": counts["proposal_validation"],
        "mutations": len(run["operations"]),
        "harness_mutations": sum(a.get("action") == "occupy_slot" for a in run["harness_actions"]),
        "logical_write_operations": len({a.operation_id for a in state.approvals}),
        "invalid_calls": counts["invalid_action"]
        + sum(
            e.kind == "tool_error"
            and e.data.get("error")
            in ("ValidationError", "ValueError", "PermissionError", "KeyError")
            for e in events
        ),
        "model_calls": state.model_calls,
        "provider_usage": usage,
        "usage_coverage": ratio(len(known), state.model_calls),
        "inference_cost": inference_cost(state, responses, pricing),
        "tool_selection": ratio(sum(selection), len(selection)),
        "arguments": ratio(sum(arguments), len(arguments)),
        "unnecessary_actions": unnecessary,
        "disallowed_actions": selection.count(False) if allowed is not None else None,
        "active_seconds": state.active_seconds,
        "preference_satisfaction": ratio(int(run["memory"]["preference_satisfied"]), 1)
        if run["memory"]["preference_satisfied"] is not None
        else ratio(0, 0),
        "recovery": ratio(int(passed and run["fault_triggered"]), 1)
        if scenario.fault
        else ratio(0, 0),
    }
    prior_result = run.get("prior_result")
    if prior_result:
        prior_metrics = prior_result["metrics"]
        for key in (
            "tool_proposals",
            "write_proposals",
            "execution_attempts",
            "read_attempts",
            "write_attempts",
            "retries",
            "mutations",
            "logical_write_operations",
            "invalid_calls",
            "model_calls",
            "active_seconds",
            "proposal_validations",
            "harness_mutations",
        ):
            metrics[key] += prior_metrics[key]
        for key in ("tool_selection", "arguments", "usage_coverage"):
            metrics[key] = ratio(
                metrics[key]["numerator"] + prior_metrics[key]["numerator"],
                metrics[key]["denominator"] + prior_metrics[key]["denominator"],
            )
        for key in ("unnecessary_actions", "disallowed_actions"):
            metrics[key] = (
                metrics[key] + prior_metrics[key]
                if metrics[key] is not None and prior_metrics[key] is not None
                else None
            )
        # A partial task total must never be presented as a full multi-task total.
        metrics["provider_usage"] = None
        metrics["inference_cost"] = None
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


def write_reports(output, suite, results, backend, repeats, pricing=None):
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
        "pricing": pricing.model_dump(mode="json") if pricing else None,
        "summary": {
            "pass_rate": ratio(statuses["passed"], len(results)),
            "statuses": dict(statuses),
            "recovery": ratio(
                sum(r["numerator"] for r in recoveries),
                sum(r["recovery_applicable"] for r in results),
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
        "| Scenario / repeat | Proposals | Attempts | Retries | Invalid | Active s | Wall s |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in results:
        m = row["metrics"] or {}
        active = f"{m['active_seconds']:.4f}" if "active_seconds" in m else "N/A"
        lines.append(
            f"| {row['id']} / {row['repeat']} | {m.get('tool_proposals', 'N/A')} | "
            f"{m.get('execution_attempts', 'N/A')} | {m.get('retries', 'N/A')} | "
            f"{m.get('invalid_calls', 'N/A')} | {active} | "
            f"{row['harness_wall_seconds']:.4f} |"
        )

    def display(value):
        return f"{value['numerator']}/{value['denominator']}" if value["denominator"] else "N/A (0)"

    lines += [
        "",
        "| Scenario / repeat | Tool choices | Arguments | Recovery | Preference | "
        "Unnecessary | Disallowed | Usage | Cost |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in results:
        m = row["metrics"]
        if m is None:
            continue
        values = [
            display(m[k])
            for k in ("tool_selection", "arguments", "recovery", "preference_satisfaction")
        ]
        values += [
            str(m[k]) if m[k] is not None else "N/A"
            for k in (
                "unnecessary_actions",
                "disallowed_actions",
                "provider_usage",
                "inference_cost",
            )
        ]
        lines.append(f"| {row['id']} / {row['repeat']} | " + " | ".join(values) + " |")
    lines += [
        "",
        "Mutations include the explicitly labeled competing harness actor. "
        "Preference satisfaction is separate from scenario success.",
        "",
        "Full assertions, denominators, hashes, execution limits, timing and usage "
        "coverage are in report.json. Per-run files contain trajectories and world snapshots.",
    ]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
