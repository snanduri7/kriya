"""P3-D: structural new-member insertion authority (kriya/workflow/insertion_locus.py).

The live A3 shape end to end (real NumberUtils.java, frozen goal, real direct workflow) and every control the P3-D
task names: the locus is decided from the parser at the exact revision, is zero-width, and authorizes only a pure
insertion at itself whose carrier the dispatched request carried."""
import dataclasses
from pathlib import Path

import pytest
from _edit_protocol_harness import run_edit_protocol
from test_prd020_milestone_requirements import _probe

from kriya.workflow.attempt import _authorize_anchors
from kriya.workflow.edit_capability import (
    ANCHORED_EDIT,
    INSERTION_ONLY,
    build_edit_capability,
    render_exact_spans,
)
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.insertion_locus import (
    INSERTION_LOCUS_NOT_SENT,
    INSERTION_OUTSIDE_LOCUS,
    INSERTION_STRUCTURE_CHANGED,
    STALE_INSERTION_LOCUS,
    TIER_AFTER_GROUNDED_MEMBER,
    TIER_OWNER_CLOSING_DELIMITER,
    planned_new_members,
    resolve_insertion_locus,
    structural_insertion_readiness,
    verify_insertion,
)
from kriya.workflow.language_adapters import JAVA_ADAPTER, PYTHON_ADAPTER, Capability, CapabilityStatus
from kriya.workflow.state import GenerationState

FIX = Path(__file__).resolve().parent / "fixtures" / "p3d"
A3_TARGET = "src/main/java/org/apache/commons/lang3/math/NumberUtils.java"
A3_SOURCE = (FIX / "NumberUtils.java.txt").read_text()
A3_GOAL = (FIX / "a3_goal.txt").read_text()

PATH = "src/main/java/p/Box.java"
GOAL = "Add `public int clamp(int v)` to Box."
SMALL = (
    "package p;\n"
    "\n"
    "public class Box {\n"
    "    private int size;\n"
    "\n"
    "    public int size() {\n"
    "        return size;\n"
    "    }\n"
    "}\n"
)
NEW_METHOD = "\n    public int clamp(int v) {\n        return Math.min(v, size);\n    }\n"


def _locus(content=SMALL, goal=GOAL, path=PATH, grounded=()):
    locus, reason = resolve_insertion_locus(path, content, goal=goal, revision=content_revision(content),
                                            grounded_keys=grounded)
    return locus, reason


def _insert(content, locus, text=NEW_METHOD):
    """``text`` added at the start of the locus's gap (right after the anchor
    member)."""
    return content[:locus.gap_start] + text.rstrip("\n") + content[locus.gap_start:]


def _state(content=SMALL, locus=None, sent=None, path=PATH, loci=()):
    state = GenerationState()
    state.attempt_number = 1
    state.edit_capabilities_attempt = 1
    state.edit_capabilities[path] = build_edit_capability(path, content, full_file=False, loci=list(loci),
                                                          budget_chars=4000, insertion=locus)
    state.edit_capability_sent[path] = tuple(
        sent if sent is not None else [render_exact_spans(state.edit_capabilities[path])])
    return state


def _refusal(state, edit, content=SMALL, path=PATH):
    try:
        _authorize_anchors(state, path, [edit], content)
    except ValueError as refused:
        return str(refused)
    return None


def _carrier_edit(locus, replace):
    return {"search": locus.carrier.rstrip("\n"), "replace": replace}


def _good_edit(locus, content=SMALL):
    """SEARCH the carrier, REPLACE it with the carrier plus the new member at
    the gap - what the rendered instruction asks for."""
    start = sum(len(line) for line in content.splitlines(keepends=True)[:locus.start_line - 1])
    head = content[start:locus.gap_start]
    tail = content[locus.gap_start:start + len(locus.carrier)]
    return _carrier_edit(locus, (head + NEW_METHOD.rstrip("\n") + tail).rstrip("\n"))


