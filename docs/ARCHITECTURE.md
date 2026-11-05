# Architecture: the Expedition Swarm

Design inspiration: the "Three Departments and Six Ministries" structure of the Edict project
(MIT licence, https://github.com/cft0808/edict). We reuse the ideas, not the code. Cite it in the
thesis (Chapter 2.2 and 3.1) as the inspiration.

Status legend: **BUILT** = exists in this repo and has tests. **PLANNED** = decided, not yet written.

## 1. The idea in one paragraph

You (the "Emperor") describe an expedition. The swarm interviews you until the request is complete,
checks that the request is plausible, plans it, has the plan reviewed *before* any work is
dispatched, lets specialists do the work, verifies the result against real-world data, and hands
you the finished plan. Limits such as the budget come from you, not from constants in the code.

## 2. Flow

```
 You ──► Intake (Crown Prince) ──► requirements.json
                                        │
                                        ▼
                      Planning (Planner): rough itinerary + task list
                                        │
                                        ▼
                      Audit: plausible scenario?  ── no ──► back to Planner (or to you)
                                        │ yes
                                        ▼
                      Dispatcher: task board, assign tasks, collect results; Justice verifies the plan
                          │            │             │
                       Works        Finance        Rites       (Engineering, Personnel: later)
                    schedule +     costs + FX     outreach
                      transit      (skills)        emails
                          └────────────┴─────────────┘
                                        │
                                        ▼
                      Justice (verification skills): places exist? travel possible?
                                        │ fail ──► back to Works
                                        ▼ pass
                                   final plan ──► You
```

Tests and the benchmark skip Intake: they load a saved `scenarios/<name>.json` that has the same
format as `requirements.json`. The single-prompt baseline reads the same file.

## 3. Roles and where they live

| Edict role | Our role | LLM or code | Status | Where |
|---|---|---|---|---|
| Crown Prince (triage) | Intake agent: asks questions, fills the requirements | LLM for wording, **code** decides when complete | PLANNED | new `intake.py`, `prompts/intake/SOUL.md` |
| Planning Dept | Planner: rough itinerary and task list | LLM | BUILT (itinerary only) | `agent_tech_planner`, `prompts/tech_planner/SOUL.md` |
| Audit (门下省) | Gatekeeper: plausibility of the scenario only, before any model call | **code** | BUILT v0.9a (`audit.py`, `plausibility.py`) | `agent_audit` |
| Justice (刑部) | Verifies the finished plan against real data, names who to blame; approves with warnings after 3 soft-only rounds | code checks, LLM explains | BUILT v0.9a (`justice.py`, `plan_quality.py`, `reality.py`) | `agent_justice`, `prompts/justice/SOUL.md` |
| Dispatcher (尚书省) | Task board; hands work to every agent and takes results back; routes Justice's rejections; runs the workers | **code** | BUILT v0.9a (`dispatcher.py`, `handoff.py`) | `Dispatcher`, `TaskBoard` |
| Logistics (工部) | visits, times and visit prices only | LLM | BUILT | `agent_logistics_specialist`, `prompts/logistics/SOUL.md` |
| Finance (户部) | lodging, meals, local transport by rule; cost report in EUR and local currency, Transit estimates compared | **code** | BUILT v0.9b (`finance.py`) | `agent_finance`, `finance_report` |
| Transit (兵部) | legs between the places of a day, checked against OSRM; order and cluster check; owns the transport mode (v0.9.7) | LLM + **code** | BUILT (order check v0.9b, `route_order.py`; modes `transit_modes.py`) | `llm_agents/transit.py: agent_transit` |
| Rites (礼部) | outreach emails; sentence-level check against invented claims | LLM + **code** | BUILT, run by the Dispatcher | `workers.agent_outreach` |
| Local Logistics (吏部) | visa, SIM, payments, transport, each section with named official sources | **code** | BUILT v0.9b (`local_logistics.py`, `data/practical/`) | `agent_local_logistics` |
| Static Web (工部) | handbook.md and handbook.json from everything the agents produced | **code** | BUILT v0.9b (`static_web.py`) | `agent_static_web` |
| Engineering | choose which labs and hubs are worth visiting | LLM | merged into the Planner for now | |

Why not make every ministry an LLM agent? Each model call costs time on a local GPU, and small
models are unreliable at arithmetic and lookups. Finance and Justice are better as deterministic
code. This is also the answer to "why not more agents?" in the defence.

## 4. The contract between stages: requirements

`requirements_schema.py` (**BUILT**). The loop, Planner and Justice read these values; the old constants
`MAX_BUDGET_EUR`, `REQUIRED_HUBS` and `EXPECTED_DAYS` are gone. A saved scenario lives in
`scenarios/<name>.json` and every run copies it to `outputs/<run>/requirements.json`:

- `destination` (city, country)
- `days`
- `budget_eur` (ceiling)
- `comfort` (budget / standard)
- `must_visit` (list of hubs, each one a real visit)
- `interests` (free text, for the Planner)
- `outreach` (list of professors or institutions to contact, may be empty)
- `notes`

Intake is finished when the schema validates and every required field is filled. A model never
decides that on its own.

## 5. Where decisions are made

- **Plausibility (Review, before dispatch):** budget against a reference price table
  (`data/cost/<city>.json`), number of days against number of must-visit places, whether the
  named places exist online. Deterministic. If it fails, Audit returns a reason, and you can
  change the answers or confirm an override.
- **Final verification (Justice, after Works):** real places, possible travel gaps, budget
  ceiling, schedule overlaps, a real visit on every day.
- **Routing of failures:** every rejection names who must fix it (see `AuditResult.failed_agent`).
  A cost shortfall goes to Works, not to the Planner.

The fixed minimums of v0.6 (60 EUR per day, 300 EUR for the trip) are gone. Since v0.7 the floors
are derived from the city's price file: a day costs at least meals + local transport, a trip at least
`days x (meals + transport) + nights x hotel`. The price files hold assumptions, to be cited in the thesis.
A request below the trip floor is stopped before any model call (`REJECTED_IMPLAUSIBLE`).
Soft problems (cost floor, repeated visit, hotel filler, extra check-in, lodging charged too often, a last day
without a visit, a place OpenStreetMap does not know)
are waived after 3 rounds, or on the last round, and listed as warnings. Missing hubs, the budget ceiling and
schedule overlaps are never waived.

## 6. What is already in place (do not rebuild)

Borrowed ideas and their status are listed in `THIRD_PARTY.md`.

- Closed-loop state machine with permitted transitions and a JSONL audit log: `state_machine.py`,
  `handoff.py`.
- One prompt file per agent: `prompts/*/SOUL.md`, loaded by `soul_loader.py`.
- Dispatcher (`dispatcher.py`, replaces `coordinator.py`): after approval, Transit routes (one per day) and Outreach emails (one per scenario target) run through permission-checked handovers; failures are recorded, never fatal. `--no-workers` skips it.
- Plan quality (v0.8.1): lodging feedback names the nights that lack a charge; `verify_place` rejects an unrelated small hit (a bank for a technology park); scenarios may list `optional_places` for a second daily visit; `practical.py` adds visa/SIM/payment/transport notes from `data/practical/<city>.json` to plan.md.
- No repeats (v0.8.2): a place may fill one day whenever must-visit plus optional places cover the days; a place the Planner chose but the map does not know is blamed on the Planner; the scenario `team` block (sender name, university, dates) goes into the Outreach emails.
- Facts for workers (v0.8.3): the Planner is told how many distinct places exist for its visit slots and which are unused; the Transit agent receives OSRM driving times and its minutes are rejected when far outside them; Outreach emails may not claim projects or results for the group; overnight rows belong to the evening.
- Costs by rule (v0.9b): Logistics plans visits only; Finance removes any hotel or meal rows the model wrote, adds one overnight row per night and one daily row for meals and local transport, so Justice no longer sends plans back for hotel or cost-floor problems. After the workers, Finance writes the cost report (Transit estimates above the transport allowance are added) and Static Web builds `handbook.md/json`.
- v0.9c: an Outreach email may not state when the visit happens unless the scenario gives `trip_dates`; a scenario may give `place_coordinates` ({"HAX": [lat, lon]}) for places OpenStreetMap does not know, which Audit, Justice and the Transit order check then use; the handbook header is no longer duplicated.
- v0.9d: when an Outreach email is rejected for its content, the retry prompt shows the model its previous body and quotes the offending sentences to rewrite; an email gets three retries instead of two.
- Batch runs: `--repeats N` writes outputs/summary_*.md; each run lists the skills it used in index.md.
- Skills: budget, schedule, live exchange rate, place verification, travel time.
- Readable outputs per run, and a version label in every result (`version.txt`).
- Review rule from Edict: soft (cost-level) problems that persist for 3 rounds end as
  `SUCCESS_WITH_WARNINGS`, with the warnings written into `plan.md`.

## 7. Build order (smallest useful steps first)

1. ~~Requirements schema, `scenarios/shenzhen_3day.json`, and the loop reading them instead of
   constants.~~ **DONE.** (Unblocks the benchmark.)
2. ~~Plausibility check in Audit, replacing the fixed cost minimums.~~ **DONE** (v0.7), together with
   the quality checks and the two 7-day scenarios.
3. Intake agent (fixed question list, model rephrases and extracts answers).
4. Connect Outreach and Transit to the loop through the Dispatcher. **DONE** (v0.8), hub-and-spoke since v0.9a.
5. Engineering and Personnel only if time allows. Otherwise they are future work.

## 8. Open questions for the supervisor and for you

- Which scenario fields does the supervisor want fixed for the benchmark?
- Should an implausible request be blocked, or only warned about?
- How many repeated runs per scenario will the benchmark use?

## v0.8.4: rules the model kept breaking move into code

- Overnight rows: `plan_quality.fix_overnight_times` sets the "Overnight stay" row to 20:00-23:59 after the Logistics agent answers (only when every other row ends by 20:00). The change is logged as a pipeline note. The soft check stays as a safety net.
- Missing nights: `quality_check` reports days (except the last) without an overnight stay (soft note).
- Outreach: invented-claim phrases are matched as whole words ("your research" is not "our research"); the retry after a too-short body shows the previous text.

## v0.8.5: no invented numbers for the hotel
- `dispatcher.day_stops` leaves out hotels and other generic stops. Their position is unknown, so Transit now routes only between real places, and a day with fewer than two real places gets no Transit file.
- More student-claim phrases are blocked in outreach emails ("currently exploring", "our studies", "we are working on", ...).

## v0.8.6: length rule matches what the model can do
- Outreach bodies may have 100-180 words (was 120-180). qwen3:8b wrote 113-119 words in five of six attempts and repeated the same text on retry; the length is not what makes an email good. The retry text now names two concrete sentences to add.
- Transit prompt states the exact number of legs; the model had added a leg to the hotel in 4 of 5 days.

- v0.9e: if an Outreach email still breaks the claim or timing rules from the second attempt on, code deletes the offending sentences and re-validates; a repaired email is marked "repaired by code" in the output. The prompt also limits "our" to "our group" and "our visit".

- v0.9.6 (structure only, no change in behaviour): the code moved into the `swarm` package (`core`, `llm_agents`,
  `code_agents`, `output`, `skills`), prompts into `prompts/`, tests into `tests/`, documents into `docs/`.
  `workers.py` became `llm_agents/retry.py`, `transit.py` and `outreach.py`; the model call and the Planner/Logistics
  agents left `run_loop.py` for `llm_agents/client.py`, `tech_planner.py` and `logistics.py`. The rules hash is unchanged.
- Version names are numbers only from here on: v0.9.6, v0.9.7, ... v1.0 (earlier labels v0.9a to v0.9e correspond to v0.9.1 to v0.9.5).
- v0.9.7: Transit owns the transport mode. `data/transit/<profile>.json` lists the modes that exist in a city (Shenzhen: Metro, Bus, Taxi, ...; Silicon Valley: Caltrain, BART, VTA, ...) plus aliases. The Transit prompt lists them, `validate_transit` rejects any other mode (BART in Shenzhen) and feeds the reason back, and after Transit succeeds the Dispatcher writes the mode of each leg into the `transit_mode` of the visit it reaches (the first visit of a day is "from hotel"). If a day's route failed, Logistics' first guess stays. The mode files are part of the rules hash.
