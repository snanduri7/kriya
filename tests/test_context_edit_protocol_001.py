"""CONTEXT-EDIT-PROTOCOL-001: Kriya never offers a mutation operation that is
infeasible under the authoritative context of that Developer invocation.

Measured before the fix on a synthetic brownfield repair (tests/
_edit_protocol_harness.py: a module far larger than the context budget, a
goal naming one line inside an elided body; no benchmark code):
- the named line reached no Developer request's code context (7 of 7);
- the contract offered FILE CONTENT (and, for an unverified model profile,
  ONLY FILE CONTENT) although whole-file authority did not exist, so every
  full-file answer was rejected by the D1 validator and a correct SEARCH
  answer could never be accepted (conservative profile: 0 of 7 attempts);
- a fabricated SEARCH block was reported as "elided in the skeletonized
  context", and retries repeated the same context.

The shared EditCapability (kriya/workflow/edit_capability.py) is decided once
per Developer invocation and read by the contract and the validators alike."""
import pytest
from _edit_protocol_harness import (
    BIG_MEMBER_SOURCE,
    CORRECT_EDIT,
    FABRICATED_EDIT,
    FAR_EDIT,
    FAR_LINE,
    FULL_FILE,
    GOAL,
    NAMED_LINE,
    NAMED_LINE_FIX,
    REAL_PLUS_FABRICATED_EDIT,
    SMALL_SOURCE,
    TARGET,
    TARGET_SOURCE,
    UNLOCALIZED_GOAL,
    run_edit_protocol,
)
from test_prd020_milestone_requirements import _probe

from kriya.workflow import context_budget as budget
from kriya.workflow.context_budget import request_capacity
from kriya.workflow.edit_capability import (
    ANCHOR_CONTEXT_NOT_ESCALATED,
    ANCHOR_NOT_IN_FILE,
    ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT,
    ANCHORED_EDIT,
    CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE,
    FULL_FILE_REPLACEMENT,
    SHOWN_UNITS,
    build_edit_capability,
    goal_code_fragments,
    locate_fragments,
    locate_search_text,
    render_exact_spans,
    shown_exact_texts,
)
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.failure import QualityGateFailure

# A long user goal (the canonical goal is 3,350 chars): mandatory text the
# known-target budget does not count, so the request sits near capacity.
GOAL_LONG = GOAL + " " + " ".join(
    f"Case {i}: a member call with type arguments through a receiver must resolve like its plain spelling."
    for i in range(30))
UNVERIFIED_PROFILE = None  # the harness then leaves the model's capabilities unverified (full_file preferred)
# Mid-file, far from the named line and from any head/tail excerpt of TARGET_SOURCE.
FAR_HELPER_LINE = "value_100_11 = compute_11(value_100_10)"


def _code_context(user_prompt):
    return user_prompt.split("=== Task ===")[0]


def _named_locus(source=TARGET_SOURCE):
    return next(i + 1 for i, line in enumerate(source.splitlines()) if NAMED_LINE in line)


def _capabilities(run):
    return [event.details for event in run.kinds("context.edit_capability")]


def _run(tmp_path, monkeypatch, answers, **kwargs):
    return run_edit_protocol(tmp_path, monkeypatch, answers, probe=_probe, **kwargs)


def _assert_no_call_repeats_a_failed_capability(run):
    """The retry invariant: every Developer call after an edit-protocol
    failure is made under a capability (context + feasible operations) no
    earlier call on the same model was made under."""
    sent = [cap for cap in _capabilities(run) if cap["targets"]]
    stops = [e for e in run.kinds("failure.recorded") if ANCHOR_CONTEXT_NOT_ESCALATED in e.message]
    called = [cap["targets"][0]["digest"] for cap in sent
              if not any(stop.attempt == attempt for stop in stops for attempt in [_attempt_of(run, cap)])]
    assert len(called) == len(run.developer)
    assert len(set(called)) == len(called), called
    return stops


def _attempt_of(run, capability_details):
    return next(e.attempt for e in run.kinds("context.edit_capability") if e.details is capability_details)


