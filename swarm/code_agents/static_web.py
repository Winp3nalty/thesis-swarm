"""Static Web: builds the expedition handbook as plain Markdown and JSON (Edict's works ministry, 工部).

Code, not a model. It gathers what the other agents produced into two files that any static host can serve:
  handbook.md   one readable page: plan, costs, routes, emails, practical notes with sources
  handbook.json the same content as data (for the GitHub Pages portal planned for v1.0)
Nothing is added that an agent did not produce; a part that failed is listed under "Problems".
"""
from __future__ import annotations

from swarm.code_agents.finance import finance_md
from swarm.code_agents.practical import practical_md


def agent_static_web(plan: dict, req, status: str, finance: dict | None, local: dict | None,
                     coordination: dict | None, warnings: list[str], version: str, printer=print) -> dict:
    """plan is the approved plan as a dict. Returns {"handbook.md": text, "handbook.json": data}."""
    from swarm.output.output_writer import render_plan_md  # imported here: output_writer is a sibling module

    transit = [{"day": t["case"]["day"], "stops": t["case"]["stops"], "ok": t["ok"],
                "legs": t["result"]["legs"] if t["ok"] else [], "order": t["case"].get("order"),
                "error": t["error"]} for t in (coordination or {}).get("transit", [])]
    emails = [{"to": o["case"]["name"], "institution": o["case"]["institution"], "ok": o["ok"],
               "email": o["result"], "error": o["error"]} for o in (coordination or {}).get("outreach", [])]
    problems = [f"Transit day {t['day']} failed: {t['error']}" for t in transit if not t["ok"]]
    problems += [f"Email to {e['to']} failed: {e['error']}" for e in emails if not e["ok"]]
    problems += (local or {}).get("problems", [])

    lines = [f"# Expedition handbook: {req.destination}", "",
             f"- **Status:** {status}", f"- **Budget:** {req.budget_eur:g} EUR" + (f" for {req.group_size} travellers" if req.group_size > 1 else ""), f"- **Version:** {version}", ""]
    if warnings:
        lines += ["## Warnings (plan approved with reservations)", ""] + [f"- {w}" for w in warnings] + [""]
    lines += [render_plan_md(plan, title=None, day_level=2), ""]
    if finance:
        lines += [finance_md(finance), ""]
    if transit:
        lines += ["## Routes", ""]
        for t in transit:
            lines.append(f"- **Day {t['day']}:** " + " → ".join(t["stops"])
                         + (f" ({sum(l['minutes'] for l in t['legs'])} min, "
                            f"{sum(l['cost_eur'] for l in t['legs']):.2f} EUR)" if t["ok"] else " (route FAILED)"))
        lines.append("")
    if emails:
        lines += ["## Outreach emails", ""]
        for e in emails:
            if e["ok"]:
                m = e["email"]
                lines += [f"### {e['to']}, {e['institution']}", "", f"**Subject:** {m['subject']}", "", m["greeting"], "",
                          m["body"], "", m["sign_off"], ""]
            else:
                lines += [f"### {e['to']}, {e['institution']}", "", "_Email FAILED, see the run folder._", ""]
    if local and local.get("info"):
        lines.append(practical_md(local["info"]))
    if problems:
        lines += ["## Problems", ""] + [f"- {p}" for p in problems] + [""]
    data = {"destination": req.destination, "status": status, "days": req.days, "budget_eur": req.budget_eur,
            "version": version, "warnings": warnings, "plan": plan, "finance": finance, "transit": transit,
            "outreach": emails, "practical": (local or {}).get("info", {}), "problems": problems}
    printer(f"  [Static Web] handbook built: {len(lines)} lines, {len(problems)} problem(s) listed")
    return {"handbook.md": "\n".join(lines), "handbook.json": data}
