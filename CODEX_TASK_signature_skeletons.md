# Codex task: fix `"signatures"` skeletonization tier

## Context

Kriya's Graph RAG context assembler degrades files through skeletonization
tiers when a generation prompt is over its token budget: `full -> skeleton ->
signatures` (`kriya/workflow/context_budget.py:247`, `_TIER_STEPS`). The
`"signatures"` tier is the most aggressive — it's meant to be the last thing
shown for a file before it's dropped entirely, and its whole purpose is to
still convey each function/method's *interface* (name, params, return type)
even though the body is gone.

It doesn't do that today. This was flagged in an external design review
(`KRIYA_DESIGN_REVIEW_AUGUST_17.txt`, item P1-10 / "Correct Graph RAG and
symbol resolution") and independently confirmed against the current code
before writing this brief — both claims are real, not stale review artifacts:

1. **Python** (`skeletonize_python`, `kriya/workflow/context_budget.py:61-67`):
   in `tier == "signatures"` mode, any line starting with `def ` is skipped
   entirely (`continue`) — the function signature itself is dropped, not just
   its body.
2. **Java/C/C++/C#** (`skeletonize_braced_code`,
   `kriya/workflow/context_budget.py:85-94`): in `tier == "signatures"` mode,
   only `import`/`package` lines and lines containing `class `/`interface
   `/`enum ` are kept. No method or constructor signature survives at all.

Contrast this with the *less* aggressive `"skeleton"` tier
(`skeletonize_braced_code`'s non-signatures branch, `context_budget.py:96+`):
it already has working method-signature detection — it matches a method
declaration via `JAVA_METHOD_SIGNATURE_CORE` (imported from
`kriya.analyzer.analyzer`) and replaces the body with `{ ... }`, keeping the
signature line intact. The `"signatures"` tier should be doing the analogous
thing (keep signature, drop body) but currently does *less* than the
`"skeleton"` tier for methods, which is backwards given `_TIER_STEPS`'
ordering (each successive tier should show less body, not lose entire
interface elements the previous tier had).

**Why this matters (real incident, not hypothetical):** this is the
confirmed root cause of a live `ignite_qpid_protocol` misattribution
incident already tracked in this project's backlog — when a defective file
got degraded to `"signatures"` tier during triage, its constructor/
encode/decode/equals() methods were completely invisible to the model, so
triage guessed a configuration file was the problem instead of the actual
broken class.

## Scope — read this carefully, it's deliberately narrow

Fix **only** the two functions above so that `"signatures"` tier:

- **Python**: keeps each `def ...:` signature line (including multi-line
  signatures that wrap across lines before the closing `:`), replaces the
  body with a single `...` placeholder line (matching how the existing
  non-signatures branch already does this a few lines below, at
  `context_budget.py:69-73`), and still keeps class declarations,
  imports/from-imports, and module/class-level attribute lines exactly as it
  does today.
- **Java/braced languages**: in addition to the existing
  import/package/class/interface/enum lines, also detect and keep method and
  constructor signatures (reuse `JAVA_METHOD_SIGNATURE_CORE` /
  `method_sig_pattern`, the same pattern the `"skeleton"` branch already uses
  a few lines below), with the body replaced by `{ ... }` — i.e. structurally
  the same brace-matching walk the non-signatures branch already does, just
  also applied when `tier == "signatures"`, rather than the current
  from-scratch line-scan that only looks for `class `/`interface `/`enum `
  substrings.

**Do not touch:**
- The `"skeleton"` tier's existing behavior (already correct, don't
  regress it).
- `skeletonize_code`'s non-Python/non-braced fallback branch
  (`context_budget.py:32-35`, the generic "first 15 lines" case) — out of
  scope.
- Anything outside `kriya/workflow/context_budget.py`. In particular, do not
  touch `kriya/workflow/attribution.py`, the Developer agent's output
  contract, `kriya/tools/sandbox.py`, or any egress/network code — those are
  separate, larger items from the same review that are explicitly **not**
  part of this task.
- Do not attempt a broader Graph RAG/symbol-resolution redesign (bare-name
  collision fixes, tree-sitter adoption, embedding zero-vector fix, etc.) —
  those are other, separate items from the same review (P1-10's other
  bullets). This task is scoped to the two skeletonization functions only.

## Acceptance criteria

1. For a representative Python file with a module-level docstring, imports,
   a top-level function with a multi-line signature, and a class with an
   `__init__` and two methods: `skeletonize_python(content, "signatures")`
   output must contain every `def` signature line (module-level and
   class-level) followed by a single-line `...` body placeholder, plus the
   class declaration and imports — and must NOT contain any original
   statement from inside a function/method body.
2. For a representative Java file with a package decl, imports, a class with
   a constructor and two methods (at least one with a multi-line parameter
   list, and at least one with a `{` appearing inside a string literal or
   comment in its body — reuse the existing `_strip_java_comments_and_strings`
   test fixtures/pattern already used to test `skeletonize_braced_code`'s
   `"skeleton"` tier if one exists): `skeletonize_braced_code(content,
   "signatures")` output must contain the package/import lines, the class
   declaration, the constructor signature, and both method signatures, each
   followed by `{ ... }` — and must NOT contain any original method-body
   statement, and must NOT be tricked by a brace inside a string/comment.
3. Existing tests for the `"skeleton"` (non-signatures) tier and for
   `skeletonize_code`'s dispatch/fallback logic must continue to pass
   unmodified — this task must not change `"skeleton"` tier behavior.
4. Add new unit tests covering both fixes (Python and Java) directly in
   whichever existing test file covers `context_budget.py` (find it via
   `grep -rl skeletonize_python tests/` or similar — don't create a new test
   file unless none currently covers this module).
5. Run the full test suite yourself (`.venv/bin/pytest`, create/activate the
   venv per this repo's `README.md`/`CLAUDE.md` if not already set up) and
   confirm no regressions before considering this done. Report the exact
   pass/fail counts in your summary.

## Deliverable

A single, focused commit (or small stack of commits) on this branch
containing only the fix described above plus its new tests. Do not push
directly to `main`, do not open a PR — leave the branch as-is for review.
Do not delete this brief file (`CODEX_TASK_signature_skeletons.md`) as part
of your change; it stays for the reviewer's reference and will be removed
separately after review.
