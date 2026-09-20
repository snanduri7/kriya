"""CTX-001 P1 WP6 (shared allocator + omission) and WP7
(build_known_target_context, replacing _brownfield_owner_contract_block's
own source-content responsibility)."""
import os

from kriya.workflow.context_budget import (
    KNOWN_TARGET_FLOOR,
    REASON_BODY_ELIDED,
    REASON_BUDGET_EXHAUSTED,
    REASON_SOURCE_UNAVAILABLE,
    REASON_STALE_REVISION_REJECTED,
    REASON_UNSUPPORTED_STRUCTURAL_EXTRACTION,
    build_code_context,
    build_code_context_package,
    build_known_target_context,
)


def _write(root, relpath, content):
    full = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)


# --- WP6: build_code_context_package / byte-compat regression guard --------

def test_build_code_context_byte_identical_no_degradation_no_scores(tmp_path):
    """Regression guard (architecture doc section 19): when everything fits
    at 'full' tier, the categorical (file_scores=None) rendering path must
    be byte-identical before/after this package - proven here against a
    literal expected string, not just "doesn't crash"."""
    _write(str(tmp_path), "a.py", "def a():\n    return 1\n")
    _write(str(tmp_path), "b.py", "def b():\n    return 2\n")

    result = build_code_context(["a.py"], ["b.py"], str(tmp_path), budget_limit=100000)

    expected = (
        "\n\n=== Codebase Semantic Reference Context ===\n"
        "\nFile: a.py (Tier: full)\ndef a():\n    return 1\n\n"
        "\n\n=== Bounded Neighborhood Dependency Context ===\n"
        "\nFile: b.py (Tier: full)\ndef b():\n    return 2\n\n"
    )
    assert result == expected


def test_build_code_context_byte_identical_no_degradation_with_scores(tmp_path):
    _write(str(tmp_path), "a.py", "def a():\n    return 1\n")
    _write(str(tmp_path), "b.py", "def b():\n    return 2\n")

    result = build_code_context(
        ["a.py"], ["b.py"], str(tmp_path), budget_limit=100000,
        file_scores={"a.py": 0.9, "b.py": 0.5},
    )

    expected = (
        "\n\n=== Codebase Semantic Reference Context ===\n"
        "\nFile: a.py (Tier: full)\ndef a():\n    return 1\n\n"
        "\n\n=== Bounded Neighborhood Dependency Context ===\n"
        "\nFile: b.py (Tier: full)\ndef b():\n    return 2\n\n"
    )
    assert result == expected


def test_build_code_context_package_matches_legacy_string_output(tmp_path):
    """build_code_context() is now a thin wrapper - its output must always
    equal build_code_context_package()'s own rendered string, for any input,
    not just the no-degradation case."""
    _write(str(tmp_path), "a.py", "x = 1\n" * 500)
    _write(str(tmp_path), "b.py", "y = 2\n" * 500)

    legacy = build_code_context(["a.py"], ["b.py"], str(tmp_path), budget_limit=50)
    rendered, _package = build_code_context_package(["a.py"], ["b.py"], str(tmp_path), budget_limit=50)

    assert legacy == rendered


def test_build_code_context_package_populates_context_package_on_degradation(tmp_path):
    """C6: the default pipeline's Graph-RAG matched/related flow must now
    produce a real ContextPackage with a reason-labeled omission whenever a
    real degradation occurred - not just the opt-in WorkflowController path."""
    _write(str(tmp_path), "a.py", "x = 1\n" * 2000)

    _rendered, package = build_code_context_package(["a.py"], [], str(tmp_path), budget_limit=10)

    assert package.relevant_files
    assert package.relevant_files[0].tier != "full"
    assert any(o["reason"] == REASON_BODY_ELIDED for o in package.omitted)


def test_build_code_context_package_records_source_unavailable(tmp_path):
    """Today's silent `except Exception: logger.debug(...)` swallow (P0's
    own finding) must now ALSO produce a real, recorded omission entry."""
    os.makedirs(str(tmp_path), exist_ok=True)

    _rendered, package = build_code_context_package(["missing.py"], [], str(tmp_path), budget_limit=1000)

    assert any(o["reason"] == REASON_SOURCE_UNAVAILABLE and o["path"] == "missing.py" for o in package.omitted)


