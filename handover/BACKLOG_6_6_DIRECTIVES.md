# Backlog Closure 6.6 (user directive, 2026-09-27)

Before Batch 7, fix only these three existing defects:

1. `DEVELOPER-PROMPT-FIT-001`
2. `PROMPT-FIT-ROLE-CHAIN-001`
3. authority-inspect raw traceback defect

Do not include vLLM support, per-adapter environment evidence or legacy trace migration.

## DEVELOPER-PROMPT-FIT-001
Fix Developer request sizing so mandatory/fixed Developer prompt cost is accounted before optional context is allocated.
- compute optional capacity only after system prompt, authoritative goal, required instructions/evidence, protocol overhead, output reserve and safety reserve;
- optional learned/reference/repository context must shrink before mandatory content;
- genuinely unsatisfiable mandatory requests still fail closed with `CONTEXT_BUDGET_UNSATISFIABLE`;
- do not increase context windows or weaken safety margins;
- preserve direct/enforce/milestone behavior and authority separation.

Tests: 8K boundary, 32K control, large reference context, mandatory-only unsatisfiable case, no authority loss.

## PROMPT-FIT-ROLE-CHAIN-001
Prompt fitting must remain valid for the model that actually receives the request, including fallback.
- do not size Planner/Reviewer requests only against the first role model;
- on fallback transition, recompute/repackage against the fallback's effective qualified context/output constraints;
- never reuse a package proven only for another runtime capacity;
- fallback may reduce optional context but not mandatory authority/evidence semantics;
- preserve exact inference identity, qualification and PRD-017 fallback telemetry;
- no provider-specific logic in workflow code.

Tests: primary larger than fallback, primary smaller than fallback, equal windows, fallback after first-model failure, mandatory request cannot fit fallback, optional context repackaging, direct/enforce/milestone.

## Authority inspect traceback
When `KRIYA_AUTHORITY_HOME` is inside the workspace, keep the existing refusal but normalize CLI output:
- no Python traceback for expected policy refusal;
- typed reason/code;
- concise actionable remediation;
- non-zero exit preserved;
- JSON mode remains machine-readable;
- unexpected internal exceptions must not be accidentally hidden.

## Scope
No unrelated refactoring. Do not implement: real vLLM adapter; INF-001 per-adapter environment observation; legacy traces migration. New P2/P3 findings are recorded only. New P0/P1 correctness/security defects block closure.

## Verification
1. focused tests for all three fixes;
2. full `.venv/bin/pytest`;
3. targeted live/fallback test if role-chain behavior needs live evidence;
4. confirm existing model identities remain QUALIFIED;
5. `doctor --production`.

Exit: all three defects VERIFIED; no P0/P1 open; model qualification PASS; context certification PASS; PRODUCTION_READY=true. Then stop and start Batch 7 PRD-030/031.