# --- 1. skeleton text never authorizes an anchor -----------------------------------
def test_a_skeleton_alone_authorizes_no_anchor_and_no_whole_file():
    skeleton_pieces = shown_exact_texts("skeleton", TARGET_SOURCE[:4000])
    capability = build_edit_capability(TARGET, TARGET_SOURCE, full_file=False, loci=(), budget_chars=4000,
                                       shown=skeleton_pieces)
    assert skeleton_pieces == [] and capability.spans == ()
    assert capability.operations == () and not capability.feasible


def test_real_source_outside_the_exact_spans_is_not_an_authorized_anchor():
    capability = build_edit_capability(TARGET, TARGET_SOURCE, full_file=False, loci=[_named_locus()],
                                       budget_chars=4000)
    assert capability.anchor_status(NAMED_LINE, TARGET_SOURCE) is None
    assert capability.anchor_status(FAR_HELPER_LINE, TARGET_SOURCE) == ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT
    assert capability.anchor_status("callee = name_node.text.decode()", TARGET_SOURCE) == ANCHOR_NOT_IN_FILE


def test_an_anchor_outside_the_authoritative_context_is_rejected_and_then_shown(tmp_path, monkeypatch):
    """The model anchors on a real line it was never shown exactly. Before the
    fix any text in the file was accepted; now it is rejected with its own
    reason, its location becomes a locus, and the next request shows it."""
    run = _run(tmp_path, monkeypatch, [FAR_EDIT, CORRECT_EDIT], source=BIG_MEMBER_SOURCE)
    failures = [e.message for e in run.kinds("failure.recorded")]
    assert ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT in failures[0]
    first, second = _capabilities(run)[:2]
    far_locus = next(i + 1 for i, line in enumerate(BIG_MEMBER_SOURCE.splitlines()) if FAR_LINE in line)
    assert far_locus not in first["targets"][0]["loci"] and far_locus in second["targets"][0]["loci"]
    assert any(s["start_line"] <= far_locus <= s["end_line"] for s in second["targets"][0]["spans"])
    assert run.result["quality_gates_passed"] is True


# --- 2. exact windows authorize anchored edits -------------------------------------
@pytest.mark.parametrize("profile", ["production", "unverified"])
def test_the_goal_named_line_is_shown_exactly_and_its_edit_applies(tmp_path, monkeypatch, profile):
    kwargs = {} if profile == "production" else {"capabilities": UNVERIFIED_PROFILE}
    run = _run(tmp_path, monkeypatch, [CORRECT_EDIT], **kwargs)
    assert len(run.developer) == 1
    system, user = run.developer[0]
    assert NAMED_LINE in _code_context(user)
    assert "SEARCH:" in system
    assert run.result["quality_gates_passed"] is True
    assert NAMED_LINE_FIX in (run.workspace / TARGET).read_text()


# --- 3. windows are byte-exact and revision-bound ----------------------------------
def test_windows_are_byte_exact_slices_of_one_revision():
    source = "def f(a):\r\n\tx = a  \r\n    return read(x, 'y')\r\n" + "".join(f"z{i} = {i}\n" for i in range(50))
    locus = 3
    capability = build_edit_capability("m.py", source, full_file=False, loci=[locus], budget_chars=10_000)
    [span] = capability.spans
    lines = source.splitlines(keepends=True)
    assert span.text == "".join(lines[span.start_line - 1:span.end_line])
    assert span.text in source and span.revision == content_revision(source)
    assert span.text in render_exact_spans(capability)
    changed = source.replace("return read", "return  read")
    assert capability.anchor_status("return read(x, 'y')", changed) == ANCHOR_NOT_IN_FILE
    assert capability.anchor_status("x = a", changed) == ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT  # stale revision


def test_localization_uses_only_quoted_code_and_real_lines():
    assert goal_code_fragments("Fix `a = b(c)` and `word` in ```\nx = y(z)\nq\n```") == ["a = b(c)", "x = y(z)"]
    lines = ["a = b(c)", "q = 1", "a = b(c)", "a = b(c)", "a = b(c)"]
    assert locate_fragments(lines, ["a = b(c)"]) == []  # matches too many lines: localizes nothing
    assert locate_search_text(["foo = compute(bar, baz)", "other = 2"], "foo = compute(bar, baz)") == [1]
    assert locate_search_text(["foo = compute(bar, baz)"], "foo = recompute(bar, baz)") == [1]  # identifier overlap


