# KRIYA ARCHITECTURE DECISIONS

**Document:** `handover/KRIYA_ARCHITECTURE_DECISIONS.md`  
**Version:** v0.1.0  
**Date:** 2026-09-30  
**Status:** Canonical architecture decision ledger — initial reconstruction  
**Project:** Kriya  
**Current implementation checkpoint referenced:** `6e0d711` (`milestone-decomposition`)  
**Current external benchmark status:** `GRAPHIFY_EXTERNAL_ACCEPTANCE = NOT ESTABLISHED`  
**Canonical Graphify failure run referenced:** `20260930T073012-85574393`

---

## 1. Purpose

This document exists to prevent architectural drift caused by long conversations, local optimizations, benchmark failures, model changes, or loss of historical rationale.

It is intentionally a **decision ledger**, not a narrative design document.

For every material architecture decision it records:

- what was decided;
- why it exists;
- the evidence/problem that caused the decision;
- the invariant that must remain true;
- what the decision does **not** imply;
- what would justify changing it;
- its current status.

A new observation must not silently overturn an older decision.

Any material future recommendation must first classify its relationship to this ledger as:

- **PRESERVE** — existing decision remains correct;
- **EXTEND** — existing decision remains correct but needs another layer;
- **SUPERSEDE** — existing decision is no longer valid, with explicit contradictory evidence.

If no such classification is made, the architectural recommendation is incomplete.

---

## 2. Authority and Precedence

When sources disagree, use the following precedence:

1. **Current deterministic implementation evidence and verified production behavior**
2. **Explicit user-approved architecture decisions**
3. **Current PRD / production-control contracts**
4. **Accepted design reviews and handover documents**
5. **Older architectural proposals**
6. **Working hypotheses / model suggestions**

A later conversation does not outrank a prior accepted architectural invariant merely because it is newer.

Implementation may lag an accepted decision. In that case:

- the **decision** remains the architecture target;
- the **implementation state** must be stated separately.

---

## 3. Status Vocabulary

| Status | Meaning |
|---|---|
| **ACTIVE** | Canonical architectural decision. Preserve unless explicit evidence justifies supersession. |
| **ACTIVE — EXTENDED** | Original decision remains valid; later evidence added another required layer. |
| **PROPOSED** | Direction is promising but not yet adopted as canonical architecture. |
| **DEFERRED** | Accepted as potentially useful but intentionally not part of the current architecture/baseline. |
| **SUPERSEDED** | Replaced by a later decision; retained for history only. |
| **HISTORICAL / VERIFY** | Appeared in earlier planning but is not safe to treat as current without source verification. |

---

# PART I — CORE PRODUCT AND AUTHORITY MODEL

## KAD-001 — Kriya Is a Deterministic Engineering Control Plane Around Replaceable LLMs

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Kriya is not a free-running autonomous coding agent.

Its core architecture is:

> **Deterministic workflow orchestration around bounded, replaceable LLM roles.**

The LLM may reason, propose, generate, diagnose and review.

Kriya owns:

- workflow state;
- repository truth;
- write authorization;
- tool authority;
- verification;
- recovery;
- budgets;
- evidence;
- persistence;
- terminal success/failure.

### Why

The project repeatedly observed that model confidence, formatting quality and code plausibility are not reliable engineering acceptance criteria, especially with local models.

The durable product is the **engineering control plane**, not any one model.

### Invariant

> **LLM OUTPUT CAN AUTHORIZE NOTHING.**

### Does not imply

- LLMs should be weakly constrained in reasoning.
- LLMs cannot use tools.
- LLMs cannot diagnose failures.
- Kriya must precompute every useful fact before inference.

### Change trigger

Only reconsider if a future architecture can prove equivalent or stronger deterministic authority while moving lifecycle authority into a probabilistic model. No current evidence supports that.

---

## KAD-002 — Deterministic Evidence Outranks Model Judgment

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Correctness authority follows the strongest available evidence.

Canonical principle:

`DETERMINISTIC > GROUNDED > JUDGMENT`

Examples of strong evidence include:

- source revision and content identity;
- compiler/parser results;
- AST/LSP facts;
- deterministic assertions;
- tests/regressions;
- runtime verification;
- policy decisions.

Model judgment is advisory when stronger evidence exists.

### Why

Kriya was built specifically to move reliability outside probabilistic model confidence.

### Invariant

A weaker source must never override stronger evidence.

A derived recovery obligation may never have greater authority than the evidence from which it was derived.

### Change trigger

Only if a stronger evidence taxonomy is defined; not merely because a model becomes more capable.

---

## KAD-003 — Human Approval Remains a First-Class Authority Boundary

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Human approval is retained for operations/policies that require it. Lack of approval must block the action rather than silently degrade the requirement.

### Why

Enterprise engineering requires explicit operator authority for sensitive paths, large/risky changes, policy exceptions and accepted residual risk.

### Invariant

A model request is not approval.

### Change trigger

Only if an explicitly approved policy replaces a human approval class with a deterministic equivalent.

---

# PART II — LOCAL-FIRST, SECURITY, TOOLS AND EGRESS

## KAD-004 — Local-First Is a Hard Architectural Goal; Public Knowledge Acquisition Is Separately Governed

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Kriya is local-LLM-first.

Proprietary source, internal documentation and private context must stay on controlled infrastructure.

Public Internet access may be used only for approved public knowledge and must never include proprietary context in outbound queries.

### Why

This is a foundational user requirement and a primary product differentiator.

### Invariant

Private repository/product information must not be attached to public lookup queries.

### Does not imply

`local_only` means universal host/network isolation. It is an LLM egress rule unless a stronger containment layer applies.

---

## KAD-005 — `kriya/core/llm.py` Is a Hard LLM Egress Boundary

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

