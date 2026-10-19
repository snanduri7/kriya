import collections
import json

rows = []
for b in ("gui", "open", "def1", "def2"):
    for x in json.load(open(f"out_{b}.json")):
        x["_batch"] = b
        rows.append(x)
assert len(rows) == 76 and len({r["id"] for r in rows}) == 76

# ---- orchestrator overrides: id -> (final_class, how_verified, note)
V_PROBE, V_CODE, V_EVID = "VERIFIED_BY_PROBE", "VERIFIED_BY_CODE_READ", "EVIDENCE_READ"
OV = {
    "GUI-REVIEW-LOCAL-TRUST-001": (
        "A",
        V_CODE,
        "OWNER DECISION 2026-10-19: candidate/repository/workspace content is NOT inside the human-decision trust boundary; authoritative decision store outside the workspace bound to run/gate/candidate identity+digest (existing candidate identity); no second approval authority",
    ),
    "GUI-NATIVE-MEMORY-DRIFT-001": (
        "A",
        V_CODE,
        "OWNER DECISION 2026-10-19: do not run the soak now; HARNESS GAP is Class A (ui/standalone/src/main/soak.ts has a 550 MB pass flag but no pass/fail threshold on the growth slope); the single final soak against the final candidate decides whether product drift exists (then D or A)",
    ),
    "RECOVERY-UPSTREAM-OWNER-ROUTING-001": (
        "A",
        V_CODE,
        "OWNER DECISION 2026-10-19: later deterministic evidence may reopen the actual upstream owner (typed, bounded, recorded, immutable history, budget limits, no A->B->C->A loop); H5 does not own it",
    ),
    "TERMINAL-REQUIREMENT-FAILURE-NO-REPAIR-001": (
        "A",
        V_CODE,
        "OWNER DECISION 2026-10-19: terminal failure final for the current candidate, deterministic evidence may enter bounded repair of the owning unit; sealed premises need new evidence + the authority path",
    ),
    "SUREFIRE-RENDERER-FAILURE-DETAIL-001": (
        "A",
        V_PROBE,
        "RECLASSIFIED C->A by the blind adversarial pass; reproduced: short console id vs fully qualified XML identity never match, so every Maven regression renders 'failure detail unavailable'",
    ),
    "PLAN-FORM-NORMALISATION-001": (
        "A",
        V_CODE,
        "RECLASSIFIED B->A by the blind adversarial pass; verified: validator rejects blank refactor_baseline for the router-chosen REFACTOR kind while the first Planner prompt example is null and never asks for it (planner contract; reconcile with the Scenario-04 planner-contract finding)",
    ),
    "HITL-RESUME-DEFERRED-GATES-001": (
        "A",
        V_PROBE,
        "RECLASSIFIED B->A by the blind adversarial pass; reproduced: in HITL the candidate_gates_passed checkpoint snapshot is ledger revision 2 while its fingerprint is revision 5 (autonomous control matches), so a resume discards the approved verified candidate (fail-closed, P3)",
    ),
    "GUI-TOOLS-HEALTH-001": (
        "B",
        V_EVID,
        "row text stale: tools/service.py exposes env var NAMES and the executable base name; the new capability (bounded health read) stays B",
    ),
    "RUN-ATTEMPT-GATE-EXTRACTION-001": (
        "B",
        V_CODE,
        "gate self-correction writes go through AuthorizedFileWriter with the write scope (gate_orchestrator.py:218-223): one mutation choke point holds; the remaining mutation-free-boundary migration is optional per PRD-031",
    ),
    "HITL-OTHER-APPROVAL-TRIGGERS-DEFERRAL-001": (
        "E",
        V_EVID,
        "CONDITIONAL: add the user-guide statement and a test for sensitive-path/diff-size approval outside human-in-the-loop (closure work)",
    ),
    "HISTORY-STATE-DIR-PER-PROJECT-001": (
        "E",
        V_EVID,
        "CONDITIONAL: add the user-guide sentence that history follows the state directory, not the folder (closure work)",
    ),
    "GUI-RUN-ATTRIBUTION-001": (
        "B",
        V_CODE,
        "RunRecord carries workspace_identity; the legacy unattributed trace rows fail closed; recording on the trace row is new capability",
    ),
    "GUI-EVIDENCE-POLL-COST-001": (
        "B",
        V_EVID,
        "classifier E; reclassified: the verify-every-read contract stays, the incremental-verification speed-up is post-modularization measured optimisation (not a disguised E)",
    ),
    "GUI-EVIDENCE-LARGE-RUN-LOAD-001": ("B", V_EVID, "same as POLL-COST"),
    "GUI-NOTIFICATION-VISUAL-001": (
        "E",
        V_EVID,
        "CONDITIONAL: the pinning test ids must be verified before closing (classifier did not)",
    ),
    "GUI-RUNTIME-COMMAND-APPROVAL-001": (
        "C",
        V_CODE,
        "implemented + tests; closure evidence is a controlled smoke (not a real run): real-run proof joins the Scenario-04 rehearsal checklist",
    ),
    "TEST-LAUNCH-APPROVAL-EXPIRY-TIMING-001": (
        "A",
        V_CODE,
        "OVERRIDE of C: the fix is a wider real-time margin (0.5 -> 5.0 s); owner rule 6 wants a deterministic clock (borderline judgement, SMALL, test-harness)",
    ),
    "ENFORCE-EXECUTE-PLAN-CONVERGENCE-001": (
        "A",
        V_CODE,
        "owner decision: one authoritative execution path; measured duplicate order/lifecycle/dependency/terminal logic in enforce_loop vs execute_plan",
    ),
    "ENFORCE-UPSTREAM-WORK-PRESERVATION-001": (
        "A",
        V_EVID,
        "linked: E premise (enforce stays separate) removed by the convergence decision; resolved inside the convergence slice",
    ),
    "LEAK-SIGKILL-RECOVERY-GAP-001": (
        "A",
        V_CODE,
        "owner decision: ID-based proven-ownership cleanup + residue report; no label-only deletion exists today (MEASURED)",
    ),
    "ENFORCE-FALSE-PREMISE-MUTATION-GUARD-001": (
        "A",
        V_CODE,
        "owner decision: sealed false premise = scoped negative constraint; MEASURED advisory-only",
    ),
    "TEST-RESOURCEWARNING-HYGIENE-001": (
        "A",
        V_CODE,
        "pytest does not error on ResourceWarning and ~70 tests leak: product leak detection is blind (owner section 1 test-harness gap)",
    ),
    "PLAT-WINDOWS-FS-GIT-001": (
        "A",
        V_PROBE,
        "core.autocrlf=true gives a CRLF worktree over an LF workspace (reproduced); Windows-only sub-items split to a B row",
    ),
    "PLAT-WINDOWS-POLICY-PARITY-001": (
        "B",
        V_EVID,
        "parity stays B; the missing fail-closed refusal of the unsupported Windows family is a NEW A row (PLAT-WINDOWS-ENTRY-REFUSAL-NEW)",
    ),
    "INTEGRATION-PASS-REQUIREMENT-POLICY-001": (
        "E",
        V_EVID,
        "CONDITIONAL on adding the missing milestone record-vs-block test and passing it",
    ),
    "OPEN-FINDINGS-ORDER-RESUME-001": (
        "A",
        V_PROBE,
        "OVERRIDE of AMBIGUOUS (judgement, flagged for owner veto): same continuity defect family as SEMANTIC-SAFETY-REPAIR-CONTINUITY (reproduced); resume survival decided inside G4 with FindingState",
    ),
    "TECHNICAL-VERIFICATION-BARE-ROWS-001": (
        "C",
        V_CODE,
        "defensive fix shipped (type.trim() filter in ResultParts.tsx) and pinned by ResultParts.test.tsx; root cause UNKNOWN is recorded; owner re-look at the rebuilt app is a qualification checklist item",
    ),
    "DIFF-APPLIED-BYTES-BINDING-001": (
        "C",
        V_PROBE,
        "commit binding refuses changed bytes/mode (MEASURED by the E14 preflight tamper pins); residual: add an end-to-end mode-only test (R4)",
    ),
    "GOAL-EXAMPLE-LITERAL-BACKSLASH-001": ("A", V_PROBE, 'reproduced: _LITERAL rejects \\t \\n \\" escapes'),
    "SEMANTIC-SAFETY-REPAIR-CONTINUITY-001": (
        "A",
        V_PROBE,
        "reproduced: no_progress_retry replaces unsafe_exception_masking",
    ),
    "SEC-CLI-URL-CREDENTIAL-REDACTION-001": (
        "A",
        V_PROBE,
        "reproduced: all three redactors print user:password@ URLs in clear",
    ),
    "TOOL-SUBTASK-DESKTOP-REVIEW-001": (
        "A",
        V_PROBE,
        "reproduced: a planned filesystem write TOOL subtask to an arbitrary absolute path COMPLETES in human-in-the-loop mode with no review and no policy (FilesystemTool has no authority mechanism; BaseTool.execute applies none; the HITL stop keys only on requires_confirmation). AUTHORITY-BLOCKING",
    ),
    "GREENFIELD-RUNTIME-VERIFICATION-DROPPED-001": (
        "A",
        V_PROBE,
        "reproduced: goal_requires_runtime_behavior is False for 'prints hello world when executed' and for the Java HelloKriya wording; the run ends success with nothing executed",
    ),
    "OWNER-RECOVERY-SELF-CORRECTION-REVIEW-001": (
        "A",
        V_CODE,
        "autonomy.self_correction_loop_enabled is REPOSITORY_SAFE (config/authority.py) so a repo's kriya.yaml can enable it; no HITL guard in owner_recovery (full path TRACED, not reproduced)",
    ),
    "MILESTONE-REPLAY-UNAPPROVED-COMMANDS-001": (
        "A",
        V_EVID,
        "classifier MEASURED: replay runs a persisted command in the real workspace without approval/stdin (CLI-only); reproduce first at implementation",
    ),
    "KNOWLEDGEGUARD-SKILL-IDENTITY-001": (
        "A",
        V_EVID,
        "classifier MEASURED: an empty folder with any name clears the Ignite knowledge gap offline; reproduce first at implementation",
    ),
}
# ---- routing of class A (existing slice or PROPOSED new slice), size, prerequisite
ROUTE = {
    "GUI-REVIEW-LOCAL-TRUST-001": ("S6", "MEDIUM", "R4 candidate identity; O-slices touch the KUP surface"),
    "GUI-NATIVE-MEMORY-DRIFT-001": (
        "T1 (soak threshold harness fix) then final UI qualification",
        "SMALL",
        "final candidate",
    ),
    "RECOVERY-UPSTREAM-OWNER-ROUTING-001": (
        "TR1 (new, ONE terminal-recovery slice after H4 and X1)",
        "LARGE",
        "F1/F4 attribution, H4, X1 (terminal decision converged)",
    ),
    "TERMINAL-REQUIREMENT-FAILURE-NO-REPAIR-001": (
        "TR1 (new, ONE terminal-recovery slice after H4 and X1)",
        "LARGE",
        "F1/F4 attribution, H4, X1 (terminal decision converged)",
    ),
    "SUREFIRE-RENDERER-FAILURE-DETAIL-001": (
        "S14 (new, canonical Maven test identity in validation_baseline)",
        "SMALL",
        "none",
    ),
    "PLAN-FORM-NORMALISATION-001": (
        "S15 (new, Planner contract matches validator for REFACTOR; one fix with the Scenario-04 planner-contract finding)",
        "SMALL",
        "none",
    ),
    "HITL-RESUME-DEFERRED-GATES-001": (
        "S8 (held-gate records: attempt.closed + checkpoint ledger snapshot, one owner and contract)",
        "SMALL",
        "E5 done",
    ),
    "EVIDENCE-HELD-GATES-001": (
        "S8 (held-gate records: attempt.closed + checkpoint ledger snapshot, one owner and contract)",
        "SMALL",
        "E5 done",
    ),
    "MVN-VALIDATE-FAIL-OPEN-NEW": (
        "L1 if L1 is demonstrably the validator owner at its preflight, else a bounded EXECUTION-validator unit",
        "SMALL",
        "none",
    ),
    "EGRESS-POLICY-TYPO-FAILS-OPEN-NEW": (
        "S16 (new, closed vocabulary for security-authority string fields; egress_policy first)",
        "SMALL",
        "none",
    ),
    "APP-RESULT-STDERR-HIDDEN-NEW": (
        "S10 (application-result presentation truth: runtime line + stdout/stderr)",
        "SMALL",
        "none",
    ),
    "BASELINE-JDK-PARITY-NEW": (
        "S17 (new, baseline capture runs under the same JDK override and records the true environment)",
        "SMALL",
        "none",
    ),
    "TOOL-SUBTASK-DESKTOP-REVIEW-001": (
        "S1 (new, EARLY, TOOLBOX authority)",
        "MEDIUM",
        "none; K2 finishes the ToolRegistry delegation",
    ),
    "SEC-CLI-URL-CREDENTIAL-REDACTION-001": ("S2 (new, EARLY, one redaction owner)", "SMALL", "none"),
    "OWNER-RECOVERY-SELF-CORRECTION-REVIEW-001": (
        "S3 (new, EARLY, config authority classification)",
        "SMALL",
        "E11 done",
    ),
    "MILESTONE-REPLAY-UNAPPROVED-COMMANDS-001": (
        "S4 (new, EARLY, replay through the typed runtime-command review)",
        "MEDIUM",
        "C3 done",
    ),
    "PLAT-WINDOWS-ENTRY-REFUSAL-NEW": ("S5 (new, EARLY, platform entry refusal)", "SMALL", "none"),
    "GUI-REVIEW-APPLY-BINDING-001": (
        "R4",
        "SMALL",
        "E10/G2 for the equality check location (G4 owns CandidateIdentity equality)",
    ),
    "GUI-TRACE-ATTEMPTS-SEMANTICS-001": ("R5", "SMALL", "none"),
    "GUI-LAUNCH-RETENTION-001": ("S7 (new, STATE retention)", "SMALL", "D2 done"),
    "REVIEW-DIFF-SIDECAR-RETENTION-001": ("S7 (new, STATE retention)", "SMALL", "D2 done"),
    "RUNTIME-LINE-EXPECTED-NONZERO-001": (
        "S10 (application-result presentation truth: runtime line + stdout/stderr)",
        "SMALL",
        "B2 done",
    ),
    "HISTORY-SNAPSHOT-STALE-ON-REOPEN-001": ("S11 (new, UI session snapshot)", "SMALL", "B2 done"),
    "LEAK-CHECK-SHARED-LINE-FALSE-POSITIVE-001": ("S9 (new, EVIDENCE leak-check provenance)", "SMALL", "D1 done"),
    "GOAL-EXAMPLE-LITERAL-BACKSLASH-001": (
        "S12 (new, example-compiler grammar)",
        "SMALL",
        "none (bump JAVA_EXAMPLE_COMPILER_VERSION)",
    ),
    "GREENFIELD-RUNTIME-VERIFICATION-DROPPED-001": (
        "S13 (new, runtime-obligation detection, generic typed criterion)",
        "MEDIUM",
        "E14 accepted baseline; E3/E4 done",
    ),
    "ATTRIBUTION-SELF-DIAGNOSIS-MENTION-001": ("F4", "MEDIUM", "F1-F3"),
    "ATTEMPT-ATOMIC-CANDIDATE-DISCARDS-VALID-EDIT-001": ("G4", "LARGE", "G1-G3"),
    "SEMANTIC-SAFETY-REPAIR-CONTINUITY-001": ("G4", "SMALL", "G1-G3"),
    "OPEN-FINDINGS-ORDER-RESUME-001": ("G4", "SMALL", "F2 (FindingState)"),
    "GENERATION-BUDGET-COUNTS-HUMAN-WAIT-001": ("H4", "LARGE", "H1-H3; C3 human-wait hook done"),
    "TRIAGE-ON-ORCHESTRATOR-NOTICE-001": ("H4", "SMALL", "H1-H3, F2"),
    "MANAGED-SERVICE-ENV-STATE-BINDING-001": ("L1", "SMALL", "E9 done"),
    "LEAK-SIGKILL-RECOVERY-GAP-001": ("L3 (new, OCI object-ID ownership + residue reconciliation)", "MEDIUM", "L1/L2"),
    "PLAT-WINDOWS-FS-GIT-001": ("M4 (new, git invocation profile on the single runner)", "MEDIUM", "M2, M3"),
    "KNOWLEDGEGUARD-SKILL-IDENTITY-001": ("J3 (new, correction after the J2 move)", "SMALL", "J1, J2"),
    "ENFORCE-EXECUTE-PLAN-CONVERGENCE-001": (
        "X1 (new, Type D, one execution path)",
        "LARGE",
        "E14 resumed, F/G/H corrections",
    ),
    "ENFORCE-UPSTREAM-WORK-PRESERVATION-001": ("X1", "MEDIUM", "X1 design (atomic-plan vs per-unit commit)"),
    "ENFORCE-FALSE-PREMISE-MUTATION-GUARD-001": (
        "X2 (new, Type C, sealed premise as scoped constraint)",
        "MEDIUM",
        "F-series plan validation, G4 repair scope",
    ),
    "TEST-LAUNCH-APPROVAL-EXPIRY-TIMING-001": ("T1 (new, test-harness hygiene before Q1)", "SMALL", "none"),
    "TEST-RESOURCEWARNING-HYGIENE-001": ("T1", "MEDIUM", "none"),
}
final = []
for r in rows:
    cls, how, note = OV.get(r["id"], (r["proposed_class"], V_EVID, ""))
    d = dict(
        id=r["id"],
        batch=r["_batch"],
        registry_status=r["registry_status"],
        priority=r["priority"],
        title=r["title"][:110],
        classifier_class=r["proposed_class"],
        final_class=cls,
        verified=how,
        note=note or r.get("class_reasoning", "")[:160],
        authority_security=r["authority_security_flag"],
    )
    if cls == "A":
        rt = ROUTE.get(r["id"])
        assert rt, r["id"]
        d["closing_slice"], d["size"], d["prerequisite"] = rt
        d["owner"] = r.get("architectural_owner", "")
    final.append(d)