# --- 4. an anchor failure escalates the context before the retry --------------------
def test_an_anchor_miss_widens_the_exact_window_before_the_next_call(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, [FABRICATED_EDIT], source=BIG_MEMBER_SOURCE)
    locus = _named_locus(BIG_MEMBER_SOURCE)
    windows = [next(s for s in cap["targets"][0]["spans"] if s["unit"] == "window"
                    and s["start_line"] <= locus <= s["end_line"]) for cap in _capabilities(run)]
    widths = [w["end_line"] - w["start_line"] for w in windows]
    assert len(run.developer) >= 3
    assert widths[0] < widths[1] < widths[2]  # each anchor miss widened the exact window around the locus
    assert widths == sorted(widths)  # never narrowed
    stops = _assert_no_call_repeats_a_failed_capability(run)
    assert stops and run.result["failure_category"] == "no_progress"  # the budget cap ends it, typed
    assert all(NAMED_LINE in _code_context(user) for _system, user in run.developer)


def test_every_window_gets_one_radius_so_no_locus_is_starved():
    """Measured on the offline Graphify replay: when a second locus arrived,
    the window around the goal's line shrank from 17 to 9 lines (an equal
    character share per locus starved the one among long lines). All
    windows now share the largest radius that fits."""
    long_lines = "".join(f"                        deep_{i} = compute_long_name_{i}(argument_one, argument_two)\n"
                         for i in range(60))
    short_lines = "".join(f"x{i} = {i}\n" for i in range(600))
    source = long_lines + short_lines
    loci = [30, 400]
    capability = build_edit_capability("m.py", source, full_file=False, loci=loci, budget_chars=3000, level=1)
    windows = [s for s in capability.spans if s.unit == "window"]
    assert len(windows) == 2 and not capability.uncovered_loci
    radii = {min(locus - s.start_line, s.end_line - locus) for locus, s in zip(loci, windows, strict=True)}
    assert len(radii) == 1 and radii.pop() >= 1


def test_a_rejected_anchor_locus_is_kept_after_an_intervening_stop(tmp_path, monkeypatch):
    """Measured on the offline Graphify replay: after a no-progress stop the
    next invocation derived its loci from the stop (no SEARCH text) and fell
    back to an earlier window set. A rejected anchor's real location is now
    remembered for every later invocation."""
    run = _run(tmp_path, monkeypatch, [REAL_PLUS_FABRICATED_EDIT], source=BIG_MEMBER_SOURCE)
    far_locus = next(i + 1 for i, line in enumerate(BIG_MEMBER_SOURCE.splitlines()) if FAR_LINE in line)
    failures = [e.message for e in run.kinds("failure.recorded")]
    assert ANCHOR_NOT_IN_FILE in failures[0] and any(ANCHOR_CONTEXT_NOT_ESCALATED in m for m in failures)
    later = [cap["targets"][0]["loci"] for cap in _capabilities(run)][1:]
    assert later and all(far_locus in loci for loci in later)
    _assert_no_call_repeats_a_failed_capability(run)


# --- 5. unchanged context + same failure family is not progress --------------------
def test_an_unchanged_capability_after_an_anchor_miss_is_a_typed_no_progress_stop(tmp_path, monkeypatch):
    """The named line's whole member already fits: nothing more can be shown.
    Before the fix the same context was resent to the model attempt after
    attempt; now one call is made and the run stops as no progress."""
    run = _run(tmp_path, monkeypatch, [FABRICATED_EDIT])
    failures = [e.message for e in run.kinds("failure.recorded")]
    assert ANCHOR_NOT_IN_FILE in failures[0]
    stops = _assert_no_call_repeats_a_failed_capability(run)
    assert stops and len(run.developer) < len(run.kinds("attempt.failed"))
    assert all(ANCHOR_NOT_IN_FILE in m or ANCHOR_CONTEXT_NOT_ESCALATED in m for m in failures)
    assert run.result["failure_category"] == "no_progress"
    assert (run.workspace / TARGET).read_text() == TARGET_SOURCE


