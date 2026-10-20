"""S19e (OI-162, OI-163): honest refusal reporting - the authoritative final outcome keeps the real failure class.

OI-162 (MEASURED at a0eb5f0, scratch s19e MEASURE_A): a typed repository refusal (RepositoryContentFilterRefused) raised
by a closer INSIDE an attempt was caught by attempt_lifecycle.run_iteration's ``except Exception`` and routed to
handle_attempt_failure: recorded as a failed attempt and RETRIED (7 Developer calls for one refusal), and the run ended
``failure_category=quality_gates_exhausted`` with the reason code nowhere in the result (its last_failure was a later
attempt's protocol error). Best-of-N (best_of_n.run_attempt_with_best_of_n) is a second retry wrapper with the same
catch, and its worktree-reset catch absorbed a refusal as "no fresh sandbox". The contract (TRACED): the refusal class
is deliberately not a RuntimeError/OSError/ValueError "so no handler written for an ordinary git failure absorbs it",
and every other site lets it reach the run's caller as itself (run entry, sandbox creation, the four closers, the
enforce plan worktree); a security decision is never widened by retry. Fixed: the two wrappers let it through, so the
run raises it with its reason code and the run's ``.exception`` trace row names it.

OI-163 (MEASURED at a0eb5f0, scratch s19e probe_b): create_git_worktree on a repository git refuses for its ownership
(GIT_TEST_ASSUME_DIFFERENT_OWNER=1) took the greenfield path - it WROTE .git/info/exclude into the refused repository
- and failed "greenfield Git bootstrap commit failed: fatal: detected dubious ownership". Fixed: the typed ownership
refusal GIT_REPOSITORY_OWNERSHIP_REFUSED (the one closed refusal table, kriya/repository/git.py), raised before the
bootstrap writes anything; git's ownership check is never weakened (no safe.directory, no retry without it). A genuine
bootstrap failure is still reported as one.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
from _protocol_responses import sentinel, wants_structured  # noqa: E402  # pylint: disable=wrong-import-position
from _scripted_run import PRIMARY, engine  # noqa: E402  # pylint: disable=wrong-import-position

from kriya.core.state_paths import trace_db_path  # noqa: E402
from kriya.repository.git import (  # noqa: E402
    GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN,
    GIT_CONTENT_FILTER_REPOSITORY_DEFINED,
    GIT_REPOSITORY_OWNERSHIP_REFUSED,
    RepositoryContentFilterRefused,
)

REPO = Path(__file__).resolve().parents[1]
BASELINE = "def add(a, b):\n    return a + b\n"
GOOD = BASELINE + "\n\ndef sub(a, b):\n    return a - b\n"
FAILING = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"     # fails the full regression
def _drifting(n: int) -> str:
    """A different wrong answer every attempt (assert 3+n == 3): never the PRD-026 no-progress repeat."""
    return f"def add(a, b):\n    return a + b + {n}\n\n\ndef sub(a, b):\n    return a - b\n"


FILES = {"calc.py": BASELINE, "test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
         ".gitignore": ".kriya/\n__pycache__/\n.pytest_cache/\n"}
# The goal carries a mutation-scope requirement, so the pre-apply closers reach requirement_closers.mutation_scope_evidence
GOAL = "Add a function sub(a, b) returning a - b to `calc.py`.\n\nDo not modify any other file in the repository.\n"
# Best-of-N samples only while no authoritative repair state is active; a goal requirement's repair contract is one, so
# the Best-of-N runs carry a plain goal (STATE-BEST-OF-N-HANDOFF-001).
PLAIN_GOAL = "Add a function sub(a, b) returning a - b to calc.py."
# OI-161's host shape: an operator `filter.media` whose program is git (an evaluator) - dormant until a path activates it
STALE_MEDIA = '[filter "media"]\n\tclean = git media clean %f\n\tsmudge = git media smudge %f\n\trequired = true\n'


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("s19e-home")
    config = home / ".gitconfig"
    config.write_text("[user]\n\tname = s19e\n\temail = s19e@local\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(home))
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                 "GIT_TEST_ASSUME_DIFFERENT_OWNER", "GIT_CEILING_DIRECTORIES", "GIT_ATTR_SOURCE", "LANGUAGE"):
        monkeypatch.delenv(name, raising=False)
    return config


def _git(cwd, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args], cwd=cwd,
                          check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout


def _workspace(root: Path) -> str:
    workspace = root / "calc"
    workspace.mkdir()
    for name, content in FILES.items():
        (workspace / name).write_text(content)
    _git(workspace, "init", "-q", "-b", "main")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-q", "-m", "initial")
    return str(workspace)


def _tree(root) -> dict:
    """Every file under ``root`` (``.git`` included) -> its sha256. Excluded: Kriya's own ``.kriya`` state and the
    registration of its managed sandbox worktree (``.git/worktrees/``): a run that ends with an exception (any
    exception, as before S19e) leaves its sandbox, which the next run inspects again and resets before reuse."""
    found = {}
    for directory, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".kriya" and os.path.join(directory, d) != os.path.join(root, ".git", "worktrees")]
        for name in names:
            path = os.path.join(directory, name)
            with open(path, "rb") as handle:
                found[os.path.relpath(path, root)] = hashlib.sha256(handle.read()).hexdigest()
    return found


def _scripted_engine(developer, *, configure=None):
    """A real WorkflowEngine (kriya's own retry, recovery, gates, closers) with a scripted model answering by ROLE."""
    eng, llm, cfg = engine(chain=False, configure=configure)
    calls = []

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        system_prompt = system_prompt or ""
        text = f"{system_prompt}\n{user_prompt}"
        if system_prompt.startswith("You are the Kriya Planner Agent"):
            return "Step 1: add sub to calc.py"
        if system_prompt.startswith("You are the Kriya Architect Agent"):
            return json.dumps({"files": ["calc.py"]})
        if not system_prompt.startswith(("You are the Kriya Developer Agent", "You are the Kriya File List Planner")):
            return json.dumps({"verdict": "PASS", "requirements": []})
        calls.append(kwargs.get("model_override") or PRIMARY)
        content = developer(len(calls))
        if wants_structured(system_prompt):
            return sentinel("calc.py", analysis="repair calc.py", content=content)
        if "FILE CONTENT:" in text:
            return f"FIX ANALYSIS: repair calc.py\nFILE CONTENT:\n{content}"
        return json.dumps([{"filepath": "calc.py", "content": content}])

    llm.complete = complete
    return eng, cfg, calls


def _run(eng, workspace: str, goal: str = GOAL):
    return asyncio.run(eng.run_generation_workflow(goal=goal, workspace_path=workspace,
                                                   approval_callback=MagicMock(return_value=True)))


def _refusing_closer(monkeypatch, workspace: str, *, on_call: int = 1):
    """requirement_closers.mutation_scope_evidence (the closer's guarded git read) refuses on its ``on_call``-th call
    with the typed refusal the S19 Q1 inspection raises; earlier calls run the real function."""
    from kriya.workflow import requirement_closers

    real = requirement_closers.mutation_scope_evidence
    calls = []

    def closer(*args, **kwargs):
        calls.append(1)
        if len(calls) >= on_call:
            raise RepositoryContentFilterRefused(GIT_CONTENT_FILTER_REPOSITORY_DEFINED, workspace,
                                                 key="filter.kx.clean", scope="local", origin="file:.git/config")
        return real(*args, **kwargs)

    monkeypatch.setattr(requirement_closers, "mutation_scope_evidence", closer)
    return calls


def _exception_row(cfg) -> dict:
    conn = sqlite3.connect(trace_db_path(cfg))
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute("SELECT * FROM runs WHERE run_id LIKE '%.exception'")]
    finally:
        conn.close()
    assert len(rows) == 1, rows
    return rows[0]


