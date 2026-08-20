import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from kriya.config.config import FallbackModelConfig
from kriya.workflow.attribution import (
    AttributionResult,
    _bounded_triage_source,
    attribute_failure,
    extract_self_diagnosed_files,
    read_worktree_file,
    resolve_fallback_model,
)
from kriya.workflow.failure import Failure


def test_read_worktree_file_reads_real_content(tmp_path):
    (tmp_path / "App.java").write_text("class App {}\n", encoding="utf-8")
    assert read_worktree_file(str(tmp_path), "App.java") == "class App {}\n"


def test_read_worktree_file_returns_none_for_missing_file(tmp_path):
    assert read_worktree_file(str(tmp_path), "DoesNotExist.java") is None


def test_bounded_triage_source_honors_even_a_tiny_budget():
    content = "abcdefghijklmnopqrstuvwxyz"
    excerpt = _bounded_triage_source(content, 10)
    assert excerpt == "abcdefghij"
    assert len(excerpt) <= 10


# --- resolve_fallback_model(): the shared model-escalation formula, in isolation ---

def test_resolve_fallback_model_returns_none_on_first_attempt():
    # retry_count == 0 means "still on the primary model" - matches
    # attempt.py's own `if state.budgets.retry_count > 0` guard exactly.
    chain = [FallbackModelConfig(model="fallback-a"), FallbackModelConfig(model="fallback-b")]
    assert resolve_fallback_model(0, chain) is None


def test_resolve_fallback_model_returns_none_with_empty_chain():
    assert resolve_fallback_model(3, []) is None


def test_resolve_fallback_model_escalates_one_step_per_retry():
    chain = [FallbackModelConfig(model="fallback-a"), FallbackModelConfig(model="fallback-b")]
    assert resolve_fallback_model(1, chain).model == "fallback-a"
    assert resolve_fallback_model(2, chain).model == "fallback-b"


def test_resolve_fallback_model_clamps_to_last_entry():
    # More retries than chain entries - stay on the last (most-escalated)
    # model rather than indexing out of range, matching attempt.py's own
    # `min(retry_count - 1, len(chain) - 1)` clamp.
    chain = [FallbackModelConfig(model="fallback-a"), FallbackModelConfig(model="fallback-b")]
    assert resolve_fallback_model(5, chain).model == "fallback-b"


# --- attribute_failure(): existing-evidence tiers (locator/judge), no LLM involved ---

@pytest.mark.asyncio
async def test_attribution_prefers_precise_locator_over_llm_judge_files():
    # A real javac-style file:[line,col] locator should win "locator"/high
    # confidence even when likely_files (a weaker, judge-provided signal)
    # names a DIFFERENT file - the precise locator is stronger evidence.
    failure = Failure(
        type="compile",
        message="compile failed",
        raw_output="src/main/java/App.java:[10,5] cannot find symbol",
        likely_files=["OtherFile.java"],
    )
    llm = MagicMock()
    result = await attribute_failure(
        failure, ["src/main/java/App.java", "OtherFile.java"], 0, [], llm, lambda fp: None,
    )
    assert result == AttributionResult(
        tier="locator", files=["src/main/java/App.java"], confidence="high",
        reasoning="Precise file:line locator found in the failure output.",
    )
    llm.complete.assert_not_called()


@pytest.mark.asyncio
async def test_attribution_trusts_judge_provided_likely_files_when_no_locator():
    # RunVerifierAgent.grade()'s own already-validated likely_files, or an
    # anchored-edit's known filepath - no precise line locator in the text,
    # but a real upstream signal exists. Should NOT fall through to triage.
    failure = Failure(
        type="run_verification", message="verification failed",
        raw_output="the app printed the wrong value", likely_files=["Server.java"],
    )
    llm = MagicMock()
    result = await attribute_failure(failure, ["Server.java", "Other.java"], 0, [], llm, lambda fp: None)
    assert result.tier == "judge"
    assert result.files == ["Server.java"]
    assert result.confidence == "medium"
    llm.complete.assert_not_called()