def test_build_code_context_package_no_degradation_has_no_omissions(tmp_path):
    _write(str(tmp_path), "a.py", "x = 1\n")

    _rendered, package = build_code_context_package(["a.py"], [], str(tmp_path), budget_limit=100000)

    assert package.omitted == ()
    assert package.relevant_files[0].is_exact is True
    assert package.relevant_files[0].tier == "full"


# --- WP7: build_known_target_context ----------------------------------------

def test_known_target_full_source_when_it_fits(tmp_path):
    _write(str(tmp_path), "Owner.java", "public class Owner {}\n")

    rendered, package = build_known_target_context(["Owner.java"], str(tmp_path), str(tmp_path), 100000)

    assert "public class Owner {}" in rendered
    assert package.relevant_files[0].tier == "full"
    assert package.relevant_files[0].is_exact is True
    assert package.omitted == ()


def test_known_target_current_source_is_worktree_not_workspace(tmp_path):
    """C3 fix, exercised directly at the allocator level: worktree content
    must win, workspace's stale content must never appear."""
    workspace = tmp_path / "workspace"
    worktree = tmp_path / "worktree"
    _write(str(workspace), "Target.java", "VERSION_A")
    _write(str(worktree), "Target.java", "VERSION_B")

    rendered, package = build_known_target_context(
        ["Target.java"], str(workspace), str(worktree), 100000,
    )

    assert "VERSION_B" in rendered
    assert "VERSION_A" not in rendered
    assert package.relevant_files[0].content == "VERSION_B"


def test_known_target_c1_repro_four_files_over_24k_all_accounted_for(tmp_path):
    """Reproduces CTX-001-P0's own C1 probe: 4 known-target files whose
    combined size exceeds the old 24,000-char cap. Every file must get
    EITHER real content at some tier OR an explicit omission entry - never
    a silent drop (the old _brownfield_owner_contract_block's own `break`
    on a naive combined cap)."""
    files = ["A.java", "B.java", "C.java", "D.java"]
    for name in files:
        _write(str(tmp_path), name, f"// {name}\n" + ("x" * 9000))

    budget_limit = 2000  # tokens - deliberately tight, combined content (~9000*4 chars each) can't all fit
    rendered, package = build_known_target_context(files, str(tmp_path), str(tmp_path), budget_limit)

    represented_paths = {item.path for item in package.relevant_files}
    omitted_paths = {o["path"] for o in package.omitted}
    # Every requested file is accounted for - either represented (at some
    # tier, possibly with its own body_elided omission too) or fully
    # omitted with a reason. Zero silent drops.
    for name in files:
        assert name in represented_paths or name in omitted_paths, (
            f"{name} has no representation and no omission record - silent drop"
        )
    # And at least one file genuinely couldn't fit at all, given the tight
    # budget - proves the scenario actually exercises budget exhaustion,
    # not just a trivially-satisfied assertion.
    assert any(o["reason"] == REASON_BUDGET_EXHAUSTED for o in package.omitted)


def test_known_target_member_exact_retained_while_sibling_degrades(tmp_path):
    """C2: given a large file with a relevant member and irrelevant
    siblings, the allocator can retain the relevant member EXACT/FULL while
    the rest of the same file degrades - reuses the P0 S2 large-file
    fixture generator (near_start/near_end/far_apart placements)."""
    import sys
    sys.path.insert(0, str((__file__.rsplit("/tests/", 1)[0]) + "/spikes/ctx_001_p0"))
    from fixtures import build_large_file

    content = build_large_file(target_lines=2000, placement="near_end")
    _write(str(tmp_path), "Large.py", content)

    rendered, package = build_known_target_context(
        ["Large.py"], str(tmp_path), str(tmp_path), budget_limit=400,
        member_hints={"Large.py": "LargeInvoiceProcessor.calculate_total"},
    )

    member_items = [i for i in package.relevant_files if i.member_id == "LargeInvoiceProcessor.calculate_total"]
    assert member_items, "the relevant member was not retained as its own exact unit"
    assert member_items[0].tier == "member_exact"
    assert member_items[0].is_exact is True
    assert "subtotal * 0.05" in member_items[0].content
    # The full 2000-line padding is NOT all present verbatim in the member
    # item itself (it's scoped to just the member's own body).
    assert member_items[0].content.count("_padding_method_") == 0


