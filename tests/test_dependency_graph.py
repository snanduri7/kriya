from kriya.analyzer.graph import DependencyGraph


def test_dependency_graph_indexing(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    
    # 1. Test Python parsing and SQL insertion
    py_content = """
import sys
from kriya.core import Kernel

class MyEngine:
    def execute(self):
        self.evaluate()
        run_process()
"""
    graph.index_file("my_engine.py", py_content, 100.0)
    
    # Assert symbol entries
    conn = graph.db_path
    import sqlite3
    c = sqlite3.connect(conn)
    cursor = c.cursor()
    
    cursor.execute("SELECT name, type FROM symbols WHERE filepath = 'my_engine.py'")
    syms = cursor.fetchall()
    assert ("MyEngine", "class") in syms
    assert ("execute", "function") in syms
    
    # Check imports relation
    imports = graph.get_imports("my_engine.py")
    assert "sys" in imports
    assert "kriya.core" in imports
    
    # Check calls relation
    callers = graph.get_callers("evaluate")
    assert len(callers) > 0
    assert callers[0]["source"] == "my_engine.py"
    
    c.close()

def test_java_conventions_indexing(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    
    java_content = """
package com.example.service;
import com.example.model.User;

public class UserService {
    public void saveUser(User u) {
        userRepository.save(u);
    }
}
"""
    graph.index_file("UserService.java", java_content, 200.0)
    
    # Verify symbols
    import sqlite3
    c = sqlite3.connect(graph.db_path)
    cursor = c.cursor()
    cursor.execute("SELECT name FROM symbols WHERE type = 'class'")
    classes = [r[0] for r in cursor.fetchall()]
    assert "com.example.service.UserService" in classes
    
    # Verify save method reference call relation
    callers = graph.get_callers("save")
    assert len(callers) > 0
    assert callers[0]["source"] == "UserService.java"
    c.close()

def test_ruby_conventions_indexing(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    
    ruby_content = """
require 'rspec'
require_relative 'helper'

class OrderProcessor
  def calculate_tax(order)
    order.compute_total()
  end
end
"""
    graph.index_file("order_processor.rb", ruby_content, 300.0)
    
    # Assert imports
    imports = graph.get_imports("order_processor.rb")
    assert "rspec" in imports
    assert "helper" in imports
    
    # Assert calls
    callers = graph.get_callers("compute_total")
    assert len(callers) > 0
    assert callers[0]["source"] == "order_processor.rb"

def test_xml_bean_indexing(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    
    xml_content = """
<?xml version="1.0" encoding="UTF-8"?>
<beans>
    <bean id="myService" class="com.example.MyService" />
</beans>
"""
    graph.index_file("beans.xml", xml_content, 400.0)

    import sqlite3
    c = sqlite3.connect(graph.db_path)
    cursor = c.cursor()
    cursor.execute("SELECT name, type FROM symbols WHERE filepath = 'beans.xml'")
    syms = cursor.fetchall()
    assert ("myService", "spring_bean") in syms
    c.close()


def test_clear_file_does_not_delete_a_different_files_relations(tmp_path):
    """Regression test for a real bug found live, 2026-08-12 (SME
    architecture review): relations.source/target are bare, unqualified
    symbol names with no file scoping - clear_file() used to delete ANY
    relation matching a symbol name defined in the file being cleared, even
    one that actually belongs to a DIFFERENT file's same-named symbol (e.g.
    two Java files each defining a method called "handle"). Re-indexing (or
    clearing) one file silently corrupted the other, untouched file's
    relations too, with no error surfaced. Fixed by tracking which file's
    parse produced each relation row (source_file) and deleting by that
    instead of by name.

    Constructed directly at the data layer (not through a language parser)
    so the test is precise about exactly which collision it's proving fixed,
    independent of any one parser's symbol-naming quirks."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))

    cursor = graph.conn.cursor()
    cursor.execute(
        "INSERT INTO symbols (filepath, name, type, start_line, end_line) VALUES (?, ?, ?, ?, ?)",
        ("A.java", "handle", "function", 1, 5),
    )
    cursor.execute(
        "INSERT INTO symbols (filepath, name, type, start_line, end_line) VALUES (?, ?, ?, ?, ?)",
        ("B.java", "handle", "function", 1, 5),
    )
    # B's own relation, referencing "handle" as a call target - correctly
    # attributed to B via source_file.
    cursor.execute(
        "INSERT INTO relations (source, target, type, source_file) VALUES (?, ?, ?, ?)",
        ("B.trigger", "handle", "calls", "B.java"),
    )
    cursor.execute("INSERT INTO files (filepath, mtime, hash) VALUES (?, ?, ?)", ("A.java", 100.0, "h1"))
    cursor.execute("INSERT INTO files (filepath, mtime, hash) VALUES (?, ?, ?)", ("B.java", 200.0, "h2"))
    graph.conn.commit()

    graph.clear_file("A.java")

    callers = graph.get_callers("handle")
    assert len(callers) == 1
    assert callers[0]["source"] == "B.trigger"

    # A's own symbols/file row are still correctly gone.
    cursor.execute("SELECT COUNT(*) FROM symbols WHERE filepath = 'A.java'")
    assert cursor.fetchone()[0] == 0
    cursor.execute("SELECT COUNT(*) FROM files WHERE filepath = 'A.java'")
    assert cursor.fetchone()[0] == 0


def test_clear_file_still_removes_a_legacy_pre_migration_relation_by_name(tmp_path):
    """A relation row inserted before the source_file column existed
    (source_file IS NULL) must still be cleaned up by clear_file() via the
    name-based fallback - not left as a permanent orphan."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))

    cursor = graph.conn.cursor()
    cursor.execute(
        "INSERT INTO symbols (filepath, name, type, start_line, end_line) VALUES (?, ?, ?, ?, ?)",
        ("A.java", "handle", "function", 1, 5),
    )
    # Simulates a row from before the source_file migration.
    cursor.execute(
        "INSERT INTO relations (source, target, type, source_file) VALUES (?, ?, ?, NULL)",
        ("A.trigger", "handle", "calls"),
    )
    cursor.execute("INSERT INTO files (filepath, mtime, hash) VALUES (?, ?, ?)", ("A.java", 100.0, "h1"))
    graph.conn.commit()

    graph.clear_file("A.java")

    assert graph.get_callers("handle") == []


def test_get_symbols_for_file_returns_real_indexed_names(tmp_path):
    """Architectural add-on from a 2026-08-12 SME review (re-ranking
    retrieval): Graph RAG seeding used to guess a file's symbol from its
    filename stem - this is the real lookup that replaced it."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    graph.index_file("my_engine.py", "class MyEngine:\n    def execute(self):\n        pass\n", 100.0)

    symbols = graph.get_symbols_for_file("my_engine.py")

    assert "MyEngine" in symbols
    assert "execute" in symbols


def test_get_symbols_for_file_returns_empty_list_for_unknown_file(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    assert graph.get_symbols_for_file("nope.py") == []


def test_get_class_symbol_locations_collapses_package_qualified_java_names(tmp_path):
    """Regression test for a real live bug, 2026-08-21 (protocol_encoder_java):
    three separate, incompatible `Protocol.java` files ended up coexisting in
    one workspace, in three different packages - _parse_java() stores the
    FULLY QUALIFIED name (package_prefix + class_name), so a qualified-name
    lookup would never see these three as related. get_class_symbol_locations()
    must collapse them onto the same simple-name key so the duplicate is
    actually visible."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    graph.index_file("src/main/java/Protocol.java", "public class Protocol {}\n", 1.0)
    graph.index_file(
        "src/main/java/protocol/Protocol.java",
        "package protocol;\npublic class Protocol {}\n", 1.0,
    )
    graph.index_file(
        "src/main/java/com/example/protocol/Protocol.java",
        "package com.example.protocol;\npublic class Protocol {}\n", 1.0,
    )

    index = graph.get_class_symbol_locations()

    assert set(index[".java:Protocol"]) == {
        "src/main/java/Protocol.java",
        "src/main/java/protocol/Protocol.java",
        "src/main/java/com/example/protocol/Protocol.java",
    }


def test_get_class_symbol_locations_scopes_by_extension_not_just_simple_name(tmp_path):
    """A Java `Protocol` and an unrelated Python `Protocol` in the same
    polyglot repo are typically different concepts, not a duplicate - the
    index key must be extension-scoped so they never collide."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    graph.index_file("src/main/java/Protocol.java", "public class Protocol {}\n", 1.0)
    graph.index_file("protocol.py", "class Protocol:\n    pass\n", 1.0)

    index = graph.get_class_symbol_locations()

    assert index[".java:Protocol"] == ["src/main/java/Protocol.java"]
    assert index[".py:Protocol"] == ["protocol.py"]


# --- Java interface symbol indexing (2026-09-07, P7 preflight) ------------
# A live P7 preflight against a real hexagonal-architecture Maven repo found
# _parse_java()'s class_regex matched only the literal `class` keyword - an
# `interface UserService {...}` port declaration was never added to the
# symbols table at all, so build_planning_structural_evidence() resolved
# every OTHER cross-module edge in the repo except the one that WAS the
# module boundary (a class `implements` an interface declared in a
# different Maven module). The `implements`/`inherits` RELATION was already
# recorded correctly - only the interface's own SYMBOL LOCATION was
# missing, making that relation's target unresolvable to a real file.

def test_interface_symbol_location_is_discovered(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    graph.index_file(
        "core/UserService.java",
        "package com.example.core;\npublic interface UserService {\n    void save();\n}\n",
        1.0,
    )

    index = graph.get_class_symbol_locations()

    assert index[".java:UserService"] == ["core/UserService.java"]


def test_class_symbol_discovery_is_unchanged_by_interface_support(tmp_path):
    """Existing class behavior must be completely unaffected - same
    extension-scoped key, same collapsing-on-simple-name behavior already
    covered above, now proven to hold with an interface also present in
    the same file set."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    graph.index_file("core/UserService.java", "package p;\npublic interface UserService {}\n", 1.0)
    graph.index_file("repo/UserServiceImpl.java", "package q;\npublic class UserServiceImpl implements UserService {}\n", 1.0)

    index = graph.get_class_symbol_locations()

    assert index[".java:UserService"] == ["core/UserService.java"]
    assert index[".java:UserServiceImpl"] == ["repo/UserServiceImpl.java"]


def test_interface_extends_and_implements_relations_are_unaffected():
    """The interface/class distinction only changes symbol-table
    membership - the existing `implements` relation (already correctly
    recorded before this fix; only its target's symbol location was
    missing) is untouched."""
    db_path = ":memory:"
    graph = DependencyGraph(db_path)
    src = (
        "package p;\n"
        "public class UserServiceImpl implements UserService {\n"
        "    public void save(){}\n"
        "}\n"
    )
    graph.index_file("UserServiceImpl.java", src, 1.0)
    symbols, relations = graph._parse_java("UserServiceImpl.java", src)
    assert {"name": "p.UserServiceImpl", "type": "class", "start_line": 2, "end_line": 7} in symbols
    assert any(
        r["source"] == "p.UserServiceImpl" and r["target"] == "UserService" and r["type"] == "implements"
        for r in relations
    )


def test_duplicate_simple_name_collision_still_detected_across_class_and_interface(tmp_path):
    """Same simple name in different packages must still be visible as a
    collision - the exact duplicate-type Quality Gate this index exists
    for - now proven across a class/interface pair, not just class/class."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    graph.index_file("a/Protocol.java", "package a;\ninterface Protocol {}\n", 1.0)
    graph.index_file("b/Protocol.java", "package b;\nclass Protocol {}\n", 1.0)

    index = graph.get_class_symbol_locations()

    assert set(index[".java:Protocol"]) == {"a/Protocol.java", "b/Protocol.java"}


def test_comment_or_string_containing_interface_keyword_is_not_indexed(tmp_path):
    """A commented-out or string-embedded 'interface Foo' must never become
    a real symbol - matches this parser's existing, unchanged safety for
    'class Foo' in the same position (line-start-anchored matching, never
    a bare substring search)."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    src = (
        "package p;\n"
        "// interface Foo {}\n"
        "/* interface Foo {} */\n"
        "public class Real {\n"
        '    String s = "interface Foo {}";\n'
        "}\n"
    )
    graph.index_file("Real.java", src, 1.0)

    index = graph.get_class_symbol_locations()

    assert ".java:Foo" not in index
    assert index[".java:Real"] == ["Real.java"]


def test_extract_class_names_covers_python_java_ruby(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))

    assert graph.extract_class_names("a.py", "class Foo:\n    pass\n") == [".py:Foo"]
    assert graph.extract_class_names("A.java", "public class A {}\n") == [".java:A"]
    assert graph.extract_class_names("a.rb", "class Foo\nend\n") == [".rb:Foo"]
    # Package-qualified Java content still collapses to the simple name.
    assert graph.extract_class_names(
        "src/main/java/protocol/Protocol.java",
        "package protocol;\npublic class Protocol {}\n",
    ) == [".java:Protocol"]


def test_extract_class_names_degrades_gracefully_for_unsupported_or_invalid_content(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))

    # Not yet a parsed language - must return [] , never raise.
    assert graph.extract_class_names("main.go", "type Protocol struct {}\n") == []
    # Invalid syntax for a parsed language must also degrade to [], not raise.
    assert graph.extract_class_names("broken.py", "class (((not valid") == []


def test_find_java_main_class_detects_array_and_varargs_shapes(tmp_path):
    graph = DependencyGraph(str(tmp_path / "dep_graph.db"))

    assert graph.find_java_main_class(
        "App.java", "public class App {\n    public static void main(String[] args) {}\n}\n"
    ) == "App"
    assert graph.find_java_main_class(
        "App.java", "class App {\n    public static void main(String... args) {}\n}\n"
    ) == "App"


def test_find_java_main_class_applies_package_prefix(tmp_path):
    graph = DependencyGraph(str(tmp_path / "dep_graph.db"))

    content = "package com.example;\npublic class App {\n    public static void main(String[] args) {}\n}\n"
    assert graph.find_java_main_class("com/example/App.java", content) == "com.example.App"


def test_find_java_main_class_returns_none_without_a_real_main_method(tmp_path):
    graph = DependencyGraph(str(tmp_path / "dep_graph.db"))

    assert graph.find_java_main_class("Protocol.java", "public class Protocol {\n    int x;\n}\n") is None
    assert graph.find_java_main_class("App.py", "public static void main(String[] args) {}") is None


def test_find_java_main_class_ignores_a_main_method_only_mentioned_in_a_comment(tmp_path):
    """A raw whole-content regex scan would false-positive on a comment
    merely DESCRIBING a main method as if it were real code - this must
    behave the same as _parse_java()'s own established comment-skipping
    convention (line-by-line, skip lines starting with //, /*, *)."""
    graph = DependencyGraph(str(tmp_path / "dep_graph.db"))
    content = (
        "public class Helper {\n"
        "    // A real entrypoint needs public static void main(String[] args)\n"
        "    void doWork() {}\n"
        "}\n"
    )
    assert graph.find_java_main_class("Helper.java", content) is None


def test_find_java_main_class_degrades_to_none_for_ambiguous_multi_class_file(tmp_path):
    """More than one top-level class in the same file is a shape a flat
    regex scan can't safely scope main() to - must degrade to None (no
    confident answer) rather than guess which class actually owns it."""
    graph = DependencyGraph(str(tmp_path / "dep_graph.db"))
    content = (
        "public class App {\n"
        "    public static void main(String[] args) {}\n"
        "}\n"
        "class Helper {\n"
        "    public static void main(String[] args) {}\n"
        "}\n"
    )
    assert graph.find_java_main_class("App.java", content) is None


def test_get_neighborhood_scores_direct_import_above_deeper_annotation_hit(tmp_path):
    """A hop-1 'imports' hit should outscore a hop-2 'annotated_with' hit -
    the whole point of scoring get_neighborhood()'s output is to let callers
    tell a strong direct relation apart from a weak, distant one instead of
    treating every hit identically."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))

    cursor = graph.conn.cursor()
    # Seed --imports--> Direct (hop 1)
    cursor.execute(
        "INSERT INTO symbols (filepath, name, type, start_line, end_line) VALUES (?, ?, ?, ?, ?)",
        ("Direct.java", "Direct", "class", 1, 1),
    )
    cursor.execute(
        "INSERT INTO relations (source, target, type, source_file) VALUES (?, ?, ?, ?)",
        ("Seed", "Direct", "imports", "Seed.java"),
    )
    # Direct --annotated_with--> Distant (hop 2 from Seed)
    cursor.execute(
        "INSERT INTO symbols (filepath, name, type, start_line, end_line) VALUES (?, ?, ?, ?, ?)",
        ("Distant.java", "Distant", "class", 1, 1),
    )
    cursor.execute(
        "INSERT INTO relations (source, target, type, source_file) VALUES (?, ?, ?, ?)",
        ("Direct", "Distant", "annotated_with", "Direct.java"),
    )
    graph.conn.commit()

    results = graph.get_neighborhood(["Seed"], max_hops=2)

    # Mirrors how kriya/workflow/workflow.py actually consumes this: the
    # BEST (max) score seen for a given file across however many neighbor
    # hits mention it, not "whichever result happened to come last."
    best_by_file: dict = {}
    for r in results:
        fp = r["filepath"]
        best_by_file[fp] = max(best_by_file.get(fp, 0.0), r["score"])

    assert best_by_file["Direct.java"] > best_by_file["Distant.java"]
    # Sorted descending by score - the strongest hit comes first.
    assert results[0]["filepath"] == "Direct.java"
    assert results[0]["score"] == best_by_file["Direct.java"]


def test_get_neighborhood_caps_results_instead_of_returning_every_hit(tmp_path):
    """Regression test for a finding from the 2026-08-12 SME review:
    get_neighborhood()'s BFS is keyed on bare, unqualified symbol names with
    no per-result cap - a common method name shared across many unrelated
    classes (e.g. "process", "save") previously pulled in every unrelated
    definition as "related" context, unbounded. Now capped, keeping the
    highest-scoring hits (a direct import) over lower-scoring ones
    (generic calls) rather than an arbitrary truncation."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))

    cursor = graph.conn.cursor()
    # One genuinely strong signal: Seed directly imports RealTarget.
    cursor.execute(
        "INSERT INTO symbols (filepath, name, type, start_line, end_line) VALUES (?, ?, ?, ?, ?)",
        ("RealTarget.java", "process", "function", 1, 1),
    )
    cursor.execute(
        "INSERT INTO relations (source, target, type, source_file) VALUES (?, ?, ?, ?)",
        ("Seed", "process", "imports", "Seed.java"),
    )
    # Many unrelated classes that happen to define a same-named "process"
    # method, reachable only via a weak generic "calls" relation - the
    # noise this cap is meant to bound.
    for i in range(40):
        filepath = f"Unrelated{i}.java"
        cursor.execute(
            "INSERT INTO symbols (filepath, name, type, start_line, end_line) VALUES (?, ?, ?, ?, ?)",
            (filepath, f"process{i}", "function", 1, 1),
        )
        cursor.execute(
            "INSERT INTO relations (source, target, type, source_file) VALUES (?, ?, ?, ?)",
            ("process", f"process{i}", "calls", "RealTarget.java"),
        )
    graph.conn.commit()

    results = graph.get_neighborhood(["Seed"], max_hops=2, max_results=10)

    assert len(results) == 10
    # The strong direct-import hit must survive the cap, not be crowded out
    # by an arbitrary subset of the 40 weak "calls" hits.
    assert any(r["filepath"] == "RealTarget.java" for r in results)


def test_get_neighborhood_default_max_results_bounds_a_large_fan_out(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))

    cursor = graph.conn.cursor()
    for i in range(50):
        filepath = f"File{i}.java"
        cursor.execute(
            "INSERT INTO symbols (filepath, name, type, start_line, end_line) VALUES (?, ?, ?, ?, ?)",
            (filepath, f"sym{i}", "function", 1, 1),
        )
        cursor.execute(
            "INSERT INTO relations (source, target, type, source_file) VALUES (?, ?, ?, ?)",
            ("Seed", f"sym{i}", "calls", "Seed.java"),
        )
    graph.conn.commit()

    results = graph.get_neighborhood(["Seed"], max_hops=2)  # default max_results

    assert len(results) <= 30
    assert len(results) < 50  # confirms it's actually bounded, not coincidentally equal


