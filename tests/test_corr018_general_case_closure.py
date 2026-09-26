"""CORR-018 general-case closure (2026-09-13): ordinary Java brownfield
generate/fix now requires BOTH file-write authority and requirement-grounded
semantic-region authority for every EXISTING .java file mutation, gated
behind `autonomy.semantic_region_enforcement_required` (default False - see
`kriya/config/config.py`'s own field docstring, and
`kriya/workflow/semantic_scope_derivation.py`'s own module docstring, for
why an unconditional default was explicitly rejected with the user).

No live LLM anywhere in this file - every test calls the deterministic
production functions directly (`derive_semantic_authority_for_run`,
`find_unauthorized_semantic_changes`, `_dispatch_tool_call`), matching this
closure task's own explicit "deterministic fake-model candidates, no live
model needed" instruction (Task 16). The adversarial matrix (Task 22, A-R)
is covered by name in the docstring of each test below.
"""
import ast
import os

from kriya.config.authority import _SECURITY_AUTHORITY_FIELDS
from kriya.config.config import AppConfig
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.semantic_region_authority import (
    REASON_DECLARATION_UNAUTHORIZED,
    REASON_FILE_HAS_NO_SEMANTIC_AUTHORITY,
    REASON_MEMBER_ADDED_UNAUTHORIZED,
    REASON_SIGNATURE_UNAUTHORIZED,
    AuthorizedSemanticRegion,
    RegionType,
    find_unauthorized_semantic_changes,
    stable_field_key,
    stable_member_key,
    stable_record_component_key,
)
from kriya.workflow.semantic_scope_derivation import derive_semantic_authority_for_run
from kriya.workflow.triage import ChangeKind

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WORKFLOW_DIR = os.path.join(_REPO_ROOT, "kriya", "workflow")

# ---------------------------------------------------------------------------
# Fixture content (Task 12's own suggested shape, adapted)
# ---------------------------------------------------------------------------

CUSTOMER_PATH = "src/main/java/com/example/Customer.java"
CUSTOMER_SRC = (
    "package com.example;\n"
    "public class Customer {\n"
    "    private String id;\n"
    "    private String region;\n"
    "    public String getId() { return id; }\n"
    "}\n"
)

RECORD_PATH = "src/main/java/com/example/CustomerRecord.java"
RECORD_SRC = "package com.example;\npublic record CustomerRecord(String id, String name) {}\n"

PRINTER_PATH = "src/main/java/com/example/CustomerPrinter.java"
PRINTER_SRC = (
    "package com.example;\n"
    "public class CustomerPrinter {\n"
    "    public String print(CustomerRecord r) { return r.name(); }\n"
    "}\n"
)

SERVICE_IFACE_PATH = "src/main/java/com/example/CustomerService.java"
SERVICE_IFACE_SRC = (
    "package com.example;\n"
    "public interface CustomerService {\n"
    "}\n"
)

SERVICE_IMPL_PATH = "src/main/java/com/example/CustomerServiceImpl.java"
SERVICE_IMPL_SRC = (
    "package com.example;\n"
    "public class CustomerServiceImpl implements CustomerService {\n"
    "}\n"
)


def _write(tmp_path, relpath, content):
    p = tmp_path / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return str(p)


def _plan(subtask_specs):
    """subtask_specs: list of (subtask_id, [paths])."""
    return EngineeringPlan(
        plan_id="corr018-probe", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id=sid, description="d", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=p, action=FileAction.MODIFY) for p in paths],
            )
            for sid, paths in subtask_specs
        ],
    )


def _write_all_fixtures(tmp_path):
    _write(tmp_path, CUSTOMER_PATH, CUSTOMER_SRC)
    _write(tmp_path, RECORD_PATH, RECORD_SRC)
    _write(tmp_path, PRINTER_PATH, PRINTER_SRC)
    _write(tmp_path, SERVICE_IFACE_PATH, SERVICE_IFACE_SRC)
    _write(tmp_path, SERVICE_IMPL_PATH, SERVICE_IMPL_SRC)


# ---------------------------------------------------------------------------
# Config flag itself
# ---------------------------------------------------------------------------

