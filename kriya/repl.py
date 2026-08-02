"""Interactive session (`kriya repl`): a persistent process you can issue several
commands into in a row, instead of restarting the CLI for each one.

Deliberately thin - every typed line is dispatched straight into the same Click
command group (`kriya.cli.main`) the regular one-shot CLI already uses, via
Click's own documented programmatic-invocation support (`standalone_mode=False`).
There is no separate command parser to keep in sync and no duplicated command
logic: `generate "goal" -y` here is the exact same code path as `kriya generate
"goal" -y` from a shell. The only things this module adds are the loop itself,
the boxed prompt, a handful of session-only meta-commands, and (when
config.routing.enabled) natural-language routing for lines that aren't an
explicit command - see kriya/routing.py.
"""
import asyncio
import shlex
from typing import List, Optional

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style

from kriya.config.config import load_config
from kriya.routing import (
    CLARIFY,
    UNROUTABLE,
    Router,
    RoutingModelUnavailable,
    build_dispatch_tokens,
)

_HELP_TEXT_BASE = """\
Type any normal Kriya command without the leading "kriya" - e.g.:
  generate "add a health check endpoint" -y
  ask "how does the retry loop work?"
  analyze .
  skills list

Not sure how to phrase a goal? Draft one first, review it, then build it:
  prompt generate "a REST API for managing a todo list"
  generate --file .kriya/last_prompt.md -y

Type "/" to see every command Kriya supports, live, as you type.

Your --config (if you started the session with one) is applied automatically
to every command unless you pass your own -c/--config inline.

Session-only commands:
  /help            Show this message.
  /clear           Clear the screen.
  /exit, /quit     End the session (Ctrl-D also works).
"""

_HELP_TEXT_ROUTING_ADDENDUM = """
Natural-language routing is on (config.routing.enabled) - you can also just
type what you want in plain English, e.g. "why is this test flaky" instead
of `ask "why is this test flaky"`. Kriya will pick a command, ask if it's
not sure between two, or say so if it's not something Kriya does.
"""

# Short, human-facing description per routable command - shown when Kriya
# needs to ask which one you meant (routing.py's CLARIFY outcome).
_COMMAND_DESCRIPTIONS = {
    "generate": "write/add new code, tests, or config files",
    "ask": "answer a question about how the repo works",
    "fix": "repair a specific error, bug, or failing test",
    "review": "review already-written code/diff for issues",
    "analyze": "summarize the structure of the repo",
    "skills": "list/show what Kriya knows about a technology",
}


def _help_text(routing_enabled: bool) -> str:
    return _HELP_TEXT_BASE + (_HELP_TEXT_ROUTING_ADDENDUM if routing_enabled else "")

_STYLE = Style.from_dict({"prompt-border": "#5f87d7 bold"})

_EXIT_COMMANDS = {"/exit", "/quit"}


def _print_banner(config_path: Optional[str], routing_enabled: bool) -> None:
    from kriya import __version__

    title = f"Kriya {__version__} interactive session"
    body_lines = [
        f"config: {config_path or '(default resolution)'}",
        "Type /help for session commands, /exit to quit.",
        (
            "Type your request in plain English, or an exact command."
            if routing_enabled else
            'Wrap a full request in quotes, e.g. generate "..." -y.'
        ),
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


def _resolve_clarify(session: PromptSession, candidates: List[str]) -> Optional[str]:
    """Presents the two commands routing.py couldn't distinguish between and
    asks the user to pick, rather than silently guessing (see kriya/routing.py
    - this was the single biggest lever separating a wrong guess from a safe
    outcome in the spike this feature was validated against). Returns None on
    anything other than a clear numeric/name choice - never forces a pick."""
    click.secho("Not sure which you meant:", fg="yellow")
    for i, command in enumerate(candidates, start=1):
        desc = _COMMAND_DESCRIPTIONS.get(command, "")
        click.secho(f"  [{i}] {command:<10} {desc}", fg="yellow")
    try:
        choice = session.prompt("Pick a number, or press Enter to cancel: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not choice:
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(candidates):
        return candidates[int(choice) - 1]
    if choice in candidates:
        return choice
    click.secho("Not a valid choice - cancelled.", fg="red")
    return None


def _route_line(cli_main: click.Group, router: Optional[Router], session: PromptSession, line: str, tokens: list):
    """Decides what to actually dispatch for one input line. If `tokens[0]`
    is a known command (or routing is off), returns tokens unchanged - the
    fast, deterministic Version A path, untouched by any of this. Otherwise
    asks routing.py's Router to classify the line and returns either the
    dispatch tokens for the resolved command, or None (nothing to dispatch -
    already reported to the user directly, e.g. out-of-scope or a cancelled
    clarification).

    Returns (tokens_or_None, router) - router comes back None if a
    RoutingModelUnavailable error disabled it for the rest of the session."""
    if tokens[0] in cli_main.commands or router is None:
        return tokens, router

    try:
        result = asyncio.run(router.route(line))
    except RoutingModelUnavailable as e:
        click.secho(str(e), fg="red")
        click.secho("Disabling natural-language routing for the rest of this session.", fg="red")
        return None, None

    if result.label == UNROUTABLE:
        click.secho(
            "I don't think that's something I can do - I write/fix/review/analyze "
            "code and manage skills, but I don't run commands, install packages, "
            "or touch live infrastructure. Type /help to see what I can do.",
            fg="yellow",
        )
        return None, router

    if result.label == CLARIFY:
        chosen = _resolve_clarify(session, result.candidates or [])
        if chosen is None:
            return None, router
        click.secho(f"-> routed to: {chosen}", fg="blue", dim=True)
        return build_dispatch_tokens(chosen, line), router

    click.secho(f"-> routed to: {result.label}", fg="blue", dim=True)
    return build_dispatch_tokens(result.label, line), router


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
    except click.exceptions.UsageError as e:
        # Covers "No such command" (first word isn't a real command) and
        # "unexpected extra argument(s)"/"missing argument" (first word IS a
        # real command, like "generate", but the rest reads as a plain-English
        # sentence rather than that command's actual arguments) - the latter
        # bypasses natural-language routing entirely even when it's enabled,
        # since _route_line only routes lines whose first word ISN'T already a
        # real command name. Click's own specific error is still shown first;
        # this is an addendum, not a replacement, so a genuine typo on an
        # already-valid command (e.g. a bad flag) still shows its real reason.
        e.show()
        click.secho(
            "(Tip: Kriya commands need exact syntax - wrap a full request in "
            'quotes, e.g. generate "..." -y, so it\'s passed as one argument.)',
            fg="yellow", dim=True, err=True,
        )
    except click.ClickException as e:
        e.show()
    except (EOFError, KeyboardInterrupt):
        raise
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")


def run_repl(config_path: Optional[str]) -> None:
    """Entry point for `kriya repl`."""
    from kriya.cli import main as cli_main

    cfg = load_config(config_path)
    router: Optional[Router] = Router(cfg) if cfg.routing.enabled else None

    _print_banner(config_path, router is not None)
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
            click.echo(_help_text(router is not None))
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

        try:
            tokens, router = _route_line(cli_main, router, session, line, tokens)
        except (EOFError, KeyboardInterrupt):
            click.echo("")
            break
        if tokens is None:
            continue

        tokens = _inject_config(tokens, config_path)

        try:
            _dispatch(cli_main, tokens)
        except (EOFError, KeyboardInterrupt):
            click.echo("")
            break

    click.echo("Goodbye.")