The LLM client remains a hard enforcement boundary for LLM egress.

Defense-in-depth may add controls elsewhere, but no alternative path may bypass the central LLM egress policy.

### Why

Earlier MA4 work explicitly required `core/llm.py` to remain the hard egress boundary.

### Invariant

Every production LLM request must pass the same effective egress authority.

### Change trigger

Only by replacing it with an equally centralized, verified, non-bypassable boundary.

---

## KAD-006 — MCP and Native Tools Are Capability Transports, Not Authority

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

MCP/native tools extend capability but do not bypass Kriya policy.

Tool use must remain behind:

- typed schemas;
- phase/capability allow-lists;
- argument validation;
- path/resource authority;
- approval where required;
- timeout/output bounds;
- containment;
- trace/evidence.

### Why

Direct model-to-tool authority would violate the core control-plane law.

### Invariant

> Requesting a capability does not grant authority to execute it.

### Does not imply

MCP should become the orchestration architecture.

---

## KAD-007 — Kriya Must Remain Modular; Avoid Re-Creating a Giant Monolithic Orchestrator

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

New production behavior should be introduced through coherent modules/services/plugins rather than growing already-large orchestration functions indefinitely.

### Why

Repeated reviews identified very large workflow/attempt/failure-handling functions as a maintainability and correctness risk.

### Invariant

Authority-critical behavior must have explicit ownership and testable boundaries.

### Change trigger

None anticipated; implementation boundaries may evolve.

---

# PART III — STATE, EXECUTION AND PERSISTENCE

## KAD-008 — One Canonical Run Lifecycle Owns Mutating Truth

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Kriya must have one canonical mutating lifecycle authority centered on `RunCoordinator` / `RunRecord` semantics rather than competing workflow truths.

Specialized stores may retain detailed evidence but must not independently redefine lifecycle truth.

### Why

State had historically been distributed across checkpoints, ledgers, traces, candidate state and registries, creating crash/resume ambiguity.

### Invariant

Exactly one canonical lifecycle record identifies:

- run identity;
- workspace/base identity;
- effective configuration;
- model/runtime identities;
- plan/obligation identity;
- candidate identity;
- verification evidence;
- retry state;
- commit state;
- terminal result.

---

## KAD-009 — Resume Must Revalidate Safety-Relevant Identity

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Resume must compare durable safety fingerprints before reusing prior state.

Changes to source, config, plan, skills, model/runtime, containment/toolchain or verification policy invalidate the dependent evidence or cause resume refusal.

### Why

Stale checkpoints can create false success or apply stale mutation authority.

### Invariant

Never silently reuse stale safety-critical evidence.

### Specific rule

Source changes invalidate exact source/mutation authority and candidate evidence.

Model/runtime changes invalidate model capability/protocol and qualification-bound evidence.

---

## KAD-010 — Direct and Milestone Execution Converge on One Authoritative Execution Model

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Direct and milestone workflows should not evolve as independent mutating architectures.

The accepted direction is a unified `ExecutionPlan` / `WorkUnit` style executor with one production control path.

### Why

Maintaining dual orchestration generations creates correctness drift and duplicate lifecycle semantics.

### Invariant

Different planning modes may produce different plans; they must not produce different safety/commit semantics.

---

## KAD-011 — Planner Output Is Untrusted Strategy, Not Requirement Authority

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Planner output must be deterministically validated and cannot silently redefine the user goal, repository contracts or terminal requirements.

Milestone boundaries are sequencing boundaries, not automatic build/ownership boundaries.

Brownfield topology and actual repository structure remain authoritative.

### Why

Earlier milestone work showed that model-generated decomposition can invent artificial ownership/build boundaries.

### Invariant

Planner strategy cannot become requirement authority.

---

## KAD-012 — Structured Plans Use Explicit Dependencies and Acceptance Contracts

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Structured planning should represent:

- explicit subtask IDs;
- DAG dependencies;
- provides/consumes relationships;
- invariants;
- file/owner intent;
- structured acceptance criteria;
- bounded replan.

### Why

Free-form prose planning is too weak to support deterministic multi-step correctness and recovery.

---

## KAD-013 — Process Profiles May Add Controls but Never Reduce Full Verification

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

A profile such as LIGHT may be used for telemetry/observation, but it must not silently reduce an already-required verification boundary.

### Why

Accepted MA2 rule.

### Invariant

Operational convenience cannot downgrade correctness authority without an explicit separate policy decision.

---

# PART IV — REQUIREMENTS, OBLIGATIONS AND CORRECTNESS

## KAD-014 — Requirements Become Explicit Change Contracts and Obligations

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Kriya represents requested change through explicit intended deltas such as:

- PRESERVE;
- MODIFY;
- REMOVE;
- ADD;
- USE;
- VERIFY.

Requirements become machine-addressable obligations with stable identity, authority, ownership, status and evidence.

### Why

Multi-stage workflows were losing requirement lineage and allowing planner/retry strategy to mutate intent.

### Invariant

Original terminal requirements retain identity and evidence through planning, architecture, development, retry and resume.

---

## KAD-015 — Obligation Ownership Is Stage-Aware

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Obligations distinguish ownership states such as:

- CURRENT;
- FUTURE_ORDERED;
- PAST_ORDERED;
- UNRELATED;
- UNOWNED.

### Why

A current subtask must not be failed for a requirement intentionally owned by another ordered stage.

---

## KAD-016 — Terminal Success Requires All Terminal-Required Obligations to Be Satisfied

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

A run cannot succeed while a terminal-required obligation remains materially:

- PENDING;
- VIOLATED;
- INDETERMINATE.

### Why

Prevents false success from local/partial gate passes.

