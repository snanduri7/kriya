"""The hosted live-model smoke contract (LIVE-SMOKE-CPU-TIMEOUT-001).

The `live_model and not live_target` tier runs a small CI model on a CPU
runner. It proves Kriya's runtime wiring and safety with a real model -
never generation quality, which belongs to the PRD-035 certification matrix
(`live_target`), where a typed failure is still FAILED.

A smoke `generate` passes when:
1. at least one real model request happened (the RunRecord binds a runtime
   fingerprint, recorded per real call, and the fingerprint is the smoke
   model's), the Planner made a real call, and every role reached is
   reported (a weak model is not required to get past the Planner);
2. that runtime identity is recorded;
3. it stayed within Kriya's own bounds (it finished inside the process
   limit - a timeout fails the test before this contract runs - and its
   retries within the attempt ceiling);
4. it reached a known terminal lifecycle, with every RunRecord parseable;
5. its typed outcome is SUCCESS or one of SMOKE_CAPABILITY_FAILURES;
6. the real workspace is unchanged unless the outcome is verified SUCCESS
   (and then only the reported files changed);
7. no safety invariant failed: no traceback, no egress violation, no
   uncertain or unsettled commit, no commit unless SUCCESS.

A weak model is not required to finish a coding task; an exception, a hang,
an untyped or unlisted outcome, corrupted evidence or an unsafe commit is
never a smoke pass.
"""
import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from _chaos_harness import attempt_ceiling, audit_run_records

from kriya.control.persistence import scan_run_records
from kriya.control.run_record import RunLifecycle

SUCCESS = "SUCCESS"

# Kriya's own bounds for a smoke run - tightened, never loosened, relative to
# the packaged defaults (max_tokens 16384, planner_max_tokens 8192): a wiring
# smoke needs short answers, and a degenerating small model on a CPU runner
# otherwise spends many minutes in one runaway call. The generation budget
# (Kriya's admission check before each Developer pass) leaves room inside
# the process limit for the calls that follow the last pass.
SMOKE_LLM_BOUNDS = {"max_tokens": 1024, "planner_max_tokens": 2048}
SMOKE_AUTONOMY_BOUNDS = {"generation_time_budget_seconds": 420}
SMOKE_PROCESS_LIMIT_SECONDS = 600

# Typed outcomes a weak model can legitimately produce while every safety
# invariant holds. Each names why it is a capability outcome. Everything
# else (environment/containment/commit/contract/static-analysis stops,
# scope or authority refusals, internal errors, untyped results) fails.
SMOKE_CAPABILITY_FAILURES: Dict[str, str] = {
    "planner_output_incomplete": "the Planner's plan was incomplete and repair did not complete it",
    "planner_output_schema_invalid": "the Planner's structured plan never validated",
    "quality_gates_exhausted": "generated code never passed the deterministic gates within the retry bound",
    "no_progress": "PRD-026 stopped identical ineffective retries",
    "generation_budget_exhausted": "Kriya's own generation time budget refused a pass it could not finish",
    "requirements_unresolved": "the verifier gave no accepted evidence for the goal's requirements",
}

# Paths Kriya and the test harness own inside the workspace; everything else
# is the user's repository.
_CONTROL_PATHS = (".git", ".kriya", "kriya.yaml", "skills", "memory")


def workspace_snapshot(workspace: Path) -> Dict[str, str]:
    """sha256 of every repository file outside the control paths, plus HEAD."""
    snapshot = {}
    for path in sorted(Path(workspace).rglob("*")):
        relpath = path.relative_to(workspace).as_posix()
        if path.is_file() and relpath.split("/")[0] not in _CONTROL_PATHS:
            snapshot[relpath] = hashlib.sha256(path.read_bytes()).hexdigest()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace, capture_output=True, text=True)
    snapshot[":HEAD"] = head.stdout.strip()
    return snapshot


