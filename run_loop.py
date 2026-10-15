import json
from openai import OpenAI
from pydantic import ValidationError
from schemas import ExpeditionPlan, AuditResult
import subprocess
import time
import urllib.request
import os

def ensure_ollama_running():
    """Checks if Ollama is listening; if not, starts the background process."""
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1)
        return
    except Exception:
        print("[INFO] Ollama server is not running. Launching background process...")
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        ollama_bin = os.path.join(local_app_data, "Programs", "Ollama", "ollama.exe")
        subprocess.Popen([ollama_bin, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Wait up to 10 seconds for the server to become ready
        for _ in range(10):
            try:
                urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1)
                print("[INFO] Ollama server successfully started.")
                return
            except Exception:
                time.sleep(1)
        print("[WARNING] Ollama process launched, but connection check timed out.")

ensure_ollama_running()

# 1. Initialize local Ollama client pointing to your local RTX A5000
client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama",
)

MODEL_NAME = "qwen2.5:7b"
MAX_BUDGET_EUR = 1200.0
REQUIRED_HUBS = ["DJI", "HAX", "Huaqiangbei"]

def get_budget_value(plan: ExpeditionPlan) -> float:
    """Safely extract the total budget/cost field regardless of exact field naming."""
    data = plan.model_dump()
    for key, val in data.items():
        if isinstance(val, (int, float)) and ("cost" in key.lower() or "budget" in key.lower()):
            return float(val)
    return 0.0

def planning_agent(user_request: str, critique_feedback: list[str] = None) -> ExpeditionPlan:
    """Planning Agent: Generates or updates the itinerary based on schema and critiques."""
    system_prompt = (
        "You are the Lead Planning Agent for global technical expeditions.\n"
        "You must respond ONLY with a raw JSON object matching this exact JSON schema:\n"
        f"{json.dumps(ExpeditionPlan.model_json_schema(), indent=2)}\n\n"
        "Do not wrap the JSON in markdown code blocks. Output raw JSON only."
    )
    
    user_prompt = f"User Request: {user_request}\n"
    if critique_feedback:
        user_prompt += (
            "\nATTENTION: Your previous plan was REJECTED by the Auditor with the following errors:\n"
            + "\n".join(f"- {note}" for note in critique_feedback)
            + "\nPlease revise the itinerary to strictly fix these violations while maintaining valid JSON."
        )

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
    raw_content = response.choices[0].message.content.strip()
    if raw_content.startswith("```"):
        raw_content = raw_content.strip("`").removeprefix("json").strip()
    return ExpeditionPlan.model_validate_json(raw_content)

def auditor_agent(plan: ExpeditionPlan) -> AuditResult:
    """Auditor Agent: Deterministic gatekeeper checking hard constraints."""
    critique_notes = []
    
    # Check 1: Budget Ceiling
    total_cost = get_budget_value(plan)
    if total_cost > MAX_BUDGET_EUR:
        critique_notes.append(
            f"Budget exceeded: Total cost is {total_cost:.2f} EUR, "
            f"which exceeds the maximum allowance of {MAX_BUDGET_EUR:.2f} EUR."
        )
    
    # Check 2: Mandatory Hardware Hubs in Shenzhen (searches full JSON text)
    full_plan_text = plan.model_dump_json().lower()
    for hub in REQUIRED_HUBS:
        if hub.lower() not in full_plan_text:
            critique_notes.append(
                f"Missing mandatory technical hub: '{hub}' was not included in any day's schedule."
            )
            
    passed = len(critique_notes) == 0
    return AuditResult(passed=passed, critique_notes=critique_notes)

def run_swarm_loop(request: str, max_iterations: int = 4):
    """Closed-loop feedback control runner."""
    print("=" * 60)
    print(f"[INPUT] User Expedition Request:\n{request}")
    print("=" * 60)

    critique_feedback = []
    iteration = 1

    while iteration <= max_iterations:
        print(f"\n--- [Iteration {iteration}] Planning Agent Generating ---")
        try:
            plan = planning_agent(request, critique_feedback)
            total_cost = get_budget_value(plan)
            print(f"[OK] Generated Valid Plan | Estimated Cost: {total_cost:.2f} EUR")
        except ValidationError as e:
            print(f"[FAIL] Schema validation error on iteration {iteration}: {e}")
            critique_feedback = [f"JSON schema validation failed: {str(e)}"]
            iteration += 1
            continue

        print(f"--- [Iteration {iteration}] Auditor Agent Verifying ---")
        audit = auditor_agent(plan)

        if audit.passed:
            print(f"\n>>> [SUCCESS] Plan Approved by Auditor on iteration {iteration}!")
            print(plan.model_dump_json(indent=2))
            return plan
        else:
            print(f"[REJECTED] Auditor flagged {len(audit.critique_notes)} issue(s):")
            for note in audit.critique_notes:
                print(f"  * {note}")
            critique_feedback = audit.critique_notes
            iteration += 1

    print(f"\n[ABORT] Exceeded maximum iterations ({max_iterations}) without convergence.")
    return None

if __name__ == "__main__":
    # Intentionally omit hub names in the initial prompt so Iteration 1 triggers a rejection!
    test_request = (
        "Organize a 3-day robotics and hardware technical expedition to Shenzhen. "
        "Strict total budget is 1200 EUR."
    )
    run_swarm_loop(test_request)