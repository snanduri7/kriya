"""D1: the production sites that can run a registered tool's EFFECT are exactly two, and both go through the durable lifecycle. A third site (an alternate caller that would bypass
STARTED/COMPLETED, the gate and retention) fails here until it is classified.

    kriya/cli.py                          `kriya tools execute` -> RunRecord tool_execution (durable started/terminal)
    kriya/workflow/subtask_executor.py    a planned TOOL subtask -> execute_model_authored, inside the enforce loop's durable tool_started/completed

The effect-free authority path (`revalidate_model_authored` / `revalidate_tool_subtask_authority`) never reaches `_run_model_authored`."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = {("kriya/cli.py", "tool.execute"), ("kriya/workflow/subtask_executor.py", "execute_model_authored")}


def _effect_calls(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "execute" and isinstance(func.value, ast.Name) and func.value.id == "tool":
            found.append("tool.execute")
        elif isinstance(func, ast.Name) and func.id == "execute_model_authored":
            found.append("execute_model_authored")
        elif isinstance(func, ast.Attribute) and func.attr == "_run_model_authored":
            found.append("_run_model_authored")
    return found


def test_the_production_sites_that_run_a_tool_effect_are_exactly_the_two_lifecycle_owned_ones():
    sites = {(str(path.relative_to(ROOT)), call) for base in ("kriya", "plugins") for path in (ROOT / base).rglob("*.py") for call in _effect_calls(path)}
    # kriya/tools/tool.py is the definition site: execute_model_authored runs the effect it was handed, after authorize_model_authored
    sites -= {("kriya/tools/tool.py", "_run_model_authored")}
    assert sites == EXPECTED, f"a new production site can run a tool's effect outside the D1 lifecycle: {sorted(sites ^ EXPECTED)}"


def test_the_effect_free_revalidation_path_never_reaches_the_effect():
    source = (ROOT / "kriya/tools/tool.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    revalidate = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "revalidate_model_authored")
    called = {c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", None) for c in ast.walk(revalidate) if isinstance(c, ast.Call)}
    assert "_run_model_authored" not in called and "execute_model_authored" not in called and "execute" not in called
    executor = (ROOT / "kriya/workflow/subtask_executor.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(executor)) if isinstance(n, ast.FunctionDef) and n.name == "revalidate_tool_subtask_authority")
    names = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert "execute_model_authored" not in names and "revalidate_model_authored" in names
