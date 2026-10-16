"""Module-tier selectors never select a default-excluded tier.

pyproject's addopts deselects live_model / live_static_analysis / real_toolchain tests by default, but an explicit
`-m <module marker>` replaces that expression. The W2 wave check ran `-m "application or context or adapters"` and
selected tests/test_live_prd033_metrics.py (a live_model test mapped to `adapters`), which failed for lack of a
qualified local model: a defect of the module-marker mapping, not of the product.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent


def _collect(marker, target):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-m", marker, target],
        cwd=REPO, capture_output=True, text=True, check=False)


def test_a_module_marker_never_selects_a_live_model_test():
    # test_live_prd033_metrics.py is mapped to the `adapters` tier AND is a live_model file.
    result = _collect("adapters", "tests/test_live_prd033_metrics.py")
    assert "test_live_prd033_metrics.py::" not in result.stdout, result.stdout


def test_the_same_file_is_still_selectable_by_its_live_marker():
    # The exclusion is only for MODULE markers: the explicit live tier still selects it (control for the test above).
    result = _collect("live_model", "tests/test_live_prd033_metrics.py")
    assert "test_live_prd033_metrics.py::" in result.stdout, result.stdout
