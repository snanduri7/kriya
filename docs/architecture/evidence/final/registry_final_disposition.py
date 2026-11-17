"""Final reconciliation: one disposition per non-closed row of handover/BACKLOG_REGISTRY.csv. Read-only over the registry.

Usage (from the repository root): python docs/architecture/evidence/final/registry_final_disposition.py [--write]
Prints the table; ``--write`` also writes registry_final_disposition.md beside this script. Exits 1 when a non-closed row has no
disposition (so a new registry row cannot be left out), or when a row named below is closed or absent (so this file cannot go stale
silently).

Dispositions: FUTURE (genuine future feature), INTENTIONAL (intentional contract), OPEN_MEASUREMENT (a real item S1-S8 did not close:
OPEN - MEASUREMENT REQUIRED by the owner's ruling of 2026-11-17, a temporary state with a measurement target, never a terminal
disposition; 13 section 6). Rows closed as FIXED+VERIFIED, DISPROVED or
INTENTIONAL CONTRACT at the reconciliation are CLOSED in the registry, with the evidence on the row.
The class of a row mapped by REGISTRY-ROOT-MAP.md (the Phase-D classification the owner ruled on, 2026-11-15) is taken from that map.
"""
import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
REGISTRY = os.path.join(ROOT, "handover", "BACKLOG_REGISTRY.csv")
ROOT_MAP = os.path.join(ROOT, "docs", "architecture", "REGISTRY-ROOT-MAP.md")

