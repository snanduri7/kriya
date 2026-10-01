"""D7 (KNOW A on demo-runtime-4, 2026-10-01, run 20261001T174654-be863346):
an edit that removes the construct its own diagnosis identified is not a
diagnosis mismatch.

Live: the analysis said "Remove the incorrect `Ignition.getOrCreateIgnite()`
call" (and named `Ignition.getOrCreateIgnite(Object)`); the edit removed that
call. find_edits_ignoring_own_diagnosis looked for the quoted text, which is
signature notation and never occurs literally in code, and rejected the edit
before the compiler could judge it. A signature-notation quote now denotes the
call `Name(`; it counts as removed only when it occurs in the pre-edit source,
every occurrence lies inside the replaced regions, and none remains."""
import copy
from pathlib import Path

import pytest

from kriya.agents.response_protocol import parse_structured
from kriya.workflow.attribution import find_edits_ignoring_own_diagnosis

FIXTURES = Path(__file__).parent / "fixtures" / "d7_knowa"
PATH = "src/main/java/com/example/IgniteDemoApp.java"
ORIGINAL = """public class App {
    public static void main(String[] args) {
        Ignite ignite = Ignition.getOrCreateIgnite(ctx.getBean("cfg"));
        IgniteCache<String, String> cache = ignite.cache("c");
        System.out.println(cache.get("k"));
    }
}
"""
CALL = '        Ignite ignite = Ignition.getOrCreateIgnite(ctx.getBean("cfg"));\n'


def _edit(search, replace):
    return [{"search": search, "replace": replace}]


def test_the_live_removal_is_accepted():
    original = parse_structured((FIXTURES / "knowa_attempt1_file_response.txt").read_text(), PATH,
                                patch_allowed=False).content
    response = parse_structured((FIXTURES / "knowa_attempt4_edit_response.txt").read_text(), PATH, patch_allowed=True)
    assert "Ignition.getOrCreateIgnite(" in original
    assert find_edits_ignoring_own_diagnosis(response.analysis, response.edit_dicts(), None, original) is None


@pytest.mark.parametrize("quote", ["Ignition.getOrCreateIgnite(Object)", "Ignition.getOrCreateIgnite()",
                                   "Ignition.getOrCreateIgnite(ApplicationContext, String)"])
def test_a_removed_call_named_in_signature_notation_is_accepted(quote):
    analysis = f"Remove the incorrect `{quote}` call; it does not exist in Ignite 2.18."
    edits = _edit(CALL, "        Ignite ignite = Ignition.ignite();\n")
    assert find_edits_ignoring_own_diagnosis(analysis, edits, None, ORIGINAL) is None


def test_a_full_file_rewrite_that_removes_the_call_is_accepted():
    analysis = "The `Ignition.getOrCreateIgnite(Object)` method does not exist."
    content = ORIGINAL.replace(CALL, "        Ignite ignite = Ignition.ignite();\n")
    assert find_edits_ignoring_own_diagnosis(analysis, None, content, ORIGINAL) is None


def test_a_removed_property_is_accepted():
    """The existing literal removal signal, unchanged."""
    xml = '<bean>\n    <property name="gridName" value="n"/>\n    <property name="peerClassLoadingEnabled" value="true"/>\n</bean>\n'
    analysis = 'The `<property name="gridName" value="n"/>` is invalid on Ignite 2.x; remove it.'
    edits = _edit('    <property name="gridName" value="n"/>\n', "")
    assert find_edits_ignoring_own_diagnosis(analysis, edits, None, xml) is None


@pytest.mark.parametrize(("analysis", "edits"), [
    # the diagnosed construct never existed in the pre-edit source
    ("Remove the incorrect `Ignition.startIgnite(Object)` call.",
     _edit('        System.out.println(cache.get("k"));\n', '        System.out.println(cache.get("key"));\n')),
    # an unrelated location changed while the diagnosed call remains
    ("Remove the incorrect `Ignition.getOrCreateIgnite(Object)` call.",
     _edit('        System.out.println(cache.get("k"));\n', '        System.out.println(cache.get("key"));\n')),
    # the call moved, it was not removed
    ("Remove the incorrect `Ignition.getOrCreateIgnite(Object)` call.",
     _edit(CALL, '        Ignite ignite = Ignition.getOrCreateIgnite(\n            ctx.getBean("cfg"));\n')),
    # a different name whose text is a fragment of the removed one: no fuzzy or substring disappearance
    ("Remove the incorrect `Ignite(Object)` call.", _edit(CALL, "        Ignite ignite = null;\n")),
    ("Remove the incorrect `CreateIgnite(Object)` call.", _edit(CALL, "        Ignite ignite = null;\n")),
    ("Remove the incorrect `getOrCreateIgnite(Object)` call.", _edit(CALL, "        Ignite ignite = null;\n")),
])
def test_a_diagnosis_the_edit_does_not_implement_is_still_a_mismatch(analysis, edits):
    assert find_edits_ignoring_own_diagnosis(analysis, edits, None, ORIGINAL) is not None


def test_only_some_occurrences_removed_is_still_a_mismatch():
    original = ORIGINAL.replace(CALL, CALL + CALL.replace("ignite =", "other ="))
    analysis = "Remove the incorrect `Ignition.getOrCreateIgnite(Object)` call."
    assert find_edits_ignoring_own_diagnosis(analysis, _edit(CALL, ""), None, original) is not None


def test_the_check_decides_only_the_verdict():
    """Authority and file scope are the caller's: the check reads its inputs, never changes them."""
    edits = _edit(CALL, "        Ignite ignite = Ignition.ignite();\n")
    before = copy.deepcopy(edits)
    find_edits_ignoring_own_diagnosis("Remove `Ignition.getOrCreateIgnite(Object)`.", edits, None, ORIGINAL)
    assert edits == before