---

# PART V — BROWNFIELD REPOSITORY UNDERSTANDING

## KAD-017 — Deterministic Brownfield Repository Analysis Is Foundational

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Before mutation, Kriya uses deterministic repository understanding such as:

- language/framework/build detection;
- module topology;
- dependency/ownership evidence;
- AST/source structure;
- symbols/members;
- repository conventions;
- LSP where available;
- existing code relationships.

### Why

Early brownfield work showed that local models were not reliably advised about **where a change belonged**. They could choose the wrong owner, miss intermediate layers or modify the wrong region.

Repository analysis was introduced specifically to solve this.

### Invariant

Local models must not be expected to rediscover brownfield ownership/topology blindly.

### Important clarification

The Graphify failure does **not** invalidate this decision.

---

## KAD-018 — Skeletons Are Structural/Localization Context, Not Mutation Authority

**Status:** ACTIVE — EXTENDED  
**Change class:** EXTEND

### Decision

Skeletonization remains a valid and important way to represent large source files within local-model context budgets.

Its purpose is:

- structure;
- signatures;
- neighboring symbols;
- ownership;
- navigation/localization.

A skeleton with `body_elided` is **not** exact mutation-authority context for the elided body.

### Why

Skeletons were introduced after brownfield failures where the correct region/owner was not being conveyed reliably.

The 2026-09-30 canonical Graphify run showed a different failure: Kriya successfully localized relevant members, but rendering elided the exact source needed for an anchored edit.

### Invariant

> Structural context tells the model **where**.  
> Exact authoritative source tells the model **what bytes it may safely edit**.

### This decision specifically rejects

The interpretation that Graphify proves skeletonization/repository analysis should be removed.

---

## KAD-019 — LSP Is Repository Intelligence, Not the Generator

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

LSP contributes:

- symbols;
- definitions;
- references;
- types;
- diagnostics;
- semantic navigation.

The LSP is not the code generator and does not replace deterministic verification.

### Why

Language-aware structure reduces raw-text guessing, especially in brownfield repositories.

---

## KAD-020 — Observation Location Is Not Necessarily the Causal Repair Target

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

A failing test/assertion/diagnostic location does not automatically authorize mutation of that file.

Recovery must attribute the causal owner/provider using grounded repository evidence.

### Why

PRV-11 and related brownfield work showed cases where the visible failure was in a consumer/test but the real defect belonged to an established provider.

### Invariant

Evidence location ≠ causal mutation authority.

---

## KAD-021 — Preserve Unauthorized Existing Behavior

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Brownfield mutation must preserve everything not authorized by the task.

This includes, where applicable:

- public APIs;
- existing ownership;
- dependencies;
- unrelated source;
- established provider/consumer contracts;
- dirty human work.

### Why

A coding agent can make tests pass by replacing or bypassing established behavior. Passing tests alone is insufficient.

---

# PART VI — CONTEXT AND MUTATION AUTHORITY

## KAD-022 — Separate Repository, Investigation and Mutation-Authority Context

**Status:** ACTIVE — EXTENDED  
**Change class:** EXTEND

### Decision

Kriya context is conceptually separated into three layers:

### L1 — Repository / Structural Context

Examples:

- modules;
- dependency graph;
- ownership;
- symbols;
- skeletons.

Purpose: planning and navigation.

### L2 — Investigation Context

Examples:

- member bodies;
- callers/references;
- nearby blocks;
- failure regions;
- grounded retrieval.

Purpose: understand the defect.

### L3 — Mutation-Authority Context

Examples:

- exact current source span;
- file identity;
- source digest/revision;
- exact bytes shown to the model.

Purpose: authorize exact mutation protocols.

### Why

Graphify proved that correct localization can coexist with insufficient exact source for mutation.

### Invariant

L1 cannot authorize an edit inside elided source merely because it identified the right member.

---

## KAD-023 — Every Offered Mutation Protocol Must Be Feasible Under Available Authoritative Context

**Status:** ACTIVE — CURRENT FIX TARGET  
**Change class:** EXTEND

### Decision

Before a Developer inference, Kriya must ensure at least one offered mutation protocol is feasible under the authoritative context available to that invocation.

Conceptually:

`context authority + operation contract -> feasible operation set`

### Required semantics

**ANCHORED_EDIT** is feasible only when sufficient exact authoritative source is available, or Kriya provides a deterministic read/acquisition step that establishes it.

**FULL_FILE_REPLACEMENT** is feasible only when authoritative full-file context exists and policy permits it.

If no operation is feasible:

`CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE`

must stop the attempt before inference.

### Why

Canonical Graphify run `20260930T073012-85574393` produced nine failed Developer attempts:

- repeated anchored edits against source that had been elided;
- full-file replacement offered by the prompt but rejected because full-file context was non-authoritative;
- no candidate produced.

This was an unwinnable contract state.

### Invariant

Kriya must never ask a model to satisfy an operation contract that its own context/authority system makes impossible.

---

## KAD-024 — Exact Source Authority Is Revision/Digest Bound

**Status:** ACTIVE — CURRENT FIX TARGET  
**Change class:** EXTEND

### Decision

An exact source span used for mutation authority must be bound to current source identity.

At minimum, authority must identify:

- target file;
- exact span/window;
- source/content digest or equivalent;
- current source revision/identity.

### Why

A span shown before the file changes cannot safely authorize an edit after source mutation.

### Invariant

Previously observed source bytes do not remain authoritative after their source identity becomes stale.

---

## KAD-025 — Exact-Source Acquisition Must Use Progressive Granularity Rather Than Whole-File Reinjection

**Status:** ACTIVE — CURRENT FIX TARGET  
**Change class:** EXTEND

### Decision