def test_has_indexed_files_false_on_empty_graph(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    assert graph.has_indexed_files() is False


# --- CTX-001 P1 WP1: relations.source_file SQL index ----------------------
# CTX-001 P0 (docs/assurance/CTX_001_P0_CURRENT_STATE.md) root-caused cold
# Graph RAG indexing's super-linear (O(N^2)) scaling to clear_file()'s own
# "DELETE FROM relations WHERE source_file = ?" doing a full table scan on
# EVERY file indexed, because relations.source_file (added later via ALTER
# TABLE) never got an index. Fixed by one additive
# `CREATE INDEX IF NOT EXISTS idx_relations_source_file ON relations(source_file)`
# in _init_db().

def test_fresh_db_has_source_file_index(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))

    cursor = graph.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'relations'")
    index_names = {r[0] for r in cursor.fetchall()}
    assert "idx_relations_source_file" in index_names


def test_existing_pre_index_db_obtains_source_file_index_on_reopen(tmp_path):
    """Simulates a real pre-P1 database on disk: relations.source_file
    column exists (from the earlier ALTER TABLE migration) but with no
    index on it - the exact state P0's probe found in production. Reopening
    it through DependencyGraph must add the index safely, without touching
    existing data (no destructive schema migration, no table recreation)."""
    import sqlite3

    db_path = tmp_path / "dep_graph.db"
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE files (filepath TEXT PRIMARY KEY, mtime REAL, hash TEXT)")
    cursor.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY AUTOINCREMENT, filepath TEXT, name TEXT, type TEXT, start_line INTEGER, end_line INTEGER)")
    cursor.execute("CREATE TABLE relations (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, target TEXT, type TEXT, source_file TEXT)")
    cursor.execute("CREATE INDEX idx_symbols_filepath ON symbols(filepath)")
    cursor.execute("CREATE INDEX idx_symbols_name ON symbols(name)")
    cursor.execute("CREATE INDEX idx_relations_source ON relations(source)")
    cursor.execute("CREATE INDEX idx_relations_target ON relations(target)")
    cursor.execute(
        "INSERT INTO relations (source, target, type, source_file) VALUES (?, ?, ?, ?)",
        ("A.trigger", "handle", "calls", "A.java"),
    )
    conn.commit()
    conn.close()

    # Pre-condition: no source_file index yet, matching the exact defect P0 found.
    conn = sqlite3.connect(str(db_path))
    pre_names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'relations'").fetchall()}
    assert "idx_relations_source_file" not in pre_names
    conn.close()

    graph = DependencyGraph(str(db_path))

    cursor = graph.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'relations'")
    index_names = {r[0] for r in cursor.fetchall()}
    assert "idx_relations_source_file" in index_names

    # No destructive migration: the pre-existing relation row survives untouched.
    cursor.execute("SELECT source, target, type, source_file FROM relations")
    rows = cursor.fetchall()
    assert rows == [("A.trigger", "handle", "calls", "A.java")]


