"""``kriya static-analysis scan``: an operator-run, read-only gate evaluation
of the working tree's changes against a git base (PRD-031A §9.5).

It reuses the service unchanged: a detached git worktree of the base is the
"workspace" (the pristine PRE), and the working tree's changed files are the
batch (POST). Nothing is committed and the real workspace is never written.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Any, List

from kriya.static_analysis.service import StaticAnalysisGateResult, StaticAnalysisRequest, StaticAnalysisService
from kriya.workflow.edit_safety import StagedFileWrite
from kriya.workflow.file_integrity import display_text, file_raw_digest, raw_digest


class OperatorScanError(RuntimeError):
    pass


def _git(workspace: str, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=workspace, capture_output=True, text=True, check=False, timeout=120,
    )
    if completed.returncode != 0:
        raise OperatorScanError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout


def changed_paths(workspace: str, base: str) -> List[str]:
    """Tracked changes against ``base`` plus untracked files (not ignored)."""
    tracked = _git(workspace, "diff", "--name-only", "--no-renames", base, "--").splitlines()
    untracked = _git(workspace, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({p for p in (*tracked, *untracked) if p and not p.startswith(".kriya/")})


def operator_writes(workspace: str, base_root: str, paths: List[str]) -> List[StagedFileWrite]:
    """The working tree's changes as a batch targeting the base worktree."""
    writes: List[StagedFileWrite] = []
    for relpath in paths:
        base_file = os.path.join(base_root, relpath)
        base_exists = os.path.isfile(base_file) and not os.path.islink(base_file)
        base_revision = file_raw_digest(base_file) if base_exists else raw_digest(b"")
        current = os.path.join(workspace, relpath)
        if os.path.isfile(current) and not os.path.islink(current):
            with open(current, "rb") as handle:
                data = handle.read()
            writes.append(StagedFileWrite(
                target_path=base_file, content=display_text(data), base_path=base_file,
                expected_base_revision=base_revision, expected_base_exists=base_exists,
                content_bytes=data,
            ))
        elif base_exists:
            writes.append(StagedFileWrite(
                target_path=base_file, content="", base_path=base_file,
                expected_base_revision=base_revision, delete=True, expected_base_exists=True,
            ))
    return writes


def run_operator_scan(cfg: Any, workspace: str, base: str) -> StaticAnalysisGateResult:
    workspace = os.path.realpath(workspace)
    paths = changed_paths(workspace, base)
    with tempfile.TemporaryDirectory(prefix="kriya-static-analysis-base-") as parent:
        base_root = os.path.join(parent, "base")
        _git(workspace, "worktree", "add", "--detach", base_root, base)
        try:
            return StaticAnalysisService(cfg).evaluate(StaticAnalysisRequest(
                writes=operator_writes(workspace, base_root, paths), workspace_path=base_root,
                run_id="operator-scan", unit_id=base.replace("/", "_"), identity_root=workspace,
            ))
        finally:
            _git(workspace, "worktree", "remove", "--force", base_root)
