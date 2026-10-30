"""Static census of every production site that can mutate a Git work tree a run is bound to (registry: GIT-MIDRUN-COMMITS-S3-001).

S3 binds a contained gate to one Git identity (the mounted checkout's HEAD must equal the run's original workspace HEAD). So every
production site that can move a repository's HEAD or rewrite a work tree must be CLASSIFIED, and a newly introduced unclassified
site fails here. The classification is not inferred from "a call exists": each row records WHO mutates, WHERE, UNDER WHAT CONDITION
the target is the bound repository, whether it writes a file / moves HEAD, whether it is reachable after the first bind and
before the terminal verification (per run mode), whether a bound gate follows, whether a later part of the same run reads what it
produced, its semantic class and its disposition.

The dynamic half is tests/test_midrun_workspace_mutations.py, which measures the `writes_file` / `moves_head` columns by running
the real writers against a real git workspace and checks the reachability columns on real standalone and enforce runs.

Two scans define the inventory, so a new site cannot hide:
  * CALLERS - every production call of a skill/knowledge writer (names below) -> key (file, enclosing function, callee);
  * GIT VERBS - every list literal in production code that is a git argv with a history/tree-mutating verb -> key
    (file, enclosing function, verb).
"""
import ast
import re
from pathlib import Path
from typing import Dict, NamedTuple, Set, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "kriya"

WRITER_CALLEES = frozenset({
    "_set_skill_yaml_field", "_set_skill_yaml_fields", "create_skill_skeleton", "record_conflict_resolution", "record_rule_provenance",
    "mark_rules_verified", "mark_verified", "mark_unverified", "mark_gap_acknowledged", "_write_skill_extraction",
    "_stage_skill_conflicts", "stage_facts", "promote_staged", "record_direct_fact", "apply_held_verifications",
    "git_commit_if_tracked", "git_commit_paths", "generate_skill_template", "persist_run_learning",
})
GIT_VERBS = frozenset({"commit", "reset", "checkout", "merge", "rebase", "cherry-pick", "update-ref", "am", "stash", "commit-tree", "apply"})


class Site(NamedTuple):
    owner: str                  # the production owner of the mutation
    target: str                 # the path / repository it mutates
    inside_bound_when: str      # the condition under which the target is the bound candidate repository
    writes_file: bool           # a file is written before the commit
    moves_head: bool            # the call commits (moves HEAD of the target work tree)
    standalone: str             # reachability after the first bind, before terminal verification, in a standalone run
    enforce: str                # the same in an enforce run (plan-level sandbox)
    later_bound_gate: str       # whether a bound gate follows the site in that window
    same_run_reader: str        # who in the same run reads what the site produced ("none" or the reader)
    semantic: str               # CANDIDATE | LEARNING | BOOKKEEPING | VERIFICATION | CLI | FUNNEL | SANDBOX
    disposition: str            # SAFE | DEFER_BOTH | RUN_LOCAL_DEFER_DURABILITY | CANDIDATE_MUTATION | CHAIN | NO_CALLER
    status: str                 # closed | open (an open row is the defect GIT-MIDRUN-COMMITS-S3-001: a learning write reachable in the bound window that the run-local shadow does not cover)
    note: str = ""


SKILLS_IN_REPO = "paths.skills resolves inside the run's workspace repository (the default ./skills, or any path under it)"
NEVER_IN_RUN = "n/a: a CLI command, never reached from a generation run"
PRE_SANDBOX = "no: runs in stages 1-3, before the Developer loop creates the sandbox (development_loop.py create_git_worktree)"
ENFORCE_WINDOW = "yes: runs inside a subtask after the plan-level sandbox exists (enforce_loop.py create_git_worktree) and before the terminal gates"
TERMINAL_GATES = "yes: the terminal gates mount the plan sandbox against the original workspace (original_workspace_path=workspace_path)"
POST_TERMINAL = "no: runs only after the terminal gates passed and the candidate was committed"

SHADOWED = ("Closed by the run-local shadow (kriya/skills/run_shadow.py): in an enforce run the shared SkillEngine works on a copy of the "
             "skills root, flushed after the terminal commit; the dynamic detector drives this writer through a real enforce run.")
