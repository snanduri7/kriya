"""RDCG-D-003 (owner decisions 2026-10-22 and 2026-10-23): a CHANGE-approval prompt needs an explicit affirmative human decision.

MEASURED before the fix, on a real pty: `kriya fix` read EOF (Ctrl-D) from /dev/tty as `readline() == ""`, stripped it to the empty string and took the displayed `[Y/n]` default -
APPROVED - while `kriya generate` aborted on the same input. The contract now, on every terminal change-approval path (`fix`, `generate`, `proposal execute`):
`y` -> APPROVED, `n` -> the person's decline (human_rejected), and anything else - a bare Enter, end of file / closed stdin, an intercepted Ctrl-C, text that is neither -
is UNAVAILABLE (approval_required) with APPROVAL_DECISION_NOT_RECORDED. No prompt shows or takes a default answer.

These tests drive the real click handling (`CliRunner.isolation`, click's own prompt function raising) and the real `readline()` end-of-file value, not a stub that returns the answer the test wants."""
import ast
import asyncio
import io
import pathlib
from typing import Any, List

import pytest
from click.testing import CliRunner

import kriya.cli as cli
from kriya.policy.approval import (
    APPROVAL_DECISION_NOT_RECORDED,
    APPROVAL_REVIEW_EXPIRED,
    ApprovalDecision,
    ApprovalOutcome,
    classify_approval_answer,
    resolve_approval,
)

FILES = [{"filepath": "a.py", "content": "+x = 1\n"}]
REASON = "review policy"
UNAVAILABLE = ApprovalOutcome.unavailable_because(APPROVAL_DECISION_NOT_RECORDED)


def _confirm_with_input(text: str) -> Any:
    with CliRunner().isolation(input=text):
        return cli._confirm_change("Apply?")


def _assert_unavailable(answer: Any) -> None:
    outcome = classify_approval_answer(answer)
    assert outcome.decision is ApprovalDecision.UNAVAILABLE
    assert outcome.reason_code == APPROVAL_DECISION_NOT_RECORDED and outcome.reason_code != APPROVAL_REVIEW_EXPIRED  # REVIEW_EXPIRED is for a review that really expired
    assert not outcome.approved and not outcome.declined  # nobody said yes and nobody said no


def _assert_approved(answer: Any) -> None:
    assert answer is True and classify_approval_answer(answer).approved


def _assert_declined(answer: Any) -> None:
    assert answer is False and classify_approval_answer(answer).declined


# --- _terminal_answer: the one parser -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["y\n", "Y\n", "yes\n", "YES\n", "  y  \n", "y"])  # "y" without a newline = typed y, then Ctrl-D
def test_explicit_yes_approves(raw):
    _assert_approved(cli._terminal_answer(raw))


@pytest.mark.parametrize("raw", ["n\n", "N\n", "no\n", "NO\n", "\t \tn\n", "n"])
def test_explicit_no_is_the_persons_decline(raw):
    _assert_declined(cli._terminal_answer(raw))


@pytest.mark.parametrize("raw", ["", "\n", "   \n", "\t\n", "maybe\n", "x\n", "yeah\n", "ok\n", "1\n", "true\n", "yn\n"])
def test_empty_eof_and_unrecognised_input_is_never_a_decision(raw):
    """"" is end of file, "\\n" a bare Enter: neither takes a default, neither approves, neither declines. Text that is not an explicit y/n is not a decision either."""
    _assert_unavailable(cli._terminal_answer(raw))


# --- _confirm_change: the click-based prompt (generate, proposal execute, the fix fallback) -------------------------------------

@pytest.mark.parametrize("typed,check", [("y\n", _assert_approved), ("yes\n", _assert_approved), ("n\n", _assert_declined), ("no\n", _assert_declined),
                                         ("\n", _assert_unavailable), ("", _assert_unavailable), ("what\n", _assert_unavailable)])
def test_the_click_prompt_maps_every_input(typed, check):
    check(_confirm_with_input(typed))


def test_the_click_prompt_shows_no_default_answer():
    with CliRunner().isolation(input="y\n") as (stdout, _stderr, _):
        cli._confirm_change("Apply?")
        shown = stdout.getvalue().decode()
    assert "[y/n]" in shown and "[Y/n]" not in shown and "[y/N]" not in shown


def test_an_intercepted_ctrl_c_at_the_click_prompt_is_unavailable(monkeypatch):
    """click turns the prompt function's KeyboardInterrupt into Abort; the callback must read that as no decision, not as an approval or a crash."""
    def interrupt(*_a: Any, **_k: Any) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("click.termui.visible_prompt_func", interrupt)
    _assert_unavailable(cli._confirm_change("Apply?"))


# --- the three public callbacks agree ----------------------------------------------------------------------------------------------

def _no_tty(monkeypatch) -> None:
    real_open = open

    def fake_open(path, *args, **kwargs):
        if path == "/dev/tty":
            raise OSError("no controlling terminal")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)


@pytest.mark.parametrize("typed,check", [("y\n", _assert_approved), ("n\n", _assert_declined), ("\n", _assert_unavailable), ("", _assert_unavailable)])
def test_generate_and_fix_give_the_same_answer_for_the_same_input(monkeypatch, typed, check):
    """Parity: the same change approval has the same meaning on both public paths (`_approval_decision` is the generate callback outside a desktop launch; fix falls back to the click prompt without a terminal)."""
    _no_tty(monkeypatch)
    with CliRunner().isolation(input=typed):
        generate_answer = cli._approval_decision(FILES, REASON, False)
    with CliRunner().isolation(input=typed):
        fix_answer = cli._fix_approval_decision(FILES, REASON, False)
    check(generate_answer)
    check(fix_answer)