# Rows the reconciliation classified itself (post-map rows, and root-gap rows whose capability is additive).
FUTURE = {
    "GOAL-EXAMPLE-COMPILER-COVERAGE-001": "a wider goal-example grammar is an additive input authority (12 section 5)",
    "AMBIGUITY-ATTENDED-QUESTION-001": "a typed question for an attended run; ambiguity is refused or held open today (S5)",
    "REQUIREMENT-PERSON-VERIFIER-MODALITY-001": "owner ruling 2026-11-16: an operator-sealed person-verifier modality",
    "TERMINAL-REQUIREMENT-FAILURE-NO-REPAIR-001": "an automatic repair episode after a terminal requirement gap",
    "S8-TERMINAL-GAP-REPAIR-NOT-BUILT-001": "the same capability, seen from S8",
    "S8-ALLOWANCE-EVENT-RECORD-001": "per-grant audit events; the ledger is the authority (a reading, flagged)",
    "SKILLS-WHEEL-OPERATOR-PROMOTE-IDENTITY-001": "an operator-owned promotion root for wheel installations (a reading, flagged)",
    "VERIFIER-SRC-LAYOUT-IDENTITY-MAPPING-001": "exact identity mapping so a held test becomes a reviewable subject",
    "MODEL-BINDING-EXPLICIT-IDENTIFIER-001": "needed before a second runtime serves the same model name",
    "LEARNED-KNOWLEDGE-GLOBAL-PROMOTION-001": "a governed global knowledge namespace",
    "SKILLS-LEGACY-INSTALL-SCAFFOLDS-001": "the doctor report; the authority part is fixed (S4)",
    "SKILL-RELEVANCE-CONTEXT-SCOPE-001": "an authored rule-level relevance authority",
}
GROUPS = {
    "A": "C-11 diagnostic and evidence truth (deferred by ruling to after S8; no decision reads any of it)",
    "B": "C-02 remainder: plan validity per executor, the enforce verifier's context",
    "C": "C-07 / C-08 remainder",
    "D": "C-01 remainder",
    "E": "C-05 remainder: history ownership, sibling membership sites",
    "F": "C-04 / C-06 remainder",
    "G": "C-12 harness and test coverage",
    "H": "C-13 environment: needs a real container daemon; measured in the real-scenario phase",
    "I": "owner decision",
}
OPEN_MEASUREMENT = {
    "LEAK-CHECK-SHARED-LINE-FALSE-POSITIVE-001": "A", "SUREFIRE-RENDERER-FAILURE-DETAIL-001": "A", "GUI-LAUNCH-RETENTION-001": "A",
    "REVIEW-DIFF-SIDECAR-RETENTION-001": "A", "GUI-TRACE-ATTEMPTS-SEMANTICS-001": "A", "EVIDENCE-HELD-GATES-001": "A",
    "KERNEL-RESULT-RUNID-DIVERGES-001": "A", "CLI-ADMISSION-REFUSAL-TEXT-MIDRUN-AUTHORITY-UNAVAILABLE-001": "A",
    "CLI-RESUME-HINT-DESIGN-STAGE-STOP-TEXT-001": "A", "TELEMETRY-FALLBACK-ROLE-ATTRIBUTION-001": "A",
    "ATTRIBUTION-KIND-AND-AUTHORITY-LABEL-DIAGNOSTIC-TRUTH-001": "A", "GATE-RESULT-PURPOSE-ABSENT-ON-TESTS-GATE-001": "A",
    "METRICS-RETRIES-TRIGGERED-COUNTS-TERMINAL-ATTEMPT-001": "A", "INFRA-STOP-ATTRIBUTION-SOURCE-DEFECT-LABEL-001": "A",
    "TECHNICAL-VERIFICATION-BARE-ROWS-001": "A", "GUI-REASON-CODES-IN-ERROR-STATES-001": "A",
    "KERNEL-FINAL-REVIEW-REUSES-PRE-BUILD-REVIEW-001": "A", "FINAL-RESIDUAL-C11-CLAUSES-001": "A",
    "ENFORCE-EXECUTE-PLAN-CONVERGENCE-001": "B", "PLAN-FORM-NORMALISATION-001": "B",
    "REQUIREMENT-VERIFIER-ENFORCE-PATH-NO-PROJECT-BEFORE-001": "B",
    "SEMANTIC-SAFETY-REPAIR-CONTINUITY-001": "C", "TRIAGE-ON-ORCHESTRATOR-NOTICE-001": "C",
    "RECOVERY-MAY-CHANGE-ANSWERED-NO-CHANGE-REFUSED-IN-UNIT-001": "C", "HITL-RESUME-DEFERRED-GATES-001": "C",
    "FINAL-RESIDUAL-C07-CLAUSES-001": "C",
    "PLANNER-SEMANTIC-STATE-AND-CRITERION-REFS-001": "D", "FIX-UNIT-BEHAVIOURAL-GATE-001": "D",
    "HISTORY-STATE-DIR-PER-PROJECT-001": "E", "HISTORY-SNAPSHOT-STALE-ON-REOPEN-001": "E",
    "DIRECTORY-NAME-PRUNING-P3-SIBLING-SITES-001": "E", "INDEX-SUBDIRECTORY-RELATIVE-PATHS-001": "E",
    "KERNEL-STAGING-WORKSPACE-BASE-ESCAPES-001": "F", "SKILL-QPID-RULE-DOCTOR-ADVICE-STALE-001": "F",
    "TEST-RESOURCEWARNING-HYGIENE-001": "G", "KERNEL-GOLDEN-HARNESS-VOLATILITY-001": "G",
    "KERNEL-TESTS-CHECKOUT-MEMORY-MORE-FILES-001": "G", "CERT-CI-PLAIN-JOBS-LOCKFILE-GROUP-001": "G",
    "DEMO-RESET-RM-WORKSPACE-RACES-DOCKER-RELEASE-001": "G", "KERNEL-GOLDEN-NO-SUCCESS-SCENARIO-001": "G",
    "REPAIR-WITHOUT-REPRODUCED-FAILURE-STAYS-OPEN-001": "G",
    "CERTIFICATION-MECHANICAL-CLEANUP-S4-001": "G",
    "MANAGED-SERVICE-ENV-STATE-BINDING-001": "H", "CERT-OWNER-DEATH-MASK-SCRATCH-001": "H", "WAVE8-DOCKER-VIEW-CLEANUP-RACE-001": "H",
    "WAVE8-PY-RUNTIME-ONLY-VENV-BOOTSTRAP-001": "H", "RUNTIME-GATE-VENV-LACKS-PROJECT-TEST-DEPENDENCY-001": "H",
    "DOCKER-BIND-SOURCE-REFUSED-AFTER-PATHNAME-RECREATION-001": "H",
    "HITL-OTHER-APPROVAL-TRIGGERS-DEFERRAL-001": "I", "APPLIED-OPEN-RUN-PERSISTS-LEARNED-SKILLS-001": "I",
    "S7-APPROVAL-SUBJECT-RESIDUALS-001": "I",
}
TARGETS = {
    "A": "impact classification per row: a trace or test showing whether any runtime branch or certification verdict reads the label, count or "
         "text; pure display wording then closes as intentional or deferred cleanup, a reader makes it a defect to fix",
    "B": "a deterministic reproducer on both executors (same plan, same candidate); a validity or requirement verdict that differs is a defect, "
         "fixed before production readiness",
    "C": "a deterministic reproducer per row on the scripted enforce loop, the P2 row first; a reproduced wrong stop or lost prohibition is fixed "
         "before production readiness whether or not a scenario reaches it",
    "D": "a deterministic reproducer per item (the row's own condition)",
    "E": "one probe per row (two state directories; index root then subdirectory; the listed sibling sites)",
    "F": "one reproducer of the untyped refusal; the qpid skill sentence is re-verified in the Qpid scenario",
    "G": "the final certification run itself (stale checkout state moved aside, skips and created state recorded); an item that can false-pass, "
         "false-fail or misclassify certification is fixed before certification is declared",
    "H": "FIRST measurement target of real-scenario validation: actual runtime, container and daemon behaviour",
    "I": "an owner decision, then its test",
}
# Intentional-contract rows of the root map that still carry a pending owner action in their own text.
PENDING_OWNER_ACTION = {"GUI-EXEC-GIT-SHIM-001", "CERT-HISTORICAL-FIXTURE-EVIDENCE-EXPOSURE-001", "DEMO-TIME-BUDGET-001"}


