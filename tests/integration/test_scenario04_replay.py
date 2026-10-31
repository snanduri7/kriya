"""Scenario-04 characterization gates (full modularization, slice A3;
handover/MODULARIZATION/TEST-MATRIX.md §3, SCENARIO-04-ARCHITECTURAL-INPUT.md).

Deterministic, model-free replays of live run 20261016T094912-e7b4804c
(Spring XML + Apache Ignite + Protocol, generation/repair failure). The
recorded inputs go through Kriya's REAL functions: the self-diagnosis
extractor, the Developer response parser, the one edit engine, ``run_attempt``
and the generation-budget admission check. These tests PIN TODAY'S
BEHAVIOUR, including the wrong decisions. An assertion that a correction
slice must flip carries ``# CHARACTERIZATION: flips in <slice>``; the slice
rewrites that expectation and shows the pre/post diff of the same test.

Fixtures: ``tests/fixtures/scenario04/`` (copied once from
``handover/SPRING-IGNITE-PROTOCOL/evidence/live-failure-20261016T094912-e7b4804c/``;
the suite never reads ``handover/``). ``MANIFEST.json`` names each fixture's
source file, the source's sha256 and the content sha256, checked by
``test_fixtures_are_the_recorded_evidence``. Request texts are gzip-compressed
verbatim; the log fixture keeps only the lines the tests read
(budget_timeline.py's patterns, the budget lines, the attempt 2-4 refusals).

MEASURED expectations (A3 gate, the three evidence scripts run with
``.venv/bin/python`` at af8a818, javac 17.0.10 on PATH; outputs identical to
the recorded ``replay-*.txt``/``budget-timeline.txt``, modulo temp paths):

Verbatim script outputs (temp paths normalized to <tmp>):

replay_attribution.py:
    {
     "recorded_analyses_attached_to_ProtocolParser": {
      "095-developer.parse-callNone-analysis_model_claimed.txt": [
       "src/main/java/com/example/protocol/Protocol.java"
      ],
      "124-developer.parse-callNone-analysis_model_claimed.txt": [
       "src/main/java/com/example/protocol/Protocol.java"
      ],
      "130-developer.parse-callNone-analysis_model_claimed.txt": [
       "src/main/java/com/example/protocol/Protocol.java"
      ],
      "161-developer.parse-callNone-analysis_model_claimed.txt": [
       "src/main/java/com/example/protocol/Protocol.java"
      ],
      "167-developer.parse-callNone-analysis_model_claimed.txt": [
       "src/main/java/com/example/protocol/Protocol.java"
      ]
     },
     "controls": {
      "analysis that only QUOTES the compiler message (names the class Protocol, says to use getters in the parser)": [
       "src/main/java/com/example/protocol/Protocol.java"
      ],
      "analysis that names no other file": [],
      "analysis that really redirects ('the defect is in Protocol.java')": [
       "src/main/java/com/example/protocol/Protocol.java"
      ]
     }
    }

replay_generation_failure.py:
    {
     "baseline_javac_errors_of_attempt1_parser": [
      "<tmp>/com/example/protocol/ProtocolParser.java:10: error: body has private access in Protocol",
      "<tmp>/com/example/protocol/ProtocolParser.java:15: error: protocolVersion has private access in Protocol",
      "<tmp>/com/example/protocol/ProtocolParser.java:16: error: softwareVersion has private access in Protocol",
      "<tmp>/com/example/protocol/ProtocolParser.java:17: error: dataLength has private access in Protocol",
      "<tmp>/com/example/protocol/ProtocolParser.java:18: error: time has private access in Protocol",
      "<tmp>/com/example/protocol/ProtocolParser.java:21: error: body has private access in Protocol"
     ],
     "attempt2_call8_parser_edit": {
      "call": 8,
      "path": "src/main/java/com/example/protocol/ProtocolParser.java",
      "parse_kind": "edits",
      "parse_reason_code": null,
      "edit_engine": "REFUSED ANCHOR_NOT_IN_FILE: Anchor matching failed for edit #1: the search block occurs neither in the source shown to the model nor in the current file (fabricated or stale)."
     },
     "attempt3_call9_protocol_response": {
      "call": 9,
      "path": "src/main/java/com/example/protocol/Protocol.java",
      "parse_kind": "invalid",
      "parse_reason_code": "INVALID_EDIT_PROTOCOL"
     },
     "attempt3_call10_parser_edit": {
      "call": 10,
      "path": "src/main/java/com/example/protocol/ProtocolParser.java",
      "parse_kind": "edits",
      "parse_reason_code": null,
      "edit_engine": "APPLIED",
      "javac_errors_after": []
     },
     "attempt4_call11_protocol_edit": {
      "call": 11,
      "path": "src/main/java/com/example/protocol/Protocol.java",
      "parse_kind": "edits",
      "parse_reason_code": null,
      "edit_engine": "REFUSED INDENTATION_STYLE_MISMATCH: edit #1: the SEARCH block matches only when indentation is ignored, and its indentation cannot be mapped onto the file's by one consistent shift (tabs vs spaces?)"
     },
     "attempt4_call12_parser_edit": {
      "call": 12,
      "path": "src/main/java/com/example/protocol/ProtocolParser.java",
      "parse_kind": "edits",
      "parse_reason_code": null,
      "edit_engine": "APPLIED",
      "javac_errors_after": []
     }
    }

budget_timeline.py:
    budget clock starts at the first model call (09:49:13.857000); the fallback pass was refused at 10:00:26.847000: 673.0 s consumed of 900 s
         77.9 s  planning (2 planner calls, repair of a schema-invalid first plan)
        129.6 s  attempt 1 Developer: 4 files, one model call each
         20.2 s  attempt 1 reviewer + static checks (before the human gate)
        128.5 s  HUMAN source review wait (owner reading and approving)
          6.3 s  attempt 1 gates + attribution
         49.5 s  attempt 2 (retry 1/3: ProtocolParser only)
         85.6 s  attempt 3 (retry 2/3: Protocol + ProtocolParser)
        174.0 s  attempt 4 (retry 3/3: Protocol + ProtocolParser)
      sum of rows 671.6 s; unattributed gaps 1.4 s
    remaining 227.0 s vs required 300.0 s (2 files x 90 s configured estimate for the never-used fallback model + 120 s gate reserve)
    if the human wait were not counted: remaining would have been 355.5 s -> the fallback pass would have started

Kriya's own refusal message says 226.8 s remaining, the timeline 227.0 s: the 0.2 s gap is MEASURED
(log timestamps vs the monotonic check); its cause is INFERRED (the check runs after the "Trying ONE
targeted fix" log line, inside _run_developer_generation, once the request is assembled). Both are pinned.

run_attempt (atomic refusal): tests/state_machine/_scripted_run.py cannot
drive it: that harness plans ONE ``target``, answers every Developer call by
wrapping content in a sentinel block for that target (it can neither return a
recorded raw response nor answer two files differently) and has no compile
gate, so the recorded attempt-1 -> attempt-3 retarget cannot be reached
through it. The same behaviour is pinned one level down, through the real
``run_attempt`` with a real ``DeveloperAgent`` whose only scripted part is
the per-file completion (it returns the recorded raw responses), in the style
of tests/test_d1_operation_mode_authority.py::TestCriticalPropagation.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest
from _strict_doubles import strict_config, strict_kernel

from kriya.agents.agent import DeveloperAgent
from kriya.agents.response_protocol import parse_structured
from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow import attempt as attempt_module
from kriya.workflow.attempt import AttemptContext, QualityGateFailure, run_attempt
from kriya.workflow.attribution import extract_self_diagnosed_files
from kriya.workflow.edit_safety import apply_anchored_edits
from kriya.workflow.failure import Failure, FileLocation
from kriya.workflow.file_integrity import FileIntegrityError
from kriya.workflow.state import GenerationState

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "scenario04"
PROTOCOL = "src/main/java/com/example/protocol/Protocol.java"
PARSER = "src/main/java/com/example/protocol/ProtocolParser.java"
KNOWN = [PROTOCOL, PARSER, "pom.xml", "src/main/resources/applicationContext.xml"]  # the run's 4 files
PRIMARY = "qwen3-coder:30b"
FALLBACK = "qwen3.6:35b-a3b-q4_K_M"
RECORDED_ANALYSES = ("095", "124", "130", "161", "167")


def fixture_text(name: str) -> str:
    raw = (FIXTURES / name).read_bytes()
    return (gzip.decompress(raw) if name.endswith(".gz") else raw).decode("utf-8")


def request_text(call: int) -> str:
    """The source shown to the model: every message of the recorded request."""
    return "\n".join(message["content"] for message in json.loads(fixture_text(f"request-call{call:02d}-messages.json.gz")))


def staged_attempt1() -> Dict[str, str]:
    return {PROTOCOL: fixture_text("attempt1-Protocol.java"), PARSER: fixture_text("attempt1-ProtocolParser.java")}


def test_fixtures_are_the_recorded_evidence():
    manifest = json.loads((FIXTURES / "MANIFEST.json").read_text(encoding="utf-8"))
    on_disk = sorted(path.name for path in FIXTURES.iterdir() if path.name != "MANIFEST.json")
    assert on_disk == sorted(manifest["files"])
    for name, entry in manifest["files"].items():
        raw = (FIXTURES / name).read_bytes()
        content = gzip.decompress(raw) if name.endswith(".gz") else raw
        assert hashlib.sha256(content).hexdigest() == entry["content_sha256"], name
        assert entry["source"].startswith(manifest["evidence_dir"]) and len(entry["source_sha256"]) == 64


# ----------------------------------------------------------- attribution

def test_attribution_characterization():
    """Recorded Developer analyses (attached to the parser, as in the run)
    through the real ``extract_self_diagnosed_files``. W3-4 FLIPPED this
    (was: ``== [PROTOCOL]`` for all five recorded analyses and the quoted
    compiler message, SCENARIO-04 §2 first wrong decision): prose that merely
    mentions Protocol is advisory evidence and no longer retargets the
    repair; only the typed REDIRECT signal does."""
    for number in RECORDED_ANALYSES:
        analysis = fixture_text(f"analysis-{number}.txt")
        found = extract_self_diagnosed_files([{"filepath": PARSER, "analysis": analysis}], KNOWN)
        assert found == [], number  # the recorded prose no longer redirects
        # the same recorded prose WITH an explicit typed redirect still does (the intended behaviour)
        typed = extract_self_diagnosed_files(
            [{"filepath": PARSER, "analysis": analysis, "redirect_to": [PROTOCOL]}], KNOWN)
        assert typed == [PROTOCOL], number

    quoted = ("The compiler says: body has private access in Protocol. "
              "The parser must use getBody() instead of the field.")
    assert extract_self_diagnosed_files([{"filepath": PARSER, "analysis": quoted}], KNOWN) == []
    assert extract_self_diagnosed_files(
        [{"filepath": PARSER, "analysis": "Use the getter methods instead of direct field access."}], KNOWN) == []
    redirect = "The real defect is in Protocol.java: its fields need accessors."
    assert extract_self_diagnosed_files([{"filepath": PARSER, "analysis": redirect}], KNOWN) == []
    assert extract_self_diagnosed_files(
        [{"filepath": PARSER, "analysis": redirect, "redirect_to": [PROTOCOL]}], KNOWN) == [PROTOCOL]


# ----------------------------------------------------- candidate transaction

def _classify(call: int, path: str) -> Dict[str, object]:
    """``replay_generation_failure.classify``: parse, then the one edit engine
    against the staged attempt-1 file and the source shown to the model."""
    parsed = parse_structured(fixture_text(f"response-call{call:02d}.txt"), path)
    report: Dict[str, object] = {"kind": parsed.kind, "reason_code": parsed.reason_code}
    if parsed.edits:
        pairs = [{"search": search, "replace": replace} for search, replace in parsed.edits]
        try:
            report["patched"] = apply_anchored_edits(staged_attempt1()[path], pairs, request_text(call))
            report["edit_engine"] = "APPLIED"
        except FileIntegrityError as error:
            report["edit_engine"] = f"REFUSED {error.reason_code}"
    return report


def _attempt_context(tmp_path: Path, responses: Dict[str, int]) -> AttemptContext:
    from kriya.workflow.migration import resolve_migration_resolution

    for path, content in staged_attempt1().items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    # W3-3: the REAL workspace of a run is a Git work tree by the time the membership guards run (``create_git_worktree``).
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    config = AppConfig()
    config.llm.model = PRIMARY
    developer = DeveloperAgent("developer", LLMClient(config))
    recorded = {path: fixture_text(f"response-call{call:02d}.txt") for path, call in responses.items()}
    asked: List[str] = []

    async def complete_file(system_prompt: str, prompt: str, **_options) -> str:
        named = re.search(r"for '([^']+)' ONLY", prompt)  # the per-file request's single-target sentence
        path = named.group(1) if named else f"<no single-file target in a {len(system_prompt)}-char request>"
        asked.append(path)
        return recorded[path]

    developer._complete_file = complete_file  # the ONLY scripted part: the per-file completion
    goal = "Spring XML + Apache Ignite + Protocol (scenario 04)"
    ctx = AttemptContext(
        goal=goal, plan="Step 1: repair the parser", design="", workspace_path=str(tmp_path),
        worktree_path=str(tmp_path), architect_files=[PROTOCOL, PARSER], resume_state=None, run_id="scenario04-a3",
        skills_prompt="", learned_rag_context="", matched_files=[], related_files=[],
        ecosystem_invariant_block="", resource_lifecycle_block="", verification_contract_block="",
        recovery_contract_block="", required_files_prompt_block="", required_dependencies_prompt_block="",
        expected_files_upfront=[PROTOCOL, PARSER], architect_basename_to_path={
            "Protocol.java": PROTOCOL, "ProtocolParser.java": PARSER},
        chain=[], targeted_max_retries=3, stream_callback=None, approval_callback=None, active_skills=[],
        active_skill_rules_snapshot={}, developer=developer, run_verifier=AsyncMock(), spec_compliance=AsyncMock(),
        skill_engine=MagicMock(), kernel=Kernel(config=config), max_retries=4, web_lookup_query_callback=None,
        approve_web_lookup=AsyncMock(return_value=False),
        migration_resolution=resolve_migration_resolution(goal, str(tmp_path)),
    )
    ctx.asked = asked  # type: ignore[attr-defined]
    return ctx


def _retry_state(previous_attempt: int, previous: Failure) -> GenerationState:
    """The state a recorded retry started from: the previous attempt's failure
    and the retarget the self-diagnosis produced (``Targeted retry 2/3`` and
    ``3/3: focusing on Protocol.java, ProtocolParser.java``)."""
    state = GenerationState()
    state.attempt_number = previous_attempt
    state.last_attempt_mode = "targeted"
    state.last_implicated_files = [PROTOCOL, PARSER]
    state.all_files_written = {PROTOCOL, PARSER}
    state.error_context = previous.message
    state.last_failure = previous
    return state


def _logged_failure(prefix: str) -> str:
    """The attempt's refusal message exactly as the live run logged it."""
    line = next(line for line in fixture_text("kriya-log-lines.txt").splitlines() if line.startswith(prefix))
    return line[len(prefix):]


