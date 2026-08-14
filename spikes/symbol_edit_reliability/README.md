# Spike: symbol-level editing vs. text-based search/replace

## The question

Does LSP-based, symbol-level editing (targeting "the method named X in class
Y" via a real language server's symbol table, the approach `oraios/serena`
uses) actually avoid the class of mechanical edit-failure Kriya's current
text-based `apply_anchored_edits()` (search/replace over literal text) has
needed several increments to patch this session - anchor-match failures,
misdirected edits, ambiguous-text rejections - or does it just trade one set
of failure modes for a different one?

Prompted by web research (see the parent conversation) finding an
independent benchmark where AST/symbol-targeted editing had **zero
mechanical failures** across every model tested - the best of every format
compared, including the search/replace style Kriya already uses. This spike
tests that finding directly against Kriya's own real code, not just cites the
research.

## Design principles (this result is informing a real architectural decision)

- **Minimize new, untested code on both sides.** The text-based side calls
  Kriya's real, production `apply_anchored_edits()`
  (`kriya.workflow.edit_safety`) directly - zero reimplementation. The
  symbol-based side reuses Kriya's real, production `JdtlsClient`
  (`kriya.tools.lsp`) for everything it already does well (jdtls process
  lifecycle, JSON-RPC framing, `JAVA_HOME` handling) - the only new code is
  `symbol_client.py`, a small subclass adding exactly one new LSP capability
  (`textDocument/documentSymbol`) plus pure, independently-testable
  functions for symbol lookup and position-based text replacement.
- **The new code is verified BEFORE it's trusted.** `test_symbol_client.py`
  exhaustively tests `symbol_client.py` in isolation - pure unit tests for
  position math and symbol-tree parsing, then live integration tests against
  a real jdtls process and real Java fixture files - and must pass in full
  before `run_comparison.py` (which trusts `symbol_client.py`'s correctness)
  is ever run.
- **Deterministic, hand-crafted scenarios, not LLM-generated.** Isolates
  "does the edit MECHANISM work" from "does a model generate good content" -
  the same separation this whole investigation has kept carefully all
  session. Five scenarios, each mapped to a real thing already observed or
  reasoned through carefully - see `run_comparison.py`'s own `SCENARIOS` list
  for the exact text/symbol inputs and what each is designed to demonstrate.
- **Deliberately fully separate from `kriya/`.** Nothing here modifies any
  file under `kriya/` - `symbol_client.py` only *subclasses*
  `kriya.tools.lsp.JdtlsClient`, never edits it.

## What `symbol_client.py` does NOT attempt (see its own docstring for why each is a real, separate follow-up, not an oversight)

- Overload disambiguation by signature - an ambiguous symbol name fails
  loudly (raises, lists every candidate) rather than guessing, the same
  safety posture `apply_anchored_edits()` and `oraios/serena`'s own
  `_find_unique_symbol()` both already use.
- Editing just a symbol's body (between braces) rather than its whole
  declaration - avoids brace-matching logic entirely, at the cost of
  replacement text needing to include the full signature.
- CRLF line endings / UTF-16 surrogate pairs in position math - not
  applicable to this spike's LF-only, ASCII-only Java fixtures.

## Running

```bash
# 1. Verify the new code is correct, in isolation, first:
.venv/bin/pytest spikes/symbol_edit_reliability/test_symbol_client.py -v

# 2. Only then run the actual comparison:
.venv/bin/python spikes/symbol_edit_reliability/run_comparison.py
```

No new Python dependencies and no new venv needed - everything here runs
against Kriya's own `.venv` (pytest/pytest-asyncio are already there) plus
the `jdtls` binary already required for Kriya's own LSP grounding. First run
of either script may take up to ~2 minutes for jdtls's own project indexing.

## Results

Both pre-verification (`test_symbol_client.py`, 24/24 passing) and the actual
comparison (`run_comparison.py`) ran clean, against a real jdtls process and
the real fixture project - not simulated.

| Scenario | text-based | symbol-based | Designed outcome |
|---|---|---|---|
| `clean_edit` | SUCCESS | SUCCESS | both succeed (baseline) |
| `whitespace_drift` | SUCCESS | SUCCESS | both succeed |
| `ambiguous_duplicate_text` | REJECTED | SUCCESS | text rejects, symbol succeeds |
| `overloaded_method_no_disambiguation` | SUCCESS | REJECTED | text succeeds, symbol rejects |
| `misdirected_content` | REJECTED | REJECTED | both reject |

All five scenarios behaved exactly as designed
(`run_comparison.py`'s own printed summary: `All scenarios behaved as
designed: True`). Every successful edit on either side was additionally
re-checked with Kriya's real `find_structural_corruption()` and produced
valid Java - no silent position-math corruption on either path.

**What this shows, precisely:**

- **`ambiguous_duplicate_text` is the scenario that matters most.** This
  reproduces a real failure shape text-based editing has actually hit this
  session (a target method's body text also appears, verbatim, somewhere
  else in the file - here, in an adjacent comment). Text-based correctly
  *rejects* rather than guessing (`apply_anchored_edits()`'s uniqueness
  check doing its job), but rejecting means the retry loop burns an attempt
  and has to re-ask the model for more surrounding context. Symbol-based
  succeeds on the first try, because a symbol name isn't text - a comment
  containing the same words isn't a symbol, so there's nothing to be
  ambiguous about.
- **`overloaded_method_no_disambiguation` is the real, honest tradeoff, not
  a symbol-based win.** When two overloads share a name and the search text
  itself is what disambiguates them (the two `describe(...)` variants),
  text-based succeeds and this spike's deliberately name-only symbol
  resolution correctly refuses to guess. This is not a bug in
  `symbol_client.py` - it is the intentionally-scoped behavior documented in
  its own docstring (overload disambiguation by signature was explicitly
  named as unattempted). A production implementation would need
  signature-aware symbol resolution to close this gap; until then, this is
  a real case where symbol-based editing is *worse*, not better.
- **`misdirected_content` shows both mechanisms have a real safety net.**
  Content that actually belongs to a different file gets rejected on both
  sides, for different underlying reasons (context-elision detection for
  text; "no such symbol" for symbol lookup) - neither approach silently
  applies content to the wrong place.
- **`clean_edit` and `whitespace_drift` confirm no regression on the easy
  cases** - symbol-based editing doesn't trade reliability on simple edits
  for its win on the ambiguous-text case.

**Bottom line:** for the specific mechanical-failure class this session's
error-resolution work actually hit repeatedly (a target's own text
recurring elsewhere in the file, causing an anchor-match rejection),
symbol-based editing genuinely avoids it - confirmed against Kriya's real
edit-safety code and a real jdtls process, not just cited from external
research. It is not a strict improvement across the board: overload
disambiguation is a real, named gap that would need closing (signature-aware
symbol resolution) before symbol-based editing could safely replace
text-based editing outright, and it remains Java-only (gated on jdtls),
where text-based editing is language-agnostic. This matches the calibrated
answer given before the spike was built: yes, for a specific mechanical
subset; no effect on content-correctness or overall retry-budget issues;
Java-only.
