"""OI-129 / N4 (HANDOVER-CLAUDE-CONTEXT-UNTRACKED): every file the tracked CLAUDE.md sends a session to is itself tracked.

CLAUDE.md keeps the always-on rules and points at ``handover/claude-context/<file>.md`` for the per-subsystem contract text
("read it before modifying a module"), and ``@handover/ENGINEERING_RULES.md`` is imported at session start. When those files lived
only in an untracked directory of one checkout, the contract corrections (S1/S16a/S18) existed nowhere a clone, a worktree or a CI
checkout could read. The control: each path named in CLAUDE.md exists AND is in the git index, and no file of the context
directory is orphaned from CLAUDE.md's table."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"
CONTEXT_DIR = "handover/claude-context"
_REFERENCED = re.compile(r"(?<![\w/.-])((?:handover/claude-context|handover)/[A-Za-z0-9_.-]+\.md)")


def referenced_authority(text: str) -> set:
    """The repository-relative ``handover/...md`` paths CLAUDE.md names (a table cell, an @import or a ``cat`` hint)."""
    return set(_REFERENCED.findall(text))


def untracked(root: Path, paths) -> list:
    """The paths that are not in ``root``'s git index (a missing file is untracked too)."""
    listed = subprocess.run(["git", "ls-files", "-z", "--", *sorted(paths)], cwd=root, check=True, capture_output=True, text=True).stdout
    tracked = set(filter(None, listed.split("\0")))
    return sorted(set(paths) - tracked)


def _in_git_checkout() -> bool:
    return subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, capture_output=True).returncode == 0


def test_the_helper_distinguishes_a_tracked_file_from_an_untracked_one(tmp_path):
    """The control is not vacuous: in a real repository an untracked file and a missing file are reported, a tracked one is not."""
    def git(*args):
        subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=T", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "handover").mkdir()
    (tmp_path / "handover" / "tracked.md").write_text("x")
    (tmp_path / "handover" / "loose.md").write_text("x")
    git("add", "handover/tracked.md")
    git("commit", "-q", "-m", "c")
    assert untracked(tmp_path, {"handover/tracked.md", "handover/loose.md", "handover/missing.md"}) == [
        "handover/loose.md", "handover/missing.md"]


def test_the_reference_extractor_finds_table_cells_imports_and_hints():
    text = ("@handover/ENGINEERING_RULES.md\n| `live-model-tiers.md` | x |\n(`cat handover/claude-context/<file>`)\n"
            "`handover/claude-context/kernel-mcp-and-tool-authority.md` and handover/BACKLOG_REGISTRY.csv")
    assert referenced_authority(text) == {"handover/ENGINEERING_RULES.md", "handover/claude-context/kernel-mcp-and-tool-authority.md"}


@pytest.mark.skipif(not _in_git_checkout(), reason="a source archive has no index; it contains only tracked files by construction")
def test_every_authority_file_claude_md_names_is_tracked():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    table = {f"{CONTEXT_DIR}/{name}" for name in re.findall(r"^\| `([a-z0-9-]+\.md)` \|", text, flags=re.MULTILINE)}
    named = referenced_authority(text) | table
    assert "handover/ENGINEERING_RULES.md" in named and len(table) >= 8, named           # the extraction really found the table
    assert untracked(ROOT, named) == []


def test_no_context_file_is_orphaned_from_claude_mds_table():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    in_table = set(re.findall(r"^\| `([a-z0-9-]+\.md)` \|", text, flags=re.MULTILINE))
    on_disk = {path.name for path in (ROOT / CONTEXT_DIR).glob("*.md")}
    assert on_disk == in_table, (sorted(on_disk - in_table), sorted(in_table - on_disk))