def test_flag_defaults_false():
    assert AppConfig().autonomy.semantic_region_enforcement_required is False


def test_flag_is_sec009_security_authority():
    assert ("autonomy", "semantic_region_enforcement_required") in _SECURITY_AUTHORITY_FIELDS


# ---------------------------------------------------------------------------
# A/B/C/D/E/Q/R: derivation + strict-mode enforcement, combined scenario
# (Task 9's exact adversarial case + Task 10's counterpart + Task 12's
# record fixture, in one coherent multi-file plan)
# ---------------------------------------------------------------------------

def test_scenario_a_authorized_record_component_add_accepted(tmp_path):
    _write_all_fixtures(tmp_path)
    goal = "Add a field named `region` to `CustomerRecord`."
    plan = _plan([("s1", [RECORD_PATH])])
    regions = derive_semantic_authority_for_run(goal, plan, str(tmp_path))
    candidate = "package com.example;\npublic record CustomerRecord(String id, String name, String region) {}\n"
    violations = find_unauthorized_semantic_changes(
        {RECORD_PATH: RECORD_SRC}, {RECORD_PATH: candidate}, regions, strict_existing_java_files=True,
    )
    assert violations == []


def test_scenario_c_planner_only_extra_file_rejected(tmp_path):
    """C + Task 9: Planner plans CustomerPrinter.java for "cosmetic cleanup"
    - no requirement/repository necessity supports it. The candidate
    actually changes it -> rejected (the P10 class of failure)."""
    _write_all_fixtures(tmp_path)
    goal = "Add a field named `region` to `CustomerRecord`."
    plan = _plan([("s1", [RECORD_PATH]), ("s2", [PRINTER_PATH])])
    regions = derive_semantic_authority_for_run(goal, plan, str(tmp_path))
    assert not any(r.relpath == PRINTER_PATH for r in regions)  # Task 9: no authority at all

    candidate_printer = PRINTER_SRC.replace("return r.name();", "return r.name().toUpperCase();")
    violations = find_unauthorized_semantic_changes(
        {RECORD_PATH: RECORD_SRC, PRINTER_PATH: PRINTER_SRC},
        {RECORD_PATH: RECORD_SRC, PRINTER_PATH: candidate_printer},
        regions, strict_existing_java_files=True,
    )
    assert any(v.relpath == PRINTER_PATH and v.reason_code == REASON_FILE_HAS_NO_SEMANTIC_AUTHORITY for v in violations)


def test_scenario_q_valid_interface_implementation_evolution_accepted(tmp_path):
    """Q + Task 10: a legitimately required interface + implementation
    change, both Planner-selected, both grounded - both accepted."""
    _write_all_fixtures(tmp_path)
    goal = "Add a method named `region` to `CustomerService`."
    plan = _plan([("s1", [SERVICE_IFACE_PATH]), ("s2", [SERVICE_IMPL_PATH])])
    regions = derive_semantic_authority_for_run(goal, plan, str(tmp_path))
    assert any(r.relpath == SERVICE_IFACE_PATH and r.region_type == RegionType.METHOD_ADD for r in regions)
    assert any(r.relpath == SERVICE_IMPL_PATH and r.region_type == RegionType.METHOD_ADD for r in regions)

    cand_iface = (
        "package com.example;\n"
        "public interface CustomerService {\n"
        "    String region();\n"
        "}\n"
    )
    cand_impl = (
        "package com.example;\npublic class CustomerServiceImpl implements CustomerService {\n"
        "    public String region() { return \"unknown\"; }\n}\n"
    )
    violations = find_unauthorized_semantic_changes(
        {SERVICE_IFACE_PATH: SERVICE_IFACE_SRC, SERVICE_IMPL_PATH: SERVICE_IMPL_SRC},
        {SERVICE_IFACE_PATH: cand_iface, SERVICE_IMPL_PATH: cand_impl},
        regions, strict_existing_java_files=True,
    )
    assert violations == []


