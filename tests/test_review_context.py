from kriya.analyzer.java_members import extract_java_members
from kriya.workflow.review_context import (
    CONFIDENCE_EVIDENCE_INSUFFICIENT,
    CONFIDENCE_PROVEN_ISSUE,
    CONFIDENCE_REQUIRES_RUNTIME,
    CONFIDENCE_STRONG_STATIC_INDICATION,
    StructuredFinding,
    adjudicate_findings,
    build_member_evidence_ids,
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
    format_relation_evidence_registry,
    format_review_repository_context,
    parse_and_validate_run_guidance,
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
        summary="ok", member_ids=member_ids, member_review_ids=[mid], coverage={"missing": [], "invented": []},
        adjudicated=[adjudicated], recommendations=[], run_guidance_statements=[], run_guidance_gaps=[],
    )

    assert "STRONG STATIC INDICATION" in report
    assert "Reviewer requested: PROVEN ISSUE" in report
    assert "Kriya downgraded" in report


# --- Case 15: formatter does not expose raw JSON ---
def test_format_adjudicated_review_does_not_expose_raw_json_keys():
    member_ids = _member_ids_fixture()
    report = format_adjudicated_review(
        summary="ok", member_ids=member_ids, member_review_ids=[], coverage={"missing": [], "invented": []},
        adjudicated=[], recommendations=[], run_guidance_statements=[], run_guidance_gaps=[],
    )

    assert '"finding_id"' not in report
    assert '"requested_confidence"' not in report
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
