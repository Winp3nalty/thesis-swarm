---
title: About
---

# About this portal

This is the public handbook of a Bachelor's thesis project at Aalto University: *AI Natives in the Loop: Orchestrating
Autonomous Agent Swarms for Global Tech Expeditions*.

## How a plan is made

1. A scenario file describes the trip: city, days, budget, must-visit places.
2. A plausibility check refuses impossible requests before any model is called.
3. Local LLM agents (Tech Planner, Logistics, Transit, Outreach) draft the plan. Plain-code agents (Audit, Finance, Local Logistics, Static Web) add and check what a model should not decide: prices, meal times, the hotel, places in the right region.
4. Justice checks the plan with deterministic skills (budget, schedule, OpenStreetMap places and driving times) and sends it back to the right agent until it passes.
5. This site is built from the approved plans.

## Read the pages with care

- Prices for meals, local transport and lodging are assumptions from a city price table, not measured prices.
- Practical notes (visa, SIM, payments) are written from general knowledge and are **not verified**: check the official sources named on each page.
- Hotel names and meal times are placeholders until confirmed.
- Every page records the code version, model and rules hash that produced it, so each result can be traced to its source.

Source code, design notes and tests: [github.com/Winp3nalty/thesis-swarm](https://github.com/Winp3nalty/thesis-swarm).
