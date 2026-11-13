"""CURRENT PROJECT VIEW (owner ruling 2026-11-07, Phase B): the derived projection every decision engine reads.

MEASURED (owner walkthrough, run 20261107T182659-f963bb85): Run 1 created ``main.py`` (committed by Kriya's own apply, still Git-untracked); Run 2 ("Change hello world message to Hello Kriya
in the app") planned ``MODIFY app.py``, a file that did not exist, and created it - no model request carried ``main.py``. The view exists so a later run SEES what an earlier run built, without
``git add`` and without promoting anything: the path owners (bound tree, commit journal, filesystem, read policy) decide; this module only projects them.
"""
import os
import subprocess
from pathlib import Path

import pytest

from kriya.repository import project_view as pv
from kriya.repository.commit import CommitEvidence, CommitState, _persist_commit_evidence
from kriya.repository.project_view import (
    AMBIGUOUS_UNTRACKED,
    KRIYA_ACCEPTED,
    TRACKED,
    build_current_project_view,
    render_before_context,
    render_project_view,
)

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


@pytest.fixture
def clean_git_home(scratch_dir, monkeypatch):
    home = scratch_dir("clean-home")      # released when the test passed (tests/conftest.py)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key, value in GIT_ENV.items():
        monkeypatch.setenv(key, value)
    return home


