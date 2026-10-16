"""RunService (slice B3): what a run in a folder would be, the launch read back from durable records, and the composition of a run.

Extracted unchanged from ``kriya/kup/execution.py`` (GUI M6: ``execution.preflight`` and ``launch.status``) and from the ``kriya generate``
composition in ``kriya/cli.py`` (``_generate_impl``: the engine built from the CLI's own composition names and bound to the operator's
artifacts; ``_mark_run_in_progress``).

``preflight`` answers, before Start Run, what a run in this folder would be: which configuration Kriya would load, where it would record
its state, which models it would route to, what security authority applies, and whether the cockpit could observe the result. It is
computed by the SAME resolution a terminal ``kriya generate`` performs when run from that folder (the process working directory decides
configuration discovery - kriya/config/config.py): the caller's ``load_in`` temporarily enters the folder. Nothing is cached: every call
resolves again, so an approval granted or revoked a moment ago is seen. The host displays the answer and decides nothing from it; the
launched process re-runs the same function and refuses on any blocker, so the preflight cannot be forged by the host.

``status`` reads one launch back from durable records only: the launch record, the RunRecord, the attempt-evidence store and the
workspace run lock. It never infers an outcome from a process exit; a launch that is not ENDED and whose lock is free is reported as
``interrupted`` (the run did not seal), which is neither success nor a failure Kriya recorded.

``start`` builds the engine of one run from ``RunServiceDeps``: the composition names the CLI owns and tests patch (``kriya.cli.LLMClient``,
``Kernel``, ``WorkflowEngine``) are passed in, looked up by the CLI at call time, so a patch of ``kriya.cli.<name>`` is what runs. The
binding of the operator artifacts happens before it, in the CLI (each has its own refusal text and exit); ``start`` attaches them.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from kriya.application.evidence import store_summary
from kriya.application.services import EVIDENCE_STORE_UNREADABLE, LAUNCH_NOT_FOUND, ApplicationRefusal
from kriya.context.model import RequestContext
from kriya.context.workspace_identity import workspace_identity
from kriya.control.launch import PHASE_ENDED, PHASE_STARTING
from kriya.control.persistence import UnreadableRunRecordError, load_launch_record, load_run_record
from kriya.control.recovery import STATUS_CLEAN, STATUS_RUN_ACTIVE, assess_recovery
from kriya.control.run_ownership import probe_run_lock
from kriya.core.state_paths import StateDirectoryError, resolve_state_directory, trace_db_path

logger = logging.getLogger(__name__)

LoadIn = Callable[[str], Tuple[Any, Any, Dict[str, Any], Optional[Dict[str, str]]]]  # workspace -> (state, cfg, authority, failure)
ToolVisibility = Callable[[], List[Dict[str, Any]]]

KNOWLEDGE_POLICY = "strict"
INTERACTION_REVIEW = ("A change Kriya proposes is held for your review in this window and applied only if you approve it. Any other step that needs a human "
                      "decision (a knowledge-gap acknowledgement, a skill choice, a command approval) stops the run instead of being approved for you.")
INTERACTION_NO_REVIEW = ("This folder's configuration sets the autonomy mode to {mode!r}: a routine change is not held for review by this configuration (a change that "
                         "touches a sensitive path or exceeds the risk threshold still is). Any other step that needs a human decision (a knowledge-gap "
                         "acknowledgement, a skill choice, a command approval) stops the run instead of being approved for you.")
INTERACTION_UNKNOWN = ("The configuration could not be resolved, so how changes are approved is unknown. Any step that needs a human decision stops the run "
                       "instead of being approved for you.")


def _interaction(cfg: Any) -> Dict[str, Any]:
    """How a change is approved in a run here, from the RESOLVED configuration (shown, never changed): the autonomy mode is the operator's, the app has no selector."""
    if cfg is None:
        return {"approvals": "unknown", "autonomy_mode": None, "knowledge_policy": KNOWLEDGE_POLICY, "note": INTERACTION_UNKNOWN}
    mode = str(cfg.autonomy.mode)
    if mode == "human-in-the-loop":
        return {"approvals": "change_review", "autonomy_mode": mode, "knowledge_policy": KNOWLEDGE_POLICY, "note": INTERACTION_REVIEW}
    return {"approvals": "configured_mode", "autonomy_mode": mode, "knowledge_policy": KNOWLEDGE_POLICY, "note": INTERACTION_NO_REVIEW.format(mode=mode)}
