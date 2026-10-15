"""D3 (live Greenfield failure, run 20261015T152407-5f87716a, attempt 6): `diagnosis_mismatch` vetoed a candidate on pom.xml and
discarded it, while a deterministic validator was about to evaluate that very candidate as a whole.

MEASURED mechanism (handover/DEMO-V2/LIVE-GREENFIELD-FAILURE-ROOT-CAUSE.md + the sealed blobs used as the fixture here): the
retry responded to `static_rule_violation [vacuous_test_assertion]` (about AppTest.java). The model rewrote pom.xml, App.java and
AppTest.java; its pom.xml analysis quoted `junit:junit:4.13.2` / `org.junit.jupiter:junit-jupiter` (Maven coordinate notation,
which no pom.xml contains literally) and `org.junit.jupiter.api.Test` (already imported by the test attempt 5 wrote). The prose
heuristic therefore reported "none of that appears new", refused pom.xml, and the whole candidate was discarded with a verdict
about the wrong file, before the static rule could judge the candidate as a whole.

Definition used here: a prose-vs-diff heuristic is advisory evidence about a response; it must not outrank, or pre-empt, the
deterministic validator that is the authority for the finding being repaired and that evaluates the WHOLE candidate right after
the write loop. A retry that responds to a deterministic gate failure therefore defers to that gate (the existing policy for
compile / pom_semantic_validation / targeted_test, now also static_rule_violation). Everywhere else the heuristic is unchanged.
"""
import gzip
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from _strict_doubles import developer_double

from kriya.workflow.attempt import _diagnosis_mismatch_bypass_reason, run_attempt
from kriya.workflow.attribution import find_edits_ignoring_own_diagnosis
from kriya.workflow.context_package import make_context_item
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.state import GenerationState

EVIDENCE = Path(__file__).resolve().parent.parent / "handover/DEMO-V2/evidence/live-greenfield-failure/attempt-evidence"
POM, MAIN_FILE, TEST_FILE = "pom.xml", "src/main/java/hellokriya/App.java", "src/test/java/hellokriya/AppTest.java"
OLD_MAIN = "package hellokriya;\npublic class App {}\n"
REAL_TEST = (
    "package hellokriya;\nimport org.junit.jupiter.api.Test;\nimport static org.junit.jupiter.api.Assertions.assertEquals;\n"
    'public class AppTest {\n    @Test\n    public void greets() { assertEquals("Hello from Kriya", App.message()); }\n}\n'
)


def _records():
    with open(EVIDENCE / "records.jsonl", encoding="utf-8") as handle:
        return {record["seq"]: record for record in map(json.loads, handle)}


def _blob(ref):
    digest = ref.split(":", 1)[1]
    return gzip.decompress((EVIDENCE / "blobs" / digest[:2] / f"{digest}.gz").read_bytes()).decode("utf-8")


def _file_block(text):
    return re.search(r'<<<KRIYA:FILE path="[^"]+">>>\n(.*?)<<<KRIYA:END_FILE>>>', text, re.S).group(1)


@pytest.fixture(scope="module")
def attempt6():
    """The model's REAL attempt-6 candidate and the files it replaced, from the sealed live-run evidence."""
    records = _records()
    pom_response = _blob(records[181]["blobs"]["content"])
    return {
        "analysis": pom_response.split("<<<KRIYA:")[0].strip(),
        "new_pom": _file_block(pom_response),
        "new_main": _file_block(_blob(records[186]["blobs"]["content"])),
        "new_test": _file_block(_blob(records[191]["blobs"]["content"])),
        "old_pom": _blob(records[204]["blobs"]["proposed"]),          # record 204 logs failed_content=prior_content: the OLD pom
        "old_test": _blob(records[159]["blobs"]["after"]),            # the AppTest attempt 5 left in the worktree
    }


def _setup(tmp_path, attempt6, signature, new_test=None):
    from test_workflow import _minimal_attempt_ctx  # noqa: PLC0415 - heavy module, only these tests need it

    old = {POM: attempt6["old_pom"], TEST_FILE: attempt6["old_test"], MAIN_FILE: OLD_MAIN}
    state = GenerationState()
    for path, text in old.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        state.known_target_context_items[path] = make_context_item(
            path=path, content=text, reason="known_target", source_type="named_in_request", trust_level="repository",
            tier="full", is_exact=True, omitted_regions=False, revision=content_revision(text))
    state.all_files_written = set(old)
    state.budgets.last_failure_signature = signature
    developer = developer_double()
    developer.run_generation = AsyncMock(return_value=[
        {"filepath": POM, "content": attempt6["new_pom"], "analysis": attempt6["analysis"]},
        {"filepath": MAIN_FILE, "content": attempt6["new_main"]},
        {"filepath": TEST_FILE, "content": new_test if new_test is not None else attempt6["new_test"]},
    ])
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer, architect_files=list(old), expected_files_upfront=list(old),
        architect_basename_to_path={"pom.xml": POM, "App.java": MAIN_FILE, "AppTest.java": TEST_FILE},
    )
    return state, ctx


