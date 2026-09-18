"""CTX-001 P1 WP4 (CurrentSourceResolver) + WP5 (member-level boundaries)."""
import os

from kriya.workflow.context_source import (
    CurrentSourceResolver,
    boundaries_matching_member_id,
    extract_member_body,
    java_member_boundaries,
    member_boundaries_for,
    member_ids_matching_name,
    parse_controlled_chunk_header_name,
    python_member_boundaries,
    python_member_ranges,
    resolve_member_hints_from_chunk_header,
    resolve_member_hints_from_failure_location,
    resolve_member_hints_from_search_evidence,
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


# --- CTX-001 P1 C2 production integration: member-hint resolvers -----------
# docs/assurance/CTX_001_P1_ARCHITECTURE.md section 25. These tests use the
# REAL kriya/analyzer/analyzer.py::chunk_file_with_metadata_headers() output
# wherever practical (not a hand-authored approximation of its header
# format), so a real drift in that function's own header shape would break
# these tests too - not silently stop working in production.

def _java_method_chunk(content: str, method_name: str) -> str:
    from kriya.analyzer.analyzer import chunk_file_with_metadata_headers
    chunks = chunk_file_with_metadata_headers(content, "Owner.java")
    for c in chunks:
        if f"Method: {method_name}\n" in c["text"]:
            return c["text"]
    raise AssertionError(f"no real chunk found for method {method_name}")


def _python_chunk(content: str, path: str, marker: str) -> str:
    from kriya.analyzer.analyzer import chunk_file_with_metadata_headers
    chunks = chunk_file_with_metadata_headers(content, path)
    for c in chunks:
        if marker in c["text"]:
            return c["text"]
    raise AssertionError(f"no real chunk found containing {marker!r}")


def test_parse_controlled_header_java_method_chunk_from_real_chunker():
    content = (
        "public class Owner {\n"
        "    public String format(String x) { return x; }\n"
        "}\n"
    )
    chunk_text = _java_method_chunk(content, "format")
    assert parse_controlled_chunk_header_name(chunk_text) == "format"


def test_parse_controlled_header_python_method_chunk_from_real_chunker():
    content = (
        "class StandardInvoiceCalculator:\n"
        "    def calculate_total(self, items):\n"
        "        return sum(items)\n"
    )
    chunk_text = _python_chunk(content, "invoice_impl.py", "Method: calculate_total")
    assert parse_controlled_chunk_header_name(chunk_text) == "calculate_total"


def test_parse_controlled_header_class_chunk_from_real_chunker():
    content = "class StandardInvoiceCalculator:\n    \"\"\"doc\"\"\"\n    pass\n"
    chunk_text = _python_chunk(content, "invoice_interface.py", "Class Declaration")
    assert parse_controlled_chunk_header_name(chunk_text) == "StandardInvoiceCalculator"


def test_parse_controlled_header_malformed_returns_none():
    assert parse_controlled_chunk_header_name("just some random text\nwith no header at all\n") is None
    assert parse_controlled_chunk_header_name("") is None
    assert parse_controlled_chunk_header_name(None) is None


def test_parse_controlled_header_ignores_arbitrary_source_prose_mentioning_method():
    """A chunk whose BODY (not header) happens to contain the literal text
    'Method: something' (a comment, a string literal, ...) far past the
    controlled header region must not be mistaken for a real header."""
    padding = "\n".join(f"line {i}" for i in range(20))
    text = f"File: x.py\nModule: x\n{padding}\n// Method: fake_injected_name\n"
    assert parse_controlled_chunk_header_name(text) is None


def test_resolve_member_hints_from_chunk_header_java_method():
    content = (
        "public class Owner {\n"
        "    public String format(String x) { return x; }\n"
        "}\n"
    )
    chunk_text = _java_method_chunk(content, "format")
    hints = resolve_member_hints_from_chunk_header("Owner.java", content, chunk_text)
    assert [h.member_id for h in hints] == ["Owner.format"]
    assert hints[0].provenance == "vector_chunk_header"


def test_resolve_member_hints_from_chunk_header_python_method():
    content = (
        "class StandardInvoiceCalculator:\n"
        "    def calculate_total(self, items):\n"
        "        return sum(items)\n"
    )
    chunk_text = _python_chunk(content, "invoice_impl.py", "Method: calculate_total")
    hints = resolve_member_hints_from_chunk_header("invoice_impl.py", content, chunk_text)
    assert [h.member_id for h in hints] == ["StandardInvoiceCalculator.calculate_total"]


def test_resolve_member_hints_from_chunk_header_stale_indexed_member_gone_from_current_source():
    """The chunk was indexed against an OLDER revision naming a method that
    no longer exists in the CURRENT worktree content - no hint, never a
    fabricated one."""
    indexed_content = "class Owner:\n    def old_method(self):\n        pass\n"
    current_content = "class Owner:\n    def renamed_method(self):\n        pass\n"
    chunk_text = _python_chunk(indexed_content, "owner.py", "Method: old_method")
    hints = resolve_member_hints_from_chunk_header("owner.py", current_content, chunk_text)
    assert hints == []


def test_resolve_member_hints_from_chunk_header_malformed_header_produces_no_hint():
    content = "class Owner:\n    def method(self):\n        pass\n"
    hints = resolve_member_hints_from_chunk_header("owner.py", content, "not a real chunk header at all")
    assert hints == []


def test_resolve_member_hints_from_chunk_header_unsupported_language_produces_no_hint():
    content = "func main() {}\n"
    chunk_text = "File: main.go\nMethod: main\n=== Method Body ===\nfunc main() {}\n"
    hints = resolve_member_hints_from_chunk_header("main.go", content, chunk_text)
    assert hints == []


def test_resolve_member_hints_from_chunk_header_ambiguous_java_overload_retains_all():
    content = (
        "public class Owner {\n"
        "    public String format(String x) { return x; }\n"
        "    public String format(String x, String y) { return x + y; }\n"
        "}\n"
    )
    chunk_text = _java_method_chunk(content, "format")
    hints = resolve_member_hints_from_chunk_header("Owner.java", content, chunk_text)
    # A bare name cannot distinguish the two overloads - conservative
    # behavior is one DISTINCT member_id (both overloads share it); the
    # boundary-expansion step (build_known_target_context, tested
    # separately) is what actually retains BOTH real bodies.
    assert [h.member_id for h in hints] == ["Owner.format"]
    boundaries = java_member_boundaries(content)
    assert len(boundaries_matching_member_id(boundaries, "Owner.format")) == 2


def test_resolve_member_hints_from_chunk_header_multiple_members_same_file():
    content = (
        "class Owner:\n"
        "    def method_a(self):\n"
        "        pass\n\n"
        "    def method_b(self):\n"
        "        pass\n"
    )
    chunk_a = _python_chunk(content, "owner.py", "Method: method_a")
    chunk_b = _python_chunk(content, "owner.py", "Method: method_b")
    hints_a = resolve_member_hints_from_chunk_header("owner.py", content, chunk_a)
    hints_b = resolve_member_hints_from_chunk_header("owner.py", content, chunk_b)
    assert {h.member_id for h in hints_a} == {"Owner.method_a"}
    assert {h.member_id for h in hints_b} == {"Owner.method_b"}


def test_resolve_member_hints_from_failure_location_java_line_inside_method():
    content = (
        "public class Owner {\n"
        "    public String format(String x) {\n"
        "        return x;\n"
        "    }\n"
        "}\n"
    )
    hints = resolve_member_hints_from_failure_location("Owner.java", content, 3)
    assert [h.member_id for h in hints] == ["Owner.format"]
    assert hints[0].provenance == "failure_location"


def test_resolve_member_hints_from_failure_location_python_line_inside_method():
    content = "class Owner:\n    def method(self):\n        return 1\n"
    hints = resolve_member_hints_from_failure_location("owner.py", content, 3)
    assert [h.member_id for h in hints] == ["Owner.method"]


def test_resolve_member_hints_from_failure_location_line_outside_any_member():
    """Module-level code (an import statement, before any class/function
    declaration) is not inside any structural member boundary at all -
    the class declaration LINE ITSELF is inside its own class boundary
    (member_id="Owner"), so this needs genuinely pre-declaration content to
    exercise "outside every boundary", not just "outside a method"."""
    content = "import os\n\nclass Owner:\n    def method(self):\n        return 1\n"
    hints = resolve_member_hints_from_failure_location("owner.py", content, 1)
    assert hints == []


def test_resolve_member_hints_from_failure_location_stale_out_of_range_line():
    content = "class Owner:\n    def method(self):\n        return 1\n"
    hints = resolve_member_hints_from_failure_location("owner.py", content, 9999)
    assert hints == []


def test_resolve_member_hints_from_failure_location_prefers_most_specific_containing_boundary():
    """A line inside a method is ALSO inside that method's enclosing class
    range - the method (more specific) must win, not the class."""
    content = "class Owner:\n    def method(self):\n        return 1\n"
    hints = resolve_member_hints_from_failure_location("owner.py", content, 3)
    assert [h.member_id for h in hints] == ["Owner.method"]


def test_resolve_member_hints_from_failure_location_unsupported_language():
    hints = resolve_member_hints_from_failure_location("main.go", "func main() {}\n", 1)
    assert hints == []


def test_resolve_member_hints_from_failure_location_worktree_version_b_not_workspace_version_a():
    """Callers always pass CurrentSourceResolver-sourced content - proving
    here that a stale (workspace) copy's member boundaries are never what
    gets used when a fresh (worktree) copy differs."""
    workspace_content = "class Owner:\n    def method(self):\n        return 1\n"
    worktree_content = (
        "class Owner:\n"
        "    def method(self):\n"
        "        return 1\n\n"
        "    def new_method(self):\n"
        "        return 2\n"
    )
    # line 6 only exists/means something in the WORKTREE version.
    hints_against_worktree = resolve_member_hints_from_failure_location("owner.py", worktree_content, 6)
    hints_against_workspace = resolve_member_hints_from_failure_location("owner.py", workspace_content, 6)
    assert [h.member_id for h in hints_against_worktree] == ["Owner.new_method"]
    assert hints_against_workspace == []


# --- CTX-001-P1-C3: resolve_member_hints_from_search_evidence --------------
#
# SOURCE 3 - a rejected anchored-edit's own SEARCH text, deterministically
# grounded against real current structure. Added after VAL-001 G1's
# post-remediation rerun (run d756a833) proved SOURCE 1/2 above can both be
# silent for an entire run at once. No Graphify production source is copied
# anywhere in this file (same convention as test_val001_g1_remediation.py) -
# every fixture below is synthetic, built to mirror the *structural shape*
# G1 exercised (a large module containing a nested member whose distinctive
# vocabulary appears nowhere else), never real content.

def _g1_shaped_python_module() -> str:
    """A large Python module (mirrors graphify/extractors/engine.py's real
    shape: one big nested-closure member handling several unrelated
    languages inside a single outer function) containing a nested member
    whose C#-related vocabulary is genuinely distinctive - it appears
    nowhere else in the file, exactly like the real `_extract_generic.
    walk_calls`/`fn_node`/`generic_name` shape G1's rerun forensics found."""
    padding_before = "\n".join(f"def unrelated_helper_{i}(value):\n    return value * {i}\n" for i in range(40))
    return f'''"""Synthetic large module - structural shape only, no real Graphify content."""
{padding_before}

def outer_extractor(nodes, config):
    def add_node(node_id):
        return node_id

    def walk_calls(node, config, source):
        callee_name = None
        is_member_call = False
        fn_node = node.child_by_field_name("function")
        if fn_node is not None and fn_node.type == "identifier":
            callee_name = read_text(fn_node, source)
        elif fn_node is not None and fn_node.type == "member_access_expression":
            mname = fn_node.child_by_field_name("name")
            if mname is not None:
                callee_name = read_text(mname, source)
                is_member_call = True
        return callee_name, is_member_call

    for node in nodes:
        walk_calls(node, config, node.source)
    return add_node
'''


def test_search_evidence_grounds_g1_shaped_nested_member_uniquely():
    """The core CTX-001-P1-C3 regression fixture: a rejected SEARCH block
    referencing real, distinctive vocabulary from ONE nested member inside
    a large module - must ground to exactly that member, not the enclosing
    outer function, not any of the unrelated padding helpers."""
    content = _g1_shaped_python_module()
    search_text = (
        'if fn_node is not None and fn_node.type == "identifier":\n'
        "    callee_name = read_text(fn_node, source)\n"
        'elif fn_node is not None and fn_node.type == "member_access_expression":\n'
        '    mname = fn_node.child_by_field_name("name")\n'
        "    callee_name = read_text(mname, source)\n"
        "    is_member_call = True"
    )
    hints = resolve_member_hints_from_search_evidence("engine.py", content, search_text)
    assert [h.member_id for h in hints] == ["outer_extractor.walk_calls"]
    assert hints[0].provenance == "search_token_containment"


def test_search_evidence_hallucinated_variable_names_fail_closed():
    """Mirrors G1 rerun attempts 4/7: the model invents plausible-looking
    local variable names that do not exist anywhere in the real file -
    containment can never be satisfied, so this must return no hint rather
    than a nearest-guess."""
    content = _g1_shaped_python_module()
    search_text = (
        'if fn_node is not None:\n'
        '    if fn_node.type == "generic_name":\n'
        '        name_child = fn_node.child_by_field_name("name")\n'
        "        callee_name = read_text(name_child, source)"
    )
    hints = resolve_member_hints_from_search_evidence("engine.py", content, search_text)
    assert hints == []


def test_search_evidence_sole_exact_member_name_grounds_directly():
    content = "def unique_target_symbol(x):\n    return x + 1\n\ndef other_symbol(y):\n    return y - 1\n"
    hints = resolve_member_hints_from_search_evidence("m.py", content, "unique_target_symbol")
    assert [h.member_id for h in hints] == ["unique_target_symbol"]
    assert hints[0].provenance == "search_symbol_reference"


def test_search_evidence_reference_to_called_member_does_not_ground_alone():
    """A SEARCH block editing one member (calculate_total) that merely
    CALLS a different, real member (apply_discount) by name must not
    ground to the called member - only joint containment across every
    distinctive token (which only calculate_total's own body satisfies)
    may ground it. Found live via this exact synthetic case during
    CTX-001-P1-C3 development - an earlier version of the rule grounded
    to apply_discount instead."""
    content = (
        "def calculate_total(items):\n"
        "    running_subtotal = 0\n"
        "    for entry in items:\n"
        "        running_subtotal += entry.unit_price\n"
        "    return apply_discount(running_subtotal)\n"
        "\n"
        "def apply_discount(running_subtotal):\n"
        "    if running_subtotal > 1000:\n"
        "        return running_subtotal * 0.9\n"
        "    return running_subtotal\n"
    )
    search_text = (
        "running_subtotal = 0\n"
        "for entry in items:\n"
        "    running_subtotal += entry.unit_price\n"
        "return apply_discount(running_subtotal)"
    )
    hints = resolve_member_hints_from_search_evidence("m.py", content, search_text)
    assert [h.member_id for h in hints] == ["calculate_total"]


def test_search_evidence_ambiguous_sibling_members_return_no_hint():
    """Two unrelated (non-nested) members whose bodies both happen to
    contain the same distinctive tokens - genuine ambiguity, must return no
    hint rather than an arbitrary pick between them."""
    content = (
        "def alpha_handler(raw_token):\n"
        "    distinctive_marker_one = raw_token\n"
        "    distinctive_marker_two = raw_token\n"
        "    return distinctive_marker_one + distinctive_marker_two\n"
        "\n"
        "def beta_handler(raw_token):\n"
        "    distinctive_marker_one = raw_token\n"
        "    distinctive_marker_two = raw_token\n"
        "    return distinctive_marker_two\n"
    )
    hints = resolve_member_hints_from_search_evidence(
        "m.py", content, "distinctive_marker_one and distinctive_marker_two together"
    )
    assert hints == []


def test_search_evidence_generic_common_tokens_alone_return_no_hint():
    """Only stoplisted/short/pervasive words survive tokenization - no
    distinctive evidence at all, must return no hint (never fabricate one
    from generic vocabulary common to nearly every member)."""
    content = "def f(self, node, name):\n    text = node\n    return text\n\ndef g(self, node, name):\n    text = node\n    return text\n"
    hints = resolve_member_hints_from_search_evidence("m.py", content, "self node name text value data")
    assert hints == []


def test_search_evidence_partial_overlap_never_wins_over_no_full_match():
    """A boundary sharing MOST but not ALL distinctive tokens must never be
    selected - this module implements no scoring/ranking at all, so a
    'highest overlap' candidate that isn't a COMPLETE, unique containment
    match returns no hint, not a best-effort pick."""
    content = (
        "def close_but_not_complete(alpha_token, beta_token):\n"
        "    combined = alpha_token + beta_token\n"
        "    return combined\n"
        "\n"
        "def unrelated(gamma_token):\n"
        "    return gamma_token\n"
    )
    # gamma_distinctive_token appears NOWHERE in the file - no boundary can
    # ever satisfy full containment, even though close_but_not_complete
    # shares 2 of the 3 distinctive tokens.
    hints = resolve_member_hints_from_search_evidence(
        "m.py", content, "alpha_token beta_token gamma_distinctive_token"
    )
    assert hints == []


def test_search_evidence_unsupported_language_returns_no_hint():
    hints = resolve_member_hints_from_search_evidence(
        "Program.cs", 'void Get<T>(string key) { return default(T); }', "generic_name callee_name"
    )
    assert hints == []


def test_search_evidence_empty_search_text_returns_no_hint():
    content = "def f():\n    return 1\n"
    assert resolve_member_hints_from_search_evidence("m.py", content, "") == []
    assert resolve_member_hints_from_search_evidence("m.py", content, "   \n  ") == []


def test_search_evidence_java_nested_call_grounds_to_calling_method_not_callee():
    """Non-Python language case (Java, existing java_member_boundaries) -
    proves the rule generalizes without any per-language code: a rejected
    SEARCH block editing calculateTotal()'s own loop body, which CALLS a
    real but different method applyDiscountSchedule() by name, must ground
    to calculateTotal (the member whose body the evidence jointly belongs
    to), never the called method."""
    content = (
        "public class InvoiceProcessor {\n"
        "    public double calculateTotal(java.util.List<LineItem> items) {\n"
        "        double runningSubtotal = 0.0;\n"
        "        for (LineItem entry : items) {\n"
        "            runningSubtotal += entry.getUnitPrice() * entry.getQuantity();\n"
        "        }\n"
        "        return applyDiscountSchedule(runningSubtotal);\n"
        "    }\n"
        "\n"
        "    private double applyDiscountSchedule(double runningSubtotal) {\n"
        "        if (runningSubtotal > 1000) {\n"
        "            return runningSubtotal * 0.9;\n"
        "        }\n"
        "        return runningSubtotal;\n"
        "    }\n"
        "}\n"
    )
    search_text = (
        "double runningSubtotal = 0.0;\n"
        "for (LineItem entry : items) {\n"
        "    runningSubtotal += entry.getUnitPrice() * entry.getQuantity();\n"
        "}\n"
        "return applyDiscountSchedule(runningSubtotal);"
    )
    hints = resolve_member_hints_from_search_evidence("InvoiceProcessor.java", content, search_text)
    assert [h.member_id for h in hints] == ["InvoiceProcessor.calculateTotal"]


def test_search_evidence_candidate_never_carries_source_text():
    """Structural proof of the discovery-vs-authority separation: a
    MemberHintCandidate has no field capable of carrying source content at
    all - only member_id (a name) and provenance (a string label). The
    caller must always re-derive real content via extract_member_body()
    against current_content, never from anything this function returns."""
    content = "def real_target(x):\n    return x\n"
    hints = resolve_member_hints_from_search_evidence("m.py", content, "real_target")
    assert hints
    field_names = set(hints[0].__dataclass_fields__.keys())
    assert field_names == {"member_id", "provenance"}
