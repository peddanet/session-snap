"""pytest configuration: load the progress plugin tools/pytest_progress.py (dev-conventions bundle).

The plugin prints a progress bar instead of pytest's dots and lists the five
slowest tests; PYTEST_PROGRESS=0 switches it off.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
pytest_plugins = ["pytest_progress"]
