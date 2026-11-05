# Code tour

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

Module names below are given without their folder; the layout above says where each lives.

Read this top to bottom once. Each section says what a file does, why it exists, and a question
you may be asked about it. The big picture comes first.

## The big picture
```
scenarios/*.json -> requirements_schema -> plausibility (stop early if impossible)
                                              |
                       run_loop.py: Audit -> Dispatcher -> Planner -> Logistics -> Justice --(problems?)--> Dispatcher routes the rework
                                                              |
                                           approved -> output_writer -> outputs/<run>/
```
Idea: a small local model makes mistakes. Instead of trusting it, the system **checks every answer
with ordinary code and real data** and sends the problems back as feedback. This is the closed loop.

## 1. Inputs
**`scenarios/*.json`**: one file per trip (city, days, budget, must-visit places, outreach targets).
Why: the request used to be hard-coded constants. Now a new trip is a new file, not a code change.

**`requirements_schema.py`**: the `Requirements` class (Pydantic). Rejects bad input (0 days, no
must-visit places, duplicates) and derives helper values such as `city`, `region`, `cost_profile`
and `max_days_per_place`. `request_text()` turns it into the sentence the agents read.
- *Q: Why a schema, not free text?* So the same request can be repeated exactly and checked by code.

**`data/cost/<city>.json`**: assumed daily prices (hotel, meals, transport). Used to compute the
cheapest realistic trip. They are assumptions and must be cited as such.

