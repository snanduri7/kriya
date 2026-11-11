"""RUN-SOURCE-IDENTITY-001 (K-E; Scenario-A run 6, 2026-11-11): ``run.opened.kriya_build`` said commit UNKNOWN / tree UNKNOWN /
``build_provenance: unavailable`` for a run executed from a Git worktree at a known HEAD, so the run's own evidence did not name
the Kriya source that produced it.

Invariant (owner rulings 2026-11-11): ONE STATE authority, ``kriya.control.source_identity``, reads the identity of Kriya's own
checkout (the only ``content_filters=False`` site). ``begin_mutating_run`` - the only place a RunContext is built - calls its
parameterless ``run_source_identity()`` once per top-level run and puts the immutable value on the RunContext; the evidence
recorder serializes that value into the existing ``kriya_build`` and reads no git; ``release_identity.kriya_revision`` delegates
to the same implementation. HEAD is recorded as it is; ``tree`` stays UNKNOWN (not read, never inferred); a typed refusal stays
a typed unavailable value, never a guessed one.

tests/conftest.py pins the run-side reader for every OTHER test. The ``real_run_source_identity`` fixture here undoes that pin:
only these controls are evidence that the identity works.
"""
import ast
import dataclasses
import json
import os
import subprocess
from pathlib import Path

import pytest
from _strict_doubles import strict_config

from kriya.control import run_coordinator, source_identity
from kriya.control.run_coordinator import RunContext, begin_mutating_run
from kriya.control.source_identity import EMBEDDED, SOURCE_CHECKOUT, UNAVAILABLE, SourceIdentity
from kriya.core import release_identity
from kriya.core.attempt_evidence import explain, reader, scope
from kriya.core.state_paths import ENV_STATE_DIR
from kriya.foundation import build_info
from kriya.repository.git import (
    GIT_ATTR_TREE_CONFIGURED,
    RepositoryContentFilterRefused,
    refuse_repository_content_filters,
)

ROOT = Path(__file__).resolve().parents[1]
_GIT = ["git", "-c", "user.email=test@kriya.local", "-c", "user.name=Kriya test", "-c", "commit.gpgsign=false",
        "-c", "maintenance.auto=false"]
MANIFEST_KEYS = {"kriya_build", "workspace_identity", "workspace_path", "base_revision", "base_tree_hash"}


def _git(repo: Path, *args: str) -> str:
    """Fixture-building git: the developer's own global configuration is never read (this host's global ``filter.lfs`` would
    otherwise run while a fixture is built)."""
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run([*_GIT, *args], cwd=repo, check=True, capture_output=True, text=True, env=env).stdout.strip()


def _commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A stand-in Kriya source checkout with two commits (so ``HEAD~1`` exists and differs from HEAD)."""
    repo = tmp_path / "kriya-source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, "code.py", "x = 1\n")
    _commit(repo, "NOTES.md", "docs only\n")
    return repo


@pytest.fixture
def workspace(tmp_path: Path) -> str:
    """A target workspace: its HEAD is NOT the Kriya source's."""
    repo = tmp_path / "ws"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, "tracked.txt", "v1\n")
    return str(repo)


@pytest.fixture
def real_run_source_identity(monkeypatch) -> None:
    """Undo the conftest pin: the run path calls the REAL reader."""
    monkeypatch.setattr(run_coordinator, "run_source_identity", source_identity.run_source_identity)


@pytest.fixture
def no_embedded_build(monkeypatch, tmp_path: Path) -> None:
    """Hermetic: no kriya/_build_info.json (a developer's local setup.py build may have written one)."""
    monkeypatch.setattr(build_info, "BUILD_INFO_FILE", str(tmp_path / "absent" / "_build_info.json"))


def _opened(run_id: str):
    run = reader.open_run(os.environ[ENV_STATE_DIR], run_id)
    (opened,) = [record for record in run.records() if record["kind"] == "run.opened"]
    return run, opened["payload"]


# ---- the one reader ------------------------------------------------------------------------------------------------------------

