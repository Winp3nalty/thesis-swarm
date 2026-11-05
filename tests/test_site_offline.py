"""Offline test of publish_site.py (no Node, no model). Run: python tests/test_site_offline.py"""
import _paths  # noqa: F401  (puts the project folder on the import path)

import json
import tempfile
from pathlib import Path

import publish_site

HANDBOOK = """# Expedition handbook: Shenzhen, China

- **Status:** SUCCESS
- **Budget:** 1800 EUR

## Day 1: Test

| Time | Location | Cost |
|---|---|---|
| 09:00-11:00 | DJI {{ danger }} <3 | 10 |

## Outreach emails

### Prof. Zhou, SUSTech

Dear Prof. Zhou, we would like a tour.

## Practical notes (reference data, verify before travel)

- Check the official sources.
"""


def make_run(outputs: Path, stamp: str, status="SUCCESS", handbook=True, days=7, dest="Shenzhen, China"):
    d = outputs / f"{stamp}_swarm_none_qwen3-8b"
    d.mkdir(parents=True)
    if handbook:
        (d / "handbook.md").write_text(HANDBOOK, encoding="utf-8")
    (d / "run.json").write_text(json.dumps({"status": status, "model": "qwen3:8b", "gpu": "RTX", "iterations": 2, "version": "v9",
                                            "git": "abc", "rules_hash": "r1", "finance": {"total_eur": 585.0}}), encoding="utf-8")
    (d / "requirements.json").write_text(json.dumps({"destination": dest, "days": days}), encoding="utf-8")
    return d


def test_sanitize_and_email_removal():
    s = publish_site.sanitize("a {{ x }} b <3 <details><summary>s</summary></details> <script>")
    assert "{{" not in s and "}}" not in s and "&lt;3" in s and "&lt;script>" in s
    assert "<details><summary>s</summary></details>" in s
    out = publish_site.without_emails(HANDBOOK)
    assert "Dear Prof. Zhou" not in out and "not published" in out
    assert "## Practical notes" in out and "## Day 1" in out, "only the emails section is removed"
    assert publish_site.without_emails("## Day 1\n\ntext\n") == "## Day 1\n\ntext\n"


def test_publish_picks_published_runs_and_writes_pages():
    with tempfile.TemporaryDirectory() as tmp:
        out, site = Path(tmp) / "outputs", Path(tmp) / "site"
        make_run(out, "2026-11-05_10-00-00")
        make_run(out, "2026-11-05_11-00-00")                       # newer, same scenario: wins with keep=1
        make_run(out, "2026-11-05_12-00-00", status="CRASH")        # not published
        make_run(out, "2026-11-05_13-00-00", handbook=False)        # no handbook: not published
        make_run(out, "2026-11-05_09-00-00", days=3, dest="Silicon Valley, USA")
        (out / "summary_shenzhen_7day_2026-11-05_14-00-00.md").write_text("# Batch summary: shenzhen_7day\n\n| a |\n", encoding="utf-8")
        res = publish_site.publish(out, site)
        assert res == {"runs": 2, "benchmarks": 1}, res
        pages = sorted(p.name for p in (site / "runs").glob("*.md"))
        assert pages == ["shenzhen-china-7day-2026-11-05_11-00-00.md", "silicon-valley-usa-3day-2026-11-05_09-00-00.md"], pages
        page = (site / "runs" / pages[0]).read_text(encoding="utf-8")
        assert page.startswith('---\ntitle: "Shenzhen, China, 7 days"') and "585.00 EUR per person" in page
        assert "Dear Prof. Zhou" not in page and "{{" not in page and "# Expedition handbook" not in page
        bench = (site / "benchmarks" / "summary_shenzhen_7day_2026-11-05_14-00-00.md").read_text(encoding="utf-8")
        assert bench.startswith('---\ntitle: "Batch summary: shenzhen_7day"\n---'), bench[:80]  # a colon needs quotes in YAML
        home = (site / "index.md").read_text(encoding="utf-8")
        assert "/runs/shenzhen-china-7day-2026-11-05_11-00-00" in home and "Benchmarks" in home
        side = json.loads((site / ".vitepress" / "generated.json").read_text(encoding="utf-8"))
        assert len(side["runs"]) == 2 and side["benchmarks"][0]["link"].startswith("/benchmarks/summary_shenzhen_7day")
        # --keep 2 publishes both Shenzhen runs; --with-emails keeps the emails; removed runs disappear on republish
        assert publish_site.publish(out, site, keep=2, with_emails=True)["runs"] == 3
        assert "Dear Prof. Zhou" in (site / "runs" / "shenzhen-china-7day-2026-11-05_11-00-00.md").read_text(encoding="utf-8")
        for d in out.glob("2026-11-05_1*"):
            for f in d.iterdir():
                f.unlink()
            d.rmdir()
        assert publish_site.publish(out, site)["runs"] == 1 and len(list((site / "runs").glob("*.md"))) == 1


def test_empty_outputs_still_make_a_site():
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "site"
        assert publish_site.publish(Path(tmp) / "nothing", site) == {"runs": 0, "benchmarks": 0}
        assert "No published runs yet" in (site / "index.md").read_text(encoding="utf-8")
        assert json.loads((site / ".vitepress" / "generated.json").read_text(encoding="utf-8"))["runs"] == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nAll {len(tests)} site tests passed.")
