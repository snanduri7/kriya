# DEV-INV-001: Governed, Model-Directed Repository Investigation

Status: implemented, feature-flagged OFF by default (`autonomy.developer_investigation_enabled`).
Baseline: `milestone-decomposition` @ `8fe1b1cd92d1d6737ebca63b82ad13c3bcd6c046`.

## Why it exists

Kriya's Developer + Quality Gates loop has always worked from *precomputed*
context: Graph RAG retrieval assembles a token-budgeted context string once,
before generation, and the Developer either uses it or fails a Quality Gate
and retries with more (error-driven) context. It has no way to ask a
targeted question mid-attempt ("what does `Foo.bar` actually look like?",
"who calls this?", "is there existing code that already does this?") the
way an interactive coding agent can. DEV-INV-001 closes that gap narrowly,
without rearchitecting the pipeline: the Developer may now request bounded,
read-only repository evidence before proposing a mutation.

Target lifecycle (only INVESTIGATE is new):

```
UNDERSTAND -> INVESTIGATE -> PROPOSE -> MUTATE -> VERIFY -> REPAIR
```

Core invariant, unconditionally: **the LLM proposes, Kriya governs.** The
model may request evidence; Kriya alone decides whether the request is
legal, how it's resolved, how much comes back, and whether it's current.

## What is new

- `kriya/workflow/investigation.py` - the entire mechanism: request
  normalization (native tool-call + marker/text fallback protocols), four
  verb resolvers, a bounded turn-loop, and a distance-independent
  no-progress gate.
- `kriya/analyzer/graph.py::DependencyGraph.find_symbol_locations` - one
  additive, indexed (`idx_symbols_name`) exact-match method backing
  `find_symbol`. Deliberately EXACT-match only (never `LIKE`) - a symbol
  search must stay a cheap point lookup regardless of repository size.
  Honest limitation: Java class-level symbols are indexed under their
  *qualified* name, so a bare simple-name lookup finds nothing for those -
  the resolver tells the model to retry with the fully-qualified name
  rather than silently falling back to a substring scan.