def _replay_attempt(tmp_path: Path, responses: Dict[str, int], previous_attempt: int, previous: Failure):
    """One recorded retry through the real ``run_attempt``."""
    ctx = _attempt_context(tmp_path, responses)
    state = _retry_state(previous_attempt, previous)
    before = {path: (tmp_path / path).read_bytes() for path in (PROTOCOL, PARSER)}
    # CHARACTERIZATION: flips in G4 (the attempt no longer fails for the invalid sibling; it then reaches the
    # real gates, so G4 must script/stub them - an unstubbed run errors once refusal is removed)
    with pytest.raises(QualityGateFailure) as refused:
        asyncio.run(run_attempt(state, ctx))
    after = {path: (tmp_path / path).read_bytes() for path in (PROTOCOL, PARSER)}
    return refused.value.failure, ctx, state, before, after


def test_transaction_characterization(tmp_path):
    """Recorded attempts 2-4 through the real parser and edit engine, then the
    recorded attempts 3 and 4 through the real ``run_attempt``: each time the
    valid parser proposal is refused together with its invalid sibling (one
    attempt = one atomic candidate), with the live run's exact message."""
    attempt2 = _classify(8, PARSER)
    assert (attempt2["kind"], attempt2["edit_engine"]) == ("edits", "REFUSED ANCHOR_NOT_IN_FILE")
    protocol3 = _classify(9, PROTOCOL)
    assert (protocol3["kind"], protocol3["reason_code"]) == ("invalid", "INVALID_EDIT_PROTOCOL")
    parser3 = _classify(10, PARSER)
    assert (parser3["kind"], parser3["edit_engine"]) == ("edits", "APPLIED")
    protocol4 = _classify(11, PROTOCOL)
    assert (protocol4["kind"], protocol4["edit_engine"]) == ("edits", "REFUSED INDENTATION_STYLE_MISMATCH")
    parser4 = _classify(12, PARSER)
    assert (parser4["kind"], parser4["edit_engine"]) == ("edits", "APPLIED")
    for patched in (parser3["patched"], parser4["patched"]):
        assert "protocol.body" not in patched and "getBody()" in patched  # the valid repair the attempt discarded

    # attempt 3: the Protocol response violates the single-file contract (operation contract, pre-write)
    failure, ctx, state, before, after = _replay_attempt(tmp_path / "attempt3", {PROTOCOL: 9, PARSER: 10}, 2, Failure(
        type="anchored_edit", message=f"ANCHORED EDIT FAILURE in {PARSER}: ANCHOR_NOT_IN_FILE",
        raw_output="ANCHOR_NOT_IN_FILE", file_locations=[FileLocation(filepath=PARSER)], likely_files=[PARSER],
        attempted_edits=[], diagnostics={"reason_code": "ANCHOR_NOT_IN_FILE"}, attempt=2))
    assert ctx.asked == [PROTOCOL, PARSER] and (state.attempt_number, state.last_attempt_mode) == (3, "targeted")
    assert failure.message == _logged_failure("targeted, full-set 1/4 + targeted 1/3: ")  # CHARACTERIZATION: flips in G4
    assert (failure.type, failure.likely_files) == ("operation_contract", [PROTOCOL])  # CHARACTERIZATION: flips in G4
    assert failure.diagnostics == {"reason_code": "INVALID_EDIT_PROTOCOL"}  # CHARACTERIZATION: flips in G4
    assert after == before  # CHARACTERIZATION: flips in G4 (the valid parser proposal is never staged)
    assert state.files_written == []  # CHARACTERIZATION: flips in G4

    # attempt 4: the Protocol edit fails in the edit engine (write loop); the parser edit is refused with it
    failure, ctx, state, before, after = _replay_attempt(tmp_path / "attempt4", {PROTOCOL: 11, PARSER: 12}, 3, Failure(
        type="operation_contract", message=_logged_failure("targeted, full-set 1/4 + targeted 1/3: "),
        raw_output="INVALID_EDIT_PROTOCOL", file_locations=[FileLocation(filepath=PROTOCOL)],
        likely_files=[PROTOCOL], attempted_edits=[], diagnostics={"reason_code": "INVALID_EDIT_PROTOCOL"},
        attempt=3))
    assert ctx.asked == [PROTOCOL, PARSER] and (state.attempt_number, state.last_attempt_mode) == (4, "targeted")
    assert failure.message == _logged_failure("targeted, full-set 1/4 + targeted 2/3: ")  # CHARACTERIZATION: flips in G4
    assert (failure.type, failure.likely_files) == ("anchored_edit", [PROTOCOL])  # CHARACTERIZATION: flips in G4
    assert failure.diagnostics == {"reason_code": "INDENTATION_STYLE_MISMATCH"}  # CHARACTERIZATION: flips in G4
    assert after == before  # CHARACTERIZATION: flips in G4 (the valid parser proposal is never staged)
    assert state.files_written == []  # CHARACTERIZATION: flips in G4


