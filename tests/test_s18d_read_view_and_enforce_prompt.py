"""S18d (TYPE C; OI-149, OI-150): the enforce Planner's TOOL sentence and the planned read view, owner decisions 2026-10-20.

  * Part 1 (OI-149): the enforce planning request told the Planner "Do not emit execution_method=tool subtasks; this
    execution path has no policy-mediated TOOL router" - false since TOOL-001. Corrected to the ONE planned-tool contract
    the Planner's own system prompt states (kriya.agents.contracts.PLANNED_TOOL_CATALOGUE_RULE): a TOOL subtask may name
    only a tool from a plannable list given to it; the enforce request gives none, so it keeps planning MODEL subtasks
    (no behaviour change). It names no tool a planned step cannot run.
  * Part 2a/b (OI-150 a, b): which repository view a planned filesystem/search/ast read describes follows the OBSERVED
    effect of its MODEL ancestry, never a label: Kriya's own per-unit record of the files a MODEL unit actually applied
    to the candidate (ControlState.subtask_written_files, from the candidate transaction's all_files_written) - a unit
    that applied a change -> the candidate; a unit that applied nothing (a verification unit, a verified NO CHANGE) ->
    walk on through that unit's own dependencies; no evidence for a MODEL unit -> the candidate (the S18c fail-closed
    rule). git keeps its S18b per-subject roots (status/diff candidate, log/branch/blame owner).
  * Part 2c (OI-150 c): planned filesystem/search/ast reads never expose repository metadata or Kriya control state as
    source: the canonical PLAT-039 trusted control paths (kriya.policy.filesystem: .git, .kriya and the scoped-snapshot
    sentinel) are refused when named and left out of listings and search hits, in every view. Ordinary dotfiles
    (.github/, .gitignore, .eslintrc) stay readable; credential files keep their existing denial.

The after-pins (marked strict-xfail in the characterization commit) were MEASURED failing on the unfixed bytes.
No live model, no Docker.
"""
import asyncio
import os
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from _plugin_test_support import load_core_tools_module
from _strict_doubles import strict_config, strict_kernel
from test_s18b_planned_tool_contracts import _shipped_tools, unplannable_example_tools
from test_s18c_read_root_identity import GIT_ARGS, GIT_COMMANDS, build_three_state, git

from kriya.agents.agent import PlannerAgent
from kriya.config.config import AppConfig
from kriya.core.llm import LLMClient
from kriya.tools.tool import resolve_planned_tool_governance
from kriya.workflow import subtask_executor
from kriya.workflow.context_package import build_context_package
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, ExecutionRole, Subtask
from kriya.workflow.planning_request import build_authoritative_planner_request
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_types import SubtaskStatus

GUARDRAILS = "guardrails"
STALE_SENTENCE = ("Do not emit execution_method=tool subtasks; this execution path has no policy-mediated "
                  "TOOL router.")
# The rule text as a literal too: the constant does not exist before S18d, and the pin must not depend on it alone.
CATALOGUE_RULE_TEXT = "only the tools listed there can run as a planned step"


def _core():
    return load_core_tools_module()


# =====================================================================================================================
# Part 1 (OI-149): the enforce planning request states the one planned-tool contract
# =====================================================================================================================

def _enforce_request() -> str:
    return build_authoritative_planner_request("Build one runnable application.")


def _planner_prompt() -> str:
    return PlannerAgent("planner", LLMClient(AppConfig())).system_prompt


def _unplannable_shipped_names():
    names = {name for name, tool in _shipped_tools().items()
             if not resolve_planned_tool_governance(tool, autonomy_mode=GUARDRAILS).plannable}
    assert {"shell", "validate_refactor"} <= names  # non-vacuous: the shipped operator-only tools
    return names


def named_unplannable_tools(text: str):
    """Every shipped tool a planned step cannot run that ``text`` names as a word."""
    return {name for name in _unplannable_shipped_names() if re.search(rf"\b{re.escape(name)}\b", text)}


