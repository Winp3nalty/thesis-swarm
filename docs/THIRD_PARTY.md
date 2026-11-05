# Third-party material

## Edict (https://github.com/cft0808/edict), MIT licence

The swarm's structure is inspired by Edict's "Three Departments and Six Ministries" design. We did
not copy its source code. We re-implemented ideas in our own code and rewrote prompt wording for
expedition planning. The licence text is reproduced below as the licence asks.

| Idea from Edict | How we use it | Status |
|---|---|---|
| Every task has a status and only whitelisted transitions are allowed, with an audit log | `state_machine.py`, `logs/audit_*.jsonl` | used |
| A table of which agent may hand work to which | `handoff.py` | used |
| One `SOUL.md` personality file per agent | `agents/*/SOUL.md`, `soul_loader.py` | used |
| Review department: four-point review (feasibility, completeness, risk, resources) with concrete, specific objections | `agents/auditor/SOUL.md` | used |
| Review rule: after three rounds the reviewer approves and attaches its suggestions | `SOFT_REJECTION_LIMIT` in `run_loop.py`, only for soft cost problems; hard problems are never waived | used |
| CI guard that keeps the state tables consistent | `test_state_table_consistent` in `test_core.py` | used |
| Triage by the Crown Prince: chat versus command, restate the request in your own words, a short summary of goal, requirements and expected output | planned for the Intake agent | planned |
| Dispatch department: a table of departments and their duties, a task order with required output, collect and summarise | planned for the Coordinator | planned |

Not used: the dashboard, Redis, SQLAlchemy, workers, the kanban command line, the LinUCB router, the
model advisor, the news and sync scripts, and the installer scripts.

### Licence text (Edict)

```
MIT License

Copyright (c) 2026 openclaw-sansheng-liubu contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
