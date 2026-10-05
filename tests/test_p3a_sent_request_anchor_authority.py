"""P3-A: an anchor is authorized by exact current-revision source the
dispatched (fitted) Developer request actually carried - and by nothing
else. The live R2-T4 a4 retry end to end is in
tests/test_p3_developer_protocol_reproducers.py; these pin each boundary of
the rule in isolation."""
import pytest

from kriya.config import AppConfig
from kriya.workflow.attempt import _authorize_anchors
from kriya.workflow.context_budget import DeveloperRequestFit, OptionalSection, RequestCapacity
from kriya.workflow.context_package import make_context_item
from kriya.workflow.edit_capability import ANCHORED_EDIT, build_edit_capability, shown_exact_texts
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.state import GenerationState

PATH = "src/A.java"
CURRENT = "".join(f"    int line{i} = {i};\n" for i in range(1, 41))
HEAD = "".join(CURRENT.splitlines(keepends=True)[:3])            # the only span the capability shows
ANCHOR = "    int line20 = 20;\n    int line21 = 21;\n"           # real, current, outside that span
MEMBER = "".join(CURRENT.splitlines(keepends=True)[17:23])        # lines 18-23 (contains the anchor)


def _state(sent=(), members=(), current=CURRENT):
    state = GenerationState()
    state.attempt_number = 1
    state.edit_capabilities_attempt = 1
    state.edit_capabilities[PATH] = build_edit_capability(
        PATH, current, full_file=False, loci=[], budget_chars=0, shown=shown_exact_texts("member_exact", HEAD))
    state.edit_capability_sent[PATH] = tuple(sent)
    state.known_target_member_items[PATH] = tuple(members)
    return state


def _member(text, revision):
    return make_context_item(path=PATH, content=text, reason="grounded member", source_type="named_in_request",
                             trust_level="repository", tier="member_exact", is_exact=True, revision=revision,
                             member_id="A.m", start_line=18, end_line=23)


def _reason(state, search=ANCHOR, current=CURRENT):
    try:
        _authorize_anchors(state, PATH, [{"search": search, "replace": "x"}], current)
    except ValueError as refused:
        return str(refused).split(":", 1)[0]
    return None


def test_without_the_sent_request_the_anchor_stays_outside():
    assert _reason(_state()) == "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT"


def test_the_whole_current_file_sent_verbatim_authorizes_the_anchor_and_only_the_anchor():
    state = _state(sent=["=== Planned File Current Source ===\n" + CURRENT])
    before = state.edit_capabilities[PATH]
    assert _reason(state) is None
    # No widening: the capability (operations, spans, full-file authority) is unchanged.
    assert state.edit_capabilities[PATH] is before
    assert before.operations == (ANCHORED_EDIT,) and before.full_file is False
    [event] = [e for e in state.run_events if e.kind == "context.anchor_authorized_by_sent_request"]
    assert event.details["revision"] == content_revision(CURRENT)


def test_a_current_exact_member_unit_sent_verbatim_authorizes_an_anchor_inside_it():
    assert _reason(_state(sent=["ctx\n" + MEMBER], members=[_member(MEMBER, content_revision(CURRENT))])) is None
    # The member text must itself be in the sent request ...
    assert _reason(_state(sent=["ctx"], members=[_member(MEMBER, content_revision(CURRENT))])) == (
        "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT")
    # ... of the current revision ...
    assert _reason(_state(sent=["ctx\n" + MEMBER], members=[_member(MEMBER, "stale-revision")])) == (
        "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT")
    # ... and contain the anchor.
    assert _reason(_state(sent=["ctx\n" + MEMBER], members=[_member(MEMBER, content_revision(CURRENT))]),
                   search=CURRENT.splitlines(keepends=True)[30]) == "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT"


def test_text_of_another_revision_never_authorizes():
    old = CURRENT.replace("int line40 = 40;", "int line40 = 41;")      # the anchor is in both revisions
    assert _reason(_state(sent=[old])) == "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT"
    # The capability was decided on another revision than the file now holds.
    stale = _state(sent=[CURRENT], current=old)
    assert _reason(stale) == "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT"


def test_an_anchor_not_in_the_file_is_still_not_in_the_file():
    fabricated = "    int line20 = 99;\n"
    assert _reason(_state(sent=[CURRENT + fabricated]), search=fabricated) == "ANCHOR_NOT_IN_FILE"
    # Even inside a sent member unit with no recorded revision whose text is
    # not the file's (a stale rendering): sent text never makes an anchor real.
    stale_member = MEMBER.replace("int line20 = 20;", "int line20 = 99;")
    assert _reason(_state(sent=[stale_member], members=[_member(stale_member, None)]),
                   search=fabricated) == "ANCHOR_NOT_IN_FILE"


def test_only_what_the_fit_kept_is_recorded_as_sent():
    """An optional section the fit dropped was never sent (the Graphify
    over-fit case): what is recorded is the fitted prompt, not the input."""
    fit = DeveloperRequestFit(AppConfig(), None, (OptionalSection("planned_source", CURRENT, lambda budget: ""),))
    fit._capacities[None] = RequestCapacity(tokens=60)
    prompt = "Repair src/A.java.\n" + CURRENT
    fitted, _details = fit.fit("system", prompt)
    assert CURRENT not in fitted and fit.fitted == [fitted]
    assert _reason(_state(sent=fit.fitted)) == "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT"
    # A copy made for an extra section shares the record.
    extra = fit.with_section(OptionalSection("siblings", "s", lambda budget: ""))
    extra.fit("system", "Repair.\n")
    assert len(fit.fitted) == 2


@pytest.mark.parametrize("sent", [["unrelated text"], []])
def test_no_sent_text_containing_the_source_means_no_authority(sent):
    assert _reason(_state(sent=sent)) == "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT"
