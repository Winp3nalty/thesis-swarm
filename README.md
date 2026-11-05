# AI Natives in the Loop: expedition swarm

Bachelor's thesis project (Aalto University). A small swarm of local LLM agents plans a multi-day
tech expedition (for example 7 days in Shenzhen or Silicon Valley). Every plan is checked against
real data and sent back for repair until it passes, or ends with the reasons it could not be fixed.

## What happens in a run
1. A scenario file (`scenarios/*.json`) describes the trip: city, days, budget, must-visit places.
2. A plausibility check rejects impossible requests before any model is called.
3. **Tech Planner** drafts the itinerary, **Logistics** adds times, travel and costs.
4. **Auditor** checks the plan with deterministic skills (budget, schedule, OpenStreetMap place and
   travel-time checks, price floors) and sends it back to the right agent, up to 6 rounds.
5. The result is saved under `outputs/<run>/` (`index.md`, `plan.md`, `history.md`, `run.json`)
   with the version, the skills used and the full audit trail.

## Run it (Windows, PowerShell)
```powershell
.\setup.ps1                                                    # first run: installs everything, runs the default scenario
.\setup.ps1 -ScriptArgs "--scenario shenzhen_7day --modes none --repeats 3"
python plausibility.py shenzhen_7day                           # check a scenario and its place names
python test_core.py; python test_loop_offline.py               # offline tests, no model needed
```
Choose the model in `model.txt` (default `qwen3:8b`, needs Ollama). Set `SWARM_OFFLINE=1` to avoid internet calls.

## Where to read more
- [CODE_TOUR.md](CODE_TOUR.md): every module in plain words, with likely questions and answers
- [ARCHITECTURE.md](ARCHITECTURE.md): design, agent roles, what is built and what is planned
- [THIRD_PARTY.md](THIRD_PARTY.md): Edict (cft0808/edict, MIT) was used as design inspiration only

Every result row records the version (`version.txt`), the git commit and a hash of the prompts and
price tables, so each number can be traced to the exact code that produced it.
