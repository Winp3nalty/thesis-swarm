# Student Action Guide: Orchestrating Autonomous Agent Swarms

This guide is your primary execution manual for the Bachelor’s thesis. You will design, build, and benchmark an autonomous multi-agent swarm that plans global technical expeditions (e.g., to Shenzhen, Shanghai, and Silicon Valley) using local open-weight LLMs on Aalto VDI, culminating in an interactive, open-access GitHub Pages portal.

---

## 1. System Architecture: The State-Machine Analogy

Treat this multi-agent architecture as a **closed-loop feedback control system**. Inspired by the historical "Three Departments and Six Ministries" (三省六部, Edict) paradigm, the agents operate not as isolated chatbots, but as interconnected state-machine nodes:

```
                  ┌─────────────────────────────────────────┐
                  │       User Expedition Request           │
                  │ (Target, Duration, Budget, Lab Focus)   │2
                  └────────────────────┬────────────────────┘
                                       │
                                       ▼
        ┌─────────────────────────────────────────────────────────────┐
        │        Planning Agent (中书省 / Drafting Dept.)              │
        │          - Decomposes goals into sub-tasks                  │
        │          - Generates baseline master schedule               │
        └──────────────────────────────┬──────────────────────────────┘
                                       │
                                       ▼
        ┌─────────────────────────────────────────────────────────────┐
        │        Audit Agent (门下省 / Gatekeeper & QA)                │
        │          - Checks budget constraints & feasibility          │
        │          - Validates email etiquette & transit sanity       │
        └──────────────┬──────────────────────────────▲───────────────┘
                       │                              │
         [Audit Passed]│                [Audit Failed / Recalculate]
                       ▼                              │
        ┌──────────────────────────────┐              │
        │ Dispatcher (尚书省 / Routing) │              │
        └──────────────┬───────────────┘              │
                       │                              │
     ┌─────────────────┼─────────────────┬────────────┴────┬─────────────────┐
     ▼                 ▼                 ▼                 ▼                 ▼
┌─────────┐       ┌─────────┐       ┌─────────┐       ┌─────────┐       ┌─────────┐
│ Finance │       │ Transit │       │ Outreach│       │ Local   │       │ Static  │
│  Agent  │       │  Agent  │       │  Agent  │       │ Logistics│      │   Web   │
│ (户部)  │       │ (兵部)  │       │ (礼部)  │       │ (吏/刑部)│      │  (工部) │
│ Cost &  │       │ Topo &  │       │ Academic│       │ SIM/Visa│       │ Markdown│
│ Currency│       │ Routes  │       │ Cold    │       │ & Comm. │       │ & JSON  │
│ (EUR/CNY│       │ (TSP)   │       │ Emails  │       │ Rules   │       │ Builder │
└─────────┘       └─────────┘       └─────────┘       └─────────┘       └────┬────┘
                                                                             │
                                                                             ▼
                                                                ┌─────────────────────────┐
                                                                │  GitHub Pages Portal    │
                                                                │ (Public Living Handbook)│
                                                                └─────────────────────────┘

```

---

## 2. 12-Week Master Roadmap

