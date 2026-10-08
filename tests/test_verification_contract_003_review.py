"""VERIFICATION-CONTRACT-003 independent review reconciliation (reviews/ARCHITECTURE_REVIEW.md, 2026-10-08): every
finding pinned with the reviewer's own failing input. VC3-R1 (BLOCKING): a second test clause the recognizers do not
match must keep the behaviour claim; R2 the doctest rule applies only to a statement that is a session; R3 a comment
or comparison in a code block is an expectation; R4 a conditional belongs to the documentation clause only when it
follows it; R5 re-exports and constants are API surface; R6 constraint adjectives are not labels; R7 an added test
file must contain a test; R9 a regression claim executes the suite at baseline; R10 the contract digest is independent
of the state directory; plus the bundle's base-revision recheck at closure and the launcher dispatch.
"""
import json
import subprocess
from types import SimpleNamespace

import pytest

from kriya.config.config import AutonomyConfig
from kriya.tools.containment import NetworkAuthority
from kriya.workflow import api_preservation as api
from kriya.workflow import authority_bundle as ab
from kriya.workflow import contract_baseline as cb
from kriya.workflow import contract_closers as cc
from kriya.workflow import requirement_scopes as rs
from kriya.workflow.contract_compilation import (
    CLOSER_DOCUMENTATION_NOT_APPLICABLE,
    STATUS_AUTHORITY_REQUIRED,
    ExternalAuthority,
    compile_verification_contract,
)
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    API_PRESERVATION,
    BEHAVIOR,
    BEHAVIOR_EXACT,
    BEHAVIOR_GENERAL,
    ORIGIN_CODE_BLOCK,
    REGRESSION_PRESERVATION,
    RequirementOutcome,
    behavior_strength,
    derive_requirements,
    record_requirement_verdicts,
    requirement_outcomes,
    seed_requirement_obligations,
    statement_origins,
)


def _scope(text, origin="sentence"):
    return rs.statement_scope("REQ-1", text, origin=origin, test_files=["tests/test_a.py"])


# ---------------------------------------------------------------- R1 (BLOCKING)
@pytest.mark.parametrize("text", [
    "Do not change the public API; do not break the tests.",
    "Do not change the public API and do not delete tests.",
    "Keep the public API unchanged and all tests passing.",
    "Keep the public API unchanged and every test passing.",
    "Keep the public API unchanged; the tests must stay green.",
    "Do not change the public API and do not touch other tests.",
])
def test_r1_an_unrecognized_second_clause_keeps_the_behaviour_claim(text):
    scope = _scope(text)
    assert API_PRESERVATION in scope.claims and BEHAVIOR in scope.claims, scope.claims
    reqs = derive_requirements(text + "\n")
    contract = compile_verification_contract(reqs, origins=statement_origins(text), test_files=["tests/test_a.py"],
                                             project_language="python")
    assert contract.entry("REQ-1").status == STATUS_AUTHORITY_REQUIRED
    assert contract.report()["admission"] == "VERIFICATION_AUTHORITY_REQUIRED"


def test_r1_recognized_clauses_still_make_a_clause_only_constraint():
    for text in ("Constraints: every existing public API and every existing test must keep passing unchanged.",
                 "Keep the public interface unchanged and every existing test green.", "The public API must stay unchanged."):
        assert BEHAVIOR not in _scope(text).claims, text


# ---------------------------------------------------------------- R2
def test_r2_the_doctest_rule_applies_only_to_a_statement_that_is_a_session():
    assert behavior_strength("Make f square its input: >>> f(2) 4 and never raise for any integer input", regression_covered=False)[0] == BEHAVIOR_GENERAL
    assert behavior_strength("Use the >>> prompt for all examples and always echo every result", regression_covered=False)[0] == BEHAVIOR_GENERAL
    assert behavior_strength(">>> from toolz import interpose >>> list(interpose('a', [])) []", regression_covered=False)[0] == BEHAVIOR_EXACT


# ---------------------------------------------------------------- R3
@pytest.mark.parametrize("block", ["print(len(cache))  # 3 (correct value is 2)", "print(f(2)) # prints 5, correct value 4",
                                   "x = f(2) # 4", "cache.expire(3) == [(1, 1)]", "cache[1] = 1 # t=0"])
