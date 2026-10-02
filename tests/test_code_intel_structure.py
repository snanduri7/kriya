"""Code Intelligence R1: the tree-sitter structural symbol model.

Covers the dependency smoke (one Java and one Python file), the Java and
Python constructs the model must carry, parse states, and the E-02/E-09
regressions through the dependency graph that indexes the model.
"""
import subprocess
import sys
import textwrap

import pytest

from kriya.analyzer.graph import DependencyGraph
from kriya.code_intel import parsing
from kriya.code_intel.model import ParseState
from kriya.code_intel.parsing import parse_file, parse_text, parser_identity, python_module_name

JAVA = textwrap.dedent("""\
    package org.example.shop;

    import java.util.List;
    import java.util.Map;
    import static java.util.Objects.requireNonNull;

    /** Prices orders. */
    @Service
    public final class PriceService<T extends Comparable<T>> extends BaseService implements Pricing, Auditable<T> {
        @Autowired
        private final Map<String, Integer> rates, overrides;
        static final int LIMIT = 10;

        PriceService(int limit) {
            this.limit = limit;
        }

        public PriceService(String name, int... limits) {
        }

        @Override
        public <R> List<R> price(
                List<T> items,
                Map<String, R> table) throws java.io.IOException {
            return null;
        }

        int price(int amount) { return amount; }

        void reset() {}

        record Quote(String id, long cents) {
            Quote {
                requireNonNull(id);
            }
        }

        enum Tier {
            GOLD, SILVER;
            int discount() { return 1; }
        }

        interface Listener { void changed(); }
    }
    """)

PYTHON = textwrap.dedent('''\
    """Module docs."""
    import os
    from typing import Optional

    LIMIT: int = 3


    @cached
    async def fetch(url):
        """Fetch one url."""
        def parse(body):
            return body
        return parse(url)


    class Client(Base, pkg.Mixin):
        timeout = 5

        @property
        def name(self):
            return "c"

        async def send(self, request):
            pass

        class Options:
            pass
    ''')


def _by_key(structure):
    return {symbol.lookup_key: symbol for symbol in structure.symbols}


def test_dependency_smoke_parses_one_java_and_one_python_file():
    java = parse_text("src/main/java/org/example/shop/PriceService.java", JAVA)
    python = parse_text("pkg/client.py", PYTHON)
    assert java.state is ParseState.PARSED and java.language == "java"
    assert python.state is ParseState.PARSED and python.language == "python"
    assert java.symbols and python.symbols
    assert java.parser_digest == python.parser_digest == parser_identity().digest


def test_parser_identity_names_every_component_and_the_safe_tree_sitter_line():
    identity = parser_identity()
    assert identity.structural_parser == parsing.STRUCTURAL_PARSER_VERSION
    assert "unavailable" not in (identity.tree_sitter, identity.java_grammar, identity.python_grammar)
    # tree-sitter 0.26.0 corrupts memory on CPython 3.14 (pyproject pin).
    assert identity.tree_sitter.startswith("0.25.")


def test_reading_node_points_across_many_trees_does_not_crash_the_interpreter():
    """The measured 0.26.0 failure: Node.start_point/end_point read across
    trees segfaulted at exit (5/5). Runs in a subprocess so a crash is an
    exit code, not a dead test worker."""
    script = textwrap.dedent("""
        import tree_sitter, tree_sitter_java
        language = tree_sitter.Language(tree_sitter_java.language())
        source = b"class A { void f() { g(); } int x; }\\n" * 200
        for _ in range(40):
            tree = tree_sitter.Parser(language).parse(source)
            stack = [tree.root_node]
            while stack:
                node = stack.pop()
                node.start_point.row; node.end_point.column
                stack.extend(node.children)
        print("ok")
    """)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0 and result.stdout.strip() == "ok", result.stderr[-2000:]


