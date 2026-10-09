"""STRUCTURAL-EVIDENCE-SELF-CALL-EDGE-001 (BACKEND-FINAL-CLOSURE-005 cohort 2, C2-S3_A; second repair cycle).

Measured on the frozen workspace: two same-package classes - a class and its deprecated twin declaring the same
method names, with no import, field, parameter or instantiation of each other - produced a mutual "references"
edge, a cycle no plan could order. The discriminating check (repair-002/prefix/D2_discriminating_check.txt) traced
three producers in build_planning_structural_evidence, not one:
1. a class's calls to its OWN methods resolved to the only OTHER declarer of those names (the twin);
2. the constructor scan matched ``new Twin()`` inside a Javadoc example;
3. a JDK static call (``CharBuffer.wrap``) resolved to the twin's own ``wrap()`` by bare name.
The fix: a self-declared callee resolves to the caller (no edge); the constructor scan reads code with comments and
string literals blanked; a bare cross-file call is an edge only when the caller's code names a type the callee's
file declares. Real cross-class references keep their edges (positive controls); ambiguous names still resolve to
nothing. Repository-independent names throughout.
"""
from kriya.workflow.workflow_controller import build_planning_structural_evidence

PKG = "package com.example.text;\n"
BUILDER = "src/main/java/com/example/text/Builder.java"
LEGACY = "src/main/java/com/example/text/LegacyBuilder.java"
CONSUMER = "src/main/java/com/example/text/Consumer.java"
HELPER_A = "src/main/java/com/example/text/HelperA.java"
HELPER_B = "src/main/java/com/example/text/HelperB.java"
POOL = "src/main/java/com/example/text/Pool.java"


def _twin(name: str, other: str) -> str:
    """A builder whose methods call each other, with the twin named only in Javadoc."""
    return (
        PKG
        + "import java.nio.CharBuffer;\n"
        + f"/**\n * Use {{@link {other}}} instead.\n * <pre>\n * {other} b = new {other}();\n * </pre>\n */\n"
        + f"public class {name} {{\n"
        + "    private char[] buffer = new char[8];\n    private int size;\n"
        + f"    public {name} append(String s) {{ ensureCapacity(size + s.length()); size += s.length(); return this; }}\n"
        + "    public void ensureCapacity(int n) { if (n > buffer.length) { buffer = new char[n]; } }\n"
        + "    public int length() { return size; }\n"
        + "    public char charAt(int i) { return buffer[i]; }\n"
        + f"    public {name} delete(int from, int to) {{ size -= to - from; return this; }}\n"
        + "    public CharBuffer asBuffer() { return CharBuffer.wrap(buffer, 0, size); }\n"
        + "}\n"
    )


def _seed(tmp_path, files):
    for relpath, content in files.items():
        full = tmp_path / relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    return list(files)


def test_twin_classes_with_the_same_method_names_never_reference_each_other(tmp_path):
    """The measured shape: self-calls, a Javadoc instantiation example and a JDK static call named like a method
    the twin declares (``wrap``, declared below on Builder) - no edge in either direction."""
    builder = _twin("Builder", "LegacyBuilder").replace(
        "    public int length()", "    public static Builder wrap(char[] initial) { return new Builder(); }\n    public int length()")
    candidates = _seed(tmp_path, {BUILDER: builder, LEGACY: _twin("LegacyBuilder", "Builder")})

    text, edges = build_planning_structural_evidence(str(tmp_path), candidates)

    assert edges == {}, edges
    assert text == ""


def test_a_real_cross_class_call_keeps_its_edge(tmp_path):
    """Positive control: the consumer holds a Builder field and calls a method only Builder declares."""
    consumer = PKG + ("public class Consumer {\n    private final Builder builder = new Builder();\n"
                      "    public int total(String s) { return builder.append(s).length(); }\n}\n")
    candidates = _seed(tmp_path, {BUILDER: _twin("Builder", "LegacyBuilder"), CONSUMER: consumer})

    _text, edges = build_planning_structural_evidence(str(tmp_path), candidates)

    assert edges == {CONSUMER: [BUILDER]}, edges


def test_a_cross_class_call_through_a_typed_parameter_keeps_its_edge_without_instantiation(tmp_path):
    """The bare-call relation still carries a real reference when the caller names the callee's type (here a
    parameter type) and never instantiates it."""
    consumer = PKG + ("public class Consumer {\n"
                      "    public int total(Builder builder, String s) { return builder.append(s).length(); }\n}\n")
    candidates = _seed(tmp_path, {BUILDER: _twin("Builder", "LegacyBuilder"), CONSUMER: consumer})

    _text, edges = build_planning_structural_evidence(str(tmp_path), candidates)

    assert edges == {CONSUMER: [BUILDER]}, edges


