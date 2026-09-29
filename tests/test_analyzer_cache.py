"""CTX-001 P1 WP8: RepositoryAnalyzer.analyze() safe reuse.

The original WP8 attempt STOPPED because compute_workspace_content_hash()
(git-tree-based, .gitignore-aware) could stay unchanged while analyze()'s
own os.walk (hardcoded ignore_dirs only, no .gitignore awareness at all)
produced different output - a concretely reproduced unsafe-cache gap (see
docs/assurance/CTX_001_P1_ARCHITECTURE.md section 26). This package closes
that gap by making analyze() ALSO honor the workspace's real .gitignore
(via the same nested_gitignore_patterns_for()/is_ignored() primitive
index_repository() already used, additive to - never replacing - the
existing ignore_dirs/dot-prefix exclusions), then keys a small, in-process-
only cache on compute_workspace_content_hash().

Every test below uses a REAL git repository (subprocess git, no live
models) - the cache is a structural no-op for a non-git workspace by
design, so testing invalidation meaningfully requires real git semantics.
"""
import gc
import os
import shutil
import subprocess
import threading
import weakref

import pytest

from kriya.analyzer.analyzer import (
    _ANALYZE_CACHE,
    _ANALYZE_CACHE_HITS,
    _ANALYZE_CACHE_MISSES,
    RepositoryAnalyzer,
    RepositoryModel,
    _prune_departed_roots,
)
from kriya.platform.filesystem_semantics import identity_key


def _init_git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)


def _commit_all(tmp_path, message="commit"):
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=tmp_path, check=True)


def _counters():
    return _ANALYZE_CACHE_HITS[0], _ANALYZE_CACHE_MISSES[0]


# --- the original unsafe-cache gap, closed -----------------------------

def test_gitignored_addition_does_not_change_analyze_output_or_break_cache_safety(tmp_path):
    """The exact scenario the original WP8 attempt reproduced as unsafe:
    a file added under a project-specific .gitignore'd directory (not in
    analyze()'s own hardcoded ignore_dirs) must now be invisible to
    analyze() too, exactly as it already is to compute_workspace_content_
    hash() - closing the gap, not merely masking it."""
    _init_git_repo(tmp_path)
    (tmp_path / "generated_reports").mkdir()
    (tmp_path / "README.md").write_text("placeholder\n")
    _commit_all(tmp_path)
    (tmp_path / ".gitignore").write_text("generated_reports/\n")
    _commit_all(tmp_path, "add gitignore")

    model_before = RepositoryAnalyzer(str(tmp_path)).analyze()

    (tmp_path / "generated_reports" / "injected.py").write_text("x = 1\n" * 5)

    model_after = RepositoryAnalyzer(str(tmp_path)).analyze()

    assert model_before.languages == model_after.languages == {}
    assert model_before.project_structure["total_files_indexed"] == model_after.project_structure["total_files_indexed"]


def test_gitignored_addition_is_served_from_cache_not_recomputed_incorrectly(tmp_path):
    """The SAME scenario above, now proving it's a real cache HIT (not just
    a coincidentally-equal fresh recompute) - the workspace hash is
    unchanged, so the second call must reuse the first call's result."""
    _init_git_repo(tmp_path)
    (tmp_path / "generated_reports").mkdir()
    (tmp_path / "README.md").write_text("placeholder\n")
    _commit_all(tmp_path)
    (tmp_path / ".gitignore").write_text("generated_reports/\n")
    _commit_all(tmp_path, "add gitignore")

    RepositoryAnalyzer(str(tmp_path)).analyze()
    hits_before, misses_before = _counters()

    (tmp_path / "generated_reports" / "injected.py").write_text("x = 1\n")
    RepositoryAnalyzer(str(tmp_path)).analyze()

    hits_after, misses_after = _counters()
    assert hits_after == hits_before + 1
    assert misses_after == misses_before


# --- required invalidation matrix ---------------------------------------

