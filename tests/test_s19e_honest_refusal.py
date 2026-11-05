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
import shlex
import shutil
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
def _isolated_git(scratch_dir, monkeypatch):
    home = scratch_dir("s19e-home")
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
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args], cwd=cwd,
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


def test_the_controller_route_ends_with_the_refusal_as_itself(tmp_path, monkeypatch):
    """WorkflowController.execute (legacy route, the CLI's composition root) above the same loop: no handler there
    turns the propagated refusal into a result; one Developer call."""
    from kriya.workflow.workflow_controller import WorkflowController

    workspace = _workspace(tmp_path)
    before = _tree(workspace)
    _refusing_closer(monkeypatch, workspace)
    eng, cfg, developer = _scripted_engine(lambda n: GOOD)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        asyncio.run(WorkflowController(eng).execute(goal=GOAL, workspace_path=workspace, migration_mode="legacy",
                                                    approval_callback=MagicMock(return_value=True)))
    assert developer == [PRIMARY]
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


def _translating_git(tmp_path: Path, monkeypatch) -> None:
    """Put a ``git`` first on PATH that is the real git (same output, same exit status) except that outside the C locale it states its ownership refusal in French,
    as a git with a French catalog does."""
    real = shlex.quote(shutil.which("git"))
    bindir = tmp_path / "translating-git"
    bindir.mkdir()
    stub = bindir / "git"
    stub.write_text(
        "#!/bin/sh\n"
        f'if [ "${{LC_ALL:-}}" = "C" ]; then exec {real} "$@"; fi\n'
        'err=$(mktemp); ' + real + ' "$@" 2>"$err"; status=$?\n'
        "if grep -q 'detected dubious ownership' \"$err\"; then echo \"fatal: propriétaire douteux détecté dans le dépôt à '$PWD'\" >&2; else cat \"$err\" >&2; fi\n"
        'rm -f "$err"; exit $status\n', encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


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
    """git translates its refusal (MEASURED on a developer host: LANGUAGE=fr -> "propriétaire douteux détecté"); the
    discovery probe reads it in the C locale.

    The translation is emulated at the process boundary by a ``git`` that behaves like the real one except that outside the C locale it answers in French. A real
    French catalog is NOT required: a hosted runner has no fr_FR locale, so git answered in English there and the test's own precondition failed (HOSTED-GIT-LOCALE-001).
    The contract proved is Kriya's (the probe asks git in the C locale), not the host's installed locales."""
    from kriya.workflow import worktree

    repo = _repository(tmp_path)
    _owned_elsewhere(monkeypatch)
    _translating_git(tmp_path, monkeypatch)
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
    folder.mkdir()                      # W3-3 U4: only an EMPTY folder is bootstrapped (a populated one is refused before any git command); the bootstrap commit is the subject
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
    """The real entry (begin_mutating_run + the engine): the typed ownership refusal as itself (before: RuntimeError
    'Failed to create an isolated generation sandbox: ... greenfield Git bootstrap commit failed: fatal: detected dubious
    ownership'); no Developer call, nothing written. D2 M3 (owner decision 2026-10-23): the carried-state inspection at the
    run entry asks git first, so the refusal now ends the run AT THE ENTRY, before the engine starts - no `.exception`
    trace row exists any more (the run never began), exactly like the stale-host-filters sibling below."""
    from kriya.control.run_coordinator import begin_mutating_run

    workspace = _workspace(tmp_path)
    before = _tree(workspace)
    eng, _cfg, developer = _scripted_engine(lambda n: GOOD)
    _owned_elsewhere(monkeypatch)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        with begin_mutating_run(workspace):
            _run(eng, workspace)
    monkeypatch.delenv("GIT_TEST_ASSUME_DIFFERENT_OWNER")
    _assert_ownership_refusal(raised, Path(workspace))
    assert developer == []
    assert _tree(workspace) == before


# ------------------------------------------------------------------- TAF-1: ownership is decided before filter inspection
#
# MEASURED at ea56d08 (TEST-A-final probe_s19e E4b/E4d, owner host shape): a repository git refuses for its ownership,
# with the operator's stale dormant `filter.media`/`filter.lfs` lines in ~/.gitconfig, was reported
# GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN, not GIT_REPOSITORY_OWNERSHIP_REFUSED. TRACED: git refuses the repository, so
# the inspection's layout probe has no work tree ((None, False)); activation cannot be decided and every operator
# definition is judged (`git media ...`: an evaluator) - before any caller asked git about ownership (the run entry
# begin_mutating_run and create_git_worktree both inspect first). Ownership is a prerequisite security decision: the
# inspection itself now asks git (C locale) before it reports a filter cause for a repository git refuses.

STALE_LFS = ('[filter "lfs"]\n\tclean = git-lfs clean -- %f\n\tsmudge = git-lfs smudge -- %f\n'
             '\tprocess = git-lfs filter-process\n\trequired = true\n')


def _host_filters(config: Path) -> None:
    with open(config, "a", encoding="utf-8") as handle:
        handle.write(STALE_MEDIA + STALE_LFS)


@pytest.mark.parametrize("stale, local", [(False, False), (True, False), (False, True), (True, True)],
                         ids=["clean-home", "stale-host-filters", "local-filter", "stale-and-local"])
def test_a_dubiously_owned_repository_is_the_ownership_refusal_whatever_the_filter_configuration(
        tmp_path, monkeypatch, _isolated_git, stale, local):
    """clean HOME and the owner's real HOME shape give the same reason for the same cause; nothing is written."""
    from kriya.workflow import worktree

    repo = _repository(tmp_path)
    if local:
        _git(repo, "config", "filter.kx.clean", "cat")
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "attributes").write_text("*.py filter=kx\n")
    if stale:
        _host_filters(_isolated_git)
    before = _tree(repo)
    _owned_elsewhere(monkeypatch)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        worktree.create_git_worktree(str(repo))
    _assert_ownership_refusal(raised, repo)
    monkeypatch.delenv("GIT_TEST_ASSUME_DIFFERENT_OWNER")
    assert _tree(repo) == before and not (repo / ".kriya").exists()