@pytest.mark.asyncio
async def test_attribution_falls_back_to_substring_scan_when_likely_files_empty():
    # The general_error case: failure.likely_files was never populated (no
    # Failure went through _build_quality_gate_failure()'s construction),
    # but a real filename still appears in the raw error text.
    failure = Failure(type="general_error", message="boom", raw_output="Error in App.java: boom")
    llm = MagicMock()
    result = await attribute_failure(failure, ["App.java", "Other.java"], 0, [], llm, lambda fp: None)
    assert result.tier == "judge"
    assert result.files == ["App.java"]
    llm.complete.assert_not_called()


# --- Golden regression fixture: the real, live-captured ignite_qpid_protocol failure ---

# Exact known_files list and captured raw_output from the real batch run that
# motivated this module (spikes/eval_harness/runs/20260812-222120/logs/
# ignite_qpid_protocol.stdout.log) - a deterministic verification-contract
# FAIL, no precise locator anywhere, "pom.xml" appearing only inside Maven's
# own "[INFO]   from pom.xml" banner (already correctly stripped by
# extract_implicated_files() as of c9140a6 - confirmed this fixture's
# raw_output produces likely_files=[] via that path, matching the real run).
IGNITE_QPID_PROTOCOL_KNOWN_FILES = [
    "pom.xml",
    "src/main/resources/applicationContext.xml",
    "src/main/resources/qpid-initial-config.json",
    "src/main/resources/system.properties",
    "src/main/java/com/example/protocol/Protocol.java",
    "src/main/java/com/example/protocol/ProtocolParser.java",
    "src/main/java/com/example/BrokerServer.java",
    "src/main/java/com/example/ProtocolProcessor.java",
    "src/main/resources/ignite-config.xml",
]

IGNITE_QPID_PROTOCOL_RAW_OUTPUT = """RUNTIME VERIFICATION FAILURE: Deterministic verification contract: the generated program's own entrypoint printed "[VERIFICATION] FAIL" - Data mismatch after round-trip.

Captured output:
=== Step 1/1: mvn -e exec:exec ===
[INFO] Error stacktraces are turned on.
[INFO] Scanning for projects...
[INFO]
[INFO] ------------------< com.example:ignite-qpid-protocol >------------------
[INFO] Building ignite-qpid-protocol 1.0-SNAPSHOT
[INFO]   from pom.xml
[INFO] --------------------------------[ jar ]---------------------------------
[Broker] BRK-1004 : Qpid Broker Ready
[22:33:40] Ignite node started OK (id=442fb3ce, instance name=ignite-server-node)
[RESULT] protocolVersion: 1
[RESULT] softwareVersion: 2
[RESULT] dataLength: 40
[RESULT] time: 4142792578
[RESULT] body length: 40
[RESULT] body content: This is a test message body for protocol
[VERIFICATION] FAIL: Data mismatch after round-trip
[22:33:40] Ignite node stopped OK [name=ignite-server-node, uptime=00:00:00.583]
[INFO] ------------------------------------------------------------------------
[INFO] BUILD SUCCESS
[INFO] ------------------------------------------------------------------------
[INFO] Total time:  14.481 s
[INFO] Finished at: 2026-08-12T22:33:42+05:30
[INFO] ------------------------------------------------------------------------
"""


