"""REG-R1 generic reproducer: a real pytest project whose pre-existing failures print run-varying text.

The live defect (OBS-4, run 20261006T231821-fb31caad) was a pytest suite whose already-failing tests print values that
differ on every execution of the SAME code - a measured ratio in one test's assertion message, a container hostname in
another test's traceback arguments. Two runs of the untouched baseline then differ in their whole output, and the
whole-output fingerprint called that a candidate regression. This fixture reproduces the mechanism generically:

- ``test_message_varies_each_run``: the assertion MESSAGE (and so the body) differs on every run;
- ``test_traceback_arguments_vary_each_run``: the message is stable, the traceback BODY differs (a frame argument);
- ``test_stable_failure``: an already-failing test whose failure text never changes;
- passing tests.

Nothing here is specific to any repository; the run-varying values come from ``os.urandom``.
"""
import sys

from kriya.tools.validate import PolymorphicValidator

SUITE = '''import os


def _run_tool(arguments):
    raise RuntimeError("tool failed")


def test_passes():
    assert True


def test_also_passes():
    assert [1, 2] == [1, 2]


def test_stable_failure():
    assert 1 + 1 == 3, "arithmetic is stable"


def test_message_varies_each_run():
    observed = int.from_bytes(os.urandom(4), "big")
    assert observed < 0, f"observed value {observed} was not negative"


def test_traceback_arguments_vary_each_run():
    _run_tool({"RUN_ID": os.urandom(6).hex()})
'''

TEST_FILE = "tests/test_suite.py"
STABLE = "tests/test_suite.py::test_stable_failure"
VARYING_MESSAGE = "tests/test_suite.py::test_message_varies_each_run"
VARYING_BODY = "tests/test_suite.py::test_traceback_arguments_vary_each_run"


def write_project(root, suite=SUITE):
    """A minimal Python project (requirements.txt marker) holding ``suite`` as tests/test_suite.py."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "requirements.txt").write_text("")
    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "__init__.py").write_text("")
    (tests / "test_suite.py").write_text(suite)
    return root


def validator_for(root):
    """The production test gate on ``root``, host mode, Kriya's own interpreter (no dependency install)."""
    validator = PolymorphicValidator(str(root))
    validator._resolve_python_interpreter = lambda: (sys.executable, None)  # pylint: disable=protected-access
    return validator
