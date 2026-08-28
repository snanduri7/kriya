"""MA8: Cross-Revision Obligation Control (kriya/workflow/obligations.py) -
first pytest coverage for this module. See its own module docstring for
the PRV-05 run #8 incident this closes."""

from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)


def _rec(
    id_, status, *, authority=ObligationAuthority.DETERMINISTIC, revision=None,
    kind=ObligationKind.PLAN_STRUCTURAL_VALIDITY, owner_subtask_id=None,
    terminal_required=False, repair_scope=(),
):
    return ObligationRecord(
        id=id_, kind=kind, status=status, authority=authority,
        description="d", source="test", revision=revision,
        owner_subtask_id=owner_subtask_id, terminal_required=terminal_required,
        repair_scope=repair_scope,
    )


def test_authority_precedence_order():
    assert ObligationAuthority.DETERMINISTIC.precedence() > ObligationAuthority.GROUNDED.precedence()
    assert ObligationAuthority.GROUNDED.precedence() > ObligationAuthority.JUDGMENT.precedence()


def test_record_returns_none_for_first_record():
    ledger = ObligationLedger()
    assert ledger.record(_rec("a", ObligationStatus.VIOLATED, revision=0)) is None


def test_record_returns_none_when_status_improves():
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.VIOLATED, revision=0))
    assert ledger.record(_rec("a", ObligationStatus.SATISFIED, revision=1)) is None


def test_record_detects_regression_same_authority():
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.SATISFIED, revision=0))
    event = ledger.record(_rec("a", ObligationStatus.VIOLATED, revision=1))
    assert event is not None
    assert event.obligation_id == "a"
    assert event.previous.status == ObligationStatus.SATISFIED
    assert event.current.status == ObligationStatus.VIOLATED
    assert ledger.regressions == [event]


def test_record_does_not_prevent_the_transition():
    """The ledger records ground truth, it never gatekeeps it - a
    regression is surfaced, not blocked."""
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.SATISFIED, revision=0))
    ledger.record(_rec("a", ObligationStatus.VIOLATED, revision=1))
    assert ledger.current("a").status == ObligationStatus.VIOLATED


def test_record_detects_regression_from_higher_authority():
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.SATISFIED, authority=ObligationAuthority.GROUNDED, revision=0))
    event = ledger.record(_rec("a", ObligationStatus.VIOLATED, authority=ObligationAuthority.DETERMINISTIC, revision=1))
    assert event is not None


def test_record_does_not_flag_regression_from_lower_authority():
    """A JUDGMENT-authority VIOLATED must never be treated as regressing a
    DETERMINISTIC-authority SATISFIED - that's the whole point of
    precedence: the lower-authority claim simply loses, it isn't even a
    real regression event. Also - the whole point of precedence, not just
    the regression signal - current() must not have flipped either; see
    test_authoritative_state_* below for the dedicated coverage of that."""
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.SATISFIED, authority=ObligationAuthority.DETERMINISTIC, revision=0))
    event = ledger.record(_rec("a", ObligationStatus.VIOLATED, authority=ObligationAuthority.JUDGMENT, revision=1))
    assert event is None
    assert ledger.regressions == []
    assert ledger.current("a").status == ObligationStatus.SATISFIED


# --- Spec §16/§49 "Authority" required tests: current()/authoritative_state()
# must implement real precedence arbitration, not a bare last-write lookup.
# Bug found and fixed 2026-08-28 - current() used to return history[-1]
# unconditionally, which happened to be correct for every producer that
# exists today (all DETERMINISTIC-only) but was silently wrong for any
# mixed-authority sequence. See current()'s own docstring in obligations.py. ---

def test_authoritative_state_deterministic_satisfied_survives_later_judgment_violated():
    """Doc §16 example 1, verbatim."""
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.SATISFIED, authority=ObligationAuthority.DETERMINISTIC, revision=0))
    ledger.record(_rec("a", ObligationStatus.VIOLATED, authority=ObligationAuthority.JUDGMENT, revision=1))
    assert ledger.current("a").status == ObligationStatus.SATISFIED
    assert ledger.authoritative_state("a").status == ObligationStatus.SATISFIED


def test_authoritative_state_judgment_satisfied_overridden_by_later_deterministic_violated():
    """Doc §16 example 2, verbatim."""
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.SATISFIED, authority=ObligationAuthority.JUDGMENT, revision=0))
    ledger.record(_rec("a", ObligationStatus.VIOLATED, authority=ObligationAuthority.DETERMINISTIC, revision=1))
    assert ledger.current("a").status == ObligationStatus.VIOLATED


def test_authoritative_state_ignores_any_number_of_repeated_lower_authority_records():
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.SATISFIED, authority=ObligationAuthority.DETERMINISTIC, revision=0))
    ledger.record(_rec("a", ObligationStatus.VIOLATED, authority=ObligationAuthority.JUDGMENT, revision=1))
    ledger.record(_rec("a", ObligationStatus.SATISFIED, authority=ObligationAuthority.JUDGMENT, revision=2))
    ledger.record(_rec("a", ObligationStatus.VIOLATED, authority=ObligationAuthority.GROUNDED, revision=3))
    assert ledger.current("a").status == ObligationStatus.SATISFIED


