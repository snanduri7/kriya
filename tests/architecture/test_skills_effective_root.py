"""One effective skills root per run (registry: GIT-MIDRUN-COMMITS-S3-001).

While an enforce run's shared `SkillEngine` shadows the skills root (kriya/skills/run_shadow.py), EVERY same-run reader or writer of
skills must go through the engine's effective root; one that read `config.paths.skills` directly would split the knowledge in two
(some components on the canonical disk inside the bound candidate repository, others on the shadow). This guard inventories every
production read of `<config>.paths.skills` and requires each to be an explained exception, and the same-run stage readers to
route through `SkillEngine.effective_skills_dir` / the unit's `ctx.skills_dir`.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "kriya"

# (file, enclosing function) -> why it may read the configured root directly; "routes" = the function must also use the effective root
ALLOWED = {
    ("kriya/workflow/stage_knowledge_guard.py", "check_knowledge_guard"): ("routes", "effective_skills_dir"),
    ("kriya/workflow/stage_analysis.py", "analyze_work_unit"): ("routes", "effective_skills_dir"),
    ("kriya/workflow/stage_apply.py", "extract_lessons"): ("routes", "skills_dir"),                 # ctx.skills_dir, the analysis stage's effective root
    ("kriya/workflow/workflow_controller.py", "WorkflowController.execute"): (
        "pre-bind", "the controller's KnowledgeGuard goal check and the creation of the run's shared engine happen BEFORE the plan "
                    "sandbox exists (the shadow is activated at the sandbox, in EnforceLoop.run); the guard only reads"),
    ("kriya/workflow/gate_orchestrator.py", "_apply_candidate_pom_corrections"): (
        "candidate-fact", "the skills directory's path RELATIVE TO THE WORKSPACE, used to keep Maven source-root coverage correct for the "
                          "candidate's layout; a fact about the candidate, not skills I/O (pointing it at a state-directory shadow would corrupt it)"),
    ("kriya/skills/skill.py", "SkillEngine.from_config"): (
        "engine-construction", "the one place an engine learns its CANONICAL root from the config; the shadow is layered over it by the engine itself"),
    ("kriya/workflow/context_certification.py", "_index_repository"): (
        "assignment", "builds an isolated certification config; assigns the field, never reads skills"),
}
CLI_FILES = {"kriya/cli.py"}                 # human commands, never inside a generation run
CONFIG_PACKAGE = "kriya/config/"


def _reads():
    found = []

    class Visitor(ast.NodeVisitor):
        def __init__(self, relative, source):
            self.relative, self.stack, self.source = relative, [], source

        def _scope(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

        def visit_Attribute(self, node):
            if node.attr == "skills" and isinstance(node.value, ast.Attribute) and node.value.attr == "paths":
                found.append((self.relative, ".".join(self.stack), node.lineno, isinstance(node.ctx, ast.Load)))
            self.generic_visit(node)

    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        Visitor(relative, source).visit(ast.parse(source))
    return found


def _function_source(relative, qualified):
    source = (ROOT / relative).read_text(encoding="utf-8")
    tree, wanted = ast.parse(source), qualified.split(".")

    def find(nodes, names):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == names[0]:
                return node if len(names) == 1 else find(node.body, names[1:])
        return None
    node = find(tree.body, wanted)
    return ast.get_source_segment(source, node) if node is not None else None


def test_every_production_read_of_the_configured_skills_root_is_an_explained_exception():
    unexplained = []
    for relative, function, line, _load in _reads():
        if relative in CLI_FILES or relative.startswith(CONFIG_PACKAGE):
            continue
        if (relative, function) not in ALLOWED:
            unexplained.append(f"{relative}:{line} in {function or '<module>'}")
    assert unexplained == [], (
        "a production module reads `paths.skills` directly. While an enforce run shadows the skills root, same-run skills I/O must use "
        "SkillEngine.effective_skills_dir (or ctx.skills_dir); add the site to ALLOWED with its reason if it is genuinely not skills I/O: "
        + "; ".join(unexplained))
    present = {(relative, function) for relative, function, _, _ in _reads()}
    assert sorted(set(ALLOWED) - present) == [], "ALLOWED lists a site that no longer reads paths.skills: remove it"


def test_the_same_run_stage_readers_route_through_the_effective_root():
    for (relative, function), (kind, token) in ALLOWED.items():
        if kind != "routes":
            continue
        text = _function_source(relative, function)
        assert text is not None and token in text, f"{relative}:{function} reads paths.skills but does not route through `{token}`"