_LEARNING_ENFORCE = dict(standalone=PRE_SANDBOX, enforce=ENFORCE_WINDOW, later_bound_gate=TERMINAL_GATES)

CALLER_SITES: Dict[Tuple[str, str, str], Site] = {
    # --- the in-run mutation owners (the stage code that decides to write) -------------------------------------------------
    ("kriya/workflow/stage_analysis.py", "analyze_work_unit", "create_skill_skeleton"): Site(
        "stage 1.2 skill-gap bootstrap of a new skill", "<skills>/<new skill>/ (3 files)", SKILLS_IN_REPO, True, True,
        semantic="LEARNING", same_run_reader="the skill registry reload: the new skill is matched and extended by later work units",
        disposition="RUN_LOCAL_DEFER_DURABILITY", status="closed", **_LEARNING_ENFORCE),
    ("kriya/workflow/stage_analysis.py", "analyze_work_unit", "mark_gap_acknowledged"): Site(
        "stage 1.2 skill-gap decline", "none: held by the engine for the run (W3-1)", SKILLS_IN_REPO, False, False,
        semantic="LEARNING", same_run_reader="the gap check of later work units (not se.is_gap_acknowledged(s))",
        disposition="RUN_LOCAL_DEFER_DURABILITY", status="closed",
        note="W3-1 owner decision: a skill inside the repository is project-supplied, so its decline is run-local and is NEVER made durable "
             "(the next run asks again); the 'defer durability' of this vocabulary therefore means 'dropped by design' here. "
             "Nothing is written or committed, so it cannot move the bound HEAD.", **_LEARNING_ENFORCE),
    ("kriya/workflow/stage_analysis.py", "analyze_work_unit", "_write_skill_extraction"): Site(
        "stage 1.2 skill-gap extraction", "<skills>/<skill>/rules.txt, examples/, rule provenance", SKILLS_IN_REPO, True, True,
        semantic="LEARNING", same_run_reader="the skill registry reload and the unverified-rule labelling of later work units",
        disposition="RUN_LOCAL_DEFER_DURABILITY", status="closed", **_LEARNING_ENFORCE),
    ("kriya/workflow/stage_analysis.py", "analyze_work_unit", "_stage_skill_conflicts"): Site(
        "stage 1.2 flagged conflicting candidate rules", "<skills>/<skill>/staged_rules.txt", SKILLS_IN_REPO, True, True,
        semantic="LEARNING", same_run_reader="none (staged_rules.txt is read only by `kriya skills` display and the resume fingerprint, which ignores it)",
        disposition="DEFER_BOTH", status="closed", **_LEARNING_ENFORCE),
    ("kriya/workflow/stage_analysis.py", "analyze_work_unit", "record_conflict_resolution"): Site(
        "stage 1.3 human conflict decision", "<skills>/.skill_conflicts.json", SKILLS_IN_REPO, True, True,
        semantic="LEARNING", same_run_reader="find_conflict_resolution in later work units (so the human is not asked twice)",
        disposition="RUN_LOCAL_DEFER_DURABILITY", status="closed", **_LEARNING_ENFORCE),
    ("kriya/workflow/stage_architecture.py", "design_work_unit", "create_skill_skeleton"): Site(
        "stage 2B design-time skill bootstrap", "<skills>/<new skill>/ (3 files)", SKILLS_IN_REPO, True, True,
        semantic="LEARNING", same_run_reader="the skill registry reload", disposition="RUN_LOCAL_DEFER_DURABILITY", status="closed",
        **_LEARNING_ENFORCE),
    ("kriya/workflow/stage_architecture.py", "design_work_unit", "_write_skill_extraction"): Site(
        "stage 2B design-time skill extraction", "<skills>/<skill>/rules.txt, examples/, rule provenance", SKILLS_IN_REPO, True, True,
        semantic="LEARNING", same_run_reader="the in-prompt skills block of this work unit and the registry reload for later ones",
        disposition="RUN_LOCAL_DEFER_DURABILITY", status="closed", **_LEARNING_ENFORCE),
    ("kriya/workflow/stage_architecture.py", "design_work_unit", "_stage_skill_conflicts"): Site(
        "stage 2B flagged conflicting candidate rules", "<skills>/<skill>/staged_rules.txt", SKILLS_IN_REPO, True, True,
        semantic="LEARNING", same_run_reader="none (see stage_analysis)", disposition="DEFER_BOTH", status="closed", **_LEARNING_ENFORCE),
    ("kriya/workflow/stage_apply.py", "extract_lessons", "stage_facts"): Site(
        "stage 7 advisory lesson staging", "<skills>/auto-<repo>/staged_knowledge.json", SKILLS_IN_REPO, True, False,
        standalone=POST_TERMINAL, enforce="yes: runs at the end of each subtask, between subtasks and before the terminal gates",
        later_bound_gate="yes in enforce (a file write, no commit: HEAD does not move, the binding holds; the workspace content moves)",
        semantic="LEARNING", same_run_reader="none (staged knowledge awaits human approval via `kriya skills approve`)",
        disposition="DEFER_BOTH", status="closed",
        note="No HEAD change, so S3 is not tripped; deferred anyway so a run writes no workspace content before its terminal verification."),
    ("kriya/workflow/stage_apply.py", "prepare_lessons", "generate_skill_template"): Site(
        "stage 7 auto-accrual: scaffold a skill for a resolved dependency", "<skills>/<library>-<version>/ (skill.yaml, rules.txt, instructions)",
        SKILLS_IN_REPO, True, False, standalone=POST_TERMINAL,
        enforce="yes: runs at the end of each subtask, between subtasks and before the terminal gates",
        later_bound_gate="yes in enforce (files written, no commit: HEAD does not move; the workspace content moves)",
        semantic="LEARNING", same_run_reader="the skill registry reload: the new skill is matched by later work units",
        disposition="RUN_LOCAL_DEFER_DURABILITY", status="closed",
        note="Found by the shadow work (it was missing from the first census, which only matched committing writers). " + SHADOWED),
    ("kriya/cli.py", "_generate_impl.run_workflow", "generate_skill_template"): Site(
        "`kriya generate` knowledge-gap resolution (a human confirms the scaffold)", "<skills>/<library>-<version>/", SKILLS_IN_REPO, True, False,
        NEVER_IN_RUN, NEVER_IN_RUN, "no", "none", "CLI", "SAFE", "closed", "Before the run starts."),
    # --- the verification promotion (P-B) ---------------------------------------------------------------------------------
    ("kriya/workflow/workflow.py", "WorkflowEngine.run_generation_workflow", "apply_held_verifications"): Site(
        "P-B: standalone run after the terminal apply", "<skills>/<skill>/skill.yaml + rule provenance", SKILLS_IN_REPO, True, True,
        standalone=POST_TERMINAL, enforce="n/a: a controller-owned run only hands over", later_bound_gate="no",
        semantic="VERIFICATION", same_run_reader="none after the flush", disposition="SAFE", status="closed"),
    ("kriya/workflow/enforce_loop.py", "EnforceLoop.run", "persist_run_learning"): Site(
        "enforce run after the terminal commit: held verifications + the run-local shadow's flush", "<skills>/ (the run's delta, one commit)",
        SKILLS_IN_REPO, True, True, standalone="n/a", enforce=POST_TERMINAL, later_bound_gate="no", semantic="VERIFICATION",
        same_run_reader="none after the flush", disposition="SAFE", status="closed",
        note="The ONLY place an enforce run's learning becomes durable. Reached only when workspace_commit_completed; "
             "otherwise discard_run_learning drops the shadow and the held verifications."),
    # --- chains (the skill subsystem's own internal calls; every public entry is classified above or below) -----------------
    ("kriya/skills/skill.py", "SkillEngine.persist_run_learning", "apply_held_verifications"): Site(
        "SkillEngine", "<skills>/<skill>/skill.yaml + rule provenance (into the shadow when one is active)", SKILLS_IN_REPO, True, False,
        POST_TERMINAL, POST_TERMINAL, "no", "none", "VERIFICATION", "SAFE", "closed",
        "With a shadow active the writes land in the copy and the commit helper is a no-op there (the copy is outside every work tree)."),
    ("kriya/skills/skill.py", "SkillEngine._set_skill_yaml_field", "_set_skill_yaml_fields"): Site(
        "SkillEngine", "<skills>/<skill>/skill.yaml", SKILLS_IN_REPO, True, True, "see callers", "see callers", "see callers",
        "see callers", "FUNNEL", "CHAIN", "closed", "Reached from mark_gap_acknowledged (a stage_analysis row) and nothing else."),
    ("kriya/skills/skill.py", "SkillEngine._set_skill_yaml_fields", "git_commit_if_tracked"): Site(
        "SkillEngine", "<skills>/<skill>/skill.yaml", SKILLS_IN_REPO, True, True, "see callers", "see callers", "see callers",
        "see callers", "FUNNEL", "CHAIN", "closed", "Reached from mark_verified / mark_unverified / _set_skill_yaml_field."),
    ("kriya/skills/skill.py", "SkillEngine.mark_gap_acknowledged", "_set_skill_yaml_field"): Site(
        "SkillEngine", "<skills>/<skill>/skill.yaml", SKILLS_IN_REPO, True, True, "see callers", "see callers", "see callers",
        "see callers", "FUNNEL", "CHAIN", "closed",
        "Reached only for a skill OUTSIDE the workspace (operator-owned), which is outside the bound repository; a workspace skill never gets here (W3-1). Called by stage_analysis (classified) and the CLI."),
    ("kriya/skills/skill.py", "SkillEngine.mark_verified", "_set_skill_yaml_fields"): Site(
        "SkillEngine", "<skills>/<skill>/skill.yaml", SKILLS_IN_REPO, True, True, "see callers", "see callers", "see callers",
        "see callers", "FUNNEL", "CHAIN", "closed", "Called by apply_held_verifications (post-terminal) and the CLI promote."),
    ("kriya/skills/skill.py", "SkillEngine.mark_unverified", "_set_skill_yaml_fields"): Site(
        "SkillEngine", "<skills>/<skill>/skill.yaml", SKILLS_IN_REPO, True, True, NEVER_IN_RUN, NEVER_IN_RUN, "no", "none",
        "CLI", "CHAIN", "closed", "Only `kriya skills unverify` (a CLI row) calls it."),
    ("kriya/skills/skill.py", "SkillEngine.apply_held_verifications", "mark_verified"): Site(
        "SkillEngine", "<skills>/<skill>/skill.yaml", SKILLS_IN_REPO, True, True, POST_TERMINAL, POST_TERMINAL, "no", "none",
        "VERIFICATION", "SAFE", "closed", "The only in-run caller of mark_verified (P-B)."),
    ("kriya/skills/skill.py", "SkillEngine.apply_held_verifications", "mark_rules_verified"): Site(
        "SkillEngine", "<skills>/<skill>/rule provenance", SKILLS_IN_REPO, True, True, POST_TERMINAL, POST_TERMINAL, "no", "none",
        "VERIFICATION", "SAFE", "closed", "The only in-run caller of mark_rules_verified (P-B)."),
    ("kriya/skills/skill.py", "SkillEngine.create_skill_skeleton", "git_commit_if_tracked"): Site(
        "SkillEngine", "<skills>/<new skill>/", SKILLS_IN_REPO, True, True, "see callers", "see callers", "see callers", "see callers",
        "FUNNEL", "CHAIN", "closed", "Reached from the two stage rows above and `kriya skills create`."),
    ("kriya/skills/skill.py", "mark_rules_verified", "git_commit_if_tracked"): Site(
        "skills subsystem", "<skills>/<skill>/rule provenance", SKILLS_IN_REPO, True, True, "see callers", "see callers", "see callers",
        "see callers", "FUNNEL", "CHAIN", "closed", "Reached only from apply_held_verifications."),
    ("kriya/skills/skill.py", "record_conflict_resolution", "git_commit_if_tracked"): Site(
        "skills subsystem", "<skills>/.skill_conflicts.json", SKILLS_IN_REPO, True, True, "see callers", "see callers", "see callers",
        "see callers", "FUNNEL", "CHAIN", "closed", "Reached only from stage_analysis (classified)."),
    ("kriya/skills/skill.py", "record_rule_provenance", "git_commit_if_tracked"): Site(
        "skills subsystem", "<skills>/<skill>/rule provenance", SKILLS_IN_REPO, True, True, "see callers", "see callers", "see callers",
        "see callers", "FUNNEL", "CHAIN", "closed", "Reached from _write_skill_extraction (classified) and the CLI-only knowledge staging."),
    ("kriya/workflow/skill_extraction.py", "_write_skill_extraction", "git_commit_if_tracked"): Site(
        "stage 1.2 / 2B extraction writer", "<skills>/<skill>/rules.txt, examples/", SKILLS_IN_REPO, True, True, "see callers", "see callers",
        "see callers", "see callers", "FUNNEL", "CHAIN", "closed", "Reached from the stage_analysis / stage_architecture rows."),
    ("kriya/workflow/skill_extraction.py", "_write_skill_extraction", "record_rule_provenance"): Site(
        "stage 1.2 / 2B extraction writer", "<skills>/<skill>/rule provenance", SKILLS_IN_REPO, True, True, "see callers", "see callers",
        "see callers", "see callers", "FUNNEL", "CHAIN", "closed", "Reached from the stage_analysis / stage_architecture rows."),
    ("kriya/workflow/skill_extraction.py", "_stage_skill_conflicts", "git_commit_if_tracked"): Site(
        "stage 1.2 / 2B conflict staging writer", "<skills>/<skill>/staged_rules.txt", SKILLS_IN_REPO, True, True, "see callers", "see callers",
        "see callers", "see callers", "FUNNEL", "CHAIN", "closed", "Reached from the stage_analysis / stage_architecture rows."),
    # --- CLI-only (a human command, never inside a generation run) ----------------------------------------------------------
    ("kriya/cli.py", "skills_approve", "promote_staged"): Site(
        "`kriya skills approve`", "<skills>/<skill>/rules.txt + knowledge", SKILLS_IN_REPO, True, True, NEVER_IN_RUN, NEVER_IN_RUN, "no",
        "none", "CLI", "SAFE", "closed"),
    ("kriya/cli.py", "skills_create", "create_skill_skeleton"): Site(
        "`kriya skills create`", "<skills>/<new skill>/", SKILLS_IN_REPO, True, True, NEVER_IN_RUN, NEVER_IN_RUN, "no", "none", "CLI",
        "SAFE", "closed"),
    ("kriya/cli.py", "skills_unverify", "mark_unverified"): Site(
        "`kriya skills unverify`", "<skills>/<skill>/skill.yaml", SKILLS_IN_REPO, True, True, NEVER_IN_RUN, NEVER_IN_RUN, "no", "none", "CLI",
        "SAFE", "closed"),
    ("kriya/cli.py", "skills_promote", "git_commit_if_tracked"): Site(
        "`kriya skills promote`", "<skills>/<skill>/rules.txt", SKILLS_IN_REPO, True, True, NEVER_IN_RUN, NEVER_IN_RUN, "no", "none", "CLI",
        "SAFE", "closed"),
    ("kriya/cli.py", "skills_promote", "mark_verified"): Site(
        "`kriya skills promote`", "<skills>/<skill>/skill.yaml", SKILLS_IN_REPO, True, True, NEVER_IN_RUN, NEVER_IN_RUN, "no", "none", "CLI",
        "SAFE", "closed"),
    ("kriya/knowledge/scaffold.py", "record_scaffold_answer", "record_direct_fact"): Site(
        "`kriya skills scaffold`", "<skills>/<skill>/rules.txt + knowledge", SKILLS_IN_REPO, True, True, NEVER_IN_RUN, NEVER_IN_RUN, "no",
        "none", "CLI", "SAFE", "closed"),
    ("kriya/knowledge/staging.py", "promote_staged", "git_commit_if_tracked"): Site(
        "knowledge staging (human approval)", "<skills>/<skill>/rules.txt + knowledge", SKILLS_IN_REPO, True, True, NEVER_IN_RUN, NEVER_IN_RUN,
        "no", "none", "CLI", "SAFE", "closed", "Reached only from `kriya skills approve`."),
    ("kriya/knowledge/staging.py", "promote_staged", "record_rule_provenance"): Site(
        "knowledge staging (human approval)", "<skills>/<skill>/rule provenance", SKILLS_IN_REPO, True, True, NEVER_IN_RUN, NEVER_IN_RUN,
        "no", "none", "CLI", "SAFE", "closed", "Reached only from `kriya skills approve`."),
    ("kriya/knowledge/staging.py", "record_direct_fact", "git_commit_if_tracked"): Site(
        "knowledge staging (human answer)", "<skills>/<skill>/rules.txt + knowledge", SKILLS_IN_REPO, True, True, NEVER_IN_RUN, NEVER_IN_RUN,
        "no", "none", "CLI", "SAFE", "closed", "Reached only from `kriya skills scaffold`."),
    ("kriya/knowledge/staging.py", "record_direct_fact", "record_rule_provenance"): Site(
        "knowledge staging (human answer)", "<skills>/<skill>/rule provenance", SKILLS_IN_REPO, True, True, NEVER_IN_RUN, NEVER_IN_RUN,
        "no", "none", "CLI", "SAFE", "closed", "Reached only from `kriya skills scaffold`."),
}