def test_scenario_r_third_planner_selected_cosmetic_file_rejected(tmp_path):
    """R: same interface+impl requirement as Q, but Planner ALSO includes
    CustomerPrinter.java for unrelated cosmetic cleanup - the printer
    change is rejected even though the plan otherwise contains a
    legitimate multi-file requirement."""
    _write_all_fixtures(tmp_path)
    goal = "Add a method named `region` to `CustomerService`."
    plan = _plan([("s1", [SERVICE_IFACE_PATH]), ("s2", [SERVICE_IMPL_PATH]), ("s3", [PRINTER_PATH])])
    regions = derive_semantic_authority_for_run(goal, plan, str(tmp_path))
    assert not any(r.relpath == PRINTER_PATH for r in regions)

    candidate_printer = PRINTER_SRC.replace("return r.name();", "return r.name().trim();")
    violations = find_unauthorized_semantic_changes(
        {PRINTER_PATH: PRINTER_SRC}, {PRINTER_PATH: candidate_printer},
        regions, strict_existing_java_files=True,
    )
    assert len(violations) == 1
    assert violations[0].reason_code == REASON_FILE_HAS_NO_SEMANTIC_AUTHORITY


def test_scenario_d_e_grounded_second_file_exact_region_only(tmp_path):
    """D: grounded second file (CustomerServiceImpl) gets exact METHOD_ADD
    region -> its OWN required change accepted. E: the SAME grounded file
    with an ADDITIONAL unrelated region changed too -> rejected (grounding
    authorizes the exact region, never the whole file)."""
    _write_all_fixtures(tmp_path)
    goal = "Add a method named `region` to `CustomerService`."
    plan = _plan([("s1", [SERVICE_IFACE_PATH]), ("s2", [SERVICE_IMPL_PATH])])
    regions = derive_semantic_authority_for_run(goal, plan, str(tmp_path))

    # D: exact required region only -> accept
    cand_impl_only = (
        "package com.example;\npublic class CustomerServiceImpl implements CustomerService {\n"
        "    public String region() { return \"unknown\"; }\n}\n"
    )
    v_d = find_unauthorized_semantic_changes(
        {SERVICE_IMPL_PATH: SERVICE_IMPL_SRC}, {SERVICE_IMPL_PATH: cand_impl_only},
        regions, strict_existing_java_files=True,
    )
    assert v_d == []

    # E: exact required region + an unrelated new helper method -> reject
    cand_impl_plus_extra = (
        "package com.example;\npublic class CustomerServiceImpl implements CustomerService {\n"
        "    public String region() { return \"unknown\"; }\n"
        "    public void unrelatedHelper() { }\n}\n"
    )
    v_e = find_unauthorized_semantic_changes(
        {SERVICE_IMPL_PATH: SERVICE_IMPL_SRC}, {SERVICE_IMPL_PATH: cand_impl_plus_extra},
        regions, strict_existing_java_files=True,
    )
    assert len(v_e) == 1 and v_e[0].reason_code == REASON_MEMBER_ADDED_UNAUTHORIZED


# ---------------------------------------------------------------------------
# F/G: field add / other field modification
# ---------------------------------------------------------------------------

def test_scenario_f_field_add_accepted(tmp_path):
    _write_all_fixtures(tmp_path)
    goal = "Add a field named `status` to `Customer`."
    plan = _plan([("s1", [CUSTOMER_PATH])])
    regions = derive_semantic_authority_for_run(goal, plan, str(tmp_path))
    key = stable_field_key(CUSTOMER_PATH, "Customer", "status")
    assert any(r.member_key == key and r.region_type == RegionType.FIELD_DECLARATION for r in regions)

    candidate = CUSTOMER_SRC.replace(
        "    private String region;\n", "    private String region;\n    private String status;\n",
    )
    violations = find_unauthorized_semantic_changes(
        {CUSTOMER_PATH: CUSTOMER_SRC}, {CUSTOMER_PATH: candidate}, regions, strict_existing_java_files=True,
    )
    assert violations == []