def test_a_git_checkout_reports_its_exact_head_and_a_clean_flag(checkout: Path) -> None:
    head = _git(checkout, "rev-parse", "HEAD")
    assert source_identity.read_kriya_source_identity(str(checkout)) == SourceIdentity(head, False, SOURCE_CHECKOUT, None)
    assert len(head) == 40 and head != _git(checkout, "rev-parse", "HEAD~1")      # a stale / previous revision is a different value


def test_head_is_recorded_as_it_is_never_an_inferred_last_code_commit(checkout: Path) -> None:
    """The fixture's HEAD is a docs-only commit: it IS the identity; nothing filters 'non-executable' commits."""
    assert _git(checkout, "show", "--name-only", "--format=", "HEAD") == "NOTES.md"
    assert source_identity.read_kriya_source_identity(str(checkout)).revision == _git(checkout, "rev-parse", "HEAD")


def test_a_modified_tracked_file_is_dirty_and_an_untracked_file_is_not(checkout: Path) -> None:
    (checkout / "scratch.txt").write_text("untracked\n", encoding="utf-8")
    assert source_identity.read_kriya_source_identity(str(checkout)).dirty is False
    (checkout / "code.py").write_text("x = 2\n", encoding="utf-8")
    identity = source_identity.read_kriya_source_identity(str(checkout))
    assert identity == SourceIdentity(_git(checkout, "rev-parse", "HEAD"), True, SOURCE_CHECKOUT, None)


def test_a_directory_that_is_not_a_checkout_is_unavailable_or_the_embedded_build(tmp_path: Path, monkeypatch, no_embedded_build) -> None:
    assert source_identity.read_kriya_source_identity(str(tmp_path)) == SourceIdentity(None, None, UNAVAILABLE, None)
    embedded = tmp_path / "_build_info.json"
    embedded.write_text(json.dumps({"schema": 1, "git_commit": "a" * 40, "git_tree": "b" * 40, "dirty": False}), encoding="utf-8")
    monkeypatch.setattr(build_info, "BUILD_INFO_FILE", str(embedded))
    assert build_info.embedded_build_info()["commit"] == "a" * 40           # validity: the embedded build is readable
    assert source_identity.read_kriya_source_identity(str(tmp_path)) == SourceIdentity("a" * 40, False, EMBEDDED, None)


def test_a_directory_inside_someone_elses_checkout_never_borrows_that_checkouts_head(checkout: Path, no_embedded_build) -> None:
    """A Kriya installed under another project's repository (a virtualenv inside the project) is not that project's source."""
    nested = checkout / ".venv" / "lib" / "site-packages"
    nested.mkdir(parents=True)
    assert _git(nested, "rev-parse", "HEAD") == _git(checkout, "rev-parse", "HEAD")   # validity: git itself WOULD answer here
    assert source_identity.read_kriya_source_identity(str(nested)) == SourceIdentity(None, None, UNAVAILABLE, None)


def test_a_checkout_without_a_commit_has_no_revision_and_is_not_called_a_source_checkout(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "-q")
    identity = source_identity.read_kriya_source_identity(str(empty))
    assert (identity.revision, identity.provenance, identity.reason_code) == (None, UNAVAILABLE, None)


def test_an_effective_attr_tree_is_refused_typed_by_the_reader_and_is_typed_unavailable_on_the_run_side(checkout: Path, monkeypatch) -> None:
    """The content-filter exemption does not cover attr.tree: the release side raises; the run side transports the reason."""
    monkeypatch.setattr(source_identity, "_SOURCE_ROOT", str(checkout))
    assert source_identity.run_source_identity().revision == _git(checkout, "rev-parse", "HEAD")   # control: readable before
    _git(checkout, "config", "attr.tree", "HEAD")
    with pytest.raises(RepositoryContentFilterRefused) as refused:
        source_identity.read_kriya_source_identity(str(checkout))
    assert refused.value.reason_code == GIT_ATTR_TREE_CONFIGURED
    with pytest.raises(RepositoryContentFilterRefused):
        release_identity.kriya_revision(str(checkout))
    assert source_identity.run_source_identity() == SourceIdentity(None, None, UNAVAILABLE, GIT_ATTR_TREE_CONFIGURED)