# --- 1. the A3 shape: a large class and a new method -----------------------------------------------------------------
A3_LAST = A3_SOURCE.rstrip("\n").rsplit("\n", 2)[1]
A3_CLOSING = A3_SOURCE.rstrip("\n").rsplit("\n", 1)[1]
A3_INSERT = ("FIX ANALYSIS: add clamp.\nSEARCH:\n" + A3_LAST + "\n" + A3_CLOSING + "\nREPLACE:\n" + A3_LAST
             + "\n\n    public static int clamp(final int value, final int min, final int max) {\n"
             "        if (min > max) {\n            throw new IllegalArgumentException(\"min > max\");\n        }\n"
             "        return Math.max(min, Math.min(max, value));\n    }\n" + A3_CLOSING + "\n")


def test_1_a3_large_class_new_method_is_constructible_without_the_whole_file(tmp_path, monkeypatch):
    run = run_edit_protocol(tmp_path, monkeypatch, [A3_INSERT], probe=_probe, goal=A3_GOAL, source=A3_SOURCE,
                            target=A3_TARGET)
    [target] = [t for e in run.kinds("context.edit_capability") for t in e.details["targets"]]
    locus = target["insertion"]
    assert len(run.developer) == 1
    assert target["operations"] == [ANCHORED_EDIT] and target["full_file"] is False
    assert locus["tier"] == TIER_OWNER_CLOSING_DELIMITER
    assert locus["owner"] == "org.apache.commons.lang3.math.NumberUtils"
    assert locus["revision"] == content_revision(A3_SOURCE)
    assert locus["planned_members"] == ["clamp"]
    sent = "".join(system + user for system, user in run.developer)
    carrier = "".join(A3_SOURCE.splitlines(keepends=True)[locus["start_line"] - 1:locus["end_line"]])
    assert carrier in sent and A3_SOURCE.strip() not in sent
    assert "public class NumberUtils" in sent             # owner signature from the existing skeleton package
    assert len(run.kinds("context.structural_insertion_authorized")) == 1
    after = (run.workspace / A3_TARGET).read_text()
    assert run.result.get("quality_gates_passed") is True
    assert after.startswith(A3_SOURCE[:-len(A3_CLOSING) - 1]) and after.endswith(A3_CLOSING + "\n")
    assert "public static int clamp(final int value" in after


def test_1_pre_fix_shape_has_no_feasible_operation_without_the_locus():
    without = build_edit_capability(A3_TARGET, A3_SOURCE, full_file=False, loci=[], budget_chars=4072)
    locus, _ = _locus(A3_SOURCE, A3_GOAL, A3_TARGET)
    with_locus = build_edit_capability(A3_TARGET, A3_SOURCE, full_file=False, loci=[], budget_chars=4072,
                                       insertion=locus)
    assert without.operations == ()
    assert with_locus.operations == (ANCHORED_EDIT,) and with_locus.full_file is False
    assert with_locus.digest != without.digest


# --- 2. a small class, the same mechanism ------------------------------------------------------------------------------
def test_2_small_class_same_mechanism():
    locus, reason = _locus()
    assert reason == "" and locus.tier == TIER_OWNER_CLOSING_DELIMITER and locus.owner_lookup_key == "p.Box"
    assert SMALL[locus.gap_end] == "}" and locus.carrier.endswith("    }\n}\n")
    state = _state(locus=locus)
    assert _refusal(state, _good_edit(locus)) is None
    assert [e.kind for e in state.run_events] == ["context.structural_insertion_authorized"]


def test_grounded_member_gives_tier_after_grounded_member():
    content = SMALL.replace("    }\n}\n", "    }\n\n    public int area() {\n        return size * size;\n    }\n}\n")
    locus, _ = _locus(content, grounded=("Box.size",))
    assert locus.tier == TIER_AFTER_GROUNDED_MEMBER
    after = _insert(content, locus)
    assert verify_insertion(locus, content, after, [locus.carrier]) is None
    assert after.index("clamp(") < after.index("area()")


# --- 3. existing-member edits stay on the ordinary anchored path --------------------------------------------------------
def test_3_existing_member_edit_uses_the_ordinary_anchored_path():
    goal = "Change `return size;` in Box.size() to return 0."
    assert _locus(goal=goal)[0] is None                 # no new member named: no insertion authority at all
    state = _state(loci=[7])
    capability = state.edit_capabilities[PATH]
    assert capability.insertion is None and capability.anchor_status("        return size;", SMALL) is None


