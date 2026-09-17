"""CTX-001 P1 WP4 (CurrentSourceResolver) + WP5 (member-level boundaries)."""
import os

from kriya.workflow.context_source import (
    CurrentSourceResolver,
    extract_member_body,
    java_member_boundaries,
    member_boundaries_for,
    python_member_boundaries,
    python_member_ranges,
)


# --- WP4: CurrentSourceResolver ---------------------------------------------

def _write(root, relpath, content):
    full = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)


def test_resolver_unchanged_file_reads_from_worktree(tmp_path):
    workspace = tmp_path / "workspace"
    worktree = tmp_path / "worktree"
    _write(str(workspace), "Target.java", "VERSION_A")
    _write(str(worktree), "Target.java", "VERSION_A")

    resolver = CurrentSourceResolver(str(workspace), str(worktree))
    result = resolver.resolve("Target.java")

    assert result.status == "current"
    assert result.content == "VERSION_A"
    assert result.root_used == str(worktree)


def test_resolver_modified_worktree_file_never_returns_stale_workspace_content(tmp_path):
    """The CTX-001-P0 C3/F9 adversarial case: same relpath, different
    workspace vs. worktree content - the resolver must return ONLY the
    worktree's (current) content, never the workspace's (stale) one."""
    workspace = tmp_path / "workspace"
    worktree = tmp_path / "worktree"
    _write(str(workspace), "Target.java", "VERSION_A")
    _write(str(worktree), "Target.java", "VERSION_B")

    resolver = CurrentSourceResolver(str(workspace), str(worktree))
    result = resolver.resolve("Target.java")

    assert result.content == "VERSION_B"
    assert "VERSION_A" not in (result.content or "")
    assert result.status == "current"


def test_resolver_newly_created_worktree_file(tmp_path):
    workspace = tmp_path / "workspace"
    worktree = tmp_path / "worktree"
    os.makedirs(str(workspace))
    _write(str(worktree), "New.java", "brand new content")

    resolver = CurrentSourceResolver(str(workspace), str(worktree))
    result = resolver.resolve("New.java")

    assert result.status == "current"
    assert result.content == "brand new content"


def test_resolver_deleted_file_is_not_silently_shown_from_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    worktree = tmp_path / "worktree"
    _write(str(workspace), "Gone.java", "old content, must not resurface")
    os.makedirs(str(worktree))

    resolver = CurrentSourceResolver(str(workspace), str(worktree))
    result = resolver.resolve("Gone.java")

    assert result.status == "deleted"
    assert result.content is None
    assert result.exists is False


def test_resolver_unavailable_when_missing_from_both_roots(tmp_path):
    workspace = tmp_path / "workspace"
    worktree = tmp_path / "worktree"
    os.makedirs(str(workspace))
    os.makedirs(str(worktree))

    resolver = CurrentSourceResolver(str(workspace), str(worktree))
    result = resolver.resolve("NeverExisted.java")

    assert result.status == "unavailable"
    assert result.content is None


def test_resolver_no_worktree_yet_uses_workspace(tmp_path):
    """Before create_git_worktree() has run (attempt-1's own Graph RAG
    retrieval window), worktree_path is None/unset - the resolver falls
    back to workspace_path, the correct root for that exact window."""
    workspace = tmp_path / "workspace"
    _write(str(workspace), "Target.java", "only workspace copy")

    resolver = CurrentSourceResolver(str(workspace), None)
    result = resolver.resolve("Target.java")

    assert result.status == "current"
    assert result.content == "only workspace copy"
    assert result.root_used == str(workspace)


def test_resolver_revision_changes_with_content(tmp_path):
    workspace = tmp_path / "workspace"
    worktree = tmp_path / "worktree"
    _write(str(workspace), "Target.java", "VERSION_A")
    _write(str(worktree), "Target.java", "VERSION_A")

    resolver = CurrentSourceResolver(str(workspace), str(worktree))
    revision_a = resolver.resolve("Target.java").revision

    _write(str(worktree), "Target.java", "VERSION_B")
    revision_b = resolver.resolve("Target.java").revision

    assert revision_a != revision_b


def test_resolver_stale_known_revision_hint_is_never_trusted(tmp_path):
    """A caller-supplied known_revisions hint that doesn't match the
    ACTUALLY-read content must never be silently trusted - the resolver
    always returns the REAL, freshly-computed revision, and flags
    stale_hint so a caller can record a stale_revision_rejected omission."""
    workspace = tmp_path / "workspace"
    worktree = tmp_path / "worktree"
    _write(str(workspace), "Target.java", "VERSION_A")
    _write(str(worktree), "Target.java", "VERSION_A")

    resolver = CurrentSourceResolver(
        str(workspace), str(worktree), known_revisions={"Target.java": "deliberately-wrong-hash"},
    )
    result = resolver.resolve("Target.java")

    assert result.stale_hint is True
    assert result.revision != "deliberately-wrong-hash"
    assert result.content == "VERSION_A"