def claims_no_tool_router(text: str) -> bool:
    return re.search(r"\bno\b[^.\n]*\bTOOL router\b", text, re.IGNORECASE) is not None


def test_the_enforce_request_names_no_tool_a_planned_step_cannot_run():
    """Before and after: the enforce request never teaches an unplannable tool (example objects, prose, any word)."""
    request = _enforce_request()
    assert named_unplannable_tools(request) == set()
    assert unplannable_example_tools(request) == set()


@pytest.mark.parametrize("planted", ["shell", "validate_refactor"])
def test_the_tool_name_checker_sees_a_planted_unplannable_tool(planted):
    request = _enforce_request()
    assert planted in named_unplannable_tools(request + f"\n- A planned step may run {planted}.\n")


def test_the_no_router_checker_sees_the_stale_claim():
    assert claims_no_tool_router(STALE_SENTENCE)
    assert claims_no_tool_router("TOOL steps are unsupported: there is no policy-mediated TOOL router yet.")
    assert not claims_no_tool_router("execution_method=tool runs one Kriya tool directly through Kriya's governed TOOL "
                                     "router.")


@pytest.mark.xfail(strict=True, reason="S18d after-pin (OI-149): MEASURED failing on the unfixed bytes")
def test_the_enforce_request_drops_the_stale_no_router_claim():
    request = _enforce_request()
    assert STALE_SENTENCE not in request
    assert "Do not emit execution_method=tool" not in request
    assert not claims_no_tool_router(request)


@pytest.mark.xfail(strict=True, reason="S18d after-pin (OI-149): MEASURED failing on the unfixed bytes")
def test_the_enforce_request_and_the_planner_prompt_state_one_planned_tool_contract():
    """ONE contract, one wording: the same rule constant in the Planner's system prompt (S18b) and the enforce request;
    the enforce request gives no list, so it keeps the Planner on MODEL subtasks (behaviour unchanged)."""
    from kriya.agents.contracts import PLANNED_TOOL_CATALOGUE_RULE

    assert PLANNED_TOOL_CATALOGUE_RULE == CATALOGUE_RULE_TEXT
    request, prompt = _enforce_request(), _planner_prompt()
    assert prompt.count(PLANNED_TOOL_CATALOGUE_RULE) == 1
    assert request.count(PLANNED_TOOL_CATALOGUE_RULE) == 1
    line = next(line for line in request.splitlines() if PLANNED_TOOL_CATALOGUE_RULE in line)
    assert "governed TOOL router" in line
    assert "This request gives no such list, so every subtask uses execution_method=model." in line


# =====================================================================================================================
# Part 2a/b (OI-150 a, b): the read view follows the observed effect of the MODEL ancestry
# =====================================================================================================================

READS = {
    "read": ("filesystem", {"operation": "read", "path": "app.py"}),
    "list": ("filesystem", {"operation": "list", "path": "."}),
    "search": ("search", {"pattern": "ORIGIN", "path": "."}),
    "ast": ("ast", {"file_path": "app.py"}),
}


def _identity(read, output, origin):
    """The answer names ``origin`` ('OWNER' or 'CANDIDATE') for this read (the S18c markers)."""
    if read == "list":
        return {"OWNER": "owner_untracked_after.py", "CANDIDATE": "candidate_only.py"}[origin] in set(output)
    if read == "ast":
        return f"def {origin.lower()}_fn" in output
    if read == "search":
        return f"app.py:1: ORIGIN = 'ORIGIN_{origin}'" in output.splitlines()
    return f"ORIGIN = 'ORIGIN_{origin}'" in output


_VERIFY = [{"type": "tool", "tool_name": "test", "description": "run the tests"}]


def _model(step_id, *, role="implementation", depends_on=()):
    """A MODEL unit. Its label (execution_role) never decides the view; only the evidence passed with the call does."""
    if role == "verification":
        return Subtask(id=step_id, description=f"{step_id} (verification)", execution_method=ExecutionMethod.MODEL,
                       execution_role=ExecutionRole.VERIFICATION, verification=_VERIFY, depends_on=list(depends_on))
    return Subtask(id=step_id, description=f"{step_id} (implementation)", execution_method=ExecutionMethod.MODEL,
                   planned_files=[{"path": "app.py", "action": "modify"}], depends_on=list(depends_on))


