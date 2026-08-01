from unittest.mock import AsyncMock, MagicMock

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from kriya.cli import main as cli_main
from kriya.repl import _dispatch, _inject_config, _resolve_clarify, _route_line, _SlashCommandCompleter
from kriya.routing import CLARIFY, UNROUTABLE, RoutingModelUnavailable, RoutingResult


def _complete(text):
    completer = _SlashCommandCompleter(cli_main)
    return list(completer.get_completions(Document(text), CompleteEvent()))


def test_inject_config_prepends_when_missing():
    assert _inject_config(["generate", "do a thing"], "kriya.yaml") == [
        "-c", "kriya.yaml", "generate", "do a thing"
    ]


def test_inject_config_skips_when_short_flag_already_present():
    tokens = ["generate", "do a thing", "-c", "other.yaml"]
    assert _inject_config(tokens, "kriya.yaml") == tokens


def test_inject_config_skips_when_long_flag_already_present():
    tokens = ["generate", "do a thing", "--config", "other.yaml"]
    assert _inject_config(tokens, "kriya.yaml") == tokens


def test_inject_config_skips_when_session_has_no_config():
    tokens = ["generate", "do a thing"]
    assert _inject_config(tokens, None) == tokens


def test_dispatch_runs_a_successful_command_without_raising(capsys):
    # Must not raise - a successful command exits click's internals via
    # click.exceptions.Exit under standalone_mode=False, which _dispatch is
    # specifically responsible for swallowing so the REPL loop keeps going.
    _dispatch(cli_main, ["version"])
    captured = capsys.readouterr()
    assert "Kriya version" in captured.out


def test_dispatch_reports_unknown_command_without_raising(capsys):
    # An unknown subcommand raises a click.UsageError (a ClickException) -
    # _dispatch must report it (ClickException.show() writes to stderr), not
    # propagate it and kill the session.
    _dispatch(cli_main, ["not-a-real-command"])
    captured = capsys.readouterr()
    assert "No such command" in captured.err


def test_dispatch_reports_missing_required_argument_without_raising(capsys):
    _dispatch(cli_main, ["ask"])
    captured = capsys.readouterr()
    assert "Missing argument" in captured.err  # reported, not a silent swallow


def test_slash_alone_lists_every_real_command_and_meta_command():
    completions = _complete("/")
    inserted = {c.text for c in completions}
    # A representative sample of real CLI commands, inserted bare (no slash),
    # since that's how they're actually invoked in the session.
    assert {"generate", "ask", "analyze", "skills", "doctor"} <= inserted
    # Meta-commands keep their slash, since that's how those are invoked.
    assert {"/help", "/exit", "/quit", "/clear"} <= inserted


def test_slash_excludes_repl_itself():
    completions = _complete("/")
    assert "repl" not in {c.text for c in completions}


def test_slash_filters_as_you_type():
    completions = _complete("/gen")
    inserted = {c.text for c in completions}
    assert inserted == {"generate"}


def test_slash_completion_replaces_the_whole_typed_prefix():
    [completion] = _complete("/gen")
    # start_position=-len("/gen") means the full "/gen" gets replaced by
    # "generate", not appended after it.
    assert completion.start_position == -4
    assert completion.text == "generate"


def test_slash_completion_carries_the_command_help_as_display_meta():
    [completion] = _complete("/version")
    assert "version" in str(completion.display_meta).lower()


def test_no_completions_once_a_command_and_space_have_been_typed():
    # Typing a full command plus its arguments (e.g. a goal string with
    # spaces) must not keep triggering the slash-command popup.
    assert _complete("/generate do a thing") == []


def test_no_completions_for_a_non_slash_line():
    assert _complete("generate") == []


def _mock_router(result: RoutingResult) -> MagicMock:
    router = MagicMock()
    router.route = AsyncMock(return_value=result)
    return router