def _javac_errors(tmp_path: Path, parser_source: str) -> List[str]:
    root = tmp_path / "javac" / "com" / "example" / "protocol"
    root.mkdir(parents=True)
    (root / "Protocol.java").write_text(staged_attempt1()[PROTOCOL], encoding="utf-8")
    (root / "ProtocolParser.java").write_text(parser_source, encoding="utf-8")
    out = subprocess.run(["javac", "-d", str(tmp_path / "javac-out"), str(root / "Protocol.java"),
                          str(root / "ProtocolParser.java")], capture_output=True, text=True, check=False, timeout=120)
    return [line.split("ProtocolParser.java:", 1)[-1] for line in out.stderr.splitlines() if "error:" in line]


@pytest.mark.real_toolchain
@pytest.mark.skipif(shutil.which("javac") is None, reason="javac not on PATH (real_toolchain half of the A3 gate)")
def test_transaction_characterization_javac(tmp_path):
    """The javac half: the attempt-1 parser fails exactly as the recorded Maven
    compile did; both discarded parser repairs compile."""
    recorded = fixture_text("attempt1-compile-output.txt")
    maven = {f"{line}: error: {field} has private access in Protocol" for line, field in re.findall(
        r"ProtocolParser\.java:\[(\d+),\d+\] (\w+) has private access in com\.example\.protocol\.Protocol", recorded)}
    baseline = _javac_errors(tmp_path / "a1", staged_attempt1()[PARSER])
    assert sorted(baseline) == sorted(maven) and len(baseline) == 6  # javac reproduces the recorded Maven errors
    for number, call in (("a3", 10), ("a4", 12)):
        assert _javac_errors(tmp_path / number, _classify(call, PARSER)["patched"]) == []


