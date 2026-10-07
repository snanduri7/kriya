"""LR-R1-M1.9: attempt-evidence retention (design §10; test T12).

Never prunes the named/active run, a referenced run or an unsealed store
younger than the grace period; prunes the oldest sealed stores first by
count, then by size; a crashed (old unsealed) store is prunable; a dry run
writes nothing; an interrupted prune leaves nothing readable behind.
"""
import os
import time

from kriya.core.attempt_evidence import reader, scope
from kriya.core.attempt_evidence.retention import UNSEALED_GRACE_SECONDS, prune_evidence
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter, run_directory, store_root
from kriya.core.state_paths import ENV_STATE_DIR

NOW = 1_900_000_000.0


def _store(state_dir, run_id, *, age_seconds, sealed=True, payload_bytes=0):
    writer = AttemptEvidenceWriter(str(state_dir), run_id, capture="full", manifest={})
    writer.record("run.opened", {}, content={"pad": "x" * payload_bytes} if payload_bytes else None)
    directory = run_directory(str(state_dir), run_id)
    stamp = NOW - age_seconds
    if sealed:
        writer.seal("test")
        os.utime(os.path.join(directory, "seal.json"), (stamp, stamp))
    os.utime(os.path.join(directory, "manifest.json"), (stamp, stamp))
    return directory


def _tree(root):
    return sorted((dirpath, tuple(sorted(files))) for dirpath, _dirs, files in os.walk(root))


def test_oldest_sealed_stores_are_pruned_first_by_count(tmp_path):
    for index, run_id in enumerate(["r-old", "r-mid", "r-new"]):
        _store(tmp_path, run_id, age_seconds=(3 - index) * 3600)
    report = prune_evidence(str(tmp_path), keep_runs=2, max_bytes=10 ** 12, now=NOW)
    assert report.pruned == ["r-old"] and report.kept == ["r-mid", "r-new"]
    assert reader.list_runs(str(tmp_path)) == ["r-mid", "r-new"]


def test_protected_stores_are_never_pruned_and_do_not_count_against_keep(tmp_path):
    _store(tmp_path, "r-active", age_seconds=10 * 3600)                       # oldest, but named
    _store(tmp_path, "r-referenced", age_seconds=9 * 3600)                    # oldest after it, referenced
    _store(tmp_path, "r-running", age_seconds=3600, sealed=False)             # young and unsealed
    _store(tmp_path, "r-a", age_seconds=5 * 3600)
    _store(tmp_path, "r-b", age_seconds=4 * 3600)
    report = prune_evidence(str(tmp_path), keep_runs=1, max_bytes=10 ** 12,
                            protect_run_ids={"r-active", "r-referenced"}, now=NOW)
    assert report.protected == {"r-active": "named", "r-referenced": "named", "r-running": "unsealed_recent"}
    assert report.pruned == ["r-a"] and report.kept == ["r-b"]
    assert set(reader.list_runs(str(tmp_path))) == {"r-active", "r-referenced", "r-running", "r-b"}


def test_a_crashed_store_older_than_the_grace_period_is_prunable(tmp_path):
    _store(tmp_path, "r-crashed", age_seconds=UNSEALED_GRACE_SECONDS + 60, sealed=False)
    _store(tmp_path, "r-young", age_seconds=UNSEALED_GRACE_SECONDS - 60, sealed=False)
    report = prune_evidence(str(tmp_path), keep_runs=0, max_bytes=10 ** 12, now=NOW)
    assert report.pruned == ["r-crashed"] and report.protected == {"r-young": "unsealed_recent"}


