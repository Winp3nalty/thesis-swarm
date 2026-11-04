"""Offline tests for readable output saving. Run: python test_output_offline.py"""
import json
import tempfile
from pathlib import Path

import output_writer
import run_loop
import test_workers_offline as tw
import test_workers


def test_swarm_outputs():
    import os
    import test_loop_offline as tl
    os.chdir(tempfile.mkdtemp())
    os.environ["SWARM_OFFLINE"] = "1"
    run_loop.call_structured_llm = tl.fake_llm
    run_loop.SAVE_OUTPUTS = True
    assert run_loop.run_swarm_loop("3-day Shenzhen trip", inject_mode="missing_hub") is not None
    runs = list(Path("outputs").iterdir())
    assert len(runs) == 1, runs
    names = {p.name for p in runs[0].iterdir()}
    assert {"index.md", "plan.md", "plan.json", "history.md", "history.json"} <= names, names
    assert "| Time |" in (runs[0] / "plan.md").read_text(encoding="utf-8")
    hist = (runs[0] / "history.md").read_text(encoding="utf-8")
    assert "REJECTED" in hist and "PASSED" in hist, hist[:500]


def test_worker_outputs():
    tmp = Path(tempfile.mkdtemp())
    run_loop.call_structured_llm = tw.good_llm
    rows, summ = [], {}
    for agent in ("transit", "outreach"):
        rows += [test_workers.run_case(agent, c, tmp) for c in tw.CASES[agent][:2]]
        summ[agent] = {"n": 2, "schema": 2, "full": 2, "final": 2}
    output_writer.write_workers_index(tmp, "m", "g", rows, summ)
    idx = (tmp / "index.md").read_text(encoding="utf-8")
    assert "outreach/" in idx and "transit/" in idx
    data = json.loads((tmp / "outreach" / f"{tw.CASES['outreach'][0]['id']}.json").read_text(encoding="utf-8"))
    assert data["final_ok"] and data["result"]["body"]
    assert "Final email" in (tmp / "outreach" / f"{tw.CASES['outreach'][0]['id']}.md").read_text(encoding="utf-8")


if __name__ == "__main__":
    for t in (test_worker_outputs, test_swarm_outputs):
        t(); print("PASS", t.__name__)
