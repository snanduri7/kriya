# Evidence for slice A6

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`. Nothing here changes runtime code,
tests, goldens or the registry. No model was called, no network request was made, no qualification, certification,
MCP server or container ran. The live measurements A6 reconciles against are the preserved ones of 2026-11-14
(`handover/PRODUCT-72H/SPRINT_LOG.md` section 42); they were not repeated.

| File | What it is | Result |
|---|---|---|
| `a6_probe.py` | Model-free probe P1 to P7, calling the real functions over scratch directories | `a6_probe_output.txt` |
| `a6_probe_output.txt` | Output on this baseline | P1: text fetched by the live lookup is in `state.error_context` with no fence; a fence END marker and a `=== Task ===` heading inside the page are kept verbatim; it is in the Developer task description outside any fence, with and without a retry package (with one, under "Advisory reference evidence" and not in `prior_error_text()`); it is inside the Reviewer's system prompt, in the block headed AUTHORITATIVE RUN DISPOSITION; a compiler-shaped line in it produces Kriya's incompatible-types scaffold; a test file name in it changes the targeted-test selection from none to that file. P2: registered adapters `ollama` (default) and `ollama_native`; `vllm`, `openai` and `Ollama` are refused; protocol versions `kriya-openai-compat/3`, `kriya-ollama-native/3`. P3: budget window = min(requested, ceiling), never enlarged by a larger served window; served below requested is refused in every profile; served different from requested is refused under an exact identity. P4: an evidence store verifies as written; one edited record is `CHAIN_BROKEN`; every record and the seal rewritten consistently is `VERIFIED` with the terminal status changed; a removed seal is `UNSEALED`. P5: a second write of one run id replaces the history row; the row has no workspace, digest or writer column. P6: the application outcome of a non-zero exit is failed without, and succeeded with, the grounded expected-exit disposition. P7: an unknown model name resolves to the primary's binding |
| `run_a6.sh` | Runs `a6_probe.py`, then `check_citations_a6.py` | probe exit 0, citations exit 0 |
| `check_citations_a6.py` | Checks that every `path::symbol` cited by the A6 rows exists at the checked-out tree, and runs the negative-control searches | `check_citations_a6_output.txt`: 102 file entries, 398 symbols, 47 controls, 0 problems |
| `coverage_overrides_a6.json` | The A6 registry coverage entries (35 A6 rows, the five rows handed over by A3 and A4, the two container rows A5 declared) | merged into `../a1/coverage_overrides.json`: 141 entries; `REGISTRY-COVERAGE.md` regenerated: 138 of 141 reconciled, 3 `NEEDS_CHARACTERIZATION` (declared Phase-D items), 0 pending |

How it was run (from the closure worktree root; interpreter of the `Kriya-product-72h` checkout; isolated state):

```
sh docs/architecture/evidence/a6/run_a6.sh <venv python> $PWD <tmp>
python3 docs/architecture/evidence/a2/merge_overrides.py docs/architecture/evidence/a1/coverage_overrides.json docs/architecture/evidence/a6/coverage_overrides_a6.json
python3 docs/architecture/evidence/a1/gen_coverage.py . docs/architecture/evidence/a1/coverage_overrides.json
```

Isolation of the probe: the runner sets `KRIYA_STATE_DIR`, `KRIYA_LOG_DIR` and `KRIYA_MODEL_RUNTIME_PROBE=0`; the
probe itself, before importing Kriya, sets a scratch `HOME`, `KRIYA_AUTHORITY_HOME` and `KRIYA_MCP_APPROVAL_HOME`.
P1 replaces the search and fetch functions with scripted ones for the duration of one call (no request leaves the
process). P4 and P5 write only inside the probe's scratch directory. The line `scratch: ...` differs per run;
nothing else does.

Read-only measurements made outside the probe, each named in the row that uses it:

- `~/.kriya/logs/runs/20261113T060030-40a14267/kriya.log` exists (1,739 lines, 67 WARNING lines) and holds the
  06:04:43 `kriya.tools.gate_view` warning at line 81; file mode `0644`, parent `~/.kriya` mode `0700`; 732 run
  directories under `~/.kriya/logs/runs` (registry `GUI-KRIYA-LOG-NOT-PERSISTED-001`, `MUT-A6-11`).
- `git merge-base --is-ancestor 82436eb 3eded93` exits 0: the revision of the last green full suite is an ancestor
  of the baseline (the stale test rows).
- `git ls-files handover/BACKEND-READINESS-V2/gate-certification/__pycache__` lists three tracked `.pyc` files
  (`WAVE8-TRACKED-BYTECODE-001`).

Corrections made inside this slice, before the checkpoint (the citation script was written before the rows and run
early, as the method requires):

- The first probe run failed P3's set-up (a wrong enum member name in the probe); fixed, re-run, measured.
- The first citation run failed three negative controls and two symbols. `fallback_incompatibilities(` is also in
  `workflow/attempt.py`, which holds only its lazy loader (added to the allowed list with that reason). The needle
  `qualification` matched a capability flag in `kup/inspect.py` and `signature` matched "failure signature" in the
  recorder; both needles were too broad and were narrowed to `model_qualification` and `private_key`. The two
  symbols were quoted with a leading escape sequence the boundary rule rejects; re-quoted. No statement in the rows
  depended on the wrong forms.

What A6 did not establish (also in the limitation field of each row): the native adapter's request and response
code, the qualification cases, routing internals, the fitting internals of `context_budget.py`, `attempt.py` beyond
the retry call sites, RunRecord storage (`control/persistence.py`), the release scripts and the certification matrix,
`plan_revision.py`, the UI code, and anything that needs a live model, a container or an MCP server.