| Phase | Week | Primary Milestone | Concrete Deliverable | Pass / Fail Metric |
| --- | --- | --- | --- | --- |
| **Phase 1: Foundation & Inference** | **W1** | Set up local LLM engine on Aalto VDI | Working OpenAI-compatible local API endpoint (vLLM / Ollama) | Local model replies to `curl` requests within 3 seconds |
|  | **W2** | Unpack Edict / OpenClaw framework | Minimal two-agent communication demo (Planner $\rightarrow$ Auditor) | Structured task handover log printed cleanly in CLI |
|  | **W3** | Specialize single agents (Transit & Outreach) | Prompts + Pydantic output schemas for 2 worker agents | 100% valid JSON matching target schema across 20 test runs |
|  | **W4** | Architecture sign-off & Related Work draft | Thesis Chapter 1 & 2 draft + system diagram | Supervisor review approval |
| **Phase 2: Closed-Loop Swarm** | **W5** | Implement closed-loop rejection logic | Gatekeeper agent intercepts invalid plans and triggers replanning | 5 out of 5 intentionally flawed itineraries caught & corrected |
|  | **W6** | Build Expedition Scenario 1: Shenzhen | Complete 7-day hardware/robotics expedition plan (DJI, InnoX, HAX) | Transit routes, visa/SIM guidance, and lab emails generated |
|  | **W7** | Build Expedition Scenario 2: Silicon Valley | Complete 7-day academic expedition plan (Stanford, UC Berkeley) | Valid US transit (BART/Caltrain), budget breakdown, cold emails |
|  | **W8** | Data pipeline to structured JSON/Markdown | Automation script saving raw agent outputs to static web source | Single command converts swarm run into a static site folder |
| **Phase 3: Web Portal & Thesis** | **W9** | Deploy GitHub Pages portal | Public interactive documentation live via VitePress | Site accessible on the web with responsive navigation |
|  | **W10** | Benchmark & quantitative evaluation | Comparison: Swarm vs. Single-prompt baseline | Metric tables: constraint violation rate, cost, hallucination rate |
|  | **W11** | Thesis draft completion | Complete draft (Chapters 1–5) in Aalto LaTeX template | Plagiarism check clean, chapters submitted to advisor |
|  | **W12** | Presentation slides & demo reel | Final PDF submission + 3-minute video recording of live run | Successful 15-minute BSc thesis oral defense |

---

## 3. The "AI-First" Troubleshooting Dictionary

When you hit a technical roadblock, paste these precise keywords and templates into ChatGPT or Gemini to get targeted, functional code.

### Category A: GPU Setup, Quantization, and vLLM on Aalto VDI

* **Keywords**: `Aalto Linux VDI`, `NVIDIA RTX 3090 24GB`, `vLLM serving`, `AWQ 4-bit quantization`, `OpenAI-compatible server`, `Out-Of-Memory (OOM) fix`
* **Prompt Template**:
> *"I am running on an Ubuntu terminal with an NVIDIA RTX 3090 (24GB VRAM). I need to serve a model like Qwen2.5-7B-Instruct or 14B using vLLM as an OpenAI-compatible endpoint. Write the exact terminal installation commands and the `vllm serve` command line flags to optimize for high throughput, low memory footprint, and an 8k context window without CUDA OOM."*



### Category B: Structured Output & Inter-Agent State Machine

* **Keywords**: `Pydantic V2 BaseModel`, `OpenAI structured outputs`, `JSON Schema validation`, `Finite State Machine in Python`, `Multi-agent routing loop`
* **Prompt Template**:
> *"I am building a multi-agent state machine in Python without heavyweight frameworks. Agent A (Planner) produces an itinerary; Agent B (Auditor) verifies it against a strict budget of 1500 EUR and transit feasibility. Provide a minimal, robust Python script using Pydantic to enforce valid JSON outputs for both agents, and a while-loop that triggers replanning if Agent B returns `status: REJECT` with critique notes."*



### Category C: Domain Logic (Expeditions & Academic Outreach)

* **Keywords**: `Academic cold email etiquette`, `Shenzhen hardware ecosystem itinerary`, `Bay Area transit optimization (BART/Caltrain)`, `Cultural constraints in business correspondence`
* **Prompt Template**:
> *"Act as a system prompt engineer. Write a system prompt for an agent named 'Outreach Officer'. Given a target professor's name, institution, and recent research topics, this agent must generate a concise, 150-word academic email requesting a 30-minute lab tour during an upcoming student expedition. The tone must be humble, scientifically precise, culturally appropriate, and never generic."*



### Category D: Web Deployment & Automated Portals

