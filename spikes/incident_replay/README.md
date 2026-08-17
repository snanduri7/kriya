# Incident replay spike

On its own branch (`spike/incident-replay-harness`), never merged into
`main` without an explicit ask - the point is to build this without any
risk to whatever's actively being validated/demoed on `main`.

## Question this answers

Every bug found in the 2026-08-16/17 diagnosis-mismatch investigation
(docs/design.md §§7.23-7.26 - 10 real bugs across 3 rounds of external
review) was root-caused by hand: grep a raw completion out of a DEBUG-level
`kriya.log`, hand-reconstruct the string (stripping the log prefix,
un-escaping `\n`, guessing where the repr ends), then re-type it into a
throwaway Python one-liner to feed through `DeveloperAgent._split_fix_
analysis_edit()` / `find_edits_ignoring_own_diagnosis()` / etc. That worked
every time, but slowly, and the reconstruction step itself was a real
source of error early on (see docs/design.md §7.23's own account of a
buffering-ambiguity dead end in the `b-10c` investigation, before DEBUG
capture existed). This asks: **can that whole manual loop be replaced with
one deterministic command, given a log already captured at DEBUG level?**

## How it works

`replay_attempt.py`:
1. `extract_raw_completions(log_path)` - scans a DEBUG-level `kriya.log` for
   every `"Developer raw completion for '<file>' (pre-parse): <repr>"` line
   (added to `kriya/agents/agent.py::_fill_missing_content` during the
   diagnosis-mismatch investigation), and recovers the exact original
   string via `ast.literal_eval` on the logged `repr()` - the precise
   inverse of how it was written, not an approximation. Also does a
   best-effort scan of the preceding `"Quality Gates FAILED"` line to guess
   which `Failure.type` this retry was responding to (a debugging aid only -
   Kriya's own `state.budgets.last_failure_signature` tracking is more
   nuanced across multi-attempt chains than "whatever the last log line
   said," so treat the guess as a starting point, override with
   `--fail-type` when you know better).
2. `replay(...)` - runs the SAME real, deterministic pipeline a live attempt
   would: `_split_fix_analysis_edit()` to parse analysis/edits/content,
   `find_structural_corruption()` against the candidate, and (when a
   `--prior-content-file` is supplied) `find_edits_ignoring_own_diagnosis()`
   plus the `static_rule_violation`/`compile` bypass logic from
   `attempt.py::_diagnosis_mismatch_bypass_reason()`. No live LLM call
   anywhere in this path.

## Known limitation

`find_edits_ignoring_own_diagnosis()` needs the target file's content
*before* the edit (`orig_text`/`prior_content`) to fully evaluate - and
that's usually **not** independently captured anywhere in a DEBUG log the
way the raw completion is. Without `--prior-content-file`, the tool still
shows the parsed analysis/edits/structural-corruption result and says so
explicitly (`"SKIPPED - no --prior-content-file supplied"`) rather than
guessing at a result. Recovering prior-content automatically (e.g. from the
full prompt's own `openai._base_client: Request options: {...}` DEBUG dump,
which usually shows the model the current file content) is a real next
step if this tool earns its keep, not attempted in this first pass.

## Smoke-tested against

`spikes/eval_harness/runs/b-10m/workspaces/ignite_qpid_person/logs/
kriya.log` - correctly extracted all 14 raw completions in that run
(cross-checked against the hand-extracted set from the live investigation),
and correctly reproduced the known verification-marker-wrap incident
(index 3: `diagnosis_mismatch: None`, confirming signal (e) resolves it
directly - see docs/design.md §7.25) when given the real prior content.
