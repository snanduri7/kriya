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


def test_process_controller_closes_stdin_by_default_instead_of_hanging(tmp_path):
    """Runtime Verification Contract (PRV-06, 2026-08-29) - live incident:
    a generated app blocking on stdin (System.in/readLine()), invoked with
    no stdin_payload, used to inherit this process's own open stdin and
    block for the FULL timeout window before being killed. stdin is now
    ALWAYS an explicit pipe, closed immediately (EOF) when no payload is
    given - a blocking read must see EOF right away, not hang."""
    controller = ProcessController()
    result = controller.run(
        [sys.executable, "-c", "import sys; print('GOT:' + (sys.stdin.readline() or 'EOF'))"],
        cwd=str(tmp_path), timeout=30,
    )
    assert not result.timeout
    assert "GOT:EOF" in result.stdout


def test_process_controller_delivers_stdin_payload_and_closes_it(tmp_path):
    controller = ProcessController()
    result = controller.run(
        [sys.executable, "-c", "import sys; print('GOT:' + sys.stdin.readline().strip())"],
        cwd=str(tmp_path), timeout=30, stdin_payload="kriya-verification-input\n",
    )
    assert not result.timeout
    assert "GOT:kriya-verification-input" in result.stdout