def test_resolver_matching_known_revision_hint_is_not_flagged_stale(tmp_path):
    from kriya.workflow.edit_safety import content_revision

    workspace = tmp_path / "workspace"
    worktree = tmp_path / "worktree"
    _write(str(workspace), "Target.java", "VERSION_A")
    _write(str(worktree), "Target.java", "VERSION_A")
    real_revision = content_revision("VERSION_A")

    resolver = CurrentSourceResolver(
        str(workspace), str(worktree), known_revisions={"Target.java": real_revision},
    )
    result = resolver.resolve("Target.java")

    assert result.stale_hint is False
    assert result.revision == real_revision


# --- WP5: member boundaries --------------------------------------------------

def test_python_member_ranges_single_class_and_method():
    content = (
        "class StandardInvoiceCalculator:\n"
        "    def calculate_total(self, items):\n"
        "        return sum(items)\n"
    )
    ranges = python_member_ranges(content)

    assert "StandardInvoiceCalculator" in ranges
    assert "StandardInvoiceCalculator.calculate_total" in ranges
    method_start, method_end = ranges["StandardInvoiceCalculator.calculate_total"]
    assert method_start == 2
    assert method_end == 3


def test_python_member_ranges_top_level_function():
    content = "def helper(x):\n    return x + 1\n"
    ranges = python_member_ranges(content)
    assert ranges["helper"] == (1, 2)


def test_python_member_ranges_async_method():
    content = (
        "class Service:\n"
        "    async def fetch(self):\n"
        "        return await self.client.get()\n"
    )
    ranges = python_member_ranges(content)
    assert "Service.fetch" in ranges


def test_python_member_ranges_decorator_does_not_lose_declaration_start():
    content = (
        "class Service:\n"
        "    @staticmethod\n"
        "    @cached\n"
        "    def compute(x):\n"
        "        return x\n"
    )
    ranges = python_member_ranges(content)
    start, end = ranges["Service.compute"]
    # The decorator lines (2-3) must be included in the member's own range,
    # not just the "def" line (4).
    assert start == 2
    assert end == 5


def test_python_member_ranges_multiple_members_in_one_file():
    content = (
        "class A:\n"
        "    def one(self):\n"
        "        pass\n\n"
        "class B:\n"
        "    def two(self):\n"
        "        pass\n"
    )
    ranges = python_member_ranges(content)
    assert {"A", "A.one", "B", "B.two"} <= set(ranges.keys())


def test_python_member_ranges_conservative_nested_declaration():
    """A def nested inside an `if` block is not independently addressable -
    it stays inside its enclosing member's own body range, never fabricated
    as its own separate unit (conservative nesting, WP5's own requirement)."""
    content = (
        "def outer():\n"
        "    if True:\n"
        "        def inner():\n"
        "            pass\n"
        "    return None\n"
    )
    ranges = python_member_ranges(content)
    assert "outer" in ranges
    assert "outer.inner" not in ranges
    assert "inner" not in ranges


def test_python_member_ranges_direct_nested_class_is_addressable():
    content = (
        "class Outer:\n"
        "    class Inner:\n"
        "        def method(self):\n"
        "            pass\n"
    )
    ranges = python_member_ranges(content)
    assert "Outer.Inner" in ranges
    assert "Outer.Inner.method" in ranges


def test_python_member_ranges_invalid_syntax_returns_empty_not_fabricated():
    assert python_member_ranges("class (((not valid") == {}


def test_java_member_boundaries_reuses_extract_java_members():
    content = (
        "public class Formatter {\n"
        "    public String format(String x) {\n"
        "        return x;\n"
        "    }\n"
        "}\n"
    )
    boundaries = java_member_boundaries(content)
    assert any(b.member_id == "Formatter.format" for b in boundaries)


def test_member_boundaries_for_unsupported_language_is_none_not_empty():
    """None (not []) is the honest "no extractor for this language" signal -
    distinguishable from a genuinely supported language with zero members."""
    assert member_boundaries_for("main.go", "func main() {}\n") is None


def test_member_boundaries_for_python_file_with_no_members_is_empty_list():
    assert member_boundaries_for("script.py", "x = 1\ny = 2\n") == []


def test_extract_member_body_returns_exact_line_range():
    content = "line1\nline2\nline3\nline4\nline5\n"
    assert extract_member_body(content, 2, 4) == "line2\nline3\nline4"


def test_extract_member_body_clamps_out_of_range_lines():
    content = "line1\nline2\n"
    assert extract_member_body(content, 1, 100) == "line1\nline2"
