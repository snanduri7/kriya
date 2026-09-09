from kriya.analyzer.java_members import extract_java_members
from kriya.workflow.review_context import (
    CONFIDENCE_EVIDENCE_INSUFFICIENT,
    CONFIDENCE_PROVEN_ISSUE,
    CONFIDENCE_REQUIRES_RUNTIME,
    CONFIDENCE_STRONG_STATIC_INDICATION,
    PROPOSAL_APPROVAL_NOT_APPROVED,
    PROPOSAL_AUTHORITY_ADVISORY_ONLY,
    ProposedModification,
    StructuredFinding,
    adjudicate_findings,
    build_member_evidence_ids,
    build_proposed_modification,
    build_relation_evidence_ids,
    build_review_batches,
    build_review_repository_context,
    build_reviewer_verified_evidence,
    build_structured_review_report,
    check_member_coverage,
    compute_member_coverage,
    find_java_repo_root,
    format_adjudicated_review,
    format_java_member_inventory,
    format_member_evidence_registry,
    format_proposed_modification,
    format_relation_evidence_registry,
    format_review_repository_context,
    parse_and_validate_run_guidance,
    parse_member_reviews,
    parse_structured_findings,
)


_TARGET_SOURCE = (
    "public class Target implements TargetInterface {\n"
    "    public Target(Collaborator c) {\n"
    "    }\n"
    "\n"
    "    public void doWork() {\n"
    "    }\n"
    "}\n"
)


def _write_a1_fixture_repo(tmp_path, extra_unrelated_files=0):
    (tmp_path / "Target.java").write_text(_TARGET_SOURCE)
    (tmp_path / "TargetInterface.java").write_text("public interface TargetInterface {\n}\n")
    (tmp_path / "Collaborator.java").write_text("public class Collaborator {\n}\n")
    (tmp_path / "TargetTest.java").write_text("public class TargetTest {\n}\n")
    for i in range(extra_unrelated_files):
        (tmp_path / f"Unrelated{i}.java").write_text(f"public class Unrelated{i} {{\n}}\n")


def test_build_review_repository_context_finds_implements_collaborator_and_test_relations(tmp_path):
    """A1-P1 Gap 2: repository context must find the three deterministic
    relationship kinds the review's required section ordering depends on -
    the interface the target implements, a constructor-injected
    collaborator type, and a test file matched by naming convention (not
    a fabricated DependencyGraph edge - see this module's own docstring)."""
    _write_a1_fixture_repo(tmp_path)
    members = extract_java_members(_TARGET_SOURCE)

    ctx = build_review_repository_context(str(tmp_path), "Target.java", _TARGET_SOURCE, members)

    assert ctx.target_type == "Target"
    assert ctx.implements == ("TargetInterface",)
    relations = {(rf.relation, rf.relpath) for rf in ctx.related_files}
    assert ("implements", "TargetInterface.java") in relations
    assert ("collaborator", "Collaborator.java") in relations
    assert ("test", "TargetTest.java") in relations


def test_build_review_repository_context_is_bounded_not_entire_repository(tmp_path):
    """Must never degrade into dumping the whole repository: a repo with
    many genuinely unrelated files should still produce a small, bounded
    `related_files` set that never includes those unrelated files, even
    though `files_scanned` correctly reflects the larger candidate walk."""
    _write_a1_fixture_repo(tmp_path, extra_unrelated_files=20)
    members = extract_java_members(_TARGET_SOURCE)

    ctx = build_review_repository_context(str(tmp_path), "Target.java", _TARGET_SOURCE, members, max_related_files=8)

    assert ctx.files_scanned >= 20
    assert len(ctx.related_files) <= 8
    assert not any(rf.relpath.startswith("Unrelated") for rf in ctx.related_files)


def test_format_review_repository_context_labels_evidence_as_deterministic_not_model_interpretation(tmp_path):
    _write_a1_fixture_repo(tmp_path)
    members = extract_java_members(_TARGET_SOURCE)
    ctx = build_review_repository_context(str(tmp_path), "Target.java", _TARGET_SOURCE, members)

    text = format_review_repository_context(ctx)

    assert "Repository Evidence" in text
    assert "not model interpretation" in text
    assert "TargetInterface.java" in text


def test_format_java_member_inventory_marks_itself_authoritative():
    members = extract_java_members(_TARGET_SOURCE)

    text = format_java_member_inventory(members)

    assert "Deterministic Symbol Inventory" in text
    assert "authoritative" in text
    assert "Target(Collaborator)" in text
    assert "doWork()" in text


def test_check_member_coverage_reports_covered_and_not_mentioned():
    members = extract_java_members(_TARGET_SOURCE)

    result = check_member_coverage("doWork looks fine.", members)

    assert "doWork" in result["covered"]
    assert "Target" in result["not_mentioned"]


def test_find_java_repo_root_walks_up_to_a_git_marker(tmp_path):
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "src" / "main" / "java"
    nested.mkdir(parents=True)
    java_file = nested / "Target.java"
    java_file.write_text(_TARGET_SOURCE)

    root = find_java_repo_root(str(java_file))

    assert root == str(tmp_path)


