# VAL-001 G1 remediation design (D1/D2/D3)

Status: DESIGN ONLY. Nothing in this document has been implemented. No Kriya production code or
tests were changed to produce it. STANDARD risk lane: investigate + design.

Baseline: Kriya `10b5523` + G0 (`0d29124f`) + G1 setup/evidence (`719c4f5b`, `a41365c`). Graphify
`67f99bd0059dd1bac9e44382907ef9f10098b39f`, run trace `8b6ee803`.

All source citations below were read directly from the current Kriya tree at this baseline; none
were inferred from G1's forensic report alone.

## Summary

G1's `CONTEXT_CORRECTNESS_GAP` traces to one specific, narrow chokepoint that both D1 and D2 share:
`kriya/workflow/operations.py::operation_for_attempt()` decides between whole-file replacement and
anchored/patch editing using only `mode: str` and `has_prior_failure: bool` — it has **zero**
visibility into whether the model was actually shown complete, current source for the file it's
about to rewrite wholesale, and zero visibility into *which sub-phase* of API-contract recovery is
running. D3 is a structurally separate defect, in a different subsystem
(`find_brownfield_public_api_changes()` / `kriya/workflow/file_resolution.py`), that treats any
`def` statement — nested or module-level — as a "public signature," and treats a bare repo-wide
text match as "evidence" of a cross-file dependency, with no reachability check. None of the three
require a new editing framework, a new recovery framework, or a new semantic graph — every fix
below extends an existing, already-in-production mechanism at the exact point it currently stops
short.

---

## D1 — Edit authorization vs. source completeness

### Trace

```
context/source projection          kriya/workflow/context_budget.py::build_known_target_context()
                                    → kriya/workflow/context_package.py::ContextItem (tier, is_exact,
                                      revision, omitted_regions — CTX-001 P1 WP3, already exists)
        ↓
tier/completeness                  ContextItem.tier ("full"/"skeleton"/"signatures"/"member_exact"),
                                    ContextItem.is_exact (bool) — computed by build_known_target_context
                                    / _fit_whole_file(), NEVER read again after this point
        ↓
edit protocol selection            kriya/workflow/operations.py::operation_for_attempt(mode,
                                    has_prior_failure) → kriya/workflow/attempt.py::_operation_map()
                                    → operation_for_file(attempt_operation, file_exists)
                                    — inputs: mode string, has_prior_failure bool, file_exists bool.
                                    NO ContextItem, NO tier, NO is_exact, NO omitted_regions ever
                                    reaches this function.
        ↓
Developer                          kriya/agents/agent.py:1598-1745 — reads operation_by_file[filepath],
                                    branches system-prompt text on requested_operation_value alone
        ↓
AuthorizedFileWriter                kriya/policy/filesystem.py::commit_file(..., expected_revision)
                                    — never reached in the G1 failure; candidate gates reject first
        ↓
candidate gates                    ownership_gate / find_brownfield_public_api_changes() —
                                    kriya/workflow/attempt.py:4862, kriya/workflow/workflow.py:3086
```

### D1_ROOT_CAUSE

`operation_for_attempt()`/`operation_for_file()` (`kriya/workflow/operations.py:73-102`) authorize
`CREATE_FULL_FILE`/`REPAIR_WITH_FULL_FILE` — i.e., "the model's returned text becomes the entire
new file content, verbatim" — using only `mode`, `has_prior_failure`, and `file_exists`. The
`ContextItem`/`ContextPackage` CTX-001 P1 already computes for the exact same file
(`build_known_target_context()`, `kriya/workflow/context_budget.py:949-1175`) already carries
`tier`, `is_exact`, `revision`, and `omitted_regions` — precisely the signal needed — but nothing
downstream of that function ever reads it again for authorization purposes. Confirmed via direct
inspection: does edit authorization currently know...

| Signal | Currently known to `operation_for_attempt`/`operation_for_file`? |
|---|---|
| existing vs. new file | YES (`file_exists` param) |
| authoritative current-source revision | NO |
| full-source availability | NO |
| `ContextItem.tier`/`is_exact` | NO |
| omitted regions | NO |
| member hints | NO |

### D1_OWNER

`kriya/workflow/operations.py` (the authorization decision itself) jointly with
`kriya/workflow/attempt.py` (the only place that has both the `ContextPackage` for the target file
and the call sites into `operation_for_attempt`/`_operation_map`, in the same function scope).

### D1_EXISTING_MECHANISM (extend, do not replace)

- `ContextItem.tier`/`is_exact`/`omitted_regions`/`revision` — CTX-001 P1 WP3, already computed,
  already attached to every known-target file's context entry. Nothing new to build.
- `REPAIR_WITH_PATCH` / `apply_anchored_edits` — already exists, already self-defending (an anchor
  that doesn't match the real current content exactly once raises, per `CLAUDE.md`'s own
  documented behavior) — the natural safe fallback when full-file replacement isn't authorized.
- `_operation_map()`'s existing per-file override pattern (`kriya/workflow/attempt.py:276-293`) —
  it already downgrades/upgrades a file's operation based on retry state (`anchor_failure_counts`);
  this is the precedent to extend, not a new mechanism.
- `AuthorizedFileWriter.commit_file(..., expected_revision)` — already revision-aware at the real
  commit boundary; not the right enforcement point for this invariant (candidate gates already
  reject before this is ever reached), but confirms the codebase already treats "revision" as a
  first-class authorization concept elsewhere, supporting reusing `ContextItem.revision` the same way.

### D1_DESIGN

