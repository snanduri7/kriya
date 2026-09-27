# PRD-031A: Pluggable Static Analysis, Policy and Risk-Acceptance Gate (directives)

This is a roadmap correction from the user, dated 2026-09-27. After PRD-030/031, do **not** start PRD-032. The next scope is PRD-031A, and PRD-032 begins only after PRD-031A is verified. The tracker's PRD-032 row now depends on PRD-031A.

PRD-031A must stay provider-neutral:
- a `StaticAnalysisPort`;
- Semgrep as the first adapter, not a core dependency;
- static analysis may be disabled;
- a provider capability, language and prerequisite check before any coverage is claimed;
- configurable policy outcomes;
- explicit accepted-risk and waiver handling;
- a PRE/POST brownfield baseline;
- normalized evidence;
- local, privacy and egress enforcement;
- no authority and no waiver created by an LLM.

Status: SPEC_APPROVED (2026-09-27). The task specification is `handover/PRD-031A_TASK.md`; §20 records the approved decisions and corrections. Priority P1, live test REQUIRED (real pinned-scanner tier; no live-model test). Implementation has not started.