def test_java_model_carries_types_members_fields_and_annotations():
    structure = parse_text("src/main/java/org/example/shop/PriceService.java", JAVA)
    keys = _by_key(structure)
    service = keys["org.example.shop.PriceService"]
    assert structure.namespace == "org.example.shop"
    assert structure.imports == ("java.util.List", "java.util.Map", "static java.util.Objects.requireNonNull")
    assert service.kind == "class" and service.modifiers == ("public", "final")
    assert service.annotations == ("Service",) and service.doc_summary == "Prices orders."
    assert service.extends == ("BaseService",) and service.implements == ("Pricing", "Auditable")
    assert service.declaration.start_line == 8 and service.signature.start_line == 9
    assert keys["org.example.shop.PriceService.Quote"].kind == "record"
    assert keys["org.example.shop.PriceService.Tier"].kind == "enum"
    assert keys["org.example.shop.PriceService.Listener"].kind == "interface"
    assert keys["org.example.shop.PriceService.Tier.GOLD"].kind == "enum_constant"
    assert keys["org.example.shop.PriceService.Tier.discount"].kind == "method"
    rates, overrides = keys["org.example.shop.PriceService.rates"], keys["org.example.shop.PriceService.overrides"]
    assert rates.kind == overrides.kind == "field" and rates.annotations == ("Autowired",)
    assert rates.return_type == "Map<String, Integer>" and rates.modifiers == ("private", "final")
    assert keys["org.example.shop.PriceService.Quote.cents"].return_type == "long"
    for symbol in structure.symbols:
        assert symbol.source_digest == structure.source_digest
        assert symbol.symbol_id.startswith("java:src/main/java/org/example/shop/PriceService.java#")


def test_java_overloads_constructors_and_package_private_members_get_distinct_ids_and_spans():
    structure = parse_text("PriceService.java", JAVA)
    callables = [s for s in structure.symbols if s.is_callable]
    ids = [s.symbol_id for s in callables]
    assert len(ids) == len(set(ids))
    price = [s for s in callables if s.name == "price"]
    assert {s.parameter_types for s in price} == {("List<T>", "Map<String, R>"), ("int",)}
    generic = next(s for s in price if len(s.parameter_types) == 2)
    # multiline declaration: annotation line, signature over three lines, body.
    assert generic.declaration.start_line == 21 and generic.signature.start_line == 22
    assert generic.signature.end_line == 24 and generic.body.end_line == 26
    assert generic.return_type == "List<R>" and generic.annotations == ("Override",)
    constructors = [s for s in callables if s.kind == "constructor"]
    assert {s.parameter_types for s in constructors} == {("int",), ("String", "int..."), ("String", "long")}
    package_private = next(s for s in constructors if s.parameter_types == ("int",))
    assert package_private.modifiers == () and package_private.declaration.start_line == 14
    assert next(s for s in callables if s.name == "reset").modifiers == ()
    text = JAVA.encode()
    assert text[generic.body.start_byte:generic.body.end_byte].startswith(b"{")
    assert text[generic.body.start_byte:generic.body.end_byte].endswith(b"}")


def test_python_model_carries_async_nested_methods_decorators_attributes_and_docstrings():
    structure = parse_text("src/pkg/client.py", PYTHON)
    keys = _by_key(structure)
    assert structure.namespace == "pkg.client"
    assert structure.imports == ("import os", "from typing import Optional")
    fetch = keys["pkg.client.fetch"]
    assert fetch.kind == "function" and fetch.modifiers == ("async",) and fetch.annotations == ("cached",)
    assert fetch.declaration.start_line == 8 and fetch.signature.start_line == 9
    assert fetch.doc_summary == "Fetch one url."
    assert keys["pkg.client.fetch.parse"].kind == "function"
    assert keys["pkg.client.fetch.parse"].parent_id == fetch.symbol_id
    client = keys["pkg.client.Client"]
    assert client.kind == "class" and client.extends == ("Base", "pkg.Mixin")
    assert keys["pkg.client.Client.send"].kind == "method" and keys["pkg.client.Client.send"].modifiers == ("async",)
    assert keys["pkg.client.Client.name"].annotations == ("property",)
    assert keys["pkg.client.Client.timeout"].kind == "attribute"
    assert keys["pkg.client.LIMIT"].kind == "variable" and keys["pkg.client.LIMIT"].return_type == "int"
    assert keys["pkg.client.Client.Options"].parent_id == client.symbol_id


def test_python_module_names_are_lexical_paths():
    assert python_module_name("kriya/code_intel/parsing.py") == "kriya.code_intel.parsing"
    assert python_module_name("src/pkg/__init__.py") == "pkg"