def test_known_target_two_relevant_members_far_apart_both_addressable(tmp_path):
    """S2's far_apart placement: two real members (an interface-shaped
    abstract method near the top, the concrete override near the bottom) in
    the SAME 2000+ line file - both independently addressable via a member
    hint, one call each."""
    import sys
    sys.path.insert(0, str((__file__.rsplit("/tests/", 1)[0]) + "/spikes/ctx_001_p0"))
    from fixtures import build_large_file

    content = build_large_file(target_lines=2500, placement="far_apart")
    _write(str(tmp_path), "Large.py", content)

    rendered_a, package_a = build_known_target_context(
        ["Large.py"], str(tmp_path), str(tmp_path), budget_limit=400,
        member_hints={"Large.py": "LargeInvoiceProcessor.calculate_total_contract"},
    )
    rendered_b, package_b = build_known_target_context(
        ["Large.py"], str(tmp_path), str(tmp_path), budget_limit=400,
        member_hints={"Large.py": "LargeInvoiceProcessor.calculate_total"},
    )

    a_items = [i for i in package_a.relevant_files if i.member_id == "LargeInvoiceProcessor.calculate_total_contract"]
    b_items = [i for i in package_b.relevant_files if i.member_id == "LargeInvoiceProcessor.calculate_total"]
    assert a_items and a_items[0].tier == "member_exact"
    assert b_items and b_items[0].tier == "member_exact"


def test_known_target_known_target_floor_beats_low_retrieval_score(tmp_path):
    """C5: a known target with an adversarially-low retrieval score must
    still clear the priority floor - target status is not order/cap
    dependent, and is not silently starved by a bad score."""
    _write(str(tmp_path), "LowScored.java", "public class LowScored {}\n")
    _write(str(tmp_path), "HighScored.java", "public class HighScored {}\n")

    _rendered, package = build_known_target_context(
        ["LowScored.java", "HighScored.java"], str(tmp_path), str(tmp_path), 100000,
        file_scores={"LowScored.java": 0.01, "HighScored.java": 0.99},
    )

    assert {i.path for i in package.relevant_files} == {"LowScored.java", "HighScored.java"}
    low = next(i for i in package.relevant_files if i.path == "LowScored.java")
    assert low.score == KNOWN_TARGET_FLOOR


def test_known_target_floor_does_not_grant_unlimited_inclusion(tmp_path):
    """The floor is a priority, not a budget override - a known target can
    still be explicitly omitted when the budget is genuinely exhausted."""
    _write(str(tmp_path), "A.java", "x" * 40000)
    _write(str(tmp_path), "B.java", "y" * 40000)

    _rendered, package = build_known_target_context(
        ["A.java", "B.java"], str(tmp_path), str(tmp_path), budget_limit=1,
    )

    # budget_limit=1 token: the FIRST file (by stable order) gets the
    # bounded excerpt that fits in that 1 token; the second genuinely has
    # zero budget left and must be an explicit omission, never silently
    # dropped with no trace.
    assert len(package.omitted) >= 1
    assert any(o["reason"] == REASON_BUDGET_EXHAUSTED for o in package.omitted)


def test_known_target_unsupported_language_member_hint_falls_back_explicitly(tmp_path):
    _write(str(tmp_path), "main.go", "func main() {}\n")

    _rendered, package = build_known_target_context(
        ["main.go"], str(tmp_path), str(tmp_path), 100000,
        member_hints={"main.go": "main"},
    )

    assert any(o["reason"] == REASON_UNSUPPORTED_STRUCTURAL_EXTRACTION for o in package.omitted)
    # Still falls back to whole-file representation - never zero content
    # just because the member hint couldn't be honored.
    assert package.relevant_files
    assert package.relevant_files[0].tier == "full"


def test_known_target_missing_member_id_falls_back_to_whole_file(tmp_path):
    _write(str(tmp_path), "Owner.py", "def real_function():\n    pass\n")

    _rendered, package = build_known_target_context(
        ["Owner.py"], str(tmp_path), str(tmp_path), 100000,
        member_hints={"Owner.py": "nonexistent_member"},
    )

    assert any(o["reason"] == REASON_UNSUPPORTED_STRUCTURAL_EXTRACTION for o in package.omitted)
    assert package.relevant_files[0].tier == "full"