@pytest.mark.asyncio
async def test_ignite_qpid_protocol_regression_no_evidence_falls_to_triage():
    """Regression test for the real, live-reproduced failure that motivated
    this module (2026-08-12/13, spikes/eval_harness/runs/20260812-222120).
    Confirms the existing-evidence tiers correctly find NOTHING for this
    exact captured output (matching the real run: likely_files=[] from
    verification_contract.py by design, and extract_implicated_files()
    correctly stripping the "[INFO]   from pom.xml" banner post-c9140a6) -
    i.e. triage is reached at all, not skipped."""
    failure = Failure(
        type="run_verification", message="RUNTIME VERIFICATION FAILURE",
        raw_output=IGNITE_QPID_PROTOCOL_RAW_OUTPUT, likely_files=[],
    )
    llm = MagicMock()
    llm.complete = AsyncMock(return_value='{"files": [], "confidence": "low", "reasoning": "no evidence"}')
    result = await attribute_failure(
        failure, IGNITE_QPID_PROTOCOL_KNOWN_FILES, 1, [], llm, lambda fp: None,
    )
    llm.complete.assert_called_once()
    assert result.tier == "full_set"
    assert result.files == []


@pytest.mark.asyncio
async def test_ignite_qpid_protocol_regression_triage_identifies_real_culprit():
    """The fix: with a real (mocked) triage response naming ProtocolParser.java
    - matching what the model's own fix-analysis text ALREADY correctly said
    in the real run ("the ProtocolParser decode method incorrectly handles
    the 3-byte dataLength field") - attribute_failure() now returns that file
    directly, instead of the real run's actual outcome (pom.xml, then
    BrokerServer.java, via an unordered full-set walk that never reached
    ProtocolParser.java before the 1200s timeout). Confirmed failing pre-fix:
    before this module existed, retry_strategy.py's `failure.likely_files or
    extract_implicated_files(...)` had no triage tier at all and would have
    returned [] here, same as the real run."""
    failure = Failure(
        type="run_verification", message="RUNTIME VERIFICATION FAILURE",
        raw_output=IGNITE_QPID_PROTOCOL_RAW_OUTPUT, likely_files=[],
    )
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=(
        '{"files": ["src/main/java/com/example/protocol/ProtocolParser.java"], '
        '"confidence": "high", "reasoning": "The failure is a data round-trip '
        'mismatch, which points at the encode/decode logic."}'
    ))
    contents = {
        fp: f"// skeleton for {fp}\nclass Placeholder {{}}\n" for fp in IGNITE_QPID_PROTOCOL_KNOWN_FILES
    }
    result = await attribute_failure(
        failure, IGNITE_QPID_PROTOCOL_KNOWN_FILES, 1, [], llm,
        lambda fp: contents.get(fp),
    )
    assert result.tier == "triage"
    assert result.files == ["src/main/java/com/example/protocol/ProtocolParser.java"]
    assert result.confidence == "high"


@pytest.mark.asyncio
async def test_triage_sees_java_method_bodies_needed_to_localize_runtime_hangs():
    """Regression for demo1: the former signatures-only triage prompt hid
    Thread.currentThread().join(), so the classifier saw Spring/Ignite names
    but not the concrete lifecycle defect and guessed applicationContext.xml.
    Triage must receive bounded implementation evidence, including method
    bodies, while still making only the same single classification call."""
    application = (
        "package com.example;\n"
        "public class Application {\n"
        "  public static void main(String[] args) {\n"
        "    try (var context = startContext()) {\n"
        "      Thread.currentThread().join();\n"
        "    }\n"
        "  }\n"
        "}\n"
    )
    xml = '<beans><bean id="ignite" class="com.example.IgniteService"/></beans>\n'
    failure = Failure(
        type="run_verification_hung",
        message="The application produced the expected output but did not exit before timeout.",
        raw_output="Ignite node started and verification passed; process timed out after 90 seconds.",
        likely_files=[],
    )
    llm = MagicMock()

    async def classify(system_prompt, user_prompt, **kwargs):
        assert "Thread.currentThread().join();" in user_prompt
        assert "Candidate source excerpts" in user_prompt
        assert "short skeleton" not in system_prompt
        return (
            '{"files": ["src/main/java/com/example/Application.java"], '
            '"confidence": "high", "reasoning": "The main thread joins itself, '
            'so the try-with-resources context can never close."}'
        )

    llm.complete = AsyncMock(side_effect=classify)
    contents = {
        "src/main/java/com/example/Application.java": application,
        "src/main/resources/applicationContext.xml": xml,
    }
    result = await attribute_failure(
        failure, list(contents), 0, [], llm, contents.get,
    )

    assert result.tier == "triage"
    assert result.files == ["src/main/java/com/example/Application.java"]


