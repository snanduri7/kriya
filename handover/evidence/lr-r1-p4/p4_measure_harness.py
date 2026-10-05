"""LR-R1-P4 measurement harness (evidence only; copied to handover/evidence/lr-r1-p4/ and removed from tests/).
Runs the P4 reproducer's scenario once and writes the measured facts to $P4_MEASURE_OUT."""
import json
import os
import time
from unittest.mock import patch

import test_lr_r1_p4_verification_only_retry_reproducer as repro

from kriya.core.attempt_evidence import reader
from kriya.core.attempt_evidence.explain import explain_run
from kriya.core.state_paths import ENV_STATE_DIR
from kriya.workflow.retry_strategy import compute_effective_workspace_hash


def test_measure(tmp_path):
    from kriya.workflow.verification_coordinator import VerificationCoordinator

    verifications = []
    started = time.monotonic()
    real_verify = VerificationCoordinator.verify

    async def verify(self, request):
        record = {"t": round(time.monotonic() - started, 3), "attempt": request.attempt_number,
                  "required_verification": request.required_verification}
        try:   # observation only: never alters the verification
            root = self._validator.workspace_path
            record["root"] = root
            record["workspace_hash"] = compute_effective_workspace_hash(root, set(request.known_files) | {repro.SERVICE})
            path = os.path.join(root, repro.SERVICE)
            record["service_sha256"] = (__import__("hashlib").sha256(open(path, "rb").read()).hexdigest()
                                        if os.path.isfile(path) else None)
        except Exception as error:  # noqa: BLE001 - measurement must not perturb the run
            record["observation_error"] = repr(error)
        verifications.append(record)
        try:
            return await real_verify(self, request)
        finally:
            record["seconds"] = round(time.monotonic() - started - record["t"], 3)

    with patch.object(VerificationCoordinator, "verify", verify):
        workspace, events, result, model_calls, test_gate_runs = repro._enforce(tmp_path)
    elapsed = time.monotonic() - started
    out_runs = test_gate_runs
    state = os.environ[ENV_STATE_DIR]
    [run_id] = reader.list_runs(state)
    explained = explain_run(state, run_id)
    s2 = [a for a in explained["attempts"] if a["unit_id"] == "s2"]
    out = {
        "wall_seconds": round(elapsed, 3),
        "model_calls": model_calls, "model_calls_in_s2": [c for c in model_calls if c[0] == "s2"],
        "verifications_verification_only": verifications, "test_gate_runs": out_runs,
        "attempt_failed": [(e.attempt, e.details.get("failure_type")) for e in events if e.kind == "attempt.failed"],
        "progress": [(e.attempt, e.details["classification"], e.details["changed_dimensions"])
                     for e in events if e.kind == "retry.progress_vector"],
        "no_progress_terminal": [e.details for e in events if e.kind == "retry.no_progress_terminal"],
        "subtask_status": {r.subtask_id: r.status.value for r in result.subtask_results},
        "legacy_status": result.legacy_result.get("status"),
        "evidence_verify": explained["verification"],
        "s2_attempts": [{"attempt": a["attempt"], "Q1": a["answers"]["Q1"].get("status"),
                         "Q4": a["answers"]["Q4"], "Q6": [{k: i.get(k) for k in ("action", "retry", "stop_loop",
                                                                               "no_progress_reason", "progress_classification")}
                                                        for i in a["answers"]["Q6"].get("items", [])],
                         "Q7": a["answers"]["Q7"].get("status")} for a in s2],
        "Q9": {k: explained["Q9"].get(k) for k in ("terminal_cause", "last_recovery_decision")},
    }
    with open(os.environ["P4_MEASURE_OUT"], "w") as handle:
        json.dump(out, handle, indent=1, default=str)
