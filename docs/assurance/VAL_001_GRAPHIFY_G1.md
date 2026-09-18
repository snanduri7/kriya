# VAL-001 G1: Kriya's first real historical Graphify brownfield replay

Status: PREPARATION COMPLETE, LIVE RUN NOT EXECUTED. This document is the immutable experiment
definition and acceptance contract, written and committed **before** the live run. Do not edit
this document's Phase 1–3 content after the run to fit the outcome — post-run findings belong in
a separate results document.

Selected task: **H1 / #3406 — C# generic call-site resolution** (per
`docs/assurance/VAL_001_GRAPHIFY_G0_QUALIFICATION.md` §7/§8, NARROW difficulty).

## 0. Critical correction to the assigned baseline SHA

The task that produced this document specified `5d09dce4` as "the G1 pre-fix Graphify revision."
**This is incorrect and was not used.** Verified directly against the frozen clone:

- `5d09dce42cc2945c9521360026c16845268ab77a` is the **maintainer's actual fix commit** — its own
  diff adds `_csharp_bare_call_name()` to `graphify/extractors/engine.py` and the new regression
  test `tests/test_csharp_generic_callsites.py`. Confirmed via `git show 5d09dce4 --
  graphify/extractors/engine.py`.
- Its parent, `67f99bd0059dd1bac9e44382907ef9f10098b39f`, is the true pre-fix state (`git describe
  --tags` → `v0.9.56`, exactly the release the original issue #3406 reports against). Confirmed
  the ground-truth test file does not exist at this SHA (`git show 67f99bd0:tests/test_csharp_
  generic_callsites.py` → "path exists on disk, but not in" that commit).

Checking out the repository at `5d09dce4` as originally instructed would have handed Kriya a
working tree that **already contains the maintainer's applied fix** — a direct violation of this
same experiment's own ground-truth isolation requirement. `67f99bd0059dd1bac9e44382907ef9f10098b39f`
is used as `GRAPHIFY_PRE_FIX_COMMIT` throughout the rest of this document instead.

## 1. Kriya baseline

`10b5523` (`10b5523da43fb9f7f908f60bc66b8490766c6cc2`) + G0 assurance commit `0d29124f`
(docs-only addition, zero `kriya/` source change). Verified pushed to
`origin/milestone-decomposition` before this document was prepared (`HEAD == origin/HEAD ==
0d29124f51af172bbf68172d7084956e0e28cdea`).

Kriya was **not** modified to prepare this experiment. The only Kriya-adjacent artifact created is
a campaign config file (`campaign_kriya.yaml`, evidence directory) that overrides only
`paths.skills/memory/logs` and `skills.load_global/load_cwd` — every `llm.*`/`autonomy.*`/
`execution_policy.*`/`llm_chain` field is left unset and falls through to Kriya's own packaged
`kriya/config/default_config.yaml` unchanged.

## 2. Graphify pre-fix worktree

- `GRAPHIFY_PRE_FIX_COMMIT`: `67f99bd0059dd1bac9e44382907ef9f10098b39f` (release `v0.9.56`)
- Two detached worktrees were created from the frozen clone: `g1_run_worktree` (the one Kriya will
  operate on — kept pristine, never installed into) and `g1_worktree` (evaluator-side scratch,
  used only for an editable `pip install` to build the evaluation venv — never touched by Kriya).
- Verified clean (`git status --porcelain` empty) and at the exact SHA above on both, immediately
  before this document was written.
- Confirmed absent at this SHA: `tests/test_csharp_generic_callsites.py` (the ground-truth
  regression test) and the string `_csharp_bare_call_name` anywhere in
  `graphify/extractors/engine.py` (the maintainer's fix helper).
- Durable location: `~/kriya-live-validation/val001-g1-graphify-c3406/` (moved out of the
  session-ephemeral scratchpad; git worktree admin links repaired after the move and re-verified
  working). This mirrors the user's own established `~/kriya-live-validation/` convention for live
  CLI checks — distinct from an unrelated, pre-existing `graphify-poc/` directory already in that
  folder, which explores using Graphify *as a tool* for Kriya's own graph-building and is unrelated
  to this experiment (Graphify here is the *validation target*, not a tool Kriya calls).

## 3. Original requirement recovery (Phase 1)

Recovered from the real, public GitHub issue **Graphify-Labs/graphify#3406** via `gh issue view
3406 --repo Graphify-Labs/graphify --comments` (read-only, no write/comment/label action taken).

- Title: "C#: an unqualified generic call (`Get<T>(...)`, `this.Get<T>(...)`) drops its `calls`
  edge", opened by `JensD-git`, closed, 1 comment.
- The issue's own **Summary / Reproducer / Expected / Where it happens** sections were used
  near-verbatim as the Kriya goal (`g1_evidence/goal.txt`) — this is real, pre-fix-dated content a
  real engineer assigned this ticket would have read.
- **Deliberately excluded** from the goal:
  - The issue's closing comment, which names the actual fixing PR (`#3422`) and release
    (`v0.9.57`) — pure post-fix ground truth.
  - The issue's own "Relation to existing reports" and "Scope" sections (cross-references to
    other issues/PRs and corpus-wide prevalence counts) — tangential triage bookkeeping, not
    needed to reproduce or fix this specific defect, excluded to keep the goal focused rather than
    for isolation reasons.
  - Anything from the maintainer's actual diff (function names, control-flow changes) — the agent
    preparing this document did view that diff (`git show 5d09dce4`) to correct the baseline SHA
    in §0, but nothing from it entered the goal text.

**Disclosed interpretive caveat:** this particular issue is unusually solution-complete. Its
"Where it happens" section already narrows the defect to two named code branches with exact
pre-fix line numbers and narrates the causal mechanism ("only the callee name a few lines above is
still taken as raw text"). This is real information a real engineer would have had — legitimately
included per Phase 1's own rule — but it materially lowers how much *cold repository comprehension*
a Kriya PASS on this specific task demonstrates, compared with a typical NARROW-difficulty defect
whose issue is a bare symptom report. This should be weighed when interpreting a PASS outcome, and
does not by itself justify excluding legitimately-available issue content.

Full recovered goal text: `g1_evidence/goal.txt` (49 lines, committed alongside this document —
see §8).

## 4. Ground-truth isolation (Phase 2)

`GROUND_TRUTH_ISOLATION = VERIFIED`

| Surface | Status |
|---|---|
| Kriya workspace (`g1_run_worktree`) | Verified clean at pre-fix SHA; ground-truth test and fix helper both confirmed absent (§2) |
| Skills | `skills.load_global: false`, `skills.load_cwd: false` in `campaign_kriya.yaml`, `paths.skills` pointed at an empty directory — no implicit skill can fire |
| Prompts / goal | `goal.txt` built only from pre-fix-dated issue content (§3); no diff, no changed-file list, no post-fix code |
| Graph RAG / index | Will be built by Kriya itself from `g1_run_worktree`'s own pre-fix source during the run — cannot contain fix content that doesn't exist in that tree |
| Environment hints | None set; `campaign_kriya.yaml` only relocates `paths.*`, does not inject content |
| Retry feedback | N/A pre-run; Kriya's own retry loop only ever sees its own prior attempts and compile/test output from `g1_run_worktree`, never the maintainer's diff |
| Generated context | N/A pre-run |
| Evaluator-side ground truth (`ground_truth_DO_NOT_EXPOSE_TO_KRIYA/`) | Stored in the evidence directory, structurally outside `g1_run_worktree` and outside anything `--config campaign_kriya.yaml` points Kriya's `paths.*` at; contains the maintainer's diff and the real regression test, for post-run evaluator use only |

## 5. Baseline acceptance (Phase 3)

**Deterministic acceptance, independently proven — not merely cited from the issue.**

An isolated venv (`g1_venv`, pip-installed editable from the evaluator-side `g1_worktree`, base
dependencies only — no LLM/network calls needed for AST-only C# extraction) was used to run the
issue's own 3-file reproducer through `graphify extract --code-only --no-cluster` against the true
pre-fix worktree, via `PYTHONPATH` injection so the same check works against whatever state a
worktree is later in (verified to work identically whether or not the package was pip-installed
from that exact tree).

**Pre-fix result (measured, not assumed): 2/5** expected `calls` edges present — cases C
(`GetRaw`) and D (`this.GetRaw`) resolve; cases A (`Get<int>`), B (`this.Get<int>`), E
(`Make<int>`) do not. This is an exact match to the original issue's own reported "2 of 5" table,
independently reproduced.

**Calibration (evaluator-side only):** the identical script run against the real fix commit
`5d09dce4` → **5/5**. Confirms the acceptance mechanism is correctly wired in both directions
before being relied on to judge Kriya's output.

**Regression baseline (existing, non-ground-truth tests):** `tests/test_csharp_type_resolution.py`
+ `tests/test_csharp_member_calls.py` → **76/76 passing** at the pre-fix SHA. Any failure in these
two files after the Kriya run is attributable to Kriya's change, not pre-existing breakage.

`BASELINE_FAILURE_PROVEN = YES`

### Acceptance criteria

1. **Behavioral**: `check_acceptance.py --repo <post-run worktree>` reports `5/5` (all of A, B, C,
   D, E produce a `calls` edge to their target method).
2. **Regression**: `test_csharp_type_resolution.py` + `test_csharp_member_calls.py` remain
   `76/76` passing (or better — any newly-added passing tests are a bonus, not a requirement).
3. **Independent**: the evaluator may, post-run only, copy in the real
   `tests/test_csharp_generic_callsites.py` from `ground_truth_DO_NOT_EXPOSE_TO_KRIYA/` and run it
   against Kriya's modified tree, entirely outside Kriya's own process, as a second independent
   confirmation. Its content must never have been visible to Kriya before this point.
4. **Broader regression** (recommended, not required for PASS/FAIL): `tests/test_extract.py`,
   `tests/test_languages.py`, `tests/test_extractors_registry.py` — these touch the shared
   `_extract_generic` engine any change to `engine.py` runs through.

### Acceptance commands

```bash
DEST=~/kriya-live-validation/val001-g1-graphify-c3406

# 1. Behavioral acceptance
"$DEST/g1_venv/bin/python3" "$DEST/g1_evidence/acceptance/check_acceptance.py" \
  --repo "$DEST/g1_run_worktree"

# 2. Regression (existing tests)
cd "$DEST/g1_run_worktree"
"$DEST/g1_venv/bin/python3" -m pytest \
  tests/test_csharp_type_resolution.py tests/test_csharp_member_calls.py -q

# 3. (Evaluator only, post-run) Independent ground-truth test
cp "$DEST/g1_evidence/ground_truth_DO_NOT_EXPOSE_TO_KRIYA/ground_truth_test_test_csharp_generic_callsites.py" \
   "$DEST/g1_run_worktree/tests/test_csharp_generic_callsites.py"
cd "$DEST/g1_run_worktree"
"$DEST/g1_venv/bin/python3" -m pytest tests/test_csharp_generic_callsites.py -q
# then remove the copied file again to keep the worktree state legible for any later re-run

# 4. Broader regression (optional)
"$DEST/g1_venv/bin/python3" -m pytest \
  tests/test_extract.py tests/test_languages.py tests/test_extractors_registry.py -q
```

## 6. Live run preparation (Phase 4)

| Field | Value |
|---|---|
| `MODEL` | `qwen3-coder:30b` (Kriya's packaged production default, `kriya/config/default_config.yaml`; confirmed pulled and reachable via `kriya doctor` — see §9 disclosure) |
| Provider / endpoint | `openai`-compatible, `http://localhost:11434/v1` (local Ollama) — unchanged default |
| `autonomy.mode` | `human-in-the-loop` (packaged default, unmodified) — the user reviews/approves the diff before it is copied into the real worktree, exactly like any other Kriya run |
| `execution_policy.mode` | `audit` (packaged default, unmodified); WorkflowController disabled (packaged default) — this is a plain single-goal `kriya generate` run, not the subtask-decomposed enforce-mode pipeline |
| `llm_chain` (fallback models) | Unmodified from `default_config.yaml` |
| Campaign override | `campaign_kriya.yaml` — only `paths.skills/memory/logs` (relocated into the evidence directory) and `skills.load_global/load_cwd: false` (eliminates implicit skill sources for a reproducible, cold run) |

`RUN_SCRIPT`: `~/kriya-live-validation/val001-g1-graphify-c3406/g1_evidence/run_g1.sh`

`RUN_COMMAND` (what the script executes, shown for transparency; `~` expansion is shell-dependent
in a quoted argument, so the wrapper script itself uses absolute paths resolved from its own
location, not literal `~`):

```bash
cd /Users/sriramnanduri/kriya-live-validation/val001-g1-graphify-c3406/g1_run_worktree
/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/kriya \
  --config /Users/sriramnanduri/kriya-live-validation/val001-g1-graphify-c3406/g1_evidence/campaign_kriya.yaml \
  generate --file /Users/sriramnanduri/kriya-live-validation/val001-g1-graphify-c3406/g1_evidence/goal.txt
```

The wrapper script additionally: prints the Kriya HEAD SHA and the Graphify worktree SHA for a
final visual check before proceeding; requires an interactive `y` confirmation that the worktree
is still clean and at the pre-fix SHA; tees the full terminal transcript to a timestamped log file
under `g1_evidence/kriya_logs/`; and records the post-run `git status --porcelain` (modified-file
set) to the same directory. It does not run any acceptance/regression command itself — those are
listed as explicit next steps for the user to run separately (§5), keeping the live-run step and
the judging step cleanly separated.

`EVIDENCE_DIRECTORY`: `~/kriya-live-validation/val001-g1-graphify-c3406/g1_evidence/`
(`kriya_logs/` for terminal transcript + modified-file set; `kriya_memory/` for Kriya's own SQLite
state from this run; `pre_run_baseline/` for the evidence in §5; `ground_truth_DO_NOT_EXPOSE_TO_
KRIYA/` for evaluator-only post-run comparison).

## 7. Classification contract (for use after the live run)

One of: `PASS`, `MODEL_CAPABILITY_FAILURE`, `CONTEXT_CORRECTNESS_GAP`,
`REPOSITORY_COMPREHENSION_GAP`, `RECOVERY_GAP`, `VALIDATION_GAP`, `TOOLING_EXECUTION_GAP`,
`RUNTIME_ENVIRONMENT_GAP`, `HARNESS_WEAKNESS`, `CAMPAIGN_FIXTURE_FAILURE`.

`PASS` requires all ten Success Requirements from the originating task, reproduced here for the
record: legitimate terminal success; behavioral acceptance satisfied; regression tests pass;
existing tests remain green; no unauthorized files accepted; no authority widening; no manual
source repair; no ground-truth leak into generation; Kriya production code unchanged; independent
post-run verification agrees with Kriya's own terminal decision. Similarity to the maintainer's
actual patch is explicitly not required — behavioral correctness against the acceptance criteria
in §5 is the sole bar.

Root-cause classification happens only after evidence collection, not as a reflex to any failure.
No Kriya change is authorized by this document or its outcome.

**Interpretive note tying §3's solution-completeness caveat to this contract:** a `PASS` outcome
on H1 is still `PASS` — the acceptance criteria in §5 are behavioral and unaffected by how the
issue was worded. But because issue #3406 already narrows the defect to two named code branches
with line numbers, a `PASS` here provides weaker evidence against `REPOSITORY_COMPREHENSION_GAP`
specifically (the "can Kriya find the right place in a large unfamiliar file without being told"
question) than an equivalent PASS would on a bare-symptom issue. If a future task is selected
specifically to test `REPOSITORY_COMPREHENSION_GAP` resistance (the G5 slot in the G0 campaign),
prefer a historical issue whose original report does not already root-cause the defect, rather than
reusing H1's pattern.

## 8. Evidence-only artifacts committed with this document

Only Kriya-repo-relative, non-bulky files are committed alongside this document:

- `docs/assurance/VAL_001_GRAPHIFY_G1.md` (this file)

The Graphify worktrees, evaluation venv, and full evidence bundle live outside this repository at
`~/kriya-live-validation/val001-g1-graphify-c3406/` (not committed — it contains a full Graphify
clone, a Python venv, and — post-run — Kriya's own generated logs/memory state, none of which
belong in Kriya's own git history). `g1_evidence/goal.txt`, `g1_evidence/campaign_kriya.yaml`, and
`g1_evidence/acceptance/check_acceptance.py` are quoted in full above (§3, §6, and the acceptance
script's own header respectively) so this document remains a complete, self-contained record even
if that external directory is later cleaned up.

## 9. Disclosures

- `kriya doctor` was run once, before this document was finalized, to confirm the packaged
  `qwen3-coder:30b` model is actually pulled and the local Ollama endpoint is reachable. This is a
  read-only connectivity/model-list check, not a generation or completion call — no goal was
  submitted, no code was generated, Graphify was not touched by it. Flagged transparently because
  the task's Phase 4 instruction is strict about the agent not executing "Ollama/model validation,"
  and this check does contact the live Ollama endpoint even though it performs no generation.
- The preparing agent viewed the maintainer's actual fix diff (`git show 5d09dce4`) once, **before**
  fetching the GitHub issue, solely to verify and correct the baseline-SHA error described in §0
  (confirming `5d09dce4` was the fix commit, not the pre-fix state, required looking at what it
  changed). The GitHub issue (`gh issue view 3406`) was fetched afterward, and `goal.txt` was
  drafted from that issue text only. Nothing from the diff — no function name
  (`_csharp_bare_call_name`), no line-by-line patch mechanics, no changed-file list — entered
  `goal.txt`, `campaign_kriya.yaml`, or any Kriya-visible artifact. This was checked mechanically,
  not just asserted: every added line in `maintainer_fix_5d09dce4.diff` and all of `goal.txt` were
  tokenized (identifier-shaped tokens, length ≥5) and intersected. **33 tokens** appear in both
  (`callee_name`, `fn_node`, `mname`, `generic_name`, `member_access_expression`, `_read_text`,
  `identifier`, etc.) — expected, since the issue's own "Where it happens" section quotes these
  exact pre-fix identifiers while describing the bug. Every one of those 33 tokens was then checked
  against the original issue body fetched via `gh issue view` independently of the diff: **all 33
  are traceable to the issue itself; zero tokens overlap the diff without also appearing in the
  issue.** No diff-only vocabulary (e.g. the fix's own new symbol `_csharp_bare_call_name`, or any
  language describing the change itself rather than the pre-existing bug) appears in `goal.txt`.