@pytest.mark.asyncio
async def test_rejected_target_without_alternate_widens_without_retriage_call():
    failure = Failure(
        type="attribution_rejected",
        message="Developer reported NO CHANGE NEEDED for applicationContext.xml",
        raw_output="applicationContext.xml is already correct",
        likely_files=[],
    )
    llm = MagicMock()
    llm.complete = AsyncMock()

    result = await attribute_failure(
        failure, ["Application.java", "applicationContext.xml"], 0, [], llm,
        lambda fp: "content",
    )

    assert result.tier == "full_set"
    assert result.files == []
    assert "rejected" in result.reasoning
    llm.complete.assert_not_called()


# --- Triage tier: model-escalation ladder, and graceful fallback on a bad response ---

@pytest.mark.asyncio
async def test_triage_rides_the_same_escalation_ladder_as_generation():
    # Per the user's explicit design constraint: triage must NOT invent its
    # own "always use the fast primary model" policy - it should run on
    # whatever model resolve_fallback_model() says THIS retry attempt is on,
    # exactly like the full-set generation call.
    fallback = FallbackModelConfig(model="devstral-small-2:24b", base_url="http://localhost:11434/v1", api_key="local-key")
    failure = Failure(type="run_verification", message="fail", raw_output="no locator here", likely_files=[])
    llm = MagicMock()
    llm.complete = AsyncMock(return_value='{"files": ["A.java"], "confidence": "medium", "reasoning": "x"}')

    await attribute_failure(failure, ["A.java", "B.java"], 2, [fallback], llm, lambda fp: "content")

    _, kwargs = llm.complete.call_args
    assert kwargs["model_override"] == "devstral-small-2:24b"
    assert kwargs["base_url_override"] == "http://localhost:11434/v1"


@pytest.mark.asyncio
async def test_triage_falls_back_to_full_set_on_malformed_response():
    failure = Failure(type="run_verification", message="fail", raw_output="no locator here", likely_files=[])
    llm = MagicMock()
    llm.complete = AsyncMock(return_value="not valid json")
    result = await attribute_failure(failure, ["A.java"], 0, [], llm, lambda fp: "content")
    assert result.tier == "full_set"
    assert result.files == []
    assert result.confidence == "low"


@pytest.mark.asyncio
async def test_triage_falls_back_to_full_set_when_llm_call_raises():
    failure = Failure(type="run_verification", message="fail", raw_output="no locator here", likely_files=[])
    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=RuntimeError("connection refused"))
    result = await attribute_failure(failure, ["A.java"], 0, [], llm, lambda fp: "content")
    assert result.tier == "full_set"


@pytest.mark.asyncio
async def test_triage_ignores_files_the_model_hallucinates_outside_known_files():
    failure = Failure(type="run_verification", message="fail", raw_output="no locator here", likely_files=[])
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=(
        '{"files": ["NotARealFile.java"], "confidence": "high", "reasoning": "x"}'
    ))
    result = await attribute_failure(failure, ["A.java", "B.java"], 0, [], llm, lambda fp: "content")
    # The hallucinated file gets filtered out, leaving nothing - falls
    # through to the honest full_set result rather than trusting an
    # unknown filepath.
    assert result.tier == "full_set"
    assert result.files == []


# --- extract_self_diagnosed_files(): reading the model's own analysis text ---