GIT_VERB_SITES: Dict[Tuple[str, str, str], Site] = {
    ("kriya/skills/skill.py", "_commit_in_work_tree", "commit"): Site(
        "the skill commit funnel", "the git work tree that holds the written skill path", SKILLS_IN_REPO, False, True, "see callers",
        "see callers", "see callers", "see callers", "FUNNEL", "CHAIN", "closed",
        "The one commit funnel behind git_commit_if_tracked (any path) and git_commit_paths (an exact pathspec: the run-local shadow's flush, after the terminal commit). Every caller is a CALLER_SITES row. Best-effort: it never raises (see the commit-failure characterization); a run-local shadow path is never committed."),
    ("kriya/skills/run_shadow.py", "_unstage", "reset"): Site(
        "the run-local shadow's rollback", "the INDEX entries of the flushed paths only (`git reset -q -- <paths>`: no tree, no HEAD, no ref)",
        SKILLS_IN_REPO, False, False, "n/a", POST_TERMINAL, "no", "none", "VERIFICATION", "SAFE", "closed",
        "Runs only after a failed flush, after the candidate's terminal commit; it undoes the flush's own staging."),
    ("kriya/workflow/worktree.py", "_bootstrap_greenfield_repository", "commit"): Site(
        "greenfield bootstrap", "the workspace repository, created because the workspace had no repository", "the workspace has no git repository yet",
        False, True, "no: before the sandbox and before the run's base revision is read", "no: before the plan sandbox is created",
        "no: there is no earlier bound gate", "n/a", "SANDBOX", "SAFE", "closed", "Creates the repository the run then binds."),
    ("kriya/workflow/worktree.py", "create_git_worktree", "commit"): Site(
        "sandbox creation, empty initial commit of a repository with no HEAD", "the workspace repository when it has zero commits",
        "the workspace repository has no commit", False, True, "no: this IS the sandbox creation; the sandbox is detached at the resulting HEAD",
        "no: this IS the plan sandbox creation", "no: the sandbox is created from this HEAD", "n/a", "SANDBOX", "SAFE", "closed"),
    ("kriya/workflow/worktree.py", "_add_worktree", "reset"): Site(
        "sandbox creation", "the NEW sandbox work tree (never the workspace)", "never: it resets the sandbox it just created", True, False,
        "no: precedes every gate", "no: precedes every gate", "n/a", "n/a", "SANDBOX", "SAFE", "closed"),
    ("kriya/workflow/worktree.py", "create_git_worktree", "checkout"): Site(
        "sandbox creation/restoration", "the sandbox work tree", "never the workspace HEAD: a forced checkout inside the sandbox", True, False,
        "no: precedes every gate", "no: precedes every gate", "n/a", "n/a", "SANDBOX", "SAFE", "closed"),
    ("kriya/workflow/worktree.py", "remove_git_worktree", "checkout"): Site(
        "sandbox removal", "the sandbox work tree being removed", "never: it runs when the sandbox is released", True, False,
        "no: runs after the terminal apply", "no: runs after the terminal gates and commit", "no", "n/a", "SANDBOX", "SAFE", "closed"),
    ("kriya/static_analysis/operator_scan.py", "run_operator_scan", "reset"): Site(
        "operator scan of a base revision", "a Kriya-owned scratch checkout of the base revision", "never: the scan owns its checkout",
        True, False, "n/a: a scan of a separate checkout", "n/a: a scan of a separate checkout", "no", "n/a", "SANDBOX", "SAFE", "closed"),
}