DISCOVERY = ("Configuration is discovered from the process working directory, which is this folder - the same as running `kriya generate` "
             "here. A kriya.yaml in the folder (else the Kriya installation) is read; fields only the operator may set need a durable approval.")

TOOLCHAIN_NOTE = ("Visibility under this process's own environment (a fixed PATH; no login shell). Which tools a goal needs is only known while the workflow runs; "
                  "a missing tool then ends the run with Kriya's own typed failure.")

def _blocker(code: str, message: str) -> Dict[str, str]:
    return {"code": code, "message": message}


def _model_identity(cfg) -> Dict[str, Any]:
    llm = cfg.llm
    roles = {}
    for role in ("planner", "architect", "reviewer", "run_verifier", "skill_gap", "spec_compliance"):
        override = getattr(getattr(cfg.agent_llms, role, None), "llm", None)
        if override is not None:
            roles[role] = override.model
    return {"provider": llm.provider, "model": llm.model, "endpoint": llm.base_url, "context_window": llm.context_window,
            "fallback_chain": [entry.model for entry in cfg.llm_chain], "role_overrides": roles}


def build_preflight(workspace: str, cockpit_state_directory: Optional[str], *, load_in: LoadIn, tool_visibility: ToolVisibility) -> Dict[str, Any]:
    """The preflight document for a canonical ``workspace``. ``cockpit_state_directory`` is where the reading side looks (None when the caller
    is the launched process itself, which cannot know). ``load_in`` resolves the folder's configuration with the folder as the working
    directory and ``tool_visibility`` reports the toolchains (both mechanisms of the caller: a cwd change and process probes)."""
    blockers: List[Dict[str, str]] = []
    state, cfg, authority, failure = load_in(workspace)
    if failure is not None:
        blockers.append(failure)
    assessment = assess_recovery(workspace)
    holder = probe_run_lock(workspace)
    if holder is not None:
        blockers.append(_blocker("RUN_ACTIVE", f"another Kriya run already owns this folder ({holder})"))
    elif assessment.status != STATUS_CLEAN:
        blockers.append(_blocker("RECOVERY_REQUIRED", f"the folder's last run left it in state {assessment.status}; recover it (kriya runs recover) before starting another"))
    tools = tool_visibility()
    git_entry = next((t for t in tools if t["tool"] == "git"), None)
    if git_entry is not None and not git_entry["available"]:
        # traced from a real run: every run creates an isolated generation sandbox with git, so a git that does not run fails the run at its first step
        why = git_entry.get("reason") or "is not on this process's PATH"
        later = f"; a working copy exists later on PATH ({git_entry['alternatives'][0]}) but a run executes the first" if git_entry.get("alternatives") else ""
        blockers.append(_blocker("TOOL_UNAVAILABLE", f"git is required for every run and {why}{later}"))
    configuration: Dict[str, Any] = {
        "status": "loaded" if cfg is not None else ("refused" if authority["status"] == "refused" else "failed"),
        "source": (state.config_source or "packaged_default") if state is not None else "unreadable",
        "file": state.config_path if state is not None else None, "directory": workspace, "discovery": DISCOVERY,
        "authority": authority, "fingerprint": None,
    }
    runtime_state: Dict[str, Any] = {"directory": None, "source": None, "cockpit_directory": cockpit_state_directory, "observable_by_cockpit": None}
    model: Optional[Dict[str, Any]] = None
    if cfg is not None:
        from kriya.control.checkpoint import compute_config_fingerprint

        configuration["fingerprint"] = compute_config_fingerprint(cfg.model_dump())
        model = _model_identity(cfg)
        try:
            directory, source = resolve_state_directory(cfg)
        except StateDirectoryError as error:
            blockers.append(_blocker("STATE_DIRECTORY_INVALID", str(error)))
        else:
            runtime_state.update(directory=directory, source=source)
            if cockpit_state_directory is not None:
                same = os.path.realpath(directory) == os.path.realpath(cockpit_state_directory)
                runtime_state["observable_by_cockpit"] = same
                if not same:
                    blockers.append(_blocker(
                        "STATE_DIRECTORY_NOT_OBSERVABLE",
                        f"a run here would record into {directory}, but this cockpit reads {cockpit_state_directory}; its progress and result would not appear"))
    return {
        "ready": not blockers, "blockers": blockers,
        "workspace": {"path": workspace, "identity": workspace_identity(workspace), "name": os.path.basename(workspace.rstrip(os.sep)) or workspace},
        "configuration": configuration, "state": runtime_state, "model": model,
        "recovery": {"status": assessment.status, "run_active": assessment.status == STATUS_RUN_ACTIVE},
        "interaction": _interaction(cfg),
        "toolchains": {"tools": tools, "note": TOOLCHAIN_NOTE},
    }