- `kriya/policy/filesystem.py::AuthorizedFileReader` - the read-side
  sibling of the existing `AuthorizedFileWriter`. Same composition
  (`make_workspace_scope`/`is_within_scope` for canonical, symlink-resolved
  containment, plus a dedicated `ExecutionPolicy` for the sensitive-path
  check), same real-enforcement posture (raises `PolicyDeniedError`, never
  audit-only). This closes a real, pre-existing gap: `ExecutionPolicy.
  _check_filesystem` already governs `READ_FILE` symmetrically with
  `WRITE_FILE`, but no production call site ever built a `READ_FILE`
  `ActionRequest` with a real `workspace_path` - so the rule never actually
  ran for a read. Every DEV-INV-001 resolver that touches a path routes
  through this class before returning any location or content.
  `AuthorizedFileReader.authorize()` deliberately omits `workspace_path`
  from its own delegated `ExecutionPolicy.evaluate()` call (unlike
  `AuthorizedFileWriter`'s otherwise-identical delegation) - its own
  `is_within_scope()` check is already the authoritative, MULTI-root
  containment decision (workspace_path *and* worktree_path, when a run has
  one), and `ExecutionPolicy._check_filesystem`'s own containment rule only
  ever checks a single `workspace_path` value; passing just the first root
  would re-deny a target legitimately inside the second one. `ExecutionPolicy`
  is consulted here purely for its unconditional sensitive-path rule.
- **No new `ContextItem.source_type` value.** `CONTEXT_SOURCE_TYPES`
  (`kriya/workflow/context_package.py`) is a deliberately closed, pinned
  vocabulary (`tests/test_context_package.py::
  test_context_source_types_cover_the_design_docs_own_vocabulary` asserts
  exact set equality, on purpose - a tripwire, not incidental). DEV-INV-001
  reuses three EXISTING values instead of adding one: `inspect_member` maps
  to `"named_in_request"` (the model explicitly named this exact path/
  member - the same value `kriya/workflow/attempt.py`'s own retry-projection
  context items already use for "explicitly targeted, not inferred"
  content); `find_symbol`/`find_callers` map to `"graph_dependency"`
  (`DependencyGraph`-backed); `search_code` maps to `"semantic_hit"`
  (vector-store-backed). `reason` (free text, e.g.
  `"developer_investigation:inspect_member:Foo.bar"`) is what actually
  distinguishes DEV-INV-001 provenance for observability - the existing
  vocabulary already accurately represents the evidence, so extending it
  was unnecessary and, once found, reverted.
- `autonomy.developer_investigation_enabled` (default `False`) and
  `autonomy.developer_investigation_max_turns` (default `10` - raised from
  `4` on 2026-09-19 once the loop's primary stopping condition became
  evidence-driven mutation-readiness rather than the turn count itself;
  see `run_investigation_loop`'s own docstring), classified
  `REPOSITORY_SAFE` in `kriya/config/authority.py`, mirroring
  `self_correction_loop_enabled`/`_max_turns` exactly (same risk shape: an
  already-governed, strictly-read-only capability toggle, never new
  authority).
- `kriya/workflow/attempt.py::_maybe_run_developer_investigation` - the one
  integration seam, called from `_run_developer_generation` (the single
  choke point all six real call sites already funnel through) before
  `ctx.developer.run_generation(**kwargs)`. `DeveloperAgent.run_generation`/
  `_fill_missing_content` are completely unmodified.
- `GenerationState.investigation_turns_used_by_attempt` (`Dict[int, int]`,
  attempt_number -> turns consumed) - a coordinated-repair attempt calls
  `_run_developer_generation` once per contract participant; this is what
  stops each participant getting its own full turn budget.

## What is reused, unmodified

- `ContextItem` (no new `EvidenceItem` class) - every verb produces
  ordinary `ContextItem`s via the existing `make_context_item`.
- `CurrentSourceResolver` / `member_boundaries_for` / `extract_member_body`
  / `boundaries_matching_member_id` (`kriya/workflow/context_source.py`) -
  `inspect_member` is a thin wrapper, nothing new.
- `DependencyGraph.get_callers` (existing, indexed) backs `find_callers`
  unmodified.
- `LocalVectorStore.query_hybrid` backs `search_code` - injected into the
  loop as a plain async callable (`InvestigationDependencies.search_code`),
  so `kriya/workflow/investigation.py` never constructs an embedding client
  or knows an embedding endpoint exists at all; the real wiring
  (`kriya/workflow/attempt.py`) mirrors `workflow.py`'s own Graph RAG
  retrieval construction exactly. This is also what makes the resolver
  testable with zero live embedding calls.
- `kriya/core/model_capabilities.py::resolve_model_capability_profile` -
  protocol selection (native tool-calling vs. the marker/text fallback)
  reads `capabilities.native_tool_calls` directly. `UNVERIFIED_MODEL_
  CONSERVATIVE_PROFILE` already sets this `False`, so an unknown/unverified
  model automatically gets the marker fallback with zero model-name-specific
  branching anywhere in this feature.
- `kriya/core/llm.py::LLMClient.complete_with_tools` - the native protocol
  reuses this exact method (already used by `kriya/workflow/self_
  correction.py`'s own bounded tool-calling repair loop); DEV-INV-001's
  four tool schemas mirror that module's small-argument-only tool shape.
- D1 (`_completeness_gated_operation`, `kriya/workflow/attempt.py`) - fully
  unmodified. A `tier="member_exact"` investigation result is merged into
  `state.known_target_context_items` via the SAME `_preserve_member_exact_
  precision` helper the retry-projection path already uses - not a second,
  independently-invented merge rule. None of the four MVP verbs ever
  produces `tier="full"`, so investigation evidence can *never* accidentally
  authorize a whole-file replacement it wouldn't otherwise be authorized
  for - it can only ever help authorize a *more precise* patch.

## MVP verb set (deliberately closed at four)

| Verb | Backing | Returns |
|---|---|---|
| `inspect_member` | `member_boundaries_for` + `CurrentSourceResolver` | with `member_id`: that member's exact current body (`tier="member_exact"`); without it: a listing of the file's top-level members (`tier="signatures"`) |
| `find_symbol` | `DependencyGraph.find_symbol_locations` (new, indexed) | matching `{path, type, line range}` entries |
| `find_callers` | `DependencyGraph.get_callers` (existing) | files that call it (file-level - `get_callers`'s own "calls" relations record the calling FILE as `source`, never a resolvable enclosing-symbol identity, for both Python and Java) |
| `search_code` | `LocalVectorStore.query_hybrid` (existing) | bounded, ranked indexed chunks (`tier="skeleton"`, `is_exact=False`) |

`inspect_dependency`/`find_implementations`/`inspect_related_tests` and any
other verb are explicitly out of scope for this MVP.

## One internal request representation

Both protocols normalize into the same `InvestigationRequest(verb,
arguments)`:

- **Native tool-calling** (`capabilities.native_tool_calls == True`):
  `LLMClient.complete_with_tools` against the four `INVESTIGATION_TOOLS`
  schemas; `normalize_native_tool_call` turns a decoded call (or an
  already-detected `argument_error`/unknown tool name) into either an
  `InvestigationRequest` or a `MalformedInvestigationRequest`.
- **Marker/text fallback** (every other model, including any unverified
  one): a single line, anchored at line start so a narrative sentence can
  never accidentally match: `INVESTIGATE: <verb> <json-object>`.
  `parse_marker_response` scans every line for the first match; no matching
  line at all is an **implicit** `ProposeSignal` (this is what lets a
  weak/non-participating model - one that never engages with the protocol
  at all - degrade safely to pre-DEV-INV-001 behavior, rather than being
  treated as malformed).

Downstream code (authorization, resolution, the no-progress gate) only ever
sees `InvestigationRequest`/`ProposeSignal`/`MalformedInvestigationRequest`
- never which protocol produced it.

## Malformed-request handling

A detected-but-unparseable `INVESTIGATE:` line (unknown verb, missing/
invalid JSON) is fed back as an ordinary `ERROR:` result and consumes one
turn of the shared turn budget - never silently reinterpreted as
"sufficient evidence" (that would change model intent), and never given an
unbounded separate repair budget (a model that keeps sending malformed
input simply exhausts `max_turns` and the loop falls through to `PROPOSE`,
exactly like the "no infinite parser-repair loop" requirement calls for).

## Read authority / security

Before any path-backed result is returned:

```
model request -> normalize -> AuthorizedFileReader.raise_if_denied
    -> workspace containment (canonical, symlink-resolved)
    -> sensitive-path denial (.ssh/.aws/.env/credentials/secrets/...)
    -> resolver (CurrentSourceResolver / DependencyGraph / vector store)
```

`find_symbol`/`find_callers`/`search_code` can each return multiple
matches; a denied match is silently *omitted* from the result (with an
"N omitted by read policy" count), never a whole-call failure - this is
the "no fuzzy path escape" requirement applied per-match rather than
all-or-nothing. Repository content returned as evidence is trust-tagged
`"repository"` exactly like any other worktree-sourced context - the
"Begin/End Untrusted Reference Context" fencing is reserved for externally
*ingested* (`kriya learn`) content, a different trust boundary entirely;
investigation evidence is the same worktree the Developer can already see
via retrieval, just fetched on request instead of pre-fetched.

## Context budget / no-progress

Every result is bounded (a handful of symbol matches, five ranked search
hits, 1500 chars per search hit, one member's exact body) and the loop
itself is bounded (`autonomy.developer_investigation_max_turns`, shared
across an attempt's participants) - prompt growth has a deterministic
ceiling by construction, never an unbounded accumulation.

No-progress detection (`investigation._NoProgressTracker`) is a single
dict, `fingerprint(verb, arguments) -> combined content_hash of the last
resolution for that fingerprint`. This generalizes distance-independent
oscillation (A -> B -> A -> ... -> A) automatically: however far apart, a
fingerprint repeat whose evidence is byte-identical is caught; a repeat
whose evidence differs (a revision changed, a stronger precision resolved
this time, different arguments) is never mistaken for one, because the
comparison is on the *result*, not the request alone. A confirmed
no-progress verdict ends the loop and transitions straight to `PROPOSE` -
it never opens a new terminal-success path, and D1 remains the only
authority over what mutation shape that then permits.

## Feature rollout

`autonomy.developer_investigation_enabled` defaults `False`. With it off,
`_maybe_run_developer_investigation` returns on its first line - zero
behavior change, byte-identical to pre-DEV-INV-001 code. With it on,
investigation is available for every brownfield attempt (any workspace
with an existing dependency-graph index - the same "has this workspace
ever been indexed" signal `workflow.py`'s own auto-index gate already
uses), on every attempt/retry, never gated on `tier != full` or
`is_exact == false` (a full, small file may still benefit from a caller
lookup elsewhere; a `member_exact` hint may already be sufficient - the
model decides whether to investigate, Kriya governs the request).

## Relationship to recovery (MA9 / C3)

Not integrated in this MVP. The same resolvers (`inspect_member`'s
`member_boundaries_for`/`CurrentSourceResolver` composition, `find_symbol`/
`find_callers`'s `DependencyGraph` composition) are structured so a later
pass can let `_resolve_retry_member_hints`/C3 call the same authoritative
resolvers DEV-INV-001 introduces here, rather than building a second,
parallel resolution path - deliberately not attempted in this pass, per the
task's own explicit scope boundary.

## Non-goals (explicit)

No new agent framework, no `EvidenceItem` class, no generic Evidence Broker,
no MCP for internal investigation, no unrestricted shell, no model-directed
writes, no new RAG/vector architecture, no Graphify-specific logic, no
model-specific workaround, no Planner/Architect investigation in this MVP,
no redesign of MA9 recovery, no change to D1 authority semantics.
