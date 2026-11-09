"""RUNTIME-CONTAINED-ENVIRONMENT-INTERMITTENT-DOCKER-FAILURES-001 (P1 incident, 2026-11-08): a gate view is never given a host path name an earlier view had.

MEASURED. Under Docker Desktop file sharing, a host directory removed and recreated under the SAME name and bound within moments is sometimes stale in the VM: the daemon rejects the `.kriya`
anchor ("bind source path does not exist ... gate-views/view-X/.kriya") or the nested venv mount arrives empty (`.kriya/venv/bin/python: not found`; pip's own vendored modules missing). A
Kriya-free probe with the failing container's exact mount shape, the same load, interleaved: reused name 117 of 120 containers failed, never-used names 0 of 120; reused name on a quiet host 0 of
25. The decision that made it reachable was ``gate_view._claim_root``: a STABLE name per tree, deleted and recreated for every gate. The deterministic control here is the decision itself - the
host path handed to a container is never one a view had before - plus the ownership rules that make unique names safe (leftovers swept by owner liveness, never another live process's view) and
the removal failure the stable name used to hide being logged as ``GateView.close`` always promised. The Docker-level factorial is external evidence (evidence/p1-incident-snapshot-1/probes).
"""
from __future__ import annotations

import logging
import os
import re

import pytest
from test_oi142_gate_view import _build, _repo, _views

from kriya.tools import gate_view

UNIQUE = re.compile(r"^view-([0-9a-f]{16})([0-9a-f]{8})([0-9a-f]{6})$")


def _names(views):
    return [os.path.basename(view.root) for view in views]


def test_a_contained_view_never_reuses_a_host_path_name_even_after_the_previous_one_was_removed(tmp_path):
    root = _repo(tmp_path)
    seen = []
    for _ in range(6):                                   # the sequence an attempt runs: build, gate, close, the next gate on the same tree
        view = _build(root)
        seen.append(view.root)
        view.close()
    assert len(set(seen)) == len(seen)                   # not one path was handed out twice, although each was gone before the next was built
    assert all(UNIQUE.match(os.path.basename(path)) for path in seen)
    assert not any(os.path.lexists(path) for path in seen)


def test_the_unique_name_still_identifies_the_tree_and_the_owner(tmp_path):
    one, two = _repo(tmp_path, name="one"), _repo(tmp_path, name="two")
    a, b, c = _build(one), _build(one), _build(two)
    try:
        key = lambda view: UNIQUE.match(os.path.basename(view.root)).groups()                      # noqa: E731
        assert key(a)[0] == key(b)[0] != key(c)[0]       # same tree -> same prefix; another tree -> another prefix
        assert int(key(a)[1], 16) == os.getpid() and key(a)[2] != key(b)[2]
    finally:
        for view in (a, b, c):
            view.close()


def test_the_failure_identity_ignores_the_view_directory_name(tmp_path):
    """The old stable name existed so the retry loop could compare failures between attempts (failure text names the directory). A never-reused name keeps that comparison honest only because failure
    identity strips it as per-run noise: the SAME failure in two different views is one failure; a different failure is still different."""
    from kriya.workflow.failure_grounding import build_failure_signature

    def output(view_name, detail="assert add(1, 2) == 3"):
        return (f"rootdir: /state/gate-views/{view_name}\ncollected 2 items\ntest_calc.py:5: in test_add\n    {detail}\n"
                f"E   ValueError: add unavailable\n  File \"/state/gate-views/{view_name}/calc.py\", line 4\n")

    first = build_failure_signature("regression_test", output("view-" + "a" * 30))
    second = build_failure_signature("regression_test", output("view-" + "b" * 30))
    legacy = build_failure_signature("regression_test", output("view-" + "c" * 16 + "-1"))
    assert first == second == legacy
    assert build_failure_signature("regression_test", output("view-" + "a" * 30).replace("add unavailable", "sub unavailable")) != first


def _dead_pid() -> int:
    candidate = 4_000_000
    while True:
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except PermissionError:
            pass
        candidate += 1