def _launch_view(record: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("launch_id", "workspace", "requested_at", "goal", "options", "phase", "run", "ended", "history", "approval_required", "declined_decisions", "pending_approval", "approval_history")
    view = {key: record.get(key) for key in keys}
    process = record.get("process")
    view["process"] = {"pid": process.get("pid")} if isinstance(process, dict) else None
    return view


def _run_record_view(workspace: str, run_id: str) -> Dict[str, Any]:
    try:
        record = load_run_record(workspace, run_id)
    except UnreadableRunRecordError as error:
        return {"found": False, "unreadable": error.reason}
    if record is None:
        return {"found": False, "unreadable": None}
    return {"found": True, "unreadable": None, "lifecycle_state": record.lifecycle_state.value, "terminal_status": record.terminal_status,
            "commit_result": record.commit_result, "revision": record.revision}


def _liveness(phase: str, lock_held: bool) -> str:
    """ENDED is the launching process's own seal. Otherwise the kernel lock - the workspace's only ownership authority - says whether a run
    is live; a RUNNING launch whose lock is free did not seal."""
    if phase == PHASE_ENDED:
        return "ended"
    if lock_held:
        return "live"
    return "starting" if phase == PHASE_STARTING else "interrupted"




def launch_status(cfg, workspace: str, launch_id: str) -> Dict[str, Any]:
    try:
        record = load_launch_record(workspace, str(launch_id))
    except UnreadableRunRecordError as error:
        raise ApplicationRefusal(EVIDENCE_STORE_UNREADABLE, f"the launch record cannot be trusted: {error.reason}", path_kind="launch") from error
    if record is None:
        raise ApplicationRefusal(LAUNCH_NOT_FOUND, f"no launch {launch_id} is recorded for this folder", reason="not_recorded")
    phase = str(record.get("phase"))
    holder = probe_run_lock(workspace)
    lock_held = holder is not None and f"run_id={launch_id}," in holder  # the lock's own diagnostic text; ownership itself is the kernel lock
    run = record.get("run")
    run_id = run.get("run_id") if isinstance(run, dict) else None
    return {
        "launch": _launch_view(record),
        "run_record": _run_record_view(workspace, str(run_id)) if run_id else {"found": False, "unreadable": None},
        "evidence": store_summary(cfg, str(run_id)) if run_id else {"state": "absent", "line_count": None, "sealed": False},
        "workspace_lock": {"held": holder is not None, "held_by_this_launch": lock_held},
        "liveness": _liveness(phase, lock_held),
        "liveness_note": "DERIVED from the launch phase and the workspace run lock; a process exit alone is never the outcome",
    }

@dataclass(frozen=True)
class RunServiceDeps:
    """The composition names of ``kriya generate`` (kriya/cli.py keeps them: they are the documented patch points)."""

    LLMClient: Callable[..., Any]  # noqa: N815 - the CLI's own class name, passed through unchanged
    Kernel: Callable[..., Any]  # noqa: N815 - idem
    WorkflowEngine: Callable[..., Any]  # noqa: N815 - idem


@dataclass(frozen=True)
class RunArtifacts:
    """The operator artifacts bound before any model call (each already loaded and verified by its own loader), or None."""

    acceptance: Any = None
    acceptance_approval: Any = None
    requirement_contract: Any = None
    requirement_dispositions: Any = None
    verification_authority_bundle: Any = None


@dataclass(frozen=True)
class RunHandle:
    """The composed run: the LLM client, the kernel and the engine, in the construction order of ``kriya generate``."""

    llm: Any
    kernel: Any
    engine: Any


class RunService:
    def preflight(self, ctx: RequestContext, cfg, workspace: str, *, load_in: LoadIn, tool_visibility: ToolVisibility) -> Dict[str, Any]:
        """``execution.preflight`` for the admitted folder ``workspace``, observed from the cockpit's own state directory."""
        from kriya.application.services import CONFIG_LOAD_FAILED

        try:
            cockpit_state, _source = resolve_state_directory(cfg)
        except StateDirectoryError as error:
            raise ApplicationRefusal(CONFIG_LOAD_FAILED, str(error)) from error
        return build_preflight(workspace, cockpit_state, load_in=load_in, tool_visibility=tool_visibility)

    def build_preflight(self, ctx: RequestContext, workspace: str, cockpit_state_directory: Optional[str], *, load_in: LoadIn,
                        tool_visibility: ToolVisibility) -> Dict[str, Any]:
        """The preflight document itself (the launched process passes ``cockpit_state_directory=None``: it cannot know where a reader looks)."""
        return build_preflight(workspace, cockpit_state_directory, load_in=load_in, tool_visibility=tool_visibility)

    def status(self, ctx: RequestContext, cfg, workspace: str, launch_id: str) -> Dict[str, Any]:
        """``launch.status`` of launch ``launch_id`` (validated by the caller) in the admitted folder ``workspace``."""
        return launch_status(cfg, workspace, launch_id)

    def start(self, ctx: RequestContext, cfg, artifacts: RunArtifacts, deps: RunServiceDeps) -> RunHandle:
        """The engine of one ``kriya generate`` run, composed from the CLI's names and bound to the operator artifacts."""
        llm = deps.LLMClient(cfg)
        kernel = deps.Kernel(config=cfg)
        we = deps.WorkflowEngine(kernel, llm)
        we.acceptance = artifacts.acceptance
        we.acceptance_approval = artifacts.acceptance_approval
        we.requirement_contract = artifacts.requirement_contract
        we.requirement_dispositions = artifacts.requirement_dispositions
        we.verification_authority_bundle = artifacts.verification_authority_bundle
        we.verification_authorities = (artifacts.verification_authority_bundle.authority(),) if artifacts.verification_authority_bundle is not None else ()
        return RunHandle(llm=llm, kernel=kernel, engine=we)

    def mark_in_progress(self, ctx: RequestContext, cfg, run_id: Optional[str], goal: str) -> None:
        """Overwrite a transient ``knowledge_gap`` trace row with an honest ``in_progress`` marker the instant a knowledge-gap retry starts
        executing the real generation run (the full rationale is on ``kriya.cli._mark_run_in_progress``). Best effort like every other
        trace write: a failure here never blocks the retry."""
        if not run_id:
            return
        try:
            from kriya.control.trace import TraceLogger
            TraceLogger(trace_db_path(cfg)).log_run(
                run_id=run_id,
                goal=goal,
                duration_sec=0.0,
                attempts=0,
                status="in_progress",
                files_modified=[],
            )
        except Exception as trace_ex:
            logger.warning(f"Failed to mark run '{run_id}' in_progress: {trace_ex}")


__all__ = ["DISCOVERY", "INTERACTION_NO_REVIEW", "INTERACTION_REVIEW", "INTERACTION_UNKNOWN", "KNOWLEDGE_POLICY", "RunArtifacts", "RunHandle",
           "RunService", "RunServiceDeps", "TOOLCHAIN_NOTE", "build_preflight"]