def _assert_refusal_ended_the_run(raised, cfg, workspace: str, before: dict, code: str) -> None:
    """The run's caller holds the refusal itself (class + reason code), the run's own .exception trace row names it,
    the workspace is untouched, and its lock was released (the next run can start)."""
    from kriya.control.run_coordinator import begin_mutating_run

    assert type(raised.value) is RepositoryContentFilterRefused
    assert raised.value.reason_code == code and str(raised.value).startswith(f"{code}: ")
    row = _exception_row(cfg)
    assert (row["status"], row["failure_category"]) == ("error", "RepositoryContentFilterRefused")
    events = [event for event in json.loads(row["run_events"] or "[]") if event["kind"] == "run.exception"]
    assert len(events) == 1 and events[0]["details"]["exception_type"] == "RepositoryContentFilterRefused"
    assert events[0]["details"]["message"].startswith(f"{code}: ")
    assert _tree(workspace) == before                                   # nothing applied or committed
    with begin_mutating_run(workspace) as context:                       # the workspace lock was released
        assert context.run_id


# ------------------------------------------------------------------------------------------- OI-162: the attempt


def test_a_refusal_in_the_first_attempt_ends_the_run_as_itself_without_a_retry(tmp_path, monkeypatch):
    from kriya.workflow import requirement_closers

    workspace = _workspace(tmp_path)
    before = _tree(workspace)
    real = requirement_closers.mutation_scope_evidence
    closer = _refusing_closer(monkeypatch, workspace)
    eng, cfg, developer = _scripted_engine(lambda n: GOOD)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        _run(eng, workspace)
    assert closer == [1], "the closer did not refuse exactly once"
    assert developer == [PRIMARY], f"the refusal was retried: {len(developer)} Developer calls"
    _assert_refusal_ended_the_run(raised, cfg, workspace, before, GIT_CONTENT_FILTER_REPOSITORY_DEFINED)
    # The sandbox the refused run left is reused by the next run (inspected again, reset): it succeeds.
    monkeypatch.setattr(requirement_closers, "mutation_scope_evidence", real)
    again, _cfg, _calls = _scripted_engine(lambda n: GOOD)
    result = _run(again, workspace)
    assert result["quality_gates_passed"] is True and Path(workspace, "calc.py").read_text() == GOOD