def test_find_java_repo_root_falls_back_to_containing_directory_when_no_marker(tmp_path):
    java_file = tmp_path / "Target.java"
    java_file.write_text(_TARGET_SOURCE)

    root = find_java_repo_root(str(java_file))

    assert root == str(tmp_path)


def test_build_review_batches_small_content_single_batch_no_truncation():
    batches, truncated = build_review_batches([("a.py", "x = 1\n"), ("b.py", "y = 2\n")], budget=5000)

    assert len(batches) == 1
    assert "a.py" in batches[0] and "b.py" in batches[0]
    assert truncated == []


def test_build_review_batches_oversized_single_file_is_truncated_and_reported():
    big_content = "\n".join(f"x_{i} = {i}  # padding line number {i}" for i in range(200))

    batches, truncated = build_review_batches([("big.py", big_content)], budget=375)

    assert truncated == ["big.py"]
    assert len(batches) == 1
    assert "TRUNCATED" in batches[0]
    assert "x_199" not in batches[0]  # the tail never made it in


def test_build_review_batches_splits_across_multiple_batches_when_over_budget():
    padding = "\n".join(f"x_{i} = {i}  # padding" for i in range(30))
    files = [("a.py", padding), ("b.py", padding.replace("x_", "y_"))]

    batches, truncated = build_review_batches(files, budget=232)

    assert len(batches) == 2
    assert truncated == []
    assert "a.py" in batches[0] and "b.py" not in batches[0]
    assert "b.py" in batches[1] and "a.py" not in batches[1]


def test_build_review_batches_empty_input_returns_no_batches():
    batches, truncated = build_review_batches([], budget=5000)

    assert batches == []
    assert truncated == []


def test_build_reviewer_verified_evidence_includes_a_passing_run_verification():
    """Regression test for a real live bug, 2026-08-25 (ignite_qpid_protocol,
    a real Ignite+Qpid Java app): with zero visibility into what already
    passed, ReviewerAgent confidently fabricated specific runtime exceptions
    (an IgniteException, a NoClassDefFoundError) for code that had, moments
    earlier in the same run, actually compiled and RUN successfully -
    directly contradicted by real evidence Kriya already had on hand but
    never showed the Reviewer."""
    gate_outcomes = [
        {"type": "compile", "success": True, "output": "BUILD SUCCESS"},
        {
            "type": "run_verification", "success": True,
            "output": "[VERIFICATION] PASS\n\n[Grader reasoning]: matched expected output",
        },
    ]
    evidence = build_reviewer_verified_evidence(gate_outcomes)

    assert "ACTUALLY RAN" in evidence
    assert "[VERIFICATION] PASS" in evidence
    assert "do not contradict it" in evidence
    # Compile isn't surfaced separately - "Files generated" reaching Review
    # already implies it, and it adds no comparably falsifiable evidence.
    assert "BUILD SUCCESS" not in evidence


def test_build_reviewer_verified_evidence_includes_a_passing_spec_compliance_check():
    gate_outcomes = [
        {"type": "goal_spec_compliance", "success": True, "output": "All named fields present."},
    ]
    evidence = build_reviewer_verified_evidence(gate_outcomes)

    assert "Goal spec compliance check PASSED" in evidence
    assert "All named fields present." in evidence


def test_build_reviewer_verified_evidence_ignores_a_failed_gate_outcome():
    """Only a real PASS is evidence worth surfacing - a failed attempt from
    an earlier retry says nothing trustworthy about the final, successful
    one being reviewed."""
    gate_outcomes = [
        {"type": "run_verification", "success": False, "output": "boom"},
    ]
    evidence = build_reviewer_verified_evidence(gate_outcomes)

    assert evidence == ""


def test_build_reviewer_verified_evidence_returns_empty_string_when_nothing_to_report():
    """A goal with no runtime-observable behavior (run_verification never
    ran at all) must never fabricate a claim of its own."""
    gate_outcomes = [{"type": "compile", "success": True, "output": "ok"}]
    evidence = build_reviewer_verified_evidence(gate_outcomes)

    assert evidence == ""


# =====================================================================
# A1-E2: Deterministic Review Evidence Adjudication
# =====================================================================

def _member_ids_fixture():
    members = extract_java_members(_TARGET_SOURCE)
    return build_member_evidence_ids(members)


def _relation_ids_fixture(tmp_path):
    _write_a1_fixture_repo(tmp_path)
    members = extract_java_members(_TARGET_SOURCE)
    ctx = build_review_repository_context(str(tmp_path), "Target.java", _TARGET_SOURCE, members)
    return build_relation_evidence_ids(ctx.related_files)


def _finding(**overrides):
    base = dict(
        finding_id="F1", title="t", member_id=None,
        requested_confidence=CONFIDENCE_PROVEN_ISSUE,
        condition_evidence_ids=(), consequence_evidence_ids=(),
        runtime_dependency_declared=False, explanation="", recommendation=None,
    )
    base.update(overrides)
    return StructuredFinding(**base)