Do not solve large-file mutation by unconditionally returning to whole-file prompt injection.

Use bounded progressive localization such as:

`file -> member -> enclosing block/span -> exact local window`

A separate class tier is optional and should be added only if evidence justifies it.

### Why

Whole-file injection previously produced requests far beyond local-model context capacity. Graphify later showed that member-level all-or-elided rendering can still be too coarse.

### Invariant

Exactness must increase without destroying bounded-context behavior.

---

## KAD-026 — Context Budgeting Must Be Model/Tokenizer Aware and Preserve Authority

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Prompt/context/output budgets must be calculated using the actual model/runtime/tokenizer profile.

Budgeting may remove/degrade optional context, but must not silently remove required authority/evidence while leaving the corresponding operation enabled.

### Why

Different local models tokenize the same request differently. Earlier fallback preflight showed a request that fit qwen3-coder but not qwen3.6 until duplicated goal/design content was removed.

### Invariant

A “fit” request that has dropped the evidence required by its operation contract is not a valid request.

---

## KAD-027 — Prompt/Context Deduplication Must Preserve Semantic Roles

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Do not duplicate the same user goal under semantically different labels merely to satisfy stage interfaces.

In particular:

`GOAL != PREDETERMINED DESIGN`

unless an independent design actually exists.

### Why

Graphify preflight found the same goal rendered as overall constraints, predetermined design and task goal. Removing the false design copy allowed fallback requests to fit without weakening meaning.

### Invariant

Deduplication must remove redundancy, not erase independent authority.

---

# PART VII — MODEL GOVERNANCE AND ROUTING

## KAD-028 — Models Are Replaceable Workers; Kriya Owns Canonical State

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Models are treated as stateless/replaceable workers.

Kriya owns canonical:

- goal;
- constraints;
- plan;
- repository facts;
- changes;
- diagnostics;
- knowledge;
- history;
- invariants.

Do not depend on lossy model-to-model context transfer as authoritative state.

### Why

Model switching otherwise introduces silent context gaps and correlated errors.

---

## KAD-029 — Exact Model + Runtime + Inference Identity Is Production-Relevant

**Status:** ACTIVE — EXTENDED (KAD-062)  
**Change class:** PRESERVE

### Decision

Production qualification is tied to exact effective model/runtime/inference identity, not merely a model tag.

Relevant identity includes the serving/runtime characteristics required by the qualification scheme.

### Why

Tool protocol, reasoning behavior, tokenizer/context behavior and renderer/parser stacks differ across runtimes and versions.

### Invariant

A model qualification from one runtime identity cannot be silently treated as certification of a materially different identity.

---

## KAD-030 — Production Roles Must Be Capability-Qualified

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

A production model/role pairing must have current qualification evidence for the capabilities actually required by that role.

Qualification policy is configuration-governed and part of qualification identity.

### Why

Kriya observed real tool-argument, context-capacity and protocol differences across Qwen-family models/runtimes.

### Invariant

Do not weaken qualification to make a preferred model pass.

---

## KAD-031 — Fallback Is a Fresh Capability Transition, Not Prompt Replay

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

When model identity changes, Kriya recomputes:

- context budget;
- tool/protocol availability;
- mutation/edit protocol;
- output/reasoning budget;
- structured-output strategy.

Fallback requests are rebuilt from authoritative structured state, not by replaying the primary model's rendered prompt.

### Why

Primary and fallback models can differ materially in tokenizer density, tools, output format and context behavior.

---

## KAD-032 — Role-Specific Models Are Allowed but Must Be Evidence-Driven

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Kriya supports different models for different roles, but role specialization must be justified by measured capability and protocol requirements.

Do not force multi-model complexity by default.

### Historical rationale

Earlier architecture deliberately favored a default single-model setup until measurements justified specialization because swapping models adds:

- latency;
- memory pressure;
- cache churn;
- context-transfer risk;
- qualification complexity.

---

## KAD-033 — qwen3.8 Reasoning / qwen3-coder Mutation Split Is a Post-Graphify Direction, Not Current Canonical Routing

**Status:** DEFERRED / PROPOSED FOR POST-GRAPHIFY VALIDATION  
**Change class:** NONE YET

### Direction recorded

Potential future role mapping:

- qwen3.8: Planner, Architect, Investigator/root-cause, Reviewer, read-only reasoning;
- qwen3-coder: Developer, code mutation, repair, exact protocol/tool execution;
- deterministic Kriya mechanisms: authority.

Conceptual summary:

> **qwen3.8 reasons; qwen3-coder executes mutations; Kriya authorizes.**

### Why not ACTIVE

qwen3.8 failed literal tool-argument integrity in repeated qualification testing and is not qualified for most current production roles.

The current Graphify benchmark explicitly excludes qwen3.8 so routing is not changed mid-benchmark.

### Required next step before adoption

`ROLE-CAPABILITY-ALIGNMENT-001` must trace each role's actual exercised protocol/capability requirements. Qualification should reflect real role behavior, not be weakened to accommodate a model.

---

# PART VIII — GENERATION, CANDIDATES AND COMMIT AUTHORITY

## KAD-034 — Model Mutation Occurs in Isolated Candidate State

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Generated/repaired changes are applied to an isolated candidate/worktree/sandbox path before real-workspace mutation.

If isolation cannot be established under the production policy, fail closed.

### Why

A rejected model candidate must never corrupt the real repository.

---

## KAD-035 — Rejected Candidates Never Become Real Workspace State

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Validation and failure handling occur against candidate content before real-workspace write.

Rejected candidate bytes must not become authoritative source.

### Why

This protects brownfield baselines and allows deterministic recovery.

---

## KAD-036 — Verified Bytes Must Equal Committed Bytes

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Final commit/application is revision-grounded and transactional.