# ------------------------------------------------------------------ budget

def _log_time(lines: List[str], pattern: str) -> datetime:
    line = next(line for line in lines if re.search(pattern, line))
    return datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f")


def _seconds(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds(), 1)


class _FakeTime:
    """Stands in for the ``time`` module inside kriya.workflow.attempt only."""

    def __init__(self, now: float) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


def test_budget_characterization(monkeypatch):
    """The recorded timeline through the real ``_ensure_generation_time_budget``
    on a fake clock: the run-wide clock starts at the first model call, the
    owner's source-review wait is inside it, and the never-used fallback model
    is priced from the configured 90 s/file because other models' timings
    are filtered out."""
    lines = fixture_text("kriya-log-lines.txt").splitlines()
    clock_start = _log_time(lines, r"Sending completion request to local LLM \[Model: qwen3-coder:30b")
    refused_at = _log_time(lines, r"Trying ONE targeted fix on fallback model")
    human_wait = _seconds(_log_time(lines, r"Escalating changes to human approval gate"),
                          _log_time(lines, r"Quality Gates: Running polymorphic compiler"))
    used = _seconds(clock_start, refused_at)
    assert (used, human_wait) == (673.0, 128.5)
    logged = next(line for line in lines if "GENERATION TIME BUDGET EXHAUSTED" in line)
    assert "with 226.8s remaining" in logged and "requires 300.0s" in logged

    config = strict_config(autonomy={"generation_time_budget_seconds": 900})
    assert (config.autonomy.generation_seconds_per_file_estimate, config.autonomy.generation_gate_reserve_seconds) \
        == (90, 120)
    ctx = MagicMock(spec=AttemptContext)
    ctx.kernel = strict_kernel(config)
    metrics = json.loads(fixture_text("generation-metrics.json"))["generation_metrics"]
    state = GenerationState()
    state.budget_started_monotonic = 1000.0
    # The run's recorded Developer timings (primary model only: 4 calls, 9 files).
    state.generation_timings = [{"model": PRIMARY, "duration_seconds": metrics["llm"]["developer_wall_seconds"],
                                 "file_count": metrics["files_requested"]}]
    fake = _FakeTime(1000.0 + used)
    monkeypatch.setattr(attempt_module, "time", fake)

    with pytest.raises(QualityGateFailure) as refusal:  # CHARACTERIZATION: flips in H4 (the human wait is excluded)
        attempt_module._ensure_generation_time_budget(state, ctx, file_count=2, active_model=FALLBACK)
    failure = refusal.value.failure
    assert (failure.type, failure.source) == ("time_budget_exhausted", "orchestrator")
    assert "with 227.0s remaining" in failure.message  # CHARACTERIZATION: flips in H4 (refused although 355.5 s of active budget remain)
    assert "requires 300.0s" in failure.message  # CHARACTERIZATION: flips in H4 (fallback priced from the 90 s default)

    fake.now = 1000.0 + 900 - 226.8  # the monotonic elapsed Kriya measured: the logged message, byte for byte
    with pytest.raises(QualityGateFailure) as logged_refusal:  # CHARACTERIZATION: flips in H4
        attempt_module._ensure_generation_time_budget(state, ctx, file_count=2, active_model=FALLBACK)
    assert logged_refusal.value.failure.message == logged.split(": ", 1)[1].strip()

    # The human wait is inside the clock: the same check with the wait taken
    # out admits the pass (355.5 s >= 300 s), so the wait alone decided it.
    fake.now = 1000.0 + used - human_wait
    attempt_module._ensure_generation_time_budget(state, ctx, file_count=2, active_model=FALLBACK)
    assert round(900 - (used - human_wait), 1) == 355.5
    # and the primary model's recorded timings would also have admitted it
    # (2 x 48.3 s + 120 s < 227 s): only the per-model filter priced it at 300 s.
    fake.now = 1000.0 + used
    attempt_module._ensure_generation_time_budget(state, ctx, file_count=2, active_model=PRIMARY)