def test_a_refusal_after_a_retried_gate_failure_keeps_its_cause(tmp_path, monkeypatch):
    """The REAL retry sequence: attempt 1's closer runs for real and its full regression fails (an ordinary retryable
    failure, recorded and retried); attempt 2's closer refuses. The earlier retry state cannot turn the refusal into
    an exhaustion: the run raises the refusal after exactly two Developer calls."""
    workspace = _workspace(tmp_path)
    before = _tree(workspace)
    closer = _refusing_closer(monkeypatch, workspace, on_call=2)     # attempt 1's closer runs for real
    eng, cfg, developer = _scripted_engine(lambda n: FAILING if n == 1 else GOOD)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        _run(eng, workspace)
    assert closer == [1, 1] and len(developer) == 2, (closer, developer)
    _assert_refusal_ended_the_run(raised, cfg, workspace, before, GIT_CONTENT_FILTER_REPOSITORY_DEFINED)


def test_a_refusal_on_the_last_permitted_attempt_is_not_reported_as_exhaustion(tmp_path, monkeypatch):
    """The same trajectory twice. Run 1 (no refusal): every attempt fails and the retry budget ends the run
    (quality_gates_exhausted) after N attempts, each running the compile gate once. Run 2: identical, except the N-th
    (last permitted) attempt's compile gate meets the refusal (the injected trigger stands for any guarded git call
    inside the attempt) - the attempt whose failure would have exhausted the budgets. The run raises the refusal;
    exhaustion never overwrites it."""
    from kriya.tools.validate import PolymorphicValidator

    real = PolymorphicValidator.run_compile_check
    compiled = []
    refuse_on = []

    def compile_gate(self, *args, **kwargs):
        compiled.append(1)
        if refuse_on and len(compiled) == refuse_on[0]:
            raise RepositoryContentFilterRefused(GIT_CONTENT_FILTER_REPOSITORY_DEFINED, str(self.workspace_path),
                                                 key="filter.kx.clean", scope="local", origin="file:.git/config")
        return real(self, *args, **kwargs)
    monkeypatch.setattr(PolymorphicValidator, "run_compile_check", compile_gate)
    reference = tmp_path / "reference"
    reference.mkdir()
    eng, _cfg, reference_calls = _scripted_engine(_drifting)
    exhausted = _run(eng, _workspace(reference))
    assert (exhausted["quality_gates_passed"], exhausted["failure_category"]) == (False, "quality_gates_exhausted")
    assert len(compiled) == len(reference_calls) > 1, (compiled, reference_calls)

    refuse_on.append(len(compiled))
    compiled.clear()
    workspace = _workspace(tmp_path)
    before = _tree(workspace)
    eng, cfg, developer = _scripted_engine(_drifting)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        _run(eng, workspace)
    assert len(compiled) == refuse_on[0] and developer == reference_calls, (compiled, developer)
    _assert_refusal_ended_the_run(raised, cfg, workspace, before, GIT_CONTENT_FILTER_REPOSITORY_DEFINED)