The candidate that passed terminal verification must be the candidate that is committed/applied.

### Invariant

> `verified candidate digest == committed candidate digest`

A later mutation requires re-verification.

---

## KAD-037 — Real Workspace Commit Happens Only After Terminal Eligibility

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

The real workspace must not be committed/applied before all required pre-commit gates and approvals have established eligibility.

### Why

Earlier production-readiness work identified post-write terminal checks as a false-success/recovery risk and moved authority toward transactional commit.

---

# PART IX — VERIFICATION AND RECOVERY

## KAD-038 — Success Is Deterministic Evidence, Never Model Confidence

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Terminal success is based on applicable deterministic evidence such as:

- structural/static validation;
- compile/build;
- tests;
- regression;
- runtime verification;
- contract/policy checks;
- requirement/obligation completion.

Reviewer/model confidence is advisory.

---

## KAD-039 — Compiler and Runtime Evidence Are First-Class Agent Gates

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Compilation and runtime execution are not optional post-processing decorations.

Failures are captured as structured evidence used to classify and repair.

### Why

Compiler/runtime behavior repeatedly exposed model knowledge and generation errors that text review did not.

---

## KAD-040 — Brownfield PRE/POST Regression Is a Correctness Boundary

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Where production policy requires brownfield regression, Kriya must establish trustworthy baseline (PRE) and candidate (POST) evidence rather than accepting only narrow generated tests.

### Why

Targeted tests can pass while unrelated existing behavior regresses.

---

## KAD-041 — Retry Is an Evidence-Driven State Transition, Not a Loop

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

A retry requires a measurable progress delta.

Canonical progress dimensions include, as applicable:

- failure signature;
- candidate/workspace hash;
- implicated files;
- evidence/context fingerprint;
- context authority/revision;
- requested operation/protocol;
- model identity;
- plan/repair-contract revision;
- deterministic diagnostics.

### Invariant

Timestamp/random prompt variation does not count as progress.

---

## KAD-042 — Missing Information Requires Context Change, Not Stochastic Resampling

**Status:** ACTIVE — EXTENDED  
**Change class:** EXTEND

### Decision

If a failure is caused by unavailable/non-authoritative source, another model call with materially identical context is not progress.

The next attempt must first change something capable of resolving the failure, such as:

- exact source authority;
- localization;
- operation set;
- deterministic diagnostics.

### Why

Canonical Graphify attempts 1–4 repeated anchored-edit failures while the needed source remained elided.

### Invariant

`same context + same feasible operations + same failure family` is not a useful retry.

---

## KAD-043 — Targeted Repair Preserves Known-Good State

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Prefer narrow/attributed repair over blind full regeneration.

Recovery modes may include targeted, missing-file, fallback-targeted, coordinated and full-set escalation, but each must be justified by evidence.

### Why

Local models can repeat errors and broad regeneration can destroy correct work.

---

## KAD-044 — Recovery Uses Causal Ownership, Not Error-File Fallback

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Repair scope expansion must use grounded causal attribution and dependency/ownership evidence.

A visible failing consumer/test does not automatically become a legal mutation target.

### Why

PRV-11 exposed the danger of conflating evidence location with causal repair ownership.

---

# PART X — KNOWLEDGE, SKILLS, CKM AND EIE

## KAD-045 — Knowledge Compilation Precedes Fine-Tuning

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Kriya's knowledge strategy starts with:

- structured knowledge compilation;
- retrieval;
- skills/rules;
- tools;
- Engineering Packs / CKM products.

Fine-tuning is not the first mechanism.

### Why

The project goal is model-independent, maintainable knowledge that can be inspected, versioned and reused across models.

---

## KAD-046 — CKM/Compiled Knowledge Is Authoritative; Derived Products Are Not the Source of Truth

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

In the knowledge architecture:

- source documents are immutable evidence;
- CKM/compiled normalized knowledge is authoritative;
- generated embeddings, skills, rules, prompt packs and datasets are derived knowledge products.

Consumers use the defined query/application boundary rather than treating a derived prompt artifact as canonical truth.

### Why

Knowledge must survive model swaps and remain inspectable/provenanced.

---

## KAD-047 — KnowledgeGuard Is Evidence-Driven, Not Merely a Release-Date Checker

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

KnowledgeGuard combines multiple signals.

Evidence strength includes:

- compiler evidence — highest/deterministic;
- verified skills/repository usage/retrieved docs — strong;
- temporal cutoff — heuristic;
- missing skill — heuristic;
- model self-uncertainty — weak.

Only deterministic evidence can establish a confirmed failure class without ambiguity.

### Why

“Released after training cutoff” alone produces false positives and does not prove the model lacks the required API knowledge.

---

## KAD-048 — Model Knowledge Cutoff Belongs to the Model Profile

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Knowledge cutoff is per model/profile, with confidence/provenance.

Do not use one global training cutoff across all models.

### Why

Different local models have different training data and cutoff uncertainty.

---

## KAD-049 — Public Dependency/Documentation Lookup Is Local-First and Policy-Governed

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Resolution preference is:

1. project/local metadata;
2. cache;
3. internal/mirror registry;
4. approved public registry;
5. unknown.

Public lookup is policy dependent and must pass the egress manager.

### Why

Supports air-gapped/private deployments while allowing controlled public knowledge acquisition.

---

## KAD-050 — Skills Are Version-Aware, Provenanced, Reusable Knowledge Products

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Skills should:

- preserve provenance;
- support version ranges;
- be reusable across models;
- be validated before trust;
- not become authoritative simply because a model generated them.

### Why

The same proprietary/external engineering knowledge should outlive individual model families.

---