def _leftover(owner_pid: int, tree_key: str = "a" * 16, counter: int = 1) -> str:
    path = os.path.join(_views(), f"view-{tree_key}{owner_pid:08x}{counter:06x}")
    os.makedirs(os.path.join(path, ".kriya", "venv"))
    return path


def test_the_next_claim_sweeps_leftovers_of_dead_owners_and_of_this_process_but_never_a_live_other_process(tmp_path):
    root = _repo(tmp_path)
    base = _views()
    os.makedirs(base, exist_ok=True)
    dead = _leftover(_dead_pid(), counter=1)                       # a crashed process
    mine = _leftover(os.getpid(), counter=2)                       # this process's own leftover (a close() that could not finish)
    other_live = _leftover(os.getppid(), counter=3)                # a different, LIVE process (the parent): its view may be open - never touched
    view = _build(root)
    try:
        assert not os.path.lexists(dead) and not os.path.lexists(mine)
        assert os.path.isdir(other_live)
        assert os.path.isdir(view.root)                            # the view just claimed is open, so it is not swept either
    finally:
        view.close()
        gate_view._remove_tree(other_live)                         # noqa: SLF001 - test cleanup


def test_a_view_that_is_open_in_this_process_is_never_swept_by_another_claim(tmp_path):
    root = _repo(tmp_path)
    open_view = _build(root)
    try:
        second = _build(root)                         # sweeps; must leave the open one alone
        try:
            assert os.path.isdir(open_view.root) and open_view.root != second.root
        finally:
            second.close()
    finally:
        open_view.close()


def test_a_removal_the_file_sharing_blocks_is_logged_and_the_leftover_is_swept_by_the_next_claim(tmp_path, monkeypatch, caplog):
    """MEASURED: right after a container that mounted the view exits, the host's rmdir of the mount-point directories can fail for a moment (EACCES) and ``ignore_errors`` hid it."""
    root = _repo(tmp_path)
    view = _build(root)
    path = view.root
    with monkeypatch.context() as patched:
        patched.setattr(gate_view, "_remove_tree", lambda _path: None)          # the removal that did not take effect
        with caplog.at_level(logging.WARNING, logger=gate_view.logger.name):
            view.close()
    assert os.path.lexists(path)
    assert any("could not be fully removed" in record.getMessage() and path in record.getMessage() for record in caplog.records)
    nxt = _build(root)
    try:
        assert not os.path.lexists(path) and nxt.root != path       # swept, and never reused
    finally:
        nxt.close()


def test_a_view_that_is_fully_removed_logs_nothing(tmp_path, caplog):
    root = _repo(tmp_path)
    view = _build(root)
    with caplog.at_level(logging.WARNING, logger=gate_view.logger.name):
        view.close()
    assert not [r for r in caplog.records if "could not be" in r.getMessage()]


@pytest.mark.parametrize("name", ["view-" + "a" * 16, "view-" + "a" * 16 + "-1", "view-notahexname", "other-" + "a" * 30])
def test_the_sweep_touches_only_names_of_the_current_form(tmp_path, name):
    root = _repo(tmp_path)
    base = _views()
    path = os.path.join(base, name)
    os.makedirs(path, exist_ok=True)
    view = _build(root)
    try:
        assert os.path.isdir(path)                                  # a directory of another form (an older Kriya's stable name, a foreign one) is not this sweep's to remove
    finally:
        view.close()
        gate_view._remove_tree(path)                                # noqa: SLF001


# ----------------------------------------------------------------------------------------------- the owner contract for a view's end (GATE-VIEW-REMOVAL-NOT-IMMEDIATE-ON-DOCKER-DESKTOP-001)
# logical ownership released; cleanup attempted; a failed removal logged; the identity never reused; closed/dead residue later swept; a live other process's view never removed. No wait, no retry.


def _blocked(monkeypatch):
    """The host file share not letting go yet: a removal that does not take effect (the measured EACCES window), with no timing involved."""
    monkeypatch.setattr(gate_view, "_remove_tree", lambda _path: None)


