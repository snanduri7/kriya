"""FS-1C0 harness: a real git repository (the authorized base), a candidate
applied on top of it as working-tree changes, and the production named-test
closure (``workflow.close_requirements_with_named_tests``) run on it with the
real PolymorphicValidator and real pytest.

Each scenario is a WRONG candidate (``value()`` returns 2; the unchanged named
test asserts 1) that makes the named test look passed through a
candidate-controlled channel other than the named test file itself - except
``correct``, the positive control, which must stay closable."""
import subprocess

from kriya.config.config import AutonomyConfig
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    RequirementOutcome,
    derive_requirements,
    record_requirement_verdicts,
    requirement_outcomes,
    seed_requirement_obligations,
)
from kriya.workflow.workflow import close_requirements_with_named_tests

GOAL = "Behaviour stays compatible with the legacy check test_legacy\n"
NAMED = "tests/test_legacy.py"

FLIP_HOOK = ("import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\n"
             "def pytest_runtest_makereport(item, call):\n    outcome = yield\n"
             "    outcome.get_result().outcome = \"passed\"\n")

BASE_APP = ("ENABLED = True\nDEFINED = True\n\n\ndef value():\n    return 1\n\n\n"
            "def feature_a():\n    return 1\n\n\ndef feature_b():\n    return 1\n")
BASE = {
    "requirements.txt": "",
    "app.py": BASE_APP,
    "tests/__init__.py": "",
    "tests/helpers.py": "def expected():\n    return 1\n",
    NAMED: ("import pytest\n\nimport app\nfrom tests.helpers import expected\n\n\n"
            "def test_legacy():\n    assert app.value() == expected()\n\n\n"
            "@pytest.mark.skipif(not app.ENABLED, reason=\"feature off\")\n"
            "def test_enabled():\n    assert app.feature_a() == 1\n\n\n"
            "if app.DEFINED:\n    def test_defined():\n        assert app.feature_b() == 1\n"),
}

WRONG_APP = BASE_APP.replace("def value():\n    return 1", "def value():\n    return 2")

SCENARIOS = {
    # Positive control: a correct candidate (app.py rewritten, still right) closes.
    "correct": {"app.py": BASE_APP + "\n\ndef added():\n    return 3\n"},
    # Negative control: a wrong candidate without tricks never closes.
    "wrong": {"app.py": WRONG_APP},
    # 1. a conftest.py hook flips the failing outcome (report AND exit code).
    "conftest_hook": {"app.py": WRONG_APP, "tests/conftest.py": FLIP_HOOK},
    # 2. a test-support module the named test imports is changed.
    "support_module": {"app.py": WRONG_APP, "tests/helpers.py": "def expected():\n    return 2\n"},
    # 3. runner configuration loads a production-side plugin that flips outcomes.
    "runner_config": {"app.py": WRONG_APP, "flip_plugin.py": FLIP_HOOK,
                      "pytest.ini": "[pytest]\npythonpath = .\naddopts = -p flip_plugin\n"},
    # 4. production code makes a baseline identity skip (exit 0, "passed, skipped").
    "identity_skipped": {"app.py": BASE_APP.replace("ENABLED = True", "ENABLED = False").replace(
        "def feature_a():\n    return 1", "def feature_a():\n    return 2")},
    # 5. production code removes a conditionally defined baseline identity (no trace in the report).
    "identity_missing": {"app.py": BASE_APP.replace("DEFINED = True", "DEFINED = False").replace(
        "def feature_b():\n    return 1", "def feature_b():\n    return 2")},
    # 6. production code rewrites the support module at import - after any
    #    pre-run check, before the named test imports it.
    "rewritten_during_run": {"app.py": WRONG_APP + (
        "\n\nimport os as _os\n\n_p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), \"tests\", \"helpers.py\")\n"
        "with open(_p, \"w\") as _f:\n    _f.write(\"def expected():\\n    return 2\\n\")\n")},
}

FORGING = ("conftest_hook", "support_module", "runner_config", "identity_skipped", "identity_missing",
           "rewritten_during_run")


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout


def make_base(root, files=None):
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    write(root, files or BASE)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


def write(root, files):
    for rel, text in files.items():
        path = root / rel
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def close(repo, *, modified, goal=GOAL, autonomy=None):
    """The production closure on ``repo`` (candidate root == workspace; the
    base is the repository HEAD) after the verifier said "satisfied" (a model
    claim: UNVERIFIED, FS-1B)."""
    reqs = derive_requirements(goal)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.SATISFIED, "") for r in reqs.requirements},
                                revision=1, evidence_fingerprint="cand-1", source="test")
    attempts = close_requirements_with_named_tests(
        autonomy or AutonomyConfig(), ledger, reqs, str(repo), str(repo), modified=modified, revision=1,
        toolchain_declaration_mutable=False)
    return attempts, requirement_outcomes(ledger, reqs), ledger


def scenario(tmp_path, name):
    repo = make_base(tmp_path / "repo")
    write(repo, SCENARIOS[name])
    attempts, outcomes, ledger = close(repo, modified=sorted(SCENARIOS[name]))
    return attempts, outcomes["REQ-1"], ledger
