"""Phase B: map every non-closed registry row to exactly one class and, where it is a confirmed gap, to one root issue.

Reads handover/BACKLOG_REGISTRY.csv (read-only) and writes docs/architecture/REGISTRY-ROOT-MAP.md.
Fails unless every non-closed row is mapped exactly once and every mapping names a known class and root.

Usage: python3 registry_root_map.py <repo_root>
"""
import csv
import sys
from pathlib import Path

ROOTS = {
    "R-01": "SEMANTIC-CONTRACT-AUTHORITY",
    "R-02": "RESULT-TRUTH-AND-EXECUTOR-CONVERGENCE",
    "R-03": "APPROVAL-SUBJECT-AUTHORITY",
    "R-04": "MUTATION-TRANSACTION-AUTHORITY",
    "R-05": "PROJECT-STATE-NAMESPACE-AUTHORITY",
    "R-06": "KNOWLEDGE-AUTHORITY",
    "R-07": "RETRY-BLOCKER-DURABILITY-AUTHORITY",
    "R-08": "RUN-RESOURCE-BUDGET-AUTHORITY",
    "R-09": "RUNTIME-IDENTITY-AND-QUALIFICATION-AUTHORITY",
    "R-10": "PROMPT-TRUST-PROVENANCE-AUTHORITY",
    "R-11": "EVIDENCE-AND-DIAGNOSTIC-TRUTH",
    "R-12": "CERTIFICATION-HARNESS-DETERMINISM",
    "R-13": "VERIFICATION-ENVIRONMENT-VERDICT",
}
CLASSES = ("ROOT_GAP", "STALE_BOOKKEEPING", "DUPLICATE_SUPERSEDED", "DISPROVED", "INTENTIONAL_CONTRACT", "FUTURE_FEATURE",
           "PHASE_D_BLOCKER", "PHASE_D_ITEM_NON_BLOCKING")

