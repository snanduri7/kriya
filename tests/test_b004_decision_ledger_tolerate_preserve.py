"""B-004 (Wave 2 Step 8; owner policy: TOLERATE AND PRESERVE): a damaged line of the decision ledger never wedges a durable audit write, is never silently treated as absent, and is never repaired.

MEASURED at 9ffcd0e (step8_repro.py in the Step-8 evidence directory): ``load_decision_ledger`` tolerated a torn JSON line while ``DecisionLedger.append_to_file`` ran an unguarded ``json.loads``
over every existing line - a torn, garbage or non-object line killed the NEXT decision write with JSONDecodeError / AttributeError (an enforce run at its first decision, an operator's retry /
abandon at its durable audit record), and a non-UTF-8 line made the reader itself raise UnicodeDecodeError. Reader and writer disagreed.

Now both classify a line with ONE function: readable -> a Decision; unreadable (not UTF-8 / not JSON / not a decision) -> a structured ``MalformedLedgerLine`` that the reader skips and reports
(``malformed_lines`` / ``degraded``), the writer carries forward as its exact bytes (the control-store writer's ``content_bytes``), and an operator's disposition result reports
(``details.audit_degraded``; ``kriya runs tool-abandon|tool-retry`` prints a note). A line stamped for ANOTHER workspace, or carrying no stamp, is not malformed: it is the D2-M4 ownership
refusal and raises exactly as before.
"""
import json
import os
import sys
from unittest.mock import patch

import pytest
from click.testing import CliRunner

sys.path.insert(0, os.path.dirname(__file__))
from test_slice_a_tool_disposition import _apply, _bytes, _dispositions, _seed  # noqa: E402
from test_w202_direct_tool_disposition import _abandon, _crashed_direct_run, _direct_audit  # noqa: E402

from kriya.cli import main  # noqa: E402
from kriya.context.workspace_identity import WorkspaceOwnershipError, workspace_identity  # noqa: E402
from kriya.control import tool_disposition as td  # noqa: E402
from kriya.control.decisions import (  # noqa: E402
    LEDGER_LINE_NOT_A_DECISION,
    LEDGER_LINE_NOT_JSON,
    LEDGER_LINE_NOT_UTF8,
    DecisionLedger,
    LedgerAppendResult,
    MalformedLedgerLine,
    load_decision_ledger,
)
from kriya.control.persistence import decision_ledger_path  # noqa: E402
from kriya.control.state import SUBTASK_STATE_TOOL_FAILED  # noqa: E402


def _healthy(ws, n=2):
    ledger = DecisionLedger()
    for index in range(n):
        ledger.record_and_persist(ws, "probe", index=index)
    return open(decision_ledger_path(ws), "rb").read()


def _stamped(ws, payload):
    return json.dumps({**payload, "_workspace_id": workspace_identity(ws)}).encode()


# shape -> (function(ws) -> the bytes of the damaged line, the reason the classifier must give)
LINES = {
    "torn_json": (lambda ws: b'{"type": "probe", "fields": {"index": 2', LEDGER_LINE_NOT_JSON),
    "ascii_garbage": (lambda ws: b"this is not json at all", LEDGER_LINE_NOT_JSON),
    "json_list": (lambda ws: b"[1, 2, 3]", LEDGER_LINE_NOT_A_DECISION),
    "json_scalar": (lambda ws: b"42", LEDGER_LINE_NOT_A_DECISION),
    "stamped_object_without_a_type": (lambda ws: _stamped(ws, {"hello": "world"}), LEDGER_LINE_NOT_A_DECISION),
    "non_utf8_bytes": (lambda ws: b"\xff\xfe\x00garbage", LEDGER_LINE_NOT_UTF8),
    "torn_line_with_crlf": (lambda ws: b'{"type": "probe", "fi\r', LEDGER_LINE_NOT_JSON),
}


