"""OPEN-FINDING-EVIDENCE-001 (Scenario-A run 20261113T235143-0762989b, MEASURED): a carried finding must keep the lines that say what failed.

Live sequence: attempt 2's `ProtocolParserTest` failed `targeted_test` with 11252 characters of Maven output; the assertion
(`expected: <9999999999> but was: <1410065407>`) is at offsets 1582 and 9994. Attempts 3, 4 and 7 failed on the response alone
(INVALID_EDIT_PROTOCOL, ANCHOR_NOT_IN_FILE, OUTPUT_TRUNCATED). The requests that followed (attempts 4, 5, 7, 8) carried the finding
through a blind 2000-character head-and-tail cut: the Maven preamble and help footer, no assertion, and no source window. Both
later candidates repaired an invented compile problem.

The fixture is the exact gate output of that attempt. The first tests drive the real recording path (`handle_attempt_failure`) and
read what the next repair request carries.
"""
from pathlib import Path

import pytest
from test_d1_open_findings import _ctx, _next_request, _protocol, _record

from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.open_findings import MAX_EVIDENCE_CHARS, _head_and_tail, bounded_evidence
from kriya.workflow.state import GenerationState

RUN10_OUTPUT = (Path(__file__).parent / "fixtures/open_finding_evidence_001/run10_s6_attempt2_maven_test_output.txt").read_text(
    encoding="utf-8")
ASSERTION = "expected: <9999999999> but was: <1410065407>"
PARSER_TEST = "src/test/java/com/example/ProtocolParserTest.java"
LINE_75 = "        assertEquals(originalProtocol.getTime(), decodedProtocol.getTime());"


def _write_parser_test(tmp_path) -> None:
    target = tmp_path / PARSER_TEST
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"// line {number}" for number in range(1, 131)]
    lines[74] = LINE_75
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run10_test_failure() -> QualityGateFailure:
    return QualityGateFailure(Failure(
        type="targeted_test", message="TEST FAILURE", raw_output=RUN10_OUTPUT,
        file_locations=[FileLocation(filepath=PARSER_TEST, line=75)], likely_files=[PARSER_TEST]))


