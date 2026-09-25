"""PLANNER-ROBUST-001 (2026-09-19): the shared, Planner-boundary-only
structured-plan repair primitives - extracted from
kriya/workflow/workflow_controller.py so BOTH the Hardened/enforce
structured-planning loop (workflow_controller.py) AND the legacy
`run_generation_workflow()` (kriya/workflow/workflow.py) can apply the
EXACT SAME bounded repair semantics to a schema-invalid Planner response,
without either module depending on the other's own execution machinery.

Root cause this closes (see docs/assurance/ for the full live G1
investigation): workflow_controller.py already had a complete, well-tested
bounded structured-plan repair loop (build_structured_plan_repair_prompt
below, its own reason-code-driven targeted corrections, a 2-repair-attempt
bound). The legacy path's own authoritative completeness gate
(kriya/workflow/file_resolution.py::classify_plan_completeness) produces
the identical "schema_invalid" classification for the identical failure
shape, but had NO repair mechanism at all - a single schema-invalid
subtask (e.g. execution_method=tool with no tool_name) terminated the
ENTIRE run before Architect, discarding an otherwise-correct Planner
response, by explicit prior design ("No Planner retry is added here - each
branch below returns exactly once").

Extraction boundary, deliberately narrow: this module holds ONLY the two
pure, Planner-boundary functions that do not depend on WorkflowController's
own state (ObligationLedger, ExecutionRoute, regression-oscillation
tracking) - `must_preserve`/`validation_evidence`/`route_kind`/
`extension_candidates`/`repository_candidates` remain OPTIONAL, caller-
supplied parameters exactly as before (all still default to None/empty),
so a caller with no such state (the legacy path) gets the same correction
text minus the WorkflowController-specific reinforcement lines, never a
degraded or different CORE contract. WorkflowController's own repair LOOP
(the while-loop orchestration, obligation-ledger bookkeeping, oscillation/
non-convergence classification) stays exactly where it is - only the
prompt-building and reason-code-classification primitives moved.

Import-direction note: workflow_controller.py already imports FROM
workflow.py (`from kriya.workflow.workflow import _log_phase_banner`) - so
importing these functions the other way around (workflow.py importing
straight from workflow_controller.py) would be a real circular import, not
a style choice. This module has no dependency on either, so both can
import from it safely."""
import json
from typing import Any, Dict, List, Optional

from kriya.workflow.triage import ChangeKind

# The existing, already-live-tested WorkflowController bound: 2 REPAIR
# attempts beyond the initial Planner response (3 total Planner calls in
# the worst case) before the caller must terminate truthfully rather than
# repair further. Both callers of build_structured_plan_repair_prompt below
# share this exact constant - PLANNER-ROBUST-001 does not increase it.
STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS = 2


def classify_structured_plan_parse_issue(
    parse_issue: Optional[str], explicit_reason_codes: Optional[List[str]] = None,
) -> List[str]:
    """The one, shared reason-code classifier for a
    parse_planner_structured_output() failure message - previously
    duplicated inline in workflow_controller.py's own structured-planning
    loop; now the single source of truth both that loop and the legacy
    path's own repair entry point (kriya/workflow/workflow.py) call, so the
    two paths can never classify the identical failure text differently
    (PLANNER-ROBUST-001's own "equivalent repair classification" test).

    explicit_reason_codes (PLANNER-ROBUST-001 P2/P9, 2026-09-19): when the
    caller already has a real, typed classification for this failure
    (currently only kriya/workflow/file_resolution.py::
    classify_plan_completeness()'s own PlanCompletenessResult.reason_codes,
    for the tool-capability-membership check it runs via kriya/workflow/
    planner_validation.py), it is returned AS-IS, bypassing substring
    inference over `parse_issue` entirely for that call - a typed source
    is always preferred over inferring one after the fact. None/empty
    (every other classification this function has ever produced) falls
    back to the original substring matching below, UNCHANGED - this
    package deliberately does not attempt to replace that fallback's own
    coupling to parse_planner_structured_output()'s raw Pydantic error
    text; see PLANNER-ROBUST-001's own closure report for why that's
    disclosed as remaining technical debt rather than restructured here.
    Never guesses/invents a reason code for a message shape it doesn't
    recognize - falls back to the generic STRUCTURED_PLAN_PARSE_FAILED,
    exactly as the original inline logic did."""
    if explicit_reason_codes:
        return list(explicit_reason_codes)
    issue = parse_issue or ""
    if "execution_method=tool but no tool_name" in issue:
        return ["TOOL_SUBTASK_MISSING_TOOL_NAME"]
    if "failed schema validation" in issue:
        return ["STRUCTURED_PLAN_SCHEMA_INVALID"]
    return ["STRUCTURED_PLAN_PARSE_FAILED"]


