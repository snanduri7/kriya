# Codex task: fix skeletonization bugs in `kriya/workflow/context_budget.py`

## Context

Kriya's Graph RAG context assembler degrades files through skeletonization
tiers when a generation prompt is over its token budget: `full -> skeleton ->
signatures` (`context_budget.py:247`, `_TIER_STEPS`). This file was flagged
by an external design review (`KRIYA_DESIGN_REVIEW_AUGUST_17.txt`, item
P1-10) as producing bad output at the `"signatures"` tier. Every claim below
was independently re-verified by actually **running** the functions against
real sample code (not just reading them) before writing this brief — the
review's own description undersold two of these bugs and missed a third
entirely, so treat the repros below as ground truth over the review text.

There are **three separate, confirmed bugs** in this file. Fix all three.
They live in the same two functions and share a root cause (`"signatures"`
mode has never had a coherent model of "keep every declaration's interface,
drop every body"), so fixing them together is less work and less risk than
fixing them separately.

---

## Bug 1 — Python `"signatures"` tier (`skeletonize_python`, lines 61-67)

Confirmed repro — run this yourself first to see the actual current output
before changing anything:

```python
from kriya.workflow.context_budget import skeletonize_python

py_sample = '''import os
from typing import List

def top_level_fn(x, y):
    z = x + y
    if z > 0:
        return z
    return -z

class Foo:
    """docstring"""
    attr = 1

    def __init__(self, x):
        self.x = x
        self.y = compute(x)

    def method_a(self, val):
        result = val * 2
        return result
'''
print(skeletonize_python(py_sample, "signatures"))
```

Current (buggy) output:

```
import os
from typing import List

    z = x + y
    if z > 0:
        return z
    return -z

class Foo:
    """docstring"""
    attr = 1
```

Two distinct problems, not one:
- `top_level_fn`'s **signature line is dropped but its body leaks through
  completely unlabeled** — those four orphan lines have no indication they
  belong to a function at all.
- `__init__`/`method_a` and their bodies are **dropped entirely** — not even
  a placeholder.

Required fix: `"signatures"` tier must, for every `def` (top-level or
class-level, at any nesting depth), keep the signature line (including
multi-line signatures that wrap before the closing `:`) and replace the body
with a single `...` placeholder line at the correct indent — i.e. the exact
behavior the `"skeleton"` (non-signatures) branch a few lines below
(`context_budget.py:69-73`) already implements correctly for `def` lines.
Reuse that logic rather than reinventing it; the difference between
`"skeleton"` and `"signatures"` for Python should end up being: `"skeleton"`
also keeps other class/module-level statements verbatim, `"signatures"`
should not (only defs + class/import lines + the `...` placeholders).

Verify by running the same sample through `"signatures"` after your fix and
confirming: every `def` line present with a `...` placeholder immediately
after it, no orphan body statements anywhere, class/import lines unchanged
from today's (already-correct) behavior.

---

## Bug 2 — Java/braced `"signatures"` tier (`skeletonize_braced_code`, lines 85-94)

Confirmed: only `import`/`package` lines and lines containing `class
`/`interface `/`enum ` survive. No method or constructor signature at all.
This is the change directly implicated in a real, already-tracked
`ignite_qpid_protocol` misattribution incident (see backlog) — a defective
class's constructor/encode/decode/equals() methods were completely invisible
at this tier during failure triage, so the model guessed a configuration
file instead of the actually-broken class.

Required: `"signatures"` tier should keep package/import lines, class/
interface/enum declaration lines (as today), **plus** every method and
constructor signature with its body replaced by `{ ... }` — i.e. structurally
the same brace-walk the `"skeleton"` branch below already does for method
bodies, but also applied at `"signatures"` tier, with non-signature lines
(fields, blank lines) dropped rather than kept. See Bug 3 immediately below
before implementing this — the existing brace-walk's method detection has a
real gap you need to fix as part of the same change, not carry forward.

---

## Bug 3 — constructor-detection is broken in the EXISTING `"skeleton"` tier too (not just signatures tier)

This is a bug in code the design review assumed was already correct. Found
by testing, not in the review. Confirmed repro:

```python
from kriya.workflow.context_budget import skeletonize_braced_code

java_sample = '''public class Bar {
    public Bar(String name) {
        this.name = name;
    }

    public String getName() {
        return name;
    }
}
'''
print(skeletonize_braced_code(java_sample, "skeleton"))
```

Current (buggy) output:

```
public class Bar {
    public Bar(String name) {
        this.name = name;
    }

    public String getName()  { ... }
}
```

The constructor's entire body leaks through **unelided** — only `getName()`
correctly collapses to `{ ... }`.

**Root cause**, confirmed by direct regex testing (do not skip re-deriving
this yourself — it's counterintuitive): `JAVA_METHOD_SIGNATURE_CORE`
(`kriya/analyzer/analyzer.py:45`) is `(?:public|protected|private|static|
\s)+[\w<>]+\s+(\w+)\s*\([^\)]*\)` — it requires **two** whitespace-separated
tokens before the `(` (a return type, then a name). A constructor has only
one token (the class name) before its `(`. The pattern still *sometimes*
matches a constructor anyway — not because constructors are supported, but
because `method_sig_pattern.search()` is unanchored and the accumulated
`buffer` for that class member often still contains leftover
newline/indentation *from the previous member* after `.strip()` only trims
the buffer's outer edges — enough incidental whitespace for the regex
engine to treat the constructor's own modifier keyword (`public`) as the
"return type" slot and the class name as the "name" slot. When a
constructor is the **first** member of a class (nothing before it to leave
that incidental whitespace behind), there's nothing left after `.strip()`
and the match fails — which is exactly the repro above. This is
whitespace-position-dependent, not a real feature; don't rely on it and
don't extend it as-is.

**Required fix — must satisfy both of these constraints:**

1. **Correctness, not just coverage.** Detect a constructor by tracking the
   name of the class/interface/enum the brace-walk is currently inside (a
   name *stack*, pushed when entering a class/interface/enum body and popped
   on its matching closing brace — plain top-level classes are the common
   case and must work correctly; if you determine correct nested-class
   tracking is materially riskier to get right, it is safe and acceptable to
   leave a nested class's own constructor unelided in that case (matching
   today's behavior — never worse than now) rather than risk an incorrect
   collapse. Regular top-level classes must always work correctly regardless
   of member order.
2. **Zero false positives on control flow.** A method body containing
   `if (...)`, `for (...)`, `while (...)`, `switch (...)`, `catch (...)`,
   `synchronized (...)`, or `try` must **never** be mistaken for a nested
   method/constructor and collapsed — these already don't match today's
   `JAVA_METHOD_SIGNATURE_CORE` pattern (verified: it requires two tokens
   before `(`, and `if`/`for`/etc. only ever have one), and matching a
   constructor by exact-equality against the tracked enclosing class name
   preserves this safety property for free — no Java keyword can ever be a
   valid class name, so an exact-name match can't accidentally fire on
   control flow. **Do not** implement constructor detection by simply
   loosening the token-count requirement (e.g. "one token before `(` is
   enough") — that reintroduces exactly this false-positive risk. Write a
   test with a method containing nested `if`/`for`/`try` blocks and confirm
   none of them get treated as their own member.

Verify by running the repro above after your fix: constructor collapses to
`{ ... }` regardless of whether it's the first, middle, or last member of
its class. Also add a test with two constructors before any other member
(overloaded constructors), and one with the constructor last, to confirm
position independence generally, not just the one repro case.

---

## Scope — deliberately narrow beyond these three bugs

**Do not touch:**
- `skeletonize_code`'s non-Python/non-braced fallback branch
  (`context_budget.py:32-35`, the generic "first 15 lines" case).
- Anything outside `kriya/workflow/context_budget.py` and, if needed,
  reading (not modifying) `kriya/analyzer/analyzer.py` for
  `JAVA_METHOD_SIGNATURE_CORE`'s definition/usage elsewhere to confirm you
  aren't breaking its other call site (`analyzer.py:181`) — you should not
  need to change that file at all.
- `kriya/workflow/attribution.py`, the Developer agent's output contract,
  `kriya/tools/sandbox.py`, or any egress/network code — separate, larger
  items from the same review, explicitly not part of this task.
- Any broader Graph RAG/symbol-resolution redesign (bare-name collision
  fixes, tree-sitter adoption, embedding zero-vector handling, etc.) — other
  items from the same review, out of scope here.
- Enum constant bodies (`ENUM_CONST(args) { ... }` anonymous-class-like
  syntax) — genuinely more complex than a normal constructor; leaving them
  unelided (today's behavior) if you find correct handling is not
  straightforward is acceptable. Do not guess at a wrong collapse.

## Acceptance criteria

1. Bug 1 repro (above) produces the corrected output described.
2. Bug 2: a representative Java file with a constructor and two methods
   (one with a multi-line parameter list) at `"signatures"` tier keeps
   package/import/class lines plus every constructor/method signature each
   followed by `{ ... }`, and drops field declarations and blank
   implementation detail — no method-body statement anywhere in the output.
3. Bug 3 repro (above) produces the corrected output (`{ ... }` for the
   constructor) — test constructor-first, constructor-last, and multiple
   overloaded constructors.
4. False-positive test: a method body containing `if`/`for`/`try` blocks
   must still collapse to a single `{ ... }` for the method itself, with
   none of the nested control-flow blocks separately mis-detected.
5. Existing tests for `"skeleton"` (non-signatures) tier's *method* handling
   and for `skeletonize_code`'s dispatch/fallback logic must continue to
   pass unmodified — confirm via
   `grep -n skeletonize tests/test_workflow.py tests/test_milestone2.py`
   before you start, and re-run them after. (Note: as of writing this brief,
   no existing test covers constructor handling at all, so Bug 3's fix has
   no existing test to preserve or contradict — you're adding coverage
   that didn't exist, not changing asserted behavior.)
6. Add new unit tests for all of the above directly in
   `tests/test_workflow.py` near the existing
   `test_skeletonize_braced_code_ignores_braces_inside_string_literals` test
   (or wherever you determine skeletonization tests belong after checking
   both files) — don't create a new test file.
7. Run the full test suite yourself
   (`.venv/bin/pytest`, set up the venv per this repo's README/CLAUDE.md if
   not already present) and confirm no regressions before considering this
   done. Report the exact pass/fail counts in your summary.

## Deliverable

A single, focused commit (or small stack of commits) on this branch
containing only the fixes described above plus their new tests. Do not push
directly to `main`, do not open a PR — leave the branch as-is for review.
Do not delete this brief file (`CODEX_TASK_signature_skeletons.md`) as part
of your change; it stays for the reviewer's reference and will be removed
separately after review.

If, while implementing, you find the "safe fallback" constraints above
(never collapse incorrectly, leave unelided if uncertain) are in tension
with actually fixing Bug 3 for a case not covered by this brief's tests,
stop and describe the tradeoff in your summary rather than guessing —
correctness of what IS collapsed matters more than maximizing what gets
collapsed.