def _best_of(n):
    """Best-of-N over ``n`` candidates, in guardrails mode: the executing gates (compile, tests) then run INSIDE
    run_attempt, where Best-of-N catches a candidate's failure (in human-in-the-loop mode they are held until after
    the approval, Gap B, outside Best-of-N)."""
    def configure(cfg):
        cfg.autonomy.best_of_n_first_attempt = n
        cfg.autonomy.mode = "guardrails"
    return configure


def test_a_best_of_n_candidate_s_refusal_starts_no_independent_candidate(tmp_path, monkeypatch):
    """Best-of-N is the second retry wrapper (it catches what run_attempt raises): a candidate whose attempt meets the
    refusal is not recorded and no fresh candidate follows. The refusal is raised from run_attempt's compile gate
    (the injected trigger stands for any guarded git call inside the attempt); best-of-N and the loop are real."""
    from kriya.tools.validate import PolymorphicValidator
    from kriya.workflow import retry_strategy

    workspace = _workspace(tmp_path)
    before = _tree(workspace)
    compiled = []

    def refusing_compile(*_args, **_kwargs):
        compiled.append(1)
        raise RepositoryContentFilterRefused(GIT_CONTENT_FILTER_REPOSITORY_DEFINED, workspace, key="filter.kx.clean",
                                             scope="local", origin="file:.git/config")
    monkeypatch.setattr(PolymorphicValidator, "run_compile_check", refusing_compile)
    handled = []
    real = retry_strategy.handle_attempt_failure

    async def counting(state, ctx, exc):
        handled.append(exc)
        return await real(state, ctx, exc)
    # best-of-N reaches it through retry_strategy at call time (the loop's own route is the facade's, not counted)
    monkeypatch.setattr(retry_strategy, "handle_attempt_failure", counting)
    eng, cfg, developer = _scripted_engine(lambda n: GOOD, configure=_best_of(3))
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        _run(eng, workspace, PLAIN_GOAL)
    assert compiled == [1] and developer == [PRIMARY] and handled == [], (compiled, developer, handled)
    _assert_refusal_ended_the_run(raised, cfg, workspace, before, GIT_CONTENT_FILTER_REPOSITORY_DEFINED)


def _first_compile_fails(monkeypatch) -> list:
    """Candidate 1's compile gate reports an ordinary compile failure (a retryable candidate failure inside
    run_attempt, where best-of-N catches it); every later compile gate is the real one."""
    from kriya.tools.validate import PolymorphicValidator

    real = PolymorphicValidator.run_compile_check
    compiled = []

    def compile_gate(self, *args, **kwargs):
        compiled.append(1)
        if len(compiled) == 1:
            return {"success": False, "output": "Build error: dependency resolution failed."}
        return real(self, *args, **kwargs)
    monkeypatch.setattr(PolymorphicValidator, "run_compile_check", compile_gate)
    return compiled


def test_a_refused_best_of_n_sandbox_reset_is_not_absorbed_as_no_fresh_sandbox(tmp_path, monkeypatch):
    """Candidate 1 fails its compile gate (recorded once); the fresh sandbox for candidate 2 is refused. Before: logged
    as "worktree reset failed" and the loop went on. The normal (non-refusal) outputs of that handler are pinned by
    test_sm_best_of_n's test_a_failed_worktree_reset_keeps_the_recorded_failure_and_the_state_it_was_recorded_in (an
    OSError: absorbed, the run continues) and test_a_failed_first_candidate_gets_a_fresh_sandbox below."""
    from kriya.workflow import worktree

    workspace = _workspace(tmp_path)
    before = _tree(workspace)
    _first_compile_fails(monkeypatch)
    resets = []

    def refused_reset(path):
        resets.append(path)
        raise RepositoryContentFilterRefused(GIT_CONTENT_FILTER_REPOSITORY_DEFINED, path, key="filter.kx.smudge",
                                             scope="local", origin="file:.git/config")
    eng, cfg, developer = _scripted_engine(lambda n: GOOD, configure=_best_of(3))
    # best_of_n.py imports create_git_worktree at call time; the run's own sandbox was created through its deps.
    monkeypatch.setattr(worktree, "create_git_worktree", refused_reset)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        _run(eng, workspace, PLAIN_GOAL)
    assert resets == [workspace] and developer == [PRIMARY], (resets, developer)
    _assert_refusal_ended_the_run(raised, cfg, workspace, before, GIT_CONTENT_FILTER_REPOSITORY_DEFINED)