def _scan() -> Tuple[Set[Tuple[str, str, str]], Set[Tuple[str, str, str]]]:
    callers: Set[Tuple[str, str, str]] = set()
    verbs: Set[Tuple[str, str, str]] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self, relative: str):
            self.relative, self.stack = relative, []

        def _scope(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

        def visit_Call(self, node):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if name in WRITER_CALLEES:
                callers.add((self.relative, ".".join(self.stack), name))
            argument_lists = [node.args] + [a.elts for a in node.args if isinstance(a, (ast.List, ast.Tuple))]
            for elements in argument_lists:
                constants = {e.value for e in elements if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                if re.match(r"^_?git", name or "") and constants & GIT_VERBS:
                    for verb in constants & GIT_VERBS:
                        verbs.add((self.relative, ".".join(self.stack), verb))
            self.generic_visit(node)

        def visit_List(self, node):
            constants = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if "git" in constants and constants & GIT_VERBS:
                for verb in constants & GIT_VERBS:
                    verbs.add((self.relative, ".".join(self.stack), verb))
            self.generic_visit(node)

    for path in sorted(PACKAGE.rglob("*.py")):
        Visitor(path.relative_to(ROOT).as_posix()).visit(ast.parse(path.read_text(encoding="utf-8")))
    return callers, verbs


@pytest.fixture(scope="module")
def scanned():
    return _scan()


def test_every_production_skill_writer_call_is_classified_and_no_classification_is_stale(scanned):
    callers, _ = scanned
    assert sorted(callers - set(CALLER_SITES)) == [], "UNCLASSIFIED mutation site(s): add a CALLER_SITES row (see the module docstring)"
    assert sorted(set(CALLER_SITES) - callers) == [], "classified site(s) that no longer exist: remove the row"


def test_every_production_git_history_or_tree_mutation_is_classified_and_no_classification_is_stale(scanned):
    _, verbs = scanned
    assert sorted(verbs - set(GIT_VERB_SITES)) == [], "UNCLASSIFIED git mutation(s): add a GIT_VERB_SITES row (see the module docstring)"
    assert sorted(set(GIT_VERB_SITES) - verbs) == [], "classified git mutation(s) that no longer exist: remove the row"


ALL_SITES = {**CALLER_SITES, **GIT_VERB_SITES}


def test_the_dispositions_are_a_closed_vocabulary_and_each_semantic_class_has_a_legal_one():
    assert {s.disposition for s in ALL_SITES.values()} <= {
        "SAFE", "DEFER_BOTH", "RUN_LOCAL_DEFER_DURABILITY", "CANDIDATE_MUTATION", "CHAIN", "NO_CALLER"}
    assert {s.status for s in ALL_SITES.values()} <= {"closed", "open"}
    for key, site in ALL_SITES.items():
        if site.semantic == "LEARNING":
            # learning writes: a reader in the same run decides run-local-with-deferred-durability vs defer-both
            assert site.disposition == ("DEFER_BOTH" if site.same_run_reader.startswith("none") else "RUN_LOCAL_DEFER_DURABILITY"), key
        # a candidate-semantic mutation needs an intentional rebind owner; none is classified, so none may appear
        assert site.disposition != "CANDIDATE_MUTATION", key
        # a site is only SAFE when it is not reachable in the bound window or it never moves the bound HEAD
        if site.disposition == "SAFE":
            assert site.semantic in {"VERIFICATION", "CLI", "SANDBOX"}, key


def test_no_row_is_open_and_every_learning_write_reachable_in_the_enforce_window_is_driven_through_a_real_enforce_run():
    """GIT-MIDRUN-COMMITS-S3-001 is closed by the run-local shadow: every learning write that can run between the plan's sandbox and the
    terminal gates must have a dynamic driver in tests/test_midrun_workspace_mutations.py (a real enforce run asserts zero HEAD and
    content drift, later-subtask visibility, and the end-of-run transaction for it)."""
    from test_midrun_workspace_mutations import IN_RUN_LEARNING_WRITERS
    driver_of = {
        "create_skill_skeleton": "create_skill_skeleton", "mark_gap_acknowledged": "mark_gap_acknowledged",
        "_write_skill_extraction": "write_skill_extraction", "_stage_skill_conflicts": "stage_skill_conflicts",
        "record_conflict_resolution": "record_conflict_resolution", "stage_facts": "stage_facts",
        "generate_skill_template": "generate_skill_template",
    }
    window = {key for key, site in ALL_SITES.items() if site.enforce.startswith("yes") and site.semantic == "LEARNING"}
    assert window, "the census lost every enforce-window learning row"
    assert {driver_of[callee] for (_, _, callee) in window} <= set(IN_RUN_LEARNING_WRITERS)
    assert [key for key, site in ALL_SITES.items() if site.status == "open"] == []


def test_the_head_and_write_columns_agree_with_the_measured_dynamic_detector():
    """The dynamic detector (tests/test_midrun_workspace_mutations.py) measured these; the static table may not drift from it."""
    from test_midrun_workspace_mutations import MEASURED_COMMITS
    by_driver = {
        "create_skill_skeleton": ("kriya/workflow/stage_analysis.py", "analyze_work_unit", "create_skill_skeleton"),
        "mark_gap_acknowledged": ("kriya/workflow/stage_analysis.py", "analyze_work_unit", "mark_gap_acknowledged"),
        "write_skill_extraction": ("kriya/workflow/stage_analysis.py", "analyze_work_unit", "_write_skill_extraction"),
        "stage_skill_conflicts": ("kriya/workflow/stage_analysis.py", "analyze_work_unit", "_stage_skill_conflicts"),
        "record_conflict_resolution": ("kriya/workflow/stage_analysis.py", "analyze_work_unit", "record_conflict_resolution"),
        "stage_facts": ("kriya/workflow/stage_apply.py", "extract_lessons", "stage_facts"),
    }
    for driver, key in by_driver.items():
        measured_commits = MEASURED_COMMITS.get(driver, 0)
        assert (measured_commits > 0) == CALLER_SITES[key].moves_head, driver
        # W3-1: the skill-gap decline is the one writer measured (test_midrun_workspace_mutations) to change no workspace byte
        assert CALLER_SITES[key].writes_file is (driver != "mark_gap_acknowledged"), driver


def test_the_registry_row_exists_and_agrees_with_the_open_rows():
    import csv
    rows = list(csv.DictReader(open(ROOT / "handover" / "BACKLOG_REGISTRY.csv", newline="", encoding="utf-8")))
    [row] = [r for r in rows if r["id"] == "GIT-MIDRUN-COMMITS-S3-001"]
    has_open = any(site.status == "open" for site in ALL_SITES.values())
    if has_open:
        assert row["status"] != "CLOSED"
    else:       # the fix is in; the row closes only after the integrated detector ran under the S7 containment stand-in
        assert row["status"] == "CLOSED" or "INTEGRATED DETECTOR PENDING" in row["closure_evidence"]
    assert row["target_scope"].strip() and row["closure_evidence"].strip()


def test_an_enforce_run_always_has_a_kernel_so_the_shared_skill_engine_always_exists():
    """Kernel-none enforce (S7 close #3 item 7): `WorkflowController` builds the enforce run's shared SkillEngine only when its
    engine has a kernel. If a production entry point could run enforce without one, each subtask would persist its own skill
    changes (the pre-P-B shape). It cannot: `WorkflowEngine.__init__` requires a kernel and every production construction passes
    one, so the case exists only for test doubles. Classified SAFE (unreachable), pinned here."""
    constructions = []
    for path in sorted(PACKAGE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "WorkflowEngine":
                constructions.append((path.relative_to(ROOT).as_posix(), node))
    assert constructions, "no production WorkflowEngine construction found: the scan is broken"
    for relative, node in constructions:
        assert len(node.args) >= 2 and not (isinstance(node.args[0], ast.Constant) and node.args[0].value is None), relative

    engine_module = ast.parse((PACKAGE / "workflow" / "workflow.py").read_text(encoding="utf-8"))
    [engine_class] = [n for n in ast.walk(engine_module) if isinstance(n, ast.ClassDef) and n.name == "WorkflowEngine"]
    [init] = [n for n in engine_class.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"]
    kernel_argument = init.args.args[1]
    assert kernel_argument.arg == "kernel" and not init.args.defaults, "the kernel must stay a required constructor argument"