## KAD-051 — EIE Validation Focus Is End-to-End Pack Sufficiency Before Expanding Evaluation Machinery

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Immediate EIE validation remains:

`real source docs -> Engineering Pack -> Pack-only application generation -> compile/run/test`

Failure classification:

- source-document knowledge missing;
- Pack synthesis missing;
- CKM-to-LLM translation gap;
- model/tooling execution gap;
- runtime environment gap.

### Why

The user explicitly rejected diversion into golden datasets/selectors/evaluation infrastructure before proving the basic Engineering Pack hypothesis.

---

## KAD-052 — Engineering Pack Compilation Must Preserve Evidence, Categories and Semantic Partiality

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Engineering Pack compilation must not invent missing engineering facts or silently collapse/conflict semantic evidence.

Important accepted properties include:

- provenance preservation;
- collection semantics where the knowledge model permits multiples;
- optional values remain absent rather than fabricated;
- explicit metadata ownership;
- no lossy LLM summarization as Pack authority;
- deterministic serialization/loading/version handling.

### Why

The Engineering Pack is intended as a trustworthy model-independent knowledge product, not a prose summary.

---

# PART XI — BENCHMARKS, FIX DISCIPLINE AND PRODUCTION CERTIFICATION

## KAD-053 — Graphify Is an External Detector, Not a Design Oracle

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Graphify is used to expose external coding-agent weaknesses.

Its evaluator/ground truth must not become an input to Kriya's design or canonical run.

### Invariant

> **Graphify may discover a symptom; a generic reproducer must justify a production fix.**

### Anti-overfitting rule

No production logic may encode:

- Graphify-specific symbols;
- evaluator cases;
- maintainer solution details;
- benchmark-specific method/file names.

---

## KAD-054 — Benchmark Failure Fixes Require a Generic Reproducer

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

A benchmark-exposed defect is eligible for production change only if the underlying Kriya problem is generic and reproducible without privileged benchmark knowledge.

### Why

Prevents engineering Kriya to pass one external task.

### Loop-stop principle

Do not automatically enter endless benchmark/fix/rerun cycles.

A new live benchmark run requires an explicit decision after:

- root cause confirmation;
- generic deterministic reproducer;
- generic fix;
- regression/mutation evidence;
- offline replay where appropriate.

---

## KAD-055 — Evidence-First Precedes Fix-Now

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Use:

`OBSERVE -> MEASURE -> TRACE -> classify -> competing hypotheses -> discriminating check -> CONFIRM -> predict fix effect -> FIX -> reproduce original scenario -> regression/mutation -> remeasure`

### Why

Earlier coding-agent investigations sometimes promoted plausible inference into defects and fixed the wrong mechanism.

### Invariant

Inference is not defect proof.

---

## KAD-056 — Fix Confirmed Defects While Evidence Is Hot; Do Not Default to Backlog

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Once root cause is confirmed and scope is coherent:

`DISCOVER -> REPRODUCE -> ROOT CAUSE -> FIX -> REGRESSION -> MUTATION -> VERIFY -> CLOSE`

Do not defer confirmed defects merely to maintain a backlog unless there is an explicit scope/release reason.

### Relationship to KAD-055

Evidence-First determines **whether** a defect is real.

Fix-Now determines **what to do after confirmation**.

---

## KAD-057 — Verification Is Progressive; Full Certification Is a Batch Boundary

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Use progressive levels:

- **L0** — syntax/import/focused checks after edits;
- **L1** — new tests, mutations, adjacent tests;
- **L2** — broader changed-architecture suite;
- **L3** — full pytest at coherent batch boundary;
- **L4** — release/doctor/scanner/integrity gates;
- **L5** — canary/live/final production matrices.

### Why

Fix-Now should not mean rerunning expensive full certification after every small edit.

---

## KAD-058 — Production Uses a Sealed Runtime Profile

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

Production posture must explicitly require its critical controls and reject incompatible combinations.

Examples include:

- authoritative controller/enforce path;
- candidate isolation;
- time budgets;
- trace/checkpoint persistence;
- required containment;
- no silent host fallback;
- production qualification;
- required brownfield policy.

### Why

Security/correctness controls cannot remain a collection of unrelated opt-in flags in production.

---

## KAD-059 — Production Certification Is Identity-Specific

**Status:** ACTIVE  
**Change class:** PRESERVE

### Decision

A prior certified model/runtime/software identity cannot be silently treated as certification of a newer runtime merely because model weights are unchanged.

### Example

Ollama `0.34.4` should not be described as equivalent to the previously certified `0.34.2` identity without new evidence.

### Invariant

Production claims must name the tested identity and evidence period.

---

# PART XII — CURRENT GRAPHIFY FINDINGS WITHOUT OVERFITTING

## KAD-060 — Canonical Graphify Run Proved Safe Failure but Not External Acceptance

**Status:** ACTIVE FACT / NOT A DESIGN CHANGE

### Evidence

Canonical run:

`20260930T073012-85574393`

Result:

- Kriya terminal status: FAILURE;
- reason: `VERIFICATION_UNRESOLVED`;
- no candidate hash;
- nothing committed;
- repository unchanged;
- external evaluator: 2/5;
- regression: 76/76 green;
- `GRAPHIFY_EXTERNAL_ACCEPTANCE = NOT ESTABLISHED`.

### Architectural interpretation

The run is evidence of two things:

1. Kriya's candidate/commit safety boundaries worked.
2. The Developer was placed in an infeasible context/edit-protocol state.

The first is a strength; the second is a generic defect.

---

## KAD-061 — Brownfield Analysis Worked Better Than the Mutation Handoff in the Graphify Failure

**Status:** ACTIVE FACT / SUPPORTS KAD-017..025

### Evidence

