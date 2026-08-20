import sys

from kriya.tools.process import ProcessController


def test_process_controller_bounds_captured_output(tmp_path):
    controller = ProcessController(max_output_chars=100)
    result = controller.run(
        [sys.executable, "-c", "print('x' * 1000)"],
        cwd=str(tmp_path), timeout=5,
    )

    assert result.returncode == 0
    assert result.stdout_truncated
    assert result.stdout.endswith("x" * 90 + "\n")


def test_process_controller_marks_timeout_and_returns(tmp_path):
    controller = ProcessController(reap_timeout=1)
    result = controller.run(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        cwd=str(tmp_path), timeout=1,
    )

    assert result.timeout
    assert "[TIMEOUT]" in result.stderr