def view(tool_name, arguments, roots, *, depends_on=(), steps=(), **evidence):
    """One planned TOOL step t9 through the production executor, in a plan holding ``steps``; ``evidence`` is what the
    caller passes the executor (the enforce loop passes ControlState.subtask_written_files as candidate_writes)."""
    core = _core()
    tool = {"git": core.GitTool, "filesystem": core.FilesystemTool, "search": core.SearchTool,
            "ast": core.ASTTool}[tool_name]()
    step = Subtask(id="t9", description=f"planned {tool_name} read", execution_method=ExecutionMethod.TOOL,
                   tool_name=tool_name, tool_arguments=dict(arguments), depends_on=list(depends_on))
    kernel = strict_kernel(strict_config(autonomy={"mode": GUARDRAILS}))
    kernel.registry.get = MagicMock(return_value=tool)
    plan = EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=[*steps, step])
    return asyncio.run(subtask_executor.execute(subtask=step, plan=plan, context=build_context_package(),
                                                kernel=kernel, tool_roots=tuple(roots), **evidence))


@pytest.fixture
def three_state(tmp_path):
    return build_three_state(tmp_path)


# The read-view table (case | ancestry | evidence | expected view). The S18c three-state fixture's candidate really
# differs from the owner (app.py rewritten, candidate_only.py created), so a wrong root cannot pass by coincidence.
READ_VIEW_CASES = {
    # case id: (steps, depends_on, candidate_writes, expected view)
    "model-changed-a-file": ([_model("m1")], ["m1"], {"m1": ["app.py"]}, "CANDIDATE"),
    "verification-unit-changed-nothing": ([_model("v1", role="verification")], ["v1"], {"v1": []}, "OWNER"),
    "no-change-implementation-unit": ([_model("m1")], ["m1"], {"m1": []}, "OWNER"),
    "verification-label-that-changed-a-file": ([_model("v1", role="verification")], ["v1"], {"v1": ["app.py"]},
                                               "CANDIDATE"),
    "verification-unit-after-a-change": ([_model("m1"), _model("v1", role="verification", depends_on=["m1"])],
                                         ["v1"], {"m1": ["app.py"], "v1": []}, "CANDIDATE"),
    "verification-unit-after-a-no-change-unit": ([_model("m1"), _model("v1", role="verification", depends_on=["m1"])],
                                                 ["v1"], {"m1": [], "v1": []}, "OWNER"),
    "no-model-ancestor": ([_model("m1")], [], {"m1": ["app.py"]}, "OWNER"),
    "model-unit-without-evidence-fails-closed": ([_model("m1")], ["m1"], {}, "CANDIDATE"),
    "one-of-two-ancestors-changed": ([_model("m1"), _model("v1", role="verification")], ["v1", "m1"],
                                     {"m1": ["app.py"], "v1": []}, "CANDIDATE"),
}


# The rows whose view differs from the S18c rule (any MODEL ancestor -> candidate) are the defect: a MODEL ancestry that
# changed nothing. The other rows are pinned before and after (the unfixed executor accepts and ignores the evidence
# keyword - it lands in **run_generation_kwargs - and answers by the S18c rule, which agrees with them).
DEFECT_CASES = ("verification-unit-changed-nothing", "no-change-implementation-unit",
                "verification-unit-after-a-no-change-unit")
_AFTER_PIN_A = pytest.mark.xfail(strict=True, reason="S18d after-pin (OI-150 a): MEASURED failing on the unfixed bytes")


@pytest.mark.parametrize("read", list(READS))
@pytest.mark.parametrize("case", [pytest.param(case, marks=_AFTER_PIN_A) if case in DEFECT_CASES else case
                                  for case in READ_VIEW_CASES])
