"""The CLI command modules (full modularization E14; TARGET-MODULE-MAP §3.4, DEPENDENCY-RULES §2 ADAPTER-CLI).

``kriya/cli.py`` and ``kriya/repl.py`` import each other (a 2-module all-import cycle in ``baseline.json``). A command
group moved to ``kriya/cli_commands/`` is imported by ``kriya.cli`` to be registered on ``main``; if it imported
``kriya.cli`` or ``kriya.repl`` back (at any scope, function-local included) it would silently join that cycle.
``kriya.cli`` registers every group and keeps every moved name as the same object (the facade names tests and
``from kriya.cli import ...`` callers use)."""
import ast
import importlib
from pathlib import Path
from typing import Dict, List

import click

from . import module_map as mm

PACKAGE = mm.REPO / "kriya" / "cli_commands"
FORBIDDEN = ("kriya.cli", "kriya.repl")


def command_modules() -> List[str]:
    return sorted(f"kriya.cli_commands.{p.stem}" for p in PACKAGE.glob("*.py") if p.stem != "__init__")


def back_imports(trees: Dict[str, ast.AST]) -> List[str]:
    """Every import (any scope) of kriya.cli / kriya.repl, or of ``cli`` / ``repl`` from ``kriya``."""
    problems = []
    for module, tree in sorted(trees.items()):
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                targets = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
            else:
                continue
            for target in targets:
                if any(target == banned or target.startswith(banned + ".") for banned in FORBIDDEN):
                    problems.append(f"{module}:{node.lineno} -> {target}")
    return sorted(set(problems))


def test_the_command_modules_never_import_the_cli_or_the_repl():
    trees = {("kriya.cli_commands" if path.stem == "__init__" else f"kriya.cli_commands.{path.stem}"):
             ast.parse(path.read_text(encoding="utf-8")) for path in PACKAGE.glob("*.py")}
    assert command_modules(), "kriya/cli_commands has no command module"
    assert back_imports(trees) == []


def test_the_back_import_rule_catches_every_form():
    planted = {"kriya.cli_commands.x": ast.parse(
        "import kriya.cli\nfrom kriya import cli\n\ndef f():\n    from kriya.repl import run_repl\n    import kriya.repl as r\n")}
    assert back_imports(planted) == [
        "kriya.cli_commands.x:1 -> kriya.cli", "kriya.cli_commands.x:2 -> kriya.cli",
        "kriya.cli_commands.x:5 -> kriya.repl", "kriya.cli_commands.x:5 -> kriya.repl.run_repl",
        "kriya.cli_commands.x:6 -> kriya.repl"]


def test_every_moved_group_is_registered_on_main_and_every_moved_name_stays_on_kriya_cli():
    cli = importlib.import_module("kriya.cli")
    groups = 0
    for module_name in command_modules():
        module = importlib.import_module(module_name)
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
        names += [t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)]
        for name in names:
            assert getattr(cli, name) is getattr(module, name), f"kriya.cli.{name} is not {module_name}.{name}"
            obj = getattr(module, name)
            if isinstance(obj, click.Group):
                assert cli.main.commands[obj.name] is obj, f"{module_name}.{name} is not registered on main"
                groups += 1
    assert groups == len([m for m in command_modules() if m != "kriya.cli_commands.common"])  # one group per command module
