"""The trace-history snapshot store owned by STATE (full modularization D2; MODULE-CONTRACTS §6 ``HistoryStore``).

``tests/golden/state/snapshot_forms.json`` was WRITTEN FROM THE PRE-D2 CODE (``kriya/kup/store.py`` + ``kriya/kup/acquire.py`` at the D1
commit, before any D2 edit) and is compared byte for byte: every ``snapshot.*`` envelope (acquire, list, verify, prune and their typed
refusals) answered through ``run_kup_operation``, the published snapshot's on-disk layout and permissions, and its manifest's keys. Values
that differ between two runs of identical code (clock, durations, digests of a freshly written file, inode/mtime) are tokens; snapshot ids
are numbered in order of appearance; the temporary state directory is ``<state>``. Regenerate (only from a commit whose forms are known
good) with ``KRIYA_WRITE_STATE_GOLDEN=1``.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, List

from _kup_fixtures import seed_store

from kriya.config import AppConfig
from kriya.core.state_paths import resolve_state_directory, trace_db_path
from kriya.kup.cli_ops import run_kup_operation

GOLDEN = Path(__file__).resolve().parent / "golden" / "state" / "snapshot_forms.json"
WRITE = os.environ.get("KRIYA_WRITE_STATE_GOLDEN") == "1"
VOLATILE_KEYS = frozenset({"observed_at", "verified_at", "duration_ms", "acquisition_started_at", "acquisition_completed_at", "mtime_ns",
                           "inode", "sha256", "size", "wal_size", "shm_size", "journal_size", "sqlite_version", "backup_steps"})
SNAPSHOT_ID = re.compile(r"\d{8}T\d{12}Z-[0-9a-f]{8}")


class _Tokens:
    def __init__(self, state: str) -> None:
        self.state, self.ids = state, {}  # type: Dict[str, str]

    def __call__(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: ("<volatile>" if k in VOLATILE_KEYS and v is not None else self(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [self(v) for v in value]
        if isinstance(value, str):
            value = value.replace(self.state, "<state>")
            return SNAPSHOT_ID.sub(lambda m: self.ids.setdefault(m.group(0), f"<snap-{len(self.ids) + 1}>"), value)
        return value


def _op(request: Dict[str, Any]) -> Dict[str, Any]:
    return run_kup_operation(AppConfig(), request, request_id="golden")


def _layout(snapshot_dir: str, tokens: _Tokens) -> List[str]:
    out = []
    for root, dirs, files in os.walk(snapshot_dir):
        dirs.sort()
        for name in sorted(dirs) + sorted(files):
            path = os.path.join(root, name)
            mode = stat.S_IMODE(os.lstat(path).st_mode)
            out.append(f"{tokens(os.path.relpath(path, snapshot_dir))} {oct(mode)}{'/' if os.path.isdir(path) else ''}")
    return sorted(out)


def _forms() -> Dict[str, Any]:
    cfg = AppConfig()
    state = resolve_state_directory(cfg)[0]
    tokens = _Tokens(state)
    forms: Dict[str, Any] = {"acquire_without_a_store": _op({"operation": "snapshot.acquire"}),
                             "list_without_snapshots": _op({"operation": "snapshot.list"}),
                             "prune_without_a_directory": _op({"operation": "snapshot.prune"})}
    seed_store(trace_db_path(cfg), 5)
    first = _op({"operation": "snapshot.acquire"})
    second = _op({"operation": "snapshot.acquire"})
    forms.update(acquire=first, acquire_again=second,
                 list=_op({"operation": "snapshot.list"}), list_verified=_op({"operation": "snapshot.list", "verify": True}),
                 verify=_op({"operation": "snapshot.verify", "snapshot_id": first["data"]["snapshot_id"]}),
                 verify_no_id=_op({"operation": "snapshot.verify"}),
                 verify_bad_id=_op({"operation": "snapshot.verify", "snapshot_id": "nope"}),
                 verify_unknown=_op({"operation": "snapshot.verify", "snapshot_id": "20200101T000000000000Z-00000000"}),
                 history_unknown_snapshot=_op({"operation": "history.list", "snapshot_id": "20200101T000000000000Z-00000000"}))
    snapshot_dir = os.path.join(state, "kup-snapshots")
    forms["layout"] = _layout(snapshot_dir, tokens)
    with open(os.path.join(snapshot_dir, first["data"]["snapshot_id"], "manifest.json"), encoding="utf-8") as handle:
        forms["manifest_keys"] = sorted(json.load(handle))
    forms["prune_bad_keep"] = _op({"operation": "snapshot.prune", "keep": -1})
    forms["prune_keep_one"] = _op({"operation": "snapshot.prune", "keep": 1})
    forms["layout_after_prune"] = _layout(snapshot_dir, tokens)
    survivor = os.path.join(snapshot_dir, second["data"]["snapshot_id"], "traces.snapshot.db")
    os.chmod(survivor, 0o600)
    with open(survivor, "ab") as handle:
        handle.write(b"tamper")
    forms["verify_corrupt"] = _op({"operation": "snapshot.verify", "snapshot_id": second["data"]["snapshot_id"]})
    forms["history_corrupt"] = _op({"operation": "history.list", "snapshot_id": second["data"]["snapshot_id"]})
    forms["prune_all"] = _op({"operation": "snapshot.prune", "keep": 0})
    forms["list_after_prune_all"] = _op({"operation": "snapshot.list"})
    return tokens(forms)


def test_snapshot_forms_are_byte_identical_to_the_pre_move_golden():
    text = json.dumps(_forms(), indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if WRITE:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
    assert not WRITE, "golden written; rerun without KRIYA_WRITE_STATE_GOLDEN"
    assert text == GOLDEN.read_text(encoding="utf-8")


def test_the_snapshot_golden_is_not_vacuous():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert golden["acquire_without_a_store"]["error"]["code"] == "READ_ONLY_UNAVAILABLE"
    assert golden["acquire_without_a_store"]["error"]["database_state"] == "missing"
    assert golden["acquire"]["data"]["rows"] == 5 and golden["acquire"]["consistency"]["kind"] == "snapshot_copy"
    assert golden["verify_corrupt"]["error"]["code"] == "SNAPSHOT_CORRUPT"
    assert golden["prune_keep_one"]["data"]["removed"] == ["<snap-1>"]
    assert any(line.endswith("traces.snapshot.db 0o400") for line in golden["layout"])


# ------------------------------------------------------------------ the store owned by STATE (D2)

def _git_workspace(path: Path) -> str:
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "a.txt").write_text("v1\n")
    subprocess.run(["git", "add", "a.txt"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"], cwd=path, check=True)
    return str(path)


def test_history_is_scoped_to_the_state_directory_and_not_reconstructed_from_control_runs(tmp_path):
    """MODULE-CONTRACTS §6 invariant: a project's ``.kriya/control/runs`` RunRecords never become history; only the trace store of the
    active state directory (``trace_db_path(cfg)``) does, through a published snapshot."""
    from kriya.application.history import HistoryService
    from kriya.context.model import RequestContext
    from kriya.control.persistence import scan_run_records
    from kriya.control.run_coordinator import begin_mutating_run

    cfg = AppConfig()
    workspace = _git_workspace(tmp_path / "project")
    with begin_mutating_run(workspace) as context:
        control_run = context.run_id
    assert [r.run_id for r in scan_run_records(workspace).records] == [control_run]  # the project has a recorded run
    seed_store(trace_db_path(cfg), 2)
    service, ctx = HistoryService(), RequestContext.local(workspace)
    assert service.location(ctx, cfg).trace_database == trace_db_path(cfg)
    assert os.path.commonpath([service.location(ctx, cfg).snapshot_directory, resolve_state_directory(cfg)[0]]) == resolve_state_directory(cfg)[0]
    snapshot = service.acquire(ctx, cfg, workspace=None).snapshot
    listed = [row["run_id"] for row in service.list(ctx, cfg, snapshot, limit=50, cursor=None)["runs"]]
    assert listed == ["run-0001", "run-0000"] and control_run not in listed
    assert not os.path.exists(os.path.join(workspace, ".kriya", "kup-snapshots"))


def test_the_store_vocabulary_equals_the_kup_wire_vocabulary():
    """STATE may not import the adapter, so the store keeps its own constants; every one the KUP vocabulary also names is identical."""
    from kriya.control import history
    from kriya.control.history import acquire
    from kriya.kup import policy

    shared = 0
    for module in (history, acquire):
        for name in dir(module):
            if name.isupper() and not name.startswith("_") and hasattr(policy, name) and isinstance(getattr(module, name), (str, int, float)):
                assert getattr(module, name) == getattr(policy, name), name
                shared += 1
    assert shared >= 30


def test_the_read_side_never_imports_the_write_capable_module():
    """The KUP read path (history, list, verify) never loads acquisition: only HistoryStore.acquire/prune import it."""
    import ast

    root = Path(__file__).resolve().parents[1] / "kriya" / "control" / "history"
    for name in ("__init__.py",):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("history.acquire")]
    port = ast.parse((root / "port.py").read_text(encoding="utf-8"))
    top = [n for n in port.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not [n for n in top if any(alias.name == "acquire" for alias in n.names)]


def test_a_store_refusal_reaches_the_client_as_the_same_typed_refusal(tmp_path):
    import pytest

    from kriya.application.history import HistoryService
    from kriya.application.services import ApplicationRefusal
    from kriya.context.model import RequestContext

    with pytest.raises(ApplicationRefusal) as refused:
        HistoryService().acquire(RequestContext.local(None), AppConfig(), workspace=None)
    assert refused.value.reason_code == "READ_ONLY_UNAVAILABLE" and refused.value.extra == {"database_state": "missing"}


def test_acquisition_for_a_workspace_with_a_live_run_is_refused_through_the_kup_path(tmp_path):
    """The ``workspace`` argument of ``snapshot.acquire`` reaches the store's run-active refusal (HistoryService -> ProjectService.active_run):
    nothing is acquired while the workspace's run lock is held."""
    from kriya.control.run_coordinator import begin_mutating_run

    cfg = AppConfig()
    seed_store(trace_db_path(cfg), 1)
    workspace = _git_workspace(tmp_path / "busy")
    with begin_mutating_run(workspace):
        refused = _op({"operation": "snapshot.acquire", "workspace": workspace})
    assert refused["error"]["code"] == "ACQUISITION_REFUSED_RUN_ACTIVE", refused
    assert not os.path.isdir(os.path.join(resolve_state_directory(cfg)[0], "kup-snapshots"))
    admitted = _op({"operation": "snapshot.acquire", "workspace": workspace})
    assert admitted["error"] is None and admitted["data"]["rows"] == 1