def _damaged(tmp_path, shape, where="last"):
    """A ledger of two healthy decisions with one damaged line placed last, first or between them. Returns (workspace, the exact bytes on disk, the 1-based number of the damaged line)."""
    ws = str(tmp_path)
    good = _healthy(ws)
    first, second = good.rstrip(b"\n").split(b"\n")
    bad = LINES[shape][0](ws)
    order = {"last": [first, second, bad], "first": [bad, first, second], "between": [first, bad, second]}[where]
    data = b"\n".join(order) + b"\n"
    open(decision_ledger_path(ws), "wb").write(data)
    return ws, data, order.index(bad) + 1


# ----------------------------------------------------------------------------------------------------------------------------------------- the reader

@pytest.mark.parametrize("where", ["last", "first", "between"])
@pytest.mark.parametrize("shape", sorted(LINES))
def test_the_reader_skips_a_damaged_line_and_reports_it_structurally(tmp_path, shape, where):
    ws, _data, number = _damaged(tmp_path, shape, where)
    ledger = load_decision_ledger(ws)
    assert [d.fields["index"] for d in ledger.all()] == [0, 1]                       # every readable decision survives, in order
    assert ledger.degraded and ledger.malformed_lines == (MalformedLedgerLine(number, LINES[shape][1]),)
    assert ledger.malformed_lines[0].to_dict() == {"line_number": number, "reason": LINES[shape][1]}


def test_a_json_nesting_error_is_not_json_not_a_crash():
    """RecursionError is what older interpreters raise for pathological nesting (this one parses 100000 levels); injected at the json seam so the branch is proven on any interpreter."""
    from kriya.control import decisions

    with patch.object(decisions.json, "loads", side_effect=RecursionError("too deep")):
        assert decisions._classify_ledger_line("/w", b"[[[[", "src") == (None, LEDGER_LINE_NOT_JSON)       # pylint: disable=protected-access


def test_a_healthy_ledger_is_not_degraded(tmp_path):
    ledger = load_decision_ledger(_dirty_free(tmp_path))
    assert not ledger.degraded and ledger.malformed_lines == () and len(ledger.all()) == 2


def _dirty_free(tmp_path):
    ws = str(tmp_path)
    _healthy(ws)
    return ws


# ----------------------------------------------------------------------------------------------------------------------------------------- the writer

@pytest.mark.parametrize("where", ["last", "first", "between"])
@pytest.mark.parametrize("shape", sorted(LINES))
def test_the_writer_appends_after_a_damaged_line_and_carries_its_exact_bytes_forward(tmp_path, shape, where):
    ws, before, number = _damaged(tmp_path, shape, where)
    ledger = DecisionLedger()
    result = ledger.append_to_file(ws, ledger.record("operator_disposition", action="retry"), durable=True)       # never raises
    assert isinstance(result, LedgerAppendResult) and result.degraded
    assert result.preserved_malformed_lines == (MalformedLedgerLine(number, LINES[shape][1]),)
    after = open(decision_ledger_path(ws), "rb").read()
    prior_lines = before.rstrip(b"\n").split(b"\n")
    assert after.split(b"\n")[: len(prior_lines)] == prior_lines                     # every prior line, readable or not, byte-identical and in place
    assert after.endswith(b"\n") and json.loads(after.rstrip(b"\n").split(b"\n")[-1])["type"] == "operator_disposition"
    assert len(after.rstrip(b"\n").split(b"\n")) == len(prior_lines) + 1             # exactly one line was added: nothing dropped, nothing duplicated
    reloaded = load_decision_ledger(ws)
    assert [d.type for d in reloaded.all()] == ["probe", "probe", "operator_disposition"] and reloaded.malformed_lines[0].line_number == number