def test_route_line_bypasses_routing_for_a_known_command():
    # A recognized command always dispatches directly, even with routing
    # enabled - no LLM/embedding call should happen at all.
    router = _mock_router(RoutingResult(label="generate", score=0.9))
    tokens, returned_router = _route_line(
        cli_main, router, session=MagicMock(), line="ask something", tokens=["ask", "something"]
    )
    assert tokens == ["ask", "something"]
    router.route.assert_not_called()
    assert returned_router is router


def test_route_line_returns_tokens_unchanged_when_routing_disabled():
    tokens, returned_router = _route_line(
        cli_main, router=None, session=MagicMock(), line="not a command", tokens=["not-a-real-command"]
    )
    assert tokens == ["not-a-real-command"]
    assert returned_router is None


def test_route_line_reports_unroutable_and_dispatches_nothing(capsys):
    router = _mock_router(RoutingResult(label=UNROUTABLE, score=0.1))
    tokens, returned_router = _route_line(
        cli_main, router, session=MagicMock(), line="delete all my files",
        tokens=["delete", "all", "my", "files"],
    )
    assert tokens is None
    assert returned_router is router
    assert "I don't think that's something I can do" in capsys.readouterr().out


def test_route_line_dispatches_the_resolved_command(capsys):
    router = _mock_router(RoutingResult(label="ask", score=0.8))
    tokens, _ = _route_line(
        cli_main, router, session=MagicMock(), line="why is this slow",
        tokens=["why", "is", "this", "slow"],
    )
    assert tokens == ["ask", "why is this slow"]
    assert "routed to: ask" in capsys.readouterr().out


def test_route_line_clarify_dispatches_the_users_choice():
    router = _mock_router(RoutingResult(label=CLARIFY, score=0.7, candidates=["fix", "ask"]))
    session = MagicMock()
    session.prompt = MagicMock(return_value="2")
    tokens, _ = _route_line(
        cli_main, router, session=session, line="explain why this test keeps failing",
        tokens=["explain", "why", "this", "test", "keeps", "failing"],
    )
    assert tokens == ["ask", "explain why this test keeps failing"]


def test_route_line_clarify_cancelled_dispatches_nothing():
    router = _mock_router(RoutingResult(label=CLARIFY, score=0.7, candidates=["fix", "ask"]))
    session = MagicMock()
    session.prompt = MagicMock(return_value="")
    tokens, _ = _route_line(
        cli_main, router, session=session, line="something ambiguous", tokens=["something", "ambiguous"]
    )
    assert tokens is None


def test_route_line_disables_router_on_model_unavailable(capsys):
    router = MagicMock()
    router.route = AsyncMock(side_effect=RoutingModelUnavailable("embeddinggemma not pulled"))
    tokens, returned_router = _route_line(
        cli_main, router, session=MagicMock(), line="add a feature", tokens=["add", "a", "feature"]
    )
    assert tokens is None
    assert returned_router is None
    assert "Disabling natural-language routing" in capsys.readouterr().out


def test_resolve_clarify_accepts_a_numeric_choice():
    session = MagicMock()
    session.prompt = MagicMock(return_value="1")
    assert _resolve_clarify(session, ["fix", "ask"]) == "fix"


def test_resolve_clarify_accepts_a_typed_command_name():
    session = MagicMock()
    session.prompt = MagicMock(return_value="ask")
    assert _resolve_clarify(session, ["fix", "ask"]) == "ask"


def test_resolve_clarify_cancels_on_empty_input():
    session = MagicMock()
    session.prompt = MagicMock(return_value="")
    assert _resolve_clarify(session, ["fix", "ask"]) is None


def test_resolve_clarify_cancels_on_invalid_choice(capsys):
    session = MagicMock()
    session.prompt = MagicMock(return_value="banana")
    assert _resolve_clarify(session, ["fix", "ask"]) is None
    assert "Not a valid choice" in capsys.readouterr().out
