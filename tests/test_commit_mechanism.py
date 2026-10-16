"""The REPOSITORY write/commit/snapshot mechanism, byte for byte (full modularization, slice M1).

``tests/golden/repository/commit_mechanism.json`` was WRITTEN FROM THE PRE-M1 CODE (``kriya.workflow.edit_safety`` and
``kriya.workflow.file_integrity`` at 1cbd41f, before any M1 production edit), generated three times (identical). For
every scenario it pins the ORDERED os-level calls of the mechanism (``tests/_repository_mechanism_harness.py``: every
open/os.open/fdopen/mkstemp/fsync/close/replace/unlink/chmod/makedirs/mkdir/rmdir/umask/listdir under the scenario
root, temp names numbered in creation order), the return value or the exception (class, kriya MRO, message, cause),
every commit-evidence file as written, and the final tree (path, kind, mode, sha256). The scenarios cover
``commit_revision_grounded_file``, ``atomic_write_file``, ``commit_revision_grounded_batch`` (modify/create/delete,
preflight refusals, the uncertainty refusal, rollback, a failing rollback = UNCERTAIN evidence, intent/terminal
evidence persistence failures, a staging failure, a base changed during the commit, an interrupt mid-apply), the
evidence readers/settlement and the snapshot/digest primitives. The harness reaches the mechanism only through the
old import paths and injects faults only at the os level, so this file runs unchanged before and after the move.
Regenerate (only from a commit whose behaviour is known good) with ``KRIYA_WRITE_REPOSITORY_GOLDEN=1``.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pytest

from kriya.workflow import edit_safety as es
from kriya.workflow import file_integrity as fi
from tests._repository_mechanism_harness import Recorder, describe_error, tree

GOLDEN = Path(__file__).resolve().parent / "golden" / "repository" / "commit_mechanism.json"
WRITE = os.environ.get("KRIYA_WRITE_REPOSITORY_GOLDEN") == "1"


def _rev(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes, mode: Optional[int] = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mode is not None:
        os.chmod(path, mode)
    return path


def _result(value: Any, recorder: Recorder) -> Any:
    if isinstance(value, es.BatchCommitResult):
        return {"revisions": recorder.norm(dict(sorted(value.items()))), "evidence": recorder.norm(value.evidence.to_dict())}
    if isinstance(value, es.CommitEvidence):
        return recorder.norm(value.to_dict())
    if isinstance(value, fi.FileSnapshot):
        return recorder.norm(_snapshot_form(value))
    if isinstance(value, (frozenset, set)):
        return sorted(recorder.norm(list(value)))
    if isinstance(value, tuple):
        return [_result(item, recorder) for item in value]
    if isinstance(value, list):
        return [_result(item, recorder) for item in value]
    if isinstance(value, es.CommitState):
        return value.value
    return recorder.norm(value)


def _scenario(base: Path, setup: Callable[[Path], Dict[str, Any]], body: Callable[[Path, Dict[str, Any]], Any],
              fault: Optional[Callable] = None) -> Dict[str, Any]:
    previous_umask = os.umask(0o022)   # file modes in the tree and the create mode the mechanism derives
    out: Dict[str, Any] = {}
    try:
        base.mkdir(parents=True)
        workspace = base / "ws"
        workspace.mkdir()
        context = setup(workspace)
        recorder = Recorder(str(base), fault=fault(context) if fault else None)
        with recorder:
            try:
                out["result"] = _result(body(workspace, context), recorder)
            except BaseException as error:  # noqa: BLE001 - KeyboardInterrupt mid-apply is a scenario
                out["error"] = describe_error(error, recorder)
    finally:
        os.umask(previous_umask)
    out["calls"] = recorder.calls()
    out["tree"] = tree(str(base), recorder)
    return out


# ---------------------------------------------------------------- scenario setups and faults

def _batch_files(workspace: Path) -> Dict[str, Any]:
    _write(workspace / "modified.txt", b"old modified\n", 0o754)
    _write(workspace / "deleted.txt", b"old deleted\n", 0o640)
    _write(workspace / "crlf.txt", b"a\r\nb\r\n", 0o644)
    return {}


def _mixed_writes(workspace: Path):
    return [
        es.StagedFileWrite(str(workspace / "nested" / "dir" / "created.txt"), "new created\n",
                           str(workspace / "nested" / "dir" / "created.txt"), _rev(b""), expected_base_exists=False),
        es.StagedFileWrite(str(workspace / "modified.txt"), "new modified\n", str(workspace / "modified.txt"),
                           _rev(b"old modified\n"), expected_base_exists=True),
        es.StagedFileWrite(str(workspace / "deleted.txt"), "", str(workspace / "deleted.txt"), _rev(b"old deleted\n"),
                           delete=True, expected_base_exists=True),
        es.StagedFileWrite(str(workspace / "crlf.txt"), "x\ny\n", str(workspace / "crlf.txt"), _rev(b"a\r\nb\r\n"),
                           expected_base_exists=True, content_bytes=b"x\r\ny\r\n", mode=0o755),
    ]


def _batch(transaction_id: Optional[str] = "tx-mixed", workspace_bound: bool = True):
    def body(workspace: Path, _context):
        return es.commit_revision_grounded_batch(_mixed_writes(workspace), str(workspace) if workspace_bound else None,
                                                 transaction_id=transaction_id)
    return body


def _fail_once(op: str, predicate: Callable[[tuple], bool], error: Optional[BaseException] = None,
               after: int = 0):
    error = OSError("injected fault") if error is None else error

    def factory(_context):
        state = {"seen": 0}

        def hook(name, args):
            if name == op and predicate(args):
                state["seen"] += 1
                if state["seen"] == after + 1:
                    return error
            return None
        return hook
    return factory


def _ends(suffix: str) -> Callable[[tuple], bool]:
    return lambda args: os.fsdecode(args[-1]).endswith(suffix)


def _src_contains(fragment: str, dst_suffix: str) -> Callable[[tuple], bool]:
    return lambda args: fragment in os.fsdecode(args[0]) and os.fsdecode(args[1]).endswith(dst_suffix)


def _faults(*hooks: Callable) -> Callable:
    def factory(context):
        built = [hook(context) for hook in hooks]

        def combined(name, args):
            for hook in built:
                error = hook(name, args)
                if error is not None:
                    return error
            return None
        return combined
    return factory


def _change_base_during_staging(_context):
    """Before the last staged file is created, rewrite modified.txt (its base): the in-apply re-check must refuse."""
    def hook(name, args):
        if name == "mkstemp" and str(args[0]).endswith("-3-"):
            target = os.path.join(os.fsdecode(args[1]), "modified.txt")
            fd = os.open(target, os.O_WRONLY | os.O_TRUNC)
            try:
                os.write(fd, b"changed by someone else\n")
            finally:
                os.close(fd)
        return None
    return hook


def _evidence_file(workspace: Path, transaction_id: str, payload: Any) -> Path:
    directory = workspace / ".kriya" / "control" / "commits"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{transaction_id}.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True))
    return path


def _evidence_payload(transaction_id: str, state: str, schema: int = 3, **extra: Any) -> Dict[str, Any]:
    payload = {"schema_version": schema, "transaction_id": transaction_id, "state": state, "started_at_unix": 1.0,
               "updated_at_unix": 2.0, "operations": [{"target_path": "a.txt", "operation": "write"}],
               "result_revisions": {}, "failure": None}
    payload.update(extra)
    return payload


def _snapshot_files(workspace: Path) -> Dict[str, Any]:
    files = {
        "lf.txt": b"one\ntwo\n", "crlf.txt": b"one\r\ntwo\r\n", "bom.txt": b"\xef\xbb\xbfone\ntwo", "mixed.txt": b"a\r\nb\n",
        "stray_cr.txt": b"a\r\r\nb\r\n", "invalid.txt": b"ok\n\xff\xfe\n", "empty.txt": b"", "nonl.txt": b"no newline",
        "lone_cr.txt": b"a\rb\n",
    }
    for name, data in files.items():
        _write(workspace / name, data, 0o640)
    os.symlink("lf.txt", workspace / "link.txt")
    return {"names": sorted(files) + ["link.txt", "missing.txt"]}


def _snapshots(workspace: Path, context):
    out = {}
    for name in context["names"]:
        path = str(workspace / name)
        snapshot = fi.load_snapshot(path)
        entry: Dict[str, Any] = {"snapshot": snapshot}
        for label, call in (("text", lambda s=snapshot: s.text), ("encode_text", lambda s=snapshot: s.encode(s.text).hex()),
                            ("encode_lf", lambda s=snapshot: s.encode("x\ny\n").hex()),
                            ("encode_crlf_payload", lambda s=snapshot: s.encode("x\r\ny\n").hex()),
                            ("shown", lambda p=path: list(fi.read_shown_text(p))),
                            ("unchanged_ok", lambda s=snapshot: fi.require_unchanged(s, s.raw_sha256)),
                            ("unchanged_refused", lambda s=snapshot: fi.require_unchanged(s, "0" * 64)),
                            ("revision", lambda p=path: es.read_file_revision(p)),
                            ("raw_digest", lambda p=path: fi.file_raw_digest(p))):
            try:
                entry[label] = call()
            except Exception as error:  # noqa: BLE001 - the typed refusal is the recorded form
                entry[label] = {"error": [type(error).__name__, str(error), getattr(error, "reason_code", None)]}
        out[name] = entry
    out["display_text"] = fi.display_text(b"ok\xff\n")
    out["newline_styles"] = [fi.newline_style(data) for data in (b"", b"a\n", b"a\r\n", b"a\r\nb\n", b"a")]
    out["raw_digest"] = fi.raw_digest(b"abc")
    out["content_revision"] = es.content_revision("abc\n")
    return _jsonable(out)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, fi.FileSnapshot):
        return _snapshot_form(value)
    return value


def _snapshot_form(snapshot) -> Dict[str, Any]:
    form = {k: (v.hex() if isinstance(v, bytes) else v) for k, v in snapshot.__dict__.items()}
    if snapshot.is_symlink:
        form["file_mode"] = "<link mode: platform-defined (0o755 macOS, 0o777 Linux)>"
    return form


def _git_tree(workspace: Path) -> Dict[str, Any]:
    environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=workspace, check=True, env=environment)
    _write(workspace / "tracked.txt", b"t\n")
    _write(workspace / ".gitignore", b"ignored/\n*.log\n")
    subprocess.run(["git", "add", "tracked.txt", ".gitignore"], cwd=workspace, check=True, env=environment)
    _write(workspace / "untracked.txt", b"u\n")
    _write(workspace / "sub" / "deep.txt", b"d\n")
    _write(workspace / "ignored" / "x.txt", b"i\n")
    _write(workspace / "build.log", b"l\n")
    _write(workspace / ".kriya" / "control" / "state.json", b"{}\n")
    _write(workspace / ".kriya-notes.txt", b"not kriya state\n")
    (workspace.parent / "plain").mkdir()
    return {}


def _binding(workspace: Path, _context):
    # one bound path: the binding records the union of two path SETS, whose iteration order is hash-randomized
    binding = fi.VerificationTreeBinding(str(workspace), ["tracked.txt"], [])
    untouched = binding.changes()
    os.utime(workspace / "tracked.txt", None)
    touched = binding.changes()
    (workspace / "tracked.txt").write_bytes(b"t2\n")
    changed = binding.changes()
    return {"paths": list(binding.paths), "untouched": untouched, "touched": touched, "changed": changed,
            "untracked": sorted(fi.untracked_repository_paths(str(workspace))),
            "not_a_repository": sorted(fi.untracked_repository_paths(str(workspace.parent / "plain")))}


def _settle(state: str, target: Any):
    def body(workspace: Path, context):
        expected = es.load_commit_evidence(str(context["path"]))
        if context.get("mutate_after_read"):
            _evidence_file(workspace, "tx-settle", _evidence_payload("tx-settle", "in_progress", failure="changed"))
        return es.settle_recovered_commit_evidence(str(workspace), expected, state=target,
                                                   recovery={"tool": "test", "decision": "proved"},
                                                   result_revisions={"a.txt": "r"})
    return body


def _settle_setup(state: str, mutate: bool = False):
    def setup(workspace: Path):
        return {"path": _evidence_file(workspace, "tx-settle", _evidence_payload("tx-settle", state)), "mutate_after_read": mutate}
    return setup


def _evidence_readers(workspace: Path, _context):
    out = {}
    for schema in (1, 2, 3, 4):
        path = _evidence_file(workspace, f"schema{schema}", _evidence_payload(f"schema{schema}", "committed", schema=schema,
                                                                              candidate_hash="h" if schema > 1 else None))
        try:
            out[f"schema{schema}"] = es.load_commit_evidence(str(path)).to_dict()
        except Exception as error:  # noqa: BLE001 - the refusal is the recorded form
            out[f"schema{schema}"] = [type(error).__name__, str(error)]
    os.unlink(workspace / ".kriya" / "control" / "commits" / "schema4.json")
    _evidence_file(workspace, "broken", "{not json")
    _evidence_file(workspace, "open", _evidence_payload("open", "in_progress"))
    (workspace / ".kriya" / "control" / "commits" / "notes.txt").write_text("ignored")
    out["listed"] = [[os.path.basename(p), None if e is None else e.state.value, err] for p, e, err in es.list_commit_evidence(str(workspace))]
    try:
        es.find_uncertain_commit_evidence(str(workspace))
    except Exception as error:  # noqa: BLE001 - the refusal is the recorded form
        out["uncertain_with_unreadable"] = [type(error).__name__, str(error)]
    os.unlink(workspace / ".kriya" / "control" / "commits" / "broken.json")
    out["uncertain"] = [e.transaction_id for e in es.find_uncertain_commit_evidence(str(workspace))]
    out["state_open"] = es.commit_state_for_transaction(str(workspace), "open").value
    out["state_absent"] = es.commit_state_for_transaction(str(workspace), "absent").value
    out["dir"] = es.commit_evidence_dir(str(workspace))
    out["prefix"] = es.stage_file_prefix("tx")
    writes = _mixed_writes(workspace)
    out["candidate_digest_bound"] = es.candidate_digest(writes, str(workspace))
    out["candidate_digest_unbound"] = es.candidate_digest(   # no workspace root: the target path itself is the key
        [es.StagedFileWrite("rel/a.txt", "a\n", "rel/a.txt", _rev(b"")), es.StagedFileWrite("rel/b.txt", "", "rel/b.txt", _rev(b"b"), delete=True)],
        None)
    out["candidate_digest_of_entries"] = es.candidate_digest_of_entries([("b", "delete", None), ("a", "f" * 64, 420)])
    out["empty_listing_without_dir"] = es.list_commit_evidence(str(workspace / "nowhere"))
    return out


def _file(name: str, data: bytes, mode: Optional[int] = 0o640):
    def setup(workspace: Path):
        if data is not None:
            _write(workspace / name, data, mode)
        return {}
    return setup


def _grounded(name: str, content: str, expected: Optional[bytes], workspace_bound: bool = True, content_bytes=None,
              expected_revision: Optional[str] = None):
    def body(workspace: Path, _context):
        return es.commit_revision_grounded_file(str(workspace / name), content,
                                                expected_revision if expected_revision is not None else _rev(expected or b""),
                                                str(workspace) if workspace_bound else None, content_bytes=content_bytes)
    return body


def _symlink_setup(workspace: Path):
    _write(workspace / "real.txt", b"real\n")
    os.symlink("real.txt", workspace / "link.txt")
    return {}


def _single(name: str, write: Callable[[Path], es.StagedFileWrite], transaction_id: str = "tx-one"):
    def body(workspace: Path, _context):
        return es.commit_revision_grounded_batch([write(workspace)], str(workspace), transaction_id=transaction_id)
    return body


def _preflight_cases() -> Dict[str, Callable[[Path], Any]]:
    def batch(writes, workspace, tx="tx-pre"):
        return es.commit_revision_grounded_batch(writes, str(workspace), transaction_id=tx)

    def w(workspace, name, content="c\n", base=None, rev=None, **kwargs):
        target = str(workspace / name) if not os.path.isabs(name) else name
        return es.StagedFileWrite(target, content, base or target, rev if rev is not None else _rev(b""), **kwargs)

    return {
        "stale_base": lambda ws: batch([w(ws, "modified.txt", rev=_rev(b"something else\n"))], ws),
        "duplicate": lambda ws: batch([w(ws, "a.txt"), w(ws, "a.txt")], ws),
        "aliased": lambda ws: batch([w(ws, "a.txt"), w(ws, os.path.join(str(ws), "sub", "..", "a.txt"))], ws),
        "missing_fields": lambda ws: batch([w(ws, "a.txt", rev="")], ws),
        "escape": lambda ws: batch([w(ws, os.path.join(str(ws.parent), "outside.txt"))], ws),
        "symlink_target": lambda ws: batch([w(ws, "link.txt", rev=_rev(b"real\n"))], ws),
        "directory_target": lambda ws: batch([w(ws, "adir")], ws),
        "base_existence_mismatch": lambda ws: batch([w(ws, "modified.txt", rev=_rev(b"old modified\n"), expected_base_exists=False)], ws),
        "create_existing": lambda ws: batch([w(ws, "existing_target.txt", base=str(ws / "absent_base.txt"), expected_base_exists=False)], ws),
        "delete_declares_missing_base": lambda ws: batch([w(ws, "absent.txt", delete=True, expected_base_exists=False)], ws),
        "delete_missing_target": lambda ws: batch([w(ws, "absent.txt", delete=True)], ws),
        "invalid_transaction_id": lambda ws: batch([w(ws, "new.txt", expected_base_exists=False)], ws, tx="bad id!"),
        "empty_batch_bound": lambda ws: batch([], ws, tx="tx-empty"),
        "empty_batch_default_id": lambda ws: es.commit_revision_grounded_batch([], str(ws)),
    }


def _preflight_setup(workspace: Path):
    _write(workspace / "modified.txt", b"old modified\n")
    _write(workspace / "existing_target.txt", b"x\n")
    _write(workspace / "real.txt", b"real\n")
    os.symlink("real.txt", workspace / "link.txt")
    (workspace / "adir").mkdir()
    return {}


def scenarios() -> Dict[str, Dict[str, Any]]:
    cases: Dict[str, tuple] = {
        # ---- commit_revision_grounded_file / atomic_write_file
        "file_modify_keeps_mode": (_file("f.txt", b"old\n", 0o755), _grounded("f.txt", "new\n", b"old\n"), None),
        "file_create": (_file("f.txt", None), _grounded("f.txt", "created\n", b""), None),
        "file_unbound_workspace": (_file("f.txt", b"old\n"), _grounded("f.txt", "new\n", b"old\n", workspace_bound=False), None),
        "file_content_bytes": (_file("f.txt", b"a\r\n"), _grounded("f.txt", "b\n", b"a\r\n", content_bytes=b"b\r\n"), None),
        "file_stale": (_file("f.txt", b"old\n"), _grounded("f.txt", "new\n", None, expected_revision=_rev(b"other\n")), None),
        "file_symlink": (_symlink_setup, _grounded("link.txt", "new\n", b"real\n"), None),
        "file_replace_fails": (_file("f.txt", b"old\n"), _grounded("f.txt", "new\n", b"old\n"),
                               _fail_once("replace", _ends("f.txt"))),
        "atomic_write": (_file("f.txt", b"old\n"), lambda ws, _c: es.atomic_write_file(str(ws / "f.txt"), "atomic\n", str(ws)), None),
        "atomic_write_bytes_unbound": (_file("f.txt", None),
                                       lambda ws, _c: es.atomic_write_file(str(ws / "f.txt"), "ignored", content_bytes=b"\x00raw"), None),
        "atomic_write_interrupted": (_file("f.txt", b"old\n"), lambda ws, _c: es.atomic_write_file(str(ws / "f.txt"), "new\n"),
                                     _fail_once("replace", _ends("f.txt"))),
        # ---- commit_revision_grounded_batch: success paths
        "batch_mixed": (_batch_files, _batch(), None),
        "batch_unbound_workspace": (_batch_files, _batch(workspace_bound=False), None),
        "batch_default_transaction_id": (_batch_files, _batch(transaction_id=None), None),
        # ---- refusal before any write
        "batch_prior_uncertain": (lambda ws: {**_batch_files(ws), "e": _evidence_file(ws, "prior", _evidence_payload("prior", "uncertain"))},
                                  _batch(), None),
        "batch_prior_unreadable": (lambda ws: {**_batch_files(ws), "e": _evidence_file(ws, "prior", "{broken")}, _batch(), None),
        "batch_intent_not_persisted": (_batch_files, _batch(), _fail_once("replace", _ends("tx-mixed.json"))),
        "batch_staging_fails": (_batch_files, _batch(),
                                _fail_once("mkstemp", lambda args: str(args[0]).startswith(".kriya-stage-"), after=1)),
        # ---- failure during apply
        "batch_apply_fails_rolls_back": (_batch_files, _batch(), _fail_once("replace", _ends("crlf.txt"))),
        "batch_first_apply_fails": (_batch_files, _batch(), _fail_once("replace", _ends("created.txt"))),
        "batch_rollback_fails_uncertain": (_batch_files, _batch(), _faults(
            _fail_once("replace", _src_contains(".kriya-stage-", "crlf.txt")),
            _fail_once("replace", _src_contains(".kriya-rollback-", "modified.txt")))),
        "batch_rolled_back_evidence_not_persisted": (_batch_files, _batch(), _faults(
            _fail_once("replace", _src_contains(".kriya-stage-", "crlf.txt")),
            _fail_once("replace", _ends("tx-mixed.json"), after=1))),
        "batch_committed_evidence_not_persisted": (_batch_files, _batch(), _fail_once("replace", _ends("tx-mixed.json"), after=1)),
        "batch_base_changed_during_commit": (_batch_files, _batch(), _change_base_during_staging),
        "batch_interrupted_mid_apply": (_batch_files, _batch(), _fail_once("replace", _ends("crlf.txt"), error=KeyboardInterrupt())),
        "batch_stage_fsync_fails": (_batch_files, _batch(), _fail_once("fsync", lambda args: ".kriya-stage-" in args[0])),
        "batch_single_modify": (_batch_files, _single("modified.txt", lambda ws: es.StagedFileWrite(
            str(ws / "modified.txt"), "single\n", str(ws / "modified.txt"), _rev(b"old modified\n"))), None),
        # ---- evidence readers and recovery settlement
        "evidence_readers": (_batch_files, _evidence_readers, None),
        "settle_committed": (_settle_setup("in_progress"), _settle("in_progress", es.CommitState.COMMITTED), None),
        "settle_rolled_back_from_uncertain": (_settle_setup("uncertain"), _settle("uncertain", es.CommitState.ROLLED_BACK), None),
        "settle_refuses_target_state": (_settle_setup("in_progress"), _settle("in_progress", es.CommitState.UNCERTAIN), None),
        "settle_refuses_settled_evidence": (_settle_setup("committed"), _settle("committed", es.CommitState.COMMITTED), None),
        "settle_refuses_changed_evidence": (_settle_setup("in_progress", mutate=True), _settle("in_progress", es.CommitState.COMMITTED), None),
        # ---- snapshot / digest primitives and the tree binding that uses them
        "snapshots": (_snapshot_files, _snapshots, None),
        "untracked_and_binding": (_git_tree, _binding, None),
    }
    for name, case in _preflight_cases().items():
        cases[f"preflight_{name}"] = (_preflight_setup, lambda ws, _c, case=case: case(ws), None)
    return cases


def _record_all(tmp_path: Path, monkeypatch) -> str:
    # untracked_repository_paths runs git with the process environment: no global/system git config (a user's
    # core.excludesFile) may change what it lists.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    out = {name: _scenario(tmp_path / name, setup, body, fault) for name, (setup, body, fault) in scenarios().items()}
    return json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def test_the_mechanism_is_byte_identical_to_the_pre_move_golden(tmp_path, monkeypatch):
    text = _record_all(tmp_path, monkeypatch)
    if WRITE:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
    assert not WRITE, "golden written; rerun without KRIYA_WRITE_REPOSITORY_GOLDEN"
    assert text == GOLDEN.read_text(encoding="utf-8")


def test_the_golden_comparison_is_not_vacuous():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    mixed = golden["batch_mixed"]
    ops = [call[0] for call in mixed["calls"]]
    # IN_PROGRESS evidence (fsynced file + directory) before the first staged byte; every stage fsynced before any replace.
    first_stage = next(i for i, call in enumerate(mixed["calls"]) if call[0] == "mkstemp" and ".kriya-stage-" in str(call[1]))
    assert ["fsync", "replace", "os.open", "fsync", "close"] == [
        op for op in ops[:first_stage] if op in ("fsync", "replace", "os.open", "close")][-5:]
    assert mixed["result"]["evidence"]["state"] == "committed"
    assert golden["batch_rollback_fails_uncertain"]["error"]["class"] == "UncertainCommitError"
    assert any('"state":"uncertain"' in row[4] for row in golden["batch_rollback_fails_uncertain"]["tree"] if row[4])
    assert golden["batch_apply_fails_rolls_back"]["error"]["message"].startswith("Candidate commit failed and was rolled back")
    assert golden["preflight_stale_base"]["error"]["class"] == "FileRevisionConflict"
    assert golden["batch_base_changed_during_commit"]["error"]["cause"][1].endswith("base changed during commit.")
    planted = dict(golden, batch_mixed=dict(mixed, calls=[c for c in mixed["calls"] if c[0] != "fsync"]))
    assert json.dumps(planted, indent=1, sort_keys=True, ensure_ascii=False) + "\n" != GOLDEN.read_text(encoding="utf-8")


def _fsync_before(calls, replace_target_suffix: str) -> bool:
    """The staged file replaced onto ``replace_target_suffix`` was fsynced before that replace."""
    for index, call in enumerate(calls):
        if call[0] == "replace" and call[2].endswith(replace_target_suffix):
            source = call[1]
            return any(c[0] == "fsync" and c[1] == source for c in calls[:index])
    raise AssertionError(f"no replace onto {replace_target_suffix}")


def test_every_staged_file_is_fsynced_before_it_replaces_its_target(tmp_path):
    out = _scenario(tmp_path / "s", _batch_files, _batch())
    for suffix in ("created.txt", "modified.txt", "crlf.txt"):
        assert _fsync_before(out["calls"], suffix), suffix
    evidence = [c for c in out["calls"] if c[0] == "replace" and c[2].endswith("tx-mixed.json")]
    assert len(evidence) == 2   # IN_PROGRESS then COMMITTED
    for call in evidence:
        assert _fsync_before(out["calls"][:out["calls"].index(call) + 1], call[2][-len("tx-mixed.json"):])


def test_a_failing_rollback_leaves_uncertain_evidence_and_raises_uncertain(tmp_path):
    setup, body, fault = scenarios()["batch_rollback_fails_uncertain"]
    out = _scenario(tmp_path / "s", setup, body, fault)
    assert out["error"]["class"] == "UncertainCommitError" and "rollback also failed for" in out["error"]["message"]
    evidence = json.loads(next(row[4] for row in out["tree"] if row[4]))
    assert evidence["state"] == "uncertain" and evidence["failure"].startswith("OSError: injected fault")


def test_a_partial_batch_failure_restores_every_applied_path(tmp_path):
    setup, body, fault = scenarios()["batch_apply_fails_rolls_back"]
    out = _scenario(tmp_path / "s", setup, body, fault)
    files = {row[0]: row for row in out["tree"] if row[1] == "file"}
    assert files["ws/modified.txt"][2:4] == [oct(0o754), _rev(b"old modified\n")]
    assert files["ws/deleted.txt"][2:4] == [oct(0o640), _rev(b"old deleted\n")]
    assert files["ws/crlf.txt"][3] == _rev(b"a\r\nb\r\n")
    assert "ws/nested" not in {row[0] for row in out["tree"]}   # created directories removed again
    evidence = json.loads(next(row[4] for row in out["tree"] if row[4]))
    assert evidence["state"] == "rolled_back"


@pytest.mark.parametrize("stale", ["preflight", "during_commit"])
def test_a_stale_base_never_reaches_the_target(tmp_path, stale):
    name = "preflight_stale_base" if stale == "preflight" else "batch_base_changed_during_commit"
    setup, body, fault = scenarios()[name]
    out = _scenario(tmp_path / "s", setup, body, fault)
    assert not any(c[0] == "replace" and c[2].endswith("ws/modified.txt") for c in out["calls"])
    if stale == "preflight":
        # refused by the whole-batch preflight itself: no evidence and no staged byte (the in-apply re-check must not
        # be what catches it - that one runs only after IN_PROGRESS evidence and staging)
        assert out["error"]["class"] == "FileRevisionConflict"
        assert not any(c[0] == "mkstemp" for c in out["calls"])
    else:
        assert out["error"]["class"] == "BatchCommitError" and out["error"]["cause"][0] == "FileRevisionConflict"


# ---------------------------------------------------------------- after M1: ownership and the legacy patch points

def test_the_old_import_paths_are_the_repository_owners_objects():
    from kriya.repository import atomic_write, commit, snapshot
    from kriya.workflow import terminal_commit

    assert es.commit_revision_grounded_batch is commit.commit_revision_grounded_batch
    assert es.atomic_write_file is atomic_write.atomic_write_file and es._execution_policy is atomic_write._execution_policy
    assert fi.load_snapshot is snapshot.load_snapshot and fi.FileIntegrityError is snapshot.FileIntegrityError
    assert terminal_commit.CandidateMaterializationError is commit.CandidateMaterializationError is es.CandidateMaterializationError
    # the privates are NOT on the facade: a patch aimed there fails loudly instead of silently missing the mechanism
    assert not any(hasattr(es, name) for name in ("_persist_commit_evidence", "_stage_content", "_atomic_write_bytes"))
    # the audit logger keeps its pre-move name (run logs carry %(name)s)
    assert atomic_write.logger.name == "kriya.workflow.edit_safety"


def test_the_legacy_os_patch_points_on_the_facade_still_reach_the_moved_mechanism(tmp_path):
    """patch("kriya.workflow.edit_safety.os.replace") (7 sites) and edit_safety.tempfile.mkstemp patch the SHARED os /
    tempfile module objects, so they bite inside kriya/repository/commit.py."""
    from unittest.mock import patch

    target = _write(tmp_path / "a.txt", b"old\n")
    write = es.StagedFileWrite(str(target), "new\n", str(target), _rev(b"old\n"))
    with patch("kriya.workflow.edit_safety.os.replace", side_effect=OSError("facade os patch")), \
            pytest.raises(es.BatchCommitError, match="facade os patch"):
        es.commit_revision_grounded_batch([write], None)
    with patch("kriya.workflow.edit_safety.tempfile.mkstemp", side_effect=OSError("facade tempfile patch")), \
            pytest.raises(es.BatchCommitError, match="facade tempfile patch"):
        es.commit_revision_grounded_batch([write], None)
    assert target.read_bytes() == b"old\n"
