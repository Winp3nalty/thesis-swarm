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
                      Review (Auditor): plausible?  ── no ──► back to Planner (or to you)
                                        │ yes
                                        ▼
                      Dispatch (Coordinator): assign tasks, collect results
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
| Crown Prince (triage) | Intake agent: asks questions, fills the requirements | LLM for wording, **code** decides when complete | PLANNED | new `intake.py`, `agents/intake/SOUL.md` |
| Planning Dept | Planner: rough itinerary and task list | LLM | BUILT (itinerary only) | `agent_tech_planner`, `agents/tech_planner/SOUL.md` |
| Review Dept | Auditor: plausibility gate before planning, then approve or reject the plan and route the blame; approves with warnings after 3 soft-only rounds | code checks, LLM explains | BUILT (`plausibility.py`, `plan_quality.py`, `reality.py`) | `agent_auditor`, `agents/auditor/SOUL.md` |
| Dispatch Dept | Coordinator: assigns tasks, collects results | mostly **code** | PLANNED (today `run_swarm_loop` does this implicitly) | `run_loop.py`, `handoff.py` |
| Works (工部) | schedule, transit | LLM | BUILT | `agent_logistics_specialist`, `workers.agent_transit` |
| Finance (户部) | budget, costs, exchange rates | **skills** | BUILT | `skills/builtin.py`, `skills/web.py` |
| Rites (礼部) | outreach emails | LLM | BUILT, not yet connected to the loop | `workers.agent_outreach` |
| Justice (刑部) | verification against real data | **skills** | BUILT | `reality.py`, `skills/web.py` (OpenStreetMap) |
| Engineering (兵部) | choose which labs and hubs are worth visiting | LLM | merged into the Planner for now | |
| Personnel (吏部) | who to contact (contact table) | data | PLANNED, optional | |

Why not make every ministry an LLM agent? Each model call costs time on a local GPU, and small
models are unreliable at arithmetic and lookups. Finance and Justice are better as deterministic
code. This is also the answer to "why not more agents?" in the defence.

## 4. The contract between stages: requirements

`requirements_schema.py` (**BUILT**). The loop, Planner and Auditor read these values; the old constants
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
  named places exist online. Deterministic. If it fails, the Auditor returns a reason, and you can
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
- One prompt file per agent: `agents/*/SOUL.md`, loaded by `soul_loader.py`.
- Coordinator (`coordinator.py`): after approval, Transit routes (one per day) and Outreach emails (one per scenario target) run through permission-checked handovers; failures are recorded, never fatal. `--no-workers` skips it.
- Plan quality (v0.8.1): lodging feedback names the nights that lack a charge; `verify_place` rejects an unrelated small hit (a bank for a technology park); scenarios may list `optional_places` for a second daily visit; `practical.py` adds visa/SIM/payment/transport notes from `data/practical/<city>.json` to plan.md.
- Batch runs: `--repeats N` writes outputs/summary_*.md; each run lists the skills it used in index.md.
- Skills: budget, schedule, live exchange rate, place verification, travel time.
- Readable outputs per run, and a version label in every result (`version.txt`).
- Review rule from Edict: soft (cost-level) problems that persist for 3 rounds end as
  `SUCCESS_WITH_WARNINGS`, with the warnings written into `plan.md`.

## 7. Build order (smallest useful steps first)

1. ~~Requirements schema, `scenarios/shenzhen_3day.json`, and the loop reading them instead of
   constants.~~ **DONE.** (Unblocks the benchmark.)
2. ~~Plausibility check in the Auditor, replacing the fixed cost minimums.~~ **DONE** (v0.7), together with
   the quality checks and the two 7-day scenarios.
3. Intake agent (fixed question list, model rephrases and extracts answers).
4. Connect Outreach and Transit to the loop through the Coordinator.
5. Engineering and Personnel only if time allows. Otherwise they are future work.

## 8. Open questions for the supervisor and for you

- Which scenario fields does the supervisor want fixed for the benchmark?
- Should an implausible request be blocked, or only warned about?
- How many repeated runs per scenario will the benchmark use?