def test_the_inspection_itself_reports_ownership_for_every_caller(tmp_path, monkeypatch, _isolated_git):
    """Every guarded caller (run entry, GitTool, checkpoint, analyzer, ...) inspects through
    refuse_repository_content_filters: on a repository git refuses, the definitions it would otherwise judge without
    activation are never the reported cause - also in another language, and with the caller's own git and env."""
    from kriya.repository.git import refuse_repository_content_filters

    repo = _repository(tmp_path)
    _host_filters(_isolated_git)
    _owned_elsewhere(monkeypatch)
    monkeypatch.setenv("LANGUAGE", "fr")
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        refuse_repository_content_filters(str(repo))
    _assert_ownership_refusal(raised, repo)
    monkeypatch.delenv("GIT_TEST_ASSUME_DIFFERENT_OWNER")
    monkeypatch.delenv("LANGUAGE")
    env = {**os.environ, "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1", "LANGUAGE": "fr"}
    git = subprocess.run(["which", "git"], capture_output=True, text=True, check=True).stdout.strip()
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        refuse_repository_content_filters(str(repo), git=git, env=env, timeout=15)
    _assert_ownership_refusal(raised, repo)


def test_a_dubiously_owned_repository_without_filter_definitions_is_not_newly_refused_by_the_inspection(
        tmp_path, monkeypatch):
    """Negative control: with nothing to judge, the inspection returns exactly as before (the caller's own git meets
    git's refusal - create_git_worktree's discovery reports it); no extra refusal and no extra spawn on any path."""
    from kriya.repository import git as repository_git

    repo = _repository(tmp_path)
    _owned_elsewhere(monkeypatch)
    spawned = []
    real = repository_git.subprocess.run

    def counting(argv, *args, **kwargs):
        spawned.append(list(argv))
        return real(argv, *args, **kwargs)
    monkeypatch.setattr(repository_git.subprocess, "run", counting)
    assert repository_git.refuse_repository_content_filters(str(repo)) is None
    assert [argv[argv.index("git") + 1:][:1] if "git" in argv else argv for argv in spawned] and \
        not any("--is-inside-work-tree" in argv for argv in spawned), spawned


def test_valid_ownership_keeps_the_activation_aware_filter_contract(tmp_path, _isolated_git):
    """Positive controls (P1 activation-aware contract, unchanged): valid ownership + dormant host filters is allowed;
    valid ownership + an ACTIVE unprovable host filter is GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN."""
    from kriya.workflow import worktree

    _host_filters(_isolated_git)
    repo = _repository(tmp_path)
    assert os.path.isdir(worktree.create_git_worktree(str(repo)))
    (tmp_path / "active").mkdir()
    active = _repository(tmp_path / "active")
    (active / ".gitattributes").write_text("*.py filter=media\n")
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        worktree.create_git_worktree(str(active))
    assert raised.value.reason_code == GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN and raised.value.driver == "media"