# --- Case 1: PROVEN + condition valid + consequence missing -> STRONG ---
def test_adjudicate_proven_with_valid_condition_and_missing_consequence_downgrades_to_strong(tmp_path):
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    f = _finding(condition_evidence_ids=(mid,), consequence_evidence_ids=())

    [result] = adjudicate_findings([f], member_ids, {})

    assert result.final_confidence == CONFIDENCE_STRONG_STATIC_INDICATION
    assert result.downgrade_reason is not None


# --- Case 2: PROVEN + condition valid + consequence valid -> PROVEN eligible ---
def test_adjudicate_proven_with_valid_condition_and_valid_consequence_remains_proven():
    member_ids = _member_ids_fixture()
    ids = list(member_ids)
    f = _finding(condition_evidence_ids=(ids[0],), consequence_evidence_ids=(ids[0],))

    [result] = adjudicate_findings([f], member_ids, {})

    assert result.final_confidence == CONFIDENCE_PROVEN_ISSUE
    assert result.downgrade_reason is None


# --- Case 3: PROVEN + fabricated consequence ID -> STRONG (member evidence) ---
def test_adjudicate_proven_with_fabricated_member_consequence_id_downgrades_to_strong():
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    f = _finding(condition_evidence_ids=(mid,), consequence_evidence_ids=("M999",))

    [result] = adjudicate_findings([f], member_ids, {})

    assert result.final_confidence == CONFIDENCE_STRONG_STATIC_INDICATION
    assert result.invalid_consequence_ids == ("M999",)


# --- historical shape 9: fabricated repository relation ID rejected ---
def test_adjudicate_proven_with_fabricated_relation_id_downgrades_to_strong(tmp_path):
    relation_ids = _relation_ids_fixture(tmp_path)
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    f = _finding(condition_evidence_ids=(mid,), consequence_evidence_ids=("R999",))

    [result] = adjudicate_findings([f], member_ids, relation_ids)

    assert result.final_confidence == CONFIDENCE_STRONG_STATIC_INDICATION
    assert result.invalid_consequence_ids == ("R999",)


# --- Case 4: PROVEN + runtime_dependency_declared -> REQUIRES_RUNTIME ---
def test_adjudicate_proven_with_runtime_dependency_declared_forces_requires_runtime():
    member_ids = _member_ids_fixture()
    ids = list(member_ids)
    f = _finding(
        condition_evidence_ids=(ids[0],), consequence_evidence_ids=(ids[0],),
        runtime_dependency_declared=True,
    )

    [result] = adjudicate_findings([f], member_ids, {})

    assert result.final_confidence == CONFIDENCE_REQUIRES_RUNTIME
    assert result.downgrade_reason is not None


# --- Case 5: STRONG + valid condition -> STRONG ---
def test_adjudicate_strong_with_valid_condition_remains_strong():
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    f = _finding(requested_confidence=CONFIDENCE_STRONG_STATIC_INDICATION, condition_evidence_ids=(mid,))

    [result] = adjudicate_findings([f], member_ids, {})

    assert result.final_confidence == CONFIDENCE_STRONG_STATIC_INDICATION
    assert result.downgrade_reason is None


# --- Case 6: STRONG + no valid condition -> EVIDENCE_INSUFFICIENT ---
def test_adjudicate_strong_with_no_valid_condition_is_evidence_insufficient():
    member_ids = _member_ids_fixture()
    f = _finding(requested_confidence=CONFIDENCE_STRONG_STATIC_INDICATION, condition_evidence_ids=("M999",))

    [result] = adjudicate_findings([f], member_ids, {})

    assert result.final_confidence == CONFIDENCE_EVIDENCE_INSUFFICIENT


# --- Case 7: REQUIRES_RUNTIME -> accepted, invalid ids still tracked not silently passed ---
def test_adjudicate_requires_runtime_is_accepted_and_tracks_invalid_ids():
    member_ids = _member_ids_fixture()
    f = _finding(
        requested_confidence=CONFIDENCE_REQUIRES_RUNTIME,
        condition_evidence_ids=("M999",), consequence_evidence_ids=(),
    )

    [result] = adjudicate_findings([f], member_ids, {})

    assert result.final_confidence == CONFIDENCE_REQUIRES_RUNTIME
    assert result.invalid_condition_ids == ("M999",)


# --- Case 8: fabricated member ID as CONDITION evidence never satisfies PROVEN ---
def test_adjudicate_proven_with_only_fabricated_condition_id_is_evidence_insufficient():
    member_ids = _member_ids_fixture()
    f = _finding(condition_evidence_ids=("M999",), consequence_evidence_ids=("M999",))

    [result] = adjudicate_findings([f], member_ids, {})

    assert result.final_confidence == CONFIDENCE_EVIDENCE_INSUFFICIENT
    assert result.invalid_condition_ids == ("M999",)


# --- Cases 12/13: evidence ids are deterministic and stable for the same input ---
def test_member_evidence_ids_are_deterministic_for_the_same_ordered_input():
    members = extract_java_members(_TARGET_SOURCE)

    ids_a = build_member_evidence_ids(members)
    ids_b = build_member_evidence_ids(members)

    assert list(ids_a.keys()) == list(ids_b.keys())
    assert set(ids_a.keys()) == {"M1", "M2"}  # _TARGET_SOURCE: the constructor + doWork()


