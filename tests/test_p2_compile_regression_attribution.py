"""P2 decision logic in isolation (the T4 chain end to end is
tests/test_p2_regression_attribution_reproducer.py):

- ``attribute_compile_regression``: a test-compilation regression is the
  candidate's only under a green, comparable PRE (level 1 NEW_FAILURE), with no
  per-test evidence, and with every compiler ERROR resolving to exactly one
  file in the tree - each negative control keeps REGRESSION_UNATTRIBUTED;
- ``_seed_grounded_loci``: a carried locus is honoured only on the exact
  revision it was observed on."""
import _p2_t4_shape as t4
import pytest

from kriya.workflow.compile_regression import attribute_compile_regression, compiler_error_locations
from kriya.workflow.edit_safety import read_file_revision
from kriya.workflow.state import GenerationState
from kriya.workflow.validation_baseline import BaselineDeltaResult, DeltaClassification, Level1Delta
from kriya.workflow.workflow import _seed_grounded_loci

NEW = DeltaClassification.NEW_FAILURE


def _delta(level1=NEW, level2_available=False):
    return BaselineDeltaResult(level1=Level1Delta(level1, None, "fp"), level2={}, aggregate_drop_detected=False,
                               blocking=True, blocking_reasons=("level1",), level2_available=level2_available)


@pytest.fixture
def tree(tmp_path):
    for rel in (t4.MAIN, t4.TEST, "src/test/java/org/apache/commons/lang3/CharSetTest.java"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("class X {}\n")
    return tmp_path


def test_the_t4_output_is_attributed_to_the_error_locator_only(tree):
    """The same output carries a COMPILATION WARNING locator (CharSetTest);
    only the ERROR counts."""
    attribution = attribute_compile_regression(_delta(), t4.T4_OUTPUT, str(tree))
    assert attribution.files == (t4.TEST,) and attribution.locations == ((t4.TEST, 167),)


def test_javac_and_gradle_error_shapes_are_recognised(tree):
    output = f"/w/{t4.MAIN}:12: error: cannot find symbol\n"
    assert attribute_compile_regression(_delta(), output, str(tree)).locations == ((t4.MAIN, 12),)
    assert compiler_error_locations(f"[ERROR] /w/{t4.TEST}:[3,1] x\n/w/{t4.MAIN}:9: warning: y\n") == [
        (f"/w/{t4.TEST}", 3)]


@pytest.mark.parametrize("delta", [
    _delta(level2_available=True),                                  # per-test evidence keeps its own rules
    _delta(level1=DeltaClassification.PRE_EXISTING_FAILURE),        # red PRE, same failure
    _delta(level1=DeltaClassification.CHANGED_FAILURE),             # red PRE, different failure
    _delta(level1=DeltaClassification.NOT_COMPARABLE),              # another environment
    _delta(level1=DeltaClassification.INFRASTRUCTURE_ENVIRONMENT_FAILURE),
    None,
])
def test_without_a_green_comparable_pre_and_no_per_test_evidence_nothing_is_attributed(tree, delta):
    assert attribute_compile_regression(delta, t4.T4_OUTPUT, str(tree)) is None


@pytest.mark.parametrize("output", [
    "",                                                                         # nothing located
    "[WARNING] /w/src/test/java/org/apache/commons/lang3/CharSetTest.java:[394,55] varargs\n",   # warning only
    "\tat org.apache.commons.lang3.ArrayFillTest.x(ArrayFillTest.java:167)\n",   # stack frame, not a compiler error
    "[ERROR] The forked VM terminated without properly saying goodbye. VM crash or System.exit called?\n",
    "[ERROR] /w/src/test/java/org/apache/commons/lang3/Missing.java:[1,1] x\n",  # resolves to no file
    "[ERROR] /elsewhere/Other.java:[1,1] x\n",                                   # outside the tree
    t4.T4_OUTPUT + "[ERROR] /w/src/test/java/org/apache/commons/lang3/Missing.java:[2,1] y\n",  # one unresolved
])
def test_any_unlocated_or_unresolvable_compiler_error_keeps_it_unattributed(tree, output):
    assert attribute_compile_regression(_delta(), output, str(tree)) is None


def test_an_ambiguous_resolution_keeps_it_unattributed(tree):
    """Two tree files are both suffixes of the diagnostic's path: never guessed."""
    for rel in ("a/Dup.java", "x/a/Dup.java"):
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text("class Dup {}\n")
    assert attribute_compile_regression(_delta(), "[ERROR] /w/x/a/Dup.java:[1,1] x\n", str(tree)) is None
    # Only one is a suffix of this path: resolved.
    assert attribute_compile_regression(_delta(), "[ERROR] /w/y/a/Dup.java:[1,1] x\n", str(tree)).files == (
        "a/Dup.java",)


def test_a_locator_in_the_candidates_own_file_is_attributed_like_any_other(tree):
    attribution = attribute_compile_regression(_delta(), f"[ERROR] /kriya/workspace/{t4.MAIN}:[40,9] x\n", str(tree))
    assert attribution.files == (t4.MAIN,)


def test_a_carried_locus_is_seeded_only_on_its_own_revision(tmp_path):
    tree = tmp_path / "wt"
    tree.mkdir()
    for path in (tree / "A.java", tree / "B.java", tmp_path / "A.java"):
        path.write_text("class A {}\n" * 200)
    fresh = read_file_revision(str(tree / "A.java"))
    stale = read_file_revision(str(tree / "B.java"))
    (tree / "B.java").write_text("class B {}\n" * 200)                   # changed since it was observed
    state = GenerationState()
    _seed_grounded_loci(state, str(tree), [
        {"filepath": "A.java", "line": 167, "revision": fresh},
        {"filepath": "B.java", "line": 12, "revision": stale},
        {"filepath": "../A.java", "line": 1, "revision": fresh},            # outside the tree (it exists)
        {"filepath": "Missing.java", "line": 1, "revision": fresh},
        {"filepath": "A.java", "line": "167", "revision": fresh},           # not a line number
    ])
    assert state.edit_anchor_loci == {"A.java": [167]}
    [event] = [e for e in state.run_events if e.kind == "recovery.grounded_loci"]
    assert event.details["seeded"] == [{"filepath": "A.java", "line": 167}]
    assert [d["filepath"] for d in event.details["dropped"]] == ["B.java", "../A.java", "Missing.java", "A.java"]