**Deterministic invariant**: for an EXISTING file, `CREATE_FULL_FILE`/`REPAIR_WITH_FULL_FILE` MUST
NOT be the operation authorized for that file unless the `ContextItem` supplied to the model for it
has `tier in ("full", "member_exact")`, `is_exact is True`, `omitted_regions is False`, and
`revision` matches the file's actual current on-disk revision at generation time. File size is
never consulted directly — only whether the model was actually shown complete, current, exact
content (a large file with `tier="full"` legitimately authorizes full-file replacement; a small
file skeletonized for an unrelated budget reason legitimately does not).

**Minimum change** (Disposition **B**, "force targeted/anchored edit when context is partial",
selected over A and C — see rationale below):

1. Thread the target file's `ContextItem` (or just its `tier`/`is_exact`/`omitted_regions`) into
   `_operation_map()` (it already has `ctx`, which already has access to the same context-building
   machinery `build_known_target_context()` populates) as an additional, optional parameter.
2. Add one more override branch in `_operation_map()`, alongside the existing
   `anchor_failure_counts` branch: when the resolved operation for a file is
   `CREATE_FULL_FILE`/`REPAIR_WITH_FULL_FILE`, the file exists, and its `ContextItem` is not exact
   (`is_exact is False` or no exact-tier `ContextItem` was found for it at all), downgrade to
   `REPAIR_WITH_PATCH`.
3. In `kriya/agents/agent.py`, `prefer_anchored_edit` (`kriya/agents/agent.py:1662-1669`) already
   evaluates True once `requested_operation_value == "repair_with_patch"` regardless of
   `apply_fix_analysis` — so step 2's output does reach this existing branch mechanically. **But
   verified against the actual prompt text this branch sends** (`agent.py:1749-1766`): it is
   unconditionally prefixed with "This is a RETRY: the previous attempt at this file failed the
   error described in the Task section above" — true for today's only caller of this branch
   (`apply_fix_analysis=True`, a genuine retry), **false** for D1's cold-first-attempt case (no
   prior error exists yet). This is a real, necessary correction bundled with the invariant, not a
   separate "prompt fix as the primary remedy": the instruction text must branch on whether a prior
   error actually exists (two short variants of the same instruction — "this file is large enough
   that only a localized change is safe" for the context-insufficiency trigger, vs. today's
   existing retry wording — selected by whether `apply_fix_analysis` is true), so the model is
   never told a factually false thing about why it's being asked for a patch.
4. **Close the accept-full-file-as-fallback hole.** `validate_operation_result`
   (`kriya/workflow/operations.py:118-150`) deliberately allows a `REPAIR_WITH_PATCH` request to
   resolve as `REPAIR_WITH_FULL_FILE` if the model decides broader restructuring is needed — correct
   and wanted for an *ordinary* targeted retry, where the model has already been shown real current
   content and a full-file fallback is still safe. It is **not** safe when `REPAIR_WITH_PATCH` was
   mandated by this invariant specifically (context insufficient / recovery phase = `REPAIR_
   BEHAVIOR`), because the model was never shown content complete enough to safely produce that
   fallback. `validate_operation_result` itself is not changed (its existing behavior stays correct
   for the ordinary case). Instead, the caller (`attempt.py`, which already knows — because it
   computed the downgrade — which files were invariant-mandated rather than merely
   preference-mandated) tracks that reason per file (the same shape as the existing
   `anchor_failure_counts` per-file dict `_operation_map` already threads through state) and, after
   generation, treats a `REPAIR_WITH_FULL_FILE`-shaped result for an invariant-mandated file as a
   validator failure — feeding the *existing* candidate-rejection path (the one G1's own
   `ownership_gate` already uses), not a new rejection mechanism. This is what makes the invariant
   actually deterministic rather than advisory: the model can still choose to write `FILE CONTENT:`
   instead of `SEARCH:`/`REPLACE:`, but that choice no longer results in an authorized write.
5. **Fail-closed edge case** (Disposition C, narrowly): if no anchor is constructible at all for a
   file this large with no prior error location — do not silently proceed with an ungrounded patch
   attempt. `apply_anchored_edits` already raises when a SEARCH: block doesn't match exactly once;
   let that existing, already-tested failure path do the rejecting (bounded, deterministic,
   observable in the trace) rather than adding new pre-emptive refusal logic. Step 4's rejection and
   this one converge on the same outcome (candidate rejected, retry budget consumed, no unsafe write)
   through the same existing failure-handling path, just triggered by two different conditions.
6. Disposition **A** ("obtain complete authoritative source before full-file replacement") is
   **not** the minimum change here: `build_known_target_context()`'s tiering is itself driven by
   the Engineering Triage's risk/context_depth classification (G1's actual root trigger) — raising
   that budget specifically for this invariant risks becoming a disguised "increase context window"
   fix, which is explicitly out of scope. B is strictly safer and strictly smaller: it doesn't ask
   for more context, it asks Kriya to stop pretending it can safely do something it doesn't have
   the material for, and use the tool (anchored edit) that doesn't need that material in the first
   place.

### D1_TESTS (deterministic, no live model)

All 8 adversarial cases from the task, phrased as assertions against `operation_for_file`/
`_operation_map`'s new signature plus `find_brownfield_public_api_changes`-adjacent fixtures where
relevant:

1. Existing large file, `ContextItem(tier="skeleton", is_exact=False, omitted_regions=True)`, mode
   requests full-file → resolved operation is `REPAIR_WITH_PATCH`, never
   `CREATE_FULL_FILE`/`REPAIR_WITH_FULL_FILE`.