def test_relation_evidence_ids_are_deterministic_and_ordered(tmp_path):
    relation_ids_a = _relation_ids_fixture(tmp_path)
    relation_ids_b = _relation_ids_fixture(tmp_path)

    assert list(relation_ids_a.keys()) == list(relation_ids_b.keys())
    assert list(relation_ids_a.keys())[0] == "R1"


# --- Cases 10/11: deterministic member coverage via exact id-set comparison ---
def test_compute_member_coverage_detects_missing_and_invented_ids():
    member_ids = {"M1": None, "M2": None}

    coverage = compute_member_coverage(member_ids, ["M1", "M99"])

    assert coverage["missing"] == ["M2"]
    assert coverage["invented"] == ["M99"]


def test_compute_member_coverage_full_coverage_reports_nothing():
    member_ids = {"M1": None, "M2": None}

    coverage = compute_member_coverage(member_ids, ["M1", "M2"])

    assert coverage["missing"] == []
    assert coverage["invented"] == []


# --- parse_structured_findings defensive parsing ---
def test_parse_structured_findings_normalizes_confidence_string_with_spaces():
    [f] = parse_structured_findings([{
        "finding_id": "F1", "title": "t", "requested_confidence": "PROVEN ISSUE",
        "condition_evidence_ids": ["M1"], "consequence_evidence_ids": ["M1"],
    }])

    assert f.requested_confidence == CONFIDENCE_PROVEN_ISSUE


def test_parse_structured_findings_skips_malformed_entries_without_raising():
    findings = parse_structured_findings(["not a dict", {"title": "ok", "requested_confidence": "STRONG_STATIC_INDICATION"}, 42])

    assert len(findings) == 1
    assert findings[0].title == "ok"


def test_parse_structured_findings_returns_empty_list_for_malformed_top_level_shape():
    assert parse_structured_findings({"not": "a list"}) == []
    assert parse_structured_findings(None) == []


# --- run_guidance evidence-id validation ---
def test_run_guidance_drops_unresolvable_evidence_ids_but_keeps_the_statement():
    member_ids = _member_ids_fixture()
    raw = {"statements": [{"text": "run mvn", "evidence_ids": ["M999"]}], "not_determinable": ["ports"]}

    statements, gaps = parse_and_validate_run_guidance(raw, member_ids, {})

    assert statements == [("run mvn", ())]
    assert gaps == ["ports"]


def test_run_guidance_keeps_valid_evidence_ids():
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    raw = {"statements": [{"text": "fact", "evidence_ids": [mid]}], "not_determinable": []}

    statements, gaps = parse_and_validate_run_guidance(raw, member_ids, {})

    assert statements == [("fact", (mid,))]


# --- Case 14: formatter exposes requested vs final confidence after downgrade ---
def test_format_adjudicated_review_shows_requested_vs_final_confidence_on_downgrade():
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    f = _finding(title="Self-invocation", condition_evidence_ids=(mid,), consequence_evidence_ids=())
    [adjudicated] = adjudicate_findings([f], member_ids, {})

    report = format_adjudicated_review(
        summary="ok", member_ids=member_ids, member_reviews={mid: ("finding", "")}, coverage={"missing": [], "invented": []},
        adjudicated=[adjudicated], recommendations=[], run_guidance_statements=[], run_guidance_gaps=[],
    )

    assert "STRONG STATIC INDICATION" in report
    assert "Reviewer requested: PROVEN ISSUE" in report
    assert "Kriya downgraded" in report


# --- Case 15: formatter does not expose raw JSON ---
def test_format_adjudicated_review_does_not_expose_raw_json_keys():
    member_ids = _member_ids_fixture()
    report = format_adjudicated_review(
        summary="ok", member_ids=member_ids, member_reviews={}, coverage={"missing": [], "invented": []},
        adjudicated=[], recommendations=[], run_guidance_statements=[], run_guidance_gaps=[],
    )

    assert '"finding_id"' not in report
    assert '"requested_confidence"' not in report
    assert '"member_id"' not in report
    assert '"note"' not in report
    assert not report.strip().startswith("{")


# --- Historical defect replay: both real live-run over-classifications, through
# the SAME generic code path, no framework-specific rule anywhere involved ---
def test_historical_shape_run1_self_invocation_overclassification_downgrades():
    """A1 run 1: Reviewer requested PROVEN ISSUE for Spring same-class
    self-invocation, with real condition evidence (the calling member) but
    no evidence for the claimed consequence (a broken transaction
    boundary) - the adjudicator must downgrade this generically, without
    knowing anything about Spring/JPA/self-invocation."""
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    f = _finding(
        finding_id="F1", title="Same-class self-invocation",
        requested_confidence=CONFIDENCE_PROVEN_ISSUE,
        condition_evidence_ids=(mid,), consequence_evidence_ids=(),
        explanation="calls another @Transactional method in the same class",
    )

    [result] = adjudicate_findings([f], member_ids, {})

    assert result.final_confidence == CONFIDENCE_STRONG_STATIC_INDICATION