def test_r3_a_comment_or_comparison_in_a_code_block_is_an_expectation(block):
    assert rs.code_block_non_claim_reason(block, ORIGIN_CODE_BLOCK) is None
    assert not _scope(block, origin=ORIGIN_CODE_BLOCK).is_non_claim


def test_r3_a_bare_block_without_a_marker_is_still_a_non_claim():
    block = 'String text = "hello,\\n\\n\\n"; List<CSVRecord> records = CSVParser.parse(text, CSVFormat.EXCEL).getRecords();'
    assert _scope(block, origin=ORIGIN_CODE_BLOCK).is_non_claim


# ---------------------------------------------------------------- R4
def test_r4_a_conditional_stated_for_another_clause_never_makes_the_documentation_conditional():
    clause = rs.documentation_clause("Fix expire() so that it drops entries at exactly t+N if any exist, and document that in the README.")
    assert clause["conditional"] is False
    text = "Fix expire() so that expire(3) -> [(1, 1)] if any entries exist, and document that in the README.\n"
    contract = compile_verification_contract(derive_requirements(text), origins=statement_origins(text), test_files=[],
                                             tracked_paths=["cache.py"], project_language="python")
    assert CLOSER_DOCUMENTATION_NOT_APPLICABLE not in contract.entry("REQ-1").closers
    assert rs.documentation_clause("Document them in the README's function list if there is one.")["conditional"] is True


# ---------------------------------------------------------------- R5
def test_r5_reexports_and_public_constants_are_api_surface():
    base = b"from .impl import helper\nimport os as _os\nMAX_SIZE = 10\nDEFAULTS: dict = {'a': 1}\n_private = 3\n"
    surface = api.public_signatures(base, "pkg")
    assert surface["pkg.helper"].startswith("reexport(") and surface["pkg.MAX_SIZE"] == "name(value=10)"
    assert "pkg._os" not in surface and "pkg._private" not in surface and "pkg.DEFAULTS" in surface
    changed = api.public_signatures(b"MAX_SIZE = 20\n", "pkg")
    assert set(surface) - set(changed) == {"pkg.helper", "pkg.DEFAULTS"} and changed["pkg.MAX_SIZE"] != surface["pkg.MAX_SIZE"]


# ---------------------------------------------------------------- R6
@pytest.mark.parametrize("text", ["Zero regressions:", "Backward compatible:", "Thread safety:", "Idempotent writes:",
                                  "Same output:", "Identical results:", "Linux only:", "No regressions:"])
def test_r6_constraint_adjectives_written_as_headings_are_claims(text):
    assert rs.label_non_claim_reason(text) is None and not _scope(text).is_non_claim


# ---------------------------------------------------------------- R7
def test_r7_an_added_test_file_must_contain_a_test():
    text = "Make calc.lower('A') -> 'a' and add a regression test for it.\n"
    reqs = derive_requirements(text)
    contract = compile_verification_contract(reqs, origins=statement_origins(text), test_files=["tests/test_a.py"])
    files = {"tests/test_new.py": b"", "tests/test_real.py": b"import calc\n\n\ndef test_lower():\n    assert calc.lower('A') == 'a'\n",
             "tests/test_helper.py": b"HELPER = 1\n"}
    for added, closed in (("tests/test_new.py", False), ("tests/test_helper.py", False), ("tests/test_real.py", True)):
        ledger = ObligationLedger()
        seed_requirement_obligations(ledger, reqs)
        record_requirement_verdicts(ledger, reqs, {"REQ-1": (RequirementOutcome.UNVERIFIED, "x")}, revision=1,
                                    evidence_fingerprint="cand", source="t")
        [entry] = cc.close_test_addition_requirements(ledger, reqs, contract, reference_test_files=["tests/test_a.py"],
                                                      candidate_test_files=["tests/test_a.py", added], base_test_identities=None,
                                                      candidate_test_identities=None, source="t", revision=1,
                                                      read_candidate=lambda p: files.get(p))
        assert entry["closed"] is closed, (added, entry)
    assert cc.file_contains_a_test("T.java", b"import org.junit.jupiter.api.Test;\nclass T { @Test void t() {} }")
    assert not cc.file_contains_a_test("tests/test_x.py", None) and not cc.file_contains_a_test("tests/test_x.py", b"def broken(:\n")


