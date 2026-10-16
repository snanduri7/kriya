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
