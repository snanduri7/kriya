"""The explicit dependencies of the kernel's extracted run stages (full modularization W4, E2).

``WorkflowEngine.run_generation_workflow`` (``kriya/workflow/workflow.py``) builds one ``StageDeps`` per use, from
its own module-level names at CALL time, and hands it to the owner modules (``run_lifecycle``, ``outcome``, and
the stage modules later W4 slices add). An owner module never imports ``kriya.workflow.workflow`` (nor
``attempt``, ``workflow_controller`` or ``kriya.cli``): every collaborator a test patches on the facade
(``kriya.workflow.workflow.logger``, ...) reaches the moved code through this object, so the patch keeps
intercepting it. Collaborators whose own imports are allowlisted for ``workflow.workflow`` only (non-public
PROVIDER/EXECUTION modules) or that sit in the kernel's all-import cycle (``kriya.workflow.state``,
``egress_authority``) are injected here rather than imported by the owner modules, so extracting a stage adds
neither an allowlist entry nor a member to that cycle.

``RunState`` is the run's ``kriya.workflow.state.GenerationState``. It is spelled ``Any`` here on purpose: an
import of ``kriya.workflow.state`` (even under ``TYPE_CHECKING``, which the architecture scan counts as a
top-level edge) would put every owner module into the 67-module all-import strongly connected component that
``state.py`` belongs to.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

RunState = Any  # kriya.workflow.state.GenerationState (see the module docstring for why it is not imported)


def _wall_clock() -> float:
    # Looked up on the time module at every call, exactly as the inline ``time.time()`` it replaces.
    return time.time()


def _monotonic() -> float:
    return time.monotonic()


@dataclass(frozen=True)
class StageDeps:
    """What an extracted run stage may use, resolved by the engine when it builds this object."""

    logger: logging.Logger                                       # kriya.workflow.workflow.logger (2 test patches)
    config: Any                                                  # the kernel's AppConfig
    new_state: Callable[..., RunState]                           # GenerationState
    egress_authority_details: Callable[[Any], Dict[str, Any]]    # PRD-012 outbound authority, for the run event
    primary_model_runtime_details: Callable[[Any], Awaitable[Dict[str, Any]]]   # PRD-013/014 runtime + qualification
    role_independence_details: Callable[[Any], Dict[str, Any]]   # PRD-018 role -> exact runtime
    trace_events: Callable[[RunState], List[Dict[str, Any]]]     # the engine's _trace_run_events (drains + metrics)
    static_analysis_fields: Callable[[Any], Dict[str, Any]]      # PRD-031A result fields
    required_verification_evidence: Callable[..., List[Dict[str, Any]]]   # workflow._build_required_verification_evidence
    deterministic_gate_evidence: Callable[[List[Dict[str, Any]], Optional[int]], List[Dict[str, Any]]]
    wall_clock: Callable[[], float] = field(default=_wall_clock)
    monotonic: Callable[[], float] = field(default=_monotonic)


__all__ = ["RunState", "StageDeps"]