# ---------------------------------------------------------------- R9
def test_r9_a_regression_claim_executes_the_suite_at_baseline():
    text = "Examples:\n  calc.lower('ABC') -> 'abc'\n\nEvery existing test must keep passing.\n"
    reqs = derive_requirements(text)
    examples = ExternalAuthority("goal_examples", "e" * 64, {"REQ-1": {BEHAVIOR: {"accepted_strength": BEHAVIOR_EXACT}}})
    contract = compile_verification_contract(reqs, origins=statement_origins(text), test_files=["tests/test_a.py"],
                                             external_authorities=[examples], project_language="python")
    passing = SimpleNamespace(passed=True, violated=False)
    # no suite judge: the regression claim is unsatisfied, never identity
    report = cb.run_baseline_authorities(contract, reqs, base_revision="abc", judge_examples=lambda: {"REQ-1": passing}, examples_digest="e" * 64)
    assert report.no_mutation_required is False and report.claims["REQ-2"][REGRESSION_PRESERVATION]["state"] == "UNBOUND"
    # a red baseline suite is never a no-mutation success
    report = cb.run_baseline_authorities(contract, reqs, base_revision="abc", judge_examples=lambda: {"REQ-1": passing},
                                         examples_digest="e" * 64, judge_suite=lambda: cb.BASELINE_FAIL)
    assert report.no_mutation_required is False and report.claims["REQ-2"][REGRESSION_PRESERVATION]["state"] == "FAIL"
    report = cb.run_baseline_authorities(contract, reqs, base_revision="abc", judge_examples=lambda: {"REQ-1": passing},
                                         examples_digest="e" * 64, judge_suite=lambda: cb.BASELINE_PASS)
    assert report.no_mutation_required is True


# ---------------------------------------------------------------- R10
def test_r10_the_contract_digest_does_not_depend_on_where_the_state_directory_lives():
    text = "lower('ABC') -> 'abc'.\n"
    reqs = derive_requirements(text)
    def contract(stored):
        authority = ExternalAuthority("goal_examples", "e" * 64, {"REQ-1": {BEHAVIOR: {"accepted_strength": BEHAVIOR_EXACT}}},
                                      provenance={"compiler_version": 1, "stored_path": stored})
        return compile_verification_contract(reqs, origins=statement_origins(text), test_files=[], external_authorities=[authority])
    assert contract("/a/state/x.py").digest == contract("/b/state/x.py").digest
    assert contract("/a/state/x.py").report()["authorities"][0]["provenance"]["stored_path"] == "/a/state/x.py"  # still reported


# ---------------------------------------------------------------- bundle: base revision at closure, launcher dispatch
GOAL = "TTLCache keeps entries alive one tick too long at the expiry boundary.\nDo not change the public API.\n"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _bundle(tmp_path, build_tool="pip", language="python", prepare=None):
    from test_verification_contract_003_authority import _bundle_dir, _load, _workspace
    tmp_path.mkdir(exist_ok=True)
    ws, base = _workspace(tmp_path)
    if language == "java":
        (ws / "pom.xml").write_text("<project/>")
        _git(ws, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        _git(ws, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "pom")
        base = _git(ws, "rev-parse", "HEAD")
    reqs = derive_requirements(GOAL)
    overrides = {"toolchain": {"language": language, "build_tool": build_tool}}
    if prepare is not None:
        overrides["prepare"] = prepare
    bundle_dir = _bundle_dir(tmp_path, reqs, base, **overrides)
    return ws, reqs, _load(tmp_path, bundle_dir, reqs, base, ws, language=language)


def test_bundle_closure_refuses_another_base_revision(tmp_path):
    ws, reqs, bundle = _bundle(tmp_path)
    contract = compile_verification_contract(reqs, origins=statement_origins(GOAL), test_files=[],
                                             external_authorities=[bundle.authority()], project_language="python")
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.UNVERIFIED, "x") for r in reqs.requirements},
                                revision=1, evidence_fingerprint="cand", source="t")
    calls = []
    entries = ab.close_requirements_with_authority_bundle(
        ledger, reqs, bundle, contract, execute=lambda: calls.append(1) or ab.AuthorityRun(verdict="PASS", reason_code="x"),
        source="t", revision=1, base_revision="0" * 40)
    assert calls == [] and entries[0]["reason_code"] == ab.AUTHORITY_BASE_MISMATCH
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    entries = ab.close_requirements_with_authority_bundle(
        ledger, reqs, bundle, contract, execute=lambda: ab.AuthorityRun(verdict="PASS", reason_code="x"),
        source="t", revision=1, base_revision=bundle.base_revision)
    assert entries[0]["closed"] is True