def test_scenario_g_other_field_modification_rejected(tmp_path):
    """Authority for Customer.status (newly added) must NOT authorize a
    change to the pre-existing, unrelated Customer.id field (Task 2's own
    required adversarial proof)."""
    _write_all_fixtures(tmp_path)
    goal = "Add a field named `status` to `Customer`."
    plan = _plan([("s1", [CUSTOMER_PATH])])
    regions = derive_semantic_authority_for_run(goal, plan, str(tmp_path))

    candidate = CUSTOMER_SRC.replace("private String id;", "private int id;").replace(
        "    private String region;\n", "    private String region;\n    private String status;\n",
    )
    violations = find_unauthorized_semantic_changes(
        {CUSTOMER_PATH: CUSTOMER_SRC}, {CUSTOMER_PATH: candidate}, regions, strict_existing_java_files=True,
    )
    assert any(v.reason_code == REASON_DECLARATION_UNAUTHORIZED and "id" in (v.member_key or "") for v in violations)


def test_field_authority_does_not_cross_owning_type(tmp_path):
    """Task 2's own required proof: authority for Customer.region must not
    authorize OtherCustomer.region."""
    region_key_customer = stable_field_key(CUSTOMER_PATH, "Customer", "region")
    region_key_other = stable_field_key(CUSTOMER_PATH, "OtherCustomer", "region")
    assert region_key_customer != region_key_other
    regions = [AuthorizedSemanticRegion(relpath=CUSTOMER_PATH, region_type=RegionType.FIELD_DECLARATION, member_key=region_key_other)]
    candidate = CUSTOMER_SRC.replace("private String region;", "private int region;")
    violations = find_unauthorized_semantic_changes({CUSTOMER_PATH: CUSTOMER_SRC}, {CUSTOMER_PATH: candidate}, regions)
    assert len(violations) == 1 and violations[0].reason_code == REASON_DECLARATION_UNAUTHORIZED


# ---------------------------------------------------------------------------
# H/I/J: record component add / other component change / component +
# unrelated method rewrite
# ---------------------------------------------------------------------------

def test_scenario_h_record_component_add_accepted(tmp_path):
    key = stable_record_component_key(RECORD_PATH, "CustomerRecord", "region")
    regions = [AuthorizedSemanticRegion(relpath=RECORD_PATH, region_type=RegionType.RECORD_COMPONENT, member_key=key)]
    candidate = "package com.example;\npublic record CustomerRecord(String id, String name, String region) {}\n"
    violations = find_unauthorized_semantic_changes({RECORD_PATH: RECORD_SRC}, {RECORD_PATH: candidate}, regions)
    assert violations == []


def test_scenario_i_other_record_component_change_rejected(tmp_path):
    key = stable_record_component_key(RECORD_PATH, "CustomerRecord", "region")
    regions = [AuthorizedSemanticRegion(relpath=RECORD_PATH, region_type=RegionType.RECORD_COMPONENT, member_key=key)]
    # authorized region add, PLUS an unrelated existing component's type changed
    candidate = "package com.example;\npublic record CustomerRecord(int id, String name, String region) {}\n"
    violations = find_unauthorized_semantic_changes({RECORD_PATH: RECORD_SRC}, {RECORD_PATH: candidate}, regions)
    assert any(v.reason_code == REASON_DECLARATION_UNAUTHORIZED and "id" in (v.member_key or "") for v in violations)


def test_scenario_j_record_component_authority_plus_unrelated_method_rewrite_rejected(tmp_path):
    """A record with an accessor-adjacent helper method - authority for the
    new component must not authorize an unrelated method body rewrite in
    the SAME file (recreates the P10 shape for a record fixture)."""
    record_with_method = (
        "package com.example;\n"
        "public record CustomerRecord(String id, String name) {\n"
        "    public String display() { return name(); }\n"
        "}\n"
    )
    key = stable_record_component_key(RECORD_PATH, "CustomerRecord", "region")
    regions = [AuthorizedSemanticRegion(relpath=RECORD_PATH, region_type=RegionType.RECORD_COMPONENT, member_key=key)]
    candidate = (
        "package com.example;\n"
        "public record CustomerRecord(String id, String name, String region) {\n"
        "    public String display() { return name().toUpperCase(); }\n"
        "}\n"
    )
    violations = find_unauthorized_semantic_changes({RECORD_PATH: record_with_method}, {RECORD_PATH: candidate}, regions)
    assert any(v.member_key and "display" in v.member_key for v in violations)


