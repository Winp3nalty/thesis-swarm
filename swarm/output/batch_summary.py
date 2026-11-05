"""Summary table for a batch of runs (python run_loop.py --repeats N).

One row per run: status, iterations, seconds and the total cost of the approved plan. The table
groups the rows by fault mode, so repeated runs on one version can be compared at a glance.
It is saved to outputs/summary_<scenario>_<time>.md next to the single-run folders.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


def run_row(mode: str, last_run: dict, plan) -> dict:
    cost = None
    if plan is not None:
        cost = round(sum(a.cost_eur for d in plan.days for a in d.activities), 2)
    return {"mode": mode, "status": last_run.get("status", "UNKNOWN"),
            "iterations": last_run.get("iterations", 0),
            "seconds": round(last_run.get("wall_clock_s", 0.0), 1), "cost_eur": cost}


def summarize(rows: list[dict]) -> list[str]:
    lines = ["| Mode | Runs | Statuses | Mean iterations | Mean seconds | Mean cost (EUR) |", "|---|---|---|---|---|---|"]
    for mode in dict.fromkeys(r["mode"] for r in rows):
        group = [r for r in rows if r["mode"] == mode]
        statuses: dict[str, int] = {}
        for r in group:
            statuses[r["status"]] = statuses.get(r["status"], 0) + 1
        costs = [r["cost_eur"] for r in group if r["cost_eur"] is not None]
        lines.append(f"| {mode} | {len(group)} | {', '.join(f'{k} x{v}' for k, v in sorted(statuses.items()))} "
                     f"| {sum(r['iterations'] for r in group) / len(group):.1f} "
                     f"| {sum(r['seconds'] for r in group) / len(group):.0f} "
                     f"| {f'{sum(costs) / len(costs):.0f}' if costs else '-'} |")
    return lines


def save_summary(scenario: str, model: str, version: str, rows: list[dict], root=None) -> str:
    out = [f"# Batch summary: {scenario}", "", f"- **Version:** {version}", f"- **Model:** {model}",
           f"- **Runs:** {len(rows)}", "", *summarize(rows), "", "## Every run", "",
           "| # | Mode | Status | Iterations | Seconds | Cost (EUR) |", "|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        out.append(f"| {i} | {r['mode']} | {r['status']} | {r['iterations']} | {r['seconds']} | "
                   f"{r['cost_eur'] if r['cost_eur'] is not None else '-'} |")
    d = Path(root) if root else Path("outputs")
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"summary_{Path(scenario).stem}_{datetime.now():%Y-%m-%d_%H-%M-%S}.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return "\n".join(out) + f"\n\n[SUMMARY] saved to {path}"