def test_known_target_deleted_file_is_source_unavailable(tmp_path):
    workspace = tmp_path / "workspace"
    worktree = tmp_path / "worktree"
    _write(str(workspace), "Gone.java", "old")
    os.makedirs(str(worktree))

    _rendered, package = build_known_target_context(
        ["Gone.java"], str(workspace), str(worktree), 100000,
    )

    assert package.relevant_files == ()
    assert any(o["reason"] == REASON_SOURCE_UNAVAILABLE for o in package.omitted)


def test_known_target_stale_revision_hint_recorded_but_not_fatal(tmp_path):
    _write(str(tmp_path), "Owner.java", "public class Owner {}\n")

    _rendered, package = build_known_target_context(
        ["Owner.java"], str(tmp_path), str(tmp_path), 100000,
        known_revisions={"Owner.java": "wrong-hash"},
    )

    assert any(o["reason"] == REASON_STALE_REVISION_REJECTED for o in package.omitted)
    # Still represented with the REAL content, not silently dropped.
    assert package.relevant_files[0].content == "public class Owner {}\n"


def test_known_target_excludes_paths_already_represented_elsewhere(tmp_path):
    """Dedup mechanism (DUPLICATE_SOURCE_CONTEXT_PATHS=0): a path in
    `exclude` never appears in this producer's own output."""
    _write(str(tmp_path), "AlreadyShown.java", "public class AlreadyShown {}\n")
    _write(str(tmp_path), "NotShown.java", "public class NotShown {}\n")

    _rendered, package = build_known_target_context(
        ["AlreadyShown.java", "NotShown.java"], str(tmp_path), str(tmp_path), 100000,
        exclude={"AlreadyShown.java"},
    )

    paths = {i.path for i in package.relevant_files}
    assert "AlreadyShown.java" not in paths
    assert "NotShown.java" in paths


def test_known_target_rendered_string_never_empty_when_evidence_omitted(tmp_path):
    """WP7's critical invariant: if modification-critical evidence cannot
    be represented, Kriya must not silently proceed as though it was
    present - the rendered (model-visible) string must carry a bounded
    summary of what was omitted, not just internal ContextPackage state."""
    _write(str(tmp_path), "A.java", "x" * 40000)
    _write(str(tmp_path), "B.java", "y" * 40000)

    rendered, package = build_known_target_context(
        ["A.java", "B.java"], str(tmp_path), str(tmp_path), budget_limit=1,
    )

    assert package.omitted
    assert "omitted" in rendered.lower()


# --- CTX-001 P1 C2: multi-member-hint support (production integration) -----

def test_known_target_member_hints_accepts_a_bare_string_backward_compatible(tmp_path):
    """Package 2's original single-string shape must keep working
    unchanged - the Union widening is additive, never a breaking change."""
    _write(str(tmp_path), "Owner.py", "class Owner:\n    def method(self):\n        pass\n")

    _rendered, package = build_known_target_context(
        ["Owner.py"], str(tmp_path), str(tmp_path), 100000,
        member_hints={"Owner.py": "Owner.method"},
    )

    member_items = [i for i in package.relevant_files if i.member_id == "Owner.method"]
    assert len(member_items) == 1
    assert member_items[0].tier == "member_exact"


def test_known_target_member_hints_accepts_a_list_of_distinct_members(tmp_path):
    content = (
        "class Owner:\n"
        "    def method_a(self):\n"
        "        pass\n\n"
        "    def method_b(self):\n"
        "        pass\n"
    )
    _write(str(tmp_path), "Owner.py", content)

    _rendered, package = build_known_target_context(
        ["Owner.py"], str(tmp_path), str(tmp_path), 100000,
        member_hints={"Owner.py": ["Owner.method_a", "Owner.method_b"]},
    )

    member_ids = {i.member_id for i in package.relevant_files if i.tier == "member_exact"}
    assert member_ids == {"Owner.method_a", "Owner.method_b"}


def test_known_target_ambiguous_java_overload_retains_both_real_bodies(tmp_path):
    """A member_id that resolves to MULTIPLE real boundaries (an
    unresolvable Java overload) must retain ALL of them - never an
    arbitrary next()-style first pick."""
    content = (
        "public class Owner {\n"
        "    public String format(String x) { return x; }\n"
        "    public String format(String x, String y) { return x + y; }\n"
        "}\n"
    )
    _write(str(tmp_path), "Owner.java", content)

    _rendered, package = build_known_target_context(
        ["Owner.java"], str(tmp_path), str(tmp_path), 100000,
        member_hints={"Owner.java": "Owner.format"},
    )

    member_items = [i for i in package.relevant_files if i.member_id == "Owner.format"]
    assert len(member_items) == 2
    contents = {i.content for i in member_items}
    assert any("String x, String y" in c for c in contents)
    assert any("String x)" in c and "String x, String y" not in c for c in contents)


