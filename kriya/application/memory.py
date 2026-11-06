"""ProjectMemoryService (product/demo-72h): VERIFIED PROJECT MEMORY as a use case.

The domain is ``kriya/control/project_memory.py`` (STATE: a projection of authoritative run evidence onto the repository as it is now; the rules, the poisoning
defences and the store live there). This service is what the clients call - the CLI before and after a run, the KUP adapter for the cockpit - so no adapter reaches
STATE directly. Three use cases, each taking the ``RequestContext`` first and plain data after it:

* ``view``      the memory of the workspace's repository NOW (read-only): every fact with its status, reason and provenance, the counts, the recent runs;
* ``reference`` the CURRENT facts as reference text for ``reference_context`` plus the ids they carry (fenced as untrusted by the consumer, never an instruction);
* ``record``    OBSERVE + VERIFY + STORE for one finished run; only a COMPLETED run is observed, and a run that is not a verified success learns nothing.

Observational by contract: nothing here blocks, alters or authorizes a run.
"""
from __future__ import annotations

from typing import Any, Dict

from kriya.application.services import INVALID_REQUEST, ApplicationRefusal
from kriya.context.model import RequestContext
from kriya.control import project_memory

NOT_A_COMPLETED_RUN = "not_a_completed_run"
_NOT_RUN_OUTCOMES = ("knowledge_gap", "needs_review")


class ProjectMemoryService:
    def view(self, ctx: RequestContext, workspace: str) -> Dict[str, Any]:
        return project_memory.project(workspace).to_dict()

    def reference(self, ctx: RequestContext, workspace: str) -> Dict[str, Any]:
        text, ids = project_memory.reference_text(project_memory.project(workspace))
        return {"text": text, "fact_ids": list(ids)}

    def record(self, ctx: RequestContext, workspace: str, run_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
        """``request``: ``{"result": <the run's result dict>, "reused": [<fact ids carried into the run>]}``. Returns ``{"recorded": False, "reason": ...}`` for an answer
        that is not a finished run (a knowledge-gap or needs-review answer, a result without the gate verdict)."""
        if not isinstance(request, dict) or not isinstance(request.get("result"), dict):
            raise ApplicationRefusal(INVALID_REQUEST, "request.result must be the run's result", reason="invalid_request")
        result = request["result"]
        if not isinstance(run_id, str) or not run_id or "quality_gates_passed" not in result or result.get("status") in _NOT_RUN_OUTCOMES:
            return {"recorded": False, "reason": NOT_A_COMPLETED_RUN}
        entry = project_memory.observe_run(
            workspace, run_id=run_id, success=project_memory.result_is_verified_success(result), passed_gates=project_memory.passed_gate_types(result),
            written_files=[str(f) for f in result.get("files") or []], reused=[str(i) for i in request.get("reused") or []])
        return {"recorded": True, "reason": None, "entry": entry.to_dict()}