def test_size_bound_drops_oldest_kept_stores_never_protected_ones(tmp_path):
    _store(tmp_path, "r-big-protected", age_seconds=9 * 3600, payload_bytes=200_000)
    for index, run_id in enumerate(["r-1", "r-2", "r-3"]):
        _store(tmp_path, run_id, age_seconds=(5 - index) * 3600, payload_bytes=50_000)
    report = prune_evidence(str(tmp_path), keep_runs=10, max_bytes=1, protect_run_ids={"r-big-protected"},
                            now=NOW)
    assert report.pruned == ["r-1", "r-2", "r-3"] and report.kept == []
    assert reader.list_runs(str(tmp_path)) == ["r-big-protected"]
    assert report.retained_bytes > 1                          # the protected run alone exceeds the bound


def test_size_bound_keeps_the_newest_that_fit(tmp_path):
    sizes = {}
    for index, run_id in enumerate(["r-1", "r-2", "r-3"]):
        directory = _store(tmp_path, run_id, age_seconds=(5 - index) * 3600, payload_bytes=50_000)
        sizes[run_id] = sum(os.path.getsize(os.path.join(d, f)) for d, _s, fs in os.walk(directory) for f in fs)
    report = prune_evidence(str(tmp_path), keep_runs=10, max_bytes=sizes["r-2"] + sizes["r-3"], now=NOW)
    assert report.pruned == ["r-1"] and report.kept == ["r-2", "r-3"]


def test_a_dry_run_writes_nothing(tmp_path):
    for index, run_id in enumerate(["r-1", "r-2", "r-3"]):
        _store(tmp_path, run_id, age_seconds=(5 - index) * 3600)
    os.makedirs(os.path.join(store_root(str(tmp_path)), ".pruning-r-0-abcd"))
    before = _tree(tmp_path)
    report = prune_evidence(str(tmp_path), keep_runs=1, max_bytes=10 ** 12, dry_run=True, now=NOW)
    assert report.pruned == ["r-1", "r-2"] and report.dry_run is True
    assert _tree(tmp_path) == before


def test_an_interrupted_prune_is_never_read_and_is_cleaned_next_time(tmp_path):
    _store(tmp_path, "r-keep", age_seconds=3600)
    staging = os.path.join(store_root(str(tmp_path)), ".pruning-r-gone-abcd")
    os.makedirs(os.path.join(staging, "blobs"))
    open(os.path.join(staging, "manifest.json"), "w").close()
    assert reader.list_runs(str(tmp_path)) == ["r-keep"]
    prune_evidence(str(tmp_path), keep_runs=5, max_bytes=10 ** 12, now=NOW)
    assert not os.path.exists(staging)


def test_a_symlinked_entry_is_never_followed_or_removed(tmp_path):
    """Even when the link points at something shaped like an old sealed
    store, the entry is not a store: it is neither pruned nor renamed."""
    outside = tmp_path / "outside"
    outside.mkdir()
    for name in ("manifest.json", "seal.json", "keep.txt"):
        (outside / name).write_text("precious")
        os.utime(outside / name, (NOW - 10 * 3600, NOW - 10 * 3600))
    _store(tmp_path, "r-1", age_seconds=3600)
    link = os.path.join(store_root(str(tmp_path)), "r-link")
    os.symlink(outside, link)
    report = prune_evidence(str(tmp_path), keep_runs=0, max_bytes=10 ** 12, now=NOW)
    assert report.pruned == ["r-1"]
    assert os.path.islink(link) and (outside / "keep.txt").read_text() == "precious"


def test_a_store_whose_age_cannot_be_read_is_protected(tmp_path):
    """A directory with neither manifest nor seal (creation interrupted):
    its age is unknown, so it is kept - and the prune does not fail."""
    _store(tmp_path, "r-1", age_seconds=3600)
    os.makedirs(os.path.join(store_root(str(tmp_path)), "r-partial", "blobs"))
    report = prune_evidence(str(tmp_path), keep_runs=0, max_bytes=10 ** 12, now=NOW)
    assert report.protected == {"r-partial": "age_unreadable"} and report.pruned == ["r-1"]
    assert os.path.isdir(os.path.join(store_root(str(tmp_path)), "r-partial"))


# -- at run close ------------------------------------------------------------------------------