**`plausibility.py`**: runs before any model call. Hard problem: budget below the cheapest realistic
trip, or more must-visit places than days can hold. Warning: tight budget, or a place not found on
OpenStreetMap. `python -m swarm.code_agents.plausibility <scenario>` shows this without running a model.
- *Q: Why stop early?* A model cannot fix an impossible request; it only wastes time or invents things.
- v0.9.8: the budget is per person (`group_size`; `budget_eur` is the group's ceiling), and a place must lie within `radius_km` of the city centre in `data/geo/<city>.json` (`geo.py`). A name not found in the region is looked up once without it, so Helsinki for a Shenzhen trip is caught.

**`meal_times.py`** and **`data/culture/<city>.json`**: breakfast, lunch and dinner windows (placeholder hours, to be confirmed with the supervisor). Code trims visits that run into a window before Finance runs; Justice reports what is left as a soft note; the Finance meals row sits in the lunch window.
**`hotels.py`** and **`data/hotels/<city>.json`**: one hotel for the whole trip, chosen by rule (the scenario's `hotel`, else the first listed), so it is the same every night and every run. The names are placeholders until you replace them. Finance writes it into the overnight rows.
**`transit_modes.py`** and **`data/transit/<city>.json`**: the modes that exist in a city. The Transit agent must choose one (code rejects others, such as BART in Shenzhen), and the Dispatcher writes the chosen mode into the plan.

## 2. The loop (`run_loop.py`; the model calls are in `swarm/llm_agents/`, `client.py` talks to Ollama)
**`call_structured_llm`**: sends a prompt to Ollama and demands JSON that matches a Pydantic schema.
If the JSON is invalid it retries with the error message. This is how small models become reliable.

**`agent_tech_planner`**: drafts a rough itinerary (which places on which day). Its prompt lists the
mandatory places, the exact number of days and the limit of days per place. If its new draft is
identical to the previous one it is challenged and resampled with a higher temperature.

**`agent_logistics_specialist`**: turns the rough itinerary into timed activities with transport
and costs in EUR. It receives exchange-rate and price-guide facts as context.

**`agent_justice`** (justice.py): does not just ask the model for an opinion. It calls the deterministic checks
(budget, schedule, `reality.py`, `plan_quality.py`) and builds the verdict from them. Its notes are
generated by code, because the model's own wording was unreliable. Each problem is routed:
missing places or repeated visits go to the Planner; schedule, cost and unknown venues go to Logistics.

**`run_swarm_loop`**: the controller. Up to 6 iterations of plan, detail, audit. Outcomes:
- `SUCCESS`: no problems.
- `SUCCESS_WITH_WARNINGS`: only **soft** problems remain (for example costs at the price floor, a
  venue not on OpenStreetMap) and they persisted 3 rounds or it is the last round. Warnings are
  written into `plan.md`. This rule is borrowed from Edict's "approve with warnings" idea.
- `ABORTED_MAX_ITER`, `SCHEMA_ABORT`, `REJECTED_IMPLAUSIBLE`, `CRASH_*`: failures, always logged.
- *Q: What is a soft problem?* One where the model's own repair attempts mostly make things worse
  (it invents venues), so the system documents it instead of looping forever. Hard problems
  (missing mandatory place, over budget, impossible schedule) are never approved.

**Fault injection** (`apply_fault_injection`, `--modes`): deliberately breaks a plan (missing hub,
budget spike, repeated failure) to prove the loop detects and repairs it. This is the Week 5 test.

## 3. The checks
**`reality.py`**: facts from the world. Does each place exist (OpenStreetMap), is the travel gap long
enough for the real driving time (OSRM), are costs above the realistic minimum, does a hotel row have
a hotel location. Unreachable internet gives "unknown", never a rejection.

**`plan_quality.py`**: shape of the plan. Repeated visits, days that are mostly hotel filler, more
than one check-in/out, lodging charged more than once. `SOFT_MARKERS` lists which messages are soft.

**`skills/`**: small functions the *code* calls (not the model): `budget_check`, `schedule_check`
(`builtin.py`), `live_exchange_rate`, `verify_place`, `travel_minutes` (`web.py`, cached in `data/`).
`skills/__init__.py` also logs every call, so each run reports a "Skills used" table.
- *Q: Why does the orchestrator call skills instead of the model?* Small models call tools unreliably;
  deterministic calls give repeatable facts and the model only receives them as context.

## 4. Control and traceability
**`state_machine.py`**: `TaskRecord` and the allowed status changes (pending, planning, scheduling,
reviewing, completed, failed). An illegal jump raises an error. Every transition goes to a JSONL log in `logs/`.

**`handoff.py`**: who may pass work to whom (user to Planner, Planner to Logistics, Logistics to
Justice, all through the Dispatcher; hub and spoke, see PERMISSIONS in handoff.py). Wrong routing is a code error, not a silent bug.

**`prompts/<name>/SOUL.md`**: each agent's instructions as a Markdown file, loaded by `soul_loader.py`.
Prompts can be edited and cited without touching Python.

**`version_info.py`, `version.txt`**: every result is stamped with version label, git commit
(`-dirty` if uncommitted) and a hash of the SOUL files and price tables. Results from different
hashes must not be pooled.

## 5. Outputs and batches
**`output_writer.py`**: writes `outputs/<run>/` with `index.md` (status, version, plausibility, skills
used), `plan.md`, `history.md` (every draft and verdict), `run.json`, `requirements.json`.

**`batch_summary.py`**: `python run_loop.py --scenario shenzhen_7day --modes none --repeats 5` runs the
same scenario several times and writes `outputs/summary_*.md` with status counts, mean iterations,
seconds and plan cost. `results.csv` gets one row per run.

**`practical.py`, `data/practical/<city>.json`**: visa, SIM, payment and transport notes added to `plan.md`.
They are fixed reference text with a "verify before travel" label, not model output, so every run has the same
notes and they cannot be invented. `optional_places` in a scenario lists real places the Planner may use for a
second visit per day.
- *Q: Why not let the model write the visa advice?* Entry rules change and a model can state them confidently
  and wrongly; fixed text with a source to check is honest and repeatable.

**`team` in the scenario**: sender name, university and travel dates. The Outreach agent must sign with the
sender's name and mention the university, so an email is ready to send once `sender_name` and `trip_dates` are filled in.
`max_days_per_place` is 1 whenever must-visit plus optional places are at least as many as the days.

## 6. Workers and the Dispatcher
**`llm_agents/transit.py`, `llm_agents/outreach.py`, `llm_agents/retry.py`, `core/worker_schemas.py`**: Transit (legs between stops) and Outreach (email requesting a lab
tour) agents with the same schema-plus-retry pattern. `tests/test_workers.py` measures first-attempt valid JSON.

**`dispatcher.py`** (also `drive_times`): looks up the OSRM driving time for each leg and hands it to the
Transit agent as a fact; `validate_transit` rejects minutes far outside it (the model used to invent them).
Outreach emails are rejected when they claim a project or result of the student group.

**`dispatcher.py`**: plain code. It keeps the task board, relays every handover, routes Justice's rejections (`route_rejection`) and, after approval, runs the workers. For each day it takes the plan's
locations in order and asks Transit for the legs between them; for each outreach target in the scenario
it asks Outreach for an email. Every handover is permission-checked (dispatcher to
transit/outreach and back to user). A failing worker is recorded as FAILED and never discards the plan.
Results appear as `transit_dayN.md` and `outreach_oNN.md` in the run folder; `--no-workers` skips them.
- *Q: Why is the Dispatcher not an LLM agent?* Its job is fixed (which worker, with which input), so
  ordinary code is cheaper, repeatable and testable. LLMs are used only where language is needed.
- *Q: Do the worker results change the plan?* Not yet; they add routes and emails next to it.

## 6b. The closing agents (v0.9b)
All are plain code, started by the Dispatcher after Justice approves, each through a permission-checked handover:
**`finance.py`** (`agent_finance` before Justice: hotel nights, meals and local transport by rule from `data/cost/<city>.json`;
`finance_report` after the workers: the cost table, Transit extra, EUR and CNY/USD), **`local_logistics.py`** (visa, SIM, payments,
transport from `data/practical/<city>.json`; a section without a named official source is reported as a problem),
**`route_order.py`** (best visiting order and 'not one cluster' legs from measured driving times; advice shown in the Transit
file), **`static_web.py`** (`handbook.md` and `handbook.json`). *Q: Why did Finance take over the hotel rows?* The model
wrote them wrongly in every run (extra check-ins, missing nights) and Justice kept sending the plan back; a rule cannot get them wrong.

## 7. Setup and tests
**`publish_site.py`** and **`site/`**: the portal. The script copies the latest approved run of each scenario (and the batch summaries) into VitePress pages; the GitHub Actions workflow builds and publishes them. Emails are left out unless `--with-emails`.
**`setup.ps1`**: installs Python venv, packages, Ollama and the model, then runs a script.
**`core/deps.py`**: installs pydantic automatically on first run.
**`tests/test_*.py`** (run as `python tests/test_core.py`): `test_core.py`, `test_loop_offline.py`, `test_workers_offline.py`, `test_agents_offline.py`, `test_dispatcher_offline.py`, `test_output_offline.py`, `test_site_offline.py`
run without a model (fake LLM answers). They cover state rules, scenario validation, every check, every
fault mode, the soft-approval rules and output files. Run them before every commit.

## Likely supervisor questions
- *What does the swarm do that one prompt does not?* It separates roles, checks each answer with real
  data and repairs it. Whether that gives better plans is what the benchmark will measure.
- *Where do the numbers come from?* `results.csv` and `outputs/summary_*.md`, stamped with version and hash.
- *What does a warning mean?* A soft problem the system could not fix and recorded openly.
- *What is borrowed?* Design ideas only (permission matrix, SOUL.md, approve-with-warnings) from Edict;
  no code. See THIRD_PARTY.md.
- *What are the limits?* The 8B model repeats drafts and sits at cost floors; OpenStreetMap misses some
  venues; prices are assumptions; Transit routes are not yet checked against the plan's own transport costs.

## Study diary template
Date | What I changed | Why | What I learned | Commit