def test_extract_self_diagnosed_files_finds_a_different_named_file():
    files = [{
        "filepath": "ProtocolApp.java",
        "content": None,
        "analysis": "the fix belongs entirely in ProtocolParser.java, not here.",
    }]
    result = extract_self_diagnosed_files(files, ["ProtocolApp.java", "ProtocolParser.java", "pom.xml"])
    assert result == ["ProtocolParser.java"]


def test_extract_self_diagnosed_files_excludes_self_mention():
    # The analysis for ProtocolApp.java happening to also say "ProtocolApp.java"
    # isn't signal - only a mention of a DIFFERENT known file counts.
    files = [{
        "filepath": "ProtocolApp.java",
        "content": None,
        "analysis": "ProtocolApp.java itself looks correct, the real issue is in Config.json.",
    }]
    result = extract_self_diagnosed_files(files, ["ProtocolApp.java", "Config.json"])
    assert result == ["Config.json"]


def test_extract_self_diagnosed_files_unions_across_multiple_files():
    files = [
        {"filepath": "A.java", "content": None, "analysis": "the real cause is in B.java."},
        {"filepath": "C.java", "content": None, "analysis": "actually this needs a change in D.java too."},
    ]
    result = extract_self_diagnosed_files(files, ["A.java", "B.java", "C.java", "D.java"])
    assert set(result) == {"B.java", "D.java"}


def test_extract_self_diagnosed_files_handles_missing_or_empty_analysis():
    files = [
        {"filepath": "A.java", "content": "class A {}"},
        {"filepath": "B.java", "content": None, "analysis": ""},
        {"filepath": "C.java", "content": None, "edits": [{"search": "x", "replace": "y"}]},
    ]
    assert extract_self_diagnosed_files(files, ["A.java", "B.java", "C.java"]) == []


def test_extract_self_diagnosed_files_no_divergence_returns_empty():
    # The analysis doesn't name any OTHER known file - nothing to redirect to.
    files = [{"filepath": "A.java", "content": None, "analysis": "the null check was missing here."}]
    assert extract_self_diagnosed_files(files, ["A.java", "B.java"]) == []


# --- attribute_failure(): self_diagnosis tier outranks a fresh locator ---

@pytest.mark.asyncio
async def test_self_diagnosis_outranks_a_fresh_locator_match():
    # Even though the failure text carries a precise, high-confidence
    # locator for ProtocolApp.java, a passed-in (already-gated by the
    # caller) self-diagnosis pointing at a different file must win - a
    # locator that already led to one failed fix on this exact repeat is
    # weaker evidence than the model's own stated disagreement with it.
    failure = Failure(
        type="run_verification", message="fail",
        raw_output="Exception at (ProtocolApp.java:51): connection failed",
    )
    llm = MagicMock()
    result = await attribute_failure(
        failure, ["ProtocolApp.java", "qpid-initial-config.json"], 2, [], llm, lambda fp: None,
        self_diagnosed_files=["qpid-initial-config.json"],
    )
    assert result == AttributionResult(
        tier="self_diagnosis", files=["qpid-initial-config.json"], confidence="high",
        reasoning="The model's own FIX ANALYSIS from the immediately-preceding attempt "
        "(responding to this exact same failure recurring) named a different file as the "
        "real cause.",
    )
    llm.complete.assert_not_called()


@pytest.mark.asyncio
async def test_no_self_diagnosis_falls_through_to_locator_as_before():
    failure = Failure(
        type="run_verification", message="fail",
        raw_output="Exception at (ProtocolApp.java:51): connection failed",
    )
    llm = MagicMock()
    result = await attribute_failure(
        failure, ["ProtocolApp.java", "qpid-initial-config.json"], 2, [], llm, lambda fp: None,
        self_diagnosed_files=None,
    )
    assert result.tier == "locator"
    assert result.files == ["ProtocolApp.java"]


# --- Golden regression fixture: the real captured validation-2 divergence ---