class _Context:
    def __init__(self, run_id):
        self.run_id = run_id


def test_run_close_prunes_with_the_configured_bounds_and_protects_references(tmp_path, monkeypatch):
    from tests._strict_doubles import strict_config

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state_dir))
    old = time.time() - 10 * 3600
    for index, run_id in enumerate(["r-referenced", "r-old", "r-newer"]):
        _store(state_dir, run_id, age_seconds=0)
        stamp = old + index
        os.utime(os.path.join(run_directory(str(state_dir), run_id), "seal.json"), (stamp, stamp))
    cfg = strict_config(evidence={"attempt_recorder": {"retention": {"keep_runs": 1}}})
    context = _Context("r-current")
    with scope.run_scope(context):
        scope.ensure_store(cfg)
        scope.close_run(context, lambda: None)
        scope.prune_after_run(context, lambda: {"r-referenced"})
    assert set(reader.list_runs(str(state_dir))) == {"r-current", "r-referenced", "r-newer"}


def test_run_close_without_an_open_store_prunes_nothing(tmp_path, monkeypatch):
    from tests._strict_doubles import strict_config

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state_dir))
    _store(state_dir, "r-old", age_seconds=0)
    cfg = strict_config(evidence={"attempt_recorder": {"capture": "off", "retention": {"keep_runs": 1}}})
    context = _Context("r-current")
    with scope.run_scope(context):
        scope.ensure_store(cfg)
        scope.prune_after_run(context, lambda: set())
    assert reader.list_runs(str(state_dir)) == ["r-old"]


def test_a_failing_reference_lookup_never_escapes(tmp_path, monkeypatch):
    from tests._strict_doubles import strict_config

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state_dir))
    _store(state_dir, "r-old", age_seconds=0)
    context = _Context("r-current")

    def broken():
        raise RuntimeError("checkpoint dir unreadable")
    with scope.run_scope(context):
        scope.ensure_store(strict_config(evidence={"attempt_recorder": {"retention": {"keep_runs": 1}}}))
        scope.prune_after_run(context, broken)          # logged, never raised
    assert "r-old" in reader.list_runs(str(state_dir))  # nothing pruned without the references


def test_a_dot_prefixed_run_id_is_refused_so_staging_names_never_collide(tmp_path):
    import pytest

    from kriya.core.attempt_evidence.writer import RecorderUnavailable

    with pytest.raises(RecorderUnavailable):
        AttemptEvidenceWriter(str(tmp_path), ".pruning-r-1", capture="full", manifest={})


def test_a_real_run_prunes_at_its_close_under_the_configured_bound(tmp_path, monkeypatch):
    """Through begin_mutating_run (the real pipeline, runtime port scripted):
    with keep_runs=1, the run's own store is kept and only the newest older
    store survives."""
    from _chaos_harness import (
        CALC,
        CALC_WITH_SUB,
        TEST_SUB,
        ChaosRuntime,
        RuntimeRegistration,
        benign_roles,
        chaos_config,
        chaos_engine,
        git_workspace,
        run_direct,
    )

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state_dir))
    old = time.time() - 10 * 3600
    for index, run_id in enumerate(["r-oldest", "r-newest"]):
        _store(state_dir, run_id, age_seconds=0)
        os.utime(os.path.join(run_directory(str(state_dir), run_id), "seal.json"), (old + index, old + index))
    cfg = chaos_config()
    cfg.evidence.attempt_recorder.retention.keep_runs = 1
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    runtime = ChaosRuntime(lambda role, request: CALC_WITH_SUB if role == "developer"
                           else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        run_direct(chaos_engine(cfg), "add sub to calc.py", workspace)
    runs = reader.list_runs(str(state_dir))
    assert "r-oldest" not in runs and "r-newest" in runs and len(runs) == 2
    [current] = [run for run in runs if run != "r-newest"]
    assert reader.open_run(str(state_dir), current).verify().status == reader.VERIFIED
