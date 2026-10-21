"""TERMINAL-CLOSURE-PROPAGATION (E-1 of the consolidated addendum over 401ea52..72d9182): no original-requirement closure path
flattens an exception.

One invariant, at every point the terminal requirement gate runs on the final candidate - the verifier, the migration-gate
closure and each of the eleven closers it imports from kriya.workflow.workflow: an exception raised THERE reaches the run's
caller as itself (a typed RepositoryContentFilterRefused stays the S19 refusal; an ordinary exception keeps its own class), the
.enforce trace row is status=error / failure_category=<Type>, and nothing is committed. It is never swallowed into "no
closure", an empty result or a requirements gap.

Why it exists: test_part_c_terminal_result_truth and test_prd030_terminal_services inject only into the first closer and into
blocking_requirements, so putting an absorbing handler back at any OTHER point (the independent reviewer's mutants: the verifier,
the migration-gate closure, the suite-preservation closer, the documentation closer) survived the repository suite. A handler that
lets the typed refusal through and absorbs everything else is the shape that evades a typed-refusal-only test, so every point is
injected with BOTH kinds.

Real enforce route (the harness of test_taf3_enforce_terminal_refusal); a call counter proves the injected point ran.
"""
from unittest.mock import AsyncMock

import pytest
from _strict_doubles import strict_config
from test_taf3_enforce_terminal_refusal import _enforce, _enforce_row, _engine, _repo

from kriya.repository.git import GIT_CONTENT_FILTER_REPOSITORY_DEFINED, RepositoryContentFilterRefused
from kriya.workflow import requirement_closers
from kriya.workflow.contract_compilation import CLOSER_DOCUMENTATION_NOT_APPLICABLE, VerificationContract
from kriya.workflow.terminal_gate_service import TerminalGateService

TERMINAL_CLOSERS = (
    "close_requirements_by_mutation_scope", "close_requirements_with_acceptance_tests",
    "close_requirements_with_authority_bundle_evidence", "close_requirements_with_derived_examples",
    "close_requirements_by_api_preservation", "close_requirements_by_test_addition", "close_requirements_by_documentation",
    "close_requirements_with_named_tests", "close_requirements_by_test_immutability",
    "close_requirements_by_file_immutability", "close_requirements_by_suite_preservation",
)
POINTS = ("verifier", "migration_gate_closure", *TERMINAL_CLOSERS)

# The terminal gate calls kriya.workflow.workflow's forwarder, which delegates to the body in requirement_closers at call time.
# Each target is named literally (a dynamic name on a kriya.workflow module is what the patch-site finder cannot resolve).
_PATCH_BODY = {
    "close_requirements_by_mutation_scope": lambda mp, fn: mp.setattr(requirement_closers, "close_requirements_by_mutation_scope", fn),
    "close_requirements_with_acceptance_tests": lambda mp, fn: mp.setattr(requirement_closers, "close_requirements_with_acceptance_tests", fn),
    "close_requirements_with_authority_bundle_evidence": lambda mp, fn: mp.setattr(requirement_closers, "close_requirements_with_authority_bundle_evidence", fn),
    "close_requirements_with_derived_examples": lambda mp, fn: mp.setattr(requirement_closers, "close_requirements_with_derived_examples", fn),
    "close_requirements_by_api_preservation": lambda mp, fn: mp.setattr(requirement_closers, "close_requirements_by_api_preservation", fn),
    "close_requirements_by_test_addition": lambda mp, fn: mp.setattr(requirement_closers, "close_requirements_by_test_addition", fn),
    "close_requirements_with_named_tests": lambda mp, fn: mp.setattr(requirement_closers, "close_requirements_with_named_tests", fn),
    "close_requirements_by_test_immutability": lambda mp, fn: mp.setattr(requirement_closers, "close_requirements_by_test_immutability", fn),
    "close_requirements_by_file_immutability": lambda mp, fn: mp.setattr(requirement_closers, "close_requirements_by_file_immutability", fn),
    "close_requirements_by_suite_preservation": lambda mp, fn: mp.setattr(requirement_closers, "close_requirements_by_suite_preservation", fn),
}


def _typed_refusal() -> RepositoryContentFilterRefused:
    return RepositoryContentFilterRefused(GIT_CONTENT_FILTER_REPOSITORY_DEFINED, "/w", key="filter.kx.clean", scope="local",
                                          origin="file:.git/config")


def _ordinary_error() -> NotImplementedError:
    return NotImplementedError("the injected point broke")      # a class no handler in the repository names


KINDS = {"typed-refusal": _typed_refusal, "ordinary-error": _ordinary_error}


def _inject(monkeypatch, engine, point: str, error: BaseException) -> list:
    """Make ``point`` raise ``error``; the returned list records each time it ran."""
    calls: list = []

    def raising(*_args, **_kwargs):
        calls.append(point)
        raise error

    if point == "verifier":
        engine.spec_compliance.check = AsyncMock(side_effect=lambda *_a, **_k: raising())
    elif point == "migration_gate_closure":
        monkeypatch.setattr(TerminalGateService, "_close_by_migration_gate", staticmethod(raising))
    elif point == "close_requirements_by_documentation":
        # Re-exported into kriya.workflow.workflow by import (there is no wrapper to patch), and gated on the contract binding a
        # documentation closer: let the REAL closer pass its own gate, then fail at its first collaborator.
        real_binding = VerificationContract.binding_closers

        def binding_closers(self, closer):
            if closer == CLOSER_DOCUMENTATION_NOT_APPLICABLE:
                return [("REQ-1", object())]
            return real_binding(self, closer)
        monkeypatch.setattr(VerificationContract, "binding_closers", binding_closers)
        monkeypatch.setattr(requirement_closers, "_tracked_workspace_paths", raising)
    else:
        _PATCH_BODY[point](monkeypatch, raising)
    return calls


@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize("point", POINTS)
@pytest.mark.asyncio
async def test_an_exception_at_any_terminal_closure_point_reaches_the_caller_as_itself(tmp_path, monkeypatch, point, kind):
    repo = _repo(tmp_path, tmp_path / "FILTER_RAN")
    config = strict_config(autonomy={"spec_compliance_enabled": True})
    engine = _engine(config, bind_filter=False)
    error = KINDS[kind]()
    calls = _inject(monkeypatch, engine, point, error)
    with pytest.raises(type(error)) as raised:
        await _enforce(repo, engine)
    assert calls == [point], f"the injected point did not run exactly once: {calls}"
    assert raised.value is error
    row = _enforce_row(config)
    assert (row["status"], row["failure_category"]) == ("error", type(error).__name__), dict(row)
    assert (repo / "app.py").read_text() == "original\n"            # fail closed: nothing committed