def root_map():
    classes = {}
    for line in open(ROOT_MAP, encoding="utf-8"):
        match = re.match(r"\| \d+ \| `([A-Z0-9-]+)` \| [^|]+\| ([A-Z_]+) \| [^|]+\| (.*) \|\s*$", line)
        if match:
            classes[match.group(1)] = (match.group(2), match.group(3).strip())
    return classes


def main():
    rows = list(csv.DictReader(open(REGISTRY, newline="", encoding="utf-8")))
    mapped = root_map()
    open_rows = [row for row in rows if row["status"] != "CLOSED"]
    table, problems = [], []
    for row in open_rows:
        rid = row["id"]
        klass, note = mapped.get(rid, (None, ""))
        if rid in OPEN_MEASUREMENT:
            group = OPEN_MEASUREMENT[rid]
            if row["status"] not in ("OPEN", "TRACKED_IN_OPEN_ITEMS"):   # the second: its status lives in OPEN-ITEMS.md
                problems.append(f"measurement-required row is not OPEN: {rid} ({row['status']})")
            table.append(("OPEN_MEASUREMENT " + group, rid, row, GROUPS[group] + ". Target: " + TARGETS[group]))
        elif rid in FUTURE:
            table.append(("FUTURE", rid, row, FUTURE[rid]))
        elif klass == "FUTURE_FEATURE":
            table.append(("FUTURE", rid, row, "root map: " + note))
        elif klass == "INTENTIONAL_CONTRACT":
            pending = "; PENDING OWNER ACTION in the row's own text" if rid in PENDING_OWNER_ACTION else ""
            table.append(("INTENTIONAL", rid, row, "root map: " + note + pending))
        else:
            problems.append(f"no disposition: {rid} ({row['status']}, root map {klass})")
    ids = {row["id"]: row["status"] for row in rows}
    for rid in list(OPEN_MEASUREMENT) + list(FUTURE) + sorted(PENDING_OWNER_ACTION):
        if ids.get(rid) in (None, "CLOSED"):
            problems.append(f"named here but {'absent' if rid not in ids else 'closed'} in the registry: {rid}")
    counts = {}
    for disposition, _rid, _row, _basis in table:
        key = disposition.split()[0]
        counts[key] = counts.get(key, 0) + 1
    lines = ["# Registry: final disposition of every non-closed row", "",
             f"Generated by `registry_final_disposition.py` from `handover/BACKLOG_REGISTRY.csv`: {len(rows)} rows, "
             f"{len(rows) - len(open_rows)} closed, {len(open_rows)} not closed "
             f"({', '.join(f'{name} {count}' for name, count in sorted(counts.items()))}).", "",
             "| Disposition | Row | Priority / status | Basis |", "|---|---|---|---|"]
    for disposition, rid, row, basis in sorted(table, key=lambda item: (item[0], item[1])):
        lines.append(f"| {disposition} | `{rid}` | {row['priority']} / {row['status']} | {basis} |")
    text = "\n".join(lines) + "\n"
    print(text)
    for problem in problems:
        print("PROBLEM:", problem)
    if "--write" in sys.argv and not problems:
        with open(os.path.join(HERE, "registry_final_disposition.md"), "w", encoding="utf-8") as handle:
            handle.write(text)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