def test_a_dubiously_owned_workspace_with_stale_host_filters_ends_a_real_run_with_the_ownership_refusal(
        tmp_path, monkeypatch, _isolated_git):
    """The real entry on the owner's host shape: begin_mutating_run inspects first and reports ownership, as with a
    clean HOME (test_a_dubiously_owned_workspace_ends_a_real_run_with_the_ownership_refusal)."""
    from kriya.control.run_coordinator import begin_mutating_run

    _host_filters(_isolated_git)
    workspace = _workspace(tmp_path)
    before = _tree(workspace)
    eng, _cfg, developer = _scripted_engine(lambda n: GOOD)
    _owned_elsewhere(monkeypatch)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        with begin_mutating_run(workspace):
            _run(eng, workspace)
    monkeypatch.delenv("GIT_TEST_ASSUME_DIFFERENT_OWNER")
    _assert_ownership_refusal(raised, Path(workspace))
    assert developer == []
    assert _tree(workspace) == before


def test_a_named_test_closure_refusal_ends_the_run_as_itself(tmp_path, monkeypatch):
    """TAF-3: the development loop lets the named-test closure's typed refusal through like its sibling closers'
    (before: its generic handler logged 'closure by named tests unavailable' and the run went on)."""
    from kriya.workflow import requirement_closers

    workspace = _workspace(tmp_path)
    before = _tree(workspace)
    calls = []

    def refusing(*_args, **_kwargs):
        calls.append(1)
        raise RepositoryContentFilterRefused(GIT_CONTENT_FILTER_REPOSITORY_DEFINED, workspace,
                                             key="filter.kx.smudge", scope="local", origin="file:.git/config")
    monkeypatch.setattr(requirement_closers, "close_requirements_with_named_tests", refusing)
    eng, cfg, developer = _scripted_engine(lambda n: GOOD)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        _run(eng, workspace)
    assert calls == [1] and developer == [PRIMARY], (calls, developer)
    _assert_refusal_ended_the_run(raised, cfg, workspace, before, GIT_CONTENT_FILTER_REPOSITORY_DEFINED)


# ------------------------------------------------------------------------- TAF-1: a dubiously owned submodule
#
# MEASURED (git 2.54.0): an ordinary operation in a valid parent (status, add -A, diff, commit -a) makes git run the
# populated submodule's own repository-local filter, and it does so with an explicit GIT_DIR for the submodule's git, which
# skips git's ownership check (made during discovery only). So a submodule's ownership is a prerequisite security decision
# of the parent's operation, not of git: Kriya must decide it itself, deterministically, whatever dormant host filters exist.

def _submodule_scenario(root: Path, config: Path, *, local_filter: bool, sub_safe: bool = False, stale: bool = False):
    """A valid parent (listed in safe.directory) with a populated submodule; optionally an ACTIVE repository-local filter
    in the submodule's own config. Returns (parent, submodule, marker) - the marker exists only if a filter ran."""
    child = root / "child"
    parent = root / "parent"
    for repo, name in ((child, "f.txt"), (parent, "p.txt")):
        repo.mkdir()
        (repo / name).write_text("x\n")
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
    _git(parent, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(child), "sub")
    _git(parent, "commit", "-q", "-m", "sub")
    sub = parent / "sub"
    marker = root / "FILTER_RAN"
    if local_filter:
        script = root / "clean.sh"
        script.write_text(f'#!/bin/sh\necho ran >> "{marker}"\ncat\n')
        script.chmod(0o755)
        _git(sub, "config", "filter.kx.clean", str(script))
        (sub / ".gitattributes").write_text("*.txt filter=kx\n")
    with open(config, "a", encoding="utf-8") as handle:
        if stale:
            handle.write(STALE_MEDIA + STALE_LFS)
        handle.write(f"[safe]\n\tdirectory = {os.path.realpath(parent)}\n")
        if sub_safe:
            handle.write(f"[safe]\n\tdirectory = {os.path.realpath(sub)}\n")
    return parent, sub, marker


def _assert_submodule_ownership_refusal(raised, sub: Path) -> None:
    assert type(raised.value) is RepositoryContentFilterRefused
    assert raised.value.reason_code == GIT_REPOSITORY_OWNERSHIP_REFUSED
    assert os.path.realpath(raised.value.repository) == os.path.realpath(sub)


@pytest.mark.parametrize("stale, local_filter", [(False, False), (True, False), (False, True), (True, True)],
                         ids=["clean-home", "stale-host-filters", "local-active-filter", "stale-and-local-active"])