# class, root ("-" when the class has none), one-line reason. Grouped by the Phase-A slice that examined the row.
M = {
    # ---- A1
    "RETRY-PROGRESS-SAME-BLOCKER-001": ("DUPLICATE_SUPERSEDED", "R-07", "superseded by RETRY-BLOCKER-IDENTITY-AUTHORITY-001 (GAP-A1-01)"),
    "RETRY-BLOCKER-IDENTITY-AUTHORITY-001": ("ROOT_GAP", "R-07", "GAP-A1-01: no authority decides blocker continuity"),
    "RETRY-STATE-RESUME-CONTINUITY-001": ("ROOT_GAP", "R-07", "GAP-A1-02, GAP-A1-09: retry and recovery state not durable"),
    "OPEN-FINDINGS-ORDER-RESUME-001": ("DUPLICATE_SUPERSEDED", "R-07", "both halves covered by GAP-A1-01 and GAP-A1-02"),
    "REPAIR-EQUIVALENCE-001": ("FUTURE_FEATURE", "-", "repeated-candidate detection exists; equivalence beyond bytes is future work"),
    "SEMANTIC-SAFETY-REPAIR-CONTINUITY-001": ("ROOT_GAP", "R-07", "GAP-A1-07: finding replacement and scoped-budget replenishment share one predicate"),
    "ATTEMPT-ATOMIC-CANDIDATE-DISCARDS-VALID-EDIT-001": ("INTENTIONAL_CONTRACT", "-", "atomic candidate per attempt is a stated contract"),
    "TRIAGE-ON-ORCHESTRATOR-NOTICE-001": ("ROOT_GAP", "R-07", "a non-candidate notice enters failure routing (AUTH-A1-01)"),
    "RETRY-REFERENCE-CONTEXT-DEDUP-001": ("FUTURE_FEATURE", "-", "prompt economy; no decision effect"),
    "METRICS-RETRIES-TRIGGERED-COUNTS-TERMINAL-ATTEMPT-001": ("ROOT_GAP", "R-11", "a recorded count does not mean what its name says"),
    "DEMO-TIME-BUDGET-001": ("INTENTIONAL_CONTRACT", "-", "a configuration choice; the authority class of the value is R-08"),
    "HITL-RESUME-DEFERRED-GATES-001": ("ROOT_GAP", "R-07", "resume of a unit whose executing gates were held"),
    "LEAK-SIGKILL-RECOVERY-GAP-001": ("FUTURE_FEATURE", "-", "registry's own W3-8 disposition"),
    "GUI-RECOVERY-ACTION-001": ("FUTURE_FEATURE", "-", "kernel recovery exists; desktop action is future"),
    "RECOVERY-UPSTREAM-OWNER-ROUTING-001": ("DUPLICATE_SUPERSEDED", "R-07", "resolved as superseded in A2"),
    "RECOVERY-MAY-CHANGE-ANSWERED-NO-CHANGE-REFUSED-IN-UNIT-001": ("ROOT_GAP", "R-07", "recovery outcome vocabulary differs between enforce loop and unit"),
    "ENFORCE-UPSTREAM-WORK-PRESERVATION-001": ("ROOT_GAP", "R-07", "GAP-A1-09: accepted unit work not durable before the terminal commit"),
    "CROSS-UNIT-PRODUCER-RECOVERY-CAPABILITY-001": ("FUTURE_FEATURE", "-", "owner disposition: capability, not defect"),
    "GUI-TRACE-ATTEMPTS-SEMANTICS-001": ("ROOT_GAP", "R-11", "runs.attempts is not the number of attempts"),
    "KERNEL-RESULT-RUNID-DIVERGES-001": ("ROOT_GAP", "R-11", "one unit invocation has several identifiers; no record binds them"),
    "GUI-EXEC-STOP-001": ("FUTURE_FEATURE", "-", "owner decision 2026-10-10"),
    # ---- A2
    "GENERATED-VERIFIER-CONTRACT-AUTHORITY-001": ("ROOT_GAP", "R-01", "GAP-A2-01: no semantic contract before the verifier"),
    "WAVE5-O2-GENERATED-TEST-WEAKENING-001": ("ROOT_GAP", "R-01", "GAP-A2-02: verifier identification fails open when unmapped"),
    "ATTRIBUTION-KIND-AND-AUTHORITY-LABEL-DIAGNOSTIC-TRUTH-001": ("ROOT_GAP", "R-11", "a diagnostic label is rewritten after the decision"),
    "INFRA-STOP-ATTRIBUTION-SOURCE-DEFECT-LABEL-001": ("ROOT_GAP", "R-11", "an infrastructure stop carries a source-defect label"),
    "REQUIREMENT-BEHAVIOR-CLAIM-NO-DETERMINISTIC-CLOSER-001": ("ROOT_GAP", "R-01", "GAP-A2-01 seen from requirement closure"),
    "REQUIREMENT-VERIFIER-ENFORCE-PATH-NO-PROJECT-BEFORE-001": ("ROOT_GAP", "R-02", "GAP-A4-01: where requirements are verified differs per executor"),
    "GOAL-EXAMPLE-COMPILER-COVERAGE-001": ("ROOT_GAP", "R-01", "one of the few ways to state semantics is narrow"),
    "GREENFIELD-DETERMINISTIC-OUTPUT-CLOSER-001": ("STALE_BOOKKEEPING", "-", "the capability exists at the baseline"),
    "FIX-UNIT-BEHAVIOURAL-GATE-001": ("ROOT_GAP", "R-01", "a fix unit gated by compile only: no behavioural contract to verify against"),
    "PROJECT-OWNED-DEPENDENCY-DEFECT-CLASSIFICATION-001": ("FUTURE_FEATURE", "-", "typed no-verdict today; an ownership classifier is future"),
    "SUREFIRE-RENDERER-FAILURE-DETAIL-001": ("ROOT_GAP", "R-11", "diagnostic detail lost in rendering"),
    "SUREFIRE-STABILITY-ENVELOPE-001": ("FUTURE_FEATURE", "-", "no Surefire stability path"),
    "WAVE5-PRD022-DUPLICATE-OWNER-NAMED-SYMBOL-001": ("FUTURE_FEATURE", "-", "ownership grounding at planning; not an authority gap"),
    # ---- A3
    "REQUIREMENT-CLOSURE-UNVERIFIED-RECORDED-AS-SUCCESS-001": ("ROOT_GAP", "R-02", "GAP-A3-01: success does not mean requirements verified"),
    "TERMINAL-REQUIREMENT-FAILURE-NO-REPAIR-001": ("ROOT_GAP", "R-02", "GAP-A3-02: no route from a terminal gap to an owning unit"),
    "MILESTONE-UNIT-REVIEW-REFUSAL-FOLD-UNMEASURED-001": ("ROOT_GAP", "R-02", "GAP-A3-03: final review decides status on two executors only"),
    "KERNEL-APPROVAL-RECORD-NO-CANDIDATE-DIGEST-001": ("ROOT_GAP", "R-03", "GAP-A3-04: approval not bound to the committed bytes"),
    "GUI-REVIEW-APPLY-BINDING-001": ("ROOT_GAP", "R-03", "GAP-A3-04 (desktop side)"),
    "GUI-REVIEW-LOCAL-TRUST-001": ("DISPROVED", "-", "stale as written (DOC-A3-04)"),
    "HITL-OTHER-APPROVAL-TRIGGERS-DEFERRAL-001": ("ROOT_GAP", "R-03", "which approvals hold execution is part of the approval-subject contract"),
    "EVIDENCE-HELD-GATES-001": ("ROOT_GAP", "R-11", "GAP-A3-07: closing evidence written before held gates run"),
    "KERNEL-FINAL-REVIEW-REUSES-PRE-BUILD-REVIEW-001": ("ROOT_GAP", "R-02", "GAP-A3-06: review text can describe another state"),
    "INTEGRATION-PASS-REQUIREMENT-POLICY-001": ("ROOT_GAP", "R-02", "GAP-A3-01 on the milestone executor"),
    "MANAGED-SERVICE-APPROVAL-001": ("FUTURE_FEATURE", "-", "fail-closed constant today"),
    "MANAGED-SERVICE-ENV-STATE-BINDING-001": ("ROOT_GAP", "R-13", "an environment fact read where a verdict is formed"),
    "OPTIONAL-RUNTIME-GRADED-FAILURE-POLICY-001": ("ROOT_GAP", "R-01", "may a model-inferred optional check fail a run: verification authority of model text"),
    "RUNTIME-LINE-EXPECTED-NONZERO-001": ("STALE_BOOKKEEPING", "-", "fixed at the kernel projection (A6 P6)"),
    "GREENFIELD-RUNTIME-VERIFICATION-DROPPED-001": ("STALE_BOOKKEEPING", "-", "the stated defect is fixed; the class remains as GAP-A4-05 under R-01"),
    "RUNTIME-GATE-VENV-LACKS-PROJECT-TEST-DEPENDENCY-001": ("PHASE_D_ITEM_NON_BLOCKING", "R-13", "provisional: ROOT_GAP under R-13"),
    "WAVE8-PY-RUNTIME-ONLY-VENV-BOOTSTRAP-001": ("ROOT_GAP", "R-13", "environment creation failure must stay a typed environment verdict"),
    "WAVE8-DOCKER-REFUSAL-BECOMES-FALSE-VERDICT-001": ("PHASE_D_BLOCKER", "R-13", "blocker for the verification-truth statement"),
    "ENFORCE-CLOSURE-STABILITY-CACHE-REUSE-001": ("FUTURE_FEATURE", "-", "performance of the terminal gate"),
    "REVIEW-DIFF-SIDECAR-RETENTION-001": ("ROOT_GAP", "R-11", "a record with no retention owner"),
    "GATE-RESULT-PURPOSE-ABSENT-ON-TESTS-GATE-001": ("ROOT_GAP", "R-11", "gate purpose declared by 4 of about 12 orchestrating entries"),
    "REVIEWER-OUTPUT-CAP-RUNAWAY-LATENCY-001": ("DUPLICATE_SUPERSEDED", "-", "superseded by the closed REVIEWER-RUNAWAY-UNBOUNDED-OUTPUT-001"),
    "KERNEL-STAGING-WORKSPACE-BASE-ESCAPES-001": ("ROOT_GAP", "R-04", "fail-closed but untyped refusal at the guarded commit"),
    "RUN-ATTEMPT-GATE-EXTRACTION-001": ("FUTURE_FEATURE", "-", "decomposition"),
    "DESKTOP-LOOKUP-QUERY-APPROVAL-001": ("FUTURE_FEATURE", "-", "a fifth review kind; depends on R-10"),
    "TECHNICAL-VERIFICATION-BARE-ROWS-001": ("PHASE_D_ITEM_NON_BLOCKING", "R-11", "provisional: presentation only, closes on the owner's observation"),
    "APPLICATION-OUTPUT-PROGRAM-STREAM-001": ("FUTURE_FEATURE", "-", "capability"),
    "STATIC-RULE-ID-IN-EVIDENCE-001": ("FUTURE_FEATURE", "-", "evidence detail"),
    # ---- A4
    "ENFORCE-EXECUTE-PLAN-CONVERGENCE-001": ("ROOT_GAP", "R-02", "GAP-A4-01: seven result-bearing truths differ per executor"),
    "PLAN-PRESERVED-REFERENCE-SOURCE-ATTRIBUTION-001": ("INTENTIONAL_CONTRACT", "-", "design limit"),
    "PLAN-FORM-NORMALISATION-001": ("ROOT_GAP", "R-02", "plan validity is established differently per executor"),
    "PLAN-EXTERNAL-PACKAGE-CAPABILITY-001": ("FUTURE_FEATURE", "-", "model behaviour is the primary cause"),
    "ENFORCE-FALSE-PREMISE-MUTATION-GUARD-001": ("ROOT_GAP", "R-02", "a guard present on one executor only"),
    "KNOWLEDGEGUARD-SKILL-IDENTITY-001": ("ROOT_GAP", "R-06", "GAP-A4-03: a folder name closes the knowledge gate"),
    "SKILL-RELEVANCE-CONTEXT-SCOPE-001": ("ROOT_GAP", "R-06", "GAP-A4-02: no skill relevance authority below the goal"),
    "SKILL-WORKSPACE-PERSISTENT-VERIFICATION-ATTESTATION-001": ("FUTURE_FEATURE", "-", "a workspace skill cannot attest itself, by design"),
    "SKILL-RULE-PROVENANCE-PROJECT-SUPPLIED-001": ("ROOT_GAP", "R-06", "a workspace rule with no provenance renders as trusted"),
    "SKILL-QPID-RULE-DOCTOR-ADVICE-STALE-001": ("ROOT_GAP", "R-06", "a shipped skill states a stale fact"),
    "GOAL-STATED-JAVA-VERSION-TOOLCHAIN-AUTHORITY-001": ("FUTURE_FEATURE", "-", "owner policy; needs R-01's contract to carry a toolchain requirement"),
    "JAVA-DEVELOPER-WRONG-PACKAGE-PATH-001": ("FUTURE_FEATURE", "-", "model behaviour; Kriya failed closed"),
    "LOCALIZATION-DEVELOPER-PATH-OVERLOAD-001": ("FUTURE_FEATURE", "-", "no measured loss"),
    "GUI-SKILLS-BUILD-001": ("FUTURE_FEATURE", "-", "blocked by R-04 and R-06"),
    "GUI-SKILLS-MUTATION-001": ("FUTURE_FEATURE", "-", "blocked by R-04 and R-06"),
    "KERNEL-ATTEMPT-CONTEXT-PLACEMENT-001": ("INTENTIONAL_CONTRACT", "-", "module placement"),
    "CLI-RESUME-HINT-DESIGN-STAGE-STOP-TEXT-001": ("ROOT_GAP", "R-11", "a message states something the run did not do"),
    "LSP-GROUNDING-CONSUMER-CHAIN-DEAD-001": ("FUTURE_FEATURE", "-", "typed refusal today"),
    # ---- A5
    "DESKTOP-PROJECT-RETRIEVAL-STATE-ISOLATION-001": ("ROOT_GAP", "R-05", "GAP-A5-01"),
    "HISTORY-STATE-DIR-PER-PROJECT-001": ("ROOT_GAP", "R-05", "run history has no project owner"),
    "HISTORY-SNAPSHOT-STALE-ON-REOPEN-001": ("ROOT_GAP", "R-05", "desktop history ownership"),
    "TOOL-RUNPHASE-PRE-EFFECT-DENIAL-TERMINAL-001": ("INTENTIONAL_CONTRACT", "-", "owner ruling"),
    "GUI-TOOLS-HEALTH-001": ("FUTURE_FEATURE", "-", "bounded health read"),
    "DIRECTORY-NAME-PRUNING-P3-SIBLING-SITES-001": ("ROOT_GAP", "R-05", "a second definition of project membership at index and discovery sites"),
    "GIT-ICASE-PATHSPECS-UNQUALIFIED-001": ("INTENTIONAL_CONTRACT", "-", "recorded as unknown and stripped; a Linux-host qualification item"),
    "SEC009-AGENT-ROLE-REASON-TEXT-001": ("ROOT_GAP", "R-11", "a refusal names the wrong reason"),
    "CLI-ADMISSION-REFUSAL-TEXT-MIDRUN-AUTHORITY-UNAVAILABLE-001": ("ROOT_GAP", "R-11", "a refusal sentence wrong for the mid-run case"),
    "PLAT-WINDOWS-POLICY-PARITY-001": ("FUTURE_FEATURE", "-", "platform program"),
    "PLAT-WINDOWS-FS-GIT-001": ("FUTURE_FEATURE", "-", "platform program"),
    "PLAT-WINDOWS-PROVIDERS-001": ("FUTURE_FEATURE", "-", "platform program"),
    "PLAT-PORTABILITY-P3-001": ("FUTURE_FEATURE", "-", "platform program"),
    "GUI-EXEC-GIT-SHIM-001": ("INTENTIONAL_CONTRACT", "-", "closed child environment"),
    "GUI-EXEC-TOOLCHAIN-PATH-001": ("INTENTIONAL_CONTRACT", "-", "closed child environment"),
    "GUI-LAUNCH-RETENTION-001": ("ROOT_GAP", "R-11", "a record with no retention owner"),
    "GUI-RUN-ATTRIBUTION-001": ("ROOT_GAP", "R-05", "the history row has no workspace"),
    "FINDER-ALIAS-BLIND-001": ("ROOT_GAP", "R-12", "an architecture test misses aliased imports"),
    "DOCKER-BIND-SOURCE-REFUSED-AFTER-PATHNAME-RECREATION-001": ("ROOT_GAP", "R-13", "environment refusal; product fail-closed; monitored"),
    "WAVE8-DOCKER-VIEW-CLEANUP-RACE-001": ("ROOT_GAP", "R-13", "cleanup is not gate truth; monitored"),
    "CERT-OWNER-DEATH-MASK-SCRATCH-001": ("ROOT_GAP", "R-13", "environment residue; not traced"),
    "PRODUCT-CANONICAL-SPELLING-LISTING-001": ("FUTURE_FEATURE", "-", "performance"),
    "PRODUCT-GIT-SUBPROCESS-COST-001": ("FUTURE_FEATURE", "-", "performance"),
    "PROCESS-DIAGNOSTIC-PROBE-REAL-AUTHORITY-HOME-001": ("INTENTIONAL_CONTRACT", "-", "process rule"),
    "CERT-HISTORICAL-FIXTURE-EVIDENCE-EXPOSURE-001": ("INTENTIONAL_CONTRACT", "-", "guard exists; published history is an owner disposition"),
    "DEMO-RESET-RM-WORKSPACE-RACES-DOCKER-RELEASE-001": ("ROOT_GAP", "R-12", "demo script race"),
    # ---- A6
    "TELEMETRY-FALLBACK-ROLE-ATTRIBUTION-001": ("ROOT_GAP", "R-11", "calls of the knowledge channels are unattributed"),
    "GUI-PHASE-OBSERVABILITY-001": ("FUTURE_FEATURE", "-", "owner decision 2026-10-10"),
    "GUI-EVIDENCE-POLL-COST-001": ("INTENTIONAL_CONTRACT", "-", "per-read verification"),
    "GUI-EVIDENCE-LARGE-RUN-LOAD-001": ("INTENTIONAL_CONTRACT", "-", "per-read verification"),
    "GUI-M1-STORED-PROMPT-001": ("FUTURE_FEATURE", "-", "legacy rows"),
    "GUI-RUN-REPORT-001": ("FUTURE_FEATURE", "-", "v1.2; must state outcome from the RunRecord (R-02)"),
    "GUI-KRIYA-LOG-NOT-PERSISTED-001": ("DISPROVED", "-", "the file exists in the log directory (measured)"),
    "LEAK-CHECK-SHARED-LINE-FALSE-POSITIVE-001": ("ROOT_GAP", "R-11", "a diagnostic tool reports hits without provenance"),
    "GUI-APPEARANCE-001": ("FUTURE_FEATURE", "-", "presentation"),
    "GUI-NOTIFICATION-VISUAL-001": ("INTENTIONAL_CONTRACT", "-", "limit of test automation"),
    "GUI-NATIVE-MEMORY-DRIFT-001": ("FUTURE_FEATURE", "-", "needs the two-hour soak; no architecture decision depends on it"),
    "GUI-REASON-CODES-IN-ERROR-STATES-001": ("ROOT_GAP", "R-02", "result and refusal wording"),
    "TEST-RESOURCEWARNING-HYGIENE-001": ("ROOT_GAP", "R-12", "harness hygiene"),
    "TEST-DEADLINE-TIMING-001": ("ROOT_GAP", "R-12", "timing race under load"),
    "TEST-LAUNCH-APPROVAL-EXPIRY-TIMING-001": ("ROOT_GAP", "R-12", "timing race under load"),
    "KERNEL-GOLDEN-HARNESS-VOLATILITY-001": ("ROOT_GAP", "R-12", "goldens depend on the checkout (also R-05) and on an unnormalized field"),
    "KERNEL-GOLDEN-DATE-DEPENDENT-001": ("STALE_BOOKKEEPING", "-", "fixed at the baseline"),
    "KERNEL-GOLDEN-RUNID-PATH-NORMALIZATION-001": ("ROOT_GAP", "R-12", "normalizer hazard in mutation sandboxes"),
    "KERNEL-RUNTIME-TRACE-PATH-LENGTH-DEPENDENT-001": ("ROOT_GAP", "R-12", "path-length dependent golden"),
    "KERNEL-TESTS-CHECKOUT-MEMORY-MORE-FILES-001": ("ROOT_GAP", "R-12", "tests read the checkout's memory (test side of R-05)"),
    "DEMO-V2-FIXED-STUB-PORT-PARALLEL-001": ("ROOT_GAP", "R-12", "fixed machine-global port"),
    "TOOLS-COMMAND-TEST-NONHERMETIC-CWD-001": ("STALE_BOOKKEEPING", "-", "fixed at the baseline"),
    "TEST-SLICE-A-OCI-STALE-NESTED-COMMAND-STRING-001": ("ROOT_GAP", "R-12", "stale text in a test"),
    "UI-SCALE-TEST-TIMEOUT-001": ("STALE_BOOKKEEPING", "-", "fixed at the baseline"),
    "WAVE8-S7-PYTHONPATH-INHERITANCE-001": ("STALE_BOOKKEEPING", "-", "fixed at the baseline"),
    "WAVE8-TRACKED-BYTECODE-001": ("ROOT_GAP", "R-12", "tracked bytecode dirties the tree during a run"),
    "CERT-DEMO-V2-IDLE-STUB-XDIST-001": ("ROOT_GAP", "R-12", "xdist-only failure"),
    "CERT-CI-PLAIN-JOBS-LOCKFILE-GROUP-001": ("ROOT_GAP", "R-12", "hosted timing bound"),
    "TEST-BASELINE-THREE-STALE-FAILURES-AT-132884B-001": ("STALE_BOOKKEEPING", "-", "green in the full suite on 82436eb"),
    "TEST-S7-H-LEDGER-STALE-CONTROL-REFERENCE-001": ("STALE_BOOKKEEPING", "-", "fixed at the baseline"),
    "TEST-REPAIR-003-F1-RESIDUAL-VENV-CREATION-COUNT-001": ("STALE_BOOKKEEPING", "-", "green in the full suite on 82436eb"),
    "TEST-CERT-TMP-GROWTH-TWO-STALE-ALLOWLISTS-001": ("STALE_BOOKKEEPING", "-", "green in the full suite on 82436eb"),
    "TEST-KERNEL-GOLDEN-ORDER-DEPENDENCE-IN-AD-HOC-MIX-001": ("ROOT_GAP", "R-12", "order dependence in one file mix"),
    "TEST-S7-H-LEDGER-OI142-CONTROL-MISSING-001": ("DUPLICATE_SUPERSEDED", "-", "duplicate of TEST-S7-H-LEDGER-STALE-CONTROL-REFERENCE-001"),
    "TEST-CERTIFICATION-WALL-CLOCK-001": ("FUTURE_FEATURE", "-", "owner-ruled optimisation; relates to R-12"),
}