def test_the_run_side_absorbs_only_the_typed_refusal(monkeypatch) -> None:
    """A programming or environment error is not turned into 'unavailable'."""
    def broken(source_root=None):
        raise RuntimeError("defect in the reader")

    monkeypatch.setattr(source_identity, "read_kriya_source_identity", broken)
    with pytest.raises(RuntimeError, match="defect in the reader"):
        source_identity.run_source_identity()


def test_the_run_side_takes_no_root_and_reads_the_installation_root(checkout: Path, monkeypatch) -> None:
    import inspect

    assert list(inspect.signature(source_identity.run_source_identity).parameters) == []
    seen = []
    real = source_identity.read_kriya_source_identity
    monkeypatch.setattr(source_identity, "read_kriya_source_identity", lambda *a, **k: seen.append((a, k)) or real(*a, **k))
    monkeypatch.setattr(source_identity, "_SOURCE_ROOT", str(checkout))
    assert source_identity.run_source_identity().revision == _git(checkout, "rev-parse", "HEAD")
    assert seen == [((), {})]


# ---- host configuration: a global filter.lfs does not make the trusted identity unavailable --------------------------------------

def _host_with_global_lfs(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    gitconfig = home / ".gitconfig"
    gitconfig.write_text('[filter "lfs"]\n\tclean = git-lfs clean -- %f\n\tsmudge = git-lfs smudge -- %f\n'
                         '\tprocess = git-lfs filter-process\n\trequired = true\n', encoding="utf-8")   # what `git lfs install` writes
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return gitconfig


def test_a_host_global_filter_lfs_does_not_make_the_trusted_identity_unavailable(checkout: Path, tmp_path: Path, monkeypatch) -> None:
    """The MEASURED host shape (global ``filter.lfs.*``) with the filter ACTIVE for a tracked path. Validity control: the ordinary
    untrusted-repository policy refuses this very folder, so a reader under that policy would leave the identity unavailable."""
    (checkout / ".gitattributes").write_text("*.bin filter=lfs\n", encoding="utf-8")
    (checkout / "blob.bin").write_bytes(b"\x00\x01")
    _git(checkout, "add", ".gitattributes", "blob.bin")
    _git(checkout, "commit", "-q", "-m", "lfs-bound path")
    head = _git(checkout, "rev-parse", "HEAD")
    _host_with_global_lfs(tmp_path, monkeypatch)
    with pytest.raises(RepositoryContentFilterRefused) as ordinary:
        refuse_repository_content_filters(str(checkout))
    assert ordinary.value.key.startswith("filter.lfs."), ordinary.value
    assert source_identity.read_kriya_source_identity(str(checkout)).revision == head
    monkeypatch.setattr(source_identity, "_SOURCE_ROOT", str(checkout))
    identity = source_identity.run_source_identity()
    # ``dirty`` is whatever git's own `status` measured here: it depends on whether a git-lfs program exists on this machine
    # (MEASURED on the development host: None, the required driver cannot run), so it is deliberately not pinned in this control.
    assert (identity.revision, identity.provenance, identity.reason_code) == (head, SOURCE_CHECKOUT, None)


def test_the_real_checkout_under_this_hosts_real_git_configuration_is_identified() -> None:
    """No isolation at all: the developer's own global configuration (this host: ``filter.lfs`` / ``filter.media``) is in force."""
    assert (ROOT / ".git").exists(), "this control needs the Git-backed Kriya checkout the suite runs from"
    assert Path(source_identity._SOURCE_ROOT) == ROOT
    identity = source_identity.run_source_identity()
    assert (identity.revision, identity.provenance, identity.reason_code) == (_git(ROOT, "rev-parse", "HEAD"), SOURCE_CHECKOUT, None)
    assert isinstance(identity.dirty, bool)


# ---- the real run path: begin_mutating_run -> RunContext -> _run_manifest -> run.opened -------------------------------------------

def test_a_real_run_records_the_real_checkouts_head_once(workspace: str, real_run_source_identity, no_embedded_build, monkeypatch) -> None:
    """The measured Run-6 shape, nothing replaced: an editable install executed from the worktree."""
    reads = []
    real_read = source_identity.read_kriya_source_identity
    monkeypatch.setattr(source_identity, "read_kriya_source_identity", lambda *a, **k: reads.append((a, k)) or real_read(*a, **k))
    head = _git(ROOT, "rev-parse", "HEAD")
    cfg = strict_config()
    with begin_mutating_run(workspace) as context:
        assert reads == [((), {})]                                           # acquired at run creation, with no root
        with begin_mutating_run(workspace) as nested:                        # a nested boundary is the same run
            assert nested is context
        scope.ensure_store(cfg)
        assert scope.store_pointer()["status"] == scope.STATUS_OPEN
        assert scope._run_manifest(context)["kriya_build"]["commit"] == head
    assert reads == [((), {})], "the source identity is acquired exactly once per top-level run"
    assert context.kriya_source == SourceIdentity(head, context.kriya_source.dirty, SOURCE_CHECKOUT, None)
    assert isinstance(context.kriya_source.dirty, bool)
    assert head != context.base_revision == _git(Path(workspace), "rev-parse", "HEAD")   # Kriya's source, not the workspace's
    run, payload = _opened(context.run_id)
    assert run.verify().status == reader.VERIFIED
    build = payload["kriya_build"]
    assert (build["commit"], build["dirty"], build["build_provenance"], build["tree"]) == (
        head, context.kriya_source.dirty, "source_checkout", build_info.UNKNOWN)
    assert "source_identity_unavailable" not in build
    assert set(payload) == MANIFEST_KEYS and run.manifest()["kriya_build"] == build    # no new top-level key
    report = build_info.version_report()
    assert {key: build[key] for key in ("product", "version", "python", "install_path")} == {
        key: report[key] for key in ("product", "version", "python", "install_path")}
    assert (report["commit"], report["build_provenance"]) == (build_info.UNKNOWN, build_info.UNAVAILABLE)   # KRIYA-VERSION-001: still no git there
    summary = explain.run_summary(os.environ[ENV_STATE_DIR], context.run_id)
    assert summary["kriya_build"]["commit"] == head


def test_each_top_level_run_acquires_its_own_identity(workspace: str, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(run_coordinator, "run_source_identity",
                        lambda: calls.append(1) or SourceIdentity(f"{len(calls):040d}", False, SOURCE_CHECKOUT))
    with begin_mutating_run(workspace) as first:
        with begin_mutating_run(workspace):
            pass
    with begin_mutating_run(workspace) as second:
        pass
    assert len(calls) == 2
    assert (first.kriya_source.revision, second.kriya_source.revision) == ("0" * 39 + "1", "0" * 39 + "2")


def test_the_run_context_transports_the_value_unchanged_and_immutably(workspace: str, monkeypatch) -> None:
    sentinel = SourceIdentity("c" * 40, True, SOURCE_CHECKOUT)
    monkeypatch.setattr(run_coordinator, "run_source_identity", lambda: sentinel)
    cfg = strict_config()
    with begin_mutating_run(workspace) as context:
        assert context.kriya_source is sentinel
        scope.ensure_store(cfg)
        with pytest.raises(dataclasses.FrozenInstanceError):
            context.kriya_source = SourceIdentity("d" * 40, False, SOURCE_CHECKOUT)
        with pytest.raises(dataclasses.FrozenInstanceError):
            sentinel.revision = "d" * 40
    _run, payload = _opened(context.run_id)
    build = payload["kriya_build"]
    assert (build["commit"], build["dirty"], build["build_provenance"], build["tree"]) == ("c" * 40, True, "source_checkout", "UNKNOWN")
    field_names = [f.name for f in dataclasses.fields(RunContext)]
    assert "kriya_source" in field_names
    (source_field,) = [f for f in dataclasses.fields(RunContext) if f.name == "kriya_source"]
    assert source_field.default is dataclasses.MISSING and source_field.default_factory is dataclasses.MISSING   # required: no context without it


def test_a_typed_refusal_reaches_run_opened_as_a_reason_code_never_a_fake_value(workspace: str, monkeypatch, no_embedded_build) -> None:
    monkeypatch.setattr(run_coordinator, "run_source_identity",
                        lambda: SourceIdentity(None, None, UNAVAILABLE, GIT_ATTR_TREE_CONFIGURED))
    cfg = strict_config()
    with begin_mutating_run(workspace) as context:
        scope.ensure_store(cfg)
    _run, payload = _opened(context.run_id)
    assert payload["kriya_build"] == {**build_info.version_report(), "source_identity_unavailable": GIT_ATTR_TREE_CONFIGURED}
    assert (payload["kriya_build"]["commit"], payload["kriya_build"]["build_provenance"]) == (build_info.UNKNOWN, build_info.UNAVAILABLE)
    assert set(payload) == MANIFEST_KEYS


def test_the_conftest_pin_is_active_for_an_ordinary_test_and_leaves_run_opened_exactly_as_before(workspace: str, no_embedded_build) -> None:
    """Validity of the harness pin (and the goldens' unchanged bytes): without the opt-out fixture the run reads no checkout and
    ``kriya_build`` is exactly the build report."""
    assert run_coordinator.run_source_identity is not source_identity.run_source_identity
    cfg = strict_config()
    with begin_mutating_run(workspace) as context:
        scope.ensure_store(cfg)
    assert context.kriya_source == SourceIdentity(None, None, UNAVAILABLE, None)
    _run, payload = _opened(context.run_id)
    assert payload["kriya_build"] == build_info.version_report()


# ---- the evidence recorder serializes; it acquires nothing -------------------------------------------------------------------------

def test_the_evidence_recorder_runs_no_git_and_acquires_no_identity(monkeypatch, no_embedded_build) -> None:
    def forbidden(*_a, **_k):
        raise AssertionError("the evidence recorder acquired a source identity or spawned a process")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(source_identity, "read_kriya_source_identity", forbidden)
    monkeypatch.setattr(source_identity, "run_source_identity", forbidden)
    monkeypatch.setattr(release_identity, "kriya_revision", forbidden)
    context = type("Context", (), {"workspace_id": "w", "workspace_path": "/p", "base_revision": None, "base_tree_hash": None,
                                   "kriya_source": SourceIdentity("e" * 40, False, SOURCE_CHECKOUT)})()
    build = scope._run_manifest(context)["kriya_build"]
    assert (build["commit"], build["dirty"], build["build_provenance"]) == ("e" * 40, False, "source_checkout")
    without = type("Context", (), {"workspace_id": "w", "workspace_path": "/p", "base_revision": None, "base_tree_hash": None})()
    assert scope._run_manifest(without)["kriya_build"] == build_info.version_report()   # no value on the context: nothing is computed


def test_the_overlay_never_claims_a_tree_and_leaves_other_provenances_alone(no_embedded_build) -> None:
    report = build_info.version_report()
    embedded_like = {**report, "commit": "a" * 40, "tree": "b" * 40, "dirty": False, "build_provenance": build_info.EMBEDDED}
    overlaid = scope._with_source_identity(embedded_like, SourceIdentity("f" * 40, True, SOURCE_CHECKOUT))
    assert (overlaid["commit"], overlaid["tree"], overlaid["dirty"]) == ("f" * 40, build_info.UNKNOWN, True)   # a stale embedded tree is not kept
    assert scope._with_source_identity(embedded_like, SourceIdentity("a" * 40, False, EMBEDDED)) == embedded_like
    assert scope._with_source_identity(report, SourceIdentity(None, None, UNAVAILABLE)) == report
    assert scope._with_source_identity(report, None) == report
    assert scope.SOURCE_CHECKOUT == source_identity.SOURCE_CHECKOUT == "source_checkout"


# ---- release identity delegates; it recomputes nothing -------------------------------------------------------------------------------

def test_release_identity_receives_exactly_the_authoritys_value_for_a_temporary_source_root(checkout: Path, monkeypatch) -> None:
    head = _git(checkout, "rev-parse", "HEAD")
    assert release_identity.kriya_revision(str(checkout)) == {"revision": head, "dirty": False}
    assert release_identity.release_identity("none", source_root=str(checkout))["kriya"] == {"revision": head, "dirty": False}
    roots = []
    answers = iter([SourceIdentity("9" * 40, True, SOURCE_CHECKOUT), SourceIdentity("8" * 40, False, EMBEDDED),
                    SourceIdentity(None, None, UNAVAILABLE), SourceIdentity(None, False, UNAVAILABLE)])
    monkeypatch.setattr(release_identity, "read_kriya_source_identity", lambda root=None: roots.append(root) or next(answers))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("release_identity recomputed an identity"))
    assert release_identity.kriya_revision(str(checkout)) == {"revision": "9" * 40, "dirty": True}
    assert release_identity.release_identity("none", source_root="/a/release/candidate")["kriya"] == {"revision": "8" * 40, "dirty": False}
    assert release_identity.kriya_revision() == {"revision": release_identity.UNAVAILABLE, "dirty": None}
    assert release_identity.kriya_revision() == {"revision": "unavailable", "dirty": False}
    assert roots == [str(checkout), "/a/release/candidate", None, None]


# ---- structure: one authority, one exemption, one choke point, no production switch -------------------------------------------------

def _production_trees():
    for root in ("kriya", "plugins"):
        for path in sorted((ROOT / root).rglob("*.py")):
            yield str(path.relative_to(ROOT)), ast.parse(path.read_text(encoding="utf-8"))


def _calls(name: str):
    """(file, enclosing function, positional count, keyword names) of every production call of ``name``."""
    found = []
    for relpath, tree in _production_trees():
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            callee = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
            if callee != name:
                continue
            owner = parents.get(call)
            while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = parents.get(owner)
            found.append((relpath, getattr(owner, "name", "<module>"), len(call.args), tuple(k.arg for k in call.keywords)))
    return sorted(found)


def test_the_run_context_is_built_at_exactly_one_choke_point_which_calls_the_parameterless_reader_once() -> None:
    assert _calls("RunContext") == [("kriya/control/run_coordinator.py", "begin_mutating_run", 0,
                                     ("run_id", "workspace_path", "workspace_id", "base_revision", "base_tree_hash", "kriya_source",
                                      "_lease"))]
    assert _calls("run_source_identity") == [("kriya/control/run_coordinator.py", "begin_mutating_run", 0, ())]


def test_no_production_path_hands_a_workspace_or_candidate_root_to_the_source_identity() -> None:
    """The root-aware reader has exactly two callers: the run side (no argument at all) and release_identity.kriya_revision, which
    forwards its own ``source_root`` parameter (whose callers tests/test_s19p1b_residuals.py pins to the installation)."""
    assert _calls("read_kriya_source_identity") == [("kriya/control/source_identity.py", "run_source_identity", 0, ()),
                                                    ("kriya/core/release_identity.py", "kriya_revision", 1, ())]
    tree = ast.parse((ROOT / "kriya" / "core" / "release_identity.py").read_text(encoding="utf-8"))
    function = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "kriya_revision")
    (call,) = [c for c in ast.walk(function) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "read_kriya_source_identity"]
    assert ast.unparse(call.args[0]) == "source_root" and [a.arg for a in function.args.args] == ["source_root"]