def test_modification_invalidates(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "requirements.txt").write_text("requests\n")
    (tmp_path / "app.py").write_text("import requests\n")
    _commit_all(tmp_path)

    model1 = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert "requests" not in model1.dependency_versions

    (tmp_path / "requirements.txt").write_text("requests==2.28.0\n")

    model2 = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert model2.dependency_versions["requests"] == "2.28.0"


def test_relevant_addition_invalidates(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "app.py").write_text("x = 1\n")
    _commit_all(tmp_path)

    model1 = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert model1.project_structure["total_files_indexed"] == 1

    (tmp_path / "helper.py").write_text("y = 2\n")

    model2 = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert model2.project_structure["total_files_indexed"] == 2


def test_deletion_invalidates(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "helper.py").write_text("y = 2\n")
    _commit_all(tmp_path)

    model1 = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert model1.project_structure["total_files_indexed"] == 2

    os.remove(str(tmp_path / "helper.py"))

    model2 = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert model2.project_structure["total_files_indexed"] == 1


def test_rename_invalidates(tmp_path):
    """A rename with UNCHANGED content still changes the git tree (a
    different path -> different blob-to-path mapping) - compute_workspace_
    content_hash() is explicitly documented as correct for renames, this
    proves analyze()'s own reuse doesn't undermine that."""
    _init_git_repo(tmp_path)
    (tmp_path / "controllers").mkdir()
    (tmp_path / "controllers" / "user.py").write_text("x = 1\n")
    _commit_all(tmp_path)

    model1 = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert model1.project_structure["top_level_folders"] == ["controllers"]

    os.makedirs(str(tmp_path / "models"))
    os.rename(str(tmp_path / "controllers" / "user.py"), str(tmp_path / "models" / "user.py"))
    os.rmdir(str(tmp_path / "controllers"))

    model2 = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert model2.project_structure["top_level_folders"] == ["models"]


