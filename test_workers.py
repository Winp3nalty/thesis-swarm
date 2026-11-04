"""
Week 3 live test: run the Transit and Outreach agents over their 20 hand-written cases
each and report how often the model's JSON is valid.

Pass metric (supervisor's guide): 100% valid JSON matching the schema across 20 runs.
We report three numbers per agent:
  first-attempt schema-valid   <- the guide's metric
  first-attempt fully valid    (schema + content rules such as correct legs / word count)
  valid after retries          (what the pipeline actually gets)

Run (from the project folder, inside the venv):
    python test_workers.py                  # both agents
    python test_workers.py --agent transit  # one agent
    python test_workers.py --limit 3        # quick smoke test with 3 cases each
"""
import argparse
import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path

import output_writer
import run_loop
import workers

CASES_FILE = Path(__file__).resolve().parent / "tests" / "w3_cases.json"
OUT_FILE = "worker_results.csv"
HEADER = ["timestamp", "agent", "case_id", "model", "gpu_name", "attempts",
          "first_schema_ok", "first_semantic_ok", "final_ok",
          "first_problems", "prompt_tokens", "completion_tokens", "seconds", "error"]


def run_case(agent: str, case: dict, out_dir=None) -> dict:
    run_loop.CALL_STATS.clear()
    started = time.time()
    error = ""
    result = None
    try:
        if agent == "transit":
            result, info = workers.agent_transit(case["city"], case["stops"])
        else:
            result, info = workers.agent_outreach(case)
        final_ok = True
    except workers.WorkerFailed as e:
        info, final_ok, error = e.info, False, str(e.last_error)[:200]
    except Exception as e:  # network down, Ollama crashed, ...
        info = {"attempts": 0, "first_schema_ok": False, "first_semantic_ok": False,
                "first_problems": [], "attempt_log": []}
        final_ok, error = False, f"{type(e).__name__}: {e}"[:200]
    stats = list(run_loop.CALL_STATS)
    link = ""
    if out_dir is not None:
        data = result.model_dump() if result is not None else None
        if data is None:  # failed: show the last attempt that at least parsed
            data = next((a["response"] for a in reversed(info["attempt_log"]) if a.get("response")), None)
        render = output_writer.render_transit_md if agent == "transit" else output_writer.render_outreach_md
        link = f"{agent}/{case['id']}.md"
        output_writer.write_text(out_dir / link, render(case, info, data, final_ok))
        output_writer.write_json(out_dir / agent / f"{case['id']}.json",
                                 {"case": case, "final_ok": final_ok, "error": error, "result": data,
                                  "attempts": info["attempt_log"]})
    return {
        "link": link,
        "first_problems": " | ".join(info["first_problems"])[:300],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "agent": agent, "case_id": case["id"], "model": run_loop.MODEL_NAME,
        "gpu_name": run_loop._gpu_name(), "attempts": info["attempts"],
        "first_schema_ok": info["first_schema_ok"], "first_semantic_ok": info["first_semantic_ok"],
        "final_ok": final_ok,
        "prompt_tokens": sum(s["prompt_tokens"] for s in stats),
        "completion_tokens": sum(s["completion_tokens"] for s in stats),
        "seconds": round(time.time() - started, 1), "error": error.replace("\n", " "),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=["transit", "outreach", "all"], default="all")
    parser.add_argument("--limit", type=int, default=None, help="only the first N cases per agent")
    args = parser.parse_args()

    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    agents = ["transit", "outreach"] if args.agent == "all" else [args.agent]
    new_file = not os.path.exists(OUT_FILE)
    all_pass = True
    out_dir = output_writer.new_run_dir("workers", run_loop.MODEL_NAME, label=args.agent)
    all_rows, summaries = [], {}

    with open(OUT_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        for agent in agents:
            rows = []
            for case in cases[agent][: args.limit]:
                row = run_case(agent, case, out_dir)
                writer.writerow(row)
                f.flush()
                rows.append(row)
                print(f"  {agent} {row['case_id']}: attempts={row['attempts']} "
                      f"first_schema_ok={row['first_schema_ok']} final_ok={row['final_ok']} "
                      f"({row['seconds']}s)")
            n = len(rows)
            schema = sum(1 for r in rows if r["first_schema_ok"])
            full = sum(1 for r in rows if r["first_semantic_ok"])
            final = sum(1 for r in rows if r["final_ok"])
            ok = schema == n
            all_rows += rows
            summaries[agent] = {"n": n, "schema": schema, "full": full, "final": final}
            all_pass &= ok
            print(f"\n[{agent.upper()}] model={run_loop.MODEL_NAME} cases={n}\n"
                  f"  first-attempt schema-valid : {schema}/{n} ({100 * schema / n:.0f}%)  "
                  f"{'PASS' if ok else 'FAIL'} (target 100%)\n"
                  f"  first-attempt fully valid  : {full}/{n} ({100 * full / n:.0f}%)\n"
                  f"  valid after retries        : {final}/{n} ({100 * final / n:.0f}%)\n")
    output_writer.write_workers_index(out_dir, run_loop.MODEL_NAME, run_loop._gpu_name(), all_rows, summaries)
    print(f"Per-case rows saved to {OUT_FILE}")
    print(f"Readable results (open index.md, Ctrl+Shift+V in VS Code): {out_dir / 'index.md'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