def test_a_failed_first_candidate_gets_a_fresh_sandbox(tmp_path, monkeypatch):
    """Normal output of best-of-N's failure and reset handlers (no refusal): candidate 1 fails its compile gate, a
    fresh sandbox is created (the real create_git_worktree, once), candidate 2 succeeds; the run passes."""
    from kriya.workflow import worktree

    workspace = _workspace(tmp_path)
    compiled = _first_compile_fails(monkeypatch)
    real = worktree.create_git_worktree
    resets = []

    def counting_reset(path):
        resets.append(path)
        return real(path)
    monkeypatch.setattr(worktree, "create_git_worktree", counting_reset)
    eng, _cfg, developer = _scripted_engine(lambda n: GOOD, configure=_best_of(2))
    result = _run(eng, workspace, PLAIN_GOAL)
    assert result["quality_gates_passed"] is True and result["failure_category"] is None
    assert resets == [workspace] and len(compiled) == 2 and developer == [PRIMARY, PRIMARY]
    assert Path(workspace, "calc.py").read_text() == GOOD


def test_an_unfaked_candidate_activated_refusal_ends_the_run_as_itself(tmp_path, monkeypatch, _isolated_git):
    """No faked refusal: the operator's dormant `filter.media` (OI-161's host line) becomes ACTIVE when the candidate
    tree holds a `.gitattributes` binding it, and the REAL inspection inside the REAL closer refuses
    (GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN, S19 P1d). The candidate's attribute file is planted the moment the closer
    starts (the scripted model writes calc.py only); everything after is Kriya's own code and git."""
    from kriya.workflow import requirement_closers

    with open(_isolated_git, "a", encoding="utf-8") as handle:
        handle.write(STALE_MEDIA)
    workspace = _workspace(tmp_path)
    before = _tree(workspace)
    real = requirement_closers.mutation_scope_evidence
    planted = []

    def candidate_binds_media(deps, candidate_root, *args, **kwargs):
        Path(candidate_root, ".gitattributes").write_text("*.py filter=media\n")
        planted.append(candidate_root)
        return real(deps, candidate_root, *args, **kwargs)
    monkeypatch.setattr(requirement_closers, "mutation_scope_evidence", candidate_binds_media)
    eng, cfg, developer = _scripted_engine(lambda n: GOOD)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        _run(eng, workspace)
    assert planted and developer == [PRIMARY], (planted, developer)
    assert raised.value.driver == "media"
    _assert_refusal_ended_the_run(raised, cfg, workspace, before, GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN)


def _handlers_of(path: str, function: str):
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    target = next(node for node in ast.walk(tree)
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function)
    for node in ast.walk(target):
        if isinstance(node, ast.Try):
            yield [getattr(handler.type, "id", None) for handler in node.handlers], node.handlers


@pytest.mark.parametrize("path, function", [("kriya/workflow/attempt_lifecycle.py", "run_iteration"),
                                            ("kriya/workflow/best_of_n.py", "run_attempt_with_best_of_n")])
def test_every_generic_handler_of_the_retry_wrappers_lets_the_refusal_through(path, function):
    """Structural twin of the runtime tests: in the two retry wrappers every ``except Exception`` is preceded, in the
    same try, by ``except RepositoryContentFilterRefused: raise`` (a bare re-raise)."""
    generic = 0
    for names, handlers in _handlers_of(path, function):
        if "Exception" not in names:
            continue
        generic += 1
        position = names.index("RepositoryContentFilterRefused")
        assert position < names.index("Exception"), (path, function, names)
        body = handlers[position].body
        assert isinstance(body[0], ast.Raise) and body[0].exc is None, (path, function)
    assert generic == (1 if function == "run_iteration" else 2)


# ------------------------------------------------------------------------------------------- OI-163: ownership


def _owned_elsewhere(monkeypatch) -> None:
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")      # git's own test switch: every repository is foreign


def _repository(root: Path, *, commit: bool = True) -> Path:
    repo = root / "owner"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "init", "-q", "-b", "main")
    if commit:
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
    return repo


def _assert_ownership_refusal(raised, repo: Path) -> None:
    assert type(raised.value) is RepositoryContentFilterRefused
    assert raised.value.reason_code == GIT_REPOSITORY_OWNERSHIP_REFUSED
    message = str(raised.value)
    assert message.startswith(f"{GIT_REPOSITORY_OWNERSHIP_REFUSED}: git refuses to operate on the repository at {repo}")
    assert "fatal: detected dubious ownership" in message and "bootstrap" not in message