def test_a_dubiously_owned_submodule_is_the_ownership_refusal_whatever_the_host_configuration(
        tmp_path, monkeypatch, _isolated_git, stale, local_filter):
    """The same cause gives the same reason in a clean HOME and in the owner's: the dormant host filters decide nothing.
    The refusal comes from the run entry's own inspection, before any git operation could run the submodule's filter."""
    from kriya.workflow import worktree

    parent, sub, marker = _submodule_scenario(tmp_path, _isolated_git, local_filter=local_filter, stale=stale)
    before = _tree(parent)
    _owned_elsewhere(monkeypatch)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        worktree.create_git_worktree(str(parent))
    _assert_submodule_ownership_refusal(raised, sub)
    monkeypatch.delenv("GIT_TEST_ASSUME_DIFFERENT_OWNER")
    assert not marker.exists(), "the submodule's repository-defined filter ran"
    assert _tree(parent) == before and not (parent / ".kriya").exists()


@pytest.mark.parametrize("stale", [False, True], ids=["clean-home", "stale-host-filters"])
def test_an_ambient_repository_selecting_environment_cannot_make_a_dubious_submodule_look_valid(
        tmp_path, monkeypatch, _isolated_git, stale):
    """The inspection asks about a submodule the way git does for its own child: the repository-selecting variables
    (GIT_DIR, GIT_WORK_TREE) are cleared - an ambient GIT_DIR (Kriya launched from a git hook, or an operator's
    export; child_environment() is a whole-environment copy and nothing else strips it) names the PARENT's git dir and
    would otherwise answer 'valid' for the submodule's folder."""
    from kriya.repository.git import refuse_repository_content_filters

    parent, sub, _marker = _submodule_scenario(tmp_path, _isolated_git, local_filter=False, stale=stale)
    _owned_elsewhere(monkeypatch)
    monkeypatch.setenv("GIT_DIR", str(parent / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(parent))
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        refuse_repository_content_filters(str(parent))
    _assert_submodule_ownership_refusal(raised, sub)


def test_a_validly_owned_submodule_is_still_judged_by_its_filters_and_asks_no_ownership_question(
        tmp_path, monkeypatch, _isolated_git):
    """Controls: (1) valid ownership + dormant host filters + no local filter is allowed, with no ownership probe for the
    submodule (git gave it a work tree: nothing to ask); (2) valid ownership + an ACTIVE repository-local filter in the
    submodule is the repository-defined refusal at the submodule."""
    from kriya.repository import git as repository_git

    root = tmp_path / "dormant"
    root.mkdir()
    parent, sub, _marker = _submodule_scenario(root, _isolated_git, local_filter=False, sub_safe=True, stale=True)
    asked = []
    original = repository_git._Caller.refuse_dubious_ownership

    def recording(self, path, *, elsewhere):
        asked.append(path)
        return original(self, path, elsewhere=elsewhere)
    monkeypatch.setattr(repository_git._Caller, "refuse_dubious_ownership", recording)
    _owned_elsewhere(monkeypatch)
    assert repository_git.refuse_repository_content_filters(str(parent)) is None
    assert asked == [], asked
    monkeypatch.delenv("GIT_TEST_ASSUME_DIFFERENT_OWNER")

    active_root = tmp_path / "active"
    active_root.mkdir()
    parent, sub, _marker = _submodule_scenario(active_root, _isolated_git, local_filter=True, sub_safe=True)
    _owned_elsewhere(monkeypatch)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        repository_git.refuse_repository_content_filters(str(parent))
    assert raised.value.reason_code == GIT_CONTENT_FILTER_REPOSITORY_DEFINED
    assert os.path.realpath(raised.value.repository) == os.path.realpath(sub)


def test_a_plain_folder_with_host_filters_is_not_asked_an_ownership_question(tmp_path, monkeypatch, _isolated_git):
    """Control for the ownership probe's cost: git's own 'not a git repository' answer is final - no repository, so no
    ownership to judge and no filter that can run - so a non-git workspace under a HOME that carries filter definitions is
    allowed without the extra discovery spawn, exactly as before."""
    from kriya.repository import git as repository_git

    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("x = 1\n")
    _host_filters(_isolated_git)
    asked = []
    original = repository_git._Caller.refuse_dubious_ownership

    def recording(self, path, *, elsewhere):
        asked.append(path)
        return original(self, path, elsewhere=elsewhere)
    monkeypatch.setattr(repository_git._Caller, "refuse_dubious_ownership", recording)
    assert repository_git.refuse_repository_content_filters(str(plain)) is None
    assert asked == [], asked