def test_there_is_exactly_one_trusted_reader_and_one_content_filter_exemption() -> None:
    exemptions, source_roots = [], []
    for relpath, tree in _production_trees():
        text = (ROOT / relpath).read_text(encoding="utf-8")
        if "_SOURCE_ROOT" in text:
            source_roots.append(relpath)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and any(k.arg == "content_filters" for k in node.keywords):
                exemptions.append((relpath, ast.unparse(node)))
    assert [relpath for relpath, _call in exemptions] == ["kriya/control/source_identity.py"], exemptions
    assert "content_filters=False" in exemptions[0][1]
    assert source_roots == ["kriya/control/source_identity.py"]


def test_release_identity_and_the_repository_authority_hold_no_git_identity_implementation() -> None:
    text = (ROOT / "kriya" / "core" / "release_identity.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {a.name for n in ast.walk(tree)
                                                                           if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    assert not names & {"git_argv", "git_environment", "refuse_repository_content_filters", "child_environment", "_SOURCE_ROOT"}, names
    strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and "\n" not in n.value}
    assert not strings & {"git", "rev-parse", "HEAD", "--porcelain", "--untracked-files=no"}, strings
    authority = (ROOT / "kriya" / "repository" / "git.py").read_text(encoding="utf-8")
    assert "content_filters=False" not in authority and "source_identity" not in authority and "_SOURCE_ROOT" not in authority