# ---------------------------------------------------------------------------
# K: authorized body + signature drift rejected (Task 11 same-file precision)
# ---------------------------------------------------------------------------

def test_scenario_k_authorized_body_plus_signature_drift_rejected(tmp_path):
    """A parameter-TYPE change alters the stable member key itself (params
    are part of identity, matching overload-disambiguation) and is a
    separate delete+add case, already covered by test_scenario_g/existing
    A3 tests - this scenario instead uses a RETURN-TYPE change, which
    keeps the same stable key (same name+params) but changes
    normalized_signature, the exact "signature changed under a BODY-only
    grant" shape Task 11/22-K describes."""
    baseline = (
        "package com.example;\n"
        "public class CustomerService {\n"
        "    public String find(String id) { return id; }\n"
        "}\n"
    )
    key = stable_member_key(SERVICE_IFACE_PATH, "CustomerService", "method", "find", ("String",))
    regions = [AuthorizedSemanticRegion(relpath=SERVICE_IFACE_PATH, region_type=RegionType.METHOD_BODY, member_key=key)]
    candidate = (
        "package com.example;\n"
        "public class CustomerService {\n"
        "    public CharSequence find(String id) { return id; }\n"
        "}\n"
    )
    violations = find_unauthorized_semantic_changes({SERVICE_IFACE_PATH: baseline}, {SERVICE_IFACE_PATH: candidate}, regions)
    assert any(v.reason_code == REASON_SIGNATURE_UNAUTHORIZED for v in violations)


# ---------------------------------------------------------------------------
# L/M: required import accepted, unrelated import rejected
# ---------------------------------------------------------------------------

def test_scenario_l_required_import_accepted(tmp_path):
    baseline = (
        "package com.example;\n"
        "public class Customer {\n"
        "    private String id;\n"
        "}\n"
    )
    key = stable_field_key(CUSTOMER_PATH, "Customer", "createdAt")
    regions = [
        AuthorizedSemanticRegion(relpath=CUSTOMER_PATH, region_type=RegionType.FIELD_DECLARATION, member_key=key),
        AuthorizedSemanticRegion(relpath=CUSTOMER_PATH, region_type=RegionType.IMPORTS),
    ]
    candidate = (
        "package com.example;\n"
        "import java.time.Instant;\n"
        "public class Customer {\n"
        "    private String id;\n"
        "    private Instant createdAt;\n"
        "}\n"
    )
    violations = find_unauthorized_semantic_changes({CUSTOMER_PATH: baseline}, {CUSTOMER_PATH: candidate}, regions)
    assert violations == []


def test_scenario_m_unrelated_import_rejected(tmp_path):
    baseline = (
        "package com.example;\n"
        "public class Customer {\n"
        "    private String id;\n"
        "}\n"
    )
    key = stable_field_key(CUSTOMER_PATH, "Customer", "createdAt")
    regions = [
        AuthorizedSemanticRegion(relpath=CUSTOMER_PATH, region_type=RegionType.FIELD_DECLARATION, member_key=key),
        AuthorizedSemanticRegion(relpath=CUSTOMER_PATH, region_type=RegionType.IMPORTS),
    ]
    candidate = (
        "package com.example;\n"
        "import java.time.Instant;\n"
        "import java.util.UUID;\n"
        "public class Customer {\n"
        "    private String id;\n"
        "    private Instant createdAt;\n"
        "}\n"
    )
    violations = find_unauthorized_semantic_changes({CUSTOMER_PATH: baseline}, {CUSTOMER_PATH: candidate}, regions)
    assert any(v.reason_code.startswith("SEMANTIC_IMPORT") for v in violations)


# ---------------------------------------------------------------------------
# N: retry after unauthorized drift - authority does not widen
# ---------------------------------------------------------------------------