def test_source_file_index_creation_is_idempotent(tmp_path):
    """Repeated DependencyGraph construction against the same DB (the real
    pattern - a fresh DependencyGraph is built per workflow run) must not
    error or duplicate the index."""
    db_path = tmp_path / "dep_graph.db"
    graph1 = DependencyGraph(str(db_path))
    graph1.index_file("A.java", "public class A { void handle(){} }\n", 1.0)
    graph1.close()

    graph2 = DependencyGraph(str(db_path))
    graph2._init_db()  # explicit second call - must be a safe no-op

    cursor = graph2.conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name = 'idx_relations_source_file'")
    assert cursor.fetchone()[0] == 1
    # Relation data untouched by re-running schema init.
    cursor.execute("SELECT COUNT(*) FROM symbols WHERE filepath = 'A.java'")
    assert cursor.fetchone()[0] >= 1


def test_clear_file_relation_behavior_unchanged_by_index(tmp_path):
    """The index is a pure performance change - clear_file()'s existing,
    already-tested correctness (file-scoped deletion, name-based fallback for
    legacy NULL rows) must be bit-for-bit unchanged."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    graph.index_file(
        "A.java",
        "package p;\npublic class A {\n    public void handle() {\n        obj.other();\n    }\n}\n",
        1.0,
    )
    graph.index_file(
        "B.java",
        "package p;\npublic class B {\n    public void trigger() {\n        obj.handle();\n    }\n}\n",
        2.0,
    )

    graph.clear_file("A.java")

    cursor = graph.conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM relations WHERE source_file = 'A.java'")
    assert cursor.fetchone()[0] == 0
    cursor.execute("SELECT COUNT(*) FROM relations WHERE source_file = 'B.java'")
    assert cursor.fetchone()[0] > 0


def test_source_file_delete_query_plan_uses_the_new_index(tmp_path):
    """Structural (not timing-based) proof that clear_file()'s own DELETE
    statement now resolves via idx_relations_source_file instead of a full
    'relations' table scan - the exact query EXPLAIN QUERY PLAN evidence P0
    used to root-cause the O(N^2) cold-index defect."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    for i in range(50):
        graph.index_file(f"File{i}.java", f"public class File{i} {{ void handle(){{}} }}\n", float(i))

    cursor = graph.conn.cursor()
    # The exact statement clear_file() runs (kept in sync with it deliberately -
    # this proves the REAL production query plan, not a simplified stand-in).
    cursor.execute(
        """
        EXPLAIN QUERY PLAN
        DELETE FROM relations
        WHERE source_file = ?
           OR (
                source_file IS NULL
                AND (
                    source IN (SELECT name FROM symbols WHERE filepath = ?)
                    OR target IN (SELECT name FROM symbols WHERE filepath = ?)
                )
              )
        """,
        ("File0.java", "File0.java", "File0.java"),
    )
    plan = " | ".join(str(row) for row in cursor.fetchall())

    assert "idx_relations_source_file" in plan
    assert "SCAN relations" not in plan