async def _run(state, ctx):
    ok = {"success": True, "output": ""}
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value=ok), \
         patch("kriya.tools.validate.PolymorphicValidator.run_pom_validate", return_value=ok), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value=ok):
        return await run_attempt(state, ctx)


STATIC = ("static_rule_violation", ("vacuous_test_assertion",))
RUN_FAILURE = ("run_verification", ("some failure",))


def test_the_prose_heuristic_itself_is_unchanged_and_still_flags_the_recorded_pom_alone(attempt6):
    """MEASURED baseline: judged against pom.xml alone, the coordinate-notation diagnosis does not match. D3 does not change
    this heuristic; it stops it from pre-empting the deterministic gate for a static-triggered retry."""
    verdict = find_edits_ignoring_own_diagnosis(attempt6["analysis"], None, attempt6["new_pom"], attempt6["old_pom"])
    assert verdict is not None and "junit:junit:4.13.2" in verdict


@pytest.mark.asyncio
async def test_recorded_attempt6_the_authoritative_finding_is_reported_not_a_diagnosis_mismatch_about_pom(tmp_path, attempt6):
    """ORIGINAL SYMPTOM at the real call site (fails on d8c2986 with type == diagnosis_mismatch on pom.xml). The candidate's test
    still holds a nested constant-truth assertion, so the correct terminal finding is the static rule, about AppTest.java."""
    state, ctx = _setup(tmp_path, attempt6, STATIC)

    with pytest.raises(QualityGateFailure) as exc_info:
        await _run(state, ctx)

    failure = exc_info.value.failure
    assert failure.type == "static_rule_violation" and "vacuous_test_assertion" in failure.message
    assert failure.authoritative_files == [TEST_FILE]                       # and D2: the validator-owned subject
    assert not any(outcome["type"] == "diagnosis_mismatch" for outcome in state.gate_outcomes)
    assert state.budgets.diagnosis_mismatch_veto_counts == {}               # the once-per-file veto budget was not spent


@pytest.mark.asyncio
async def test_a_candidate_that_resolves_the_static_finding_is_accepted_despite_a_notation_only_analysis(tmp_path, attempt6):
    """The valid case the false positive would have discarded: same recorded pom + analysis, test now genuinely asserts."""
    state, ctx = _setup(tmp_path, attempt6, STATIC, new_test=REAL_TEST)

    await _run(state, ctx)                                                   # must not raise

    assert (tmp_path / TEST_FILE).read_text() == REAL_TEST
    assert not any(outcome["type"] == "diagnosis_mismatch" for outcome in state.gate_outcomes)


@pytest.mark.asyncio
async def test_a_retry_not_responding_to_a_deterministic_gate_still_gets_the_prose_veto(tmp_path, attempt6):
    """Not weakened globally: the same response, answering a failure with no deterministic re-check, is still vetoed."""
    state, ctx = _setup(tmp_path, attempt6, RUN_FAILURE)

    with pytest.raises(QualityGateFailure) as exc_info:
        await _run(state, ctx)

    assert exc_info.value.failure.type == "diagnosis_mismatch"
    assert exc_info.value.failure.failed_content.keys() == {POM}


@pytest.mark.parametrize(("signature", "deferred"), [
    (STATIC, True), (("compile", ("x",)), True), (("pom_semantic_validation", ("x",)), True), (("targeted_test", ("x",)), True),
    (RUN_FAILURE, False), (("regression_test", ("x",)), False), (("goal_spec_compliance", ("x",)), False), (None, False),
])
def test_which_retries_defer_to_their_deterministic_gate(tmp_path, signature, deferred):
    from test_workflow import _minimal_attempt_ctx  # noqa: PLC0415

    state = GenerationState()
    state.budgets.last_failure_signature = signature
    reason = _diagnosis_mismatch_bypass_reason(state, _minimal_attempt_ctx(tmp_path), POM, "<project/>")
    assert (reason is not None) is deferred


def test_the_once_per_file_veto_budget_is_unchanged(tmp_path):
    from test_workflow import _minimal_attempt_ctx  # noqa: PLC0415

    state = GenerationState()
    state.budgets.last_failure_signature = RUN_FAILURE
    state.budgets.diagnosis_mismatch_veto_counts[POM] = 1
    assert _diagnosis_mismatch_bypass_reason(state, _minimal_attempt_ctx(tmp_path), POM, "<project/>") is not None