# --- 6. full-file is neither offered nor accepted without authority ------------------
@pytest.mark.parametrize("profile", ["production", "unverified"])
def test_full_file_is_never_offered_or_accepted_without_authority(tmp_path, monkeypatch, profile):
    kwargs = {} if profile == "production" else {"capabilities": UNVERIFIED_PROFILE}
    run = _run(tmp_path, monkeypatch, [FULL_FILE], **kwargs)
    assert run.developer and all("FILE CONTENT:" not in system for system, _user in run.developer)
    assert all(cap["targets"][0]["operations"] == [ANCHORED_EDIT] for cap in _capabilities(run))
    messages = [e.message for e in run.kinds("failure.recorded")]
    assert "OPERATION CONTRACT FAILURE" in messages[0]
    assert not any("OUTPUT_BUDGET_UNSATISFIABLE" in m for m in messages)  # no full-file output was sized
    assert _assert_no_call_repeats_a_failed_capability(run)
    assert run.result["failure_category"] == "no_progress"
    assert (run.workspace / TARGET).read_text() == TARGET_SOURCE


# --- 7. genuine full-file authority still permits the whole file --------------------
def test_genuine_full_file_authority_still_offers_and_accepts_the_whole_file(tmp_path, monkeypatch):
    rewritten = SMALL_SOURCE.replace(NAMED_LINE, NAMED_LINE_FIX)
    # First attempt, no failure: the full-file request asks for raw content.
    run = _run(tmp_path, monkeypatch, [rewritten], source=SMALL_SOURCE)
    [cap] = _capabilities(run)
    assert "MODE: REPAIR_WITH_FULL_FILE" in run.developer[0][0]
    assert set(cap["targets"][0]["operations"]) == {ANCHORED_EDIT, FULL_FILE_REPLACEMENT}
    assert cap["targets"][0]["full_file"] is True
    assert run.result["quality_gates_passed"] is True
    assert (run.workspace / TARGET).read_text() == rewritten


@pytest.mark.parametrize("window", [4096, 32768])
def test_a_target_that_fits_whole_keeps_its_whole_file_authority_in_a_small_window(tmp_path, monkeypatch, window):
    """Found by the adjacent suite: holding the window reserve back
    unconditionally left a small window no room for a 15-line known target,
    so it lost the whole-file rendering (and authority) it had before. The
    reserve is held back only when a target is not shown whole."""
    rewritten = SMALL_SOURCE.replace(NAMED_LINE, NAMED_LINE_FIX)
    run = _run(tmp_path, monkeypatch, [rewritten], source=SMALL_SOURCE, window=window)
    [cap] = _capabilities(run)
    assert cap["targets"][0]["full_file"] is True
    assert [s["unit"] for s in cap["targets"][0]["spans"]] == ["shown_full"]
    assert SMALL_SOURCE in run.developer[0][1]
    assert (run.workspace / TARGET).read_text() == rewritten


# --- 8. no feasible operation fails closed before inference -------------------------
def test_no_feasible_operation_stops_before_any_developer_request(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, [CORRECT_EDIT], goal=UNLOCALIZED_GOAL)
    assert run.developer == []
    assert run.result["failure_category"] == "context_edit_protocol_unsatisfiable"
    assert run.result["environment_failure"].startswith(CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE)
    [cap] = _capabilities(run)
    assert cap["targets"][0]["operations"] == []
    assert (run.workspace / TARGET).read_text() == TARGET_SOURCE


def test_a_locus_that_cannot_be_shown_within_the_budget_is_infeasible():
    capability = build_edit_capability(TARGET, TARGET_SOURCE, full_file=False, loci=[_named_locus()], budget_chars=10)
    assert capability.uncovered_loci == (_named_locus(),) and not capability.anchored
    # Other exact source being shown does not make an uncovered locus anchorable.
    head = "".join(TARGET_SOURCE.splitlines(keepends=True)[:20])
    partly = build_edit_capability(TARGET, TARGET_SOURCE, full_file=False, loci=[_named_locus()], budget_chars=10,
                                   shown=[("member_exact", head)])
    assert partly.spans and partly.uncovered_loci == (_named_locus(),)
    assert partly.operations == () and not partly.feasible
    assert build_edit_capability(TARGET, TARGET_SOURCE, full_file=True, loci=[_named_locus()],
                                 budget_chars=10).operations == (FULL_FILE_REPLACEMENT,)