def test_the_read_view_follows_the_observed_effect_of_the_model_ancestry(three_state, case, read):
    s = three_state
    steps, depends_on, writes, expected = READ_VIEW_CASES[case]
    tool, arguments = READS[read]
    result = view(tool, arguments, (s.owner, s.candidate), depends_on=depends_on, steps=steps, candidate_writes=writes)
    assert result.status == SubtaskStatus.COMPLETED, result.error
    other = "OWNER" if expected == "CANDIDATE" else "CANDIDATE"
    assert _identity(read, result.tool_output, expected) and not _identity(read, result.tool_output, other)


@pytest.mark.parametrize("read", list(READS))
def test_without_evidence_the_read_view_keeps_the_s18c_fail_closed_rule(three_state, read):
    """No evidence at all (an embedder or test calling the executor directly): any MODEL ancestor -> the candidate."""
    s = three_state
    tool, arguments = READS[read]
    result = view(tool, arguments, (s.owner, s.candidate), depends_on=["v1"], steps=[_model("v1", role="verification")],
                  candidate_writes=None)
    assert result.status == SubtaskStatus.COMPLETED, result.error
    assert _identity(read, result.tool_output, "CANDIDATE")


@pytest.mark.parametrize("sub", list(GIT_COMMANDS))
def test_git_keeps_its_per_subject_roots_whatever_the_evidence(three_state, sub):
    """OI-150 (b), no forced symmetry: status/diff describe the candidate (the delta) even with no or no-change
    ancestry; log/branch/blame keep the owner - the evidence never moves git."""
    s = three_state
    expected = git(s.candidate if sub in ("status", "diff") else s.owner, *GIT_COMMANDS[sub])
    for depends_on, writes in (((), {}), (("v1",), {"v1": []}), (("m1",), {"m1": ["app.py"]})):
        result = view("git", {"subcommand": sub, **GIT_ARGS.get(sub, {})}, (s.owner, s.candidate),
                      depends_on=depends_on, steps=[_model("m1"), _model("v1", role="verification")],
                      candidate_writes=writes)
        assert result.status == SubtaskStatus.COMPLETED, (depends_on, result.error)
        assert result.tool_output == expected, depends_on


# -- the real scenario, end to end (the production enforce loop records and passes the evidence) ------------------------

