"""S6 (C-02) shared test seam: a run ends SUCCESS only when its RunRecord carries a resolved requirement truth.

A test that is about something else (milestone bookkeeping, resume, recovery routing, the human-wait clock) and needs its scripted run
to END SUCCESS must therefore resolve the run's requirements the way the product does, at one of the two places a truth comes from:

- a REAL pipeline computes its truth from ``kriya.workflow.requirements.requirement_outcomes`` (the one outcome owner). A scripted
  goal has a general requirement nothing in the script can close, so the test closes it at that owner:
  ``requirement_outcome_at_the_owner()`` (or the ``requirements_closed`` fixture of ``tests/conftest.py``);
- a FAKE engine's result carries no truth at all (a real engine's always does): ``with_resolved_truth(result)`` states the resolved
  truth a passing real pass reports.

A test that is ABOUT the open run asserts the S6 words instead (``applied_requirements_open``, ``technical_gates_passed``); it uses
neither helper. Nothing here is imported by the product.
"""
import contextlib
import sys
from typing import Any, Dict, Iterator
from unittest.mock import patch

# Every module that binds the outcome owner by name is imported BEFORE any patch: a module first imported while the owner is replaced
# would keep the stand-in after the test ends.
import kriya.workflow.authority_bundle  # noqa: F401 - imported so its by-name binding exists before a patch
import kriya.workflow.development_loop  # noqa: F401 - same
import kriya.workflow.outcome  # noqa: F401 - same
import kriya.workflow.spec_compliance  # noqa: F401 - same
import kriya.workflow.terminal_gate_service  # noqa: F401 - same
import kriya.workflow.workflow_controller  # noqa: F401 - same
from kriya.workflow import requirements as requirements_module
from kriya.workflow.requirements import RequirementOutcome


def resolved_truth() -> Dict[str, Any]:
    """The typed truth of a run whose one requirement was verified (the vocabulary of ``requirements.requirement_truth``)."""
    return {"status": "ALL_VERIFIED", "verified": 1, "accepted": 0, "total": 1, "unresolved": []}


def with_resolved_truth(result: Dict[str, Any]) -> Dict[str, Any]:
    """``result`` of a FAKE engine, carrying the resolved truth when it claims a pass and states no truth of its own. A result that
    does not claim a pass, or that already states a truth (a test's own open or contradicted one), is returned unchanged."""
    if result.get("quality_gates_passed") is True and "requirement_truth" not in result:
        return {**result, "requirement_truth": resolved_truth()}
    return result


@contextlib.contextmanager
def requirement_outcome_at_the_owner(outcome: RequirementOutcome = RequirementOutcome.CLOSED_BY_EVIDENCE) -> Iterator[None]:
    """Every requirement of the run has ``outcome`` at the one outcome owner and at every name a product module bound it to."""
    original = requirements_module.requirement_outcomes

    def outcomes(ledger, requirement_set, **_reader_inputs):   # S5: the readers pass the sealed contract (``contract=``)
        del ledger
        return {requirement.id: outcome for requirement in requirement_set.requirements}

    bound = [module for name, module in list(sys.modules.items())
             if name.startswith("kriya.") and getattr(module, "requirement_outcomes", None) is original]
    assert requirements_module in bound, "the outcome owner is already replaced: nested use is not supported"
    with contextlib.ExitStack() as stack:
        for module in bound:
            stack.enter_context(patch.object(module, "requirement_outcomes", outcomes))
        yield
