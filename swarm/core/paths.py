"""Where things live. Every module that reads a prompt, a data file or a scenario asks this file."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # the project folder (where run_loop.py is)
DATA_DIR = ROOT / "data"
PROMPTS_DIR = ROOT / "prompts"          # one SOUL.md per model-driven agent
SCENARIO_DIR = ROOT / "scenarios"
TESTS_DIR = ROOT / "tests"
MODEL_FILE = ROOT / "model.txt"
VERSION_FILE = ROOT / "version.txt"
REQUIREMENTS_FILE = ROOT / "requirements.txt"