def test_the_fixture_is_the_measured_run10_output():
    assert len(RUN10_OUTPUT) == 11252
    assert [i for i in range(len(RUN10_OUTPUT)) if RUN10_OUTPUT.startswith("1410065407", i)] == [1582, 9994]
    # The pre-fix cut, unchanged as the fallback, is what lost it.
    assert ASSERTION not in _head_and_tail(RUN10_OUTPUT, MAX_EVIDENCE_CHARS)


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", [
    {"reason": "INVALID_EDIT_PROTOCOL", "raw": "malformed repair response: INVALID_EDIT_PROTOCOL: text after the last protocol block"},
    {"type_": "anchored_edit", "reason": "ANCHOR_NOT_IN_FILE", "raw": "ANCHOR_NOT_IN_FILE: Anchor matching failed for edit #1"},
    {"reason": "OUTPUT_TRUNCATED", "raw": "malformed repair response: OUTPUT_TRUNCATED: the model stopped at its output budget"},
])
async def test_run10_the_request_after_a_delivery_failure_still_shows_the_assertion_and_the_line(tmp_path, delivery):
    _write_parser_test(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    await _record(state, ctx, _run10_test_failure(), _protocol(files=(PARSER_TEST,), **delivery))

    [finding] = state.open_findings
    assert finding.failure_type == "targeted_test" and finding.locations == ((PARSER_TEST, 75),)
    assert ASSERTION in finding.evidence
    assert "(ProtocolParserTest.java:75)" in finding.evidence
    assert len(finding.evidence) <= MAX_EVIDENCE_CHARS

    package = _next_request(state, ctx, targets=(PARSER_TEST,))
    assert ASSERTION in package.render_error()
    assert ASSERTION in package.prior_error_text()
    window = state.last_error_source_context[PARSER_TEST]
    assert "Source context at the reported error location (line 75)" in window
    assert f">> 75: {LINE_75}" in window


@pytest.mark.asyncio
async def test_two_consecutive_delivery_failures_keep_the_assertion_and_the_window(tmp_path):
    """Run 10 attempts 3 then 4: the second delivery failure must not erase what the first one kept."""
    _write_parser_test(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    await _record(state, ctx, _run10_test_failure(), _protocol(files=(PARSER_TEST,)),
                  _protocol(files=(PARSER_TEST,), type_="anchored_edit", reason="ANCHOR_NOT_IN_FILE", raw="ANCHOR_NOT_IN_FILE"))

    assert ASSERTION in _next_request(state, ctx, targets=(PARSER_TEST,)).render_error()
    assert f">> 75: {LINE_75}" in state.last_error_source_context[PARSER_TEST]


@pytest.mark.asyncio
async def test_the_window_is_read_fresh_from_the_current_source(tmp_path):
    _write_parser_test(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    await _record(state, ctx, _run10_test_failure())
    target = tmp_path / PARSER_TEST
    target.write_text(target.read_text(encoding="utf-8").replace(LINE_75, "        // rewritten line"), encoding="utf-8")
    await _record(state, ctx, _protocol(files=(PARSER_TEST,)))

    assert ">> 75:         // rewritten line" in state.last_error_source_context[PARSER_TEST]


@pytest.mark.asyncio
async def test_a_delivery_failure_with_no_located_open_finding_shows_no_source_window(tmp_path):
    """Negative control: without a validator location nothing is invented."""
    _write_parser_test(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    unlocated = QualityGateFailure(Failure(type="targeted_test", message="TEST FAILURE", raw_output=RUN10_OUTPUT,
                                           likely_files=[PARSER_TEST]))
    await _record(state, ctx, unlocated, _protocol(files=(PARSER_TEST,)))

    [finding] = state.open_findings
    assert finding.locations == ()
    assert finding.evidence == _head_and_tail(RUN10_OUTPUT, MAX_EVIDENCE_CHARS)
    assert state.last_error_source_context == {}


@pytest.mark.asyncio
async def test_a_pure_delivery_failure_with_no_open_finding_shows_no_source_window(tmp_path):
    _write_parser_test(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    await _record(state, ctx, _protocol(files=(PARSER_TEST,)))

    assert state.open_findings == [] and state.last_error_source_context == {}


@pytest.mark.asyncio
async def test_a_failure_that_is_not_about_the_response_does_not_bring_the_window_back(tmp_path):
    """Scope control: only a delivery failure re-shows the open finding's lines; an operational failure keeps today's behavior."""
    _write_parser_test(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    operational = QualityGateFailure(Failure(type="general_error", message="boom", raw_output="boom"))
    await _record(state, ctx, _run10_test_failure(), operational)

    assert [finding.failure_type for finding in state.open_findings] == ["targeted_test"]
    assert state.last_error_source_context == {}


# --- the excerpt itself ---------------------------------------------------------------------------------------------------------

def test_short_evidence_is_returned_whole():
    text = "Foo.java:[3,1] error: boom\n" * 3
    assert bounded_evidence(text, [("src/Foo.java", 3)], 2000) == text


def test_no_anchor_in_the_output_falls_back_to_both_ends():
    text = "HEAD-LINE\n" + "noise line\n" * 5000 + "DECISIVE-TAIL-LINE"
    assert bounded_evidence(text, [("src/Foo.java", 3)], 2000) == _head_and_tail(text, 2000)
    assert bounded_evidence(text, [], 2000) == _head_and_tail(text, 2000)


def test_a_location_on_another_line_of_the_same_file_is_not_an_anchor():
    text = "noise\n" * 2000 + "at pkg.Foo.run(Foo.java:41)\n" + "noise\n" * 2000
    assert bounded_evidence(text, [("src/Foo.java", 40)], 2000) == _head_and_tail(text, 2000)


def test_huge_trailing_help_text_cannot_evict_the_failure():
    text = ("preamble\n" * 30 + "org.example.Boom: the real reason\n\tat pkg.Foo.run(Foo.java:41)\n"
            + "[ERROR] For more information please read the following articles\n" * 3000)
    excerpt = bounded_evidence(text, [("src/Foo.java", 41)], 2000)
    assert "org.example.Boom: the real reason" in excerpt and "(Foo.java:41)" in excerpt
    assert len(excerpt) <= 2000


def test_several_errors_keep_every_authoritative_anchor_within_the_bound():
    noise = "[INFO] building a module with a long descriptive line of output\n" * 400
    text = (noise + "[ERROR] /w/src/A.java:[12,40] unreported exception\n" + noise
            + "[ERROR] /w/src/B.java:[59,7] cannot find symbol\n" + noise
            + "[ERROR] /w/src/C.java:[117,3] incompatible types\n" + noise)
    excerpt = bounded_evidence(text, [("src/A.java", 12), ("src/B.java", 59), ("src/C.java", 117)], 2000)
    for line in ("A.java:[12,40] unreported exception", "B.java:[59,7] cannot find symbol", "C.java:[117,3] incompatible types"):
        assert line in excerpt
    assert len(excerpt) <= 2000
    assert excerpt.count("... [lines omitted] ...") == 4


def test_only_the_validators_locations_are_anchors_when_the_output_names_more():
    noise = "[INFO] noise line of build output that fills the evidence\n" * 400
    text = noise + "[ERROR] /w/src/A.java:[12,40] theirs\n" + noise + "[ERROR] /w/src/B.java:[59,7] not selected\n" + noise
    excerpt = bounded_evidence(text, [("src/A.java", 12)], 2000)
    assert "A.java:[12,40] theirs" in excerpt and "B.java:[59,7] not selected" not in excerpt


def test_more_anchors_than_fit_keeps_the_first_ones_and_stays_bounded():
    rows = [f"[ERROR] /w/src/F{number}.java:[{number},1] " + "x" * 150 + "\n" + "[INFO] filler\n" * 5 for number in range(1, 101)]
    excerpt = bounded_evidence("".join(rows), [(f"src/F{number}.java", number) for number in range(1, 101)], 2000)
    assert len(excerpt) <= 2000
    assert "F1.java:[1,1]" in excerpt and "F2.java:[2,1]" in excerpt and "F100.java:[100,1]" not in excerpt


def test_an_anchor_line_longer_than_the_bound_falls_back():
    text = "start\n" + "[ERROR] /w/src/A.java:[12,40] " + "y" * 5000 + "\nend"
    assert bounded_evidence(text, [("src/A.java", 12)], 2000) == _head_and_tail(text, 2000)


@pytest.mark.parametrize("limit", [200, 500, 2000])
def test_the_bound_is_never_exceeded(limit):
    assert len(bounded_evidence(RUN10_OUTPUT, [(PARSER_TEST, 75)], limit)) <= limit