def test_parse_states_are_typed_never_an_empty_success():
    broken = parse_text("Broken.java", "class Broken { void f( { }\n void g() {} }")
    assert broken.state is ParseState.PARTIALLY_PARSED and broken.error_count > 0
    assert parse_text("x.rb", "def f; end").state is ParseState.UNSUPPORTED
    assert parse_file("x.py", b"def f(:\n").state is ParseState.PARTIALLY_PARSED


def test_a_parser_failure_is_parse_failed_not_no_symbols(monkeypatch):
    def explode(language, data):
        raise RuntimeError("grammar load failed")
    monkeypatch.setattr(parsing, "_parse_tree", explode)
    structure = parse_text("A.java", "class A {}")
    assert structure.state is ParseState.PARSE_FAILED and "grammar load failed" in structure.detail
    assert structure.symbols == ()


def test_e02_graph_indexes_constructs_the_regex_parser_missed_with_real_spans():
    graph = DependencyGraph(":memory:")
    symbols, relations = graph._parse_java("PriceService.java", JAVA)
    by_name = {(s["name"], s["type"]): s for s in symbols}
    # final class, record, enum, nested interface: all regex misses (E-02).
    assert by_name[("org.example.shop.PriceService", "class")] == {
        "name": "org.example.shop.PriceService", "type": "class", "start_line": 8, "end_line": 44}
    assert ("org.example.shop.PriceService.Quote", "nested_record") in by_name
    assert ("org.example.shop.PriceService.Tier", "nested_enum") in by_name
    assert ("org.example.shop.PriceService.Listener", "nested_interface") in by_name
    # constructors (including package-private), the multiline generic method, the package-private method
    assert sum(1 for s in symbols if s["type"] == "constructor") == 3
    assert ("price", "method") in by_name and ("reset", "method") in by_name and ("discount", "method") in by_name
    assert {"source": "org.example.shop.PriceService", "target": "org.example.shop.BaseService",
            "type": "inherits"} in relations
    assert {"source": "PriceService.java", "target": "java.util.Map", "type": "imports"} in relations
    assert {"source": "PriceService.java", "target": "Map<String,Integer>", "type": "injects"} in relations
    assert {"source": "PriceService.java", "target": "requireNonNull", "type": "calls"} in relations


def test_e02_supertype_resolution_prefers_an_exact_import_over_the_package():
    graph = DependencyGraph(":memory:")
    _symbols, relations = graph._parse_java(
        "A.java", "package p;\nimport q.Base;\nimport r.*;\nclass A extends Base implements Other, s.Third {}\n")
    targets = {(r["type"], r["target"]) for r in relations if r["source"] == "p.A"}
    assert targets == {("inherits", "q.Base"), ("implements", "p.Other"), ("implements", "s.Third")}


def test_e02_only_top_level_types_count_for_the_duplicate_type_gate(tmp_path):
    graph = DependencyGraph(str(tmp_path / "g.db"))
    graph.index_file("a/One.java", "package a;\npublic final class One { static class Builder {} }\n", 1.0)
    graph.index_file("b/Two.java", "package b;\nrecord Two(int x) { static class Builder {} }\n", 1.0)
    locations = graph.get_class_symbol_locations()
    assert locations[".java:One"] == ["a/One.java"] and locations[".java:Two"] == ["b/Two.java"]
    assert ".java:Builder" not in locations
    assert graph.extract_class_names("c/E.java", "package c;\nenum E { A }\n") == [".java:E"]


def test_e09_async_functions_and_methods_are_indexed():
    graph = DependencyGraph(":memory:")
    symbols, _relations = graph._parse_python("client.py", PYTHON)
    names = {s["name"] for s in symbols if s["type"] == "function"}
    assert {"fetch", "send", "parse", "name"} <= names


@pytest.mark.parametrize("path", ["kriya/code_intel/parsing.py", "kriya/analyzer/graph.py"])
def test_kriya_sources_parse_cleanly(path):
    with open(path, "rb") as handle:
        structure = parse_file(path, handle.read())
    assert structure.state is ParseState.PARSED
    assert any(s.kind in ("function", "method") for s in structure.symbols)
