"""
Installs the project's one dependency (pydantic) on first run, so no manual pip step is needed.

Entry scripts start with:   from swarm.core import deps; deps.ensure()
The list of packages is requirements.txt. If the automatic install fails (for example no internet),
the script stops with the exact command to run by hand.
"""
import importlib
import importlib.util
import subprocess
import sys
from swarm.core.paths import REQUIREMENTS_FILE as REQUIREMENTS


def ensure() -> None:
    if importlib.util.find_spec("pydantic"):
        return
    print("[setup] pydantic is missing in this Python environment. Installing from requirements.txt ...")
    result = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)])
    importlib.invalidate_caches()
    if result.returncode != 0 or not importlib.util.find_spec("pydantic"):
        raise SystemExit("[setup] Could not install pydantic automatically. Run this once, then try again:\n"
                         f"    {sys.executable} -m pip install -r requirements.txt")
    print("[setup] pydantic installed.")