def _enforce_read_views(tmp_path, monkeypatch):
    """A real enforce run (model scripted at the transport): s1 caches the service (a change), s2 is answered NO CHANGE
    after s1, s3 is a verification unit with no ancestry, s4 a NO CHANGE unit with no ancestry; r1..r5 read the service
    after s1, s2, s3, s4 and after nothing. Returns {read id: True if it saw the cached (candidate) service}."""
    import _t6_harness
    import test_enforce_verified_no_change as shape
    from _chaos_harness import chaos_config
    from _protocol_responses import sentinel

    other, other_src = "shop/other.py", "def other():\n    return 'other'\n"
    common = {"relevant_global_invariant_ids": ["gi1"], "verification": []}

    def read(step_id, depends_on):
        return {"id": step_id, "description": f"read the service ({step_id})", "execution_method": "tool",
                "tool_name": "filesystem", "tool_arguments": {"operation": "read", "path": shape.SERVICE},
                "depends_on": list(depends_on), "provides": [f"{step_id}_shown"], "acceptance_criteria_ids": [],
                **common}

    def plan():
        document = shape._plan(shape.TOOL_CRITERION).model_dump()
        s1, s2 = document["subtasks"]
        s3 = {"id": "s3", "description": "run the tests to confirm", "execution_method": "model",
              "execution_role": "verification", "planned_files": [], "depends_on": [], "provides": ["verified"],
              "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": [],
              "verification": [{"type": "tool", "tool_name": "test", "verifier_kind": "test", "description": "tests"}]}
        s4 = {"id": "s4", "description": "confirm other() needs nothing", "execution_method": "model",
              "planned_files": [{"path": other, "action": "modify"}], "provides": ["other_ok"], "depends_on": [],
              "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac1"],
              "verification": [{"type": "tool", "tool_name": "test", "description": "run the tests"}]}
        document["subtasks"] = [s1, s2, s3, s4, read("r1", ["s1"]), read("r2", ["s2"]), read("r3", ["s3"]),
                                read("r4", ["s4"]), read("r5", [])]
        return EngineeringPlan.model_validate(document)

    def responder(role, request):
        system = next((m["content"] for m in request.messages if m["role"] == "system"), "")
        user = next((m["content"] for m in request.messages if m["role"] == "user"), "")
        if role == "file_list" and "other()" in user:
            return '{"files": ["%s"]}' % other
        if role == "developer" and (f'path="{other}"' in system or "other()" in user):
            return sentinel(other, analysis="nothing to change.", no_change=True)
        return _t6_harness.shop_responder(role, request)

    cfg = chaos_config()
    cfg.autonomy.mode = GUARDRAILS
    observed = _t6_harness.enforce_run(tmp_path, monkeypatch, responder, {**_t6_harness.shop_files(), other: other_src},
                                       shape.GOAL, [plan], cfg=cfg, tools={"filesystem": _core().FilesystemTool()})
    assert observed.result.legacy_result["status"] == "success", observed.result.legacy_result
    steps = {r.subtask_id: r for r in observed.result.subtask_results}
    # The evidence the views rest on, MEASURED in this run: s1 applied the service; s2/s3/s4 applied nothing.
    assert steps["s2"].reason_codes == ("VERIFIED_NO_CHANGE",) and steps["s4"].reason_codes == ("VERIFIED_NO_CHANGE",)
    assert observed.result.control_state.subtask_written_files == {"s1": [shape.SERVICE], "s2": [], "s3": [], "s4": []}
    views = {}
    for read_id in ("r1", "r2", "r3", "r4", "r5"):
        assert steps[read_id].status == SubtaskStatus.COMPLETED, steps[read_id].error
        views[read_id] = "lru_cache" in steps[read_id].tool_output
    return views


def test_an_enforce_run_reads_the_candidate_after_a_change_and_through_a_no_change_unit(tmp_path, monkeypatch):
    """Before and after: r1 (after the change) and r2 (after a NO CHANGE unit that itself follows the change) see the
    candidate; r5 (no dependency) sees the owner baseline."""
    views = _enforce_read_views(tmp_path, monkeypatch)
    assert (views["r1"], views["r2"], views["r5"]) == (True, True, False)


@pytest.mark.xfail(strict=True, reason="S18d after-pin (OI-150 a): MEASURED failing on the unfixed bytes")
def test_an_enforce_run_reads_the_baseline_after_units_that_changed_nothing(tmp_path, monkeypatch):
    """OI-150 (a), MEASURED on the unfixed bytes: r3 (after a verification unit) and r4 (after a NO CHANGE unit), neither
    with any change in their ancestry, saw the candidate."""
    views = _enforce_read_views(tmp_path, monkeypatch)
    assert (views["r3"], views["r4"]) == (False, False)


# =====================================================================================================================
# Part 2c (OI-150 c): planned reads never expose repository metadata or Kriya control state as source
# =====================================================================================================================

SENTINEL = ".kriya-scoped-snapshot"
ORDINARY_DOTFILES = {".github/workflows/ci.yml": "name: CI_MARKER\n", ".eslintrc": '{"root": true} ESLINT_MARKER\n',
                     ".env.example": "EXAMPLE_MARKER=1\n"}


@pytest.fixture
def dotted(tmp_path):
    """The three-state fixture with ordinary dotfiles in every root (owner, its candidate, plain, its snapshot)."""
    s = build_three_state(tmp_path)
    for root in (s.owner, s.candidate, s.plain, s.snapshot):
        for relpath, text in ORDINARY_DOTFILES.items():
            Path(root, relpath).parent.mkdir(parents=True, exist_ok=True)
            Path(root, relpath).write_text(text)
    return s


