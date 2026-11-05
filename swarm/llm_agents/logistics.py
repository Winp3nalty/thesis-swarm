"""
Logistics (Agent 2): turns the rough itinerary into a timed schedule with visit prices (persona and rules in
prompts/logistics/SOUL.md). Hotel, meals and local transport are not its job; Finance adds them.
"""
from __future__ import annotations

from swarm.code_agents.reality import cost_guide_text
from swarm.core.schemas import ExpeditionPlan, RoughItinerary
from swarm.core.soul_loader import build_system_prompt
from swarm.core.state_machine import TaskRecord
from swarm.llm_agents import client
from swarm.skills import run_skill


def _exchange_rate_context(record: TaskRecord = None) -> str:
    """Call the internet skill live_exchange_rate and return a prompt block with real rates.

    The orchestrator calls the skill (small local models call tools unreliably) and logs
    every call, with its source (live, cache or static), in the audit log.
    """
    lines = []
    for ccy in ("CNY", "USD"):
        res = run_skill("live_exchange_rate", amount=1, from_ccy="EUR", to_ccy=ccy)
        if not res["ok"]:
            if record:
                record.note("logistics", f"skill live_exchange_rate failed: {res['error']}")
            continue
        r = res["result"]
        lines.append(f"1 EUR = {r['amount']} {ccy}")
        msg = f"skill live_exchange_rate EUR->{ccy}: rate={r['rate']} source={r['source']} date={r['rate_date'] or 'n/a'}"
        print(f"  [Skill] {msg}" + (" (one web request fetched all rates; the other pair reads the cache)"
                                    if r["source"] == "live" and ccy == "CNY" else ""))
        if record:
            record.note("logistics", msg)
    if not lines:
        return ""
    return ("\nCurrent exchange rates (use them for any price quoted in CNY or USD; "
            "cost_eur must always be in EUR):\n" + "\n".join(f"- {l}" for l in lines) + "\n")


def agent_logistics_specialist(
    rough_plan: RoughItinerary,
    budget_cap: float,
    previous_detailed: ExpeditionPlan = None,
    critique_feedback: list[str] = None,
    record: TaskRecord = None,
    temperature: float = 0.2,
    cost_ref: dict = None,
) -> ExpeditionPlan:
    print("  [Agent 2: Logistics Specialist] Enforcing time slots, transit, and EUR costs...")
    system_prompt = build_system_prompt(
        "logistics",
        extra=f"Keep total costs strictly under {budget_cap} EUR.",
    )
    user_prompt = f"Rough Outline to Follow:\n{rough_plan.model_dump_json(indent=2)}\n"
    user_prompt += _exchange_rate_context(record)
    user_prompt += cost_guide_text(len(rough_plan.days), cost_ref)

    if previous_detailed and critique_feedback:
        user_prompt += (
            f"\nYOUR PREVIOUS SCHEDULE DRAFT:\n{previous_detailed.model_dump_json(indent=2)}\n"
            "\nFIX THESE LOGISTICS/BUDGET ERRORS (DO NOT DROP ANY HUBS):\n"
            + "\n".join(f"- {note}" for note in critique_feedback)
        )
    plan = client.call_structured_llm(system_prompt, user_prompt, ExpeditionPlan, temperature=temperature)
    if previous_detailed and plan.model_dump() == previous_detailed.model_dump():
        # The model ignored the feedback and repeated itself: say so and sample more freely.
        print("  [Agent 2] Plan identical to the previous draft. Asking again with a warning.")
        user_prompt += ("\n\nYOUR NEW DRAFT WAS IDENTICAL TO THE PREVIOUS ONE, so the problems above are still "
                        "unresolved. Change the schedule and costs as the notes describe.")
        plan = client.call_structured_llm(system_prompt, user_prompt, ExpeditionPlan,
                                   temperature=max(temperature, 0.8))
    return plan