def test_authoritative_state_same_authority_as_established_can_still_update():
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.SATISFIED, authority=ObligationAuthority.GROUNDED, revision=0))
    ledger.record(_rec("a", ObligationStatus.VIOLATED, authority=ObligationAuthority.JUDGMENT, revision=1))
    ledger.record(_rec("a", ObligationStatus.VIOLATED, authority=ObligationAuthority.GROUNDED, revision=2))
    assert ledger.current("a").status == ObligationStatus.VIOLATED


def test_detect_oscillation_true_for_violated_satisfied_violated():
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.VIOLATED, revision=0))
    ledger.record(_rec("a", ObligationStatus.SATISFIED, revision=1))
    ledger.record(_rec("a", ObligationStatus.VIOLATED, revision=2))
    assert ledger.detect_oscillation("a") is True
    assert ledger.oscillating_ids() == ["a"]


def test_detect_oscillation_false_for_monotonic_progress():
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.VIOLATED, revision=0))
    ledger.record(_rec("a", ObligationStatus.SATISFIED, revision=1))
    assert ledger.detect_oscillation("a") is False
    assert ledger.oscillating_ids() == []


def test_detect_oscillation_true_with_repeated_intermediate_status():
    """Not necessarily a strict 3-consecutive-record triple - a repeated
    re-check landing on SATISFIED twice before regressing is still the
    same real oscillation."""
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.VIOLATED, revision=0))
    ledger.record(_rec("a", ObligationStatus.SATISFIED, revision=1))
    ledger.record(_rec("a", ObligationStatus.SATISFIED, revision=2))
    ledger.record(_rec("a", ObligationStatus.VIOLATED, revision=3))
    assert ledger.detect_oscillation("a") is True


def test_satisfied_and_violated_ids_reflect_current_state_only():
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.VIOLATED, revision=0))
    ledger.record(_rec("a", ObligationStatus.SATISFIED, revision=1))
    ledger.record(_rec("b", ObligationStatus.VIOLATED, revision=0))
    assert ledger.satisfied_ids() == ["a"]
    assert ledger.violated_ids() == ["b"]


def test_current_by_kind_filters_correctly():
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.SATISFIED, kind=ObligationKind.PLAN_STRUCTURAL_VALIDITY))
    ledger.record(_rec("b", ObligationStatus.SATISFIED, kind=ObligationKind.MIGRATION_COMPLETION))
    plan_records = ledger.current_by_kind(ObligationKind.PLAN_STRUCTURAL_VALIDITY)
    assert [r.id for r in plan_records] == ["a"]


def test_history_preserves_full_revision_order():
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.VIOLATED, revision=0))
    ledger.record(_rec("a", ObligationStatus.SATISFIED, revision=1))
    history = ledger.history("a")
    assert [r.revision for r in history] == [0, 1]
    assert [r.status for r in history] == [ObligationStatus.VIOLATED, ObligationStatus.SATISFIED]


# --- Batch 1 (spec §12/§20/§42): owner_subtask_id/terminal_required/
# repair_scope fields, unresolved_terminal_obligations(), and MUST_PRESERVE
# relevance filtering. ---

def test_unresolved_terminal_obligations_finds_only_unsatisfied_terminal_required():
    ledger = ObligationLedger()
    ledger.record(_rec("violated-terminal", ObligationStatus.VIOLATED, terminal_required=True))
    ledger.record(_rec("satisfied-terminal", ObligationStatus.SATISFIED, terminal_required=True))
    ledger.record(_rec("violated-not-terminal", ObligationStatus.VIOLATED, terminal_required=False))
    unresolved = ledger.unresolved_terminal_obligations()
    assert [r.id for r in unresolved] == ["violated-terminal"]


def test_unresolved_terminal_obligations_includes_pending():
    """Doc §42: a required PENDING must not produce terminal PASS."""
    ledger = ObligationLedger()
    ledger.record(_rec("still-pending", ObligationStatus.PENDING, terminal_required=True))
    assert [r.id for r in ledger.unresolved_terminal_obligations()] == ["still-pending"]


def test_unresolved_terminal_obligations_includes_indeterminate():
    """Doc §42/§49/§56: a material INDETERMINATE must not produce terminal
    PASS either - the real live producer is workflow_controller.py's
    terminal migration gate when MigrationResolutionStatus.INDETERMINATE
    fires (migration.identity_resolution), reproduced here at the unit
    level so this status's own terminal-blocking behavior has direct
    coverage independent of the full WorkflowController harness."""
    ledger = ObligationLedger()
    ledger.record(_rec(
        "migration.identity_resolution", ObligationStatus.INDETERMINATE, terminal_required=True,
    ))
    assert [r.id for r in ledger.unresolved_terminal_obligations()] == ["migration.identity_resolution"]