def _views(s):
    """(view id, roots, depends_on): the owner baseline, the worktree candidate, the scoped-snapshot candidate."""
    after = (["m1"], [_model("m1")], {"m1": ["app.py"]})
    return {"owner": ((s.owner, s.candidate), [], [], None),
            "worktree": ((s.owner, s.candidate), *after),
            "snapshot": ((s.plain, s.snapshot), *after)}


def _in_view(s, name, tool, arguments):
    roots, depends_on, steps, writes = _views(s)[name]
    extra = {} if writes is None else {"candidate_writes": writes}
    return view(tool, arguments, roots, depends_on=depends_on, steps=steps, **extra)


def test_the_control_artifacts_are_really_there(dotted):
    """Guard (MEASURED): the owner holds .git and .kriya, the worktree candidate a .git gitfile, the snapshot a Kriya-made
    .git and the sentinel - so the pins below cannot pass because nothing was there."""
    s = dotted
    assert {".git", ".kriya"} <= set(os.listdir(s.owner))
    assert os.path.isfile(os.path.join(s.candidate, ".git"))
    assert {".git", SENTINEL} <= set(os.listdir(s.snapshot))


@pytest.mark.parametrize("name", ["owner", "worktree", "snapshot"])
def test_ordinary_dotfiles_stay_readable_and_searchable(dotted, name):
    """Before and after (no 'hide every dotfile'): .github/ and .eslintrc read in every view; a dotfile's line is a search
    hit; the listing keeps them."""
    s = dotted
    for relpath in (".github/workflows/ci.yml", ".eslintrc"):
        result = _in_view(s, name, "filesystem", {"operation": "read", "path": relpath})
        assert result.status == SubtaskStatus.COMPLETED and result.tool_output == ORDINARY_DOTFILES[relpath], result
    listing = _in_view(s, name, "filesystem", {"operation": "list", "path": "."})
    assert {".github", ".eslintrc"} <= set(listing.tool_output)
    hits = _in_view(s, name, "search", {"pattern": "ESLINT_MARKER", "path": "."})
    assert '.eslintrc:1: {"root": true} ESLINT_MARKER' in hits.tool_output.splitlines()


@pytest.mark.parametrize("name", ["owner", "worktree", "snapshot"])
def test_credential_shaped_files_keep_their_existing_denial(dotted, name):
    """Before and after, MEASURED: the existing credential pattern (.env and .env.<suffix>) denies .env.example too -
    unchanged here (loosening a credential rule is not this unit's decision; owner question in the S18d report)."""
    s = dotted
    result = _in_view(s, name, "filesystem", {"operation": "read", "path": ".env.example"})
    assert result.status == SubtaskStatus.FAILED and result.reason_codes == ("SENSITIVE_PATH_DENIED",), result


CONTROL_ENTRIES = {"owner": {".git", ".kriya"}, "worktree": {".git"}, "snapshot": {".git", SENTINEL}}


@pytest.mark.xfail(strict=True, reason="S18d after-pin (OI-150 c): MEASURED failing on the unfixed bytes")
@pytest.mark.parametrize("name", ["owner", "worktree", "snapshot"])
def test_a_listing_leaves_out_the_control_entries(dotted, name):
    s = dotted
    listing = _in_view(s, name, "filesystem", {"operation": "list", "path": "."})
    assert listing.status == SubtaskStatus.COMPLETED, listing.error
    names = set(listing.tool_output)
    assert not names & CONTROL_ENTRIES[name]
    assert {"app.py", ".github", ".eslintrc"} <= names


CONTROL_READS = [
    ("owner", "filesystem", {"operation": "read", "path": ".git/config"}),
    ("owner", "filesystem", {"operation": "read", "path": ".git/HEAD"}),
    ("owner", "filesystem", {"operation": "list", "path": ".kriya"}),
    ("owner", "filesystem", {"operation": "list", "path": ".GIT"}),
    ("worktree", "filesystem", {"operation": "read", "path": ".git"}),
    ("snapshot", "filesystem", {"operation": "read", "path": SENTINEL}),
    ("snapshot", "filesystem", {"operation": "list", "path": ".git"}),
    ("snapshot", "ast", {"file_path": SENTINEL}),
    ("snapshot", "search", {"pattern": "scoped", "path": ".git"}),
]