@pytest.mark.parametrize("commit", [True, False], ids=["committed", "no-commit"])
def test_a_dubiously_owned_repository_is_the_ownership_refusal_and_nothing_is_written(tmp_path, monkeypatch, commit):
    from kriya.workflow import worktree

    repo = _repository(tmp_path, commit=commit)
    before = _tree(repo)
    _owned_elsewhere(monkeypatch)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        worktree.create_git_worktree(str(repo))
    _assert_ownership_refusal(raised, repo)
    monkeypatch.delenv("GIT_TEST_ASSUME_DIFFERENT_OWNER")
    assert _tree(repo) == before, "the refused repository was written (the greenfield bootstrap ran)"
    assert not (repo / ".kriya").exists()
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_the_ownership_refusal_is_recognised_whatever_git_s_language(tmp_path, monkeypatch):
    """git translates its refusal (MEASURED on this host: LANGUAGE=fr -> "propriétaire douteux détecté"); the
    discovery probe reads it in the C locale."""
    from kriya.workflow import worktree

    repo = _repository(tmp_path)
    _owned_elsewhere(monkeypatch)
    monkeypatch.setenv("LANGUAGE", "fr")
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    translated = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL)
    assert translated.returncode == 128 and "dubious ownership" not in translated.stderr, translated.stderr
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        worktree.create_git_worktree(str(repo))
    _assert_ownership_refusal(raised, repo)


def test_a_plain_folder_whose_bootstrap_commit_git_refuses_for_ownership_is_the_ownership_refusal(tmp_path, monkeypatch):
    """`git init` does not check ownership, the commit does: the bootstrap commit's ownership failure is classified by
    asking git again in the C locale. CHARACTERIZED, unchanged (S19e STOP, owner decision): the `.git` the greenfield
    init created before the commit was refused stays (the pre-existing bootstrap behaviour)."""
    from kriya.workflow import worktree

    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "a.py").write_text("x = 1\n")
    _owned_elsewhere(monkeypatch)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        worktree.create_git_worktree(str(folder))
    _assert_ownership_refusal(raised, folder)
    assert (folder / ".git" / "HEAD").is_file()


def test_a_genuine_bootstrap_commit_failure_is_still_the_bootstrap_failure(tmp_path, monkeypatch):
    """A real, non-ownership git failure of the bootstrap commit (its index lock cannot be created): reported exactly
    as before (the message is the base's, byte for byte), never as an ownership refusal."""
    from kriya.workflow import worktree

    folder = tmp_path / "plain"
    folder.mkdir()
    missing = tmp_path / "no-such-directory" / "index"
    monkeypatch.setenv("GIT_INDEX_FILE", str(missing))
    with pytest.raises(RuntimeError) as raised:
        worktree.create_git_worktree(str(folder))
    assert not isinstance(raised.value, RepositoryContentFilterRefused)
    assert str(raised.value) == (
        f"Git repository detection/bootstrap failed for {str(folder)!r}: greenfield Git bootstrap commit failed: "
        f"fatal: Unable to create '{missing}.lock': No such file or directory")


def test_a_dubiously_owned_workspace_ends_a_real_run_with_the_ownership_refusal(tmp_path, monkeypatch):
    """The real entry (begin_mutating_run + the engine): the run reaches the sandbox creation and ends with the typed
    ownership refusal as itself (before: RuntimeError 'Failed to create an isolated generation sandbox: ... greenfield
    Git bootstrap commit failed: fatal: detected dubious ownership'); no Developer call, nothing written."""
    from kriya.control.run_coordinator import begin_mutating_run

    workspace = _workspace(tmp_path)
    before = _tree(workspace)
    eng, cfg, developer = _scripted_engine(lambda n: GOOD)
    _owned_elsewhere(monkeypatch)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        with begin_mutating_run(workspace):
            _run(eng, workspace)
    monkeypatch.delenv("GIT_TEST_ASSUME_DIFFERENT_OWNER")
    _assert_ownership_refusal(raised, Path(workspace))
    assert developer == []
    row = _exception_row(cfg)
    assert (row["status"], row["failure_category"]) == ("error", "RepositoryContentFilterRefused")
    assert _tree(workspace) == before