def test_known_target_member_hint_that_no_longer_resolves_falls_back_to_whole_file(tmp_path):
    """A member_id that doesn't match any real CURRENT boundary (stale/
    renamed/removed) degrades to the existing whole-file handling - never
    a fabricated member, never a silent drop."""
    _write(str(tmp_path), "Owner.py", "class Owner:\n    def real_method(self):\n        pass\n")

    _rendered, package = build_known_target_context(
        ["Owner.py"], str(tmp_path), str(tmp_path), 100000,
        member_hints={"Owner.py": "Owner.removed_method"},
    )

    assert any(o["reason"] == "unsupported_structural_extraction" for o in package.omitted)
    assert package.relevant_files[0].tier == "full"


# --- CTX-001 P1 WP9: AttemptContext-lifetime source-derivation reuse -------

def test_build_code_context_package_cached_equals_uncached(tmp_path):
    """Semantic equivalence: for identical inputs, the cached-path output
    must equal the uncached-path output exactly - cache hits may change
    hit/miss COUNTERS, never rendered context content."""
    from kriya.workflow.context_source import SourceDerivationCache

    _write(str(tmp_path), "a.py", "x = 1\n" * 500)
    _write(str(tmp_path), "b.py", "y = 2\n" * 500)

    uncached_rendered, uncached_package = build_code_context_package(
        ["a.py"], ["b.py"], str(tmp_path), budget_limit=50,
    )
    cache = SourceDerivationCache()
    cached_rendered, cached_package = build_code_context_package(
        ["a.py"], ["b.py"], str(tmp_path), budget_limit=50, cache=cache,
    )

    assert uncached_rendered == cached_rendered
    assert [i.to_dict() for i in uncached_package.relevant_files] == [i.to_dict() for i in cached_package.relevant_files]
    assert uncached_package.omitted == cached_package.omitted


def test_build_code_context_package_reuses_derivation_across_calls(tmp_path):
    """Cross-attempt scenario (file-level): attempt 1 derives a skeleton for
    a file; attempt 2 (same cache, unchanged file) must reuse it - zero
    additional skeletonization work for that file."""
    from kriya.workflow.context_source import SourceDerivationCache

    content = "x = 1\n" * 2000
    _write(str(tmp_path), "a.py", content)
    cache = SourceDerivationCache()

    build_code_context_package(["a.py"], [], str(tmp_path), budget_limit=10, cache=cache)
    misses_after_first = cache.derivation_misses
    assert misses_after_first > 0

    build_code_context_package(["a.py"], [], str(tmp_path), budget_limit=10, cache=cache)
    assert cache.derivation_misses == misses_after_first
    assert cache.derivation_hits > 0


def test_cross_attempt_file_a_reused_file_b_recomputed(tmp_path):
    """Required deterministic scenario: attempt 1 derives A and B; B is
    modified between attempts; attempt 2 must reuse A's derivation and
    recompute B's - no stale B representation may enter the result."""
    from kriya.workflow.context_source import SourceDerivationCache

    _write(str(tmp_path), "A.py", "a_content = 1\n" * 2000)
    _write(str(tmp_path), "B.py", "b_content = 'VERSION_1'\n" * 2000)
    cache = SourceDerivationCache()

    build_code_context_package(["A.py", "B.py"], [], str(tmp_path), budget_limit=20, cache=cache)
    hits_before, misses_before = cache.derivation_hits, cache.derivation_misses

    import time
    time.sleep(0.01)
    _write(str(tmp_path), "B.py", "b_content = 'VERSION_2'\n" * 2000)
    os.utime(str(tmp_path / "B.py"), None)

    rendered, _package = build_code_context_package(["A.py", "B.py"], [], str(tmp_path), budget_limit=20, cache=cache)

    # A's own derivation was reused (a new hit recorded); B's was recomputed
    # (a new miss recorded) - and the rendered output reflects B's CURRENT
    # content, never a stale cached one.
    assert cache.derivation_hits > hits_before
    assert cache.derivation_misses > misses_before
    assert "VERSION_1" not in rendered