2. Existing file, `ContextItem(tier="signatures", is_exact=False)` → same as (1).
3. Existing file, `ContextItem(tier="member_exact", is_exact=True, member_id="some_function")` →
   `REPAIR_WITH_PATCH`, never whole-file replacement. The simple, checkable rule: `member_id is not
   None` means the exact representation covers only part of the file, so it can never by itself
   authorize replacing the *whole* file — only `tier == "full"` (necessarily `member_id is None`)
   authorizes full-file replacement. This is a single-expression check
   (`item.tier == "full" and item.is_exact and item.member_id is None`), not a judgment call.
4. Existing file, `ContextItem(tier="full", is_exact=True, revision=<current>)` → full-file
   operation permitted, unchanged from today's behavior (regression guard: this must NOT become
   stricter than necessary).
5. New file (`file_exists=False`) → `CREATE_FULL_FILE` permitted unconditionally, regardless of any
   `ContextItem` state (nothing to be incomplete about; regression guard against over-applying the
   invariant).
6. Existing file, `ContextItem.revision` present but stale (≠ current on-disk revision) →
   `is_exact` must be treated as `False` for authorization purposes even if the flag itself says
   `True` (a stale-but-flagged-exact item is not safe) → `REPAIR_WITH_PATCH`.
7. Existing file with an explicit `omitted` entry for the same path (`make_omitted_entry`) → same
   as (1), even if some other `ContextItem` for a *different* member of the same file happens to be
   exact.
8. Retry/recovery path: `mode == "api_contract_recovery"`, phase `REPAIR_BEHAVIOR`, `ContextItem`
   still skeleton-tier (unchanged from attempt 1) → `REPAIR_WITH_PATCH`, not
   `REPAIR_WITH_FULL_FILE` — this is the exact G1 scenario for attempts 3/4 and must be covered
   directly, not only inferred from cases 1/2.
9. **Fallback-closure test** (direct regression test for the hole found while designing this fix):
   for any of cases 1/2/8 above, simulate the model ignoring the patch instruction and returning
   `FILE CONTENT:` (full content) anyway → `validate_operation_result` still classifies this as a
   legal `REPAIR_WITH_FULL_FILE` transition (unchanged, by design), but the invariant-mandated-file
   tracking added in D1_DESIGN step 4 must cause the caller to reject the candidate rather than
   commit it — assert the candidate gate fails and the sandbox is not committed as accepted, not
   just that `validate_operation_result` returns a particular enum value.