def test_unresolved_terminal_obligations_empty_when_all_satisfied():
    ledger = ObligationLedger()
    ledger.record(_rec("a", ObligationStatus.SATISFIED, terminal_required=True))
    ledger.record(_rec("b", ObligationStatus.SATISFIED, terminal_required=True))
    assert ledger.unresolved_terminal_obligations() == []


def test_relevant_for_preservation_includes_just_fixed_obligation():
    """Reproduces PRV-05 run 8's own shape: A=refactor_baseline invalid,
    B=planned-file action invalid. Repair 1 fixes B; ahead of repair 2, B
    (just satisfied) must appear in the preserve set even though A (still
    violated) is the only thing currently failing."""
    ledger = ObligationLedger()
    ledger.record(_rec("A", ObligationStatus.VIOLATED, revision=0, terminal_required=True))
    ledger.record(_rec("B", ObligationStatus.VIOLATED, revision=0, terminal_required=True, owner_subtask_id="s4"))
    ledger.record(_rec("A", ObligationStatus.VIOLATED, revision=1, terminal_required=True))
    ledger.record(_rec("B", ObligationStatus.SATISFIED, revision=1, terminal_required=True, owner_subtask_id="s4"))
    preserve_ids = [r.id for r in ledger.relevant_for_preservation(ObligationKind.PLAN_STRUCTURAL_VALIDITY, ["A"])]
    assert "B" in preserve_ids


def test_relevant_for_preservation_includes_previously_regressed_obligation():
    ledger = ObligationLedger()
    ledger.record(_rec("B", ObligationStatus.SATISFIED, revision=0, owner_subtask_id="s4"))
    ledger.record(_rec("B", ObligationStatus.VIOLATED, revision=1, owner_subtask_id="s4"))
    ledger.record(_rec("B", ObligationStatus.SATISFIED, revision=2, owner_subtask_id="s4"))
    ledger.record(_rec("C", ObligationStatus.VIOLATED, revision=2, owner_subtask_id="unrelated"))
    preserve_ids = [r.id for r in ledger.relevant_for_preservation(ObligationKind.PLAN_STRUCTURAL_VALIDITY, ["C"])]
    assert "B" in preserve_ids


def test_relevant_for_preservation_excludes_unrelated_stale_satisfied_obligation():
    """A satisfied obligation that never regressed, didn't just change, and
    shares neither owner nor id-domain with a currently-violated one should
    NOT be dumped into the prompt - the whole point of §20's filter."""
    ledger = ObligationLedger()
    ledger.record(_rec("plan.subtask.s1.model_subtask_scope", ObligationStatus.SATISFIED, revision=0, owner_subtask_id="s1"))
    ledger.record(_rec("plan.subtask.s1.model_subtask_scope", ObligationStatus.SATISFIED, revision=1, owner_subtask_id="s1"))
    ledger.record(_rec("plan.file.pom.xml.action_consistency", ObligationStatus.VIOLATED, revision=1, owner_subtask_id="s4"))
    preserve_ids = [
        r.id for r in ledger.relevant_for_preservation(
            ObligationKind.PLAN_STRUCTURAL_VALIDITY, ["plan.file.pom.xml.action_consistency"],
        )
    ]
    assert "plan.subtask.s1.model_subtask_scope" not in preserve_ids


def test_relevant_for_preservation_includes_same_owner_as_violation():
    ledger = ObligationLedger()
    ledger.record(_rec("plan.file.a.ownership", ObligationStatus.SATISFIED, revision=0, owner_subtask_id="s4"))
    ledger.record(_rec("plan.file.a.ownership", ObligationStatus.SATISFIED, revision=1, owner_subtask_id="s4"))
    ledger.record(_rec("plan.file.b.action_consistency", ObligationStatus.VIOLATED, revision=1, owner_subtask_id="s4"))
    preserve_ids = [
        r.id for r in ledger.relevant_for_preservation(
            ObligationKind.PLAN_STRUCTURAL_VALIDITY, ["plan.file.b.action_consistency"],
        )
    ]
    assert "plan.file.a.ownership" in preserve_ids


def test_relevant_for_preservation_includes_same_domain_as_violation():
    ledger = ObligationLedger()
    ledger.record(_rec("plan.file.a.ownership", ObligationStatus.SATISFIED, revision=0))
    ledger.record(_rec("plan.file.a.ownership", ObligationStatus.SATISFIED, revision=1))
    ledger.record(_rec("plan.file.b.action_consistency", ObligationStatus.VIOLATED, revision=1))
    preserve_ids = [
        r.id for r in ledger.relevant_for_preservation(
            ObligationKind.PLAN_STRUCTURAL_VALIDITY, ["plan.file.b.action_consistency"],
        )
    ]
    assert "plan.file.a.ownership" in preserve_ids
