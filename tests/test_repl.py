from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from kriya.cli import main as cli_main
from kriya.repl import _dispatch, _inject_config, _SlashCommandCompleter


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