def test_historical_shape_run2_delete_persistence_overclassification_downgrades():
    """A1-R2: Reviewer requested PROVEN ISSUE for delete()'s missing
    explicit save() call, with real condition evidence (the method body)
    but no evidence for the claimed consequence (that the change will not
    persist) - same generic downgrade, no JPA-specific rule involved."""
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    f = _finding(
        finding_id="F1", title="Missing persistence call",
        requested_confidence=CONFIDENCE_PROVEN_ISSUE,
        condition_evidence_ids=(mid,), consequence_evidence_ids=(),
        explanation="mutates a field with no explicit save() call afterwards",
    )

    [result] = adjudicate_findings([f], member_ids, {})

    assert result.final_confidence == CONFIDENCE_STRONG_STATIC_INDICATION


# --- End-to-end orchestration ---
def test_build_structured_review_report_end_to_end_with_downgrade_and_fabricated_id():
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    raw = {
        "summary": "Overview text.",
        "member_reviews": [{"member_id": mid, "status": "finding"}],
        "findings": [{
            "finding_id": "F1", "title": "Issue", "member_id": mid,
            "requested_confidence": "PROVEN_ISSUE",
            "condition_evidence_ids": [mid], "consequence_evidence_ids": ["M999"],
            "runtime_dependency_declared": False, "explanation": "explanation text",
        }],
        "recommendations": ["do X"],
        "run_guidance": {"statements": [], "not_determinable": ["REST endpoints"]},
    }

    report = build_structured_review_report(raw, member_ids, {})

    assert "Overview text." in report
    assert "STRONG STATIC INDICATION" in report
    assert "Reviewer requested: PROVEN ISSUE" in report
    assert "M999" in report  # unresolvable reference surfaced, not silently dropped
    assert "do X" in report
    assert "REST endpoints" in report


def test_build_structured_review_report_never_crashes_on_totally_malformed_shape():
    """A successfully-parsed-but-wrong-shape JSON object must degrade
    gracefully (empty findings/coverage/etc.), never raise - a total
    call/parse failure is handled one layer up, in ReviewerAgent.
    run_structured_review(), before this function is ever called."""
    member_ids = _member_ids_fixture()

    report = build_structured_review_report({"unexpected": "shape"}, member_ids, {})

    assert isinstance(report, str)
    assert "0/2 deterministic member(s) accounted for" in report


# =====================================================================
# A1 UX fix: member_reviews[] status/note preserved and rendered
# (previously extracted only the bare member id and discarded the rest)
# =====================================================================

def test_parse_member_reviews_preserves_status_and_note():
    parsed = parse_member_reviews([
        {"member_id": "M1", "status": "no_issue", "note": "Stores the dependency."},
        {"member_id": "M2", "status": "finding", "note": "No body."},
    ])

    assert parsed == {
        "M1": ("no_issue", "Stores the dependency."),
        "M2": ("finding", "No body."),
    }


def test_parse_member_reviews_skips_malformed_entries():
    parsed = parse_member_reviews(["not a dict", {"status": "no_issue"}, {"member_id": "M1", "note": "ok"}])

    assert parsed == {"M1": ("", "ok")}


def test_parse_member_reviews_returns_empty_dict_for_malformed_top_level_shape():
    assert parse_member_reviews({"not": "a list"}) == {}
    assert parse_member_reviews(None) == {}


# --- 1/4/5: two reviewed members both render, status rendered ---
def test_format_adjudicated_review_renders_two_member_reviews():
    member_ids = _member_ids_fixture()  # M1 = constructor, M2 = doWork()
    member_reviews = {"M1": ("no_issue", "Stores the collaborator."), "M2": ("finding", "No body.")}

    report = format_adjudicated_review(
        summary="", member_ids=member_ids, member_reviews=member_reviews,
        coverage={"missing": [], "invented": []}, adjudicated=[], recommendations=[],
        run_guidance_statements=[], run_guidance_gaps=[],
    )

    assert "## Member Review" in report
    assert "### M1" in report and "Status: no_issue" in report
    assert "### M2" in report and "Status: finding" in report


# --- 2: deterministic member order preserved (M1 before M2 regardless of
# the order member_reviews happened to list them in) ---
def test_format_adjudicated_review_member_order_is_deterministic_not_model_order():
    member_ids = _member_ids_fixture()
    # Deliberately reversed vs. member_ids' own M1-then-M2 order.
    member_reviews = {"M2": ("no_issue", "second declared"), "M1": ("no_issue", "first declared")}

    report = format_adjudicated_review(
        summary="", member_ids=member_ids, member_reviews=member_reviews,
        coverage={"missing": [], "invented": []}, adjudicated=[], recommendations=[],
        run_guidance_statements=[], run_guidance_gaps=[],
    )

    assert report.index("### M1") < report.index("### M2")