def test_a_second_append_still_works_and_the_damage_neither_grows_nor_shrinks(tmp_path):
    ws, _before, number = _damaged(tmp_path, "non_utf8_bytes", "between")
    for action in ("retry", "abandon"):
        ledger = DecisionLedger()
        result = ledger.append_to_file(ws, ledger.record("operator_disposition", action=action))
        assert [m.line_number for m in result.preserved_malformed_lines] == [number]
    assert [m.line_number for m in load_decision_ledger(ws).malformed_lines] == [number]
    assert b"\xff\xfe\x00garbage" in open(decision_ledger_path(ws), "rb").read()


def test_a_healthy_append_is_unchanged_and_reports_no_degradation(tmp_path):
    ws = str(tmp_path)
    ledger = DecisionLedger()
    result = ledger.append_to_file(ws, ledger.record("probe", index=0))
    assert result == LedgerAppendResult() and not result.degraded
    raw = open(decision_ledger_path(ws), "rb").read()
    line = json.loads(raw)
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1 and line["type"] == "probe" and line["_workspace_id"] == workspace_identity(ws)
    assert raw.rstrip(b"\n") == json.dumps(line, sort_keys=True).encode()


def test_blank_lines_are_still_normalised_away_and_that_is_not_a_malformed_line(tmp_path):
    ws = str(tmp_path)
    good = _healthy(ws)
    open(decision_ledger_path(ws), "wb").write(good.replace(b"\n", b"\n\n"))
    ledger = DecisionLedger()
    assert not ledger.append_to_file(ws, ledger.record("probe", index=9)).degraded
    assert open(decision_ledger_path(ws), "rb").read().count(b"\n") == 3 and not load_decision_ledger(ws).degraded


def test_a_log_warning_names_the_preserved_lines(tmp_path, caplog):
    ws, _before, number = _damaged(tmp_path, "ascii_garbage", "between")
    ledger = DecisionLedger()
    with caplog.at_level("WARNING"):
        ledger.append_to_file(ws, ledger.record("probe", index=3))
    assert any("malformed line" in r.getMessage() and str(number) in r.getMessage() and "preserved byte-for-byte" in r.getMessage() for r in caplog.records)


# ------------------------------------------------------------------------------------------------------------ the ownership refusal is NOT malformed

def test_a_line_without_an_ownership_stamp_is_still_the_ownership_refusal_in_the_reader_and_the_writer(tmp_path):
    ws = str(tmp_path)
    good = _healthy(ws)
    data = good + b'{"type": "probe", "index": 5}\n'
    open(decision_ledger_path(ws), "wb").write(data)
    with pytest.raises(WorkspaceOwnershipError):
        load_decision_ledger(ws)
    ledger = DecisionLedger()
    with pytest.raises(WorkspaceOwnershipError):
        ledger.append_to_file(ws, ledger.record("probe", index=6))
    assert open(decision_ledger_path(ws), "rb").read() == data                          # nothing written


def test_a_line_stamped_for_another_workspace_is_still_refused_never_preserved_as_malformed(tmp_path):
    ws = str(tmp_path)
    good = _healthy(ws)
    data = good + json.dumps({"type": "probe", "_workspace_id": "someone-else"}).encode() + b"\n"
    open(decision_ledger_path(ws), "wb").write(data)
    with pytest.raises(WorkspaceOwnershipError):
        load_decision_ledger(ws)
    ledger = DecisionLedger()
    with pytest.raises(WorkspaceOwnershipError):
        ledger.append_to_file(ws, ledger.record("probe", index=6))
    assert open(decision_ledger_path(ws), "rb").read() == data


# ------------------------------------------------------------------------------------------- operator retry / abandon must not wedge (end to end)

