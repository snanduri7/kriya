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
import os
import subprocess

from kriya.analyzer.analyzer import (
    _ANALYZE_CACHE_HITS,
    _ANALYZE_CACHE_MISSES,
    RepositoryAnalyzer,
)


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
