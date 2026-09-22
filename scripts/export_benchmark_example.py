"""Copy actual reports and compact paired evidence; never synthesize run results."""

import argparse
import json
from pathlib import Path


def export(source, output):
    output.mkdir(parents=True, exist_ok=True)
    for name in ("report.json", "report.md"):
        (output / name).write_text((source / name).read_text(encoding="utf-8"), encoding="utf-8")
    report = json.loads((source / "report.json").read_text(encoding="utf-8"))
    paired = {
        "report_schema_version": report["report_schema_version"],
        "revision": report["revision"],
        "dirty": report["dirty"],
        "environment": report["backend"],
        "decision_source": "explicit synthetic test-harness decisions",
        "process_restart": "not_measured",
        "runs": [],
    }
    for scenario in ("memory_disabled", "memory_structured"):
        run = json.loads((source / f"{scenario}-0.json").read_text(encoding="utf-8"))
        state = run["checkpoint"]["state"]
        paired["runs"].append(
            {
                "scenario": scenario,
                "task": state["task"],
                "selected_memory": state["selected_memory"],
                "calendar_before": run["before"],
                "calendar_after": run["after"],
                "approvals": state["approvals"],
                "operations": run["operations"],
                "findings": state["findings"],
                "memory_projection": state["memory_projection"],
                "memory": run["memory"],
                "harness_actions": run["harness_actions"],
                "assertions": run["assertions"],
                "prior_task_id": run["prior_task"]["checkpoint"]["state"]["task"]["id"],
            }
        )
    (output / "memory-pair.json").write_text(json.dumps(paired, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("artifacts/benchmark"))
    parser.add_argument("--output", type=Path, default=Path("docs/benchmark-example"))
    args = parser.parse_args()
    export(args.input, args.output)