# derived rows from splits
final.append(
    dict(
        id="PLAT-WINDOWS-ENTRY-REFUSAL-NEW",
        batch="def1",
        registry_status="NEW",
        priority="P2",
        title="fail-closed refusal of the unsupported Windows platform family at CLI/KUP entry (split from PLAT-WINDOWS-POLICY-PARITY-001)",
        classifier_class="B",
        final_class="A",
        verified=V_EVID,
        note="owner decision: unsupported-Windows refusal gap = A",
        authority_security=True,
        closing_slice=ROUTE["PLAT-WINDOWS-ENTRY-REFUSAL-NEW"][0],
        size="SMALL",
        prerequisite="none",
        owner="ADAPTER entry / POLICY",
    )
)
final.append(
    dict(
        id="PLAT-WINDOWS-FS-GIT-WINDOWS-ONLY-NEW",
        batch="def1",
        registry_status="NEW",
        priority="P3",
        title="Windows-only sub-items PLAT-022/023/024/027 (split from PLAT-WINDOWS-FS-GIT-001)",
        classifier_class="B",
        final_class="B",
        verified=V_EVID,
        note="Windows parity is a future platform",
        authority_security=False,
    )
)


def derived(i, title, prio, note, owner, ver):
    rt = ROUTE[i]
    final.append(
        dict(
            id=i,
            batch="adv/allow",
            registry_status="NEW",
            priority=prio,
            title=title,
            classifier_class="-",
            final_class="A",
            verified=ver,
            note=note,
            authority_security=True,
            closing_slice=rt[0],
            size=rt[1],
            prerequisite=rt[2],
            owner=owner,
        )
    )