def test_an_ambiguous_method_name_resolves_to_nothing(tmp_path):
    """Negative control (the pre-existing conservative rule): ``run`` declared by two other candidates."""
    consumer = PKG + "public class Consumer {\n    public void go() { run(); }\n}\n"
    helper_a = PKG + "public class HelperA {\n    public void run() { }\n}\n"
    helper_b = PKG + "public class HelperB {\n    public void run() { }\n}\n"
    candidates = _seed(tmp_path, {CONSUMER: consumer, HELPER_A: helper_a, HELPER_B: helper_b})

    _text, edges = build_planning_structural_evidence(str(tmp_path), candidates)

    assert edges == {}, edges


def test_a_jdk_static_call_named_like_a_candidate_method_is_not_a_reference(tmp_path):
    """``CharBuffer.wrap(...)`` in Pool resolves to nothing although Builder declares ``wrap``: Pool's code never names
    Builder. Naming the type (a field) turns the same call into a reference - the discriminator, not the name."""
    builder = _twin("Builder", "LegacyBuilder").replace(
        "    public int length()", "    public static Builder wrap(char[] initial) { return new Builder(); }\n    public int length()")
    pool = PKG + ("import java.nio.CharBuffer;\npublic class Pool {\n"
                  "    public CharBuffer view(char[] data) { return CharBuffer.wrap(data, 0, data.length); }\n}\n")
    candidates = _seed(tmp_path, {BUILDER: builder, POOL: pool})
    _text, edges = build_planning_structural_evidence(str(tmp_path), candidates)
    assert edges == {}, edges

    (tmp_path / POOL).write_text(pool.replace("public class Pool {\n", "public class Pool {\n    private Builder builder;\n"))
    _text, edges = build_planning_structural_evidence(str(tmp_path), candidates)
    assert edges == {POOL: [BUILDER]}, edges


def test_an_instantiation_inside_a_comment_or_string_is_not_a_reference(tmp_path):
    """The constructor scan reads code, not Javadoc or string literals; a real ``new Builder()`` still counts."""
    commented = PKG + ("/** Example: Builder b = new Builder(); */\npublic class Consumer {\n"
                       "    public String hint() { return \"call new Builder() first\"; }\n}\n")
    candidates = _seed(tmp_path, {BUILDER: _twin("Builder", "LegacyBuilder"), CONSUMER: commented})
    _text, edges = build_planning_structural_evidence(str(tmp_path), candidates)
    assert edges == {}, edges

    (tmp_path / CONSUMER).write_text(PKG + "public class Consumer {\n    public Object make() { return new Builder(); }\n}\n")
    _text, edges = build_planning_structural_evidence(str(tmp_path), candidates)
    assert edges == {CONSUMER: [BUILDER]}, edges


def test_a_self_call_is_never_an_edge_even_when_the_twin_type_is_named_in_code(tmp_path):
    """The self-declared rule on its own: Builder holds a LegacyBuilder field (so the type IS named in code) but calls
    only its own methods; the only cross-file evidence is the field, which is not a call, an import or an
    instantiation - no edge. Dropping the self-declared check would resolve Builder's own ``append`` to the twin."""
    builder = _twin("Builder", "LegacyBuilder").replace(
        "    private char[] buffer", "    private LegacyBuilder legacy;\n    private char[] buffer")
    candidates = _seed(tmp_path, {BUILDER: builder, LEGACY: _twin("LegacyBuilder", "Builder")})

    _text, edges = build_planning_structural_evidence(str(tmp_path), candidates)

    assert edges == {}, edges


def test_python_modules_calling_their_own_same_named_functions_never_reference_each_other(tmp_path):
    """The relation is language-neutral: two modules each defining and calling their own ``helper`` produce no edge
    (before the fix each resolved to the other, the only OTHER definer)."""
    files = {
        "pkg/first.py": "def helper(x):\n    return x + 1\n\n\ndef run():\n    return helper(1)\n",
        "pkg/second.py": "def helper(x):\n    return x * 2\n\n\ndef go():\n    return helper(2)\n",
    }
    candidates = _seed(tmp_path, files)

    _text, edges = build_planning_structural_evidence(str(tmp_path), candidates)

    assert edges == {}, edges
