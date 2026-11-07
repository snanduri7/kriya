"""PROJECT-CONTINUITY (owner ruling 2026-11-07, Phase B): a second goal against the SAME project sees, edits and judges what the first run built.

REPRODUCER of the measured defect (walkthrough run 20261107T182659-f963bb85, folder green-demo4): Run 1 created ``main.py`` (applied, Git-untracked, no ``git add``); Run 2 ("Change hello
world message to Hello Kriya in the app") never saw ``main.py``, planned ``MODIFY app.py`` (absent), created ``app.py``, and the Reviewer/spec check - given only that new file - called it correct.
Everything below drives the REAL direct pipeline with a scripted model; the scripted roles decide ONLY from what their prompt actually carries, so a prompt that omits ``main.py`` reproduces the defect.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _chaos_harness import ChaosRuntime, RuntimeRegistration, chaos_config, chaos_engine, run_direct  # noqa: E402

GOAL_1 = "Create main.py with a function hello() that returns the text Hello World"
GOAL_2 = "Change hello world message to Hello Kriya in the app"


def _user(request):
    return next((m.get("content") or "" for m in reversed(request.messages) if m.get("role") == "user"), "")


def _all_text(request):
    return "\n".join(str(m.get("content") or "") for m in request.messages)


class Scripted:
    """A model whose every decision is a pure function of the prompt it is shown."""

    def __init__(self, *, run):
        self.run = run
        self.seen = {}

    def __call__(self, role, request):
        text = _all_text(request)
        self.seen.setdefault(role, []).append(text)
        sees_main = "main.py" in text
        if self.run == 1:
            target, body = "main.py", 'print("Hello World")\n'
        else:
            target = "main.py" if sees_main else "app.py"
            body = 'print("Hello Kriya")\n'
        if role == "file_list":
            return json.dumps([target])
        if role == "planner":
            return f"Step 1: change {target}"
        if role == "architect":
            return f"Design: change {target}"
        if role == "developer":
            return body
        if role == "run_verifier":
            return '{"should_run": false, "run_commands": null, "reasoning": "n/a"}'
        return "Review: Approved"


def _run(workspace, goal, scripted, *, window=32768):
    """``window``: the served context window. The harness default (8k) leaves a real Planner request no room for the Current Project View - which then degrades to nothing, by design - so continuity scenarios serve the 32k a real run has."""
    cfg = chaos_config()
    if window:
        cfg.llm.context_window = window
    runtime = ChaosRuntime(scripted)
    if window:
        runtime.served_window = window
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(cfg), goal, workspace)
    return result, runtime


def _tree(workspace):
    return sorted(p.name for p in Path(workspace).iterdir() if p.name not in (".git", ".kriya"))


def test_two_runs_the_second_edits_the_first_runs_file_and_creates_no_substitute(tmp_path):
    first, _ = _run(tmp_path, GOAL_1, Scripted(run=1))
    assert first["quality_gates_passed"] is True, first
    assert (tmp_path / "main.py").read_text() == 'print("Hello World")\n'
    status = subprocess.run(["git", "status", "--short"], cwd=tmp_path, capture_output=True, text=True).stdout
    assert "?? main.py" in status                                                    # applied, never git-added: the premise of the defect

    scripted = Scripted(run=2)
    second, runtime = _run(tmp_path, GOAL_2, scripted)
    for role in ("planner", "architect"):
        assert "main.py" in scripted.seen[role][0], f"{role} was not shown the project's existing file"
    assert second["quality_gates_passed"] is True, second
    developer = scripted.seen["developer"][0]
    assert 'print("Hello World")' in developer and "EXISTING SOURCE" in developer                 # the Developer is handed the file's CURRENT content as the owner contract
    reviewer = scripted.seen["reviewer"][0]
    assert "=== Project BEFORE this change" in reviewer and "main.py: EXISTING file, its content before this change" in reviewer
    assert reviewer.index('print("Hello World")') < reviewer.index("Files generated:") < reviewer.rindex('print("Hello Kriya")')      # BEFORE precedes AFTER
    assert _tree(tmp_path) == ["main.py"], _tree(tmp_path)                           # no substitute app.py
    assert (tmp_path / "main.py").read_text() == 'print("Hello Kriya")\n'


# ---- the project-grounded action rule: a MODIFY of an absent file is a bounded plan repair, never a create ------------------------------------------------------------------------
# The structured plan is the REAL one the Planner returned in the measured run (evidence 20261107T182659-f963bb85), its path and action parameterised.
STRUCTURED_PLAN = """{
  "global_invariants": [{"id": "gi1", "statement": "The application is a Python-based flat/modular package layout with a single entry point"}],
  "subtasks": [
    {"id": "s1", "description": "Locate the hello world message in the application code", "execution_method": "model", "execution_role": "implementation", "depends_on": [],
     "planned_files": [{"path": "@PATH@", "action": "@ACTION@", "environment_requirements": ["python"], "requires_capabilities": []}],
     "provides": ["capability.hello_world_location_found"], "requires": [], "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac1"],
     "verification": [{"type": "judgment", "description": "confirm the location of the hello world message in the codebase", "verifier_kind": "judgment", "requires_runtime_execution": false}]},
    {"id": "s2", "description": "Modify the hello world message to \\"Hello Kriya\\" in the application", "execution_method": "model", "execution_role": "implementation", "depends_on": ["s1"],
     "planned_files": [{"path": "@PATH@", "action": "@ACTION@", "environment_requirements": ["python"], "requires_capabilities": []}],
     "provides": ["capability.hello_world_message_updated"], "requires": ["capability.hello_world_location_found"], "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac1"],
     "verification": [{"type": "tool", "tool_name": "compile", "verifier_kind": "compile", "description": "verify the modified code compiles without errors"}]}
  ],
  "acceptance_criteria": [{"id": "ac1", "description": "The application should display 'Hello Kriya' instead of 'hello world'", "method": "judgment"}],
  "extension_points": [], "refactor_baseline": null
}"""


def _structured(path, action):
    return "Plan:\n```json\n" + STRUCTURED_PLAN.replace("@PATH@", path).replace("@ACTION@", action) + "\n```\n"


class StructuredScripted(Scripted):
    """A Planner that answers with a structured plan whose first-choice path is ``first``; once a repair prompt names the mismatch it answers with ``then`` (None: it never changes its mind)."""

    def __init__(self, *, first, then=None):
        super().__init__(run=2)
        self.first, self.then, self.planner_prompts = first, then, []

    def __call__(self, role, request):
        if role == "planner":
            text = _all_text(request)
            self.planner_prompts.append(text)
            self.seen.setdefault(role, []).append(text)
            repairing = "PLANNED_FILE_ACTION_MISMATCH" in text or "does not exist on disk" in text
            path = self.then if (repairing and self.then) else self.first
            return _structured(path, "modify")
        return super().__call__(role, request)


def _git_project(tmp_path, files):
    from _chaos_harness import git_workspace

    return git_workspace(tmp_path, files, name="ws")


def test_E_a_modify_of_a_file_the_project_does_not_hold_is_never_turned_into_a_create(tmp_path):
    workspace = _git_project(tmp_path, {"main.py": 'print("Hello World")\n'})
    scripted = StructuredScripted(first="missing.py")
    result, runtime = _run(workspace, GOAL_2, scripted, window=32768)
    assert result.get("quality_gates_passed") is not True and result["status"] == "planner_output_schema_invalid", result
    assert not (workspace / "missing.py").exists() and not (workspace / "app.py").exists()
    assert (workspace / "main.py").read_text() == 'print("Hello World")\n'
    assert runtime.count("developer") == 0                                       # no Developer attempt was made against an invented target
    assert result.get("reason_codes") == ["PLANNED_FILE_ACTION_MISMATCH"], result
    assert len(scripted.planner_prompts) == 3                                    # the original + the bounded repair attempts (STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS), then fail closed


def test_E_the_bounded_repair_lets_the_planner_name_the_real_file(tmp_path):
    workspace = _git_project(tmp_path, {"main.py": 'print("Hello World")\n'})
    scripted = StructuredScripted(first="app.py", then="main.py")
    result, _ = _run(workspace, GOAL_2, scripted, window=32768)
    assert result["quality_gates_passed"] is True, result
    assert not (workspace / "app.py").exists()
    assert (workspace / "main.py").read_text() == 'print("Hello Kriya")\n'
    assert any("PLANNED_FILE_ACTION_MISMATCH" in prompt or "does not exist on disk" in prompt for prompt in scripted.planner_prompts[1:])


def test_a_modify_of_an_existing_file_is_not_disturbed_by_the_rule(tmp_path):
    workspace = _git_project(tmp_path, {"main.py": 'print("Hello World")\n'})
    scripted = StructuredScripted(first="main.py")
    result, _ = _run(workspace, GOAL_2, scripted, window=32768)
    assert result["quality_gates_passed"] is True and len(scripted.planner_prompts) == 1
    assert (workspace / "main.py").read_text() == 'print("Hello Kriya")\n'


# ---- F: a candidate that writes a DIFFERENT file while the existing application is unchanged must not be judged compliant -----------------------------------------------------------
SPEC_MARKER = "Goal Spec Compliance Checker"


class WrongFileScripted(Scripted):
    """A Planner/Architect/Developer that (wrongly) CREATE a substitute ``app.py``, and a spec checker that judges ONLY from the prompt it is shown: if the files generated include the
    project's existing ``main.py`` it is satisfied; if they are a different file and the prompt shows ``main.py`` still printing the OLD message, the requirement is missing; if the
    candidate is all it can see (the measured defect), it is satisfied."""

    def __init__(self):
        super().__init__(run=2)
        self.spec_prompts = []

    def __call__(self, role, request):
        system = next((m.get("content") or "" for m in request.messages if m.get("role") == "system"), "")
        if SPEC_MARKER in system.splitlines()[0]:
            text = _all_text(request)
            self.spec_prompts.append(text)
            generated = text[text.index("=== Files Generated ==="):]
            old_application_untouched = 'print("Hello World")' in text[:text.index("=== Files Generated ===")] and "=== main.py ===" not in generated
            return json.dumps({
                "compliant": not old_application_untouched,
                "reasoning": "the existing application still prints the old message" if old_application_untouched else "the candidate prints the new message",
                "missing_requirements": ["Hello Kriya in the existing application"] if old_application_untouched else [],
                "likely_files": ["main.py"] if old_application_untouched else [],
                "requirement_verdicts": [{"id": "REQ-1", "verdict": "missing" if old_application_untouched else "satisfied", "evidence": "main.py" if old_application_untouched else "app.py"}]})
        if role == "planner":
            return _structured("app.py", "create")
        if role == "file_list":
            return json.dumps(["app.py"])
        if role == "architect":
            return "Design: create app.py"
        return super().__call__(role, request)


def _wrong_file_run(tmp_path, **autonomy):
    workspace = _git_project(tmp_path, {"main.py": 'print("Hello World")\n'})
    scripted = WrongFileScripted()
    runtime = ChaosRuntime(scripted)
    cfg = chaos_config(spec_compliance_enabled=True, **autonomy)
    cfg.llm.context_window = 32768
    runtime.served_window = 32768
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(cfg), GOAL_2, workspace)
    return workspace, scripted, result


def test_F_the_requirement_closure_judges_the_candidate_against_the_project_and_does_not_record_success(tmp_path):
    """MEASURED defect: shown only ``app.py``, the checker answered ``satisfied``. Shown the project BEFORE, it can see ``main.py`` still prints the old message."""
    _, scripted, result = _wrong_file_run(tmp_path)
    assert scripted.spec_prompts, "the requirement closure never consulted the spec checker"
    assert all("=== Project BEFORE this change" in prompt and "main.py (not changed by this candidate), current content" in prompt for prompt in scripted.spec_prompts)
    assert result["requirements"]["outcomes"]["REQ-1"] != "satisfied", result["requirements"]


def test_F_with_the_unverified_policy_blocking_the_substitute_is_refused_and_nothing_is_applied(tmp_path):
    workspace, _, result = _wrong_file_run(tmp_path, requirement_unverified_policy="block")
    assert result["quality_gates_passed"] is False, result
    assert not (workspace / "app.py").exists() and (workspace / "main.py").read_text() == 'print("Hello World")\n'


# ---- controls A, B, C through the real pipeline ---------------------------------------------------------------------------------------------------------------------------------------
def _after_run_one(tmp_path):
    first, _ = _run(tmp_path, GOAL_1, Scripted(run=1))
    assert first["quality_gates_passed"] is True, first
    return tmp_path


def test_control_A_an_unrelated_untracked_file_is_named_as_origin_unknown_and_its_content_never_reaches_a_model(tmp_path):
    workspace = _after_run_one(tmp_path)
    (workspace / "rogue.py").write_text("ROGUE_SENTINEL = 'do-not-send'\n", encoding="utf-8")
    scripted = Scripted(run=2)
    result, runtime = _run(workspace, GOAL_2, scripted)
    assert result["quality_gates_passed"] is True, result
    planner = scripted.seen["planner"][0]
    assert "rogue.py [untracked, origin unknown - not created by Kriya]" in planner and "main.py [created by an earlier Kriya run" in planner
    assert "ROGUE_SENTINEL" not in runtime.transcript()                          # not in ANY request to ANY role
    assert (workspace / "rogue.py").read_text() == "ROGUE_SENTINEL = 'do-not-send'\n"
    assert (workspace / "main.py").read_text() == 'print("Hello Kriya")\n'


def test_control_B_a_user_edit_between_runs_is_what_every_role_sees_not_the_first_runs_bytes(tmp_path):
    workspace = _after_run_one(tmp_path)
    (workspace / "main.py").write_text('print("Hello World")  # edited by the user\nUSER_MARKER = 7\n', encoding="utf-8")
    scripted = Scripted(run=2)
    result, runtime = _run(workspace, GOAL_2, scripted)
    assert result["quality_gates_passed"] is True, result
    for role in ("planner", "developer", "reviewer"):
        assert "USER_MARKER = 7" in scripted.seen[role][0], role


def test_control_C_an_accepted_file_the_user_deleted_is_not_hallucinated_as_current(tmp_path):
    workspace = _after_run_one(tmp_path)
    (workspace / "main.py").unlink()
    scripted = Scripted(run=2)
    _run(workspace, GOAL_2, scripted)
    for role in ("planner", "architect"):
        text = scripted.seen[role][0]
        assert "Current Project View" not in text and "main.py" not in text, role     # the journal remembers the path; the filesystem says it is gone, and the filesystem wins