@pytest.mark.xfail(strict=True, reason="S18d after-pin (OI-150 c): MEASURED failing on the unfixed bytes")
@pytest.mark.parametrize("name, tool, arguments", CONTROL_READS,
                         ids=[f"{n}-{t}-{a.get('path') or a.get('file_path')}" for n, t, a in CONTROL_READS])
def test_naming_a_control_path_is_refused_typed(dotted, name, tool, arguments):
    s = dotted
    result = _in_view(s, name, tool, arguments)
    assert result.status == SubtaskStatus.FAILED and result.reason_codes == ("TRUSTED_CONTROL_PATH_DENIED",), result
    assert result.tool_output is None


CONTROL_HITS = [("worktree", "gitdir"), ("snapshot", "workspace-scoped"), ("snapshot", "not an enclosing")]


@pytest.mark.xfail(strict=True, reason="S18d after-pin (OI-150 c): MEASURED failing on the unfixed bytes")
@pytest.mark.parametrize("name, pattern", CONTROL_HITS)
def test_a_search_never_hits_a_control_file(dotted, name, pattern):
    """MEASURED on the unfixed bytes: the worktree's .git gitfile (gitdir: <owner path>) and the snapshot sentinel were
    search hits."""
    s = dotted
    result = _in_view(s, name, "search", {"pattern": pattern, "path": "."})
    assert result.status == SubtaskStatus.COMPLETED and result.tool_output == "No matches found.", result


def test_the_sentinel_name_is_one_definition():
    """The scoped-snapshot sentinel the plugin layer refuses is the one the worktree code writes (no second copy)."""
    from kriya.workflow import worktree

    assert worktree._SCOPED_SNAPSHOT_SENTINEL == SENTINEL


@pytest.mark.xfail(strict=True, reason="S18d after-pin (OI-150 c): MEASURED failing on the unfixed bytes")
def test_the_sentinel_is_a_trusted_control_path_for_the_writer_too(tmp_path):
    """The canonical set (kriya.policy.filesystem, PLAT-039) now names the sentinel: a candidate write to it is refused
    with the existing code, like .git/.kriya; look-alike names stay ordinary."""
    from kriya.policy.filesystem import TRUSTED_CONTROL_NAMES, AuthorizedFileWriter, is_trusted_control_path
    from kriya.workflow import worktree

    assert SENTINEL in TRUSTED_CONTROL_NAMES and worktree._SCOPED_SNAPSHOT_SENTINEL == SENTINEL
    writer = AuthorizedFileWriter(str(tmp_path))
    for relpath in (SENTINEL, f"pkg/{SENTINEL}", ".KRIYA-SCOPED-SNAPSHOT"):
        assert is_trusted_control_path(str(tmp_path), str(tmp_path / relpath)), relpath
        assert writer.authorize(str(tmp_path / relpath)).reason_code == "TRUSTED_CONTROL_PATH_DENIED", relpath
    for relpath in (f"{SENTINEL}.md", "docs/kriya-scoped-snapshot", ".kriya-scoped-snapshots/x"):
        assert not is_trusted_control_path(str(tmp_path), str(tmp_path / relpath)), relpath


def test_the_canonical_control_rule_is_unchanged_for_its_existing_names(tmp_path):
    """Before and after: .git/.kriya at any depth are control paths; ordinary look-alikes are not (PLAT-039)."""
    from kriya.policy.filesystem import is_trusted_control_path

    for relpath in (".git", ".git/config", ".kriya/control/x", "sub/.git/HEAD", ".Kriya/x"):
        assert is_trusted_control_path(str(tmp_path), str(tmp_path / relpath)), relpath
    for relpath in (".github/workflows/ci.yml", ".gitignore", ".eslintrc", ".kriyarc", "src/.git_helper.py"):
        assert not is_trusted_control_path(str(tmp_path), str(tmp_path / relpath)), relpath