The retry/localization machinery identified relevant members/regions, but rendering downgraded required member bodies to `body_elided` under budget.

The model therefore did not receive exact source required for valid anchored mutation.

### Interpretation

The correct architectural response is:

`analysis -> skeleton -> localization -> exact authoritative source acquisition -> mutation`

not:

- remove analysis;
- remove skeletons;
- blindly dump the whole file;
- give the model benchmark-specific hints.

---

# PART XIII — HISTORICAL STRATEGIES AND NON-INVARIANTS

## KAD-062 — Effective Inference Identity Is Proven at the Provider Boundary

**Status:** ACTIVE (2026-09-30, PROVIDER-CONTRACT-001)  
**Change class:** EXTEND (KAD-029, KAD-030, KAD-031)

### Decision

Provider/runtime settings used for budgeting, qualification, routing or authority must be expressed and/or independently observed at the provider boundary. Desired configuration is not equivalent to effective inference identity.

Each runtime adapter declares, per semantic setting, how it can carry it (per request, server configuration only, observable only, unsupported). Each request is a plan: what was requested, what the provider applies, and how that is known. A setting that cannot be proven either fails closed (production) or is recorded as unverified (never as applied).

### Why

MEASURED on Ollama 0.34.4:

- `/v1` ignored every `options.*` field that Kriya had been recording as identity (`num_ctx`, `top_k`, `think`).
- The served window came from the server environment (65536), not the 32768 Kriya budgeted and qualified.
- The SDK silently retried and inherited proxies.
- An over-window prompt was truncated without an error.

Qualification records and budgets described an identity the provider never ran.

### Invariant

Any model/runtime setting that Kriya records, budgets against, qualifies or treats as authoritative is either proven to reach the provider with the intended semantics, or Kriya fails closed:

- requested, served and budget context are separate facts;
- the budget never grows to a larger served window;
- a smaller served window is refused;
- qualification refuses an identity it cannot verify;
- one Kriya call is one HTTP request, never proxied through inherited environment variables.

### Does not imply

- The native adapter is not the production default until its own identity is qualified.
- A settings mismatch is not a model-quality verdict.
- Server-side pinning (`kriya model pin`) is the mechanism for settings a request cannot carry, not a license to depend on implicit server defaults.

### Change trigger

- A new runtime adapter or provider version.
- Any new setting Kriya budgets or qualifies against.
- Measured provider behaviour that contradicts an adapter's declared support.

### Related

KAD-028, KAD-029, KAD-030, KAD-031, KAD-059. Evidence: `evidence/provider-contract-001/` (pre_fix, batch_a/b/c, live), `handover/PROVIDER_CONTRACT_001.md`.

---

## KAD-H01 — One-File-at-a-Time Generation Is a Stability Strategy, Not a Universal Product Law

**Status:** ACTIVE STRATEGY WHERE APPLICABLE

### Decision

One-file-at-a-time generation was adopted because local models produced malformed multi-file blobs and because compile/validation after small steps improves diagnosis.

### Constraint

Multi-file tasks may still require coordinated work units and atomic acceptance.

Do not turn “one file at a time” into a rule that prevents legitimate cross-owner/coordinated repair.

---

## KAD-H02 — Stable-First Prompt Ordering Is an Optimization, Not Correctness Authority

**Status:** ACTIVE STRATEGY WHERE APPLICABLE

Stable prompt ordering can improve local inference/cache behavior.

It must never prevent inclusion of newer authoritative evidence or create stale-context semantics.

---

## KAD-H03 — LangGraph/OpenClaw Runtime Choices Are Not Canonical Invariants

**Status:** HISTORICAL / VERIFY

Earlier discussions considered LangGraph as a low-level workflow runtime and rejected OpenClaw as the core runtime.

Current Kriya architecture is governed by the deterministic control-plane/state-machine invariants above, not by a mandatory dependency on either framework.

Do not introduce/remove a workflow framework merely because an older discussion mentioned it; verify current source and production value first.

---

# PART XIV — OPEN / PROPOSED DIRECTIONS

## KAD-P01 — Bounded Agentic Read Loop for Developer

**Status:** PROPOSED — NOT YET CANONICAL

### Proposal

After deterministic repository narrowing, allow a Developer to acquire additional read-only exact source through bounded tools such as:

- symbol search;
- references;
- exact file/span read;
- member read;
- grep/search.

Mutation remains controlled by Kriya.

### Motivation

Claude-style coding agents succeed partly because they can discover missing source interactively rather than relying on a perfect precomputed prompt.

### Important historical reconciliation

This proposal must **not** replace deterministic brownfield analysis.

The intended synthesis, if adopted, would be:

`deterministic analysis -> skeleton/localization -> bounded exact reads -> proposal -> deterministic authority`

not:

`model explores repository blindly`.

### Adoption requirement

Must be validated on generic brownfield tasks and local models before becoming ACTIVE.

---

## KAD-P02 — Role-Capability Alignment

**Status:** PROPOSED — POST-GRAPHIFY

Before adopting qwen3.8/qwen3-coder role specialization, trace what each role actually does:

- read-only reasoning;
- structured output;
- tool calls;
- literal argument fidelity;
- mutation;
- review;
- deterministic evidence consumption.

Qualification should require capabilities genuinely exercised by the role, without removing requirements merely to make a chosen model qualify.

---

# PART XV — SESSION-START CONSISTENCY CONTRACT

Before proposing a material Kriya architectural change, the reviewer/assistant/coding agent must answer:

1. **Which KAD decisions govern this subsystem?**
2. **What new measured evidence exists?**
3. **Does that evidence PRESERVE, EXTEND or SUPERSEDE the existing decision?**
4. **If SUPERSEDE, which original assumption is now disproven?**
5. **Is the proposed change generic, or derived from one benchmark/model incident?**
6. **What deterministic reproducer exists?**
7. **What invariant will be stronger after the change?**