def roles_called(state_dir: str, model: str) -> Dict[str, int]:
    """Real model calls per role on ``model``, from the run's persisted
    PRD-018 ``model.role_metrics`` events (traces.db in the state directory)."""
    path = Path(state_dir) / "traces.db"
    if not path.exists():
        return {}
    with sqlite3.connect(str(path)) as connection:
        stored = [row[0] for row in connection.execute("SELECT run_events FROM runs")]
    calls: Dict[str, int] = {}
    for events in stored:
        for event in json.loads(events or "[]"):
            if event.get("kind") != "model.role_metrics":
                continue
            for row in (event.get("details") or {}).get("rows") or ():
                if row.get("model") == model and row.get("calls"):
                    calls[row["role"]] = calls.get(row["role"], 0) + int(row["calls"])
    return calls


def smoke_outcome(payload: Mapping[str, Any]) -> Optional[str]:
    """SUCCESS, the typed failure, or None when the result carries no type."""
    if payload.get("quality_gates_passed") is True:
        return SUCCESS
    if payload.get("failure_category"):
        return str(payload["failure_category"])
    status = payload.get("status")
    return str(status) if isinstance(status, str) and status.startswith("planner_output_") else None


def assert_smoke_contract(
    workspace: Path, payload: Mapping[str, Any], *, before: Mapping[str, str], stderr: str,
    state_dir: str, model: str,
) -> Dict[str, Any]:
    """Assert the smoke contract; returns the evidence it checked."""
    tail = f"\n--- stderr (tail) ---\n{stderr[-6000:]}"
    assert "Traceback (most recent call last)" not in stderr, tail
    assert "EgressViolationError" not in stderr, tail

    # 4. RunRecords: parseable, terminal, never uncertain or unsettled.
    outcome = smoke_outcome(payload)
    succeeded = outcome == SUCCESS
    audit = audit_run_records(workspace, allow_success=succeeded, allow_committed=succeeded)
    records = scan_run_records(str(workspace)).records
    assert records, "no RunRecord: the run never reached the lifecycle" + tail
    assert all(r.lifecycle_state is not RunLifecycle.UNCERTAIN for r in records), audit.evidence()

    # 5. A typed outcome from the allowlist.
    assert outcome is not None, f"untyped outcome: {json.dumps(payload, default=str)[:2000]}" + tail
    assert outcome == SUCCESS or outcome in SMOKE_CAPABILITY_FAILURES, f"outcome {outcome!r} is not a smoke outcome" + tail

    # 1 + 2. A real model request, and the runtime identity it ran on.
    fingerprints = [digest for r in records for digest in r.model_runtime_fingerprint_ids]
    assert fingerprints, "no model runtime fingerprint recorded: no real model request" + tail
    runtimes = [json.loads((Path(state_dir) / "model_runtimes" / f"{digest}.json").read_text())
                for digest in dict.fromkeys(fingerprints)
                if (Path(state_dir) / "model_runtimes" / f"{digest}.json").exists()]
    assert any(model in json.dumps(runtime) for runtime in runtimes), f"no recorded runtime names {model!r}"
    # Role wiring: the Planner always runs on a real response; how far past it
    # a weak model gets is reported, never required.
    roles = roles_called(state_dir, model)
    assert roles.get("planner", 0) >= 1, f"no real Planner call recorded on {model!r}: {roles}" + tail

    # 3. Retries within Kriya's own ceiling.
    retries = sum(int(v) for r in records for v in (r.retry_counters or {}).values())
    assert retries <= attempt_ceiling(), f"{retries} retries exceed the ceiling"

    # 6. The real workspace: unchanged, unless verified SUCCESS changed exactly its files.
    after = workspace_snapshot(workspace)
    changed = {path for path in set(before) | set(after) if before.get(path) != after.get(path)} - {":HEAD"}
    if succeeded:
        reported = {os.path.normpath(str(f)).replace(os.sep, "/") for f in payload.get("files") or ()}
        assert changed <= reported, f"changed outside the reported files: {sorted(changed - reported)}"
    else:
        assert not changed and before[":HEAD"] == after[":HEAD"], f"workspace changed without SUCCESS: {sorted(changed)}"

    return {"outcome": outcome, "reason": SMOKE_CAPABILITY_FAILURES.get(outcome, "verified success"),
            "runtime_fingerprints": sorted(set(fingerprints)), "roles_called": roles, "retries": retries,
            "changed_files": sorted(changed), **audit.evidence()}