def test_ordinary_span_wins_over_the_carrier():
    locus, _ = _locus()
    capability = build_edit_capability(PATH, SMALL, full_file=False, loci=[8], budget_chars=4000, insertion=locus)
    assert capability.anchor_status("    }", SMALL) is None              # the size() window covers it
    only = build_edit_capability(PATH, SMALL, full_file=False, loci=[], budget_chars=4000, insertion=locus)
    assert only.anchor_status("    }", SMALL) == INSERTION_ONLY


# --- 4. an ambiguous owner is refused ------------------------------------------------------------------------------------
def test_4_ambiguous_owner_refused():
    two = SMALL + "\nclass Other {\n    int x;\n}\n"
    locus, reason = _locus(two, goal="Add `public int clamp(int v)` to Box and Other.")
    assert locus is None and "ambiguous" in reason
    locus, reason = _locus(two, goal="Add `public int clamp(int v)`.", grounded=("Box", "Other"))
    assert locus is None and "ambiguous" in reason


def test_owner_by_file_name_when_goal_and_grounding_are_silent():
    two = SMALL + "\nclass Other {\n    int x;\n}\n"
    locus, _ = _locus(two, goal="Add `public int clamp(int v)`.")
    assert locus.owner_lookup_key == "p.Box"
    assert _locus(two, goal="Add `public int clamp(int v)`.", path="src/main/java/p/Nothing.java")[0] is None


# --- 5/6. stale revision, changed owner -----------------------------------------------------------------------------------
def test_5_stale_revision_refused():
    locus, _ = _locus()
    changed = SMALL.replace("private int size;", "private int size = 1;")
    assert verify_insertion(locus, changed, _insert(changed, locus), [locus.carrier]).startswith(STALE_INSERTION_LOCUS)


def test_6_owner_identity_changed_refused():
    locus, _ = _locus()
    other = dataclasses.replace(locus, owner_symbol_id=locus.owner_symbol_id.replace("Box", "Crate"))
    assert verify_insertion(other, SMALL, _insert(SMALL, locus), [locus.carrier]).startswith(STALE_INSERTION_LOCUS)


def test_carrier_digest_mismatch_refused():
    locus, _ = _locus()
    forged = dataclasses.replace(locus, carrier="    }\n}\n\n")
    assert verify_insertion(forged, SMALL, _insert(SMALL, locus), [forged.carrier]).startswith(STALE_INSERTION_LOCUS)


# --- 7. a carrier the final request did not carry authorizes nothing (P3-A) ---------------------------------------------
@pytest.mark.parametrize("sent", [(), ("the fitted request, carrier trimmed",)])
def test_7_carrier_not_in_the_sent_request_refused(sent):
    locus, _ = _locus()
    state = _state(locus=locus, sent=sent)
    assert INSERTION_LOCUS_NOT_SENT in _refusal(state, _good_edit(locus))


# --- 8/9/10. a location the model invents, another class, outside the owner ------------------------------------------------
def test_8_model_invented_location_refused():
    locus, _ = _locus()
    state = _state(locus=locus)
    refused = _refusal(state, _carrier_edit(locus, "    }\n}\n\nclass Evil {\n}"))
    assert INSERTION_OUTSIDE_LOCUS in refused or INSERTION_STRUCTURE_CHANGED in refused
    # An anchor in no span and no carrier stays outside the authoritative context.
    assert "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT" in _refusal(state, {"search": "    private int size;",
                                                                      "replace": "    private int size;\n    int y;"})


def test_9_insertion_into_another_class_of_the_same_file_refused():
    two = SMALL + "\nclass Other {\n    int x;\n}\n"
    locus, _ = _locus(two)
    assert locus.gap_end < two.index("class Other") and two[locus.gap_end] == "}"   # Box's own brace, by the parser
    at = two.index("    int x;\n") + len("    int x;\n")
    after = two[:at] + "    public int clamp(int v) {\n        return v;\n    }\n" + two[at:]
    assert verify_insertion(locus, two, after, [locus.carrier]).startswith(INSERTION_OUTSIDE_LOCUS)


