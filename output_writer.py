"""
Saves agent responses as readable Markdown plus JSON copies, one folder per run.

    outputs/<date>_<time>_<kind>_<label>_<model>/
        index.md      <- start here: status, summary table, links to everything else
        ...           <- one readable .md and one .json per case or per run

Open the .md files with Ctrl+Shift+V in VS Code (Markdown preview). The .json copies are
for scripts and the thesis pipeline. No other project modules are imported here.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

OUTPUT_ROOT = "outputs"


def _slug(text) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-") or "x"


def _cell(text) -> str:
    """Make text safe inside a Markdown table cell."""
    return str(text).replace("|", "/").replace("\n", " ").strip()


def new_run_dir(kind: str, model: str, label: str = "", root=None) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    name = "_".join([stamp, kind] + ([_slug(label)] if label else []) + [_slug(model)])
    base = Path(root or OUTPUT_ROOT) / name
    d, n = base, 1
    while d.exists():  # two runs in the same second must not overwrite each other
        n += 1
        d = Path(f"{base}-{n}")
    d.mkdir(parents=True)
    return d


def write_text(path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def write_json(path, obj) -> None:
    write_text(path, json.dumps(obj, indent=2, ensure_ascii=False, default=str))


# ==========================================
# Swarm (expedition plans)
# ==========================================
def render_plan_md(plan: dict, title: str | None = "Expedition plan", day_level: int = 2) -> str:
    total = sum(a["cost_eur"] for d in plan["days"] for a in d["activities"])
    lines = []
    if title:
        lines += [f"# {title}", ""]
    lines += [f"- **Destination:** {plan.get('destination', '?')}",
              f"- **Days:** {len(plan['days'])}",
              f"- **Total cost (summed from activities):** {total:.2f} EUR", ""]
    for day in plan["days"]:
        lines += [f"{'#' * day_level} Day {day['day']}: {day['focus_topic']}", ""]
        if not day["activities"]:
            lines += ["_No activities planned._", ""]
            continue
        lines += ["| Time | Location | What happens | Transit | Cost (EUR) |", "|---|---|---|---|---|"]
        for a in day["activities"]:
            lines.append(f"| {a['start_time']}-{a['end_time']} | {_cell(a['location'])} | "
                         f"{_cell(a['activity_description'])} | {_cell(a['transit_mode'])} | {a['cost_eur']:.2f} |")
        day_total = sum(a["cost_eur"] for a in day["activities"])
        lines += ["", f"Day total: {day_total:.2f} EUR", ""]
    return "\n".join(lines)


def render_iterations_md(iterations: list[dict]) -> str:
    lines = ["# Iteration history", "",
             "Every draft the agents produced and what the Auditor said about it.", ""]
    for it in iterations:
        n = it["iteration"]
        if "error" in it:
            lines += [f"## Iteration {n}: the model's reply could not be parsed", "", f"`{_cell(it['error'])}`", ""]
            continue
        audit = it["audit"]
        verdict = "PASSED" if audit["passed"] else f"REJECTED (blame: {audit['failed_agent']})"
        agents = "Tech Planner, then Logistics" if it["planner_ran"] else "Logistics only (direct fix)"
        lines += [f"## Iteration {n}: {verdict}", "", f"- Agents run: {agents}",
                  f"- Summed cost: {it['total_cost']:.2f} EUR"]
        lines += [f"- Auditor note: {note}" for note in audit["critique_notes"]]
        if it.get("verification"):
            lines += ["", "<details><summary>Online checks (OpenStreetMap)</summary>", ""]
            for v in it["verification"]:
                state = {True: "ok", False: "NOT FOUND", None: "unverified"}.get(v["found"], "?")
                lines.append(f"- {v['check']}: {v['name']}: {state} ({v['source']}) {v['detail']}")
            lines += ["", "</details>"]
        lines += ["", "<details><summary>Planner draft (hubs per day)</summary>", ""]
        for d in it["rough_plan"]["days"]:
            hubs = ", ".join(h["hub_name"] for h in d["target_hubs"]) or "none"
            lines.append(f"- Day {d['day']} ({d['focus_topic']}): {hubs}")
        lines += ["", "</details>", "", "<details><summary>Logistics schedule</summary>", "",
                  render_plan_md(it["detailed_plan"], title=None, day_level=4), "", "</details>", ""]
    return "\n".join(lines)


def write_swarm_run(request, mode, model, gpu, status, iterations, final_plan, audit_log, root=None,
                    warnings=None) -> Path:
    d = new_run_dir("swarm", model, label=mode, root=root)
    write_text(d / "history.md", render_iterations_md(iterations))
    write_json(d / "history.json", iterations)
    from version_info import version_info, version_line
    write_json(d / "run.json", {"request": request, "fault_mode": mode, "model": model, "gpu": gpu,
                                "status": status, "iterations": len(iterations), **version_info()})
    index = ["# Swarm run", "", f"- **Version:** {version_line()}", f"- **Status:** {status}", f"- **Fault mode:** {mode}",
             f"- **Model:** {model}", f"- **GPU:** {gpu}", f"- **Iterations:** {len(iterations)}",
             f"- **Audit log (state path and handovers):** `{audit_log}`", "",
             "## Request", "", f"> {request}", "", "## Files", ""]
    if final_plan:
        plan_md = render_plan_md(final_plan, title="Approved expedition plan")
        if warnings:
            plan_md += "\n## Warnings (approved by the Auditor with reservations)\n\n" + "\n".join(f"- {x}" for x in warnings) + "\n"
        write_text(d / "plan.md", plan_md)
        write_json(d / "plan.json", final_plan)
        index += ["- [plan.md](plan.md): the approved expedition plan, readable",
                  "- [plan.json](plan.json): the same plan as data"]
    else:
        index.append("- No plan was approved in this run.")
    index += ["- [history.md](history.md): every draft and the Auditor's verdict",
              "- [history.json](history.json): the same data for scripts",
              "- [run.json](run.json): settings and version of this run", ""]
    write_text(d / "index.md", "\n".join(index))
    return d


# ==========================================
# Worker agents (Transit, Outreach)
# ==========================================
def _status_line(info: dict, final_ok: bool, error: str = "") -> str:
    first = ("schema valid" if info.get("first_schema_ok") else "schema INVALID") + ", " + \
            ("content rules met" if info.get("first_semantic_ok") else "content rules BROKEN")
    line = (f"**Result:** {'valid' if final_ok else 'FAILED'} after {info.get('attempts', 0)} attempt(s). "
            f"First attempt: {first}.")
    return line + (f"\n\n**Error:** `{_cell(error)}`" if error else "")


def _attempts_md(info: dict) -> list[str]:
    names = {"ok": "accepted", "schema_error": "rejected (JSON or schema)", "content_problems": "rejected (content rules)"}
    lines = ["## Attempts", ""]
    for a in info.get("attempt_log", []):
        lines.append(f"### Attempt {a['attempt']}: {names.get(a['status'], a['status'])}")
        lines += [f"- {p}" for p in a["problems"]]
        if a.get("rejected_text"):
            lines += ["", "Rejected text:", "", "> " + a["rejected_text"].replace("\n", "\n> ")]
        lines.append("")
    return lines


def render_transit_md(case: dict, info: dict, result: dict | None, final_ok: bool, error: str = "") -> str:
    lines = [f"# Transit {case['id']}: {case['city']}", "",
             "**Stops:** " + " → ".join(case["stops"]), "", _status_line(info, final_ok, error), ""]
    if result:
        lines += ["## Route", "", "| # | From | To | Mode | Minutes | Cost (EUR) |", "|---|---|---|---|---|---|"]
        for i, leg in enumerate(result["legs"], 1):
            lines.append(f"| {i} | {_cell(leg['from_stop'])} | {_cell(leg['to_stop'])} | {_cell(leg['mode'])} | "
                         f"{leg['minutes']} | {leg['cost_eur']:.2f} |")
        lines += ["", f"Total: {sum(l['minutes'] for l in result['legs'])} min, "
                      f"{sum(l['cost_eur'] for l in result['legs']):.2f} EUR", ""]
    return "\n".join(lines + _attempts_md(info))


def render_outreach_md(case: dict, info: dict, result: dict | None, final_ok: bool, error: str = "") -> str:
    lines = [f"# Outreach {case['id']}: {case['name']}, {case['institution']}", "",
             "**Research topics:** " + "; ".join(case["topics"]), f"**Expedition city:** {case['city']}", "",
             _status_line(info, final_ok, error), ""]
    if result:
        lines += ["## Final email", "", f"**Subject:** {result['subject']}", "", result["greeting"], "",
                  result["body"], "", result["sign_off"], "",
                  f"_Body length: {len(result['body'].split())} words_", ""]
    return "\n".join(lines + _attempts_md(info))


def write_workers_index(run_dir: Path, model: str, gpu: str, rows: list[dict], summaries: dict) -> None:
    from version_info import version_line
    lines = ["# Worker agent test run", "", f"- **Version:** {version_line()}", f"- **Model:** {model}",
             f"- **GPU:** {gpu}", ""]
    for agent, s in summaries.items():
        n = s["n"]
        lines += [f"## {agent.capitalize()} agent", "",
                  f"- First-attempt schema-valid: {s['schema']}/{n}",
                  f"- First-attempt fully valid (schema and content rules): {s['full']}/{n}",
                  f"- Valid after retries: {s['final']}/{n}", "",
                  "| Case | Final | Attempts | First attempt | What was wrong first | File |",
                  "|---|---|---|---|---|---|"]
        for r in (x for x in rows if x["agent"] == agent):
            first = "ok" if r["first_semantic_ok"] else ("schema invalid" if not r["first_schema_ok"] else "content rules")
            lines.append(f"| {r['case_id']} | {'valid' if r['final_ok'] else 'FAILED'} | {r['attempts']} | "
                         f"{first} | {_cell(r['first_problems'] or r.get('error') or '')} | [{r['case_id']}.md]({r['link']}) |")
        lines.append("")
    write_text(Path(run_dir) / "index.md", "\n".join(lines))
