"""The checkpoint and trace stores owned by STATE (full modularization D2; MODULE-CONTRACTS §6 ``CheckpointStore``).

``tests/golden/state/checkpoint_and_trace_forms.json`` was WRITTEN FROM THE PRE-D2 CODE (``kriya.workflow.checkpoint`` and
``kriya.core.trace`` at the D1 commit, before any D2 edit) and is compared byte for byte: the checkpoint file as ``save_checkpoint``
writes it (key order, indentation, ownership block, run-record reference; the save time and path-bound identities replaced by tokens),
its path under the workspace, the config/registry fingerprints, the listing and run-reference rules, and the trace database schema and
one logged row exactly as stored. Regenerate (only from a commit whose forms are known good) with ``KRIYA_WRITE_STATE_GOLDEN=1``.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path

from kriya.context.workspace_identity import workspace_identity

GOLDEN = Path(__file__).resolve().parent / "golden" / "state" / "checkpoint_and_trace_forms.json"
WRITE = os.environ.get("KRIYA_WRITE_STATE_GOLDEN") == "1"


def _owners():
    """The checkpoint store and trace logger under test: their STATE owner modules (D2)."""
    from kriya.control import checkpoint, trace
    return checkpoint, trace


def _git_workspace(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "a.txt").write_text("v1\n")
    subprocess.run(["git", "add", "a.txt"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"], cwd=path, check=True)
    return str(path)


def _tokenize(text: str, workspace: str) -> str:
    text = text.replace(workspace_identity(workspace), "<workspace_id>")
    return re.sub(r'"saved_at": [0-9.e+-]+', '"saved_at": "<time>"', text)


def _forms(tmp_path: Path) -> dict:
    from kriya.control.run_coordinator import begin_mutating_run

    checkpoint, trace = _owners()
    workspace = _git_workspace(tmp_path / "ws")
    data = {"stage": "candidate", "goal_hash": "g" * 8, "nested": {"b": [1, 2], "a": None}, "flag": True}
    checkpoint.save_checkpoint(workspace, "20261016T000000-aaaaaaaa", data)
    plain = Path(checkpoint.checkpoint_path(workspace, "20261016T000000-aaaaaaaa")).read_text(encoding="utf-8")
    with begin_mutating_run(workspace) as context:
        checkpoint.save_checkpoint(workspace, "20261016T000001-bbbbbbbb", {"stage": "plan"})
        run_id, revision = context.run_id, context.record_revision
    inside = Path(checkpoint.checkpoint_path(workspace, "20261016T000001-bbbbbbbb")).read_text(encoding="utf-8")
    assert isinstance(revision, int) and f'"revision": {revision}' in inside
    inside = re.sub(r'("revision": )\d+', r'\1"<revision>"', inside.replace(run_id, "<run_id>"))
    (Path(workspace) / checkpoint.CHECKPOINT_DIR / "notes.txt").write_text("not a checkpoint")
    (Path(workspace) / checkpoint.CHECKPOINT_DIR / "broken.json").write_text("{not json")
    listed = checkpoint.list_checkpoints(workspace)
    checkpoint.delete_checkpoint(workspace, "20261016T000000-aaaaaaaa")
    after_delete = [c["run_id"] for c in checkpoint.list_checkpoints(workspace)]
    db_path = str(tmp_path / "traces.db")
    logger = trace.TraceLogger(db_path)
    logger.init_db()
    logger.log_run("r-1", "goal", 1.5, 2, "success", ["a.py", "b.py"], retrieved_chunks=[{"c": 1}], active_skills=["s"],
                   prompt_rendered="p", gate_outcomes=[{"gate": "compile"}], model_hops=[], failure_category=None,
                   run_events=[{"kind": "k"}], evidence_records=[], generation_metrics={"m": 1}, failure_report=[], enforce_run_id="e-1")
    logger.log_milestone_plan("grp", "VALID", 3, 2, 1, 0, 0, 1, ["X"], {"build_system": "maven"})
    with sqlite3.connect(db_path) as db:
        schema = [row[0] for row in db.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")]
        db.row_factory = sqlite3.Row
        row = dict(db.execute("SELECT * FROM runs").fetchone())
        plan = dict(db.execute("SELECT * FROM milestone_plans").fetchone())
    row["timestamp"], plan["timestamp"] = "<time>", "<time>"
    return {
        "checkpoint_dir": checkpoint.CHECKPOINT_DIR,
        "checkpoint_relpath": os.path.relpath(checkpoint.checkpoint_path(workspace, "r1"), workspace),
        "checkpoint_file_outside_a_run": _tokenize(plain, workspace),
        "checkpoint_file_inside_a_run": _tokenize(inside, workspace),
        "listed_run_ids": [c["run_id"] for c in listed],
        "latest": checkpoint.find_latest_checkpoint(workspace),
        "run_references": [ref.replace(run_id, "<run_id>") for ref in checkpoint.list_checkpoint_run_references(workspace)],
        "after_delete": after_delete,
        "config_fingerprint": checkpoint.compute_config_fingerprint({"b": [1, 2], "a": {"x": 1}, "evidence": {"capture": "off"}}),
        "config_fingerprint_without_evidence": checkpoint.compute_config_fingerprint({"b": [1, 2], "a": {"x": 1}}),
        "observational_sections": list(checkpoint.OBSERVATIONAL_CONFIG_SECTIONS),
        "registry_hash": checkpoint.compute_registry_hash({"contracts": {"c": {"revision": 2}}, "version": 1}),
        "new_run_id_shape": bool(re.fullmatch(r"\d{8}T\d{6}-[0-9a-f]{8}", checkpoint.new_run_id())),
        "trace_schema": schema,
        "trace_row": row,
        "trace_milestone_plan": plan,
    }


def _text(tmp_path: Path) -> str:
    return json.dumps(_forms(tmp_path), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def test_checkpoint_and_trace_forms_are_byte_identical_to_the_pre_move_golden(tmp_path):
    text = _text(tmp_path)
    if WRITE:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
    assert not WRITE, "golden written; rerun without KRIYA_WRITE_STATE_GOLDEN"
    assert text == GOLDEN.read_text(encoding="utf-8")


def test_the_golden_comparison_is_not_vacuous(tmp_path):
    """A planted change in any recorded form is a difference: the tokens never hide the content."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert '"stage": "candidate"' in golden["checkpoint_file_outside_a_run"] and "<workspace_id>" in golden["checkpoint_file_outside_a_run"]
    assert '"_run_record"' in golden["checkpoint_file_inside_a_run"] and '"_run_record"' not in golden["checkpoint_file_outside_a_run"]
    assert golden["trace_row"]["files_modified"] == "a.py,b.py" and golden["trace_row"]["enforce_run_id"] == "e-1"
    planted = dict(golden, config_fingerprint="0" * 64)
    assert json.dumps(planted, indent=1, sort_keys=True, ensure_ascii=False) + "\n" != GOLDEN.read_text(encoding="utf-8")



def test_the_old_import_paths_are_the_owners_objects():
    """``kriya.workflow.checkpoint`` (partial facade: it keeps the git fingerprints and the resume validation) and ``kriya.core.trace``
    (pure facade) hand out the STATE owners' objects, so the engine's patched ``save_checkpoint``/``delete_checkpoint`` and every old
    importer reach the one store."""
    from kriya.control import checkpoint as owner
    from kriya.control.trace import TraceLogger
    from kriya.core import trace as old_trace
    from kriya.workflow import checkpoint as old
    from kriya.workflow import workflow

    for name in owner.__all__:
        assert getattr(old, name) is getattr(owner, name), name
    assert old_trace.TraceLogger is TraceLogger
    assert workflow.save_checkpoint is owner.save_checkpoint and workflow.delete_checkpoint is owner.delete_checkpoint


def test_the_checkpoint_store_imports_no_kernel_module():
    import ast

    source = Path(__file__).resolve().parents[1] / "kriya" / "control" / "checkpoint.py"
    modules = {node.module for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
               if isinstance(node, ast.ImportFrom) and node.module}
    assert not {m for m in modules if m.startswith(("kriya.workflow", "kriya.agents", "kriya.core.kernel"))}