derived(
    "MVN-VALIDATE-FAIL-OPEN-NEW",
    "mvn validate: any unexpected exception returns success=True 'skipped, not confirmed valid' (kriya/tools/validate.py:1728-1731)",
    "P3",
    "fail-open validator; AST scan finds this one only",
    "EXECUTION validator",
    V_CODE,
)
derived(
    "EGRESS-POLICY-TYPO-FAILS-OPEN-NEW",
    "autonomy.egress_policy is a bare str; any value other than local_only (typo, case) is treated as non-local (15 comparison sites)",
    "P2",
    "reproduced: local-only / LOCAL_ONLY / nonsense accepted",
    "CONFIG/POLICY egress",
    V_PROBE,
)
derived(
    "APP-RESULT-STDERR-HIDDEN-NEW",
    "ApplicationResult renders stdout||stderr: a failed run with non-empty stdout hides stderr under 'Output / error' (ResultParts.tsx:42)",
    "P3",
    "TRACED deterministic; no other cockpit surface shows stderr; the only test uses empty stdout",
    "APPLICATION/UI presentation",
    V_CODE,
)
derived(
    "BASELINE-JDK-PARITY-NEW",
    "baseline run built without the JDK override that candidate runs use, yet recorded as the same environment (development_loop.py:271-282 vs :1052; baseline_policy.py:173)",
    "P2",
    "reproduced by the adversarial reviewer probe; I ran it: PRE java_home None vs POST /fake/jdk-17, SAME_ENV True",
    "KERNEL baseline capture",
    V_PROBE,
)
json.dump(final, open("FINAL_CLASSIFICATION.json", "w"), indent=1)
orig = [f for f in final if not f["id"].endswith("-NEW")]  # the 76 registry rows
c = collections.Counter(f["final_class"] for f in orig)
print("original 76 rows:", dict(c))
allc = collections.Counter(f["final_class"] for f in final)
print("with the 2 derived rows (78):", dict(allc))
ver = collections.Counter(f["verified"] for f in orig)
print("verification depth:", dict(ver))
A = [f for f in final if f["final_class"] == "A"]
print("Class A rows:", len(A), "sizes", dict(collections.Counter(f["size"] for f in A)))
by = collections.defaultdict(list)
for f in A:
    by[f["closing_slice"]].append(f["id"])
for k, v in by.items():
    print(f"  {k}: {len(v)}")
print("NEEDS_OWNER_DECISION:", [f["id"] for f in final if f["final_class"] == "NEEDS_OWNER_DECISION"])
print("conditional E:", [f["id"] for f in final if f["final_class"] == "E" and "CONDITIONAL" in f["note"]])