def test_the_evidence_package_imports_no_identity_reader_and_no_process_spawner() -> None:
    for path in sorted((ROOT / "kriya" / "core" / "attempt_evidence").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {
            n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not {m for m in imported if "source_identity" in m or "release_identity" in m}, (path.name, imported)
    scope_tree = ast.parse((ROOT / "kriya" / "core" / "attempt_evidence" / "scope.py").read_text(encoding="utf-8"))
    scope_imports = {a.name for n in ast.walk(scope_tree) if isinstance(n, ast.Import) for a in n.names} | {
        n.module or "" for n in ast.walk(scope_tree) if isinstance(n, ast.ImportFrom)}
    assert "subprocess" not in scope_imports and not any(m.startswith("kriya.repository") or m == "kriya.platform" for m in scope_imports)


def test_production_has_no_switch_that_replaces_the_real_reader() -> None:
    """The conftest pin is a test-harness monkeypatch only: the authority reads no environment variable or configuration, nothing
    in production assigns or patches the reader, and no production file knows the fixture."""
    tree = ast.parse((ROOT / "kriya" / "control" / "source_identity.py").read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {
        f"{n.module}.{a.name}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert imported == {"__future__.annotations", "os", "shutil", "subprocess", "dataclasses.dataclass", "typing.List", "typing.Mapping",
                        "typing.Optional", "kriya.config.environment.child_environment", "kriya.platform.git_argv",
                        "kriya.platform.git_environment", "kriya.repository.git.RepositoryContentFilterRefused",
                        "kriya.repository.git.refuse_repository_content_filters", "kriya.foundation.build_info.UNKNOWN",
                        "kriya.foundation.build_info.embedded_build_info"}, imported
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not attributes & {"environ", "getenv", "config", "argv"}, attributes
    assert not [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value.startswith("KRIYA_")]
    for relpath, production in _production_trees():
        text = (ROOT / relpath).read_text(encoding="utf-8")
        assert "_pinned_run_source_identity" not in text and "conftest" not in text or relpath != "kriya/control/source_identity.py"
        for node in ast.walk(production):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else []
            assigned = {getattr(t, "id", getattr(t, "attr", "")) for t in targets}
            assert not assigned & {"run_source_identity", "read_kriya_source_identity"}, (relpath, assigned)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "setattr" and len(node.args) > 1:
                assert "source_identity" not in ast.unparse(node.args[1]), (relpath, ast.unparse(node))
    run_path = (ROOT / "kriya" / "control" / "run_coordinator.py").read_text(encoding="utf-8")
    assert "from kriya.control.source_identity import SourceIdentity, run_source_identity\n" in run_path
    assert "_pinned_run_source_identity" not in run_path and "PYTEST" not in run_path
