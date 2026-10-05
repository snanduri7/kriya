"""FS-1C finding (Track A), fixed by FS-1C0: the named-test closure's oracle
depended on files it did not guard - a candidate conftest.py hook turned the
unchanged named test's failure into a pass (report AND exit code), and the
requirement closed. ``close_with_candidate`` keeps Track A's specimen
signature (handover/evidence/fs1c/test_named_test_oracle_required.py imports
it) and now drives the production closure on a real git base; the pre-fix pin
is kept as evidence (handover/evidence/fs1c/test_named_test_oracle_prefix_pin.py)."""
from _named_test_oracle_harness import FLIP_HOOK, close, make_base, write

from kriya.workflow.named_test_oracle import ORACLE_DEPENDENCY_CHANGED, ORACLE_IDENTITY_NOT_PASSED
from kriya.workflow.requirements import RequirementOutcome

LEGACY = "from app import value\n\n\ndef test_legacy():\n    assert value() == 1\n"


def close_with_candidate(tmp_path, conftest):
    """Track A's specimen: the candidate breaks app.value() and (optionally)
    adds a conftest.py; the named test file itself is unchanged."""
    repo = make_base(tmp_path / "repo", {"requirements.txt": "", "app.py": "def value():\n    return 1\n",
                                          "tests/__init__.py": "", "tests/test_legacy.py": LEGACY})
    candidate = {"app.py": "def value():\n    return 2\n"}  # candidate: wrong
    if conftest:
        candidate["tests/conftest.py"] = FLIP_HOOK
    write(repo, candidate)
    [attempt], outcomes, _ = close(repo, modified=sorted(candidate))
    return attempt, outcomes["REQ-1"]


def test_control_the_unchanged_named_test_catches_the_wrong_candidate(tmp_path):
    attempt, outcome = close_with_candidate(tmp_path, conftest=False)
    assert attempt["closed"] is False and outcome is RequirementOutcome.UNVERIFIED
    assert attempt["reason_code"] == ORACLE_IDENTITY_NOT_PASSED


def test_a_candidate_changed_oracle_dependency_never_closes_a_requirement(tmp_path):
    attempt, outcome = close_with_candidate(tmp_path, conftest=True)
    assert attempt["closed"] is False and outcome is RequirementOutcome.UNVERIFIED
    assert attempt["reason_code"] == ORACLE_DEPENDENCY_CHANGED and "tests/conftest.py" in attempt["reason"]
