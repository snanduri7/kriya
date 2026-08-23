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