def test_a_view_whose_removal_failed_has_still_released_its_ownership_and_is_sweep_eligible(tmp_path, monkeypatch):
    from _gate_view_contract import assert_view_released

    root = _repo(tmp_path)
    view = _build(root)
    with monkeypatch.context() as patched:
        _blocked(patched)
        view.close()
    assert os.path.lexists(view.root)                      # the physical residue is there...
    assert_view_released(view.root)                        # ...but it is closed (not owned) and of the form the next claim sweeps
    nxt = _build(root)
    try:
        assert not os.path.lexists(view.root) and nxt.root != view.root
    finally:
        nxt.close()


def test_failed_removals_do_not_accumulate_past_the_next_claim_and_no_name_is_ever_handed_out_twice(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    base = _views()
    handed_out = []
    with monkeypatch.context() as patched:
        _blocked(patched)
        for _ in range(5):                                 # five gates in a row, every close blocked: the residue grows only while the share is blocked
            view = _build(root)
            handed_out.append(view.root)
            view.close()
    assert len(os.listdir(base)) == 5 and len(set(handed_out)) == 5
    nxt = _build(root)                                     # the share has let go: ONE later claim clears every closed residue of this process
    try:
        handed_out.append(nxt.root)
        assert os.listdir(base) == [os.path.basename(nxt.root)]
        assert len(set(handed_out)) == 6
    finally:
        nxt.close()


def test_a_sweep_that_cannot_remove_a_leftover_says_so_keeps_it_and_the_next_claim_tries_again(tmp_path, monkeypatch, caplog):
    """The contract's 'record inability to remove' also holds for the sweep: a residue that stays is not allowed to stay unseen."""
    root = _repo(tmp_path)
    base = _views()
    os.makedirs(base, exist_ok=True)
    stuck = _leftover(_dead_pid(), counter=7)
    with monkeypatch.context() as patched:
        _blocked(patched)
        with caplog.at_level(logging.WARNING, logger=gate_view.logger.name):
            view = _build(root)
        view.close()
    assert os.path.lexists(stuck)
    assert any("could not be removed by the sweep" in r.getMessage() and stuck in r.getMessage() for r in caplog.records)
    nxt = _build(root)                                     # the removal now works: the same residue is swept, nothing waited for it
    try:
        assert not os.path.lexists(stuck)
    finally:
        nxt.close()


def test_a_sweep_that_removes_a_leftover_logs_nothing(tmp_path, caplog):
    root = _repo(tmp_path)
    os.makedirs(_views(), exist_ok=True)
    _leftover(_dead_pid(), counter=8)
    with caplog.at_level(logging.WARNING, logger=gate_view.logger.name):
        view = _build(root)
    try:
        assert not [r for r in caplog.records if "could not be removed" in r.getMessage()]
    finally:
        view.close()


def test_the_contract_helper_rejects_a_view_that_is_still_open_and_a_residue_no_claim_would_sweep(tmp_path):
    """The real-Docker tests lean on this helper; it must be able to fail (otherwise they could pass vacuously)."""
    from _gate_view_contract import assert_view_released

    root = _repo(tmp_path)
    open_view = _build(root)
    try:
        with pytest.raises(AssertionError, match="still open"):
            assert_view_released(open_view.root)
    finally:
        open_view.close()
    assert_view_released(open_view.root)                   # closed and gone: released
    legacy = os.path.join(_views(), "view-" + "a" * 16 + "-1")  # an older Kriya's stable name: no claim ever sweeps it
    os.makedirs(legacy)
    with pytest.raises(AssertionError, match="current unique form"):
        assert_view_released(legacy)
    other = _leftover(os.getppid(), counter=9)             # a live OTHER process's residue is not this process's to leave
    try:
        with pytest.raises(AssertionError, match="not owned by this process"):
            assert_view_released(other)
    finally:
        gate_view._remove_tree(other)                      # noqa: SLF001 - test cleanup
        gate_view._remove_tree(legacy)                     # noqa: SLF001 - test cleanup