def main() -> int:
    root = Path(sys.argv[1])
    rows = [r for r in csv.DictReader((root / "handover/BACKLOG_REGISTRY.csv").open()) if not r["status"].upper().startswith("CLOSED")]
    ids = [r["id"] for r in rows]
    problems = [f"unmapped: {i}" for i in ids if i not in M] + [f"not in registry: {i}" for i in M if i not in ids]
    for i, (cls, rid, _why) in M.items():
        if cls not in CLASSES:
            problems.append(f"{i}: unknown class {cls}")
        if rid != "-" and rid not in ROOTS:
            problems.append(f"{i}: unknown root {rid}")
        if cls in ("ROOT_GAP", "PHASE_D_BLOCKER", "PHASE_D_ITEM_NON_BLOCKING") and rid == "-":
            problems.append(f"{i}: class {cls} needs a root")
    if problems:
        print("\n".join(problems))
        return 1
    counts = {c: sum(1 for v in M.values() if v[0] == c) for c in CLASSES}
    per_root = {r: sorted(i for i, v in M.items() if v[1] == r) for r in ROOTS}
    out = ["# Registry rows by class and root issue (Phase B)", "",
           "Generated by `evidence/b/registry_root_map.py` from `handover/BACKLOG_REGISTRY.csv` at the baseline (read-only).",
           "Every non-closed registry row appears exactly once. The per-row evidence is in `REGISTRY-COVERAGE.md`; the root",
           "issues are in `06-GAP-AUTHORITY-AUDIT.md`. A class written here is a proposal for the owner's Phase-D review.", "",
           f"- Rows: **{len(ids)}**. " + ", ".join(f"{c} {n}" for c, n in counts.items()) + ".", "",
           "## Rows per root issue", "", "| Root | Name | Rows mapped (all classes) |", "|---|---|---|"]
    for rid, name in ROOTS.items():
        out.append(f"| {rid} | {name} | {len(per_root[rid])}: " + ", ".join(f"`{i}`" for i in per_root[rid]) + " |")
    out += ["", "## Every row", "", "| # | Row ID | Registry priority / status | Class | Root | Reason |", "|---|---|---|---|---|---|"]
    for n, r in enumerate(rows, 1):
        cls, rid, why = M[r["id"]]
        status = " ".join(r["status"].split())[:30]
        out.append(f"| {n} | `{r['id']}` | {r['priority']} / {status} | {cls} | {rid} | {why} |")
    out.append("")
    (root / "docs/architecture/REGISTRY-ROOT-MAP.md").write_text("\n".join(out))
    print(f"rows={len(ids)} " + " ".join(f"{c}={n}" for c, n in counts.items()))
    print(" ".join(f"{r}={len(v)}" for r, v in per_root.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