Fixture: a synthetic ~200-line Python module with one ~120-line function containing 3 small nested
closures with generic names (mirroring `engine.py`'s real shape without copying its content), built
once and reused across D1 and D3 tests (see "Regression fixture" below).

---

## D2 — Recovery preservation

### Trace

```
API contract violation             find_brownfield_public_api_changes() → violations list
        ↓
APIContractRecovery.detected()     kriya/workflow/state.py:41-142 — phase=DETECTED
        ↓
begin_restoration()                phase: DETECTED → RESTORE_PUBLIC_CONTRACT
        ↓
deterministic restoration          kriya/workflow/attempt.py (RESTORE_PUBLIC_CONTRACT branch —
                                    "deterministic restore, no Developer call" per G1's own trace)
        ↓
owner_contract_restored()          phase: RESTORE_PUBLIC_CONTRACT → REPAIR_BEHAVIOR
        ↓
subsequent Developer repair        operation_for_attempt("api_contract_recovery", ...) →
                                    UNCONDITIONALLY REPAIR_WITH_FULL_FILE (operations.py:78-81),
                                    regardless of phase — the phase object exists and is threaded
                                    through state but operation_for_attempt never receives it
        ↓
candidate mutation                 full-file regeneration discards the just-restored content
        ↓
validation                         find_brownfield_public_api_changes() re-runs, catches the
                                    re-loss correctly (bounded, correct rejection — the SAFETY NET
                                    already works) but recovery has now burned an attempt without
                                    building on its own prior restoration
```

### D2_ROOT_CAUSE

`operation_for_attempt()`'s `mode == "api_contract_recovery"` branch
(`kriya/workflow/operations.py:78-81`) returns `REPAIR_WITH_FULL_FILE` unconditionally for the
*entire* recovery lifecycle, with a code comment explaining the rationale for the
`RESTORE_PUBLIC_CONTRACT` sub-phase specifically ("avoids making restoration of a missing
declaration depend on an anchor that may no longer exist in the bad candidate"). That rationale is
sound for `RESTORE_PUBLIC_CONTRACT` — but `RESTORE_PUBLIC_CONTRACT` is *already implemented as a
deterministic, non-LLM step* in this codebase (confirmed in G1's own trace: "deterministic restore,
no Developer call"), so operation selection doesn't even apply to it. The *only* phase that
actually reaches this branch with a real Developer call is `REPAIR_BEHAVIOR` — which runs strictly
*after* restoration, against a file Kriya itself just confirmed contains every required signature.
The full-file-replacement rationale (protecting an anchor that "may no longer exist in the bad
candidate") does not apply to `REPAIR_BEHAVIOR`: by construction, `owner_contract_restored()` is
only called once the file is *not* bad in the specific way that rationale is protecting against.
`APIContractRecoveryPhase` (`kriya/workflow/state.py:23-28`) already exists and is already
authoritative — `operation_for_attempt()` simply never receives it.

### D2_OWNER

`kriya/workflow/operations.py` (same authorization chokepoint as D1) jointly with
`kriya/workflow/state.py` (owns `APIContractRecoveryPhase`, already correct and already
authoritative — no change needed there beyond reading it one level up).

### D2_EXISTING_MECHANISM (extend MA9, no parallel subsystem)

- `APIContractRecovery.phase` / `APIContractRecoveryPhase` (`state.py`) — already tracks exactly the
  distinction needed (`RESTORE_PUBLIC_CONTRACT` vs. `REPAIR_BEHAVIOR`), already enforces legal
  transitions via `_transition()` (illegal transitions raise `ValueError` — a real, tested
  invariant already in place). Nothing new to build.
- `APIContractRecovery.violations` / `.protected_evidence_files` — already the MUST_FIX/
  MUST_PRESERVE data the invariant needs; already computed once at `detected()` and carried through
  every phase transition.
- `find_brownfield_public_api_changes()` — already re-runs as a candidate gate on every attempt,
  including `REPAIR_BEHAVIOR` ones (confirmed: this is exactly what caught G1's attempts 3/4). This
  is the existing MUST_PRESERVE enforcement mechanism; D2 does not need a second one.
- `operation_for_attempt()` — same function D1 extends; D2 adds a second, independent condition to
  the same function rather than a second function.

### D2_DESIGN

**Deterministic invariant** (as specified): once `APIContractRecovery.phase ==
APIContractRecoveryPhase.REPAIR_BEHAVIOR`, the Developer call for that attempt MUST use
`REPAIR_WITH_PATCH` (anchored), never `REPAIR_WITH_FULL_FILE` — with the same fail-closed fallback
as D1's step 4 (an anchor that can't be constructed/matched raises via the existing
`apply_anchored_edits` path rather than silently falling back to full-file).

**Minimum change**:

1. `operation_for_attempt()` gains an additional optional parameter (e.g.
   `recovery_phase: Optional[APIContractRecoveryPhase] = None`). When `mode ==
   "api_contract_recovery"` and `recovery_phase is APIContractRecoveryPhase.REPAIR_BEHAVIOR`,
   return `CodeOperation.REPAIR_WITH_PATCH` instead of the current unconditional
   `REPAIR_WITH_FULL_FILE`. When `recovery_phase` is `RESTORE_PUBLIC_CONTRACT` (or not supplied,
   preserving today's behavior for any caller not yet passing it), keep the existing
   `REPAIR_WITH_FULL_FILE` — correct for that sub-phase's own rationale, and in practice unreached
   by a real Developer call today since that sub-phase is deterministic.
2. The one call site in `attempt.py:3958` gains `recovery_phase=state.api_contract_recovery.phase
   if state.api_contract_recovery else None` — `state.api_contract_recovery` is already in scope at
   that exact line (it's read two lines later, at `decide_retry_action(...,
   has_api_contract_recovery=bool(state.api_contract_recovery), ...)`).
3. No change to `find_brownfield_public_api_changes()`, `APIContractRecovery`'s own transition
   methods, or the candidate-gate re-check — all three already do their job correctly; this design
   only removes the one path that made their job harder than necessary.
4. Same fallback-closure requirement as D1_DESIGN step 4 applies here without modification: a
   `REPAIR_BEHAVIOR` attempt is exactly one of the invariant-mandated cases, so a model response
   that ignores the patch instruction and returns `FILE CONTENT:` must be rejected by the same
   caller-side check, not silently accepted via `validate_operation_result`'s existing (and, for
   this case, too permissive) fallback allowance.
4. This automatically satisfies "legitimate targeted repair remains possible": `REPAIR_WITH_PATCH`
   against a file that already contains every `violations` signature is a strictly *easier* anchor
   target than the original bug's own anchor (`engine.py`'s C# call-handling code, present and
   unmodified in the restored file) — nothing about this narrows what the model is allowed to fix,
   only how it's allowed to write the fix.

### D2_TESTS

1. Baseline signatures restored deterministically (fixture: a `GenerationState` with
   `api_contract_recovery.phase == RESTORE_PUBLIC_CONTRACT`, then `owner_contract_restored()`
   called) → `state.api_contract_recovery.phase == REPAIR_BEHAVIOR`.
2. `operation_for_attempt("api_contract_recovery", has_prior_failure=True,
   recovery_phase=REPAIR_BEHAVIOR)` → asserts `REPAIR_WITH_PATCH`, not `REPAIR_WITH_FULL_FILE`
   (the direct regression test for this defect).
3. Simulated subsequent repair attempt whose returned edits' SEARCH: block does not match the
   restored file (e.g., stale-anchor adversarial input) → `apply_anchored_edits` raises, candidate
   is rejected, `violations` signatures remain present in the (unmodified, rejected) file — proves
   the preservation gate rejects a destructive mutation rather than accepting it.
3a. Same setup, but the model instead ignores the patch instruction and returns `FILE CONTENT:`
   omitting the restored signatures (i.e. reproducing G1's actual attempt-3/4 shape exactly) →
   the D1_DESIGN-step-4 fallback-closure check rejects it; `violations` signatures remain present
   in the (unmodified, rejected) file. This is the single most direct regression test for D2 —
   it replays the literal failure mode observed in run `8b6ee803`.
4. Simulated legitimate targeted repair (SEARCH:/REPLACE: pair touching only the actual C#
   call-handling lines, none of the `violations` signatures) → accepted, candidate gates pass,
   `violations` signatures still present after commit.
5. Bounded no-progress behavior: two consecutive `REPAIR_BEHAVIOR` attempts that both fail to
   produce a matching anchor → `retry_strategy`'s existing `api_contract_recovery_count`/max-retry
   bookkeeping (unchanged by this design) still terminates the run with `quality_gates_exhausted`,
   exactly as G1 observed — proves this design doesn't remove the existing bounded-failure
   guarantee, only changes *how* each attempt tries to succeed.
6. Regression: `recovery_phase=RESTORE_PUBLIC_CONTRACT` (or omitted) → unchanged
   `REPAIR_WITH_FULL_FILE`, proving the deterministic-restore sub-phase's own existing behavior is
   untouched.

---

## D3 — API contract scope

### D3_ROOT_CAUSE

Two independent, source-confirmed defects in `find_brownfield_public_api_changes()`
(`kriya/workflow/file_resolution.py:619-727`):

1. `_normalized_public_signatures()` (`file_resolution.py:563-608`), for Python, uses
   `_PYTHON_PUBLIC_FUNCTION_RE = re.compile(r"^[ \t]*(?:async\s+)?def\s+([A-Za-z][A-Za-z0-9]*)\s*\(([^)]*)\)", re.MULTILINE)`
   — `^[ \t]*` matches *any* leading indentation, so this fires identically for a module-level
   function and a function nested arbitrarily deep inside another function. The only filter is
   `if name.startswith("_"): continue` — a **naming-convention** filter, not a **scope** filter.
   Confirmed directly against the real G1 evidence: `bind` (nested in
   `_csharp_method_receiver_types`), `visit` (nested in `_ruby_local_class_bindings`), and `walk`
   (nested in `_extract_generic` and four other unrelated functions) are all non-underscore-named,
   function-local closures — none returned or otherwise exposed by their enclosing function, hence
   structurally uncallable from any other file — yet all four were extracted as "signatures" and
   reported as removed "public API."
2. `evidence_files` (`file_resolution.py:700-704`) is computed via
   `re.search(rf"(?<![\w$]){re.escape(api_name)}\s*\(", evidence_content)` against **every other
   file's raw text**, workspace-wide, with no import/call-graph/reachability check at all. A file
   containing its own, entirely unrelated, same-named local helper (extremely common for generic
   AST-traversal helper names like `walk`/`bind`/`visit`) is indistinguishable, to this regex, from
   a file that genuinely imports and calls the flagged symbol.

Neither defect is model-specific or Graphify-specific: (1) fires on any Python file with a
non-underscore-named nested function; (2) fires on any repo where a short, common helper name is
independently reused across files — a normal, unremarkable pattern in any sizeable codebase.

### D3_OWNER

`kriya/workflow/file_resolution.py` (`_normalized_public_signatures`, `find_brownfield_public_api_changes`).

### D3_EXISTING_MECHANISM

Direct answer to the task's specific questions:

- **Should nested functions ever constitute brownfield public API?** No. A function-nested closure
  that is never returned or otherwise exposed by its enclosing scope is, by Python's own semantics,
  uncallable from any other module. (A closure that *is* returned/exposed — e.g. a factory pattern
  — is a distinguishable, rarer case; see the design below for how to handle it without
  over-restricting.)
- **Should underscore-prefixed symbols require stronger evidence?** The premise should be inverted:
  the filter should be **scope** (module-level vs. nested), not **naming convention**. A
  module-level function without a leading underscore is a plausible public API regardless of
  whether anyone calls it yet (Python has no true "private" access enforcement at module level, so
  a naming-convention floor is a reasonable *supplementary* signal there) — but a *nested* function
  is never a public API candidate at all, regardless of its name. Today's filter gets this backward:
  it lets naming convention override scope, when scope should gate eligibility first and naming
  convention should only refine what's already module-level-eligible.
- **Is text occurrence sufficient evidence?** No — directly disproven by G1 (all 4 citations were
  name collisions, not real dependencies).
- **Does reachability/import/export evidence already exist elsewhere in Kriya and can be reused?**
  Partially. `kriya/analyzer/graph.py::DependencyGraph` already has `get_callers()`, `get_callees()`,
  `get_imports()`, `get_symbols_for_file()` — genuinely closer to real evidence than a blind
  substring search (its `calls`/`imports` relations come from `ast.walk()`'s own `ast.Call`/
  `ast.Import`/`ast.ImportFrom` node types, not arbitrary text). **However**, direct inspection of
  `DependencyGraph._parse_python()` (`kriya/analyzer/graph.py:541-617`) shows it has the **same**
  nesting blindness as `_normalized_public_signatures()`: it also uses `ast.walk(tree)` (a flat,
  scope-unaware traversal) to capture `ast.FunctionDef` symbols and `ast.Call` relations, so
  `get_callers("walk")` today would show the same false positives `find_brownfield_public_api_
  changes()` currently produces. Reusing `DependencyGraph` as-is does not solve this; reusing it
  *after* the same module-level-scoping fix is applied to its own `_parse_python()` would.

### D3_DESIGN

**Minimum change, in two parts, both inside existing files — no new semantic graph**:

1. **Scope-correct signature extraction** (`file_resolution.py::_normalized_public_signatures`):
   replace the regex-based Python extraction with an `ast.parse()`-based walk restricted to
   `tree.body` (module-level statements) for functions, plus one level into each `ast.ClassDef`'s
   own body for methods (tracked as a distinct category, e.g. `f"{ClassName}.{method_name}(...)"`,
   since a public class method *is* externally reachable via attribute access even though it isn't
   a bare module-level name) — never recursing into a `FunctionDef`'s own body. This directly fixes
   defect (1): a closure nested inside `_extract_generic` or any other function is structurally
   excluded from consideration, regardless of its name. The existing underscore-prefix filter is
   kept as a secondary refinement on top of this (module-level `_helper()` still excluded, matching
   today's intent for genuinely module-private functions) — not removed, just no longer the *only*
   gate.
2. **Reachability-checked evidence** (`file_resolution.py`'s `evidence_files` computation): replace
   the unscoped `re.search` against every file's raw text with a query against
   `DependencyGraph.get_callers(api_name)` / `get_imports(path)`. **Checked, not assumed**: neither
   `attempt.py:4862` nor `workflow.py:3086` (the two `find_brownfield_public_api_changes()` call
   sites) currently receives a `DependencyGraph` instance — both files construct one elsewhere for
   unrelated purposes (`attempt.py:2084/2103/2302`, `workflow.py:347/1651`), but not at these
   specific call sites. This is a real, bounded plumbing change (pass a `DependencyGraph`
   reference, or the `dependency_graph.db` path needed to construct one, down to these two call
   sites), not something that falls out for free. It also depends on `dependency_graph.db` already
   holding current-enough symbol data for the *evidence* files being queried (not the file under
   repair, which is expected to be stale/mid-edit) — reasonable in practice since evidence files
   are, by definition, files the current candidate batch isn't touching, but not proven here by a
   live run.

   **Fail-closed direction, stated explicitly**: when no `DependencyGraph` is available (indexing
   hasn't run, or the plumbing above isn't wired for a given caller), `find_brownfield_public_api_
   changes()` must treat the absence of graph evidence as "no evidence" — i.e., it stops flagging
   *any* API-signature removal for that run, including a genuine module-level public-function break
   it correctly catches today under the current text-search mechanism. This is a real, traded-away
   detection capability, not a free precision improvement: an un-indexed workspace (or a caller not
   yet wired to receive the graph) would silently lose brownfield-API protection entirely, in
   exchange for eliminating the false positives G1 demonstrated. Whether this tradeoff is acceptable
   depends on how reliably a `DependencyGraph` is actually populated by the time Quality Gates run
   in a real generation workflow — not verified here (STANDARD lane, investigate + design only) and
   should be confirmed with a live-trace check (does `dependency_graph.db` exist and contain
   current entries at the moment `find_brownfield_public_api_changes()` runs, across ordinary
   generation runs, not just this one) before this specific sub-fix ships. If that check comes back
   negative for a meaningful fraction of runs, the corrected design should keep the text-search as
   a secondary, lower-confidence evidence source (clearly labeled as such, never conflated with
   graph-verified evidence) rather than dropping it outright.
3. Fixing (1) is a prerequisite for (2) to be trustworthy: `DependencyGraph`'s own `_parse_python()`
   needs the identical module-level/class-method scoping fix (same technique as step 1, applied to
   `graph.py:541-617`'s `ast.FunctionDef`/`ast.Call` handling) so `get_callers()` doesn't inherit
   the same nesting blindness it would otherwise still have. This is the one place D3 genuinely
   touches a second file — but it is the *same fix pattern* applied twice, not two different
   mechanisms, and it's a pure narrowing (fewer false "symbol"/"caller" entries), not a new
   capability.

### D3_TESTS

Eight cases from the task, each a direct unit test of `_normalized_public_signatures()` +
`find_brownfield_public_api_changes()` against small synthetic fixtures:

1. Module-level public function (`def foo(x): ...` at column 0) → counted as a signature; removal
   flagged (regression guard — must still work).
2. Exported public function (module-level, referenced via `from module import foo` in another
   fixture file) → counted; removal flagged with `evidence_files` containing the real importer,
   via `get_callers`/`get_imports`, not text search.
3. Class public method (`class C: def method(self): ...`) → counted under the
   `ClassName.method(...)` key; removal flagged only when genuinely reachable (a class that's never
   instantiated/imported elsewhere should not spuriously flag via bare-name text collision).
4. Underscore-prefixed module-level function (`def _helper(): ...`) → not counted (unchanged from
   today).
5. Nested function with a non-underscore, "public-looking" name (`def outer(): \n    def walk(n):
   ...`) → **not** counted as a signature at all — the direct regression test for G1's actual
   failure.
6. Nested underscore-prefixed function → not counted (doubly excluded, for completeness).
7. Unrelated repo-wide textual occurrence (a second, independent file with its own unrelated
   `def walk(x): ...` at module level, sharing only the name) → does NOT appear in `evidence_files`
   for the first file's `walk`, since there is no real call/import edge between them.
8. Genuine reachable/imported consumer (a file that actually does `from module_a import walk` and
   calls `walk(...)`) → DOES appear in `evidence_files`.

### Regression fixture

A synthetic module mirroring `engine.py`'s real shape without copying Graphify source:
~150–250 lines, one large module-level function (`def _extract_generic(...)`) containing 3–4
nested closures with short, generic, non-underscore names (`bind`, `visit`, `walk` — the literal
names G1 demonstrated the failure on, since they're a fair, non-Graphify-specific choice: any
codebase's AST-walker helpers commonly use these exact names), plus 2–3 genuine module-level public
functions and one class with a public method, so the same fixture set exercises both D1's
context-tier cases (a file "too large" relative to a small configured budget in the test) and D3's
scope cases (nested vs. module-level vs. class-method) without needing two separate fixtures or any
real Graphify file in the test tree.

---

## Cross-defect analysis

D1 and D2 **share an owning component** (`operations.py::operation_for_attempt`) but have
**distinct root causes** — D1 is blind to context completeness, D2 is blind to recovery sub-phase —
and either can occur independently of the other (D1 can manifest on a clean first attempt with no
recovery involved at all; D2's phase-blindness would apply even on a file whose context happened to
be exact, if some other reason made `REPAIR_BEHAVIOR`'s full-file rewrite lossy). They are extended
as two independent, additively-composed conditions on the *same* function rather than merged into
one check, per the task's explicit instruction not to collapse defects to reduce code changes.

D3 is **architecturally independent** of D1/D2 — different subsystem
(`file_resolution.py`'s signature/evidence extraction vs. `operations.py`'s protocol selection),
different failure mode (mis-detection/mis-scoping vs. mis-authorization). D3's fix does not prevent
D1/D2's failure mode, and D1/D2's fix does not prevent D3's. They interact only in *severity*: if
D1's invariant ships, destructive whole-file loss of 100+ functions (the scenario where D3's
under-detection matters most) becomes structurally unreachable in the first place, since anchored
edits cannot silently delete unrelated functions the way a full-file replacement can — but D3
remains independently real and worth fixing (a smaller-scale API break, e.g. an anchored edit that
happens to touch a module-level function's signature, would still be mis-evaluated by the current
nesting-blind detector even after D1/D2 ship).

## Per-defect summary

| | D1 | D2 | D3 |
|---|---|---|---|
| `ROOT_CAUSE` | `operation_for_attempt`/`operation_for_file` blind to `ContextItem.tier`/`is_exact` | `operation_for_attempt`'s `api_contract_recovery` branch blind to `APIContractRecoveryPhase` | `_normalized_public_signatures` scope-blind (nesting) + `evidence_files` reachability-blind (text-only) |
| `OWNING_COMPONENT` | `kriya/workflow/operations.py` + `attempt.py` | same, + `kriya/workflow/state.py` (unmodified, just read) | `kriya/workflow/file_resolution.py` (+ `kriya/analyzer/graph.py` for evidence quality) |
| `EXISTING_MECHANISM_TO_EXTEND` | `ContextItem`/`ContextPackage` (CTX-001 P1 WP3), `REPAIR_WITH_PATCH`/`apply_anchored_edits`, `_operation_map`'s override pattern | `APIContractRecoveryPhase`, same `operation_for_attempt` | `ast.parse()` module-level walk (replacing a regex), `DependencyGraph.get_callers`/`get_imports` |
| `MINIMUM_CHANGE` | New optional param + one override branch in `_operation_map`; extend `prefer_anchored_edit`'s existing condition | New optional param + one conditional return in `operation_for_attempt`; one new argument at its one call site | Rewrite one function's signature-extraction to be scope-aware; swap one regex search for a graph query (with a narrowing fail-closed default) |
| `AUTHORITY_IMPACT` | None — narrows what's authorized, never widens | None — narrows, never widens | None — narrows detection scope in one direction (fewer false "public API" claims), only widens in the reachability-evidence direction within already-existing graph data, never grants new filesystem/network/execution authority |
| `REGRESSION_RISK` | Low-Medium: `REPAIR_WITH_PATCH` itself is already-shipped/tested; the new fallback-closure check (step 4) is the one genuinely new code path and needs its own direct test (D1_TESTS #9) since nothing exercises it today | Low: only changes behavior for the one specific `(mode, phase)` combination that never worked correctly anyway (its current behavior is 0-for-2 in the only real run that exercised it); inherits D1's fallback-closure risk for the same reason | Medium-High: part 1 (scope rewrite) needs a full pass of existing `find_brownfield_public_api_changes`/CORR-016 tests to confirm no currently-passing module-level case regresses; part 2 (graph-backed evidence) trades away real detection coverage on any caller/workspace state where `DependencyGraph` isn't populated — see the explicit tradeoff discussion above, not yet verified live |

---

## Architecture invariants (verified against this design)

- `AUTHORITY_EXPANSION_PATHS = 0` — every change above narrows an authorization or detection
  surface; none widens filesystem/network/execution authority.
- `MODEL_SPECIFIC_WORKAROUNDS = 0` — nothing keys on `qwen3-coder` or any model identity.
- `CTX001_ARCHITECTURE_REPLACEMENT = 0` — D1 reads CTX-001 P1's existing `ContextItem` fields
  as-is; adds no new tier vocabulary, no new skeletonization behavior.
- `NEW_EDIT_FRAMEWORK = 0` — D1/D2 route through the already-shipped `REPAIR_WITH_PATCH`/
  `apply_anchored_edits`; no new operation kind is added to `CodeOperation`.
- `NEW_RECOVERY_FRAMEWORK = 0` — D2 reads `APIContractRecoveryPhase` as-is; no new phase, no new
  state object.

No STOP condition is triggered: no authority widening, no model-specific or Graphify-specific
handling (the regression fixture deliberately does not copy Graphify source), no CTX-001
replacement, and every fix extends an existing mechanism rather than introducing new editing/
recovery architecture.

---

## Implementation status (2026-09-18)

D1, D2, and D3-part-1 are **implemented**, exactly as designed above, plus one closed gap and one
scope extension found only while implementing (both documented here, not silently folded into the
design narrative above, which is left as originally written):

- **Closed gap**: `validate_operation_result()`'s own PATCH→FULL_FILE fallback (intentionally
  permissive for an ordinary targeted retry) would otherwise have made the new invariant advisory,
  not deterministic, for a model that simply ignores the patch instruction. Closed with a
  caller-side "mandatory patch" check at `run_attempt()`'s existing operation-contract enforcement
  block (`kriya/workflow/attempt.py`) — `validate_operation_result()` itself is unchanged.
- **Scope extension found via the real existing test suite, not anticipated in the design above**:
  a targeted retry's own content-supply mechanism (`RetryPackage`/`FileProjection`,
  `kriya/workflow/retry_package.py` + `context_projection.py`) is a second, independent producer of
  exactly the same real completeness signal `ContextItem` carries (`path`/`revision`/`level`/
  `omitted_regions`) — proven necessary by `test_first_anchor_failure_switches_next_protocol_
  without_widening_scope` (an existing test whose own legitimate full-file fallback would otherwise
  have been wrongly blocked). `_record_retry_projection_context_items()`
  (`kriya/workflow/attempt.py`) converts `FileProjection` → `ContextItem` at both
  `_build_targeted_retry_prompt()` call sites, the same shape and same target dict
  (`state.known_target_context_items`) as the known-target path already uses.
- **Exemption found the same way**: `RESTORE_PUBLIC_CONTRACT` is deterministic (never a Developer
  call — `_restore_api_contract_owners_deterministically()`) and its own docstring documents that
  its output is deliberately shaped identically to real Developer output so downstream consumers
  don't need to know the difference. The new invariant is about the risk of *probabilistic*
  generation from incomplete context — a risk this phase provably does not carry — so
  `_completeness_gated_operation()` exempts it explicitly rather than gate it.

**Verification performed** (no `.venv/bin/pytest` invocation, per this repo's own quota-discipline
convention — every check below ran via direct Python import/execution against this project's own
`.venv`, not a mocked or reimplemented harness):

- All 28 new deterministic tests (`tests/test_val001_g1_remediation.py`) pass.
- 288 existing tests across `tests/test_agents.py`, `tests/test_corr016_planner_authority_gate.py`,
  `tests/test_corr018_general_case_closure.py`, and `tests/test_semantic_region_authority.py` pass
  unchanged.
- All 145 directly-invokable `run_attempt()`-exercising tests in `tests/test_workflow.py` pass,
  including all 18 that directly exercise `find_brownfield_public_api_changes()`. Three genuine
  interactions were found this way (not found by reasoning about the design alone) and resolved:
  the anchor-failure fallback test above, and two runtime-artifact-cleanup tests
  (`test_run_attempt_cleans_up_runtime_artifacts_between_attempts[_without_git]`) plus one
  process-boundary-recurrence test (`test_run_attempt_escalates_message_when_process_boundary_
  failure_recurs`) whose own mocked `developer.run_generation` bypasses the real context-building
  step this invariant depends on — each fixed by recording the same real-content `ContextItem` a
  production run's own preceding steps would have produced, not by weakening any assertion.
- The one existing test whose own fixture needed updating
  (`test_first_anchor_failure_switches_next_protocol_without_widening_scope`) had its *setup*
  extended (one `known_target_context_items` entry added, reflecting real production sequencing) —
  its assertion and intent are unchanged.

**D3-part-2 prerequisite check (investigation only, no code changed for it)**: confirmed directly
against source that `dependency_graph.db` is populated exclusively by `index_repository()`, which
runs only from the explicit `kriya analyze` CLI command by default. `autonomy.auto_index_missing_
dependency_graph` (the only auto-population path during ordinary `generate`/`fix`) defaults to
`False` in the packaged config, and even when enabled, silently degrades back to an empty graph on
any failure. `kriya/workflow/triage.py`'s own code comment states plainly that "dependency_graph.db
is frequently empty." This confirms the design's own disclosed fail-closed tradeoff is a **live**
risk for the packaged-default deployment, not a theoretical one — D3-part-2 should not ship as "query
the persisted DependencyGraph" without addressing it. Two already-implemented, reusable patterns
exist for a corrected future design, found during this same investigation: `kriya/workflow/
attempt.py`'s duplicate-type-check builds a live, DB-independent per-file parse whenever the
persisted graph is empty/stale, and `kriya/workflow/workflow_controller.py`'s cross-file
symbol-mismatch gate builds a purely ephemeral, in-memory `DependencyGraph` scoped to just the
relevant candidate files, never touching the persisted DB at all — either pattern would let
D3-part-2 reuse real `DependencyGraph`/symbol evidence (per its own original design constraint)
without depending on `kriya analyze` having been run.

**Files changed** (matches the task's own expected-scope list exactly, plus `state.py` for the one
new field + import both D1 and D2 read/write):

- `kriya/workflow/operations.py` — `operation_for_attempt()` gains `recovery_phase` (D2).
- `kriya/workflow/attempt.py` — `_completeness_gated_operation()` (new), `_operation_map()`
  extended, `_record_retry_projection_context_items()` (new), two `build_known_target_context()`
  call sites + two `_build_targeted_retry_prompt()` call sites now record `ContextItem`s, the
  operation-contract enforcement block gains the mandatory-patch-fallback rejection, the
  `operation_for_attempt()` call site passes `recovery_phase`.
- `kriya/agents/agent.py` — the anchored-edit system prompt and fix-analysis instruction now branch
  on `apply_fix_analysis` instead of unconditionally claiming a retry.
- `kriya/workflow/file_resolution.py` — `_PYTHON_PUBLIC_FUNCTION_RE`'s character class widened
  (a discovered, adjacent, in-scope bug: it silently missed any function name containing an
  underscore anywhere, not just a leading one); `_python_module_and_class_level_signatures()` (new,
  AST-based, module/class-scope-only) replaces the old whole-file regex for Python specifically;
  `evidence_files`'s unscoped text search is unchanged (D3-part-2, explicitly out of scope here).
  **This is the highest-risk single change in this package, stated plainly rather than buried in a
  risk-level label**: widening the character class changes what counts as a tracked signature for
  *every* Python file `find_brownfield_public_api_changes()` ever sees, and it changes it in the
  direction of finding MORE signatures than before (previously-invisible snake_case functions are
  now tracked) — meaning it can surface genuinely NEW brownfield-API violations on Python files that
  reported zero violations before this change, not just fix the nested-closure false positives it
  was written for. The 145+288-test sweep in this section covers every test that actually exercises
  this code path in this repository today and all of them pass, but that is not the same claim as
  "the full suite is clear" — it is a necessary, not sufficient, check. The full suite (below) is
  what actually closes this out.
- `kriya/workflow/state.py` — `GenerationState.known_target_context_items` (new field).
- `tests/test_val001_g1_remediation.py` (new) — the designed D1/D2/D3-part-1 test suite.
- `tests/test_workflow.py` — three existing tests' *setup* extended (not their assertions) to
  supply the same real-content evidence a production run's preceding steps would have produced.