If these cannot be answered, do not change the architecture yet.

---

# PART XVI — CURRENT HIGH-LEVEL ARCHITECTURE

The current intended architecture can be summarized as:

```text
USER INTENT
    |
    v
KNOWLEDGE / POLICY / REPOSITORY GROUNDING
    |
    v
STRUCTURED REQUIREMENTS + OBLIGATIONS
    |
    v
DETERMINISTIC BROWNFIELD ANALYSIS
    |        \
    |         -> AST / LSP / ownership / dependencies
    v
PLANNER / ARCHITECT ADVISORY REASONING
    |
    v
AUTHORIZED PLAN / WORK UNITS
    |
    v
STRUCTURAL CONTEXT / SKELETON
    |
    v
LOCALIZATION
    |
    v
EXACT AUTHORITATIVE SOURCE CONTEXT
    |
    v
DEVELOPER PROPOSAL
    |
    v
ISOLATED CANDIDATE
    |
    v
STATIC / COMPILER / TEST / RUNTIME / POLICY EVIDENCE
    |
    +------ failure ------> ATTRIBUTED, PROGRESS-BOUND RECOVERY
    |
    v
TERMINAL OBLIGATIONS SATISFIED
    |
    v
VERIFIED DIGEST
    |
    v
APPROVAL / TRANSACTIONAL COMMIT
    |
    v
COMMITTED DIGEST == VERIFIED DIGEST
    |
    v
AUDITABLE SUCCESS
```

North-star shorthand:

> **INTENT -> STRUCTURED OBLIGATIONS -> EVIDENCE -> AUTHORIZED PLAN -> ISOLATED CANDIDATE -> DETERMINISTIC VERIFICATION -> GLOBAL CORRECTNESS -> TRANSACTIONAL COMMIT -> AUDITABLE SUCCESS**

---

# PART XVII — WHAT MUST NOT BE LOST AGAIN

The following are particularly easy to forget and must be treated as explicit safeguards against architectural drift:

1. **Brownfield analysis and skeletons were introduced because local models failed to identify the correct existing owner/region reliably.**
2. **Graphify did not disprove that architecture; it exposed the missing exact-source handoff from localization to mutation.**
3. **A frontier agent succeeding through interactive exploration does not prove local models should be left to explore a repository blindly.**
4. **Context preparation and mutation authority are different problems.**
5. **Safe failure is necessary but not sufficient; Kriya must still provide a genuine opportunity for the model to succeed.**
6. **The benchmark is a detector, not the design authority.**
7. **New complexity is justified only when it strengthens a generic invariant or closes a measured production defect.**
8. **Model changes must not be mistaken for architecture changes.**
9. **No successful model output can bypass deterministic verification/commit authority.**
10. **When historical rationale and new evidence appear to conflict, reconcile them before recommending a new direction.**

---

# PART XVIII — MAINTENANCE RULES FOR THIS FILE

## Adding a decision

Every new KAD must include:

- Status;
- Decision;
- Why;
- Invariant;
- Does-not-imply / scope guard where useful;
- Change trigger;
- related KAD IDs.

## Changing a decision

Never silently edit the meaning of an older KAD.

Use one of:

- mark old KAD `SUPERSEDED BY KAD-xxx`;
- mark it `ACTIVE — EXTENDED` and create the extension;
- correct factual metadata without changing the decision.

## Evidence references

When a decision is driven by a significant run or incident, record:

- revision;
- run ID;
- model/runtime identity when material;
- exact measured outcome.

## Benchmark discipline

Never add benchmark ground truth, solution hints or evaluator implementation details to this file.

Only generic architectural conclusions belong here.

---

# Appendix A — Current Decision Map

| Area | Canonical decisions |
|---|---|
| Core authority | KAD-001..003 |
| Local/security/tools | KAD-004..007 |
| State/execution | KAD-008..013 |
| Requirements/obligations | KAD-014..016 |
| Brownfield analysis | KAD-017..021 |
| Context/mutation authority | KAD-022..027 |
| Model governance | KAD-028..033, KAD-062 |
| Candidate/commit | KAD-034..037 |
| Verification/recovery | KAD-038..044 |
| Knowledge/CKM/EIE | KAD-045..052 |
| Benchmark/process/certification | KAD-053..059 |
| Current Graphify facts | KAD-060..061 |
| Historical strategies | KAD-H01..H03 |
| Proposed directions | KAD-P01..P02 |

---

# Appendix B — Source Basis for v0.1.0

This reconstruction was assembled from the project's accumulated architecture discussions and currently available project artifacts, including themes and decisions from:

- initial Kriya local-agent architecture work;
- brownfield hardening and ownership/localization discussions;
- MA2/MA3/MA4 decisions;
- MA8/MA9 correctness and recovery architecture;
- PRV brownfield/recovery investigations;
- PRD-001..036 production-hardening program;
- KnowledgeGuard / CKM / Engineering Pack / EIE work;
- model qualification and Qwen tool-protocol investigations;
- PRD-036 production certification and later leak/state-machine hardening;
- Graphify control/preflight/canonical acceptance work through 2026-09-30;
- the canonical Graphify failure analysis that separated skeleton/localization context from exact mutation-authority context.

Project artifacts consulted during reconstruction included architecture/design reviews, the exhaustive implementation instruction set, KnowledgeGuard implementation plan, architecture flow/retry guide, internal briefing materials and Engineering Pack contract reviews.

This file intentionally records **architecture decisions and rationale**, not every implementation detail or historical experiment.

---

**End of v0.1.0**
