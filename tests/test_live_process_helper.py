"""tests/_live_process.py: a live-tier timeout keeps the process's output."""
import sys

import pytest
from _live_process import run_reporting_timeout


def test_a_timeout_reports_elapsed_time_and_the_output_tail():
    script = "import sys, time; print('stage-1 done', flush=True); print('warn', file=sys.stderr, flush=True); time.sleep(30)"
    with pytest.raises(AssertionError) as failure:
        run_reporting_timeout([sys.executable, "-c", script], capture_output=True, text=True, timeout=2)
    message = str(failure.value)
    assert "timed out after" in message and "(limit 2s)" in message
    assert "stage-1 done" in message and "warn" in message


def test_a_completed_process_is_returned_unchanged():
    result = run_reporting_timeout([sys.executable, "-c", "print('ok')"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and result.stdout == "ok\n"
