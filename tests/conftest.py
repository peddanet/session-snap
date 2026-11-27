"""pytest configuration: load the plugins of the dev-conventions bundle from tools/.

pytest_progress prints a progress bar instead of pytest's dots and lists the
slowest tests and modules (PYTEST_PROGRESS=0 switches it off). bundle_tests
skips the tests of the files copied unchanged from the bundle while
tools/bundle.sha256 still matches (CHECK_BUNDLE_TESTS=1 runs them anyway).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
pytest_plugins = ["pytest_progress", "bundle_tests"]