def test_scenario_n_retry_cannot_widen_authority(tmp_path):
    """Attempt 1 illegally changes Customer.id; rejection references it.
    Attempt 2, re-derived from the SAME grounding_goal/plan, still has no
    authority for id (derivation is a pure function of goal+plan+repo
    content - it never reads a prior attempt's error/likely_files)."""
    _write_all_fixtures(tmp_path)
    goal = "Add a field named `status` to `Customer`."
    plan = _plan([("s1", [CUSTOMER_PATH])])

    attempt1_candidate = CUSTOMER_SRC.replace("private String id;", "private int id;")
    regions_1 = derive_semantic_authority_for_run(goal, plan, str(tmp_path))
    v1 = find_unauthorized_semantic_changes({CUSTOMER_PATH: CUSTOMER_SRC}, {CUSTOMER_PATH: attempt1_candidate}, regions_1)
    assert any("id" in (v.member_key or "") for v in v1)

    # Simulate a retry: re-derive fresh (this is what the real pre-write
    # gate does on every attempt - see attempt.py's own call site) and try
    # the SAME illegal id change again.
    regions_2 = derive_semantic_authority_for_run(goal, plan, str(tmp_path))
    assert regions_1 == regions_2  # deterministic, no widening between calls
    v2 = find_unauthorized_semantic_changes({CUSTOMER_PATH: CUSTOMER_SRC}, {CUSTOMER_PATH: attempt1_candidate}, regions_2)
    assert any("id" in (v.member_key or "") for v in v2)


# ---------------------------------------------------------------------------
# O: self-correction drift -> rejected pre-write (Task 15)
# ---------------------------------------------------------------------------

def test_scenario_o_self_correction_apply_patch_rejected_by_semantic_gate(tmp_path):
    """Calls _dispatch_tool_call directly (no LLM) with an apply_patch call
    that would change Customer.id without authority - proves the new
    pre-write gate wired into self_correction.py's own write path (Task 15
    Preferred: candidate -> semantic gate -> write) rejects it before any
    write happens."""
    from kriya.workflow.self_correction import _dispatch_tool_call

    worktree = str(tmp_path / "worktree")
    os.makedirs(worktree, exist_ok=True)
    full_path = os.path.join(worktree, "Customer.java")
    with open(full_path, "w") as fh:
        fh.write(CUSTOMER_SRC)

    status_key = stable_field_key(CUSTOMER_PATH.replace("src/main/java/com/example/", ""), "Customer", "status")
    # region authorized for "status" field addition only
    regions = [AuthorizedSemanticRegion(
        relpath="Customer.java", region_type=RegionType.FIELD_DECLARATION,
        member_key=stable_field_key("Customer.java", "Customer", "status"),
    )]
    call = {
        "name": "apply_patch",
        "arguments": {
            "filepath": "Customer.java",
            "edits": [{"search": "private String id;", "replace": "private int id;"}],
        },
    }
    result = _dispatch_tool_call(
        call, worktree, validator=None, files_in_scope=["Customer.java"],
        writable_files={"Customer.java"}, active_code_context="",
        modified_files={}, read_files=set(), observed_revisions={},
        scope_conflict_files=set(),
        authorized_semantic_regions=regions,
        strict_existing_java_files=False,
        baseline_contents={"Customer.java": CUSTOMER_SRC},
    )
    assert result.startswith("ERROR:")
    assert "semantic" in result.lower()
    # confirm nothing was actually written to disk
    with open(full_path) as fh:
        assert fh.read() == CUSTOMER_SRC


def test_self_correction_authorized_patch_still_succeeds(tmp_path):
    """The new gate must not block a LEGITIMATE, authorized self-correction
    patch (a plain False for baseline_contents=None is the default no-op -
    this test confirms the opt-in path itself doesn't over-reject when the
    patch IS inside the authorized region)."""
    from kriya.workflow.self_correction import _dispatch_tool_call

    worktree = str(tmp_path / "worktree")
    os.makedirs(worktree, exist_ok=True)
    full_path = os.path.join(worktree, "Customer.java")
    with open(full_path, "w") as fh:
        fh.write(CUSTOMER_SRC)

    key = stable_field_key("Customer.java", "Customer", "status")
    regions = [AuthorizedSemanticRegion(relpath="Customer.java", region_type=RegionType.FIELD_DECLARATION, member_key=key)]
    call = {
        "name": "apply_patch",
        "arguments": {
            "filepath": "Customer.java",
            "edits": [{"search": "private String region;", "replace": "private String region;\n    private String status;"}],
        },
    }
    result = _dispatch_tool_call(
        call, worktree, validator=None, files_in_scope=["Customer.java"],
        writable_files={"Customer.java"}, active_code_context="",
        modified_files={}, read_files=set(), observed_revisions={},
        scope_conflict_files=set(),
        authorized_semantic_regions=regions,
        strict_existing_java_files=False,
        baseline_contents={"Customer.java": CUSTOMER_SRC},
    )
    assert not result.startswith("ERROR:"), result
    with open(full_path) as fh:
        assert "status" in fh.read()