def test_known_target_context_cached_equals_uncached_member_exact(tmp_path):
    from kriya.workflow.context_source import SourceDerivationCache

    _write(str(tmp_path), "Owner.py", "class Owner:\n    def method(self):\n        return 1\n")

    uncached_rendered, uncached_package = build_known_target_context(
        ["Owner.py"], str(tmp_path), str(tmp_path), 100000,
        member_hints={"Owner.py": "Owner.method"},
    )
    cache = SourceDerivationCache()
    cached_rendered, cached_package = build_known_target_context(
        ["Owner.py"], str(tmp_path), str(tmp_path), 100000,
        member_hints={"Owner.py": "Owner.method"}, cache=cache,
    )

    assert uncached_rendered == cached_rendered
    assert [i.to_dict() for i in uncached_package.relevant_files] == [i.to_dict() for i in cached_package.relevant_files]


def test_known_target_context_overload_boundaries_do_not_collide_in_cache(tmp_path):
    """The cache-key discriminator (member_id + line range) must keep two
    real overload bodies distinct - never one clobbering the other's cache
    entry."""
    from kriya.workflow.context_source import SourceDerivationCache

    content = (
        "public class Owner {\n"
        "    public String format(String x) { return \"ONE_ARG\"; }\n"
        "    public String format(String x, String y) { return \"TWO_ARG\"; }\n"
        "}\n"
    )
    _write(str(tmp_path), "Owner.java", content)
    cache = SourceDerivationCache()

    _rendered, package = build_known_target_context(
        ["Owner.java"], str(tmp_path), str(tmp_path), 100000,
        member_hints={"Owner.java": "Owner.format"}, cache=cache,
    )

    member_items = [i for i in package.relevant_files if i.member_id == "Owner.format"]
    assert len(member_items) == 2
    contents = {i.content for i in member_items}
    assert any("ONE_ARG" in c for c in contents)
    assert any("TWO_ARG" in c for c in contents)


def test_member_hint_renamed_before_retry_cached_boundaries_do_not_survive(tmp_path):
    """Member-hint interaction (required scenario): attempt 1 resolves a
    real member; the member is renamed before a retry; the SAME
    SourceDerivationCache must not let a stale attempt-1 derivation for the
    OLD name leak into attempt 2's result - the resolver falls back
    conservatively (unsupported_structural_extraction) against CURRENT
    source, exactly as the uncached path already does."""
    from kriya.workflow.context_source import SourceDerivationCache

    _write(str(tmp_path), "Owner.py", "class Owner:\n    def old_name(self):\n        return 1\n")
    cache = SourceDerivationCache()

    build_known_target_context(
        ["Owner.py"], str(tmp_path), str(tmp_path), 100000,
        member_hints={"Owner.py": "Owner.old_name"}, cache=cache,
    )

    import time
    time.sleep(0.01)
    _write(str(tmp_path), "Owner.py", "class Owner:\n    def new_name(self):\n        return 1\n")
    os.utime(str(tmp_path / "Owner.py"), None)

    _rendered, package = build_known_target_context(
        ["Owner.py"], str(tmp_path), str(tmp_path), 100000,
        member_hints={"Owner.py": "Owner.old_name"}, cache=cache,
    )

    assert not any(i.member_id == "Owner.old_name" for i in package.relevant_files)
    assert any(o["reason"] == "unsupported_structural_extraction" for o in package.omitted)


def test_source_derivation_cache_performance_counters_report_reuse(tmp_path):
    """Deterministic instrumentation, not wall-clock: hits/misses must
    concretely demonstrate reuse on an unchanged repeated call."""
    from kriya.workflow.context_source import SourceDerivationCache

    _write(str(tmp_path), "a.py", "x = 1\n" * 500)
    cache = SourceDerivationCache()

    build_code_context_package(["a.py"], [], str(tmp_path), budget_limit=5, cache=cache)
    first_misses = cache.derivation_misses
    first_reads = cache.content_reads

    build_code_context_package(["a.py"], [], str(tmp_path), budget_limit=5, cache=cache)

    assert cache.derivation_misses == first_misses  # no NEW misses - fully reused
    assert cache.derivation_hits > 0
    assert cache.content_reads > first_reads  # a read attempt still happens...
    assert cache.content_read_hits > 0  # ...but is itself a cache hit (mtime unchanged)