def build_structured_plan_repair_prompt(
    goal: str,
    previous_plan_text: str,
    errors: List[str],
    reason_codes: List[str],
    repair_attempt: int,
    *,
    route_kind: Optional[ChangeKind] = None,
    extension_candidates: Optional[List[str]] = None,
    repository_candidates: Optional[List[str]] = None,
    must_preserve: Optional[List[str]] = None,
    validation_evidence: Optional[List[Dict[str, Any]]] = None,
    available_tool_names: Optional[List[str]] = None,
) -> str:
    """Build a bounded local-only correction request for the complete plan.

    must_preserve (PRV-05 run #8, MA8 - kriya/workflow/obligations.py):
    human-readable descriptions of obligations the PREVIOUS draft already
    satisfied (computed by the caller from the ObligationLedger, not
    re-derived here) - found live, run #8: the Planner fixed
    refactor_baseline on repair attempt 2 but silently regressed an
    already-fixed planned-file action, because the repair prompt only ever
    showed the CURRENT attempt's error list, with nothing telling the model
    that both constraints had to hold simultaneously. Originally
    PLAN_STRUCTURAL_VALIDITY only; the caller now also folds in
    PRESERVED_REFERENCE obligations (PRV-11 preservation extension,
    2026-09-07, Production Validation P5) for the identical reason at a
    finer resolution: P5's own live incident showed the strict-regression
    guard (_is_strict_regression, reason-code-set level) cannot see a
    regression INSIDE a single reason code's own evidence - two rounds
    both reporting only MISSING_GROUNDED_PRODUCTION_ARTIFACT looked
    identical even though round 2 silently dropped an already-correct
    preserved_references entry (BaseEntity.java) while adding a new one
    (Visit.java) instead of retaining both. Each PRESERVED_REFERENCE line
    self-attributes its own (source, target) pair explicitly, so one
    file's already-validated preservation can never be misread as applying
    to a different file - no separate grouping/scoping structure needed,
    and no new ledger: this reuses relevant_for_preservation() exactly as
    PLAN_STRUCTURAL_VALIDITY already does, surfacing validated obligation
    state, never the previous draft's raw JSON directly. This is a
    best-effort PROMPT instruction, not an enforcement mechanism
    - the ledger's own regression detection (surfaced by the caller as
    PLAN_REPAIR_OSCILLATION/PLAN_REPAIR_NON_CONVERGENCE) is what actually
    catches it if the model ignores this anyway.

    A caller with no ObligationLedger of its own (the legacy `run_
    generation_workflow()` path) simply never passes must_preserve/
    validation_evidence/route_kind/extension_candidates/
    repository_candidates - every one of them already defaults to None/
    empty, producing the identical CORE correction text (the reason-code-
    driven targeted_correction blocks below, keyed only on `reason_codes`,
    which every caller always has) minus the WorkflowController-specific
    MUST PRESERVE reinforcement section, never a different contract.

    available_tool_names (PLANNER-ROBUST-001, 2026-09-19): the caller's own
    already-resolved kernel.registry.list_components("tool") snapshot -
    reused as-is, never re-derived or hardcoded here. Deliberately narrow:
    only ever surfaced inside the TOOL_SUBTASK_MISSING_TOOL_NAME targeted
    correction below (the one reason code where a real tool name is
    actually needed) - never a blanket catalog dump appended regardless of
    reason code. None/empty means "no registered tools this run" - the
    correction text then tells the model plainly rather than silently
    saying nothing, so a Planner never guesses a name into existence."""
    targeted_correction = ""
    prerequisite_evidence = [
        item for item in (validation_evidence or [])
        if item.get("consumer_subtask") and item.get("provider_subtask")
        and (item.get("missing_requires_edge") or item.get("missing_depends_on_edge"))
    ]
    if prerequisite_evidence:
        targeted_correction += "- Apply these exact planned-prerequisite corrections:\n"
        for item in prerequisite_evidence:
            targeted_correction += (
                f"  Consumer: subtask={item['consumer_subtask']} file={item['consumer_file']}\n"
                f"  Required planned capability: {item['prerequisite_capability']}\n"
                f"  Provider: subtask={item['provider_subtask']} file={item['provider_file']}\n"
            )
            if item.get("missing_requires_edge"):
                targeted_correction += (
                    f"  Required repair: add {item['prerequisite_capability']} to "
                    f"{item['consumer_subtask']}.requires\n"
                )
            if item.get("missing_depends_on_edge"):
                targeted_correction += (
                    f"  Required repair: add {item['provider_subtask']} to "
                    f"{item['consumer_subtask']}.depends_on\n"
                )
            targeted_correction += "  Preserve unrelated valid plan edges.\n"
    if "TOOL_SUBTASK_MISSING_TOOL_NAME" in reason_codes or "UNREGISTERED_TOOL_NAME" in reason_codes:
        # TOOL-001 (2026-09-13): TOOL-execution-method subtasks are now
        # supported in enforce mode (governed execution via the existing
        # BaseTool.execute() boundary - see _run_structured_enforce's own
        # per-subtask loop). TOOL_SUBTASK_UNSUPPORTED_IN_ENFORCE no longer
        # exists as a reason code; this guidance now applies to a
        # genuinely malformed TOOL subtask missing its required tool_name,
        # AND (PLANNER-ROBUST-001 P2, 2026-09-19) to one whose tool_name IS
        # set but doesn't resolve to a real registered tool (UNREGISTERED_
        # TOOL_NAME, kriya/workflow/planner_validation.py) - the corrective
        # action is identical for both: point the model at the real
        # catalog below, so both codes share this one block rather than
        # duplicating near-identical guidance.
        targeted_correction += (
            "- A TOOL-execution-method subtask requires tool_name to be set (it names the "
            "registered tool this subtask actually runs). Set it to the exact registered tool "
            "name, or if this was meant to be a code-generation step, use execution_method=model "
            "instead.\n"
            "- Do not confuse this with a verification item's own tool_name (inside a subtask's "
            "verification[] list) - that field identifies a deterministic CHECK (e.g. "
            "tool_name=compile or tool_name=test) and never satisfies the subtask's OWN top-level "
            "execution_method=tool requirement. A verification-only subtask (execution_role="
            "verification, planned_files=[]) whose check is itself deterministic should virtually "
            "always use execution_method=model with a real verification[] entry, not "
            "execution_method=tool at the subtask level.\n"
        )
        if available_tool_names:
            targeted_correction += (
                "- The ONLY valid values for a Subtask's own tool_name are these currently "
                f"registered Kriya tools: {json.dumps(sorted(available_tool_names))}. Never invent "
                "a name not in this list (a verifier keyword like \"pytest\"/\"compile\"/\"test\" is "
                "NOT a valid Subtask.tool_name - those belong only inside verification[].tool_name, "
                "a different field with a different vocabulary).\n"
            )
        else:
            targeted_correction += (
                "- No Kriya tools are currently registered for direct subtask execution in this "
                "run, so execution_method=tool has no valid tool_name to set at all right now - use "
                "execution_method=model instead.\n"
            )
    if "MODEL_SUBTASK_MISSING_PLANNED_FILES" in reason_codes:
        targeted_correction += (
            "- For each named unscoped MODEL subtask: if it is a non-editing build/test/run/output "
            "check, set its execution_role to verification (keep it as its own subtask, keep its "
            "depends_on and acceptance_criteria_ids) and give it at least one concrete verification "
            "entry - do NOT remove it or invent a planned_files path for it. If it genuinely edits "
            "files, retain it as execution_role=implementation with the exact real planned_files it "
            "owns. Never invent a fake file for a check.\n"
        )
    if "STRUCTURED_PLAN_SCHEMA_INVALID" in reason_codes:
        targeted_correction += (
            "- Repair every schema-invalid field to the system contract. In particular, each "
            "verification item must be an object with type, description, verifier_kind, and "
            "requires_runtime_execution; never use a string verification item.\n"
        )
    if "SUBTASK_REQUIREMENT_UNPROVIDED" in reason_codes:
        targeted_correction += (
            "- Replace each unprovided requires value with the exact, character-for-character "
            "provides value exported by its declared upstream dependency. Do not paraphrase "
            "capability names.\n"
        )
    if "AMBIGUOUS_PLANNED_FILE_OWNERSHIP" in reason_codes:
        candidates = repository_candidates or []
        targeted_correction += (
            "- Each planned file path must be owned by exactly one MODEL subtask. For every "
            "duplicated path named in the validation errors, retain it only on the subtask that "
            "actually performs that file's implementation change. REMOVE any separate MODEL "
            "subtask whose sole purpose is to analyze, inspect, research, or explain that same "
            "file; fold necessary analysis into the implementation subtask. Express downstream checks as "
            "verification or acceptance criteria on an appropriate implementation subtask; do "
            "not duplicate a path merely so another subtask can compile, test, inspect, or use "
            "it. Preserve real dependency edges and do not rename, replace, or invent files to "
            "avoid the ownership conflict. Existing local paths available as ownership evidence: "
            f"{json.dumps(candidates)}. For modify/delete, use an exact relevant existing path; "
            "do not create or rename a parallel artifact when a relevant owner exists.\n"
        )
    if "EXTENSION_POINT_REQUIRED" in reason_codes:
        candidates = extension_candidates or []
        targeted_correction += (
            f"- The {route_kind.value if route_kind else 'current'} route requires a real "
            "extension point. Set extension_points using only these existing relative paths: "
            f"{json.dumps(candidates)}. Do not invent a path.\n"
        )
    if "REFACTOR_BASELINE_MISSING" in reason_codes:
        targeted_correction += (
            "- Set refactor_baseline to the exact id (a string like \"s3\") of the subtask whose "
            "completed output the equivalence verification should be ordered against - typically "
            "the LAST implementation subtask in dependency order, not an empty string, null, or a "
            "prose description.\n"
        )
    if "PLANNED_FILE_ACTION_MISMATCH" in reason_codes:
        targeted_correction += (
            "- For each planned file the errors name as an action mismatch: if the file does not "
            "yet exist in the repository evidence, its action must be \"create\"; if it already "
            "exists, its action must be \"modify\" (or \"delete\"). Do not change which subtask "
            "owns the file, only its action.\n"
        )
    if "VERIFICATION_EVIDENCE_PATH_MISSING" in reason_codes:
        targeted_correction += (
            "- For each verification requirement the errors name as having no evidence producer: "
            "it cannot remain type=judgment with tool_name=null and requires_runtime_execution=false "
            "- Kriya has no way to ever confirm it passed. If satisfying it requires actually "
            "running the built application (observing output, exit behavior, processing sample "
            "input, or another runtime side effect), set type=judgment, omit tool_name entirely, "
            "and set verifier_kind=application_runtime and requires_runtime_execution=true TOGETHER "
            "- all four together; never set type=tool or any tool_name for this case. If it can be "
            "confirmed by compiling or running the test suite instead, set type=tool with "
            "tool_name=compile/verifier_kind=compile or tool_name=test/verifier_kind=test instead. "
            "Do not just restate the same judgment-only shape.\n"
        )
    if "MISSING_GROUNDED_PRODUCTION_ARTIFACT" in reason_codes:
        targeted_correction += (
            "- For each production artifact the errors name as referenced by a test file but "
            "owned by no subtask (grounded structural evidence - a real import, method call, or "
            "constructor instantiation the previous draft's own repository already contains, not "
            "a guess): account for that grounded artifact in the planned production path. If the "
            "authoritative goal and repository evidence show that satisfying the goal requires "
            "changing it, assign it to a MODEL implementation subtask using its existing path and "
            "action=modify. Ensure the downstream verification subtask's depends_on and requires "
            "route through the responsible production owner and one of that owner's provides "
            "capabilities. Do not infer that the artifact must be modified solely because the "
            "structural edge exists. If the authoritative goal and repository evidence do NOT "
            "require changing it, do not invent an owner for it at all - instead add its exact "
            "existing path to the referencing test file's own planned_files[].preserved_references "
            "list, to declare that this reference is intentional and the file stays unchanged.\n"
        )
    if "MISWIRED_GROUNDED_DEPENDENCY_EDGE" in reason_codes:
        targeted_correction += (
            "- For each test file the errors name as referencing a production artifact owned by a "
            "specific subtask that is outside its own dependency chain (the owning subtask id is "
            "given in the error text itself): change that test subtask's own requires to the exact "
            "provides value the NAMED owning subtask exports, and add that owning subtask's id to "
            "the test subtask's own depends_on - do not leave requires/depends_on pointing at an "
            "earlier producer already in the chain merely because a dependency edge exists to it. "
            "The grounded evidence names the file the test actually references; requires/depends_on "
            "must route through whichever subtask really owns that exact file.\n"
        )
    if "PRESERVED_REFERENCE_CONFLICTS_WITH_OWNERSHIP" in reason_codes:
        # P6 production-validation run 1 (2026-09-07): the Planner correctly
        # identified every grounded edge on attempt 1, but wrongly declared
        # an actively-owned production file (owned by a DIFFERENT subtask)
        # as its own preserved_references target too. On the final repair
        # attempt it fixed the other reported issue but left this exact
        # entry untouched, because - unlike MISSING_GROUNDED_PRODUCTION_
        # ARTIFACT/MISWIRED_GROUNDED_DEPENDENCY_EDGE above - this reason
        # code had no targeted correction at all, only the generic error
        # string. Point 3 below ("retain every other already-validated
        # preserved_references entry") is not duplicated here: it is
        # already covered by _preserved_reference_must_preserve_lines()'s
        # own MUST PRESERVE reinforcement (P5 fix) for every entry the
        # ledger has already validated as SATISFIED.
        targeted_correction += (
            "- For each conflict the errors name (a file declared in one subtask's "
            "preserved_references, but planned for modification by a different subtask - the "
            "owning subtask id is given in the error text itself): remove ONLY that specific "
            "conflicting target from the declaring subtask's preserved_references; do not touch "
            "any other preserved_references entry, on that subtask or any other. Do not take "
            "modification ownership of the conflicting file yourself - it already has a real "
            "owner. If the declaring subtask genuinely needs that file's output to exist first, "
            "add the named owning subtask's id to the declaring subtask's own depends_on instead "
            "of declaring the file preserved.\n"
        )
    if "GROUNDED_SEMANTIC_PROVIDER_MISMATCH" in reason_codes:
        targeted_correction += (
            "- For each grounded test-to-production relationship whose production owner is already "
            "in the test subtask's depends_on chain, also route semantic responsibility through "
            "that same owner: add one of the named owner's provides capabilities to the test "
            "subtask's requires. Remove any requires value that incorrectly routes this grounded "
            "verification through an earlier or unrelated producer. Keep depends_on and "
            "requires -> provides aligned with the same responsible owner.\n"
        )
    if "VERIFICATION_PREREQUISITE_MANIFEST_MISSING" in reason_codes:
        # PRV-17 Run 8 diagnostic audit (2026-09-03): the structured
        # record _stack_dependent_verification_prerequisite_evidence()
        # (plan_validation.py) attaches to validate_plan()'s own
        # `evidence` list - {consumer_subtask, required_tool,
        # candidate_manifests} - already reaches this function's
        # `validation_evidence` parameter (workflow_controller.py extends
        # it straight from validation.evidence), but was silently dropped
        # here: the `prerequisite_evidence` filter above requires a
        # `provider_subtask` key this record never has (there IS no
        # provider yet - that's the entire reason the record exists), and
        # unlike every other reason_code in this function, nothing
        # consumed it into an actionable correction - the reason code
        # string and free-text error alone reached the repair prompt,
        # with no explicit "add a provider, order it ahead" instruction
        # the way SUBTASK_REQUIREMENT_UNPROVIDED/MISSING_GROUNDED_
        # PRODUCTION_ARTIFACT/etc. above already get. Generic by
        # construction - required_tool/candidate_manifests come from
        # plan_validation.py's own _TOOL_MANIFEST_BASENAMES table (every
        # supported ecosystem, not just Python/Django), so this carries no
        # framework-specific special case.
        manifest_missing_evidence = [
            item for item in (validation_evidence or [])
            if item.get("consumer_subtask") and item.get("required_tool") and item.get("candidate_manifests")
        ]
        if manifest_missing_evidence:
            # Correctness fix (2026-09-03, post-audit review): the first
            # draft of this text collapsed classify_file_ownership()'s two
            # independently-legal satisfying relations (CURRENT: the
            # consumer subtask plans the manifest itself - no depends_on
            # involved at all - and PAST_ORDERED: a separate upstream
            # subtask plans it and the consumer depends_on that subtask)
            # into one prescriptive "add it to depends_on" instruction,
            # which would have wrongly steered the Planner into always
            # inventing a separate provider subtask even when the
            # simplest, equally valid repair is for the consumer to plan
            # its own manifest file. State the actual invariant, not one
            # implementation of it.
            targeted_correction += "- Establish the missing dependency-provisioning prerequisite:\n"
            for item in manifest_missing_evidence:
                manifests = "/".join(item["candidate_manifests"])
                targeted_correction += (
                    f"  Consumer: subtask={item['consumer_subtask']} runs "
                    f"{item['required_tool']}-dependent verification\n"
                    f"  Required repair: the dependency manifest (one of: {manifests}) must be "
                    "provided either (1) by this consumer subtask itself - add it to that "
                    f"subtask's own planned_files - or (2) by a subtask ordered before "
                    f"{item['consumer_subtask']} - if using a separate provider subtask, add that "
                    f"provider to {item['consumer_subtask']}.depends_on (directly or transitively) "
                    "so it is established before the verification consumer runs. Do not remove or "
                    "weaken the verification itself to satisfy this.\n"
                )
    if "UNKNOWN_GLOBAL_INVARIANT" in reason_codes:
        targeted_correction += (
            "- For each subtask the errors name as referencing an unknown global invariant id: "
            "replace that entry with one of the declared ids listed in the same error (shown as "
            "\"declared ids are [...]\"), the one whose statement is actually relevant to this "
            "subtask. Do not invent a new id, do not restate the invariant's statement text as the "
            "id, and do not add a new entry to global_invariants unless the goal states a real "
            "constraint no existing invariant covers. A subtask relevant to only part of a compound "
            "invariant still references that invariant's existing id whole - it does not split it "
            "into a new id or a partial statement. Existing global invariant ids from the previous "
            "draft must be preserved unchanged (same id, same statement) unless the invariant "
            "itself is being genuinely removed or replaced.\n"
        )
    # Repair Guidance audit (2026-09-07, before P7): the user's own P6
    # diagnosis - three independent live incidents (P2, P5, P6) all being
    # the same systemic gap between the validation side's rich reason
    # codes and this function's ad-hoc, one-at-a-time-discovered guidance -
    # prompted a bounded audit of every structured-plan reason code BEFORE
    # starting P7, rather than continuing to find the next gap only when a
    # live run burns wall-clock time on it. These ten blocks are that
    # audit's real findings: reason codes that were already produced by
    # validate_plan()/find_missing_grounded_production_artifacts() (or, for
    # the three DUPLICATE_SUBTASK_ID/SUBTASK_DEPENDS_ON_UNKNOWN_ID/
    # SUBTASK_DEPENDENCY_CYCLE codes, newly given a reason code in the same
    # audit - see plan_validation.py) with no targeted correction at all.
    # Every block below follows the same established pattern already used
    # throughout this function: read the specific detail (subtask id, file
    # path, capability name) straight out of the deterministic error text
    # rather than re-deriving it, exactly like MISWIRED_GROUNDED_DEPENDENCY_
    # EDGE already does above. No new evidence plumbing was needed for any
    # of these - the existing error strings already name everything a
    # correction needs.
    if "SEMANTIC_DEPENDENCY_EDGE_MISSING" in reason_codes:
        targeted_correction += (
            "- For each subtask the errors name as requiring a capability from a single, named "
            "provider subtask but not declaring that provider in depends_on (the provider's exact "
            "subtask id is given in the error text): add that provider's id to the consumer "
            "subtask's own depends_on. Do not change requires/provides values themselves, only add "
            "the missing dependency edge.\n"
        )
    if "SUBTASK_SEMANTIC_CONTRACT_MISSING" in reason_codes:
        targeted_correction += (
            "- For each subtask the errors name as declaring neither provides nor requires: give it "
            "at least one of the two. If it consumes another subtask's output, add the exact "
            "provides string that subtask exports to this subtask's own requires (and add that "
            "subtask to depends_on if not already present). If it produces something a later "
            "subtask consumes (or is the terminal verification stage), add a stable provides "
            "capability string describing what it produces. Do not invent a contract for a subtask "
            "that is genuinely self-contained and consumed by nothing - only the subtasks the "
            "errors actually name need this.\n"
        )
    if "AMBIGUOUS_SUBTASK_CAPABILITY_PROVIDER" in reason_codes:
        targeted_correction += (
            "- For each capability the errors name as provided by more than one subtask: keep that "
            "exact capability string in provides on only ONE of those subtasks - the one that "
            "actually produces it - and remove it from every other subtask's provides. If two "
            "subtasks genuinely each produce something related but distinct, give each its own, "
            "differently-named capability string instead of sharing one.\n"
        )
    if "APPLICATION_RUNTIME_OWNER_MISSING" in reason_codes:
        targeted_correction += (
            "- The authoritative goal requires observing the real running application, but no "
            "subtask's verification currently sets requires_runtime_execution=true with "
            "verifier_kind=application_runtime. Identify the subtask whose verification is meant to "
            "prove this (typically the final, runnable-entrypoint stage) and set exactly that "
            "combination on its own verification entry - compile/test verifiers cannot satisfy this "
            "requirement, only a real application_runtime verifier can.\n"
        )
    if "AUTHORITATIVE_STACK_SUBSTITUTION" in reason_codes:
        targeted_correction += (
            "- The errors name a specific planned file (STACK_CONTRACT_VIOLATION: <path> belongs to "
            "<wrong family>, but the authoritative USER_GOAL requests <required family>) that "
            "belongs to the wrong language/ecosystem for this goal. Remove that exact planned file "
            "if it is not genuinely required, or replace it with the equivalent artifact in the "
            "authoritative stack the error names - never introduce a second language/ecosystem "
            "alongside the one the goal actually requires.\n"
        )
    if "INTEGRATION_RELATIONSHIP_UNKNOWN_SUBTASK" in reason_codes:
        targeted_correction += (
            "- For each integration_relationships entry the errors name as referencing an unknown "
            "producer or consumer subtask id: replace that id with a real subtask id from this "
            "plan's own subtasks, or remove the relationship entirely if it no longer applies. Do "
            "not invent a new subtask solely to satisfy a stale relationship id.\n"
        )
    if "PLANNED_ARTIFACT_PROVIDER_NOT_UPSTREAM" in reason_codes:
        targeted_correction += (
            "- For each integration_relationships entry the errors name as having a consumer that "
            "can execute before its provider(s) (the specific relationship id, consumer id, and "
            "missing-upstream provider id(s) are given in the error text): add the named provider "
            "subtask id(s) to the consumer subtask's own depends_on, so the provider is guaranteed "
            "to run first. Do not remove the relationship or change its participating_artifacts.\n"
        )
    if "PLANNED_ARTIFACT_PREREQUISITE_INVALID" in reason_codes:
        targeted_correction += (
            "- For each planned file the errors name as declaring a blank requires_capabilities "
            "entry: remove that blank entry. If the file genuinely has a real prerequisite, name "
            "the actual capability string instead of leaving it empty.\n"
        )
    if "DUPLICATE_SUBTASK_ID" in reason_codes:
        targeted_correction += (
            "- The errors name subtask id(s) used more than once. Give each subtask a distinct id; "
            "do not merge or delete a subtask's own real content just to resolve the collision - "
            "rename one of the colliding ids and update every depends_on/requires reference to it "
            "accordingly.\n"
        )
    if "SUBTASK_DEPENDS_ON_UNKNOWN_ID" in reason_codes:
        targeted_correction += (
            "- The errors name a subtask whose depends_on references an id that does not exist in "
            "this plan (the exact unknown id is given in the error text). Replace it with the real "
            "id of the subtask it was meant to reference, or remove the reference if no such "
            "subtask is actually needed.\n"
        )
    if "PLAN_REQUIREMENT_ID_UNKNOWN" in reason_codes:
        # PRD-020: requirement ids are Kriya's, derived from the user's goal.
        targeted_correction += (
            "- The errors name a subtask whose requirement_ids contains an id that is not one of "
            "the user's original requirements (the exact id is given in the error text). Use only "
            "the REQ ids listed under Original Requirements in the request; remove any other id. "
            "Do not invent, renumber or split a requirement.\n"
        )
    if "SUBTASK_DEPENDENCY_CYCLE" in reason_codes:
        targeted_correction += (
            "- The subtask dependency graph (depends_on edges) contains a cycle. Break it by "
            "removing or reversing whichever depends_on edge does not reflect genuine execution "
            "order - two subtasks that each require the other's output cannot both run; split the "
            "shared work so one genuinely completes before the other starts.\n"
        )
    must_fix_section = ""
    if must_preserve:
        must_fix_section = (
            "\nMUST PRESERVE (already correct in the previous draft above - do not undo any of "
            "these while fixing the items below; a corrected plan that changes one of these back "
            "is itself a regression):\n"
            + "\n".join(f"- {item}" for item in must_preserve)
            + "\n\nMUST FIX (still wrong in the previous draft):\n"
        )
    return (
        "Repair the previous structured engineering plan. This is PLAN_REPAIR, not implementation.\n"
        "Return only one complete JSON object and nothing else. Do not use Markdown or code fences.\n\n"
        f"Original request:\n{goal}\n\n"
        f"Deterministic reason codes: {json.dumps(reason_codes)}\n"
        + must_fix_section
        + "Deterministic validation errors:\n"
        + "\n".join(f"- {error}" for error in errors)
        + "\n\nCorrection rules:\n"
        "- Return a complete corrected plan, preserving every valid subtask and dependency.\n"
        "- Correct only invalid plan structure; do not broaden scope or invent modules/entrypoints.\n"
        "- Declare depends_on for every stage that consumes files, configuration, contracts, or "
        "build setup produced by another stage.\n"
        "- Preserve or add goal-derived global_invariants (each with a stable id and a statement) "
        "and per-subtask relevant_global_invariant_ids referencing those ids, plus stable "
        "provides/requires metadata; every requires string must exactly equal one provides string "
        "from exactly one declared dependency, and every relevant_global_invariant_ids entry must "
        "exactly equal one global_invariants id - never restate the statement text as the id.\n"
        "- Every verification item must be an object with type, description, verifier_kind, and "
        "requires_runtime_execution; use type=tool/tool_name=compile/verifier_kind=compile for compilation, "
        "type=tool/tool_name=test/verifier_kind=test for tests, and type=judgment without tool_name only for "
        "a genuinely non-deterministic semantic check; never emit a verification string.\n"
        "- For verification of observable application behavior that requires executing the built "
        "application - output, exit behavior, processing sample input, runtime side effects, etc. - "
        "set verifier_kind=application_runtime and requires_runtime_execution=true TOGETHER on that "
        "explicit application verifier, and false on build-only checks. Do not use verifier_kind="
        "judgment for behavior that can only be established by executing the application; a "
        "judgment-only requirement with no tool_name and requires_runtime_execution=false has no way "
        "to ever be confirmed and will be rejected.\n"
        "- Map each acceptance criterion only to a stage capable of directly proving it; runtime "
        "output criteria belong on the runnable entrypoint stage.\n"
        "- Every execution_role=implementation subtask MUST declare every file it may modify in "
        "planned_files.\n"
        "- A non-editing build/test/run/output check is its own subtask with "
        "execution_role=verification, planned_files=[], and at least one concrete verification "
        "entry - never a MODEL subtask with no files and no verification entry, and never a fake "
        "planned_files path invented just to pass validation.\n"
        + targeted_correction
        # PLANNER-ROBUST-001 P6 (2026-09-19): replaces the obsolete claim
        # "authoritative enforce mode has no policy-mediated TOOL router
        # yet" - false since TOOL-001 closed (TOOL-tagged subtasks DO
        # execute, governed, in enforce mode). This line is ALWAYS present
        # (outside every reason-code conditional above, like the line it
        # replaces) - kept concise and pointing at the same contract the
        # Planner's own system prompt states in full, never reproducing
        # PlannerAgent.system_prompt's own worked examples here.
        + "- A TOOL-execution-method subtask is valid only when it is directly dispatched to a "
        "real, currently registered Kriya tool named in its own top-level tool_name (never "
        "invented) - never confuse this with a verification[] entry's own tool_name, a separate "
        "field naming a deterministic check. Ordinary verification-only work should normally use "
        "execution_method=model with a concrete verification[] entry instead.\n"
        "- Output the complete corrected JSON object, not a patch, explanation, or Markdown plan.\n\n"
        f"Previous Planner response (repair attempt {repair_attempt}):\n"
        + previous_plan_text[-20000:]
    )