class _Recorder:
    def __init__(self, root):
        self.workspace_path = root
        self.autonomy_cfg = AutonomyConfig(contained_execution_required=True)
        self.tree_binding = None
        self.calls = []

    def _dependency_cache_dir(self, tool):
        return f"/cache/{tool}"

    def _run_gradle_cmd(self, gradle_cmd, tasks, cwd, timeout=600, deadline=None):
        self.calls.append(("gradle", gradle_cmd, list(tasks), timeout))
        return {"returncode": 0, "stdout": "", "stderr": "", "timeout": False}

    def _run_maven_cmd(self, goals, cwd, timeout=300, **kw):
        self.calls.append(("maven", list(goals), timeout))
        return {"returncode": 0, "stdout": "", "stderr": "", "timeout": False}

    def _run_cmd_with_timeout(self, cmd, cwd, timeout=300, network=NetworkAuthority.DENIED, **kw):
        self.calls.append(("raw", list(cmd), network))
        return {"returncode": 0, "stdout": "", "stderr": "", "timeout": False}


def test_a_gradle_or_maven_prepare_runs_through_the_validators_two_phase_path(tmp_path):
    ws, reqs, bundle = _bundle(tmp_path, build_tool="gradle", language="java",
                               prepare=["./gradlew", ":hamcrest:compileJava"])
    rec = {}
    def factory(root):
        rec["v"] = _Recorder(root)
        return rec["v"]
    run = ab.run_authority_bundle(bundle, str(ws), state_root=str(tmp_path / "state"), validator_factory=factory)
    assert run.verdict == "PASS"
    assert rec["v"].calls[0] == ("gradle", "./gradlew", [":hamcrest:compileJava"], bundle.timeout_seconds)
    assert rec["v"].calls[1][0] == "raw" and rec["v"].calls[1][2] is NetworkAuthority.DENIED  # verify stays a denied command
    ws2, reqs2, maven = _bundle(tmp_path / "m", build_tool="maven", language="java", prepare=["mvn", "-q", "test-compile"])
    rec2 = {}
    def factory2(root):
        rec2["v"] = _Recorder(root)
        return rec2["v"]
    assert ab.run_authority_bundle(maven, str(ws2), state_root=str(tmp_path / "state2"), validator_factory=factory2).verdict == "PASS"
    assert rec2["v"].calls[0] == ("maven", ["-q", "test-compile"], maven.timeout_seconds)
    # a launcher is never allowed as an absolute path, and only for its own build tool
    with pytest.raises(ab.AuthorityBundleError):
        _bundle(tmp_path / "x", build_tool="pip", prepare=["/usr/bin/gradlew", "build"])
    _, _, pip_bundle = _bundle(tmp_path / "y", build_tool="pip", prepare=["./gradlew", "build"])  # accepted as a launcher name...
    rec3 = {}
    def factory3(root):
        rec3["v"] = _Recorder(root)
        return rec3["v"]
    ab.run_authority_bundle(pip_bundle, str(tmp_path / "y" / "ws"), state_root=str(tmp_path / "state3"), validator_factory=factory3)
    assert rec3["v"].calls[0][0] == "raw"  # ...but a pip bundle never gets the Gradle two-phase path


def test_review_record_exists():
    import pathlib
    assert (pathlib.Path(__file__).resolve().parents[1] / "handover" / "VERIFICATION_CONTRACT_003_DESIGN.md").exists()
    json.dumps({"ok": True})