# ---------------------------------------------------------------------------
# P: final-workspace post-acceptance drift caught terminally (Task 16)
# ---------------------------------------------------------------------------

def test_scenario_p_terminal_recheck_catches_post_acceptance_drift(tmp_path):
    """Simulates the terminal-recheck shape directly (state.all_original_
    contents vs. a FRESH read of final workspace content, exactly what
    workflow.py's own terminal call site does) - an initial accepted change
    followed by a later, unauthorized drift (e.g. a subsequent repair pass)
    is still caught, because the terminal check always re-diffs against
    the TRUE original baseline, never trusting the earlier pre-write pass."""
    key = stable_field_key("Customer.java", "Customer", "status")
    regions = [AuthorizedSemanticRegion(relpath="Customer.java", region_type=RegionType.FIELD_DECLARATION, member_key=key)]
    original_contents = {"Customer.java": CUSTOMER_SRC}
    # "final" workspace content after some later, unauthorized repair pass
    # touched an unrelated member post-acceptance
    final_contents = {
        "Customer.java": CUSTOMER_SRC.replace(
            "private String region;", "private String region;\n    private String status;",
        ).replace("private String id;", "private int id;"),
    }
    violations = find_unauthorized_semantic_changes(
        original_contents, final_contents, regions, strict_existing_java_files=True,
    )
    assert any("id" in (v.member_key or "") for v in violations)


def test_terminal_call_site_passes_strict_mode_flag():
    """Structural proof the terminal recheck in workflow.py actually wires
    strict_existing_java_files= from the config flag (not just the pre-
    write gate) - a silent omission here would mean strict mode is only
    half-enforced."""
    with open(os.path.join(_WORKFLOW_DIR, "workflow.py"), encoding="utf-8") as fh:
        source = fh.read()
    assert "strict_existing_java_files=self.kernel.config.autonomy.semantic_region_enforcement_required" in source


def test_pre_write_call_site_passes_strict_mode_flag():
    with open(os.path.join(_WORKFLOW_DIR, "attempt.py"), encoding="utf-8") as fh:
        source = fh.read()
    assert "strict_existing_java_files=ctx.kernel.config.autonomy.semantic_region_enforcement_required" in source


# ---------------------------------------------------------------------------
# Member identity (Task 17) - overloads, same field name in different
# classes, nested classes, records
# ---------------------------------------------------------------------------

def test_overloaded_methods_distinct_keys():
    key1 = stable_member_key("X.java", "X", "method", "find", ("String",))
    key2 = stable_member_key("X.java", "X", "method", "find", ("long",))
    assert key1 != key2


def test_same_field_name_different_classes_distinct_keys():
    assert stable_field_key("A.java", "A", "region") != stable_field_key("B.java", "B", "region")


def test_record_components_in_different_records_distinct_keys():
    assert (
        stable_record_component_key("A.java", "RecA", "id")
        != stable_record_component_key("B.java", "RecB", "id")
    )


def test_constructor_and_method_same_name_distinct_namespaces():
    ctor_key = stable_member_key("X.java", "X", "constructor", "X", ())
    method_key = stable_member_key("X.java", "X", "method", "X", ())
    assert ctor_key != method_key


# ---------------------------------------------------------------------------
# Public API composition (Task 18) - semantic authority and the public-API
# gate stay independent, never merged
# ---------------------------------------------------------------------------

