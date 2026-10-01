# CORR-016: Authorized Contract Evolution — Architecture Design

Status: DESIGN ONLY. No production code changes. Not implemented. Not authorized for
implementation until this document is reviewed and explicitly approved.

**Revision 2 (2026-09-08, same day)**: Revision 1's DERIVED-authorization rule was reviewed and
found unsound — it let the Developer's own candidate serve as the primary evidence for its own
mutation's necessity, which proves only that a chosen mutation is internally consistent, never
that it was required. Revision 2 replaces that rule, re-reads PRV-08's actual frozen authoritative
goal text (never inferred from the Planner plan or Developer candidate), and finds the real
scenario does **not** explicitly require `CustomerSummary`'s own public contract to change at
all — the correct behavior is preservation, not mutation. All changes are marked inline; §12 is
fully rewritten. See the changelog at the end of this document for a section-by-section diff
summary.

Design date: 2026-09-08. Author: Claude (this session), following an architecture-extension
authorization scoped strictly to design work (no production code, no P9, no live LLM).

---

## 1. Problem statement

`find_brownfield_public_api_changes()` (`kriya/workflow/file_resolution.py`) is Kriya's
deterministic guard against a model silently mutating an established public contract during
brownfield repair. It is deliberately fail-closed: a removed public signature that is still
referenced anywhere in the workspace is rejected, with one narrow, pre-existing escape hatch —
`_goal_explicitly_requests_api_change(goal)`, a regex over the goal text for explicit
migration language ("rename the public API", etc.).

P9 (2026-09-08, live production run of PRV-08) FAILED: the Developer's candidate for subtask s2
mutated `CustomerSummary`'s public record signature (adding a `region` component) after upstream
subtask s1 correctly extended `CustomerRecord` with that field, and the guard rejected it.

