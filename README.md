# AI Natives in the Loop: expedition swarm

Bachelor's thesis project (Aalto University). A small swarm of local LLM agents plans a multi-day
tech expedition (for example 7 days in Shenzhen or Silicon Valley). Every plan is checked against
real data and sent back for repair until it passes, or ends with the reasons it could not be fixed.

## What happens in a run
1. A scenario file (`scenarios/*.json`) describes the trip: city, days, budget, must-visit places.
2. A plausibility check rejects impossible requests before any model is called.
3. **Tech Planner** drafts the itinerary, **Logistics** adds times and visit prices, **Finance** adds hotel nights, meals and local transport by rule.
4. **Justice** (after **Audit** has judged the scenario and the **Dispatcher** has shared the work) checks the plan with deterministic skills (budget, schedule, OpenStreetMap place and
   travel-time checks, price floors) and sends it back to the right agent, up to 6 rounds.
5. After approval the **Dispatcher** runs Transit (routes) and Outreach (emails), then **Local Logistics** (visa, SIM with official sources), **Finance** (cost report in EUR and CNY) and **Static Web** (`handbook.md/json`).
6. The result is saved under `outputs/<run>/` (`index.md`, `plan.md`, `history.md`, `run.json`)
   with the version, the skills used and the full audit trail.

## Run it (Windows, PowerShell)
```powershell
.\setup.ps1                                                    # first run: installs everything, runs the default scenario
.\setup.ps1 -ScriptArgs "--scenario shenzhen_7day --modes none --repeats 3"
python -m swarm.code_agents.plausibility shenzhen_7day         # check a scenario and its place names
python tests/test_core.py; python tests/test_loop_offline.py   # offline tests, no model needed
```
A place OpenStreetMap cannot find (for example HAX) can get coordinates in the scenario: `"place_coordinates": {"HAX": [lat, lon]}` (copy them from a map). Then its driving times are measured too.
Choose the model in `model.txt` (default `qwen3:8b`, needs Ollama). Set `SWARM_OFFLINE=1` to avoid internet calls.

## Project layout
```
run_loop.py            the main program (start here)
swarm/
  core/          state machine, handoffs, schemas, scenario format, prompt loader, version stamp, paths
  llm_agents/    the agents that call the model, one file each: tech_planner, logistics, justice, transit, outreach
                 plus client.py (the one place that talks to Ollama), retry.py (shared retry loop), textcheck.py
  code_agents/   plain code, no model: audit, plausibility, dispatcher, finance, local_logistics, static_web,
                 reality, plan_quality, route_order, practical
  output/        output_writer (outputs/<run>/ files), batch_summary (summary_*.md)
  skills/        deterministic tools: budget and schedule checks, OpenStreetMap, OSRM, exchange rate
prompts/<agent>/SOUL.md   the persona and rules of each model-driven agent
data/  scenarios/  tests/  docs/
```

## Where to read more
- [docs/CODE_TOUR.md](docs/CODE_TOUR.md): every module in plain words, with likely questions and answers
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): design, agent roles, what is built and what is planned
- [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md): Edict (cft0808/edict, MIT) was used as design inspiration only

Every result row records the version (`version.txt`), the git commit and a hash of the prompts and
price tables, so each number can be traced to the exact code that produced it.