def test_10_outside_the_owner_body_refused():
    locus, _ = _locus()
    after = SMALL + "class Extra {\n    int clamp(int v) { return v; }\n}\n"
    # These bytes also equal an insertion ending just before the owner's last "}\n" (inside the gap), so the
    # re-parse decides: the new declarations are outside the owner.
    assert verify_insertion(locus, SMALL, after, [locus.carrier]).startswith(INSERTION_STRUCTURE_CHANGED)
    unambiguous = SMALL + "class Extra {\n    int clamp(int v) { return v; }\n}\n// end\n"
    assert verify_insertion(locus, SMALL, unambiguous, [locus.carrier]).startswith(INSERTION_OUTSIDE_LOCUS)


# --- 11. nested-class boundaries ---------------------------------------------------------------------------------------------
NESTED = (
    "package p;\n\npublic class Box {\n    private int size;\n\n    static class Inner {\n"
    "        int depth() {\n            return 1;\n        }\n    }\n}\n"
)


def test_11_nested_class_boundaries():
    locus, _ = _locus(NESTED)
    assert locus.owner_lookup_key == "p.Box" and NESTED[locus.gap_end] == "}"
    assert NESTED[locus.gap_start - 1] == "}"           # right after Inner's closing brace, not inside Inner
    good = _insert(NESTED, locus)
    assert verify_insertion(locus, NESTED, good, [locus.carrier]) is None
    inner_end = NESTED.index("        }\n    }\n") + len("        }\n")
    into_inner = NESTED[:inner_end] + "        int clamp(int v) { return v; }\n" + NESTED[inner_end:]
    assert verify_insertion(locus, NESTED, into_inner, [locus.carrier]).startswith(INSERTION_OUTSIDE_LOCUS)
    inner, _ = _locus(NESTED, goal="Add `int clamp(int v)` to Inner.")
    assert inner.owner_lookup_key == "p.Box.Inner"


# --- 12. braces in strings and comments ------------------------------------------------------------------------------------
def test_12_braces_in_strings_and_comments():
    tricky = (
        "package p;\n\npublic class Box {\n    private String s = \"}\";\n\n    String brace() {\n"
        "        return \"}}\"; // }\n    }\n    /* } */\n}\n"
    )
    locus, _ = _locus(tricky)
    assert tricky[locus.gap_start - 1] == "}" and tricky[locus.gap_end] == "/"     # stops before the comment
    assert tricky.rindex("}") > locus.gap_end
    assert verify_insertion(locus, tricky, _insert(tricky, locus), [locus.carrier]) is None


# --- 13. annotations, generics, records, interfaces; enums and annotation types typed unsupported ----------------------------
@pytest.mark.parametrize("source,owner", [
    ("package p;\n\n@Deprecated\npublic class Box<T extends Comparable<T>> {\n    T v;\n}\n", "p.Box"),
    ("package p;\n\npublic interface Box {\n    int size();\n}\n", "p.Box"),
    ("package p;\n\npublic record Box(int size) {\n    int twice() {\n        return 2 * size;\n    }\n}\n", "p.Box"),
])
def test_13_supported_owner_kinds(source, owner):
    locus, reason = _locus(source)
    assert locus is not None, reason
    assert locus.owner_lookup_key == owner
    text = "\n    default int clamp(int v) {\n        return v;\n    }\n" if "interface" in source else NEW_METHOD
    assert verify_insertion(locus, source, _insert(source, locus, text), [locus.carrier]) is None


@pytest.mark.parametrize("source", [
    "package p;\n\npublic enum Box {\n    A, B;\n}\n",
    "package p;\n\npublic @interface Box {\n    int size();\n}\n",
])
def test_13_enum_and_annotation_type_are_typed_unsupported(source):
    locus, reason = _locus(source)
    assert locus is None and "not supported for structural insertion" in reason


def test_python_is_typed_unsupported():
    assert _locus("class Box:\n    pass\n", path="p/box.py")[1] == "structural insertion is not supported for this language"
    assert PYTHON_ADAPTER.status(Capability.STRUCTURAL_INSERTION) is CapabilityStatus.UNSUPPORTED
    assert JAVA_ADAPTER.status(Capability.STRUCTURAL_INSERTION) is CapabilityStatus.PARTIAL


