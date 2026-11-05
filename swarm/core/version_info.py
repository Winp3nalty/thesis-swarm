"""
What version of the project produced a result?

  version      label from version.txt (edit that file by hand at milestones)
  git_commit   short git hash, '-dirty' if tracked files have uncommitted changes, 'no-git' if unknown
  rules_hash   short hash of the prompts/*/SOUL.md files, data/cost, transit, geo, culture and hotels *.json, because
               changing a prompt or the cost table changes behaviour without changing any code

Stdlib only, so every other module can import it.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from swarm.core.paths import ROOT as _ROOT
_CACHE: dict | None = None


def read_version(path=None) -> str:
    """First non-empty, non-comment line of version.txt, or 'unversioned'."""
    p = Path(path) if path else _ROOT / "version.txt"
    try:
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    except OSError:
        pass
    return "unversioned"


def git_commit() -> str:
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=_ROOT, capture_output=True,
                              text=True, timeout=10)
        if head.returncode != 0 or not head.stdout.strip():
            return "no-git"
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=_ROOT,
                               capture_output=True, text=True, timeout=10)
        return head.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError):
        return "no-git"


def rules_hash(root=None) -> str:
    base = Path(root) if root else _ROOT
    files = sorted((base / "prompts").glob("*/SOUL.md")) + sorted((base / "data" / "cost").glob("*.json")) \
        + sorted((base / "data" / "transit").glob("*.json")) \
        + sorted((base / "data" / "geo").glob("*.json")) + sorted((base / "data" / "hotels").glob("*.json")) + sorted((base / "data" / "culture").glob("*.json"))
    h = hashlib.sha256()
    for f in files:
        try:
            h.update(f.name.encode() + b"\0" + f.read_bytes().replace(b"\r\n", b"\n"))
        except OSError:
            h.update(f.name.encode() + b"\0missing")
    return h.hexdigest()[:8]


def version_info() -> dict:
    """{'version', 'git_commit', 'rules_hash'}, computed once per process."""
    global _CACHE
    if _CACHE is None:
        _CACHE = {"version": read_version(), "git_commit": git_commit(), "rules_hash": rules_hash()}
    return dict(_CACHE)


def version_line() -> str:
    v = version_info()
    return f"{v['version']} (git {v['git_commit']}, rules {v['rules_hash']})"
