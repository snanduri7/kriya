"""RDCG-D-003 (owner decision 2026-10-22): end of input at a CHANGE-approval prompt is never an approval.

MEASURED before the fix, on a real pty: `kriya fix` read EOF (Ctrl-D) from /dev/tty as `readline() == ""`, stripped it to the empty string and took the displayed `[Y/n]` default -
APPROVED - while `kriya generate` aborted on the same input. The contract now: `y` approves, `n` is the person's decline, and input that ends with no answer (EOF / closed stdin) is
UNAVAILABLE (-> approval_required) on every terminal change-approval path (`fix`, `generate`, `proposal execute`).

These tests drive the real click EOF handling (`CliRunner.isolation`) and the real `readline()` end-of-file value, not a stub that returns the answer the test wants."""
import ast
import io
import pathlib
from typing import Any, List

import pytest
from click.testing import CliRunner

import kriya.cli as cli
from kriya.policy.approval import (
    APPROVAL_REVIEW_EXPIRED,
    ApprovalDecision,
    ApprovalOutcome,
    classify_approval_answer,
)

FILES = [{"filepath": "a.py", "content": "+x = 1\n"}]
REASON = "review policy"
UNAVAILABLE = ApprovalOutcome.unavailable_because(APPROVAL_REVIEW_EXPIRED)


def _confirm_with_input(text: str, **kwargs: Any) -> Any:
    with CliRunner().isolation(input=text):
        return cli._confirm_change("Apply?", **kwargs)


def _assert_unavailable(answer: Any) -> None:
    outcome = classify_approval_answer(answer)
    assert outcome.decision is ApprovalDecision.UNAVAILABLE and outcome.reason_code == APPROVAL_REVIEW_EXPIRED
    assert not outcome.approved and not outcome.declined  # nobody said yes and nobody said no


# --- _confirm_change: the click-based prompt (generate, proposal execute, the fix fallback) -------------------------------------

def test_eof_at_the_click_prompt_is_unavailable_not_an_approval_and_not_a_decline():
    for default in (False, True):  # the displayed default must not be taken for input that never arrived
        _assert_unavailable(_confirm_with_input("", default=default))


@pytest.mark.parametrize("typed,default,expected", [("y\n", False, True), ("yes\n", False, True), ("n\n", True, False), ("no\n", True, False), ("\n", True, True), ("\n", False, False)])
def test_a_typed_answer_is_still_the_plain_bool_and_the_default_still_applies_to_enter(typed, default, expected):
    assert _confirm_with_input(typed, default=default) is expected


def test_generate_callback_matches_fix_for_end_of_input(monkeypatch):
    """Parity: the same change approval has the same meaning on both public paths. `_approval_decision` is the generate callback (outside a desktop launch)."""
    with CliRunner().isolation(input=""):
        generate_answer = cli._approval_decision(FILES, REASON, False)
    with CliRunner().isolation(input=""):
        fix_answer = cli._fix_approval_decision(FILES, REASON, False)  # no controlling terminal needed: the /dev/tty read fails or yields EOF, then the click fallback
    _assert_unavailable(generate_answer)
    _assert_unavailable(fix_answer)


# --- _fix_tty_answer / _fix_approval_decision: the /dev/tty read ----------------------------------------------------------------

@pytest.mark.parametrize("raw", ["y\n", "Y\n", "yes\n", "  y  \n", "y"])  # "y" without a newline = typed y, then Ctrl-D
def test_fix_tty_yes_variants_approve(raw):
    assert cli._fix_tty_answer(raw) is True


@pytest.mark.parametrize("raw", ["n\n", "no\n", "N\n", "x\n", "maybe\n", "\t \tn\n"])
def test_fix_tty_everything_else_is_the_persons_no(raw):
    assert cli._fix_tty_answer(raw) is False


def test_fix_tty_a_bare_enter_takes_the_displayed_yes_default_but_end_of_file_does_not():
    assert cli._fix_tty_answer("\n") is True       # unchanged: Enter is an explicit keypress at a prompt that shows [Y/n]
    _assert_unavailable(cli._fix_tty_answer(""))   # readline() returns "" ONLY at end of file


def _open_tty_returning(monkeypatch, content: str, opened: List[str]) -> None:
    real_open = open

    def fake_open(path, *args, **kwargs):
        if path == "/dev/tty":
            opened.append(path)
            return io.StringIO(content)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)


@pytest.mark.parametrize("content,check", [("", "unavailable"), ("n\n", False), ("y\n", True), ("\n", True)])
def test_fix_callback_reads_the_controlling_terminal_and_maps_each_answer(monkeypatch, content, check):
    opened: List[str] = []
    _open_tty_returning(monkeypatch, content, opened)
    answer = cli._fix_approval_decision(FILES, REASON, False)
    assert opened == ["/dev/tty"]
    if check == "unavailable":
        _assert_unavailable(answer)
    else:
        assert answer is check


def test_fix_yes_flag_auto_approves_without_touching_the_terminal(monkeypatch):
    opened: List[str] = []
    _open_tty_returning(monkeypatch, "", opened)
    assert cli._fix_approval_decision(FILES, REASON, True) is True
    assert opened == []


def test_fix_without_a_controlling_terminal_falls_back_to_the_click_prompt_and_eof_there_is_unavailable(monkeypatch):
    real_open = open

    def no_tty(path, *args, **kwargs):
        if path == "/dev/tty":
            raise OSError("no controlling terminal")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", no_tty)
    with CliRunner().isolation(input=""):
        _assert_unavailable(cli._fix_approval_decision(FILES, REASON, False))
    with CliRunner().isolation(input="n\n"):
        assert cli._fix_approval_decision(FILES, REASON, False) is False
    with CliRunner().isolation(input="y\n"):
        assert cli._fix_approval_decision(FILES, REASON, False) is True


def test_the_broad_catch_in_the_fix_callback_does_not_hide_a_normal_answer(monkeypatch, capsys):
    """Rule 4: the `except Exception` around the terminal read must not turn a normal answer into the fallback prompt."""
    opened: List[str] = []
    _open_tty_returning(monkeypatch, "n\n", opened)
    monkeypatch.setattr(cli, "_confirm_change", lambda *a, **k: pytest.fail("the click fallback must not run when /dev/tty answered"))
    assert cli._fix_approval_decision(FILES, REASON, False) is False
    assert "[Y/n]" in capsys.readouterr().out


# --- structure: no change-approval callback prompts by itself any more ---------------------------------------------------------

def test_no_change_approval_callback_calls_click_confirm_directly():
    """The three terminal change-approval callbacks (generate, proposal execute, fix) answer through the one `_confirm_change`; a direct `click.confirm` would bring back the
    Abort-on-EOF divergence. Found by walking the AST, not by matching text."""
    tree = ast.parse(pathlib.Path(cli.__file__).read_text(encoding="utf-8"))
    guarded = {"_approval_decision", "on_approval", "_fix_approval_decision", "approval_cb"}
    offenders: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in guarded:
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "confirm" and isinstance(call.func.value, ast.Name) and call.func.value.id == "click":
                    offenders.append(f"{node.name}:{call.lineno}")
    assert offenders == []
    # control: the helper itself does prompt (the walk is looking at the right thing)
    helper = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_confirm_change")
    assert any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "confirm" for c in ast.walk(helper))


def test_an_unavailable_answer_is_never_read_through_truthiness():
    with pytest.raises(TypeError):
        bool(UNAVAILABLE)