**The frozen authoritative goal, quoted in full** (`goal.md`, PRV-08 hardened fixture —
`/PRV-08-contract-evolution/hardened/goal.md`, never inferred from the Planner plan or the
Developer candidate, per this revision's own review requirement):

> Extend the existing `CustomerRecord` contract with a new required field named `region`.
>
> Requirements:
> - Preserve all existing contract fields.
> - Update the provider implementation.
> - Update all affected consumers.
> - Revalidate every downstream component whose assumptions are affected.
> - Preserve unrelated behavior.

Read literally: the requirement explicitly names **`CustomerRecord`** as the contract being
extended, and explicitly names the change (`region`, required, new field). It applies "update"
and "revalidate" — not "extend"/"add a field to"/"expose" — to consumers and downstream
components. Nowhere does it name `CustomerSummary` or say any downstream component's *own public
contract* must change. It also says, twice, "preserve" (existing contract fields; unrelated
behavior) and never says "preserve" is conditional on convenience.

**`PRV08_CUSTOMER_SUMMARY_CONTRACT_CHANGE: NOT_EXPLICITLY_REQUIRED`** — not merely ambiguous:
the text consistently applies mutation language (`extend`, `new required field`) only to
`CustomerRecord`, and consistently applies non-mutation language (`update`, `revalidate`,
`preserve`) to everything downstream. "Update all affected consumers" is satisfied by a consumer
whose *implementation* correctly continues to work against the extended `CustomerRecord` — it
does not, on its own, say a consumer's *own public signature* must also gain the new field. This
reading is reinforced, not contradicted, by "Preserve unrelated behavior" and "Preserve all
existing contract fields."

This means the real P9 defect is **not** "the guard wrongly rejected a plan-required downstream
evolution." The correct diagnosis, confirmed by the authoritative text alone: the Developer's
candidate chose an unnecessarily invasive implementation (mutating `CustomerSummary`) when the
goal did not require it, and very plausibly when a preserving implementation was available (see
§12 — in the real fixture shape, `SummaryService.summarize(CustomerRecord r)` only reads
`CustomerRecord` via accessor methods, never constructs it positionally, so `CustomerRecord`
gaining a component does not by itself force any change to `SummaryService`'s or
`CustomerSummary`'s own signatures). The guard's rejection of that specific candidate was
**correct**. The actual production gap is that Kriya's retry/repair loop did not converge on a
preserving candidate before the run's retry budget was spent — a materially different, and in
this specific case likely *smaller*, problem than "Kriya has no way to authorize legitimate
downstream evolution."

This document still designs the authority mechanism (some goals genuinely do require a named
downstream contract to evolve — §5's Direct case, and rare §5 Derived cases), because that
mechanism is real and independently useful. But it no longer assumes PRV-08 itself needed it, and
it is now explicit that **preservation is the default outcome whenever necessity is not
independently, deterministically proven** — never "whenever the Developer's candidate happens to
be internally consistent."

A same-day fix attempt (`compute_authorized_contract_evolutions()`) wired `requires`/`provides`
and `completed_subtask_ids` into an allowlist of "authorized owners" and was caught, by a direct
integration test, silently authorizing an **unrelated, incidental** single-symbol rename riding
in the same candidate batch. Root cause: the allowlist was the current subtask's entire legal
write scope, and `Subtask.requires`/`provides` are file-agnostic string tokens with no binding
to a specific file or symbol anywhere in `plan_schema.py`. The fix was fully reverted. A
Revision 1 design (this document, first pass) then proposed a DERIVED-authorization rule keyed
on "every in-scope caller was updated in the same batch, consistent with the new symbol" — this
revision found that rule unsound too, for a structurally identical reason: it let the candidate
that introduces the mutation also serve as the evidence that justifies it. See
`docs/assurance/KRIYA_PRODUCTION_RISK_REGISTER.md`'s `CORR-016` row for the complete history.

## 2. Current-state gap (what already exists, and what's missing)

Already present and reused, unmodified, by this design:

| Structure | What it gives us |
|---|---|
| `AttemptContext.grounding_goal` (`attempt.py:510`) | The raw, unmediated top-level user request string, already kept separate from Planner-authored text (`build_subtask_goal_text()`'s "Authoritative Goal" vs. "Planned Implementation Strategy" split, PRV-11 2026-08-30). The one genuine SOURCE=USER field in the pipeline. |
| `Subtask.requires` / `.provides` / `.relevant_global_invariant_ids` | Planner-declared dependency tokens — real, structurally validated by `plan_validation.py`, but STRATEGY_ONLY: they prove a dependency *exists*, never that a mutation is *permitted*. |
| `completed_subtask_ids` | Computed live from `approved_stage_states` in `WorkflowController` — the one genuinely non-Planner-asserted fact already threaded to both call sites that matter. DETERMINISTIC_DERIVED. |
| `ObligationLedger` / `ObligationRecord` / `ObligationAuthority` (`obligations.py`) | A working, precedence-ordered (DETERMINISTIC > GROUNDED > JUDGMENT), revision-aware, regression-detecting ledger, already threaded through `AttemptContext.obligation_ledger` and the plan-repair loop. `ObligationKind.SUBTASK_SEMANTIC_CONTRACT` already records, DETERMINISTIC, that a `requires`/`provides` edge is structurally real and unambiguous (not merely asserted) — the strongest existing AFFECTEDNESS signal, produced by `plan_validation.py`'s own existing checks. |
| `find_brownfield_public_api_changes()` symbol identity (`_normalized_public_signatures()`) | Already computes a normalized, language-aware signature identity per public symbol — the exact identity primitive a new authorization needs, with no reinvention. |
| `find_brownfield_public_api_changes()` candidate overlay (Step 6, kept from the CORR-016 investigation) | A consumer file updated in the same candidate batch is evaluated on its own new content, not stale on-disk text. **Revision 2 note**: still correct and still kept, but its role is now narrower — it is used only to confirm a mutation is *not stale/incomplete*, never as evidence that the mutation was *necessary* (§3). |
| `AuthorizedFileWriter` / `WriteScopeMode` / `ctx.expected_files_upfront` | The real, already-enforced legal-write-scope boundary. An authorization's `legal_scope` must be checked against this, never treated as a second, competing scope mechanism. |
| `RepairContract` (MA9) | Cross-file repair transactions — explicitly out of scope here (§8 explains why this design does not touch it). |

Confirmed missing, by direct grep and by the P9 incident itself:

- No `ChangeContract`/`change_contract`/`authoritative_requirement`/`ContractDelta` type
  exists anywhere in the codebase.
- No `ObligationKind` represents "an authoritative requirement permits this specific downstream
  public-API delta." The closest, `SUBTASK_SEMANTIC_CONTRACT`, answers a different question
  (edge validity, not mutation permission).
- No mechanism binds `grounding_goal` (SOURCE=USER, AUTHORITATIVE) to a specific
  `(file, symbol, change category)` triple. The existing `_goal_explicitly_requests_api_change`
  escape hatch is goal-wide and symbol-blind — it either exempts the entire candidate batch's
  goal check or does nothing; it cannot express "this one field on this one record."
- **(Revision 2)** No deterministic mechanism exists, or is proposed to exist, capable of
  proving in the *general* case that a downstream public contract *cannot* be preserved. This
  is not a missing-wiring gap — it reflects a real limit (§3): whether an alternative,
  preserving implementation exists is in general undecidable from static evidence alone, and
  this design does not pretend otherwise.

This is the confirmed gap this document closes.

## 3. Three proofs, kept strictly separate

**PROOF A — DIRECT AUTHORITY.** The user explicitly authorizes a protected contract delta —
"Add `region` to `CustomerRecord`." Grounded entirely in `grounding_goal`; may create a DIRECT
`ContractEvolutionAuthorization` (§5).

**PROOF B — AFFECTEDNESS.** Repository/dependency evidence proves another component is
affected — `SummaryService` consumes `CustomerRecord`. This creates an obligation to
*revalidate/repair* `SummaryService` (already representable via `SUBTASK_SEMANTIC_CONTRACT` +
`completed_subtask_ids`). **It does not authorize `CustomerSummary`'s public API to change** —
restated, unchanged from Revision 1, and now more firmly grounded by §1's finding that the real
PRV-08 text itself only ever asks for revalidation, not mutation, downstream.

**PROOF C — DERIVED MUTATION NECESSITY.** Independent evidence that satisfying the authoritative
requirement *requires* a specific downstream protected contract to evolve — independent meaning,
explicitly, **not the Developer candidate under evaluation**.

### Auditing Revision 1's preservation-infeasibility rule

Revision 1 proposed: parent authorization active + dependency edge real + current candidate has
a violation + every in-scope caller updated to the new symbol + category propagation permits it
→ derived authorization granted.

**Does this prove COMPATIBILITY_AFTER_MUTATION or MUTATION_NECESSITY? It proves only
COMPATIBILITY_AFTER_MUTATION.** Every one of those five conditions is a fact about the *chosen*
candidate's own internal consistency (did the model finish what it started, are all its own
callers now aligned with its own new shape) — none of them is a fact about whether a *different*
candidate could have satisfied the same requirement without the mutation.

**Counterexample, worked through exactly as asked**: `CustomerRecord` adds `region`. Developer
changes `CustomerSummary(id, name)` → `CustomerSummary(id, name, region)` and updates every
caller consistently. Revision 1's five conditions: parent ACTIVE (yes) · edge real (yes) ·
violation present (yes, by construction) · every in-scope caller updated (yes, the Developer did
this) · category matches (yes, ADD→MODIFY is in the fixed table) — **all five hold**. Revision 1's
algorithm **would issue authorization**. But an alternative valid implementation — leaving
`CustomerSummary(id, name)` untouched (the real fixture's `SummaryService` never needed `region`
at all, per §1/§12) — also satisfies the goal. Revision 1's rule cannot distinguish these two
worlds, because it never looks at the alternative at all; it only ever examines the one candidate
handed to it.

**`CURRENT DERIVED-AUTHORITY RULE SOUND: NO`. `CANDIDATE USED TO CREATE ITS OWN AUTHORITY: YES`.**
Confirmed exactly as suspected. Revision 1's Proof C is deleted; §5 (Derived) is rewritten below.

### Adopted invariant (§4 of the review)

**A `ContractEvolutionAuthorization` must exist, or be independently derivable from evidence that
does not include the candidate under evaluation, *before* that candidate's protected delta is
accepted.** Candidate contents may prove implementation correctness, consumer compatibility, and
absence of remaining stale consumers (this is exactly what the candidate-overlay check, §2/§11,
still legitimately does) — they must never, on their own, create the authority permitting their
own mutation.

## 4. `ContractEvolutionAuthorization` — typed structure (Revision 2: provenance split from authority)

A frozen (immutable-once-created) dataclass, JSON-serializable.

```python
@dataclass(frozen=True)
class ContractEvolutionAuthorization:
    authorization_id: str
    source_requirement_id: str
    provenance: "AuthorizationProvenance"        # DIRECT | DERIVED  -- structural origin
    authority: "AuthorizationAuthority"           # AUTHORITATIVE | DETERMINISTIC_DERIVED
    source_contract_owner: Optional[str]           # required iff provenance == DIRECT
    source_symbol: Optional[str]                   # required iff provenance == DIRECT
    source_change_category: Optional["ChangeCategory"]  # required iff provenance == DIRECT
    affected_owner: str                             # exactly one file path — never a list
    affected_symbol: str                             # one normalized signature identity
    allowed_change_category: "ChangeCategory"        # ADD | MODIFY | REMOVE
    derivation_evidence: Dict[str, Any]
    parent_authorization_id: Optional[str]           # required iff provenance == DERIVED, else None
    legal_scope: "AuthorizationScope"                # {owner: str, subtask_id: str}
    plan_revision: str                               # fingerprint, see §10
```

**Revision 2 change, per the review's §7**: the single Revision-1 field `source_authority`
(`USER_DIRECT | DERIVED`) conflated *where the record structurally came from* with *how strong
its evidence is*. Split into two fields:

- `provenance: AuthorizationProvenance` — `DIRECT | DERIVED`. Purely structural: does this
  record cite a `parent_authorization_id` or not.
- `authority: AuthorizationAuthority` — `AUTHORITATIVE | DETERMINISTIC_DERIVED`. Evidentiary
  strength. A DIRECT record's authority is always `AUTHORITATIVE` (it is grounded straight in
  `grounding_goal`). A DERIVED record's authority is always `DETERMINISTIC_DERIVED`, never
  `AUTHORITATIVE`, regardless of provenance depth (Rule 4, §7).

`source_requirement_id` is retained, unchanged, and — per the review's explicit instruction —
**a DERIVED record keeps its root `source_requirement_id` unchanged through the whole chain**
(not re-pointed at its immediate parent's own id), so any record can be traced to the original
authoritative requirement in one hop, without walking `parent_authorization_id` links, while
`parent_authorization_id` still carries the immediate lineage for revalidation purposes (§10).

Field-by-field (only fields that changed from Revision 1 are re-justified; the rest are
unchanged, see Revision 1 text preserved in the changelog):

| Field | Why required | Source of truth | Authority level | Persisted? |
|---|---|---|---|---|
| `authorization_id` | Stable, deterministic identity from `(affected_owner, affected_symbol, allowed_change_category, plan_revision)` — never LLM free text. | Derivation function. | N/A. | Yes. |
| `source_requirement_id` | Points to the one authoritative fact that makes the record valid at all, unchanged across the whole derivation chain. | DIRECT grounding check (§5) for the root; copied verbatim into every descendant. | AUTHORITATIVE. | Yes. |
| `provenance` | Structural classification only — see split above. | Computed at creation. | N/A. | Yes. |
| `authority` | Evidentiary tier — see split above; this is the field Rule 4 actually constrains. | Computed at creation, fixed per §5. | N/A (a classification of authority, not itself an authority claim). | Yes. |
| `source_contract_owner`/`source_symbol`/`source_change_category` | DIRECT-only; unchanged from Revision 1. | Grounding check. | AUTHORITATIVE. | Yes. |
| `affected_owner` / `affected_symbol` / `allowed_change_category` | Unchanged from Revision 1 — still the core, still exactly-one-pair, still symbol- and category-bounded. | Grounding check (DIRECT) or the rewritten §5 derivation conditions (DERIVED). | `authority` field above. | Yes. |
| `derivation_evidence` | Unchanged — the structured audit trail; **Revision 2 tightens what may appear here**: for a DERIVED record, this must now include the specific structural-necessity proof (§5), never merely "candidate updated all callers." | Derivation step. | EVIDENCE_ONLY. | Yes. |
| `parent_authorization_id` | Unchanged. | Derivation step. | N/A. | Yes. |
| `legal_scope` / `plan_revision` | Unchanged. | Derivation step. | N/A. | Yes. |

Still explicitly not modeled: no timestamp field (carried by the wrapping record, §8), no
wall-clock expiry (unchanged reasoning from Revision 1). **Revision 2 removes** the
Revision-1 plan to reuse `ObligationStatus` as the record's lifecycle — see §8/§9, storage
location changed.

## 5. Direct vs. derived authorization (Revision 2: both tightened)

### Direct — grounding now requires OWNER, SYMBOL, and CATEGORY independently (Revision 2)

Revision 1's rule ("owner named + a directive verb in proximity") was under-specified: it did
not require the *symbol* itself to be named, so a goal like "update downstream consumers,
including `CustomerSummary`" could have matched "owner named (`CustomerSummary`) + directive
verb (`update`)" and produced a DIRECT record with an undefined symbol/category. **Corrected
rule: a DIRECT authorization requires three independent, deterministic textual groundings against
`grounding_goal` — owner, symbol, and category — not two.**

1. **Owner**: the resolved owner's real identity (class/record/basename, known from real repo
   content post-Architect) is named in `grounding_goal`.
2. **Symbol**: a specific member/field/method name is named in `grounding_goal`, in the same
   clause as the owner (e.g., "...with a new required field named `region`" — "region" is the
   symbol).
3. **Category**: a directive verb from the fixed vocabulary (add/extend/introduce/change/modify/
   rename/remove/delete/require) is present in the same clause, mapped to `ADD`/`MODIFY`/`REMOVE`
   by a small fixed table (add/extend/introduce/require → `ADD`; change/modify/rename → `MODIFY`;
   remove/delete → `REMOVE`).

All three must be satisfiable from the **same clause** of `grounding_goal` — not merely present
somewhere in the whole text. If any one is missing, **no DIRECT authorization is created for
that owner**, full stop (fail-closed default, §15; Rule 9).

Worked example, PRV-08's actual clause: *"Extend the existing `CustomerRecord` contract with a
new required field named `region`."* — owner=`CustomerRecord` (named) ✓, symbol=`region` (named,
"a new required field named region") ✓, category=`ADD` ("Extend... with a new... field") ✓ → DIRECT
authorization: `owner=CustomerRecord.java`, `symbol=region`, `category=ADD`,
`provenance=DIRECT`, `authority=AUTHORITATIVE`.

Contrasting non-example, exactly as the review specified: *"Update all affected consumers."* —
no owner named, no symbol named (only a generic noun, "consumers") → **grounds nothing**. It
cannot produce `owner=CustomerSummary, symbol=region, category=ADD` no matter how the directive
vocabulary is widened, because the symbol and owner are simply absent from the text. This is the
correct, intended outcome, not a gap.

Why not A (pure deterministic extraction) alone, why not C (a new requirement-analysis stage) for
this extension: unchanged from Revision 1 — see the retained discussion in §19 (Risks).

### Derived — rewritten (Revision 2). Reserved for a narrow, structurally-provable case; the common case gets no derived authorization at all.

Given §3's finding, Proof C cannot, in general, be established from repository evidence alone —
whether a preserving implementation exists is not decidable by static inspection in the general
case (it is asking to prove a negative existence claim over the space of possible
implementations). This design does not pretend to solve that in general. It defines the one
narrow class of case where necessity **is** genuinely, deterministically provable, independent of
any candidate, and treats every other "affected but not explicitly named" case as **preservation
required, no derived authorization available** (§5's Option C, adopted as the default).

**The one soundly-derivable case: compiler-forced signature incompatibility against a contract
the affected owner does not itself own.** Concretely: `affected_owner` implements or overrides an
interface/abstract method/protocol whose *own* signature is declared elsewhere (not owned by
`affected_owner`) and is itself part of an already-ACTIVE, DIRECT or DERIVED authorization chain
that changed that declaration's shape. In that case, `affected_owner`'s override is **statically,
compiler-provably** unable to type-check against the old signature any more — this is a real
`javac`/interpreter-checkable fact (the parent declaration's own new shape, checked against the
override's declared parameter/return types), not a usage-pattern inference over any particular
candidate's own diff. A derived authorization for `affected_owner` may be created **only** when:

1. A parent exists and is currently ACTIVE (Rule 3), and its own `authority` is `AUTHORITATIVE`
   or `DETERMINISTIC_DERIVED` (i.e., it is itself a validly-established record — chains of
   depth > 2 are allowed structurally but see §19 for why they are expected to be rare in
   practice).
2. `SUBTASK_SEMANTIC_CONTRACT` confirms the structural dependency edge is real and
   `completed_subtask_ids` confirms the parent's owning subtask actually completed (AFFECTEDNESS,
   necessary, never sufficient — Rule 2, unchanged).
3. **The forcing relationship itself is declared, not inferred**: `affected_owner`'s own
   signature is declared, in the *pre-candidate*, on-disk source (i.e., visible before the
   Developer's candidate for this subtask is even generated — from the workspace state as of
   the start of this attempt), as implementing/overriding a contract element that the parent
   authorization's own `(source_contract_owner, source_symbol)` identifies. This is checked
   against real, already-existing code structure — not against anything the candidate proposes.
4. **Symbol- and category-bounded exactly as before** (Rule 5/6) — the derived authorization
   covers only the minimal signature change needed to restore type-compatibility (typically
   `MODIFY`), never an arbitrary elaboration.
5. `authority` is fixed at `DETERMINISTIC_DERIVED` (never `AUTHORITATIVE`) — Rule 4.

**This case does not match PRV-08's own `CustomerSummary`/`SummaryService` shape** — a record
gaining a component does not force any override elsewhere to stop type-checking; nothing in that
fixture implements an interface whose signature the `region` addition touches. Under this
corrected rule, **no derived authorization is created for `CustomerSummary` in the real PRV-08
scenario** — see §12's rewritten walkthrough.

**If conditions 1–5 don't all hold** (which will be the common case for "affected but not
structurally forced" downstream components, exactly like PRV-08's `CustomerSummary`): **no
derived authorization is created.** The observed violation, if any, is rejected exactly as today.
This is not a fallback or a degraded mode — per §3's Option C, **this is the intended, correct
outcome whenever a preserving implementation is possible**, and Kriya cannot in general prove one
doesn't exist just by looking at repository structure. See §11 for what happens next
(instructing the repair loop toward a preserving implementation) and §6/§11 (unattended
fail-closed policy) for what happens when no preserving implementation is ever found within the
retry budget.

## 6. Why the Planner cannot create root authority alone (unchanged in substance, restated for the new derivation rule)

The Planner proposes candidate `(owner, symbol, category)` triples (via `planned_files`,
`provides`, `description`) — this is **strategy**, JUDGMENT-tier at best, STRATEGY_ONLY at
worst. Neither is sufficient to create a DIRECT authorization (only the deterministic
`grounding_goal` grounding check, §5, can) nor to create a DERIVED one (only the deterministic
compiler-forcing check, §5, can). No step in this design asks an LLM "was this API change
intended?" or "was this mutation necessary?" and treats the answer as authority. If a future
iteration wants bounded LLM judgment to *propose* a candidate compiler-forcing relationship for a
human/deterministic check to then confirm, that proposal enters `derivation_evidence` as
supporting context only (`ObligationAuthority.JUDGMENT`-equivalent), strictly below the
deterministic gate — it may inform, never independently create or bypass, an authorization.

## 7. Authority propagation rules (formal, Revision 2)

1. A direct, deterministically-grounded match against `grounding_goal` (owner **and** symbol
   **and** category, §5) may create a DIRECT authorization, `authority=AUTHORITATIVE`.
2. Affectedness alone never creates mutation authority — necessary precondition for *considering*
   a DERIVED authorization, never sufficient (unchanged).
3. Every DERIVED authorization must cite exactly one `parent_authorization_id`, resolvable to an
   ACTIVE record, and must retain the root `source_requirement_id` unchanged (Revision 2 addition,
   §4).
4. A derived authorization's `authority` is always `DETERMINISTIC_DERIVED`, never
   `AUTHORITATIVE`, regardless of the parent's own tier or chain depth.
5. Every authorization is bound to exactly one `(affected_owner, affected_symbol)` pair (unchanged).
6. Every authorization carries an explicit `allowed_change_category`; a match must agree on
   category (unchanged).
7. `legal_scope` can only narrow, never widen, the existing write-scope boundary (unchanged).
8. Any public delta with no matching, ACTIVE, in-scope authorization remains prohibited,
   unconditionally (unchanged).
9. Planner-created strategy can never, by itself, create a root (DIRECT) authorization
   (unchanged).
10. **(Revision 2, replaces prior Rule 10)** A DERIVED authorization may be created **only** from
    the narrow, pre-candidate, compiler-forced-incompatibility evidence defined in §5 — never
    from any property of the candidate under evaluation (Developer output) for the same
    protected delta it would authorize. Candidate contents may confirm compatibility/completeness
    of an *already-created* authorization's own consumption (§11), but must never be the origin
    of the authorization itself.
11. **(Revision 2, new)** Absent an ACTIVE DIRECT or soundly-DERIVED authorization for a specific
    `(affected_owner, affected_symbol)`, the default and required outcome is **preservation**,
    not mutation — the guard rejects, and the retry/repair loop is directed toward a preserving
    implementation (§11), never toward re-attempting the same mutation until it happens to look
    "consistent enough" to pass.

## 8. MA8 integration (Revision 2: not an `ObligationKind`)

**Revision 1 recommendation withdrawn.** The review correctly identifies that an obligation
("something must become true, tracked as SATISFIED/VIOLATED across revisions") and an
authorization ("something is permitted") are different modalities, and that folding the latter
into `ObligationKind` purely to reuse serialization/ledger plumbing is not adequate
justification.

**Revision 2 recommendation: `ContractEvolutionAuthorization` is stored as its own typed
collection, owned by the same `ObligationLedger` *instance*, not modeled as an `ObligationRecord`
or `ObligationKind` at all — referenced by obligations, not represented as one.**

Concretely:

- `ObligationLedger` gains one new attribute, `authorizations: Dict[str, ContractEvolutionAuthorization]`
  (keyed by `authorization_id`), and two small methods, `record_authorization()` and
  `active_authorizations_for(subtask_id)` (returns the subset whose `plan_revision` matches the
  ledger's own current plan-revision marker — §10). No change to `ObligationRecord`,
  `ObligationKind`, `ObligationStatus`, `ObligationAuthority`, or any existing regression-detection
  logic. This is additive to the object, not a redesign of it, and it is still the **same
  instance** already threaded through `AttemptContext.obligation_ledger` and the plan-repair loop
  — explicitly not a second ledger.
- Validity of a `ContractEvolutionAuthorization` is simply: **present in
  `ledger.authorizations` and its `plan_revision` matches the ledger's current plan revision.**
  No separate status enum is needed (§9) — presence-plus-revision-match *is* the status, which is
  simpler than Revision 1's reused-`ObligationStatus` plan, not more complex.
- Where an *obligation* legitimately needs to reference an authorization (for example, if a
  future `SUBTASK_SEMANTIC_CONTRACT`-adjacent obligation wants to record "this edge's downstream
  consequence is already authorized"), it does so by storing `authorization_id` as a plain string
  in its own `evidence` dict — a pointer, not a re-representation. No obligation kind is required
  to change meaning to accommodate this.

Not touched (unchanged from Revision 1): `RepairContract` (MA9), `preserved_references`,
`RECV`/MA9 stage ownership, `PlanValidationResult`, `SpecComplianceAgent`.

## 9. Lifecycle (Revision 2: simplified, no reused obligation-status vocabulary)

- **When created**: DIRECT records, once per plan validation. DERIVED records, lazily, the first
  time a violation is observed **and** the narrow §5 compiler-forcing evidence already exists in
  the on-disk pre-candidate workspace (not "the first time the candidate happens to satisfy the
  old Revision-1 conditions").
- **Who creates / validates**: the same deterministic function, `kriya/workflow/contract_authority.py`
  — no separate validator, no new agent.
- **Active**: `authorizations[id]` exists and `plan_revision` matches current — no explicit
  "pending" state, since validation is the same call as creation (unchanged reasoning from
  Revision 1, restated without the `ObligationStatus` framing).
- **Consumed**: `find_brownfield_public_api_changes()` looks up
  `ledger.active_authorizations_for(current_subtask_id)` by `(owner, symbol)` (§11).
- **Survives same-subtask retry**: yes, unchanged — nothing about retrying the same subtask
  under the same plan revision invalidates a record.
- **On plan repair**: never silently carried — new `plan_revision` means the old record no longer
  matches `active_authorizations_for()`; must be independently re-derived (§10, unchanged).
- **Resume/checkpoint**: re-derived, not restored (§14, unchanged from Revision 1).
- **Invalidated**: plan repair (revision mismatch, §10); parent no longer ACTIVE (checked live at
  lookup time via `parent_authorization_id`, cascades naturally without needing an explicit
  invalidation pass); run-level reset (fresh, empty ledger).

## 10. Plan-repair authority safety (unchanged in substance)

Every `ContractEvolutionAuthorization` still carries `plan_revision`. A new plan revision makes
every prior record fail the `active_authorizations_for()` match (§8/§9) until independently
re-derived. A repair cannot launder broadened authority by resemblance to a prior record — this
is now enforced by the lookup itself (revision must match), not by an obligation-status
transition, but the guarantee is identical to Revision 1's: **REQUIRE REVALIDATION, never
silent inheritance.**

## 11. Brownfield guard integration (Revision 2: lookup source changed, default outcome clarified)

```python
def find_brownfield_public_api_changes(
    workspace_path: str,
    original_contents: Dict[str, str],
    final_contents: Dict[str, str],
    goal: str,
    active_authorizations: Optional[Mapping[Tuple[str, str], ContractEvolutionAuthorization]] = None,
) -> List[Dict[str, Any]]:
```

Unchanged mechanically from Revision 1 (§11 steps 1–7: detect → lookup by `(owner,
removed_signature)` → verify `legal_scope` live → candidate overlay still runs first, confirming
compatibility/completeness only, never necessity → category match → exact per-symbol drop).
`active_authorizations` is now built from `ctx.obligation_ledger.active_authorizations_for(ctx.current_subtask_id)`
(§8), not from `SATISFIED CONTRACT_EVOLUTION_AUTHORIZED` obligations.

**Revision 2 clarifies the default path explicitly**, per the review's §11/§10 requirements: when
no matching authorization exists (the common case now, since §5's DERIVED rule is narrow), the
violation is rejected and the `Failure` raised carries a `reason_code` distinguishing this from an
ordinary incidental rename — e.g. `PROTECTED_CONTRACT_EVOLUTION_NOT_AUTHORIZED` — so the retry
prompt built from it (existing infrastructure, `retry_prompts.py`, not modified by this design)
can be worded to instruct the Developer to find a **preserving** implementation, rather than
generically "restore the removed signature." (Improving that specific retry-prompt wording is
listed as a companion follow-on in §19 — it is a prompt-content change, not an authority-model
change, and is explicitly out of this document's own narrow scope, but it is what actually
determines whether PRV-08-shaped scenarios converge in practice; see §19's own honest caveat.)

**Existing goal-text escape hatch** (`_goal_explicitly_requests_api_change`): unchanged
recommendation from Revision 1 — deprecate and fold into the DIRECT-authorization grounding check
(§5), which is now a strict, *symbol-bounded* refinement of it (owner+symbol+category, not just a
whole-batch verb match).

## 12. PRV-08 walkthrough — rewritten (Revision 2)

Rebuilt from §1's finding: the authoritative text does not require `CustomerSummary`'s own
contract to evolve.

```
User goal (verbatim, goal.md):
  "Extend the existing CustomerRecord contract with a new required field named region.
   Requirements: Preserve all existing contract fields. Update the provider implementation.
   Update all affected consumers. Revalidate every downstream component whose assumptions
   are affected. Preserve unrelated behavior."
  │
  ▼
Plan validated (s1: provides=[updated_customer_record_contract], owns CustomerRecord.java;
                s2: requires=[updated_customer_record_contract], owns CustomerSummary.java/
                    SummaryService.java)
  │
  ▼
derive_direct_contract_authorizations(grounding_goal, plan, repo_symbol_index)
  │  owner="CustomerRecord" named ✓, symbol="region" named ✓, category="Extend...new...field"
  │  → ADD ✓  →  ALL THREE grounded in the SAME clause
  │  → DIRECT ContractEvolutionAuthorization:
  │      affected_owner=CustomerRecord.java, affected_symbol=region, allowed_change_category=ADD,
  │      provenance=DIRECT, authority=AUTHORITATIVE
  │
  │  No other clause of grounding_goal names any other owner or symbol - "Update all affected
  │  consumers" / "Revalidate every downstream component" ground NOTHING (§5): no owner named,
  │  no symbol named. No DIRECT authorization exists for CustomerSummary, SummaryService, or
  │  any other file.
  ▼
s1 executes: CustomerRecord.java gains `region` — matches the DIRECT record exactly, allowed.
s1 completes → completed_subtask_ids ⊇ {s1}.
  │
  ▼  (AFFECTEDNESS only)
SUBTASK_SEMANTIC_CONTRACT: s2.requires matches s1.provides, real edge — s2 is affected, must be
REVALIDATED. Grants nothing toward mutating s2's own owned files' public contracts (Rule 2).
  │
  ▼
Pre-candidate check (before any Developer output for s2 exists): does CustomerSummary.java or
SummaryService.java, AS THEY EXIST ON DISK RIGHT NOW, implement/override any interface or
abstract signature declared elsewhere whose shape the CustomerRecord DIRECT authorization
changed? In the real fixture: SummaryService.summarize(CustomerRecord r) takes CustomerRecord
BY REFERENCE and reads it only via accessor methods (r.customerId(), r.firstName(), ...) - it
never positionally constructs CustomerRecord, and neither CustomerSummary nor SummaryService
implements any interface whose signature involves CustomerRecord's own component list. §5's
narrow compiler-forcing condition (3) is NOT met. → NO derived authorization is even attempted
for CustomerSummary or SummaryService - there is nothing to derive, because nothing forces it.
  │
  ▼
s2's Developer candidate is generated. TWO possible outcomes:

  (a) PRESERVING candidate (the correct one under this design): CustomerSummary(id, name)
      UNCHANGED; SummaryService.summarize() body unchanged or trivially adjusted, still never
      referencing region. Compiles and passes - CustomerRecord's own new field is simply not
      surfaced through this particular consumer, which is consistent with the goal ("update...
      consumers" is satisfied by continuing to work correctly against the extended provider,
      not by mirroring every new field everywhere). find_brownfield_public_api_changes() finds
      NO violation at all (CustomerSummary's signature never changed) - nothing to authorize,
      nothing to reject. RUN PASSES.

  (b) MUTATING candidate (the real P9 attempt): CustomerSummary(id, name) -> (id, name, region),
      SummaryService's call site updated to pass r.region(). find_brownfield_public_api_changes()
      detects the violation. Lookup: no ACTIVE authorization exists for
      (CustomerSummary.java, <record identity>) - no DIRECT record (never named), no DERIVED
      record (compiler-forcing condition never met, §5) -> REJECTED, Failure reason_code
      PROTECTED_CONTRACT_EVOLUTION_NOT_AUTHORIZED. Retry loop is directed toward (a).
  │
  ▼
Unattended bound (§6/§11 of the review, adopted as policy, §"Unattended behavior" below): if the
Developer's retries keep proposing (b)-shaped candidates and the bounded retry budget is
exhausted without ever producing (a), the subtask - and the run - terminates with a precise,
distinct failure: authority/requirement ambiguity for CustomerSummary's own contract, not a
silently-accepted mutation and not a generic "quality gate failed."
  │
  ▼
s2's candidate ALSO renames an unrelated Helper.helperMethod -> Helper.renamedMethod in the same
batch: no authorization exists, no parent, no compiler-forcing evidence - REJECTED,
independently of whatever happens with CustomerSummary (Rule 5, per-symbol matching).
  │
  ▼
terminal verification: same lookup, same rules, over the full final diff.
```

**Expected correct Kriya behavior for PRV-08, under this design: reach outcome (a) — preserve
`CustomerSummary`'s public signature, satisfy the goal's actual text (extend the provider, keep
consumers working, revalidate them), and pass without ever creating a derived authorization.**
If some part of the real fixture genuinely does force `CustomerSummary`/`SummaryService` to
change (not evidenced by `goal.md` alone, and not confirmed here since this analysis was
scoped to the authoritative text as instructed), the correct outcome is either a DIRECT
authorization (if the real goal text used elsewhere in the fixture pack turns out to name it
explicitly — worth confirming against the actual fixture project before implementation, §19) or
a clean, diagnosable authority-ambiguity failure (§11) — never a silent mutation.

## 13. Negative scenarios (Revision 2: #3 and #7 re-justified under the new rule; new #10 added)

| # | Scenario | Outcome | Why |
|---|---|---|---|
| 1 | Planner invents a `requires`/`provides` chain with no real grounding | REJECT | No DIRECT authorization for any owner it touches (Rule 9); matches `test_completed_planner_dependency_cannot_authorize_public_api_change_without_requirement_authority`. |
| 2 | Upstream completed, but the original requirement never authorized any API evolution | REJECT | No DIRECT record ever created for that owner; `completed_subtask_ids` is AFFECTEDNESS only (Rule 2). |
| 3 | Legitimate upstream evolution, downstream public contract *can* remain stable | REJECT the mutation | **(Revision 2)** Now the default outcome whenever §5's narrow compiler-forcing condition is not met — not merely "evidence was absent this time," but "this class of case structurally never produces a derived authorization" (§5). The Developer must find the preserving implementation; this is PRV-08 itself (§12). |
| 4 | Same-file unrelated signature mutation alongside an authorized one | REJECT the unrelated symbol; ALLOW the authorized one | Per-`(owner, symbol)` matching (Rule 5), unchanged. |
| 5 | Unrelated downstream module mutation | REJECT | No chain at all. |
| 6 | Plan repair proposes a new API break | REQUIRE REVALIDATION | `plan_revision` mismatch (§10). |
| 7 | MA9 recovery retries with a broader API change | REQUIRE REVALIDATION | `RepairContract`'s scope widening ≠ mutation authority (unchanged). |
| 8 | User explicitly authorizes removal ("delete the deprecated `legacyGreet` method entirely") | ALLOW | DIRECT authorization: owner+symbol+category all grounded in one clause (§5). |
| 9 | User goal ambiguous about whether the API may change | REJECT (fail closed) | §5 requires all three groundings in the same clause; partial grounding grounds nothing. |
| 10 | **(Revision 2, new — the review's own key negative case)**: user goal = "Add `region` to `CustomerRecord` and update affected consumers"; Planner correctly identifies `SummaryService`; Developer proposes mutating `CustomerSummary` and updating all callers consistently; a valid preserving implementation exists | REJECT the mutation | The candidate's own internal consistency (all callers updated) is exactly Revision 1's unsound signal (§3) — under Revision 2's rule, this alone never creates a derived authorization; §5's compiler-forcing condition is checked against pre-candidate, on-disk structure and is not met by this scenario; the mutation is rejected regardless of how consistently the Developer applied it. |

## 14. Persistence / resume (unchanged in substance; storage location updated)

Unchanged from Revision 1: `ObligationLedger` itself has no checkpoint persistence today
(`ORCH-003`/`STATE-001`, tracked separately); `ContractEvolutionAuthorization` records are
re-derived on resume, not restored, for the same reasons as before (derivation is a pure function
of durable inputs — `grounding_goal`, plan, `completed_subtask_ids`, and now also the *on-disk,
pre-candidate* workspace state for the §5 compiler-forcing check, which is exactly as durable
across resume as the workspace itself). The "derive, don't restore" principle applies identically
to the new `ledger.authorizations` collection (§8) as it would have to the withdrawn
`ObligationKind`-based storage.

## 15. Security property (formal, unchanged)

> A protected public-contract mutation MUST NOT be accepted unless an ACTIVE
> `ContractEvolutionAuthorization` exists whose `(affected_owner, affected_symbol,
> allowed_change_category)` exactly matches the observed mutation, and whose `legal_scope`
> matches the current write boundary and owning subtask.
>
> Absence or ambiguity → reject, always. No partial credit.

**Revision 2 adds, as the operational complement to this property (the review's §11/§15):**

> When a protected mutation is rejected for lack of authorization, and no compiler-forcing
> necessity can be established (§5), Kriya's required behavior is to instruct the repair/retry
> loop toward a **preserving** implementation, not to retry the same mutation hoping it becomes
> authorized. If bounded retries are exhausted without a preserving implementation ever being
> produced, the subtask/run terminates with a distinct, diagnosable authority/requirement
> ambiguity failure. **Authority is never granted merely to obtain convergence.**

## 16. Architecture size constraint — expected production impact (Revision 2: obligations.py change removed, contract_authority.py absorbs it)

- One new module: `kriya/workflow/contract_authority.py` — `ContractEvolutionAuthorization`,
  `AuthorizationProvenance`/`AuthorizationAuthority`/`ChangeCategory`/`AuthorizationScope`,
  `derive_direct_contract_authorizations()`, `derive_contract_evolution_authorization()` (the
  narrow §5 DERIVED function).
- **(Revision 2)** `kriya/workflow/obligations.py`: **no change** — Revision 1's planned new
  `ObligationKind` is withdrawn (§8). `ObligationLedger` itself gains the small
  `authorizations`/`record_authorization()`/`active_authorizations_for()` addition — still in
  `obligations.py` since that's where `ObligationLedger` already lives, but additive to the
  class, not a new enum value or new serialization convention.
- One new, optional parameter on `find_brownfield_public_api_changes()` (unchanged from
  Revision 1).
- Two call-site changes (`attempt.py`, `workflow.py`) — build `active_authorizations` from
  `ctx.obligation_ledger.active_authorizations_for(...)`.
- One new call site for `derive_direct_contract_authorizations()`, post-plan-validation
  (`workflow_controller.py`).
- No persistence layer changes.
- Deterministic tests only.

Explicitly not touched: unchanged list from Revision 1 (`WorkflowController` sequencing,
`RepairContract`/MA9, `PlanValidationResult`, `SpecComplianceAgent`, agent prompts,
`AuthorizedFileWriter`/`WriteScopeMode`).

## 17. Deterministic test plan (Revision 2: #2, #7 rewritten; #10 given the central role)

1. **DIRECT_AUTHORIZATION** — owner+symbol+category all grounded in one clause → exactly one
   record; any one missing → `[]`. Explicitly tests the "update downstream consumers" non-example
   from §5 grounds nothing.
2. **DERIVED_AUTHORIZATION** — **(Revision 2, rewritten)**: constructs the one narrow case §5
   defines (an interface/abstract method override whose declared signature is forced by an
   ACTIVE parent's own change, present pre-candidate) → derived record created; explicitly does
   **not** use "candidate updated all callers" as its evidence.
3. **AFFECTEDNESS_WITHOUT_AUTHORITY** — unchanged from Revision 1.
4. **PLANNER_LAUNDERING** — unchanged; `test_completed_planner_dependency_cannot_authorize_public_api_change_without_requirement_authority` remains the negative control.
5. **SAME_FILE_OVERREACH** — unchanged.
6. **UNRELATED_OWNER** — unchanged.
7. **DERIVED_AUTHORITY_NOT_REQUIRED_IF_PUBLIC_CONTRACT_CAN_BE_PRESERVED** — **(Revision 2,
   promoted to the central case, not a secondary variant)**: this is now scenario #10 from §13 —
   the PRV-08-shaped case itself. Candidate mutates `CustomerSummary` consistently, with every
   caller updated; §5's compiler-forcing condition is not met (nothing on disk pre-candidate
   forces it) → REJECT, regardless of the candidate's own internal consistency. This is THE test
   that must fail against Revision 1's rule and pass against Revision 2's.
8. **PLAN_REPAIR_CANNOT_ESCALATE_AUTHORITY** — unchanged.
9. **RECOVERY_CANNOT_ESCALATE_AUTHORITY** — unchanged.
10. **RESUME_PRESERVES_AUTHORITY_PROVENANCE** — unchanged in intent; re-derivation now exercises
    `ledger.authorizations`/`active_authorizations_for()` instead of an `ObligationRecord` lookup.
11. **AMBIGUOUS_USER_GOAL_FAILS_CLOSED** — unchanged.
12. **PRV08_DETERMINISTIC_VERTICAL** — **(Revision 2, rewritten)**: full `run_attempt()` walkthrough
    of §12's outcome (a) — a preserving candidate for s2 passes with zero authorizations
    consumed; a companion assertion (or paired test) confirms outcome (b) — the real, original P9
    mutating candidate — is still rejected, with `reason_code=PROTECTED_CONTRACT_EVOLUTION_NOT_AUTHORIZED`.
    This replaces Revision 1's test #12, which assumed (incorrectly, per §1) that the mutation
    should be allowed.

## 18. Implementation sequence (Revision 2: obligations.py step narrowed)

Unchanged ordering from Revision 1, with step 3 corrected:

1. `contract_authority.py`: types + `derive_contract_evolution_authorization()` (§5's narrow
   compiler-forcing function) + unit tests (#2, #5, #6, #7).
2. `derive_direct_contract_authorizations()` + unit tests (#1, #11).
3. **(Revision 2)** `ObligationLedger.authorizations`/`record_authorization()`/
   `active_authorizations_for()` — additive methods on the existing class, not a new
   `ObligationKind`.
4. `find_brownfield_public_api_changes()`'s new parameter + lookup/match logic + unit tests
   (#3, #4, #6).
5. Wire both existing call sites + the one new plan-validation call site — integration test #12.
6. `plan_revision` fingerprinting + revalidation — tests #8, #9.
7. Resume re-derivation confirmation — test #10.
8. Migrate `_goal_explicitly_requests_api_change` (deprecate-and-fold) — last, independently
   revertible.
9. Full regression pass — this file, `test_workflow_controller*.py`, `CORR-008/009/010`, full
   `pytest`.

Each step independently testable and revertible.

## 19. Risks / tradeoffs (Revision 2: rewritten, more caveats)

- **The corrected DERIVED rule (§5) is much narrower than Revision 1's.** Most "affected but not
  explicitly named" downstream components — very plausibly including PRV-08's own
  `CustomerSummary` — will now get **zero** derived authorization, by design. Whether PRV-08 (and
  scenarios shaped like it) actually converges to a PASS depends on whether Kriya's repair/retry
  loop reliably steers the Developer toward the preserving implementation (§11/§12's outcome
  (a)) within the retry budget — **this document does not design that steering**; it only
  specifies that the guard must keep rejecting until either a preserving candidate is found or
  the run fails closed with a distinct reason. Improving the retry-prompt wording so the
  Developer is actively pointed at "keep this signature, the goal doesn't require changing it" is
  flagged as a necessary **companion change**, not covered here, and its absence is a real risk
  to real-world convergence even after this design ships correctly.
- **§5's narrow, compiler-forced-incompatibility case may turn out to be rare or empty in
  practice** — it is possible no real incident ever exercises it, in which case (matching this
  codebase's own "add only when a live incident demonstrates the need" discipline) it may be
  reasonable to defer implementing DERIVED authorization at all in a first cut, shipping DIRECT
  authorization alone plus the corrected (rejecting) default. This is worth an explicit go/no-go
  decision at implementation time, not assumed here.
- **This analysis (§1) rests on `goal.md` alone**, per the review's own instruction not to infer
  from the Planner plan or Developer candidate. It has not independently re-confirmed whether the
  real PRV-08 fixture repository (`M1`/`M2`/`M3` modules) contains some OTHER piece — e.g., an M3
  consumer that genuinely can't compile without `CustomerSummary` carrying `region` — that would
  make `CustomerSummary`'s evolution structurally forced after all. If implementation review of
  the real fixture finds such a forcing relationship, §5's narrow compiler-forcing case is exactly
  the mechanism that should catch it; if it doesn't, §12's outcome (a) is the correct, expected
  result. This should be confirmed against the real fixture files before or during implementation,
  not assumed either way from this design pass alone.
- Unchanged from Revision 1: the DIRECT grounding check's false-negative rate on loosely-phrased
  goals (now compounded by the stricter three-part grounding, §5 — accepted, same fail-closed
  reasoning); the indirect safety dependency of `plan_revision` fingerprinting catching an
  Architect owner-identity change; this design does not solve `ORCH-003`/`STATE-001`'s
  checkpoint-persistence gap.

## 20. Explicit non-goals (unchanged, plus one addition)

Unchanged from Revision 1 (not a general Change Contract system; no new orchestration
stage/agent/LLM call in the gate itself; no `WorkflowController`/`RepairContract`/
`PlanValidationResult` redesign; not a fix for `ORCH-003`/`STATE-001`; not a claim of `CLOSED`
without implementation + tests + a real P9 rerun).

**Added (Revision 2)**: **not** a design for the retry/repair-prompt wording that steers the
Developer toward a preserving implementation once a mutation is rejected — flagged in §19 as a
necessary, separate companion change for PRV-08-shaped scenarios to reliably converge in
practice, deliberately left out of this document's own narrow scope (prompt content, not
authority model).

---

## Changelog (Revision 1 → Revision 2)

- §1: added the frozen `goal.md` quote and the `NOT_EXPLICITLY_REQUIRED` finding; reframed the
  real P9 root cause as an unnecessarily invasive Developer candidate plus insufficient
  retry-loop steering, not solely a missing-authorization gap.
- §3: new section — the three-proofs framework (A/B/C), the soundness audit of Revision 1's
  DERIVED rule (found unsound), and the worked counterexample.
- §4: split `source_authority` into `provenance` + `authority`; clarified `source_requirement_id`
  is retained unchanged through the whole derivation chain.
- §5 (Direct): tightened to require owner + symbol + category all grounded in the same clause
  (was: owner + directive verb only).
- §5 (Derived): fully rewritten — Revision 1's candidate-based "preservation-infeasibility
  evidence" rule replaced with a narrow, pre-candidate, compiler-forced-incompatibility rule;
  documented that the common case now yields no derived authorization at all, by design.
- §7: Rule 10 rewritten (candidate may never be the origin of its own authorization); new Rule 11
  (preservation is the required default).
- §8: withdrew the `ObligationKind.CONTRACT_EVOLUTION_AUTHORIZED` recommendation; recommends an
  additive `authorizations` collection directly on `ObligationLedger` instead.
- §9: lifecycle simplified accordingly (no reused `ObligationStatus` vocabulary; validity =
  presence + revision match).
- §11: clarified the default-rejection path's `reason_code` and its relationship to
  (out-of-scope) retry-prompt wording.
- §12: fully rewritten around the corrected finding — `CustomerSummary` preserved by default;
  both outcomes (preserving pass, mutating reject) walked through explicitly.
- §13: scenario #3 re-justified under the new default; new scenario #10 added (the review's own
  key negative case).
- §15: added the operational "preservation over convergence" complement to the security property.
- §16/§18: `obligations.py` change narrowed from "new ObligationKind" to "additive ledger
  methods."
- §17: tests #2, #7, #12 rewritten to match the corrected DERIVED rule and PRV-08 outcome.
- §19: substantially rewritten — new, more honest risk about retry-loop convergence being outside
  this document's scope, and an open question about whether the real fixture might contain a
  genuine forcing relationship not visible from `goal.md` alone.
- §20: added the retry-prompt-wording non-goal.
