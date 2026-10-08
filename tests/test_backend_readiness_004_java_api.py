"""JAVA-API-PRESERVATION-PREDICATE-001 (BACKEND-READINESS-004): the public-API preservation predicate for Java,
from the code-intelligence structural model (tree-sitter stays in kriya/code_intel/parsing.py).

Surface: public/protected types reachable through public/protected enclosing types; members with their kind,
API-relevant modifiers, return type, parameter types (overloads distinct), throws clause; interface members
implicitly public; enum constants and record components. Not surface: private/package-private members, nested
private types, parameter names, annotations, test-side files. Compared base vs candidate like Python: removed,
narrowed, re-signed, made final/abstract or re-parented is VIOLATED; added is recorded; a file the parser cannot
parse cleanly is UNAVAILABLE.
"""
import subprocess

from kriya.workflow import api_preservation as api

BASE = b"""package org.example;

import java.io.IOException;

/** A public type. */
public class Widget extends Base implements Runnable, Comparable<Widget> {
    public static final int LIMIT = 3;
    protected String name;
    private int secret;
    String packagePrivate;

    public Widget(String name) { this.name = name; }
    public Widget(String name, int n) { this.name = name; }
    public String name() { return name; }
    public void run() {}
    public int compareTo(Widget other) { return 0; }
    protected void hook(java.util.List<String> items) throws IOException {}
    private void internal() {}
    void packageOnly() {}

    public static class Nested { public int value() { return 1; } }
    private static class Hidden { public int value() { return 2; } }
}

interface Internal { void x(); }

public interface Shape { double area(); default String label() { return "shape"; } }

public enum Color { RED, GREEN }

public record Point(int x, int y) {}
"""


def _sig(source=BASE, path="src/main/java/org/example/Widget.java"):
    return api.java_public_signatures(source, path)


def test_01_the_surface_is_the_public_and_protected_reachable_declarations_only():
    surface = _sig()
    assert "org.example.Widget" in surface and surface["org.example.Widget"].startswith("class[public] extends(Base) implements(Comparable, Runnable)")
    assert "org.example.Widget.LIMIT" in surface and surface["org.example.Widget.name"].startswith("field[protected] String")
    assert "org.example.Widget.secret" not in surface and "org.example.Widget.packagePrivate" not in surface
    assert {k for k in surface if k.startswith("org.example.Widget.Widget")} == {"org.example.Widget.Widget(String)", "org.example.Widget.Widget(String, int)"}
    assert surface["org.example.Widget.hook(java.util.List<String>)"] == "method[protected] void (java.util.List<String>) throws(IOException)"
    assert "org.example.Widget.internal()" not in surface and "org.example.Widget.packageOnly()" not in surface
    assert "org.example.Widget.Nested.value()" in surface and "org.example.Widget.Hidden.value()" not in surface
    assert "org.example.Internal" not in surface and "org.example.Internal.x()" not in surface
    assert surface["org.example.Shape.area()"].startswith("method[]") and "org.example.Shape.label()" in surface
    assert surface["org.example.Color.RED"] == "enum_constant[] " and "org.example.Point.x" in surface


def test_02_parameter_names_and_annotations_are_not_surface_but_modifiers_types_and_throws_are():
    renamed = BASE.replace(b"public String name() { return name; }", b"@Deprecated public String name() { return name; }") \
                  .replace(b"public Widget(String name) { this.name = name; }", b"public Widget(String label) { this.name = label; }")
    assert _sig(renamed) == _sig()
    cases = {
        "removed": BASE.replace(b"    public void run() {}\n", b""),
        "narrowed": BASE.replace(b"public String name()", b"String name()"),
        "return type": BASE.replace(b"public String name()", b"public CharSequence name()"),
        "parameter type": BASE.replace(b"public int compareTo(Widget other)", b"public int compareTo(Object other)"),
        "made final": BASE.replace(b"public class Widget", b"public final class Widget"),
        "hierarchy": BASE.replace(b"extends Base implements Runnable", b"implements Runnable"),
        "throws": BASE.replace(b"throws IOException", b""),
    }
    base = _sig()
    for what, mutated in cases.items():
        after = _sig(mutated)
        removed = [k for k in base if k not in after]
        changed = [k for k in base if k in after and after[k] != base[k]]
        assert removed or changed, what
    # an addition is never a change
    added = _sig(BASE.replace(b"    public void run() {}\n", b"    public void run() {}\n    public void extra() {}\n"))
    assert set(base) < set(added) and all(added[k] == base[k] for k in base)


def test_03_unparseable_sources_fail_closed_and_test_side_files_are_not_surface():
    try:
        _sig(b"public class Broken {")
    except SyntaxError as error:
        assert "Broken.java" in str(error) or "PARTIALLY_PARSED" in str(error) or "PARSE_FAILED" in str(error)
    else:  # pragma: no cover - the assertion above is the contract
        raise AssertionError("an incomplete compilation unit must not yield a surface")
    assert api._public_java_unit("src/main/java/a/B.java") and not api._public_java_unit("src/test/java/a/BTest.java")
    assert not api._public_java_unit("src/main/java/a/package-info.java") and not api._public_java_unit("src/main/java/a/B.kt")


def test_04_compare_public_api_judges_a_java_repository_base_versus_candidate(tmp_path):
    root = tmp_path / "repo"
    (root / "src/main/java/org/example").mkdir(parents=True)
    (root / "src/test/java/org/example").mkdir(parents=True)
    main = root / "src/main/java/org/example/Widget.java"
    main.write_bytes(BASE)
    (root / "src/test/java/org/example/WidgetTest.java").write_bytes(b"package org.example; public class WidgetTest { public void t() {} }\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base"], cwd=root, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    files = ["src/main/java/org/example/Widget.java", "src/test/java/org/example/WidgetTest.java"]

    def reader(content):
        return lambda path: content if path.endswith("Widget.java") else (main.read_bytes() if False else None) if path.endswith("WidgetTest.java") else None

    preserved = api.compare_public_api(str(root), base, candidate_files=files, read_candidate=reader(BASE), language="java")
    assert preserved.available and preserved.preserved and preserved.compared_files == ("src/main/java/org/example/Widget.java",)
    assert preserved.base_symbols > 10 and preserved.to_dict()["language"] == "java"
    broken = api.compare_public_api(str(root), base, candidate_files=files,
                                    read_candidate=reader(BASE.replace(b"public String name()", b"String name()")), language="java")
    assert broken.available and not broken.preserved and broken.removed == ("org.example.Widget.name()",)
    resigned = api.compare_public_api(str(root), base, candidate_files=files,
                                      read_candidate=reader(BASE.replace(b"public String name()", b"public CharSequence name()")), language="java")
    assert resigned.available and resigned.changed == ("org.example.Widget.name()",) and resigned.removed == ()
    assert resigned.to_dict()["changed_signatures"]["org.example.Widget.name()"]["base"].endswith("String () throws()")
    gone = api.compare_public_api(str(root), base, candidate_files=files, read_candidate=lambda p: None, language="java")
    assert gone.available and len(gone.removed) == gone.base_symbols  # every symbol of a deleted unit is removed
    unparseable = api.compare_public_api(str(root), base, candidate_files=files, read_candidate=reader(b"public class Widget {"), language="java")
    assert not unparseable.available and "does not parse" in unparseable.reason
    assert not api.compare_public_api(str(root), base, candidate_files=files, read_candidate=reader(BASE), language="ruby").available