@pytest.mark.parametrize("typed,approved,declined", [("y\n", True, False), ("n\n", False, True), ("\n", False, False), ("", False, False)])
def test_sync_and_async_callback_paths_resolve_to_the_same_decision(typed, approved, declined):
    """The workflow resolves a callback answer through `resolve_approval`, sync or awaitable; both forms of the same terminal answer must resolve identically."""
    def sync_callback(_files: Any, _reason: str) -> Any:
        return cli._terminal_answer(typed)

    async def async_callback(_files: Any, _reason: str) -> Any:
        return cli._terminal_answer(typed)

    sync_outcome = asyncio.run(resolve_approval(sync_callback, FILES, REASON))
    async_outcome = asyncio.run(resolve_approval(async_callback, FILES, REASON))
    assert sync_outcome == async_outcome
    assert (sync_outcome.approved, sync_outcome.declined) == (approved, declined)
    if not approved and not declined:
        assert sync_outcome.reason_code == APPROVAL_DECISION_NOT_RECORDED


# --- the /dev/tty read used by `kriya fix` -----------------------------------------------------------------------------------------

def _open_tty_returning(monkeypatch, content: Any, opened: List[str]) -> None:
    """`content` is the tty's text, or an exception class the read raises."""
    real_open = open

    def fake_open(path, *args, **kwargs):
        if path == "/dev/tty":
            opened.append(path)
            if isinstance(content, type) and issubclass(content, BaseException):
                class Interrupting(io.StringIO):
                    def readline(self, *_a: Any, **_k: Any) -> str:
                        raise content

                return Interrupting("")
            return io.StringIO(content)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)


@pytest.mark.parametrize("content,check", [("", _assert_unavailable), ("\n", _assert_unavailable), ("n\n", _assert_declined), ("y\n", _assert_approved),
                                           ("maybe\n", _assert_unavailable), (KeyboardInterrupt, _assert_unavailable)])
def test_fix_callback_reads_the_controlling_terminal_and_maps_each_answer(monkeypatch, content, check):
    opened: List[str] = []
    _open_tty_returning(monkeypatch, content, opened)
    answer = cli._fix_approval_decision(FILES, REASON, False)
    assert opened == ["/dev/tty"]
    check(answer)


def test_fix_prompt_text_shows_no_default(monkeypatch, capsys):
    _open_tty_returning(monkeypatch, "n\n", [])
    cli._fix_approval_decision(FILES, REASON, False)
    shown = capsys.readouterr().out
    assert "[y/n]" in shown and "[Y/n]" not in shown


def test_fix_yes_flag_auto_approves_without_touching_the_terminal(monkeypatch):
    opened: List[str] = []
    _open_tty_returning(monkeypatch, "", opened)
    assert cli._fix_approval_decision(FILES, REASON, True) is True
    assert opened == []


def test_the_broad_catch_in_the_fix_callback_does_not_hide_a_normal_answer(monkeypatch):
    """Rule 4: the `except Exception` around the terminal read must not turn a normal answer into the fallback prompt."""
    opened: List[str] = []
    _open_tty_returning(monkeypatch, "n\n", opened)
    monkeypatch.setattr(cli, "_confirm_change", lambda *a, **k: pytest.fail("the click fallback must not run when /dev/tty answered"))
    _assert_declined(cli._fix_approval_decision(FILES, REASON, False))


# --- structure: no change-approval callback prompts by itself, and no default can authorize ----------------------------------------

def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def test_no_change_approval_callback_calls_click_confirm_or_prompt_directly():
    """The three terminal change-approval callbacks (generate, proposal execute, fix) answer through the one `_confirm_change` / `_terminal_answer`; a direct `click.confirm` (a
    `default=` answer, an Abort on EOF) would bring back the divergence. Found by walking the AST, not by matching text."""
    tree = ast.parse(pathlib.Path(cli.__file__).read_text(encoding="utf-8"))
    guarded = {"_approval_decision", "on_approval", "_fix_approval_decision", "approval_cb"}
    offenders: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in guarded:
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in ("confirm", "prompt") and isinstance(call.func.value, ast.Name) and call.func.value.id == "click":
                    offenders.append(f"{node.name}:{call.lineno}")
    assert offenders == []
    helper = _function(tree, "_confirm_change")  # control: the helper itself does prompt (the walk is looking at the right thing)
    assert any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "prompt" for c in ast.walk(helper))


def test_the_change_approval_helpers_have_no_default_answer():
    tree = ast.parse(pathlib.Path(cli.__file__).read_text(encoding="utf-8"))
    for name in ("_confirm_change", "_terminal_answer", "_fix_approval_decision"):
        args = _function(tree, name).args
        assert not args.defaults and not any(d is not None for d in args.kw_defaults), name
    prompt_call = next(c for c in ast.walk(_function(tree, "_confirm_change")) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "prompt")
    defaults = [k.value.value for k in prompt_call.keywords if k.arg == "default"]
    assert defaults == [""]  # the prompt's own default is the empty answer, which _terminal_answer reads as no decision


def test_an_unavailable_answer_is_never_read_through_truthiness():
    with pytest.raises(TypeError):
        bool(UNAVAILABLE)
