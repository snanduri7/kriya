"""Interactive session (`kriya repl`): a persistent process you can issue several
commands into in a row, instead of restarting the CLI for each one.

Deliberately thin - every typed line is dispatched straight into the same Click
command group (`kriya.cli.main`) the regular one-shot CLI already uses, via
Click's own documented programmatic-invocation support (`standalone_mode=False`).
There is no separate command parser to keep in sync and no duplicated command
logic: `generate "goal" -y` here is the exact same code path as `kriya generate
"goal" -y` from a shell. The only things this module adds are the loop itself,
the boxed prompt, and a handful of session-only meta-commands.
"""
import shlex
from typing import Optional

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style

_HELP_TEXT = """\
Type any normal Kriya command without the leading "kriya" - e.g.:
  generate "add a health check endpoint" -y
  ask "how does the retry loop work?"
  analyze .
  skills list

Type "/" to see every command Kriya supports, live, as you type.

Your --config (if you started the session with one) is applied automatically
to every command unless you pass your own -c/--config inline.

Session-only commands:
  /help            Show this message.
  /clear           Clear the screen.
  /exit, /quit     End the session (Ctrl-D also works).
"""

_STYLE = Style.from_dict({"prompt-border": "#5f87d7 bold"})

_EXIT_COMMANDS = {"/exit", "/quit"}


def _print_banner(config_path: Optional[str]) -> None:
    from kriya import __version__

    title = f"Kriya {__version__} interactive session"
    body_lines = [
        f"config: {config_path or '(default resolution)'}",
        "Type /help for session commands, /exit to quit.",
    ]
    # Top/bottom border length is derived from whichever line is longest -
    # title or body - so the box stays a clean rectangle regardless of
    # version-string length or how long a --config path is.
    width = max(len(title) + 3, *(len(line) + 3 for line in body_lines))

    click.secho(f"╭─ {title} " + "─" * (width - len(title) - 3), fg="blue")
    for line in body_lines:
        click.secho(f"│  {line}", fg="blue")
    click.secho("╰" + "─" * width, fg="blue")


_META_COMMANDS = [
    ("/help", "Show session help"),
    ("/clear", "Clear the screen"),
    ("/exit", "End the session"),
    ("/quit", "End the session"),
]


class _SlashCommandCompleter(Completer):
    """Typing "/" lists every command Kriya supports, live, as you type -
    session-only meta-commands (kept WITH their leading "/", since that's how
    they're actually invoked) plus every real CLI command (inserted WITHOUT a
    "/", since that's how those are invoked) enumerated straight from the same
    Click group the REPL dispatches into - no separate list to keep in sync.
    "repl" itself is excluded: starting a session from inside a session isn't
    a real use case and isn't worth the nested-event-loop question."""

    def __init__(self, cli_main: click.Group):
        self._commands = [
            (name, (cmd.get_short_help_str(limit=60) or "").strip())
            for name, cmd in sorted(cli_main.commands.items())
            if not getattr(cmd, "hidden", False) and name != "repl"
        ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return
        typed = text[1:].lower()

        for insert, desc in _META_COMMANDS:
            if insert[1:].startswith(typed):
                yield Completion(insert, start_position=-len(text), display=insert, display_meta=desc)

        for name, desc in self._commands:
            if name.lower().startswith(typed):
                yield Completion(name, start_position=-len(text), display=name, display_meta=desc)


def _inject_config(tokens: list, config_path: Optional[str]) -> list:
    """Prepends -c <config_path> to a parsed command line, unless the user
    already supplied their own -c/--config or the session itself started with
    no config (default resolution applies, same as running the CLI bare)."""
    if not config_path or any(t in ("-c", "--config") for t in tokens):
        return tokens
    return ["-c", config_path] + tokens


def _dispatch(cli_main: click.Group, tokens: list) -> None:
    """Runs one parsed command through the real CLI group in-process. Click's
    own subcommands call sys.exit(0) on success in standalone mode; with
    standalone_mode=False that surfaces as click.exceptions.Exit instead of
    tearing down the whole REPL process, which is exactly what we want here -
    every other exception is a genuine command failure and gets reported
    without ending the session."""
    try:
        cli_main.main(args=tokens, prog_name="kriya", standalone_mode=False)
    except click.exceptions.Exit:
        pass
    except click.ClickException as e:
        e.show()
    except (EOFError, KeyboardInterrupt):
        raise
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")


def run_repl(config_path: Optional[str]) -> None:
    """Entry point for `kriya repl`."""
    from kriya.cli import main as cli_main

    _print_banner(config_path)
    session = PromptSession(
        history=InMemoryHistory(),
        completer=_SlashCommandCompleter(cli_main),
        complete_while_typing=True,
    )

    while True:
        try:
            line = session.prompt(
                HTML("<prompt-border>╭─ kriya\n╰─></prompt-border> "),
                style=_STYLE,
            )
        except EOFError:
            click.echo("")
            break
        except KeyboardInterrupt:
            continue

        line = line.strip()
        if not line:
            continue
        if line in _EXIT_COMMANDS:
            break
        if line == "/help":
            click.echo(_HELP_TEXT)
            continue
        if line == "/clear":
            click.clear()
            continue

        try:
            tokens = shlex.split(line)
        except ValueError as e:
            click.secho(f"Could not parse that line: {e}", fg="red")
            continue
        if not tokens:
            continue

        tokens = _inject_config(tokens, config_path)

        try:
            _dispatch(cli_main, tokens)
        except (EOFError, KeyboardInterrupt):
            click.echo("")
            break

    click.echo("Goodbye.")