# --- 3: signature shown comes from Kriya's own evidence registry, not the model ---
def test_format_adjudicated_review_uses_kriya_signature_not_model_text():
    member_ids = _member_ids_fixture()
    mid, member = next(iter(member_ids.items()))
    member_reviews = {mid: ("no_issue", "some note")}

    report = format_adjudicated_review(
        summary="", member_ids=member_ids, member_reviews=member_reviews,
        coverage={"missing": [], "invented": []}, adjudicated=[], recommendations=[],
        run_guidance_statements=[], run_guidance_gaps=[],
    )

    assert member.signature in report
    assert f"### {mid} — {member.signature}" in report


# --- 6: missing member explicitly reported (both in the section itself and
# in the existing Member Coverage summary) ---
def test_format_adjudicated_review_reports_missing_member_explicitly():
    member_ids = _member_ids_fixture()  # M1, M2
    member_reviews = {"M1": ("no_issue", "covered")}  # M2 never addressed

    report = format_adjudicated_review(
        summary="", member_ids=member_ids, member_reviews=member_reviews,
        coverage={"missing": ["M2"], "invented": []}, adjudicated=[], recommendations=[],
        run_guidance_statements=[], run_guidance_gaps=[],
    )

    assert "### M2" in report
    assert "not reviewed" in report
    assert "No review was provided for this member." in report
    assert "**Missing (never addressed by the review):** M2" in report


# --- 7: invented member ID explicitly reported, per existing coverage behavior ---
def test_format_adjudicated_review_reports_invented_member_id_explicitly():
    member_ids = _member_ids_fixture()
    member_reviews = {"M1": ("no_issue", "x"), "M2": ("no_issue", "y"), "M999": ("no_issue", "fabricated")}

    report = format_adjudicated_review(
        summary="", member_ids=member_ids, member_reviews=member_reviews,
        coverage={"missing": [], "invented": ["M999"]}, adjudicated=[], recommendations=[],
        run_guidance_statements=[], run_guidance_gaps=[],
    )

    assert "**Invented (not part of the supplied inventory - ignored):** M999" in report
    assert "Ignored - not part of the supplied deterministic inventory: M999" in report
    assert "### M999" not in report  # never rendered as if it were a real member section


# --- 8/9/10: member note is advisory prose only - never evidence authority ---
def test_member_note_does_not_affect_finding_confidence():
    """A finding's condition/consequence evidence must resolve through the
    normal evidence registry only - member_reviews[].note is never
    consulted by adjudicate_findings() at all (different function,
    different inputs), so a note asserting a consequence cannot upgrade a
    finding that cites no real consequence evidence id."""
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    f = _finding(condition_evidence_ids=(mid,), consequence_evidence_ids=())

    [result] = adjudicate_findings([f], member_ids, {})

    assert result.final_confidence == CONFIDENCE_STRONG_STATIC_INDICATION


def test_member_note_cannot_provide_condition_or_consequence_evidence():
    """adjudicate_findings()'s signature takes only (findings, member_ids,
    relation_ids) - member_reviews/notes are never passed to it at all, so
    there is no code path through which a note's text could be read as an
    evidence reference, by construction."""
    import inspect

    params = list(inspect.signature(adjudicate_findings).parameters)

    assert params == ["findings", "member_ids", "relation_ids"]
    assert "member_reviews" not in params and "notes" not in params


# --- 11: raw JSON still not rendered, including the new fields ---
def test_format_adjudicated_review_member_section_does_not_expose_raw_json():
    member_ids = _member_ids_fixture()
    member_reviews = {"M1": ("no_issue", "a note")}

    report = format_adjudicated_review(
        summary="", member_ids=member_ids, member_reviews=member_reviews,
        coverage={"missing": [], "invented": []}, adjudicated=[], recommendations=[],
        run_guidance_statements=[], run_guidance_gaps=[],
    )

    assert '"status"' not in report
    assert '"member_id"' not in report
    assert not report.strip().startswith("{")


# --- 14: frozen A1 9-member fixture renders exactly 9 member sections ---
def test_frozen_a1_default_driver_service_renders_nine_member_sections():
    from test_java_members import _DEFAULT_DRIVER_SERVICE_SOURCE

    members = extract_java_members(_DEFAULT_DRIVER_SERVICE_SOURCE)
    member_ids = build_member_evidence_ids(members)
    assert len(member_ids) == 9

    member_reviews = {mid: ("no_issue", f"note for {mid}") for mid in member_ids}
    report = format_adjudicated_review(
        summary="", member_ids=member_ids, member_reviews=member_reviews,
        coverage={"missing": [], "invented": []}, adjudicated=[], recommendations=[],
        run_guidance_statements=[], run_guidance_gaps=[],
    )

    for mid in member_ids:
        assert f"### {mid} —" in report
    assert "9/9 deterministic member(s) accounted for" in report


# =====================================================================
# A2: review finding -> proposed modification (advisory only, read-only)
# =====================================================================