@pytest.mark.parametrize("action", ["abandon", "retry"])
@pytest.mark.parametrize("shape", ["torn_json", "non_utf8_bytes", "json_list"])
def test_an_operator_disposition_succeeds_over_a_damaged_ledger_and_says_so(tmp_path, action, shape):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    ws, before, number = _damaged(tmp_path, shape, "between")
    result = _apply(tmp_path, action)
    assert result.ok and result.status == td.STATUS_APPLIED                              # not wedged
    degraded = result.details["audit_degraded"]
    assert degraded["malformed_ledger_lines"] == [{"line_number": number, "reason": LINES[shape][1]}] and "preserved byte-for-byte" in degraded["note"]
    assert result.to_dict()["details"]["audit_degraded"] == degraded                    # in the serialized result too
    after = open(decision_ledger_path(ws), "rb").read()
    assert after.split(b"\n")[: len(before.rstrip(b"\n").split(b"\n"))] == before.rstrip(b"\n").split(b"\n")
    assert len(_dispositions(tmp_path)) == 1                                              # the durable audit record was appended after the damage


def test_a_healthy_ledger_adds_no_audit_degraded_field_to_a_disposition(tmp_path):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    result = _apply(tmp_path, "abandon")
    assert result.ok and "audit_degraded" not in result.details and "audit_degraded" not in result.to_dict()["details"]


def test_a_refused_disposition_over_a_damaged_ledger_changes_no_byte_and_still_reports_the_damage(tmp_path):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    ws, _before, number = _damaged(tmp_path, "ascii_garbage", "last")
    snapshot = _bytes(tmp_path)
    result = _apply(tmp_path, "abandon", attempt=7)                                       # a stale attempt: refused
    assert not result.ok and result.reason_code == td.STALE_ATTEMPT
    assert _bytes(tmp_path) == snapshot
    assert result.details["audit_degraded"]["malformed_ledger_lines"][0]["line_number"] == number


@pytest.mark.parametrize("shape", ["torn_json", "non_utf8_bytes"])
def test_abandoning_a_direct_run_succeeds_over_a_damaged_ledger_and_says_so(tmp_path, shape):
    """The second operator path: `kriya runs tool-abandon --run RUN_ID` (a REAL crashed direct run) writes its durable audit record into the same ledger."""
    workspace, _plugins, _effects, run_id = _crashed_direct_run(tmp_path)
    ws = str(workspace)
    good = open(decision_ledger_path(ws), "rb").read() if os.path.exists(decision_ledger_path(ws)) else b""
    bad = LINES[shape][0](ws)
    data = (good if good.endswith(b"\n") or not good else good + b"\n") + bad + b"\n"
    os.makedirs(os.path.dirname(decision_ledger_path(ws)), exist_ok=True)
    open(decision_ledger_path(ws), "wb").write(data)
    number = data.rstrip(b"\n").count(b"\n") + 1
    result = _abandon(workspace, run_id)
    assert result.ok and result.status == td.STATUS_APPLIED
    assert result.details["audit_degraded"]["malformed_ledger_lines"] == [{"line_number": number, "reason": LINES[shape][1]}]
    after = open(decision_ledger_path(ws), "rb").read()
    assert after.startswith(data) and len(_direct_audit(workspace)) == 1                  # the damage is byte-identical and the audit record follows it


def test_the_cli_prints_a_note_and_exits_zero_over_a_damaged_ledger(tmp_path):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    _damaged(tmp_path, "non_utf8_bytes", "between")
    result = CliRunner().invoke(main, ["runs", "tool-abandon", "t1", "--workspace", str(tmp_path), "--attempt", "1", "--confirm"])
    assert result.exit_code == 0 and "Traceback" not in result.output
    assert "decision ledger has unreadable line(s) 2" in result.output and "preserved byte-for-byte" in result.output


def test_the_cli_json_carries_the_structured_field(tmp_path):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    _damaged(tmp_path, "torn_json", "last")
    result = CliRunner().invoke(main, ["runs", "tool-abandon", "t1", "--workspace", str(tmp_path), "--attempt", "1", "--confirm", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["details"]["audit_degraded"]["malformed_ledger_lines"] == [{"line_number": 3, "reason": LEDGER_LINE_NOT_JSON}]
