"""FS-1 required behaviour (evidence; run explicitly:
PYTHONPATH=$PWD:$PWD/tests pytest -p conftest handover/evidence/fs1/test_fs1_required_behaviour.py).
Fails until FS-1 is fixed; moves into tests/ with the fix (no xfail in the suite)."""
from test_fs1_false_success_reproducer import BASE_MODULE, MODULE, _run


def test_a_requirement_backed_by_unexecuted_tests_never_succeeds(tmp_path, monkeypatch):
    """The goal requires tests; the only test written for the new behaviour
    has no execution evidence, and the only positive evidence for the
    requirement is the model's claim. Kriya must not report SUCCESS or apply
    the candidate."""
    observed = _run(tmp_path, monkeypatch)
    assert observed.result.legacy_result["status"] != "success"
    assert (observed.workspace / MODULE).read_text() == BASE_MODULE   # nothing applied


def test_the_suite_isolation_applies_here():
    import os

    from kriya.core.state_paths import ENV_STATE_DIR

    home = os.path.expanduser("~/.kriya")
    for variable in (ENV_STATE_DIR, "KRIYA_LOG_DIR", "KRIYA_QUALIFICATION_HOME"):
        assert os.environ.get(variable) and not os.environ[variable].startswith(home), variable
    assert os.environ.get("KRIYA_MODEL_RUNTIME_PROBE") == "0"