def _proposal_finding(**overrides):
    base = dict(
        finding_id="F1", title="Constructor stores collaborator without null check", member_id=None,
        requested_confidence=CONFIDENCE_STRONG_STATIC_INDICATION,
        condition_evidence_ids=(), consequence_evidence_ids=(),
        runtime_dependency_declared=False, explanation="No null guard on c.",
        recommendation="Add a null check on the constructor parameter.",
    )
    base.update(overrides)
    return StructuredFinding(**base)


def test_build_proposed_modification_grounded_finding_produces_full_proposal():
    """Tests 1,2,4,5,6,7,8,9: a valid grounded finding with evidence and a
    recommendation produces a fully-populated, structurally advisory proposal."""
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    f = _proposal_finding(member_id=mid, condition_evidence_ids=(mid,))
    [adj] = adjudicate_findings([f], member_ids, {})

    proposal = build_proposed_modification("F1", [adj], member_ids, {}, "src/main/java/Target.java")

    assert isinstance(proposal, ProposedModification)
    assert proposal.source_finding_id == "F1"  # test 2: retains source finding id
    assert any(mid in e for e in proposal.evidence)  # test 3: retains valid evidence reference
    assert proposal.target_file == "src/main/java/Target.java"  # test 4: deterministic file identity
    assert member_ids[mid].signature in proposal.target_member  # test 4: deterministic member identity
    assert "null guard" in proposal.problem_statement.lower()  # test 5: problem statement
    assert proposal.proposed_change == f.recommendation  # test 6: concrete proposed change
    assert len(proposal.must_preserve) >= 3  # test 7: preservation constraints
    assert len(proposal.verification) >= 2  # test 8: verification requirements
    assert proposal.authority == PROPOSAL_AUTHORITY_ADVISORY_ONLY  # test 9
    assert proposal.approval == PROPOSAL_APPROVAL_NOT_APPROVED  # test 9


def test_proposal_authority_is_advisory_regardless_of_finding_strength():
    """Test 10/18 (authority half): a PROVEN_ISSUE-strength finding and an
    EVIDENCE_INSUFFICIENT one both produce an equally advisory/unapproved
    proposal - authority never scales with the finding's own confidence."""
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    strong = _proposal_finding(
        finding_id="F1", condition_evidence_ids=(mid,), consequence_evidence_ids=(mid,),
        requested_confidence=CONFIDENCE_PROVEN_ISSUE,
    )
    weak = _proposal_finding(finding_id="F2", requested_confidence="not a real tier", condition_evidence_ids=(mid,))
    [adj_strong] = adjudicate_findings([strong], member_ids, {})
    [adj_weak] = adjudicate_findings([weak], member_ids, {})

    p_strong = build_proposed_modification("F1", [adj_strong], member_ids, {}, "X.java")
    p_weak = build_proposed_modification("F2", [adj_weak], member_ids, {}, "X.java")

    for p in (p_strong, p_weak):
        assert p.authority == PROPOSAL_AUTHORITY_ADVISORY_ONLY
        assert p.approval == PROPOSAL_APPROVAL_NOT_APPROVED


def test_proposal_reflects_downgraded_confidence_not_requested_confidence():
    """Test 18 (the real distinction from test 9/10): a finding requesting
    PROVEN_ISSUE but downgraded (no consequence evidence) to
    STRONG_STATIC_INDICATION must produce a proposal whose OWN confidence
    claim is the downgraded tier - never the originally requested,
    unsupported one. The downgrade is still transparently disclosed in
    Assumptions (same "say so explicitly" convention as
    format_adjudicated_review()), just never rendered as the proposal's
    own Confidence: line."""
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    f = _proposal_finding(
        requested_confidence=CONFIDENCE_PROVEN_ISSUE,
        condition_evidence_ids=(mid,), consequence_evidence_ids=(),  # no consequence -> downgrade
    )
    [adj] = adjudicate_findings([f], member_ids, {})
    assert adj.final_confidence == CONFIDENCE_STRONG_STATIC_INDICATION  # sanity on the fixture

    proposal = build_proposed_modification("F1", [adj], member_ids, {}, "X.java")
    report = format_proposed_modification(proposal)

    assert proposal.final_confidence == CONFIDENCE_STRONG_STATIC_INDICATION
    assert "Confidence: STRONG STATIC INDICATION" in report
    assert "Confidence: PROVEN ISSUE" not in report
    assert "Kriya downgraded this finding's confidence from PROVEN_ISSUE" in report  # transparent disclosure


def test_proposal_cannot_be_built_for_unknown_finding_id():
    """Test 16: an id not present in this invocation's own adjudicated list
    is rejected outright, never silently ignored or matched fuzzily."""
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    f = _proposal_finding(condition_evidence_ids=(mid,))
    [adj] = adjudicate_findings([f], member_ids, {})

    try:
        build_proposed_modification("F999", [adj], member_ids, {}, "X.java")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Unknown finding id" in str(e)


def test_proposal_refused_when_finding_has_no_recommendation():
    """A concrete proposed change cannot be fabricated - if the finding
    itself carries no recommendation, refuse rather than inventing one."""
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    f = _proposal_finding(condition_evidence_ids=(mid,), recommendation=None)
    [adj] = adjudicate_findings([f], member_ids, {})

    try:
        build_proposed_modification("F1", [adj], member_ids, {}, "X.java")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "no recommendation" in str(e).lower()