def test_manifest_change_invalidates(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text('{"dependencies": {"express": "^4.0.0"}}')
    _commit_all(tmp_path)

    model1 = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert "Express" in model1.frameworks
    assert "React" not in model1.frameworks

    (tmp_path / "package.json").write_text('{"dependencies": {"express": "^4.0.0", "react": "^18.0.0"}}')

    model2 = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert "React" in model2.frameworks


def test_directory_structure_change_invalidates(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("placeholder\n")
    _commit_all(tmp_path)

    model1 = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert model1.project_structure["top_level_folders"] == []

    (tmp_path / "myapp").mkdir()
    (tmp_path / "myapp" / "views.py").write_text("# real code\n")

    model2 = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert model2.project_structure["top_level_folders"] == ["myapp"]


def test_nested_gitignore_is_honored(tmp_path):
    """A .gitignore INSIDE a subdirectory (not just the repo root) must be
    honored - the same nested-.gitignore primitive index_repository()
    already relies on, now also driving analyze()."""
    _init_git_repo(tmp_path)
    (tmp_path / "service").mkdir()
    (tmp_path / "service" / "app.py").write_text("x = 1\n")
    _commit_all(tmp_path)

    model_before = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert model_before.project_structure["total_files_indexed"] == 1

    (tmp_path / "service" / ".gitignore").write_text("local_only/\n")
    (tmp_path / "service" / "local_only").mkdir()
    (tmp_path / "service" / "local_only" / "scratch.py").write_text("z = 1\n")
    _commit_all(tmp_path, "add nested gitignore")

    model_after = RepositoryAnalyzer(str(tmp_path)).analyze()
    # The nested-ignored file must never be counted, even though it's a
    # real .py file physically present under the walked tree.
    assert model_after.project_structure["total_files_indexed"] == 1


def test_unrelated_hardcoded_ignore_dir_content_change_is_still_excluded(tmp_path):
    """The pre-existing ignore_dirs/dot-prefix exclusions are ADDITIVE, not
    replaced - a change inside one of Kriya's own hardcoded-ignored
    directories must still never affect analyze()'s output, exactly as
    before this package."""
    _init_git_repo(tmp_path)
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"\x00\x01")
    _commit_all(tmp_path)

    model = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert model.project_structure["total_files_indexed"] == 1
    assert "__pycache__" not in model.project_structure["top_level_folders"]


# --- worktree divergence / non-git ---------------------------------------

def test_two_independent_git_roots_never_cross_contaminate_cache(tmp_path):
    """'Worktree divergence': two distinct real roots (workspace vs. a
    worktree checkout, modeled here as two separate git repos with
    different content) must always resolve to independently-correct
    results, never a stale cross-root cache hit."""
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()
    _init_git_repo(root_a)
    _init_git_repo(root_b)
    (root_a / "app.py").write_text("x = 1\n")
    (root_b / "app.py").write_text("x = 1\n")
    (root_b / "extra.py").write_text("y = 2\n")
    _commit_all(root_a)
    _commit_all(root_b)

    model_a = RepositoryAnalyzer(str(root_a)).analyze()
    model_b = RepositoryAnalyzer(str(root_b)).analyze()

    assert model_a.project_structure["total_files_indexed"] == 1
    assert model_b.project_structure["total_files_indexed"] == 2


def test_non_git_workspace_always_recomputes_and_still_works(tmp_path):
    """WP8 must not make a supported non-git workspace unanalyzable - the
    cache key is None for a non-git root, so every call recomputes fresh,
    identical to pre-WP8 behavior."""
    (tmp_path / "app.py").write_text("x = 1\n")

    hits_before, misses_before = _counters()
    model1 = RepositoryAnalyzer(str(tmp_path)).analyze()
    (tmp_path / "helper.py").write_text("y = 2\n")
    model2 = RepositoryAnalyzer(str(tmp_path)).analyze()
    hits_after, misses_after = _counters()

    assert model1.project_structure["total_files_indexed"] == 1
    assert model2.project_structure["total_files_indexed"] == 2
    # Neither call ever hit the cache (no hash to key on) - counters only
    # ever move via a real git workspace.
    assert hits_after == hits_before


# --- output safety / immutability -----------------------------------------

def test_cached_model_is_not_a_shared_mutable_instance(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "app.py").write_text("x = 1\n")
    _commit_all(tmp_path)

    model1 = RepositoryAnalyzer(str(tmp_path)).analyze()
    model1.languages["Python"] = -999.0  # a hostile/careless caller mutating its own copy

    model2 = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert model2.languages.get("Python") != -999.0
    assert model2.languages == {"Python": 100.0}


def test_cache_hit_and_miss_produce_identical_model_content(tmp_path):
    """Semantic equivalence: the cached (second call) and freshly-computed
    (first call) RepositoryModel content must be identical, field for
    field - a cache hit changes only performance, never meaning."""
    _init_git_repo(tmp_path)
    (tmp_path / "app.py").write_text("from fastapi import FastAPI\n")
    (tmp_path / "requirements.txt").write_text("fastapi==0.95.0\n")
    _commit_all(tmp_path)

    model1 = RepositoryAnalyzer(str(tmp_path)).analyze()
    model2 = RepositoryAnalyzer(str(tmp_path)).analyze()

    assert model1.model_dump() == model2.model_dump()


# --- performance counters ---------------------------------------------

def test_repeated_unchanged_analyze_decreases_recomputation_via_counters(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "app.py").write_text("x = 1\n")
    _commit_all(tmp_path)

    hits_before, misses_before = _counters()
    RepositoryAnalyzer(str(tmp_path)).analyze()
    misses_after_first = _ANALYZE_CACHE_MISSES[0]
    assert misses_after_first == misses_before + 1

    for _ in range(4):
        RepositoryAnalyzer(str(tmp_path)).analyze()

    hits_after, misses_after = _counters()
    assert misses_after == misses_after_first  # zero NEW misses across 4 more calls
    assert hits_after == hits_before + 4


# --- discovery-primitive convergence (analyze() vs. index_repository()) --
#
# Deliberately does NOT call index_repository() itself - that method always
# attempts a real embedding call (client.get_embedding("test"), used for
# dimension probing) and, absent an existing auto-skill directory, a real
# LLM completion call too (ConventionsExtractorAgent) - exactly the "no
# live models/embeddings" boundary this task requires never be crossed.
# Instead, this proves discovery-primitive CONVERGENCE directly and
# deterministically: nested_gitignore_patterns_for()/is_ignored() (the
# shared primitive) is invoked the SAME way index_repository()'s own walk
# invokes it (verified by reading, kriya/analyzer/analyzer.py's
# index_repository() body), applied here in an isolated os.walk that never
# touches the network.

def test_analyze_and_shared_discovery_primitive_agree_on_a_nested_gitignore_case(tmp_path):
    """Direct proof analyze()'s own walk and the shared discovery
    primitive index_repository() also uses converge on a representative
    nested-.gitignore case - DISCOVERY_SEMANTIC_DIVERGENCES=0 for the
    paths claimed to share canonical semantics."""
    from kriya.analyzer.analyzer import is_ignored, nested_gitignore_patterns_for, parse_gitignore

    _init_git_repo(tmp_path)
    (tmp_path / "service").mkdir()
    (tmp_path / "service" / "app.py").write_text("x = 1\n")
    (tmp_path / "service" / ".gitignore").write_text("local_only/\n")
    (tmp_path / "service" / "local_only").mkdir()
    (tmp_path / "service" / "local_only" / "scratch.py").write_text("z = 1\n")
    _commit_all(tmp_path)

    model = RepositoryAnalyzer(str(tmp_path)).analyze()
    assert model.project_structure["total_files_indexed"] == 1

    # The SAME discovery walk index_repository() itself performs (verified
    # against that method's own real body, not a re-derived approximation).
    root = str(tmp_path)
    gitignore_cache = {root: parse_gitignore(root)}
    discovered = []
    for walk_root, dirs, files in os.walk(root):
        current_patterns = nested_gitignore_patterns_for(walk_root, root, gitignore_cache)
        dirs[:] = [d for d in dirs if not is_ignored(os.path.join(walk_root, d), root, current_patterns)]
        for f in files:
            filepath = os.path.join(walk_root, f)
            if is_ignored(filepath, root, current_patterns):
                continue
            discovered.append(os.path.relpath(filepath, root))

    assert set(discovered) == {os.path.join("service", "app.py")}


# --- LEAK-ANALYZE-CACHE-001: at most one retained entry per root ---------
#
# Assertions are on len(_ANALYZE_CACHE) DELTAS, never on the key shape, so
# a regression back to (root, hash) keys fails them (other tests' roots may
# already sit in the module-level cache).

def _settled_size():
    """Cache size once departed roots of earlier tests are pruned (a store
    prunes them too, so this is the baseline a test's own stores add to)."""
    _prune_departed_roots()
    return len(_ANALYZE_CACHE)


def _entry_for(root):
    return _ANALYZE_CACHE.get(identity_key(str(root)))


def _languages_revision(tmp_path, n):
    (tmp_path / "app.py").write_text("x = %d\n" % n)


def test_same_content_repeatedly_keeps_one_entry_and_reuses_the_model(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "app.py").write_text("x = 1\n")
    _commit_all(tmp_path)
    size_before = _settled_size()

    RepositoryAnalyzer(str(tmp_path)).analyze()
    retained = _entry_for(tmp_path).model
    hits_before, misses_before = _counters()
    for _ in range(3):
        RepositoryAnalyzer(str(tmp_path)).analyze()

    assert len(_ANALYZE_CACHE) == size_before + 1
    assert _entry_for(tmp_path).model is retained
    assert _counters() == (hits_before + 3, misses_before)


def test_new_revision_replaces_the_entry_and_releases_the_old_model(tmp_path):
    _init_git_repo(tmp_path)
    _languages_revision(tmp_path, 1)
    _commit_all(tmp_path)
    size_before = _settled_size()

    RepositoryAnalyzer(str(tmp_path)).analyze()
    entry_a = _entry_for(tmp_path)
    model_a = weakref.ref(entry_a.model)
    hash_a = entry_a.content_hash
    del entry_a

    (tmp_path / "helper.py").write_text("y = 2\n")
    model_b = RepositoryAnalyzer(str(tmp_path)).analyze()
    gc.collect()

    entry = _entry_for(tmp_path)
    assert len(_ANALYZE_CACHE) == size_before + 1
    assert entry.content_hash != hash_a
    assert entry.model.model_dump() == model_b.model_dump()
    assert model_b.project_structure["total_files_indexed"] == 2
    assert model_a() is None  # revision A is no longer retained by the cache


def test_returning_to_an_earlier_revision_recomputes_it(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "app.py").write_text("x = 1\n")
    _commit_all(tmp_path)
    size_before = _settled_size()

    model_a = RepositoryAnalyzer(str(tmp_path)).analyze()
    hash_a = _entry_for(tmp_path).content_hash
    (tmp_path / "helper.py").write_text("y = 2\n")
    RepositoryAnalyzer(str(tmp_path)).analyze()
    os.remove(str(tmp_path / "helper.py"))

    hits_before, misses_before = _counters()
    model_a_again = RepositoryAnalyzer(str(tmp_path)).analyze()

    assert _counters() == (hits_before, misses_before + 1)  # recomputed, not a retained A
    assert model_a_again.model_dump() == model_a.model_dump()
    assert _entry_for(tmp_path).content_hash == hash_a
    assert len(_ANALYZE_CACHE) == size_before + 1


def test_many_revisions_of_one_root_keep_exactly_one_entry(tmp_path):
    _init_git_repo(tmp_path)
    _languages_revision(tmp_path, 0)
    _commit_all(tmp_path)
    size_before = _settled_size()

    hashes = set()
    for n in range(1, 9):
        _languages_revision(tmp_path, n)
        RepositoryAnalyzer(str(tmp_path)).analyze()
        hashes.add(_entry_for(tmp_path).content_hash)
        assert len(_ANALYZE_CACHE) == size_before + 1

    assert len(hashes) == 8  # every revision really was a different key


def test_each_root_keeps_its_own_latest_entry(tmp_path):
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    for root in (root_a, root_b):
        root.mkdir()
        _init_git_repo(root)
        (root / "app.py").write_text("x = 1\n")
        _commit_all(root)
    size_before = _settled_size()

    RepositoryAnalyzer(str(root_a)).analyze()
    RepositoryAnalyzer(str(root_b)).analyze()
    entry_b = _entry_for(root_b)
    (root_a / "helper.py").write_text("y = 2\n")
    model_a2 = RepositoryAnalyzer(str(root_a)).analyze()

    assert len(_ANALYZE_CACHE) == size_before + 2
    assert _entry_for(root_b) is entry_b  # updating A never evicts B
    assert _entry_for(root_a).model.model_dump() == model_a2.model_dump()


def test_an_alias_of_a_root_shares_its_single_entry_and_keeps_its_own_root_path(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    _init_git_repo(real)
    (real / "app.py").write_text("x = 1\n")
    _commit_all(real)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    size_before = _settled_size()

    via_real = RepositoryAnalyzer(str(real)).analyze()
    via_alias = RepositoryAnalyzer(str(alias)).analyze()

    assert len(_ANALYZE_CACHE) == size_before + 1
    assert via_real.root_path == str(real)
    assert via_alias.root_path == str(alias)  # never served the other spelling's model


def test_failed_replacement_keeps_the_prior_entry_bound_to_its_own_content(tmp_path, monkeypatch):
    """Defined behaviour: a build that raises leaves the root's previous
    valid entry in place (still keyed to its own hash, so it can never be
    served for the new content) and adds nothing partial."""
    _init_git_repo(tmp_path)
    (tmp_path / "app.py").write_text("x = 1\n")
    _commit_all(tmp_path)
    RepositoryAnalyzer(str(tmp_path)).analyze()
    size_before = _settled_size()
    prior = _entry_for(tmp_path)

    (tmp_path / "helper.py").write_text("y = 2\n")

    def _boom(self):
        raise RuntimeError("analysis failed")

    with monkeypatch.context() as patch:
        patch.setattr(RepositoryAnalyzer, "_analyze_uncached", _boom)
        with pytest.raises(RuntimeError):
            RepositoryAnalyzer(str(tmp_path)).analyze()

    assert len(_ANALYZE_CACHE) == size_before
    assert _entry_for(tmp_path) is prior
    recovered = RepositoryAnalyzer(str(tmp_path)).analyze()  # new content is built, not served A
    assert recovered.project_structure["total_files_indexed"] == 2
    assert _entry_for(tmp_path).content_hash != prior.content_hash


def test_concurrent_builds_for_one_root_leave_one_consistent_entry(tmp_path, monkeypatch):
    """Two analyses of the same root for different content overlap inside
    the build; whichever stores last wins, and the entry's hash always
    belongs to the model stored with it."""
    tmp_path.mkdir(exist_ok=True)
    root_key = identity_key(str(tmp_path))
    size_before = _settled_size()
    both_building = threading.Barrier(2)
    local = threading.local()

    def _key(self):
        return (root_key, local.content_hash)

    def _build(self):
        both_building.wait(timeout=10)
        return RepositoryModel(root_path=self.root_path, languages={local.content_hash: 100.0})

    monkeypatch.setattr(RepositoryAnalyzer, "_analyze_cache_key", _key)
    monkeypatch.setattr(RepositoryAnalyzer, "_analyze_uncached", _build)
    results, errors = {}, []

    def _run(content_hash):
        local.content_hash = content_hash
        try:
            results[content_hash] = RepositoryAnalyzer(str(tmp_path)).analyze()
        except Exception as exc:  # surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=_run, args=(h,)) for h in ("hash-a", "hash-b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert {h: m.languages for h, m in results.items()} == {"hash-a": {"hash-a": 100.0}, "hash-b": {"hash-b": 100.0}}
    entry = _ANALYZE_CACHE[root_key]
    assert len(_ANALYZE_CACHE) == size_before + 1
    assert entry.model.languages == {entry.content_hash: 100.0}


def _git_root(path):
    path.mkdir()
    _init_git_repo(path)
    (path / "app.py").write_text("x = 1\n")
    _commit_all(path)
    return path


def test_a_removed_root_is_dropped_at_the_next_store(tmp_path):
    """The leak the audit measured: every removed workspace (a candidate
    worktree per enforce run) kept its entry for good."""
    gone = _git_root(tmp_path / "worktree")
    RepositoryAnalyzer(str(gone)).analyze()
    gone_key = identity_key(str(gone))
    shutil.rmtree(gone)

    RepositoryAnalyzer(str(_git_root(tmp_path / "other"))).analyze()

    assert gone_key not in _ANALYZE_CACHE or _ANALYZE_CACHE[gone_key].model.root_path != str(gone)


def test_many_removed_roots_never_accumulate(tmp_path):
    size_before = _settled_size()
    for n in range(6):
        root = _git_root(tmp_path / ("w%d" % n))
        RepositoryAnalyzer(str(root)).analyze()
        shutil.rmtree(root)
    RepositoryAnalyzer(str(_git_root(tmp_path / "live"))).analyze()

    assert len(_ANALYZE_CACHE) == size_before + 1  # only the live root


def test_a_replaced_root_does_not_keep_the_old_directory_entry(tmp_path):
    """The path now names a different directory (the old one moved aside, so
    its inode stays alive and cannot be reused): the old entry is dropped
    and the new directory gets its own, never served the old model."""
    root = _git_root(tmp_path / "ws")
    RepositoryAnalyzer(str(root)).analyze()
    old_key = identity_key(str(root))
    root.rename(tmp_path / "ws-old")
    root = _git_root(tmp_path / "ws")
    (root / "helper.py").write_text("y = 2\n")
    size_before = len(_ANALYZE_CACHE)

    model = RepositoryAnalyzer(str(root)).analyze()

    assert identity_key(str(root)) != old_key
    assert old_key not in _ANALYZE_CACHE
    assert len(_ANALYZE_CACHE) == size_before  # replaced, not added beside it
    assert model.project_structure["total_files_indexed"] == 2
