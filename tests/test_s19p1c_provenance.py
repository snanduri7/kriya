"""S19 P1c (OI-141, second fix-forward of S19 Q1): an operator (global/system) or command-scope content-filter
definition is trusted only when the code it EXECUTES is provably the operator's (owner decisions 1-3, 2026-10-20).

MEASURED at base 135acfe (scratch p1c/out_base.jsonl): a global `clean = ./tools/clean.sh`, `tools/clean.sh`,
`sh tools/clean.sh`, `bash ./tools/clean.sh`, `python3 ./tools/filter.py`, an absolute script path inside the work
tree, an operator-folder symlink into it and a repository file called `./git-lfs` were all admitted and git ran
repository code; so did this host's own `git media clean %f` line with a repository-local `alias.media`
(p1c/out_hostcfg.txt). The recognizer (kriya/repository/git.py, PROVENANCE_CAUSES) admits only the conservative
direct form; everything else refuses with GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN and a closed cause.

Also pinned here: the Open Folder inspection uses the probe's 15 s timeout (owner decision 6), a timeout or spawn
failure of that inspection never lets the status probe run (relayed finding 8(e)), and the caller's GIT_DIR no longer
turns a legitimate operator file into an in-repository one (relayed finding 8(c)).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List

import pytest

from tests.test_s19q1_content_filter_refusal import _bind, _dirty, _marker_script, _repository, _setup

UNPROVEN = "GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN"


@pytest.fixture(autouse=True)
def _isolated_git(scratch_dir, monkeypatch):
    home = scratch_dir("s19p1c-git-home")
    config = home / "gitconfig"
    config.write_text("[user]\n\tname = s19p1c\n\temail = s19p1c@local\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(home))                 # the Open Folder probe reads HOME/.gitconfig (none here)
    for name in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(name, "2026-10-20T00:00:00+00:00")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                 "GIT_EXEC_PATH", "GIT_CEILING_DIRECTORIES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    return config


@pytest.fixture
def owner(tmp_path) -> Path:
    return _repository(tmp_path)


class Plant:
    """A global filter command plus the repository and operator programs it may name; every one leaves a tag in the
    same marker file when it runs."""

    def __init__(self, repo: Path, global_config: Path, monkeypatch) -> None:
        self.repo, self.config, self.monkeypatch = repo, global_config, monkeypatch
        self.marker = repo.parent / "PROGRAM_RAN"
        self.operator = repo.parent / "operator-bin"
        self.operator.mkdir()
        (repo / "tools").mkdir()
        _marker_script(repo / "tools" / "clean.sh", self.marker)
        (repo / "tools" / "filter.py").write_text(f"import sys\nopen({str(self.marker)!r}, 'a').write('py')\n"
                                                  "sys.stdout.write(sys.stdin.read())\n")
        _marker_script(repo / "git-lfs", self.marker)
        _marker_script(repo / "tools" / "git-lfs-clean.sh", self.marker)
        (repo / "bin").mkdir()
        _marker_script(repo / "bin" / "git-lfs", self.marker)
        _marker_script(self.operator / "git-lfs", self.marker)
        _marker_script(self.operator / "op-filter", self.marker)
        monkeypatch.setenv("PATH", f"{self.operator}:/usr/bin:/bin")

    def command(self, template: str) -> str:
        return template.format(ws=self.repo, op=self.operator, parent=self.repo.parent)

    def define(self, template: str) -> None:
        # git-config quoting: an unquoted `;` or `#` would start a comment and cut the value short.
        value = '"' + self.command(template).replace("\\", "\\\\").replace('"', '\\"') + '"'
        with open(self.config, "a", encoding="utf-8") as handle:
            handle.write(f'[filter "kx"]\n\tclean = {value}\n\tsmudge = {value}\n')
        _bind(self.repo)
        _dirty(self.repo)


def _status(repo: Path):
    from kriya.workflow import worktree
    return worktree.git_status_entries(str(repo))


def _refused(repo: Path, marker: Path, cause_text: str) -> str:
    from kriya.repository.git import RepositoryContentFilterRefused

    with pytest.raises(RepositoryContentFilterRefused) as raised:
        _status(repo)
    assert not marker.exists(), "a refused command ran (during the inspection or after it)"
    assert raised.value.reason_code == UNPROVEN, str(raised.value)
    message = str(raised.value)
    assert "filter.kx." in message and "global scope" in message and cause_text in message
    return message


# --------------------------------------------------------------------------------- REFUSE: repository code

_REFUSED = [
    ("./tools/clean.sh", "relative to the repository"),
    ("tools/clean.sh", "relative to the repository"),
    ("sh tools/clean.sh", "an argument names a path"),
    ("bash ./tools/clean.sh", "an argument names a path"),
    ("python3 ./tools/filter.py", "an argument names a path"),
    ("node tools/filter.js", "an argument names a path"),
    ("sh", "shell, interpreter or evaluator (sh)"),
    ("python3.12 -", "shell, interpreter or evaluator (python)"),
    ("git media clean %f", "shell, interpreter or evaluator (git)"),
    ("{ws}/tools/clean.sh", "resolves inside the repository"),
    ("{ws}/tools/../tools/clean.sh", "resolves inside the repository"),
    ("{op}/op-filter {ws}/tools/clean.sh", "an argument names a path"),
    ("{op}/op-filter --config=./x", "an argument names a path"),
    ("{op}/op-filter .", "an argument names a path"),
    ("{op}/op-filter | sh", "shell construct"),
    ("{op}/op-filter; ./tools/clean.sh", "shell construct"),
    ("{op}/op-filter $(./tools/clean.sh)", "shell construct"),
    ("{op}/op-filter `tools/clean.sh`", "shell construct"),
    ("{op}/op-filter > out", "shell construct"),
    ("{op}/op-filter *", "shell construct"),
    ("{op}/op-filter $HOME", "shell construct"),
    ("{op}/op-filter '%f'", "shell construct"),
    ("~/x", "shell construct"),
    ("PATH=./tools op-filter", "shell construct"),
    ("not-installed-anywhere -- %f", "does not resolve"),
    # negative controls: a repository FILE NAMED like an operator program is repository code, never recognised by name
    ("./git-lfs clean -- %f", "relative to the repository"),
    ("tools/git-lfs-clean.sh", "relative to the repository"),
    ("{ws}/bin/git-lfs clean -- %f", "resolves inside the repository"),
]


@pytest.mark.parametrize("template, cause", _REFUSED, ids=[t for t, _ in _REFUSED])
def test_an_operator_command_that_does_not_provably_run_operator_code_is_refused(owner, _isolated_git, monkeypatch,
                                                                                 template, cause):
    plant = Plant(owner, _isolated_git, monkeypatch)
    plant.define(template)
    message = _refused(owner, plant.marker, cause)
    if " " in template or "/" in template:
        assert plant.command(template) not in message                     # never the command text


def test_a_symlink_into_the_repository_is_repository_code(owner, _isolated_git, monkeypatch):
    plant = Plant(owner, _isolated_git, monkeypatch)
    (plant.operator / "link-clean").symlink_to(owner / "tools" / "clean.sh")
    plant.define("{op}/link-clean")
    _refused(owner, plant.marker, "resolves inside the repository")


def test_a_repository_symlinked_directory_on_the_program_path_is_repository_code(owner, _isolated_git, monkeypatch):
    """The program's own real file is outside (/bin/cat), but the path goes through a directory the repository owns
    and can retarget at any time."""
    plant = Plant(owner, _isolated_git, monkeypatch)
    (owner / "binlink").symlink_to("/bin")
    plant.define("{ws}/binlink/cat")
    _refused(owner, plant.marker, "resolves inside the repository")


def test_an_operator_alias_of_a_shell_is_still_a_shell(owner, _isolated_git, monkeypatch):
    """The evaluator set is matched on the RESOLVED program too: an innocently named operator symlink to /bin/sh."""
    plant = Plant(owner, _isolated_git, monkeypatch)
    (plant.operator / "innocent").symlink_to("/bin/sh")
    plant.define("innocent -c x")
    _refused(owner, plant.marker, "shell, interpreter or evaluator (sh)")


@pytest.mark.parametrize("entry", ["", ".", "tools", "{ws}/bin"])
def test_a_relative_empty_or_in_repository_search_path_entry_before_the_program_refuses(owner, _isolated_git,
                                                                                        monkeypatch, entry):
    """MEASURED: an empty or relative PATH entry is the work tree top for git's filter (the repository's program ran);
    an in-repository entry can gain the program at any time."""
    plant = Plant(owner, _isolated_git, monkeypatch)
    plant.define("git-lfs clean -- %f")
    monkeypatch.setenv("PATH", f"{plant.command(entry)}:{plant.operator}:/usr/bin:/bin")
    _refused(owner, plant.marker, "search path")


def test_git_s_exec_path_is_searched_first(owner, _isolated_git, monkeypatch):
    """MEASURED (git 2.54.0): git looks a bare filter program up in its exec path before PATH."""
    plant = Plant(owner, _isolated_git, monkeypatch)
    plant.define("git-lfs clean -- %f")
    monkeypatch.setenv("GIT_EXEC_PATH", str(owner / "bin"))
    _refused(owner, plant.marker, "search path")


def test_a_definition_naming_main_work_tree_code_is_refused_from_a_linked_worktree(owner, _isolated_git, monkeypatch,
                                                                                    tmp_path):
    """Worktree variant: inspected from a linked worktree OUTSIDE the main work tree, an absolute program in the main
    work tree is still this repository's code (git's own common-dir answer, as for configuration files)."""
    plant = Plant(owner, _isolated_git, monkeypatch)
    _setup(owner, "add", "-A")
    _setup(owner, "commit", "-q", "-m", "programs")
    linked = tmp_path / "linked"
    _setup(owner, "worktree", "add", "-q", "--detach", str(linked))
    plant.define("{ws}/tools/clean.sh")
    _dirty(linked)
    _refused(linked, plant.marker, "resolves inside the repository")


def test_a_submodule_s_git_is_refused_for_a_relative_operator_program(tmp_path, _isolated_git, monkeypatch):
    """Submodule variant: the parent's status recurses into the child with the same operator definition; the
    relative program would be the CHILD's file (git runs it at the child's top)."""
    from tests.test_s19q1_content_filter_refusal import _embedded

    parent = _repository(tmp_path)
    child = _embedded(parent, "vendored")                 # binds its *.py to filter kx by its own .gitattributes
    _setup(parent, "add", "vendored")
    _setup(parent, "commit", "-q", "-m", "gitlink")
    marker = tmp_path / "PROGRAM_RAN"
    (child / "tools").mkdir()
    _marker_script(child / "tools" / "clean.sh", marker)
    with open(_isolated_git, "a", encoding="utf-8") as handle:
        handle.write('[filter "kx"]\n\tclean = tools/clean.sh\n')
    (child / "n.py").write_text("y = 2\n")
    _refused(parent, marker, "relative to the repository")


def test_an_operator_include_of_a_repository_file_still_refuses_by_origin(owner, _isolated_git, monkeypatch):
    """The P1 origin check comes first: an operator file including a repository file that names repository code."""
    from kriya.repository.git import RepositoryContentFilterRefused

    plant = Plant(owner, _isolated_git, monkeypatch)
    (owner / "filters.gitcfg").write_text('[filter "kx"]\n\tclean = ./tools/clean.sh\n')
    with open(_isolated_git, "a", encoding="utf-8") as handle:
        handle.write(f"[include]\n\tpath = {owner / 'filters.gitcfg'}\n")
    _bind(owner)
    _dirty(owner)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        _status(owner)
    assert raised.value.reason_code == "GIT_CONTENT_FILTER_ORIGIN_IN_REPOSITORY"
    assert not plant.marker.exists()


def test_a_command_scope_definition_is_judged_the_same_way(owner, monkeypatch, _isolated_git):
    plant = Plant(owner, _isolated_git, monkeypatch)
    _bind(owner)
    _dirty(owner)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "filter.kx.clean")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "./tools/clean.sh")
    from kriya.repository.git import RepositoryContentFilterRefused
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        _status(owner)
    assert raised.value.reason_code == UNPROVEN and "command scope" in str(raised.value)
    assert not plant.marker.exists()


# --------------------------------------------------------------------------------- ALLOW: operator code

@pytest.mark.parametrize("template", ["git-lfs clean -- %f", "{op}/op-filter", "{op}/op-filter --mode=x -- %f",
                                      "cat"])
def test_an_operator_program_outside_the_repository_still_runs(owner, _isolated_git, monkeypatch, template):
    """Positive controls: an operator-installed git-lfs found on PATH outside the repository, an absolute operator
    program (with options and git's %f), and a system program by bare name - admitted, and they really run."""
    plant = Plant(owner, _isolated_git, monkeypatch)
    plant.define(template)
    assert [entry for entry in _status(owner) if entry[0] != "??"] == [(" M", "a.py", None), (" M", "skill.md", None)]
    assert plant.marker.exists() or template == "cat", "the operator's program did not run"


# --------------------------------------------------------------------------------- 8(c): the caller's GIT_DIR

def test_the_caller_s_git_dir_does_not_make_an_operator_file_repository_content(owner, _isolated_git, monkeypatch):
    """MEASURED at base 135acfe: with GIT_DIR=<repository>/.git in the caller's environment, `rev-parse
    --git-common-dir` in the operator file's folder answered the repository's common dir and a legitimate operator
    include was refused as GIT_CONTENT_FILTER_ORIGIN_IN_REPOSITORY (a misclassified cause)."""
    from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters

    operator = owner.parent / "operator"
    operator.mkdir()
    program = _marker_script(operator / "op-filter", owner.parent / "UNUSED")
    (operator / "filters.cfg").write_text(f'[filter "kx"]\n\tclean = {program}\n')
    with open(_isolated_git, "a", encoding="utf-8") as handle:
        handle.write(f"[include]\n\tpath = {operator / 'filters.cfg'}\n")
    monkeypatch.setenv("GIT_DIR", str(owner / ".git"))
    refuse_repository_content_filters(str(owner))
    refuse_repository_content_filters(str(owner), git="/usr/bin/git" if os.path.exists("/usr/bin/git") else None,
                                      env=dict(os.environ))
    # the in-repository include is still refused under the same GIT_DIR
    (owner / "filters.gitcfg").write_text(f'[filter "kx"]\n\tsmudge = {program}\n')
    with open(_isolated_git, "a", encoding="utf-8") as handle:
        handle.write(f"[include]\n\tpath = {owner / 'filters.gitcfg'}\n")
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        refuse_repository_content_filters(str(owner))
    assert raised.value.reason_code == "GIT_CONTENT_FILTER_ORIGIN_IN_REPOSITORY"


# --------------------------------------------------------------------------------- decision 6 / 8(e): Open Folder

def _describe(folder: Path):
    from kriya.tools import git_metadata
    return git_metadata.describe_checkout(str(folder))


def test_the_open_folder_inspection_uses_the_probe_s_own_15_s_timeout(owner, monkeypatch):
    """Owner decision 6: the established Open Folder probe timeout (15 s, git_metadata before P1b) - never the
    authority's 60 s. Every inspection spawn of the probe carries it."""
    from kriya.repository import git as authority
    from kriya.tools import git_metadata

    assert git_metadata._PROBE_TIMEOUT_SECONDS == 15
    timeouts: List[object] = []
    real = subprocess.run

    def recording(argv, *args, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return real(argv, *args, **kwargs)
    monkeypatch.setattr(authority.subprocess, "run", recording)
    _dirty(owner)
    assert _describe(owner).dirty is True
    monkeypatch.setattr(authority.subprocess, "run", real)
    assert timeouts and set(timeouts) == {15}


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired(["git"], 15), PermissionError(13, "denied")])
def test_an_open_folder_inspection_that_cannot_finish_never_runs_the_status_probe(owner, monkeypatch, failure):
    """Relayed finding 8(e) (the OLD blanking path failed open): a timeout or spawn failure of the inspection reports
    the folder unavailable (typed) and the filter-capable status probe is never started (MEASURED: not reproducible
    on 135acfe either; pinned)."""
    from kriya.repository import git as authority
    from kriya.tools import git_metadata

    real = subprocess.run
    statuses = []

    def failing(argv, *args, **kwargs):
        if "config" in argv:
            raise failure
        return real(argv, *args, **kwargs)

    def probe(git, cwd, *args):
        if args and args[0] == "status":
            statuses.append(args)
        return real_probe(git, cwd, *args)
    real_probe = git_metadata._probe
    monkeypatch.setattr(authority.subprocess, "run", failing)
    monkeypatch.setattr(git_metadata, "_probe", probe)
    facts = _describe(owner)
    assert statuses == []
    assert facts.dirty is None and facts.reason.startswith("GIT_CONFIG_UNINSPECTABLE: ")
