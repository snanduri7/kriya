"""SUITE-PRESERVATION-ATTRIBUTION-TYPED-REFUSAL-001: a typed S19 content-filter refusal raised while the terminal
suite-preservation closure attributes a failing candidate suite reaches the closure's caller as itself - never as the
REGRESSION_UNATTRIBUTED prose "baseline attribution raised RepositoryContentFilterRefused: ...".

Found by the independent TEST-A addendum of 401ea52 (TRACED there, MEASURED here on the real closer): the lazy baseline
capture computes the untouched workspace's revision through ``compute_workspace_content_hash`` - which inspects the
repository's content filters and raises the typed refusal - outside any handler of ``_capture_single_baseline``; the broad
``except Exception`` of ``close_suite_preservation_requirements`` (requirements.py) turned it into a closure entry whose
reason names the refusal only in text. The attribution step has the same flattening (requirement_closers.py
``attribute_suite``). Every ordinary failure of attribution stays what the owner designed: the requirement stays open,
REGRESSION_UNATTRIBUTED, fail closed.

Real wrapper, real git workspace, real pytest gate, nothing stubbed (the scaffold of
test_repair_003_p1_2_suite_preservation_attribution). The refusal's cause is real: a repository-local filter that becomes
ACTIVE in the untouched baseline workspace only (the candidate copy is clean, so the closure reaches the attribution).
"""
import subprocess

import pytest
from test_repair_003_p1_2_suite_preservation_attribution import (
    CALC_ADD_WORSE,
    CALC_TIDIED,
    _candidate,
    _close,
    _suite_entries,
    _workspace,
)

from kriya.repository.git import GIT_CONTENT_FILTER_REPOSITORY_DEFINED, RepositoryContentFilterRefused
from kriya.workflow import workflow as wf

UNATTRIBUTED = "REGRESSION_UNATTRIBUTED"


def _activate_repository_filter_in(workspace) -> None:
    subprocess.run(["git", "config", "filter.kx.clean", "cat"], cwd=workspace, check=True, capture_output=True)
    (workspace / ".gitattributes").write_text("*.py filter=kx\n")


def _during_the_attribution(monkeypatch, workspace, effect) -> list:
    """The baseline capture (workflow's own revision computation) is the real one; ``effect`` runs before every revision
    computation of the ATTRIBUTION step (suite_attribution's own binding), so the failure arises after a baseline was
    captured. Returns the list of attribution-step computations."""
    from kriya.workflow import suite_attribution

    real = suite_attribution.compute_workspace_content_hash
    calls = []

    def hashing(path):
        calls.append(path)
        effect(workspace)
        return real(path)
    monkeypatch.setattr(suite_attribution, "compute_workspace_content_hash", hashing)
    return calls


def test_control_a_clean_workspace_still_closes_by_the_stable_pre_existing_failure(tmp_path):
    """The scenario is real: without an active filter the same closure attributes and closes."""
    workspace = _workspace(tmp_path)
    candidate = _candidate(tmp_path, workspace, CALC_TIDIED)
    entries, *_ = _close(workspace, candidate)
    [entry] = _suite_entries(entries)
    assert entry["closed"] is True, entry


def test_a_refusal_at_the_lazy_baseline_capture_reaches_the_caller_as_itself(tmp_path):
    workspace = _workspace(tmp_path)
    candidate = _candidate(tmp_path, workspace, CALC_TIDIED)
    _activate_repository_filter_in(workspace)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        _close(workspace, candidate)
    assert raised.value.reason_code == GIT_CONTENT_FILTER_REPOSITORY_DEFINED and raised.value.driver == "kx"


def test_a_refusal_during_the_attribution_itself_reaches_the_caller_as_itself(tmp_path, monkeypatch):
    """CALC_ADD_WORSE changes the pre-existing failure's message, so the attribution MEASURES that test's stability on the
    untouched workspace - the step that computes the workspace revision again (pytest_stability._measure_context)."""
    workspace = _workspace(tmp_path)
    candidate = _candidate(tmp_path, workspace, CALC_ADD_WORSE)
    calls = _during_the_attribution(monkeypatch, workspace, _activate_repository_filter_in)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        _close(workspace, candidate)
    assert calls, "the attribution step never computed a revision: the refusal is not the attribution's"
    assert raised.value.reason_code == GIT_CONTENT_FILTER_REPOSITORY_DEFINED


def _boom(_workspace):
    raise RuntimeError("revision computation broke")


def test_an_ordinary_failure_at_the_lazy_capture_stays_open_unattributed(tmp_path, monkeypatch):
    """Negative control: only the typed refusal passes through; the owner's fail-closed handling of every other failure is
    exactly as before (the requirement stays open, the raw suite result is kept, the cause is named in the reason)."""
    monkeypatch.setattr(wf, "compute_workspace_content_hash", _boom)
    workspace = _workspace(tmp_path)
    candidate = _candidate(tmp_path, workspace, CALC_TIDIED)
    entries, *_ = _close(workspace, candidate)
    [entry] = _suite_entries(entries)
    assert entry["closed"] is False and entry["success"] is False, entry
    assert entry["reason"].startswith(UNATTRIBUTED) and "baseline attribution raised RuntimeError" in entry["reason"], entry["reason"]
    assert entry["regression_attribution"]["available"] is False


def test_an_ordinary_failure_during_the_attribution_stays_open_unattributed(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    candidate = _candidate(tmp_path, workspace, CALC_ADD_WORSE)
    calls = _during_the_attribution(monkeypatch, workspace, _boom)
    entries, *_ = _close(workspace, candidate)
    assert calls, "the attribution step never computed a revision"
    [entry] = _suite_entries(entries)
    assert entry["closed"] is False and entry["success"] is False, entry
    # the attribution step's own wording (requirement_closers.attribute_suite), not the closure's outer one
    assert entry["reason"].startswith(UNATTRIBUTED), entry["reason"]
    assert "the PRE/POST attribution could not be made (RuntimeError: revision computation broke)" in entry["reason"], entry["reason"]
    assert entry["regression_attribution"]["available"] is False