def _git(root, *args):
    subprocess.run(["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def project(tmp_path, clean_git_home):
    """A Greenfield-shaped project: Git initialised with ONE EMPTY commit (what the bootstrap commits), nothing tracked."""
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "commit", "-q", "--allow-empty", "-m", "Kriya: initial commit to enable isolation")
    return root


def _put(root, name, text="x = 1\n"):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


_SEQ = iter(range(1, 10_000))


def _journal(root, *operations, state=CommitState.COMMITTED, at=None):
    """A real commit-journal document (the same writer the apply path uses), ownership-stamped to this workspace."""
    number = next(_SEQ)
    stamp = float(number if at is None else at)
    _persist_commit_evidence(str(root), CommitEvidence(
        schema_version=3, transaction_id=f"tx{number:04d}", state=state, started_at_unix=stamp, updated_at_unix=stamp,
        operations=tuple({"kind": kind, "target_path": path, "base_path": path} for kind, path in operations),
        result_revisions={path: "0" * 64 for kind, path in operations if kind != "DELETE"}))


def _tree(root):
    """Every non-``.git`` file with its bytes (``.git`` is pruned: this test never inspects repository internals)."""
    found = []
    for directory, names, files in os.walk(root):
        names[:] = [name for name in names if name != ".git"]
        found.extend((os.path.relpath(os.path.join(directory, name), root), Path(directory, name).read_bytes()) for name in files)
    return sorted(found)


def _view(root):
    return build_current_project_view(str(root))


# ---- the three classes -----------------------------------------------------------------------------------------------------------------------------------
def test_a_file_kriya_created_is_visible_as_accepted_while_git_does_not_track_it(project):
    _put(project, "main.py", 'print("Hello World")\n')
    _journal(project, ("CREATE", "main.py"))
    entry = _view(project).get("main.py")
    assert entry.klass == KRIYA_ACCEPTED and entry.kriya_accepted is True
    assert entry.content == 'print("Hello World")\n' and entry.digest and entry.withheld is None


def test_a_tracked_file_is_tracked_whether_or_not_kriya_wrote_it(project):
    _put(project, "calc.py", "a = 1\n")
    _put(project, "main.py", "b = 2\n")
    _journal(project, ("CREATE", "main.py"))
    _git(project, "add", "calc.py", "main.py")
    _git(project, "commit", "-q", "-m", "x")
    view = _view(project)
    assert view.get("calc.py").klass == TRACKED and view.get("calc.py").kriya_accepted is False
    assert view.get("main.py").klass == TRACKED and view.get("main.py").kriya_accepted is True      # provenance is kept, the class is Git's declaration
    assert view.get("calc.py").content == "a = 1\n"


def test_control_A_a_pre_existing_untracked_file_is_ambiguous_and_its_content_is_never_injected(project):
    _put(project, "main.py", 'print("Hello World")\n')
    _journal(project, ("CREATE", "main.py"))
    _put(project, "rogue.py", "SECRET_TOKEN_IN_BODY = 'xyz'\n")
    view = _view(project)
    rogue = view.get("rogue.py")
    assert rogue.klass == AMBIGUOUS_UNTRACKED and rogue.kriya_accepted is False
    assert rogue.content is None and rogue.digest is None and rogue.withheld == pv.WITHHELD_AMBIGUOUS
    rendered = render_project_view(view)
    assert "rogue.py" in rendered and "origin unknown" in rendered and "SECRET_TOKEN_IN_BODY" not in rendered
    assert "SECRET_TOKEN_IN_BODY" not in render_before_context(view, ["main.py", "rogue.py"])


def test_a_rogue_file_never_gains_accepted_status_from_another_files_journal_entry(project):
    _put(project, "main.py")
    _put(project, "rogue.py")
    _journal(project, ("CREATE", "main.py"))
    assert pv.accepted_paths(str(project)) == frozenset({"main.py"})
    assert _view(project).get("rogue.py").klass == AMBIGUOUS_UNTRACKED


# ---- the journal applies to the PATH, the filesystem owns the bytes -----------------------------------------------------------------------------------------
def test_control_B_a_user_edit_after_the_accepted_run_is_what_the_view_carries(project):
    path = _put(project, "main.py", 'print("Hello World")\n')
    _journal(project, ("CREATE", "main.py"))
    path.write_text('print("edited by the user")\n', encoding="utf-8")
    entry = _view(project).get("main.py")
    assert entry.klass == KRIYA_ACCEPTED and entry.content == 'print("edited by the user")\n'
    assert "Hello World" not in render_project_view(_view(project))


def test_control_C_an_accepted_file_the_user_deleted_is_absent_and_nothing_is_remembered_of_it(project):
    path = _put(project, "main.py", 'print("Hello World")\n')
    _journal(project, ("CREATE", "main.py"))
    path.unlink()
    view = _view(project)
    assert view.get("main.py") is None and "main.py" not in view.paths
    assert "Hello World" not in render_project_view(view) and "main.py" not in render_project_view(view)


def test_a_later_committed_delete_ends_the_provenance(project):
    _put(project, "main.py")
    _journal(project, ("CREATE", "main.py"))
    _journal(project, ("DELETE", "main.py"))
    assert pv.accepted_paths(str(project)) == frozenset()
    assert _view(project).get("main.py").klass == AMBIGUOUS_UNTRACKED          # same name, no longer provably Kriya's


def test_only_committed_transactions_of_this_workspace_confer_provenance(project, tmp_path):
    _put(project, "a.py")
    _put(project, "b.py")
    _put(project, "c.py")
    _journal(project, ("CREATE", "a.py"), state=CommitState.ROLLED_BACK)
    _journal(project, ("CREATE", "b.py"), state=CommitState.UNCERTAIN)
    _journal(project, ("CREATE", "c.py"), state=CommitState.IN_PROGRESS)
    assert pv.accepted_paths(str(project)) == frozenset()


def test_a_journal_document_written_for_another_workspace_confers_nothing(project, tmp_path, clean_git_home):
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    _git(other, "commit", "-q", "--allow-empty", "-m", "x")
    _journal(other, ("CREATE", "main.py"))
    _put(project, "main.py")
    commits = Path(other) / ".kriya" / "control" / "commits"
    target = Path(project) / ".kriya" / "control" / "commits"
    target.mkdir(parents=True)
    for document in commits.iterdir():
        (target / document.name).write_bytes(document.read_bytes())
    assert pv.accepted_paths(str(project)) == frozenset()                          # the ownership stamp names the other workspace: never read
    assert _view(project).get("main.py").klass == AMBIGUOUS_UNTRACKED


# ---- path / security authority wins over the journal ---------------------------------------------------------------------------------------------------------
def test_control_D_an_accepted_path_replaced_by_a_symlink_to_a_secret_outside_is_not_listed_or_read(project, tmp_path):
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("TOP SECRET\n", encoding="utf-8")
    link = Path(project) / "main.py"
    link.symlink_to(outside)
    _journal(project, ("CREATE", "main.py"))
    view = _view(project)
    assert view.get("main.py") is None
    assert "TOP SECRET" not in render_project_view(view) and "main.py" not in render_project_view(view)


def test_control_D2_an_accepted_path_replaced_by_an_inside_symlink_is_listed_without_content(project):
    _put(project, "real.py", "REAL = 1\n")
    (Path(project) / "main.py").symlink_to("real.py")
    _journal(project, ("CREATE", "main.py"))
    entry = _view(project).get("main.py")
    assert entry.kind == "symlink" and entry.content is None and entry.withheld == pv.WITHHELD_NOT_REGULAR and entry.digest is None


def test_a_secret_bearing_name_is_not_even_listed(project):
    _put(project, ".env", "API_KEY=abc\n")
    _put(project, "credentials.json", "{}")
    _put(project, "main.py")
    _journal(project, ("CREATE", ".env"), ("CREATE", "main.py"))
    view = _view(project)
    assert ".env" not in view.paths
    assert "API_KEY" not in render_project_view(view) and ".env" not in render_project_view(view)
    assert "main.py" in view.paths


def test_git_ignored_files_and_kriya_state_are_not_members(project):
    _put(project, ".gitignore", "build/\n")
    _put(project, "build/out.py", "OUT = 1\n")
    _put(project, "main.py")
    view = _view(project)
    assert "build/out.py" not in view.paths and ".kriya" not in " ".join(view.paths)


# ---- bounded, deterministic ----------------------------------------------------------------------------------------------------------------------------------
def test_a_binary_or_oversized_file_is_listed_without_content_and_does_not_disable_the_others(project):
    _put(project, "ok.py", "OK = 1\n")
    (Path(project) / "blob.bin").write_bytes(b"\x00\x01\x02")
    _put(project, "big.py", "x = 1\n" * 5_000)
    _journal(project, ("CREATE", "ok.py"), ("CREATE", "blob.bin"), ("CREATE", "big.py"))
    view = _view(project)
    assert view.get("ok.py").content == "OK = 1\n"
    assert view.get("blob.bin").content is None and view.get("blob.bin").withheld == pv.WITHHELD_NOT_TEXT
    assert view.get("big.py").content is None and view.get("big.py").withheld == pv.WITHHELD_TOO_LARGE
    assert view.contents_included is True


def test_a_large_project_keeps_the_inventory_but_shows_no_inline_content(project):
    for index in range(pv.MAX_CONTENT_FILES + 1):
        _put(project, f"m{index:02d}.py", f"V = {index}\n")
        _journal(project, ("CREATE", f"m{index:02d}.py"))
    view = _view(project)
    assert view.contents_included is False and len(view.files) == pv.MAX_CONTENT_FILES + 1
    assert all(entry.content is None and entry.withheld == pv.WITHHELD_PROJECT_TOO_LARGE for entry in view.files)
    assert "m00.py" in render_project_view(view) and "V = 0" not in render_project_view(view)


def test_the_inventory_is_bounded(project):
    for index in range(pv.MAX_LISTED_PATHS + 7):
        _put(project, f"d/f{index:03d}.txt", "x")
    view = _view(project)
    assert len(view.files) == pv.MAX_LISTED_PATHS and view.omitted_paths == 7
    assert "7 more not listed" in render_project_view(view)


def test_the_view_is_deterministic_and_changes_nothing_on_disk(project):
    _put(project, "main.py", "a = 1\n")
    _put(project, "rogue.py", "b = 2\n")
    _journal(project, ("CREATE", "main.py"))
    before = _tree(project)
    first, second = _view(project), _view(project)
    after = _tree(project)
    assert first == second and render_project_view(first) == render_project_view(second)
    assert before == after
    status = subprocess.run(["git", "status", "--short"], cwd=project, capture_output=True, text=True).stdout.splitlines()
    assert "?? main.py" in status and "?? rogue.py" in status and not [line for line in status if not line.startswith("??")]     # nothing added, committed or promoted


def test_an_empty_project_renders_nothing(project):
    assert render_project_view(_view(project)) == ""


# ---- the BEFORE rendering for review ---------------------------------------------------------------------------------------------------------------------
def test_before_context_shows_existing_content_names_new_files_and_lists_the_rest(project):
    _put(project, "main.py", 'print("Hello World")\n')
    _journal(project, ("CREATE", "main.py"))
    text = render_before_context(_view(project), ["app.py"])
    assert "app.py: NEW file" in text and "main.py (not changed by this candidate), current content" in text and 'print("Hello World")' in text
    both = render_before_context(_view(project), ["main.py"])
    assert "main.py: EXISTING file, its content before this change" in both and 'print("Hello World")' in both


def test_before_context_is_bounded_and_falls_back_to_names(project):
    for index in range(4):
        _put(project, f"m{index}.py", "x = 1\n" * 400)
        _journal(project, ("CREATE", f"m{index}.py"))
    text = render_before_context(_view(project), ["m0.py"], max_chars=3_000)
    assert "m0.py: EXISTING file, its content before this change" in text
    assert "(content not shown): " in text or "content not shown" in text
    assert text.count("x = 1") <= 3_000 // 6


def test_kriya_knowledge_directories_are_not_project_content(project):
    _put(project, "skills/conflict.json", '{"rule_a": "ONLY-IN-A-SKILL"}')
    _put(project, "pkg/SKILL.md", "---\nname: x\n---\nSKILL-PACKAGE-TEXT")
    _put(project, "pkg/helper.py", "H = 1\n")
    _put(project, "main.py", "M = 1\n")
    skills = os.path.join(project, "skills")
    view = build_current_project_view(str(project), exclude_directory=lambda directory: os.path.realpath(directory) == os.path.realpath(skills) or os.path.isfile(os.path.join(directory, "SKILL.md")))
    assert view.paths == frozenset({"main.py"})
    assert "ONLY-IN-A-SKILL" not in render_project_view(view) and "SKILL-PACKAGE-TEXT" not in render_project_view(view)
    assert {"skills/conflict.json", "pkg/SKILL.md", "pkg/helper.py", "main.py"} <= build_current_project_view(str(project)).paths   # without the predicate they are ordinary files


def test_the_rendering_degrades_with_the_requests_room_and_never_exceeds_it(project):
    for index in range(6):
        _put(project, f"m{index}.py", f"VALUE_{index} = {index}\n" * 40)
        _journal(project, ("CREATE", f"m{index}.py"))
    view = _view(project)
    full = render_project_view(view)
    assert "VALUE_0 = 0" in full
    names_only = render_project_view(view, max_chars=len(full) - 1)
    assert "VALUE_0" not in names_only and "m0.py" in names_only and "context window is small" in names_only
    few = render_project_view(view, max_chars=len(names_only) - 1)
    assert 0 < len(few) <= len(names_only) - 1 and "more not listed" in few
    assert render_project_view(view, max_chars=10) == ""
    for limit in (len(full), len(names_only), len(few), 400, 250):
        assert len(render_project_view(view, max_chars=limit)) <= limit
