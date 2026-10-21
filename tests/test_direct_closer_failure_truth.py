"""Q5 of the consolidated addendum: the DIRECT workflow never reports a passed quality gate after a requirement closer crashed.

MEASURED at ec50268 on the production-reachable ``run_generation_workflow`` (the entry ``kriya generate`` uses, and the per-subtask
engine under enforce): an ordinary AttributeError inside a closer was caught by the closer handlers of ``_close_and_check_requirements``,
turned into ``closures = []`` plus a WARNING ("evidence unavailable"), and the run ended ``quality_gates_passed: True`` with a
structured result identical to the successful control. Verification had not completed; the authoritative result said it had.

The correction is at that first wrong decision: the handlers no longer absorb an unexpected exception. It reaches the ONE attempt-exception
classifier (recovery_coordinator.classify_attempt_exception), which owns the meaning of "an exception without an attached Failure":
Kriya's own accidental control-plane errors (``INTERNAL_FRAMEWORK_ERROR_TYPES``, now including AttributeError and NameError) are
``internal_framework_error`` - public category INTERNAL, STOP_ENVIRONMENT, no Developer retry, the original class kept in the message and
in ``Failure.diagnostics``. A typed ``RepositoryContentFilterRefused`` is not caught anywhere on the way, so it stays the typed refusal.
Ambiguous families (RuntimeError, OSError, CalledProcessError) stay with the generic classifier; no type-only refinement is made here.

Real direct path (the scripted-engine harness of test_s19e_honest_refusal); a call counter proves each injected closer ran.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_s19e_honest_refusal import (  # noqa: E402  # pylint: disable=wrong-import-position
    GOOD,
    _isolated_git,  # noqa: F401  # autouse fixture of that module, re-exported so it applies here
    _run,
    _scripted_engine,
    _workspace,
)

from kriya.repository.git import (  # noqa: E402
    GIT_CONTENT_FILTER_REPOSITORY_DEFINED,
    RepositoryContentFilterRefused,
)
from kriya.workflow import requirement_closers  # noqa: E402
from kriya.workflow.contract_compilation import CLOSER_DOCUMENTATION_NOT_APPLICABLE, VerificationContract  # noqa: E402

# The direct path calls kriya.workflow.workflow's forwarder, which delegates to the body in requirement_closers at call time. Each
# target is named literally (a dynamic name on a kriya.workflow module is what the patch-site finder cannot resolve).
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
POINTS = (*_PATCH_BODY, "close_requirements_by_documentation")


def _inject(monkeypatch, point: str, error: BaseException) -> list:
    calls: list = []

    def raising(*_args, **_kwargs):
        calls.append(point)
        raise error

    if point == "close_requirements_by_documentation":
        # Re-exported into kriya.workflow.workflow by import (no wrapper to patch) and gated on the contract binding a documentation
        # closer: let the REAL closer pass its own gate, then fail at its first collaborator.
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


def _failure_report(result) -> list:
    return [(entry.get("failure_type"), entry.get("category")) for entry in result.get("failure_report") or []]


def test_control_a_run_whose_closers_all_succeed_is_unchanged(tmp_path):
    result = _run(_scripted_engine(lambda n: GOOD)[0], _workspace(tmp_path))
    assert result["quality_gates_passed"] is True
    assert not result.get("environment_failure") and not result.get("failure_category")
    assert ("internal_framework_error", "internal") not in _failure_report(result)


@pytest.mark.parametrize("error_class, message", [(AttributeError, "'NoneType' object has no attribute 'x'"),
                                                   (NameError, "name 'undefined_variable' is not defined")])
@pytest.mark.parametrize("point", POINTS)
def test_a_closer_that_crashes_with_a_kriya_programming_error_stops_the_run_as_an_internal_error(tmp_path, monkeypatch, point,
                                                                                               error_class, message):
    error = error_class(message)
    calls = _inject(monkeypatch, point, error)
    eng, _cfg, developer_calls = _scripted_engine(lambda n: GOOD)
    result = _run(eng, _workspace(tmp_path))
    assert calls == [point], f"the injected closer did not run exactly once: {calls}"
    assert result["quality_gates_passed"] is not True                         # verification did not complete: never a pass
    assert (result["last_failure"]["type"], ) == ("internal_framework_error",)
    assert ("internal_framework_error", "internal") in _failure_report(result)  # the canonical public category: INTERNAL
    assert len(developer_calls) == 1, f"a deterministic Kriya error was retried: {len(developer_calls)} Developer calls"
    stop = result["environment_failure"]
    assert stop.startswith("INTERNAL KRIYA ERROR (not a generated-application defect): ")
    assert error_class.__name__ in stop and message in stop                   # the original class and cause survive


@pytest.mark.parametrize("point", POINTS)
def test_a_typed_repository_refusal_at_any_closer_stays_the_typed_refusal(tmp_path, monkeypatch, point):
    refusal = RepositoryContentFilterRefused(GIT_CONTENT_FILTER_REPOSITORY_DEFINED, "/w", key="filter.kx.clean", scope="local",
                                             origin="file:.git/config")
    calls = _inject(monkeypatch, point, refusal)
    eng, _cfg, developer_calls = _scripted_engine(lambda n: GOOD)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        _run(eng, _workspace(tmp_path))
    assert raised.value is refusal and calls == [point]
    assert len(developer_calls) == 1                                            # ended as itself, no retry