def test_semantic_gate_and_brownfield_gate_are_separate_functions():
    """Structural proof (Invariant: no duplicate public-API implementation)
    - semantic_region_authority.py never IMPORTS file_resolution.py's
    find_brownfield_public_api_changes() (a real call/merge would require
    an import; a docstring cross-reference by name, which this module's
    own prose does have, is not a code dependency)."""
    path = os.path.join(_WORKFLOW_DIR, "semantic_region_authority.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=path)
    imported_names = set()
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
            imported_modules.add(node.module or "")
    assert "find_brownfield_public_api_changes" not in imported_names
    assert not any("file_resolution" in m for m in imported_modules)


def test_semantic_authority_rejects_signature_drift_independent_of_public_api_gate():
    """A METHOD_BODY-only authorization rejects a signature change on its
    own (already proven, Scenario K) - this test just confirms the
    rejection happens via find_unauthorized_semantic_changes alone, with
    no dependency on file_resolution.py's own gate having run first."""
    baseline = (
        "package com.example;\n"
        "public class X {\n"
        "    public String find(String id) { return id; }\n"
        "}\n"
    )
    candidate = (
        "package com.example;\n"
        "public class X {\n"
        "    public CharSequence find(String id) { return id; }\n"
        "}\n"
    )
    key = stable_member_key("X.java", "X", "method", "find", ("String",))
    regions = [AuthorizedSemanticRegion(relpath="X.java", region_type=RegionType.METHOD_BODY, member_key=key)]
    violations = find_unauthorized_semantic_changes({"X.java": baseline}, {"X.java": candidate}, regions)
    assert any(v.reason_code == REASON_SIGNATURE_UNAUTHORIZED for v in violations)


# ---------------------------------------------------------------------------
# Flag-off preservation (user's own explicit correction): today's behavior
# must be byte-identical when the flag is False.
# ---------------------------------------------------------------------------

def test_flag_off_non_strict_mode_is_a_full_noop_for_unlisted_files(tmp_path):
    other = "public class Other { public void m() { } }\n"
    other_candidate = "public class Other { public void m() { System.out.println(1); } }\n"
    violations = find_unauthorized_semantic_changes(
        {"Other.java": other}, {"Other.java": other_candidate}, [],
        strict_existing_java_files=False,
    )
    assert violations == []


def test_a3_proposal_call_shape_unaffected_by_new_strict_parameter():
    """A3's own call sites never pass strict_existing_java_files - the
    default (False) preserves byte-identical behavior, proven here by
    confirming the default parameter value itself."""
    import inspect
    sig = inspect.signature(find_unauthorized_semantic_changes)
    assert sig.parameters["strict_existing_java_files"].default is False


# ---------------------------------------------------------------------------
# Bypass sweep (Task 21) - UNEXPLAINED_SEMANTIC_BYPASSES = 0
# ---------------------------------------------------------------------------

def test_derivation_never_reads_planner_authored_text():
    """Invariant 3/5: semantic_scope_derivation.py never reads
    Subtask.description/requires/provides/GlobalInvariant anywhere."""
    path = os.path.join(_WORKFLOW_DIR, "semantic_scope_derivation.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=path)
    forbidden_attrs = {"description", "requires", "provides", "relevant_global_invariant_ids"}
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
            hits.append(node.attr)
    assert hits == [], f"semantic_scope_derivation.py reads Planner-authored field(s): {hits}"


def test_repository_grounded_derivation_scoped_to_plan_planned_files_only():
    """Task 7's own explicit bound: the repository-grounded derivation
    never scans files outside structured_plan's own planned_files (no
    unbounded repository-wide scan)."""
    path = os.path.join(_WORKFLOW_DIR, "semantic_scope_derivation.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    assert "os.walk" not in source
    assert "plan_paths" in source  # scoped iteration variable, not a full-repo listdir


def test_authorized_file_writer_and_semantic_gate_remain_independent_layers():
    """Structural proof: kriya/policy/filesystem.py (file-write authority)
    never imports semantic_region_authority.py or semantic_scope_
    derivation.py - the two authority layers stay independently composed,
    never merged into one gate (Invariant 1)."""
    path = os.path.join(_REPO_ROOT, "kriya", "policy", "filesystem.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    assert "semantic_region_authority" not in source
    assert "semantic_scope_derivation" not in source