# Exact text from the real live-captured attempt 4 fix-analysis
# (spikes/eval_harness/runs/attribution-fix-validation-2/logs/
# ignite_qpid_protocol.stdout.log), attached to ProtocolApp.java - correctly
# names the real cause (qpid-initial-config.json's missing defaultAlias
# entry, already documented in skills/qpid/rules.txt) even though the
# stack-trace locator in the SAME failure's raw_output points at
# ProtocolApp.java, where the JMS connection exception was thrown, not
# where the misconfiguration lives.
VALIDATION_2_FIX_ANALYSIS = (
    "The error shows \"Unknown hostname in connection open: 'localhost'\" which "
    "indicates the Qpid broker's AMQP port configuration is not properly set up to "
    "accept connections from localhost. Looking at the qpid-initial-config.json, I "
    "see that the virtualhostaliases are missing the required defaultAlias entry and "
    "the port configuration doesn't have proper hostname resolution setup. The issue "
    "is in the broker configuration - specifically the virtualhostaliases section "
    "needs to include a \"defaultAlias\" type entry for the connection to work "
    "properly with localhost."
)

VALIDATION_2_KNOWN_FILES = [
    "pom.xml",
    "src/main/resources/qpid-initial-config.json",
    "src/main/java/com/example/ProtocolApp.java",
]

VALIDATION_2_RAW_OUTPUT = (
    "org.apache.qpid.jms.JmsResourceNotFoundException: Unknown hostname in "
    "connection open: 'localhost' [condition = amqp:not-found]\n"
    "\tat org.apache.qpid.jms.JmsConnection.start(JmsConnection.java:359)\n"
    "\tat com.example.ProtocolApp.main(ProtocolApp.java:51)\n\n"
    "[Grader reasoning]: The program output shows a JMS connection error "
    "('Unknown hostname in connection open: 'localhost'') and no protocol details "
    "or verification result were printed, indicating the round-trip process failed "
    "before completing the verification step."
)


def test_validation_2_regression_extracts_qpid_config_from_real_analysis_text():
    files = [{
        "filepath": "src/main/java/com/example/ProtocolApp.java",
        "content": None,
        "analysis": VALIDATION_2_FIX_ANALYSIS,
    }]
    result = extract_self_diagnosed_files(files, VALIDATION_2_KNOWN_FILES)
    assert result == ["src/main/resources/qpid-initial-config.json"]


@pytest.mark.asyncio
async def test_validation_2_regression_attribution_redirects_away_from_the_throw_site():
    """Regression test for the real, live-reproduced divergence found
    2026-08-13 (spikes/eval_harness/runs/attribution-fix-validation-2):
    without self-diagnosis feedback, attribute_failure() would have returned
    tier="locator", files=["ProtocolApp.java"] here (exactly what the real
    run did, 4 attempts in a row) - the file where the exception was
    thrown, not where the misconfiguration lives. With the model's own
    analysis fed back in (as retry_strategy.py now does, gated on a
    confirmed same-signature repeat), it correctly redirects to
    qpid-initial-config.json instead."""
    failure = Failure(
        type="run_verification", message="RUNTIME VERIFICATION FAILURE",
        raw_output=VALIDATION_2_RAW_OUTPUT, likely_files=[],
    )
    prior_files = [{
        "filepath": "src/main/java/com/example/ProtocolApp.java",
        "content": None,
        "analysis": VALIDATION_2_FIX_ANALYSIS,
    }]
    self_diagnosed = extract_self_diagnosed_files(prior_files, VALIDATION_2_KNOWN_FILES)

    llm = MagicMock()
    result = await attribute_failure(
        failure, VALIDATION_2_KNOWN_FILES, 2, [], llm, lambda fp: None,
        self_diagnosed_files=self_diagnosed,
    )
    assert result.tier == "self_diagnosis"
    assert result.files == ["src/main/resources/qpid-initial-config.json"]
    llm.complete.assert_not_called()
