import json
from openai import OpenAI
from schemas import ExpeditionPlan

client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama"
)

SYSTEM_PROMPT = f"""You are the Planning Agent for a student expedition swarm.
Your job is to generate a structured expedition plan.
You must output ONLY valid, raw JSON matching this schema, with no markdown formatting, no backticks, and no conversational text:

{json.dumps(ExpeditionPlan.model_json_schema(), indent=2)}
"""

USER_PROMPT = """Plan a 3-day technical expedition to Shenzhen visiting hardware hubs (e.g., DJI, HAX, Huaqiangbei).
The total budget must be under 1200 EUR.
"""

def generate_plan():
    print("Calling local model on RTX A5000...")
    response = client.chat.completions.create(
        model="qwen2.5:7b",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT}
        ],
        temperature=0.2,
    )
    raw_content = response.choices[0].message.content.strip()

    if raw_content.startswith("```"):
        raw_content = raw_content.split("```")[1]
        if raw_content.startswith("json"):
            raw_content = raw_content[4:]
        raw_content = raw_content.strip()

    plan = ExpeditionPlan.model_validate_json(raw_content)
    return plan

if __name__ == "__main__":
    plan = generate_plan()
    print("\n--- Successfully Parsed ExpeditionPlan ---")
    print(f"Destination: {plan.destination}")
    print(f"Duration: {plan.duration_days} days")
    print(f"Total Budget Stated: {plan.total_budget_eur} EUR")
    for d in plan.days:
        daily_total = sum(act.cost_eur for act in d.activities)
        print(f"  Day {d.day} ({d.focus}): {len(d.activities)} activities, Est. Cost: {daily_total:.2f} EUR")