def test_proposal_refused_when_no_evidence_id_resolves():
    """Grounding requirement: a finding citing only invented/unresolvable
    evidence ids has nothing real for the proposal to be traceable to -
    refuse rather than proposing an ungrounded change."""
    member_ids = _member_ids_fixture()
    f = _proposal_finding(condition_evidence_ids=("M999",), consequence_evidence_ids=("R999",))
    [adj] = adjudicate_findings([f], member_ids, {})

    try:
        build_proposed_modification("F1", [adj], member_ids, {}, "X.java")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "no evidence id" in str(e).lower()


def test_proposal_excludes_invented_evidence_ids_entirely():
    """Test 17: a finding citing a mix of a real and an invented evidence
    id keeps only the real one - the invented id is excluded from the
    proposal entirely (never listed, even as "ignored"), matching how
    adjudicate_findings() already treats an unresolvable id as simply not
    counting, by construction."""
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    f = _proposal_finding(condition_evidence_ids=(mid, "M999"))
    [adj] = adjudicate_findings([f], member_ids, {})

    proposal = build_proposed_modification("F1", [adj], member_ids, {}, "X.java")
    report = format_proposed_modification(proposal)

    assert not any("M999" in e for e in proposal.evidence)
    assert "M999" not in report


def test_proposal_runtime_dependent_finding_keeps_runtime_verification():
    """Test 19: a finding whose consequence is runtime-dependent must keep
    a runtime/profiling verification requirement, and must never be
    converted into a certainty claim about runtime behavior."""
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    f = _proposal_finding(
        requested_confidence=CONFIDENCE_REQUIRES_RUNTIME, condition_evidence_ids=(mid,),
        runtime_dependency_declared=True,
    )
    [adj] = adjudicate_findings([f], member_ids, {})
    assert adj.final_confidence == CONFIDENCE_REQUIRES_RUNTIME

    proposal = build_proposed_modification("F1", [adj], member_ids, {}, "X.java")
    report = format_proposed_modification(proposal)

    assert any("profiling/runtime evidence" in v for v in proposal.verification)
    assert "definitely fix" not in report.lower()
    assert "will definitely" not in report.lower()


def test_proposal_target_member_uses_kriya_signature_not_model_text():
    """Test 20: the rendered target member/signature comes from Kriya's own
    member registry, never from the finding's own free-text title/
    explanation - even when the model's text names something different,
    Kriya's own registry value is what's authoritative and rendered."""
    member_ids = _member_ids_fixture()
    mid, member = next(iter(member_ids.items()))
    f = _proposal_finding(
        member_id=mid, condition_evidence_ids=(mid,),
        title="totally unrelated model-invented method name AAAAZZZZ",
    )
    [adj] = adjudicate_findings([f], member_ids, {})

    proposal = build_proposed_modification("F1", [adj], member_ids, {}, "X.java")

    assert member.signature in proposal.target_member
    assert "AAAAZZZZ" not in proposal.target_member


def test_format_proposed_modification_states_no_modification_occurred():
    """Test 21: the rendered proposal must explicitly say no files were
    modified and nothing was approved - never leave the user to infer it."""
    member_ids = _member_ids_fixture()
    mid = next(iter(member_ids))
    f = _proposal_finding(condition_evidence_ids=(mid,))
    [adj] = adjudicate_findings([f], member_ids, {})
    proposal = build_proposed_modification("F1", [adj], member_ids, {}, "X.java")

    report = format_proposed_modification(proposal)

    assert "has not been approved" in report
    assert "no source files were modified" in report
    assert f"Authority: {PROPOSAL_AUTHORITY_ADVISORY_ONLY}" in report
    assert f"Approval: {PROPOSAL_APPROVAL_NOT_APPROVED}" in report


def test_a1_format_adjudicated_review_signature_and_behavior_unchanged():
    """Test 22: A2 must not touch format_adjudicated_review() at all - its
    signature (no new proposal-shaped parameter) is proof A1's own report
    is unaffected by A2 existing in the same module."""
    import inspect

    params = list(inspect.signature(format_adjudicated_review).parameters)
    assert "proposal" not in params
    assert "proposed_modification" not in params


def test_review_context_module_never_imports_write_capable_components():
    """Zero-write structural proof (parts 10-14 of the required test list):
    review_context.py - where A2's build_proposed_modification()/
    format_proposed_modification() live - must never import
    DeveloperAgent, AuthorizedFileWriter, or the generation/recovery
    workflow, by construction. Checked via the actual compiled AST import
    graph, not a source-text grep (which would trivially pass even if the
    forbidden name were merely mentioned in a call deep in the module)."""
    import ast
    import inspect

    import kriya.workflow.review_context as rc

    tree = ast.parse(inspect.getsource(rc))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)

    forbidden = {"DeveloperAgent", "AuthorizedFileWriter", "WorkflowEngine", "run_generation_workflow"}
    assert not (imported_names & forbidden), imported_names & forbidden
