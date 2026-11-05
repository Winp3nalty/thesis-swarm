"""
Turn finished swarm runs into the pages of the public portal (a VitePress site in site/).

    python publish_site.py                  latest run per scenario, from outputs/ into site/
    python publish_site.py --keep 3         the three latest runs per scenario
    python publish_site.py --with-emails    also publish the outreach emails (off by default: the site is public)

Plain Python, standard library only. It writes site/index.md, site/runs/*.md, site/benchmarks/*.md and
site/.vitepress/generated.json (the sidebar). Commit site/ and push: the GitHub Actions workflow builds and
publishes it. Preview locally with:  cd site; npm install; npm run dev
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

PUBLISHED = ("SUCCESS", "SUCCESS_WITH_WARNINGS")
EMAILS_NOTE = "_The outreach emails are not published on the public site (they name real people). Run publish_site.py with --with-emails to include them._"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def fm(value: str) -> str:
    """A front-matter value as a quoted YAML string (a bare 'Batch summary: x' is a YAML error)."""
    return json.dumps(value, ensure_ascii=False)


def sanitize(md: str) -> str:
    """Make generated Markdown safe for VitePress, which reads pages as Vue templates: no '{{', and no '<' that
    could start a tag, except the <details>/<summary> pair that the run files use."""
    md = md.replace("{{", "{ {").replace("}}", "} }")
    return re.sub(r"<(?!/?(?:details|summary)\b)", "&lt;", md)


def without_emails(md: str) -> str:
    """Replace the '## Outreach emails' section of a handbook with a note."""
    return re.sub(r"(?ms)^## Outreach emails\n.*?(?=^## |\Z)", "## Outreach emails\n\n" + EMAILS_NOTE + "\n\n", md)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def find_runs(outputs: Path) -> list[dict]:
    """Published runs (handbook.md present, status SUCCESS*), newest first."""
    runs = []
    for d in sorted(outputs.glob("*_swarm_*"), reverse=True):
        meta, req = _read_json(d / "run.json"), _read_json(d / "requirements.json")
        if not (d / "handbook.md").exists() or meta.get("status") not in PUBLISHED:
            continue
        stamp = d.name[:19]
        try:
            when = datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            when = None
        runs.append({"dir": d, "meta": meta, "req": req, "when": when, "stamp": stamp,
                     "destination": req.get("destination") or "Expedition", "days": req.get("days")})
    return runs


def run_page(run: dict, with_emails: bool) -> str:
    meta, fin = run["meta"], run["meta"].get("finance") or {}
    md = (run["dir"] / "handbook.md").read_text(encoding="utf-8")
    if not with_emails:
        md = without_emails(md)
    title = f"{run['destination']}, {run['days']} days" if run["days"] else run["destination"]
    rows = [("Status", meta.get("status", "?")), ("Version", f"{meta.get('version', '?')} (git {meta.get('git', '?')}, rules {meta.get('rules_hash', '?')})"),
            ("Model", meta.get("model", "?")), ("GPU", meta.get("gpu", "?")), ("Iterations", meta.get("iterations", "?")),
            ("Total cost", f"{fin['total_eur']:.2f} EUR per person" if fin.get("total_eur") is not None else "?"),
            ("Run", run["stamp"])]
    table = "| | |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows)
    body = md.split("\n", 1)[1] if md.startswith("# ") else md  # the page title comes from the front matter
    return f"---\ntitle: {fm(title)}\noutline: [2, 3]\n---\n\n# {title}\n\n{sanitize(table)}\n\n{sanitize(body)}"


def publish(outputs: Path, site: Path, keep: int = 1, with_emails: bool = False) -> dict:
    runs_dir, bench_dir = site / "runs", site / "benchmarks"
    for d in (runs_dir, bench_dir):  # generated pages are rebuilt from scratch, so removed runs disappear
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    chosen, count = [], {}
    for run in find_runs(outputs):
        key = (run["destination"], run["days"])
        if count.get(key, 0) < keep:
            count[key] = count.get(key, 0) + 1
            chosen.append(run)
    pages = []
    for run in chosen:
        name = f"{slug(run['destination'])}-{run['days']}day-{run['stamp']}" if run["days"] else f"{slug(run['destination'])}-{run['stamp']}"
        (runs_dir / f"{name}.md").write_text(run_page(run, with_emails), encoding="utf-8")
        pages.append({**run, "name": name})
    benches = []
    for f in sorted(outputs.glob("summary_*.md"), reverse=True):
        text = f.read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ").strip() if text else f.stem
        (bench_dir / f.name).write_text(f"---\ntitle: {fm(title)}\n---\n\n{sanitize(text)}", encoding="utf-8")
        benches.append({"text": f"{title} ({f.stem[-19:]})", "link": f"/benchmarks/{f.stem}"})

    index = ["---", "layout: doc", "title: Expedition swarm", "---", "", "# Expedition swarm: living handbook", "",
             "Plans made by a swarm of local LLM agents, checked by code against real data (see [About](/about)). "
             "Every page lists the version, model and rules hash that produced it.", "", "## Expedition plans", ""]
    if pages:
        index += ["| Plan | Status | Cost per person | Model | Version | Date |", "|---|---|---|---|---|---|"]
        for p in pages:
            fin = p["meta"].get("finance") or {}
            cost = f"{fin['total_eur']:.0f} EUR" if fin.get("total_eur") is not None else "-"
            when = p["when"].strftime("%Y-%m-%d") if p["when"] else p["stamp"]
            title = f"{p['destination']}, {p['days']} days" if p["days"] else p["destination"]
            index.append(f"| [{title}](/runs/{p['name']}) | {p['meta'].get('status')} | {cost} | {p['meta'].get('model')} | "
                         f"{p['meta'].get('version')} | {when} |")
    else:
        index.append("_No published runs yet. Run the swarm, then `python publish_site.py`._")
    if benches:
        index += ["", "## Benchmarks", ""] + [f"- [{b['text']}]({b['link']})" for b in benches]
    (site / "index.md").write_text(sanitize("\n".join(index)) + "\n", encoding="utf-8")

    sidebar = {"runs": [{"text": (f"{p['destination']}, {p['days']} days" if p["days"] else p["destination"]) + f" ({p['stamp'][:10]})",
                         "link": f"/runs/{p['name']}"} for p in pages], "benchmarks": benches}
    (site / ".vitepress").mkdir(parents=True, exist_ok=True)
    (site / ".vitepress" / "generated.json").write_text(json.dumps(sidebar, indent=2), encoding="utf-8")
    return {"runs": len(pages), "benchmarks": len(benches)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outputs", default="outputs", help="folder with the run folders (default: outputs)")
    ap.add_argument("--site", default="site", help="the VitePress folder (default: site)")
    ap.add_argument("--keep", type=int, default=1, help="runs to publish per scenario, newest first (default: 1)")
    ap.add_argument("--with-emails", action="store_true", help="also publish the outreach emails")
    a = ap.parse_args()
    out = publish(Path(a.outputs), Path(a.site), a.keep, a.with_emails)
    print(f"[SITE] published {out['runs']} run(s) and {out['benchmarks']} benchmark summary(ies) into {a.site}/")
    print("[SITE] next: commit site/ and push (GitHub Actions builds it), or preview: cd site; npm install; npm run dev")