* **Keywords**: `VitePress documentation`, `GitHub Pages automated deployment`, `GitHub Actions workflow`, `Markdown dynamic rendering`
* **Prompt Template**:
> *"I have a Python project that generates a folder of Markdown files and a `meta.json` file. Provide a step-by-step setup to turn this folder into a clean, modern documentation website using VitePress, along with the complete `.github/workflows/deploy.yml` file to publish it automatically to GitHub Pages."*



---

## 4. Engineering Contingency Matrix

| Failure Mode | Direct Workaround |
| --- | --- |
| **Edict or OpenClaw source code is too complex or fails to install** | Do **not** spend more than 2 days debugging third-party UI code. Fall back to a lightweight Python script using basic OpenAI client calls, modeling the departments as explicit Python classes or a clean **LangGraph** / **CrewAI** state graph. |
| **Small local LLMs fail at complex Tool/Function Calling** | Stop using dynamic function calling. Switch to **Strict Prompt-Based JSON Output**. Instruct the model to return raw JSON only, and use Python's `pydantic.ValidationError` handling to catch and retry invalid payloads. |
| **Live travel or maps APIs are blocked or rate-limited** | Do not write brittle scrapers. Create a local static data store: a directory of mock JSON files (`data/shenzhen_hubs.json`, `data/flight_benchmarks.json`) for the agents to read. Your thesis evaluates **agent coordination**, not web scraping resilience. |
| **Aalto VDI session drops during long experiment runs** | Run all serving and agent scripts inside a persistent terminal multiplexer: `tmux` or `screen`. Pipe all execution outputs to timed `.log` files. |

---

## 5. Thesis Structural Blueprint (Aalto Format)

```
1. Introduction
   ├── 1.1 Motivation: From Solitary Engineering to Agent Swarms
   ├── 1.2 The Expedition Challenge: Logistics, Culture, & Academic Outreach
   └── 1.3 Contributions & Thesis Outline

2. Background & Related Work
   ├── 2.1 Large Language Models & Local Edge Deployment (vLLM / Quantization)
   ├── 2.2 Multi-Agent Orchestration & Organizational Models (Edict / OpenClaw)
   └── 2.3 Closed-Loop Control Principles Applied to Generative Systems

3. Swarm Design & Methodology
   ├── 3.1 Role Decomposition: The Three Departments & Six Ministries Paradigm
   ├── 3.2 Protocol Design: Inter-Agent Schemas & State Transitions
   └── 3.3 The Gatekeeping Engine: Automated Critique & Rejection Loops

4. Implementation & Case Studies
   ├── 4.1 Compute Infrastructure: Local Serving on RTX 3090
   ├── 4.2 Case Study I: The Greater Bay Area Hardware & Robotics Tour
   ├── 4.3 Case Study II: The Silicon Valley Academic & Embodied AI Hub
   └── 4.4 Automated Publishing: The Living Handbook Portal on GitHub Pages

5. Evaluation & Benchmarks
   ├── 5.1 Multi-Agent Swarm vs. Single-Prompt Baseline (Constraint Violations)
   ├── 5.2 Latency, Token Economy, and Hardware Utilization
   └── 5.3 Qualitative Assessment of Outreach & Itinerary Feasibility

6. Conclusion & Future Outlook
   ├── 6.1 Summary of Findings
   └── 6.2 Scaling AI Native Workflows for Global Research Exploration

```

---

## 6. Core Advice for Day One

1. **Think like a control systems engineer**: LLMs are noisy actuators. Your job is to design the feedback loops, filters, and safety boundaries that guarantee reliable, structured output.
2. **Build the minimal toy system before scaling**: Your week 1 goal is simply to have a Python script run on the VDI, prompt your local model, and write a 3-sentence itinerary to a `.txt` file. Add roles, checks, and complexity incrementally.
3. **Use the dictionary**: Whenever an error occurs, identify the relevant keywords above and use your AI assistant to generate precise fixes, keeping your development momentum high.



5-6pm demonstration.