# --- 14. no neighbouring-member modification ---------------------------------------------------------------------------------
def test_14_neighbouring_member_modification_refused():
    locus, _ = _locus()
    state = _state(locus=locus)
    rewritten = locus.carrier.rstrip("\n").replace("    }", "    } // touched", 1)
    assert INSERTION_OUTSIDE_LOCUS in _refusal(state, _carrier_edit(locus, rewritten + "\n" + NEW_METHOD.strip("\n")))
    neighbour = SMALL.replace("return size;", "return size + 1;")
    assert verify_insertion(locus, SMALL, _insert(neighbour, locus), [locus.carrier]).startswith(INSERTION_OUTSIDE_LOCUS)


def test_deleting_or_insertion_without_a_new_member_refused():
    locus, _ = _locus()
    assert verify_insertion(locus, SMALL, SMALL.replace("    private int size;\n", ""),
                            [locus.carrier]).startswith(INSERTION_OUTSIDE_LOCUS)
    assert verify_insertion(locus, SMALL, _insert(SMALL, locus, "\n    // just a comment\n"),
                            [locus.carrier]).startswith(INSERTION_STRUCTURE_CHANGED)


# --- 15. imports are never implied by the locus ------------------------------------------------------------------------------
def test_15_imports_need_their_own_authority():
    locus, _ = _locus()
    with_import = SMALL.replace("package p;\n", "package p;\n\nimport java.util.List;\n")
    assert verify_insertion(locus, SMALL, with_import, [locus.carrier]).startswith(INSERTION_OUTSIDE_LOCUS)
    both = with_import[:locus.gap_start + len("\nimport java.util.List;\n")] + NEW_METHOD.rstrip("\n") \
        + with_import[locus.gap_start + len("\nimport java.util.List;\n"):]
    assert verify_insertion(locus, SMALL, both, [locus.carrier]).startswith(INSERTION_OUTSIDE_LOCUS)
    state = _state(locus=locus)
    assert "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT" in _refusal(
        state, {"search": "package p;", "replace": "package p;\n\nimport java.util.List;"})


# --- 16. a resumed or later invocation never reuses a stale locus -------------------------------------------------------------
def test_16_stale_locus_is_never_reused():
    locus, _ = _locus()
    changed = SMALL.replace("private int size;", "private int size = 2;")
    capability = build_edit_capability(PATH, changed, full_file=False, loci=[], budget_chars=4000, insertion=locus)
    assert capability.insertion is None and capability.operations == ()
    state = _state(locus=locus)
    # The stale capability refuses the anchor before any insertion check; the locus itself refuses too.
    assert _refusal(state, _good_edit(locus), content=changed).startswith("ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT")
    assert verify_insertion(locus, changed, _insert(changed, locus), [locus.carrier]).startswith(STALE_INSERTION_LOCUS)
    assert not any("insertion" in field.name for field in dataclasses.fields(GenerationState))


# --- full-file authority is never granted by insertion -------------------------------------------------------------------------
def test_insertion_never_grants_full_file_authority():
    locus, _ = _locus()
    capability = _state(locus=locus).edit_capabilities[PATH]
    assert capability.full_file is False and capability.operations == (ANCHORED_EDIT,)
    assert capability.anchor_status(SMALL.rstrip("\n"), SMALL) not in (None, INSERTION_ONLY)
    rendered = render_exact_spans(capability)
    assert locus.carrier in rendered and "ADD" in rendered and SMALL not in rendered


# --- evidence, preflight ------------------------------------------------------------------------------------------------------
def test_planned_new_members_come_only_from_quoted_code_and_skip_existing():
    assert planned_new_members("Add `public int clamp(int v)` and `size()`.", ["size"]) == ["clamp"]
    assert planned_new_members("add a clamp(x) method", []) == []
    assert planned_new_members("```java\nint f(int a) { if (a > 0) return g(a); }\n```", ["g"]) == ["f"]


def test_graphify_preflight_signal():
    assert structural_insertion_readiness("A.java", True) == "YES"
    assert structural_insertion_readiness("a.py", True) == "NO"
    assert structural_insertion_readiness("A.java", False) == "NOT_REQUIRED"
