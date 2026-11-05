"""Imported first by every test file: puts the project folder on the import path, so the tests run from anywhere
(python tests/test_core.py, or from inside the tests folder)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