# --- CTX-001 P1 WP2: Python inheritance relations --------------------------
# CTX-001 P0 (docs/assurance/CTX_001_P0_CURRENT_STATE.md, finding F17/S3)
# found _parse_python() never emitted an "inherits"/"implements" relation at
# all - confirmed live via a real interface/implementation fixture that
# produced zero relation row for a real inheritance edge, unlike
# _parse_java() (see test_interface_extends_and_implements_relations_are_
# unaffected above), which has always recorded "inherits"/"implements".
# _python_base_name()/the ast.ClassDef.bases walk below closes that gap
# using the SAME relation vocabulary and the SAME source=class_name
# convention _parse_java() already established, deliberately for semantic
# consistency (kept to a single "inherits" type - Python's `class Foo(Base):`
# syntax has no class/interface keyword distinction to key an "implements"
# type off of, unlike Java).

def test_python_inheritance_single_base(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    content = "class Base:\n    pass\n\n\nclass Child(Base):\n    pass\n"
    symbols, relations = graph._parse_python("mod.py", content)

    assert {"name": "Base", "type": "class", "start_line": 1, "end_line": 2} in symbols
    assert {"name": "Child", "type": "class", "start_line": 5, "end_line": 6} in symbols
    assert relations == [{"source": "Child", "target": "Base", "type": "inherits"}]


def test_python_inheritance_multiple_bases(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    content = "class Base1:\n    pass\n\n\nclass Base2:\n    pass\n\n\nclass Child(Base1, Base2):\n    pass\n"
    _symbols, relations = graph._parse_python("mod.py", content)

    inherits = {(r["source"], r["target"]) for r in relations if r["type"] == "inherits"}
    assert inherits == {("Child", "Base1"), ("Child", "Base2")}


def test_python_inheritance_imported_base(tmp_path):
    """`from other_module import Base; class Child(Base): ...` - Base is a
    plain ast.Name at the class-definition site (the import itself is
    already captured separately as its own "imports" relation), so the
    inherits relation resolves identically to a same-file base."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    content = "from other_module import Base\n\n\nclass Child(Base):\n    pass\n"
    _symbols, relations = graph._parse_python("mod.py", content)

    assert {"source": "mod.py", "target": "other_module", "type": "imports"} in relations
    assert {"source": "Child", "target": "Base", "type": "inherits"} in relations


def test_python_inheritance_qualified_base(tmp_path):
    """`class Child(pkg.Base): ...` - an ast.Attribute chain rooted in a
    Name must resolve to the full dotted path deterministically, without
    any runtime import resolution."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    content = "import pkg\n\n\nclass Child(pkg.Base):\n    pass\n"
    _symbols, relations = graph._parse_python("mod.py", content)

    assert {"source": "Child", "target": "pkg.Base", "type": "inherits"} in relations


def test_python_inheritance_deeply_qualified_base(tmp_path):
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    content = "class Child(pkg.sub.Base):\n    pass\n"
    _symbols, relations = graph._parse_python("mod.py", content)

    assert {"source": "Child", "target": "pkg.sub.Base", "type": "inherits"} in relations


def test_python_inheritance_abc_interface_fixture(tmp_path):
    """Reuses CTX-001-P0's own core billing fixture semantics
    (spikes/ctx_001_p0/fixtures.py CORE_FILES) - an ABC interface
    (InvoiceCalculator) with a concrete implementation
    (StandardInvoiceCalculator(InvoiceCalculator)) - this is the exact
    interface/implementation shape P0's F17 finding demonstrated as
    invisible to the dependency graph. Now fixed."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    interface_content = (
        "from abc import ABC, abstractmethod\n\n\n"
        "class InvoiceCalculator(ABC):\n"
        "    @abstractmethod\n"
        "    def calculate_total(self, items):\n"
        "        raise NotImplementedError\n"
    )
    impl_content = (
        "from core.billing.invoice_interface import InvoiceCalculator\n\n\n"
        "class StandardInvoiceCalculator(InvoiceCalculator):\n"
        "    def calculate_total(self, items):\n"
        "        return sum(i.unit_price * i.quantity for i in items)\n"
    )
    graph.index_file("core/billing/invoice_interface.py", interface_content, 1.0)
    graph.index_file("core/billing/invoice_impl.py", impl_content, 2.0)

    cursor = graph.conn.cursor()
    cursor.execute("SELECT source, target FROM relations WHERE type = 'inherits'")
    rows = cursor.fetchall()
    assert ("StandardInvoiceCalculator", "InvoiceCalculator") in rows


def test_python_inheritance_coexists_with_imports_and_calls(tmp_path):
    """Existing import/call extraction must remain green alongside the new
    inherits relation - same node, same ast.walk() pass."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    content = (
        "import sys\n"
        "from base_module import Base\n\n\n"
        "class Child(Base):\n"
        "    def run(self):\n"
        "        self.helper()\n"
        "        other()\n"
    )
    _symbols, relations = graph._parse_python("mod.py", content)

    assert {"source": "mod.py", "target": "sys", "type": "imports"} in relations
    assert {"source": "mod.py", "target": "base_module", "type": "imports"} in relations
    assert {"source": "mod.py", "target": "helper", "type": "calls"} in relations
    assert {"source": "mod.py", "target": "other", "type": "calls"} in relations
    assert {"source": "Child", "target": "Base", "type": "inherits"} in relations


def test_python_inheritance_dynamic_base_does_not_fabricate_edge(tmp_path):
    """A base that isn't statically nameable (a call, a subscript) must
    never invent a relationship - CTX-001 P1 WP2's explicit conservative-
    behavior requirement, mirroring extract_class_names()'s own established
    "degrade gracefully, never guess" precedent."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    content = (
        "from typing import Generic, TypeVar\n\n"
        "T = TypeVar('T')\n\n\n"
        "class Dynamic(get_base()):\n"
        "    pass\n\n\n"
        "class Parameterized(Generic[T]):\n"
        "    pass\n"
    )
    _symbols, relations = graph._parse_python("mod.py", content)

    inherits = [r for r in relations if r["type"] == "inherits"]
    assert inherits == []


def test_python_inheritance_metaclass_keyword_is_not_treated_as_a_base(tmp_path):
    """`metaclass=SomeMeta` is an ast.keyword, never a member of
    node.bases - only the real positional base ("Base") may produce a
    relation; SomeMeta (referenced only as the keyword's value, never
    defined as its own class here) must not."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    content = "class Child(Base, metaclass=SomeMeta):\n    pass\n"
    _symbols, relations = graph._parse_python("mod.py", content)

    inherits = {(r["source"], r["target"]) for r in relations if r["type"] == "inherits"}
    assert inherits == {("Child", "Base")}


def test_python_inheritance_stale_edge_removed_on_reindex(tmp_path):
    """Re-indexing a file whose base class changed (or was removed) must not
    leave a stale inherits edge behind - this falls out of clear_file()'s
    existing source_file-scoped deletion (now index-backed, WP1), applied
    uniformly to every relation type including the new "inherits" one; no
    additional code was needed for this to hold."""
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    graph.index_file("mod.py", "class Base:\n    pass\n\n\nclass Child(Base):\n    pass\n", 1.0)

    cursor = graph.conn.cursor()
    cursor.execute("SELECT source, target FROM relations WHERE type = 'inherits'")
    assert ("Child", "Base") in cursor.fetchall()

    # Re-index the SAME file with the base class removed.
    graph.index_file("mod.py", "class Child:\n    pass\n", 2.0)

    cursor.execute("SELECT source, target FROM relations WHERE type = 'inherits'")
    assert cursor.fetchall() == []


def test_has_indexed_files_true_once_a_file_is_recorded(tmp_path):
    # index_file() (used elsewhere in this test module) only ever writes to
    # symbols/relations, never to files itself - has_indexed_files() checks
    # the files table directly (the same one index_repository() writes real
    # mtime/hash rows into), so this inserts there directly rather than via
    # index_file().
    db_path = tmp_path / "dep_graph.db"
    graph = DependencyGraph(str(db_path))
    cursor = graph.conn.cursor()
    cursor.execute(
        "INSERT INTO files (filepath, mtime, hash) VALUES (?, ?, ?)",
        ("App.java", 123.0, "abc123"),
    )
    graph.conn.commit()
    assert graph.has_indexed_files() is True
