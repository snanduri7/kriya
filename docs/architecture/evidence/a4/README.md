# Evidence for slice A4

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`. Nothing here changes runtime code,
tests, goldens or the registry. No model was called and no network request was made (the two network functions the
probe touches are replaced by local stubs inside the probe process).

| File | What it is | Result |
|---|---|---|
| `a4_probe.py` | Model-free probe P1 to P11, calling the real functions with hand-built inputs | `a4_probe_output.txt` |
| `a4_probe_output.txt` | Output on this baseline | P1: requirement digest is stable and equals `goal_identity`; "Make it better." derives `REQ-1`; a clarification would mint `REQ-C1`. P2: a direct unit carries the goal as `requirement_goal`; an enforce unit carries `None`. P3: one structured plan is `complete` for the direct classifier and has six reason codes for `validate_plan`. P4: the Planner's skill selection is `[alpha-skill]`, unit s2's is `[alpha-skill, beta-skill]`. P5: an empty folder named after the artifact removes the knowledge gap; a coordinate named only by Planner text stops an enforce unit. P6: the lookup sanitizer accepts non-public names and refuses paths, code and long text. P7: fetched page text is in the retry error context, outside any untrusted fence. P8: a workspace skill cannot attest itself; its rule is rendered under the trusted `Rules:` header. P9: the plan hash covers `plan_id`. P10: packaged defaults are direct, human-in-the-loop, `record` / `record`; the Project Setup profile is enforce, human-in-the-loop, and sets no requirement policy. P11: the template the knowledge guard scaffolds (no rule, unverified) removes the gap |
| `run_a4.sh` | Runs `a4_probe.py` and two committed test files, unchanged | `a4_tests_output.txt`: 37 passed (7 in `tests/test_greenfield_runtime_verification_dropped.py`, 30 in `tests/test_plan_external_package_capability_001.py`) |
| `check_citations_a4.py` | Checks that every `path::symbol` cited by the A4 rows exists at the checked-out tree, and runs 18 negative-control searches | `check_citations_a4_output.txt`: 49 files, 218 symbols, 18 controls, 0 problems |
| `coverage_overrides_a4.json` | The A4 registry coverage entries (18 A4 rows and the row handed over by A3) | merged into `../a1/coverage_overrides.json`: 80 entries; `REGISTRY-COVERAGE.md` regenerated: 73 of 141 reconciled, 7 `NEEDS_CHARACTERIZATION`, 61 pending |

How it was run (from the closure worktree root; interpreter of the `Kriya-product-72h` checkout; isolated state):

```
sh docs/architecture/evidence/a4/run_a4.sh <venv python> $PWD <tmp>
python3 docs/architecture/evidence/a4/check_citations_a4.py .
python3 docs/architecture/evidence/a2/merge_overrides.py docs/architecture/evidence/a1/coverage_overrides.json docs/architecture/evidence/a4/coverage_overrides_a4.json
python3 docs/architecture/evidence/a1/gen_coverage.py . docs/architecture/evidence/a1/coverage_overrides.json
```

P11 was added after the first run; the probe alone was then run again with the same environment (the test output is
from the first run and the tests were not re-run, as nothing they read changed).

A correction made inside this slice, before the checkpoint: the first run of `check_citations_a4.py` failed three
negative controls. Two were statements of the A4 draft that were wrong or incomplete: the knowledge gate has three
callers, not two (the third reads the Architect's design, `stage_architecture.py`), and `tools/knowledge.py` does
contain `skill.yaml`, because it writes a skill template (`generate_skill_template`). Following the second one led to
P11 and `MUT-A4-11`. The third (`risk_threshold_lines` in `config/authority.py`) is the field's classification, not a
reader. The rows were corrected and the controls restated; the first failing output is not preserved, this paragraph
is its record.

Limits of this evidence, stated once:

- No enforce run and no direct run was driven. P2 builds the invocation exactly as `_invoke_bounded_subtask` does;
  P3 calls the two validators directly; P4 calls the selection predicate with real collaborators and synthetic
  skills. Everything about the order of steps inside a run is `TRACED`.
- P5 and P11 run the knowledge guard offline: the release-date (temporal) check is skipped, so they measure the
  structural (skill coverage) half only.
- P7 measures the function that builds the augmented error text. Where that text sits in the Developer request was
  not read (STAGE-GENERATION, slice A6); its position at the head of the Planner prompt is `TRACED`
  (`stage_planning.py`).
- `validate_plan` (lines 565 to 1418) was read at its signature, docstring and reason-code inventory. `milestones.py`,
  `shadow_mode.py`, `plan_revision.py`, `triage.py`, `skills/run_shadow.py`, the registry adapters and the CLI
  intake and confirmation code were not read.
- `GAP-A4-01` item (c) (two closers unreachable under enforce) is `TRACED` from the guard in `development_loop.py`,
  the terminal call arguments and P2; the closers were not executed.
- Whether a resumed enforce run keeps its run id is UNKNOWN (`GAP-A4-06`).