# --- 9. large-file context stays within its budget ----------------------------------
def test_large_file_windows_stay_within_their_budget_and_every_request_fits(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, [FABRICATED_EDIT], source=BIG_MEMBER_SOURCE)
    capacity = request_capacity(run.config)
    lines = BIG_MEMBER_SOURCE.splitlines(keepends=True)
    for cap in _capabilities(run):
        derived = [s for s in cap["targets"][0]["spans"] if s["unit"] not in SHOWN_UNITS]
        assert derived
        assert sum(len("".join(lines[s["start_line"] - 1:s["end_line"]])) for s in derived) <= cap["budget_chars"]
    for system, user in run.developer:
        assert capacity.count(system) + capacity.count(user) <= capacity.tokens


def test_exact_windows_are_budget_neutral(tmp_path, monkeypatch):
    """Measured on the offline Graphify replay: attempt 1 grew from 15,477
    to 17,255 tokens against a 16,096 capacity, because the windows were
    added on top of a known-target package already sized to fill its
    budget. The window share is now deducted before that package is sized,
    so a request with windows is no larger than the same request without
    the feature (up to the windows' own marker lines)."""
    from kriya.workflow import attempt, edit_capability

    capacity = request_capacity(_run(tmp_path / "w", monkeypatch, [CORRECT_EDIT], goal=GOAL_LONG,
                                     max_tokens=16384).config)
    with_windows = _run(tmp_path / "a", monkeypatch, [CORRECT_EDIT], goal=GOAL_LONG, max_tokens=16384)
    monkeypatch.setattr(edit_capability, "render_exact_spans", lambda _capability: "")
    monkeypatch.setattr(attempt, "exact_window_reserve", lambda _window: 0)
    without = _run(tmp_path / "b", monkeypatch, [CORRECT_EDIT], goal=GOAL_LONG, max_tokens=16384)
    [(system_a, user_a)], [(system_b, user_b)] = with_windows.developer, without.developer
    assert NAMED_LINE in _code_context(user_a) and NAMED_LINE not in _code_context(user_b)
    size_a = capacity.count(system_a) + capacity.count(user_a)
    size_b = capacity.count(system_b) + capacity.count(user_b)
    markers = "\n".join(line for line in user_a.splitlines()
                        if line.startswith(("=== EXACT CURRENT SOURCE", "=== END EXACT SOURCE")))
    assert markers
    assert size_a <= size_b + capacity.count(markers), (size_a, size_b)


@pytest.mark.parametrize("where", ["optional", "mandatory"])
def test_only_mandatory_text_counts_as_shown_source(tmp_path, where):
    """Measured on the offline Graphify replay: a retry's optional
    planned-source section held the whole target when the capability was
    decided, and the request fit then cut it to ~95 tokens - the capability
    claimed a whole-file exact span the model never received. Optional
    sections can be trimmed, so they never make source shown."""
    from test_prd016_adaptive_budget import _attempt_ctx, _cfg

    from kriya.workflow.attempt import _decide_edit_capabilities
    from kriya.workflow.state import GenerationState

    source = "def calc(a, b):\n    return a + b\n"
    (tmp_path / "calc.py").write_text(source)
    ctx = _attempt_ctx(tmp_path, _cfg(), developer=None)
    shown = f"=== File: calc.py ===\n{source}"
    kwargs = {"known_target_files": ["calc.py"], "existing_code_context": "HEAD\n" + shown}
    if where == "optional":
        kwargs["optional_sections"] = (budget.OptionalSection("planned_source", shown, lambda _b: ""),)
    state = GenerationState()
    state.attempt_number = 1
    if where == "optional":
        with pytest.raises(QualityGateFailure) as stopped:
            _decide_edit_capabilities(state, ctx, kwargs)
        assert stopped.value.failure.diagnostics["reason_code"] == CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE
    else:
        capabilities = _decide_edit_capabilities(state, ctx, kwargs)
        assert capabilities["calc.py"].operations == (ANCHORED_EDIT,)
        assert [s.unit for s in capabilities["calc.py"].spans] == ["shown_full"]


# --- 10. no unconditional full-file reinjection -------------------------------------
def test_the_whole_target_is_never_reinjected(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, [FABRICATED_EDIT])
    assert run.developer
    for _system, user in run.developer:
        assert TARGET_SOURCE not in user
        assert FAR_HELPER_LINE not in _code_context(user)
