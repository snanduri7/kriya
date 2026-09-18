# VAL-001 G1: Kriya's first real historical Graphify brownfield replay

Status: LIVE RUN EXECUTED BY USER, FORENSICALLY CLASSIFIED. §§1–9 below are the immutable
pre-run experiment definition and acceptance contract, written and committed **before** the live
run — left unedited here as originally written. §10 ("Forensic classification") is a new,
clearly-delimited section appended after the run, evidence-only, added per a separate follow-up
task; it does not alter anything above it.

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

---

## 10. Forensic classification (post-run, evidence-only)

Run trace `8b6ee803`, checkpoint `20260918T085216-9ab100c7`, terminal result `Quality Gates:
FAILED` / `failure_category: quality_gates_exhausted`. All evidence below comes from
`~/kriya-live-validation/val001-g1-graphify-c3406/g1_evidence/` (`kriya_logs/traces.db`,
`kriya_logs/terminal_transcript_20260918T032216Z.log`, `g1_run_worktree/.kriya/checkpoints/`) plus
direct inspection of the true pre-fix Graphify tree and, where cited, Kriya's own source
(`kriya/workflow/file_resolution.py`, `kriya/agents/agent.py`). No model was re-run. No Kriya or
Graphify tracked file was modified during this analysis; a candidate reconstruction was tested
only inside a throwaway scratch copy (`g1_evidence/forensics/candidate_test_scratch/`, deleted
after use) built entirely from already-generated transcript content.

### Task 1 — Evidence preservation

| Artifact | Location | SHA-256 (where applicable) |
|---|---|---|
| Run trace (`runs` table, `run_id=8b6ee803`) | `g1_evidence/kriya_logs/traces.db` | `0f9cfd82e1263fee04ebc64caa5d8d307cc50c45ffe946c5a87f3d2154e45057` |
| Checkpoint | `g1_run_worktree/.kriya/checkpoints/20260918T085216-9ab100c7.json` | `260ee319a2814fe5922c539afa10b9950b9ee40ca61d7831d1c99356484e3ad4` |
| Terminal transcript | `g1_evidence/kriya_logs/terminal_transcript_20260918T032216Z.log` (1949 lines) | — |
| Planner output | `traces.db.plan` field (also `checkpoint.plan`) | — |
| Architect output | `traces.db.gate_outcomes`-adjacent `checkpoint.design`/`checkpoint.architect_files` | — |
| Developer attempts (raw streamed candidates) | extracted verbatim from the terminal transcript into `g1_evidence/forensics/attempt1_candidate_raw.txt` (attempt 1, cleaned) and `attempt3_candidate_engine.py` (attempt 3) | — |
| Validation results | `traces.db.gate_outcomes`, `.run_events`, `.evidence_records` | — |
| Recovery instructions | `run_events` (`api_contract_recovery.phase_advanced` events), terminal transcript lines 1541–1893 | — |
| Original Graphify file | `g1_run_worktree/graphify/extractors/engine.py` at `67f99bd0` | `1158691a0a856c90aac2c717f31246a286f4ac757ae717793889ba1684fd9d78` |
| Final untouched workspace file | same path, same hash, post-run | **identical** — confirms no mutation |

`WORKSPACE_MUTATION_AFTER_FAILURE = NO`. Verified three independent ways: (1) `git diff --stat
HEAD` in `g1_run_worktree` is completely empty (zero tracked files touched anywhere in the tree,
not just `engine.py`); (2) `git status --porcelain` in `.kriya/worktree` (Kriya's own internal
scratch worktree, where Developer edits are actually staged during the retry loop) is also
completely clean at `67f99bd0`; (3) `engine.py`'s SHA-256 is identical before and after. The only
untracked additions anywhere are `.kriya/` (checkpoint + scratch worktree + lockfile) and `logs/`
(Kriya's own log file) — neither is a Graphify source or test file.

Ground-truth material (maintainer diff, ground-truth test) was **not** consulted for Tasks 1–6
below; it is addressed only in §Task 7, after the rest of the analysis was complete, per the
task's own ordering requirement.

### Task 2 — Attempt reconstruction

Four `attempt` indices appear in the trace; only three are real Developer/LLM calls (`model_hops`
= `["qwen3-coder:30b", "qwen3-coder:30b", "qwen3-coder:30b"]`, `generation_metrics.llm.
developer_calls = 3`). Attempt 2 is a **deterministic, non-LLM restoration step**.

| ATTEMPT | MODE | INPUT_CONTEXT | TARGET_FILES | MEMBER_HINTS | SOURCE_REVISION | OUTPUT_EDIT | SIGS BEFORE | SIGS AFTER | VALIDATOR_FAILURE | RECOVERY_DIRECTIVE | NEXT_ATTEMPT_CHANGE |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `create_full_file` (`full_set`) | Known-target context package, **skeleton tier only**, body elided (`reason: body_elided`, `estimated_tokens: 24565`); `member_hint_paths: []` (empty) | `graphify/extractors/engine.py` | none | `67f99bd0` | Full-file replacement text returned by the model; **51 `def` statements** vs. baseline's 116 (see Task 4); output ends mid-file with the literal comment `# The rest of the file remains unchanged...` | present (4/4, in baseline) | **absent (0/4)** | `brownfield_public_api_changed` — `ownership_gate` (`find_brownfield_public_api_changes`) rejected before write | "Restore the existing owner contract before behavioral repair" | Enter `API_CONTRACT_RECOVERY` |
| 2 | `repair_with_full_file` / `RESTORE_PUBLIC_CONTRACT` | N/A — **no Developer/LLM call this step** ("deterministic restore, no Developer call" per the trace log) | `graphify/extractors/engine.py` | n/a | attempt 1's candidate | Kriya's own code mechanically re-inserted the 4 missing signatures | absent (0/4) | **present (4/4)** | none — "RESTORE_PUBLIC_CONTRACT pre-check passed" | transition to `REPAIR_BEHAVIOR` | Developer call for behavior repair |
| 3 | `repair_with_full_file` / `REPAIR_BEHAVIOR` | Same known-target skeleton-tier package as attempt 1 (no evidence of a different/fuller tier for the repair step) | `graphify/extractors/engine.py` | none | attempt 2's (restored) candidate, nominally | Model returned a **10-`def`, 287-line** file (down from attempt 1's 51/1394) — collapsed further, not incrementally edited | present (4/4, post-restore) | **absent (0/4) again** | `api_contract_recovery_incomplete` — same 4 signatures, byte-identical `evidence_files` lists to attempt 1 | "restore every authoritative baseline signature before quality gates" (verbatim repeat of attempt-1 signature list) | Retry `REPAIR_BEHAVIOR` again |
| 4 | `repair_with_full_file` / `REPAIR_BEHAVIOR` | Same as attempt 3 | `graphify/extractors/engine.py` | none | attempt 3's candidate, nominally | **139 output tokens in 4.03s** (vs. attempt 3's 2,940 tokens / 128.6s and attempt 1's 12,935 tokens / 564.6s) — by far the shortest, most truncated response of the run | present (4/4, post-restore) | **absent (0/4), identical list** | `api_contract_recovery_incomplete`, identical to attempt 3 | same directive repeated | none — `retry_strategy`: "Quality Gates exceeded maximum debug retries"; terminal `FAILED` |

`FIRST_DESTRUCTIVE_ATTEMPT = 1`. All four signatures disappeared simultaneously at attempt 1's
first (and only) full-file write — not a gradual erosion across attempts. They were correctly,
mechanically restored by attempt 2 (no model involved), then **destroyed again** by attempt 3's
Developer call, and never recovered afterward.

Determined from persisted `gate_outcomes`/`run_events`/reconstructed candidate content, not from
reviewer prose. The Reviewer's own final narrative (transcript lines 1917–1928) was read only
after this reconstruction, as a cross-check — it correctly names the same 4 signatures but,
consistent with only ever seeing the `ownership_gate`'s narrow report, does not mention the
far larger actual scope of content loss documented in Task 4 below.

### Task 3 — Context correctness (initial attempt)

- Selected file/member: `graphify/extractors/engine.py`, whole file (no member-level scoping —
  `member_hint_paths: []`).
- Context tier supplied: **skeleton** (`"tier": "skeleton"` in `run_events`'s
  `context.known_target_package` entry) — not whole-file, not signatures-only, not member_exact.
  The skeleton's own body was explicitly elided (`"reason": "body_elided"`), estimated at **24,565
  tokens** — i.e., Kriya's own instrumentation recorded, before generation even began, that it was
  withholding an amount of source content larger than the entire configured `max_tokens: 16384`
  Developer completion ceiling.
- Relevant member present: **NO** for the four cited signatures, and for the overwhelming majority
  of the file's real content — a skeleton tier, by construction, elides function bodies, and all
  four flagged signatures are function-*nested* closures (confirmed in Task 4) that live inside
  those elided bodies. The model was never shown they existed, let alone their implementation.
- Omissions explicitly recorded: **YES** — `run_events` logs the omission with a path, rank,
  reason, and token estimate. This is itself evidence the omission was a deliberate, tracked
  budget decision, not silent data loss.
- Stale source present: no evidence found (single source revision throughout; `SOURCE_REVISION`
  column above is `67f99bd0` for every attempt, no drift detected).
- Duplicate stale/current representations: not observed.
- Context budget causing relevant source loss: **YES, directly evidenced.** The "Engineering
  Triage" stage (new to this run relative to what `CLAUDE.md`'s Architecture section documents —
  see the Unexplained Findings note below) classified the task, before repository analysis had
  identified the actual 6,318-line target file, as `initial_risk_class: LOW`, `execution_weight:
  light`, `context_depth: narrow` (`reason_codes: ["no_signals_fired_defaulted_to_task"]`,
  `router_used: false`). This triage classification is what produced the skeleton-tier context
  package for a file whose real body (24,565 estimated tokens) cannot fit inside the "narrow"
  budget it authorized, or even inside the full `max_tokens` completion ceiling on the way back out.

`INITIAL_CONTEXT_SUFFICIENT = NO`

This is a demonstrated instance of the specific problem shape CTX-001 targets (member-level
context, skeletonization degrading large files) manifesting on a real, 6,318-line production file.
CTX-001's own closure evidence never included a case this large relative to the configured
completion budget. Per the task's own instruction, this is reported as a demonstrated
context-insufficiency finding **for this run**, not a reopening of CTX-001 — CTX-001's closure
evidence and this finding can both be true: CTX-001 closed specific, tested guarantees about
member-hint derivation and skeletonization degradation *order*; nothing examined here contradicts
those tests. What this run adds is a **new, previously untested combination**: a target file whose
un-elided body alone exceeds the Developer's own completion token ceiling, paired with the
CREATE_FULL_FILE edit protocol (Task 4) that requires reproducing that entire body in one shot
regardless.

### Task 4 — Edit protocol

- **Production edit protocol selected**: `create_full_file` (attempt 1) / `repair_with_full_file`
  (attempts 2–4) — i.e., **whole-file reconstruction**, not member-local/anchored editing, for
  every single attempt including both recovery rounds. Confirmed in `generation_metrics.retry`:
  `"full_set_attempts": 1, "targeted_attempts": 0"` — zero anchored/targeted attempts occurred at
  any point in this run.
- Tool calls/operations: a single `create_full_file`-mode LLM completion per attempt (3 completions
  total), plus one non-LLM deterministic signature-restoration step (attempt 2).
- Change shape: **whole-file reconstruction attempted, producing a drastically truncated partial
  reconstruction** — not a member-local edit, and not a complete, faithful whole-file replacement
  either. A third category the task's A–D options don't quite name in isolation: the model
  attempted whole-file reconstruction and substantially failed at it.
- Size of original file: **6,318 lines / 331,064 bytes** (116 `def` statements: 11 non-underscore,
  105 underscore-prefixed). Note: G0's qualification doc (§4 of that document) reports this same
  file at 6,955 LOC — that figure was measured at `26b02b5` (the `v8` branch tip); this run's
  baseline is `67f99bd0`, several commits earlier on the same file's history. The two numbers are
  both correct for their respective revisions; the difference is real revision drift, not a
  measurement error.
- Size of candidate:
  - Attempt 1: **1,394 lines**, 51 `def` statements (44% of baseline's line count, 44% of its
    function count) — ends with the literal placeholder comment `# The rest of the file remains
    unchanged...`.
  - Attempt 3: **287 lines**, 10 `def` statements (4.5% of baseline) — all 10 remaining names are
    underscore-prefixed C#/Java type-reference helpers; every other category of logic (including
    `_extract_generic`, the shared 3,394-line dispatch core, and all 29 non-C#/Java language
    extractors' call sites into `engine.py`) is absent.
  - Attempt 4: 139 output tokens in 4.03 seconds — by far the shortest response of the run;
    `gate_outcomes` shows the identical 4-signature failure as attempt 3, consistent with a
    candidate that changed little or nothing from attempt 3's.
- Lines/regions preserved: the C#/Java generic-type-reference helpers the model judged relevant to
  the stated goal (`_csharp_collect_type_refs`, `_csharp_classify_base`,
  `_csharp_type_parameters_in_scope`, etc.) — a coherent, goal-relevant subset, not random.
- Lines/regions lost: everything else — by attempt 3, that means **106 of 116** original functions,
  spanning all 29 non-C#/Java-specific extractors that also live in this shared file.
- **Destructive intermediate representation accepted before contract validation**: YES. Kriya's
  `ownership_gate` (`find_brownfield_public_api_changes`, `kriya/workflow/file_resolution.py:619`)
  ran and rejected the candidate *before* it reached the real workspace — correctly preventing
  the worst outcome (§Task 6) — but the candidate itself, at 44%–4.5% of the original file's
  content, was accepted as far as "committed generated/edited candidate to sandbox" (transcript
  line 1544, 1859, 1882) without any completeness/size sanity check prior to that gate running.

**Independent verification, not inference**: the attempt-1 candidate was reconstructed verbatim
from the terminal transcript into an isolated scratch copy of the workspace (never touching the
tracked worktree) and actually executed through `graphify extract`. Result: a real Python
`ImportError: cannot import name '_cpp_declarator_name' from 'graphify.extractors.engine'`,
raised the moment `graphify/extract.py`'s own module-level `from graphify.extractors.engine import
..., _extract_generic, ...` statement runs — the candidate does not merely fail to fix the C# bug,
it makes the **entire package fail to import**, before any extraction logic runs at all. This
confirms the true scope of damage is categorically worse than the 4 named signatures the
`ownership_gate` reported.

**Determination — which of A–E caused the missing signatures**: **C — incomplete source/context
supplied to model.** Compounding, secondary factors: A (the model's own reconstruction was
additionally incomplete relative to even what it could infer from the insufficient context it did
receive) and B (edit-application accepted an uncompleteness-unchecked candidate before contract
validation). C is primary because it is the upstream, non-model-dependent cause — see the token-
budget arithmetic below. Evidence against a pure "A"
(model-generated destructive edit, full stop) reading: the model's own "FIX ANALYSIS" text on both
completions independently and correctly diagnosed the real root cause (tree-sitter's
`generic_name` vs. `identifier` node-type distinction, matching the original issue's own diagnosis
almost exactly) — its *reasoning* was sound. What it could not do was faithfully reproduce ~6,300
lines of implementation it was shown only a skeleton of, inside a completion budget
(`max_tokens: 16384`) smaller than just the elided body's own estimated size (24,565 tokens). D
(candidate parsing/rendering defect) was checked and ruled out: the candidate is syntactically
valid Python (`ast.parse` succeeds); its incompleteness is real content loss, not a
transcription/rendering artifact.

### Task 5 — API contract recovery

Two real recovery rounds (attempts 3 and 4), preceded by one non-LLM deterministic restoration
(attempt 2).

- Authoritative baseline source available to Kriya: yes — `67f99bd0`'s own on-disk
  `engine.py` was read fresh for `find_brownfield_public_api_changes`'s "original" side (it walks
  `original_contents`, populated from the real pre-attempt worktree state); the deterministic
  restore in attempt 2 used this same source to splice the 4 signatures back in mechanically.
- MUST_FIX signatures (as Kriya itself framed them): exactly the same 4 across every recovery
  round — `bind(name: str | None, type_name: str | None, scope_node)`, `visit(n)`, `walk(n)`,
  `walk(node, parent_class_nid: str | None = None)`.
- MUST_PRESERVE evidence (`protected_evidence_files`): 27–30 files per round, computed by
  `find_brownfield_public_api_changes`'s `evidence_files` search — **verified by direct source
  reading (`kriya/workflow/file_resolution.py:700–704`) to be an unscoped, whole-workspace regex
  text-substring search** (`re.search(rf"(?<![\w$]){re.escape(api_name)}\s*\(", evidence_content)`
  against every file's raw text) with **no import/call-graph/reachability check at all**.
- Recovery scope: narrowly the 4 named signatures — **demonstrably far narrower than the actual
  damage** (106/116 functions missing by attempt 3, not 4/116).
- Source/context actually supplied to Developer for the repair calls: the same skeleton-tier
  package as attempt 1 — no evidence of the repair calls receiving fuller (full-body or
  member-exact) context than the original, already-insufficient attempt.
- Exact baseline bodies/signatures supplied: **NO** for the surrounding ~6,300 lines of context the
  model would have needed to safely reconstruct the whole file without loss; **YES, but only as a
  string in the failure message**, for the 4 named signatures themselves (the recovery directive
  quotes their exact text, but does not supply their bodies or the bodies of the ~100 other
  functions never mentioned at all).
- Recovery instructed restoration before secondary work: YES, explicitly (`RESTORE_PUBLIC_CONTRACT
  -> REPAIR_BEHAVIOR`, in that order) — and the *deterministic* half of that ordering worked
  correctly (attempt 2 genuinely restored all 4 signatures with zero model involvement, and its own
  pre-check passed).
- Model response: both attempt-3 and attempt-4 Developer calls returned `create_full_file`-shaped
  whole-file output again, discarding attempt 2's mechanically-restored content in the process —
  attempt 3's own candidate (287 lines, 10 defs) is *smaller* than attempt 1's (1,394 lines, 51
  defs), i.e. the "repair" calls did not build incrementally on the restored file; they
  re-attempted full reconstruction from the same insufficient context and did categorically worse
  each time.
- Why each recovery attempt remained incomplete: the `REPAIR_BEHAVIOR` phase re-invokes the same
  `create_full_file`-shaped generation path attempt 1 used, subject to the same skeleton-only
  context and the same completion budget — there is no mechanism observed that constrains the
  repair call to an anchored edit on top of the already-restored candidate, or that re-supplies the
  now-restored content as a base to preserve. The recovery mechanism fixed the *narrow* symptom it
  could see (4 signatures) exactly once (attempt 2, deterministically), then handed the file back
  to a generation mode structurally certain to re-lose it.

**Determination**: `RECOVERY_SCOPE_INCORRECT`, with a demonstrated compounding
`RECOVERY_IMPLEMENTATION_DEFECT`. Scope is incorrect because the 4-signature target was never
capable of producing a working file even if perfectly satisfied (106 functions were missing, not
4 — see Task 4's `ImportError` reproduction). Implementation is defective because the
`REPAIR_BEHAVIOR` phase's own generation mode discards the very restoration
`RESTORE_PUBLIC_CONTRACT` just performed, rather than building on it. This is **not**
`MODEL_FAILED_TO_FOLLOW_SUFFICIENT_RECOVERY_CONTEXT` — the context was not sufficient in the first
place (same skeleton-tier package throughout), so "sufficient recovery context" was never actually
supplied for the model to fail to follow.

### Task 6 — Terminal correctness

- `FALSE_SUCCESS = NO` — terminal status is `failure`/`Quality Gates: FAILED`, matching every
  independently-verified fact below; nothing about this run claims or implies success anywhere in
  the persisted record.
- `ATOMIC_REJECTION = PASS` — the candidate was rejected by `ownership_gate` before ever being
  copied into the real workspace (`applied: false` in every `attempt.failed` event); `git status`/
  `git diff` on both the real worktree and Kriya's own internal scratch worktree are completely
  clean at `67f99bd0`; SHA-256 of `engine.py` is unchanged.
- `AUTHORITY_WIDENING = NO` — `autonomy.mode`, `execution_policy.mode`, and every other
  campaign-config field were left at packaged defaults (§6 above); nothing in the trace shows a
  policy, approval, or scope change during the run; the run terminated via the ordinary
  `quality_gates_exhausted` path, not an approval bypass.
- `MANUAL_REPAIR = NO` — this analysis made zero edits to any Kriya or Graphify tracked file; the
  one file touched during verification (`candidate_test_scratch/graphify/extractors/engine.py`)
  lives entirely inside a throwaway scratch copy outside both git repositories and was deleted
  after use.
- Checkpoint does not constitute accepted workspace state: confirmed — the checkpoint's own
  `stage: "design"` field and its `plan`/`design`/`architect_files` contents predate the
  Developer/Quality-Gates loop entirely; it exists solely to let a future `--resume-id` run skip
  Plan/Design, not as a record of an accepted candidate.
- Terminal `FAILED` agrees with deterministic gates: yes — `ownership_gate` (deterministic,
  regex/text-based, not a model judgment) is what rejected every attempt; the Reviewer agent ran
  only afterward, to narrate the already-terminal failure, and did not itself gate anything.

### Task 7 — Acceptance / ground truth (consulted only after Tasks 1–6)

| State | Behavioral result (via `check_acceptance.py`, the same script calibrated in §5) |
|---|---|
| Untouched baseline (`67f99bd0`, real workspace) | 2/5 — unchanged from the pre-run baseline recorded in §5; confirms the real workspace genuinely was never touched |
| Rejected candidate (attempt 1, reconstructed in an isolated scratch copy only) | **Does not reach the 5-call-site check at all** — `graphify extract` itself crashes with `ImportError: cannot import name '_cpp_declarator_name'` before any C# file is parsed. Strictly worse than the pre-fix baseline, not a partial fix. |
| Maintainer-fixed (`5d09dce4`, evaluator-side calibration only, §5) | 5/5, as previously recorded — for reference only, never exposed to Kriya |

Kriya's intended approach, as evidenced by both Developer "FIX ANALYSIS" texts, was **conceptually
aligned** with the maintainer's real fix: both independently identify that tree-sitter parses
`Get<int>(...)` as a `generic_name` node (not `identifier`), that the existing code only handles
the `identifier` case, and that the fallback path captures the raw text including the type-argument
list instead of the bare method name — the same mechanism the original issue itself describes and
the same mechanism the real fix (`_csharp_bare_call_name`, `5d09dce4`) addresses. Kriya never
reached the point of implementing this diagnosis as a working patch; the conceptual alignment is
visible only in the "FIX ANALYSIS" prose, never realized in either candidate's actual code (neither
candidate contains any working, syntactically-integrated generic-name-stripping logic reachable
from the real call sites — attempt 3's candidate is missing the call-handling code entirely, since
`_extract_generic` itself is absent). Similarity to the maintainer's implementation was not used as
a correctness criterion anywhere in this determination, per the task's own instruction.

### Classification

`G1_PRIMARY_CLASSIFICATION = CONTEXT_CORRECTNESS_GAP`

Root cause, in causal order: Kriya's engineering-triage stage classified this goal as `LOW risk` /
`light` execution weight / `narrow context_depth` before repository analysis had identified the
actual target file's real size; this produced a skeleton-only (body-elided) context package for
`graphify/extractors/engine.py`, whose elided body alone was estimated at 24,565 tokens — larger
than the Developer's own `max_tokens: 16384` completion ceiling; Kriya's classic pipeline
unconditionally uses `CREATE_FULL_FILE` (whole-file reconstruction) on a clean first attempt
regardless of target-file size, and the `API_CONTRACT_RECOVERY` repair phase re-uses the same
generation mode on every subsequent attempt; the combination made faithful, complete reproduction
of this specific file structurally unachievable from the context actually supplied, independent of
which model executed it. The model's own reasoning (both "FIX ANALYSIS" texts) was conceptually
correct; its failure was reproduction of unseen content under an incompatible edit protocol, not
diagnosis.

**SECONDARY_FINDINGS** (each independently demonstrated, none used to adjust the primary
classification above):

1. **HARNESS_WEAKNESS in `find_brownfield_public_api_changes`** (`kriya/workflow/
   file_resolution.py:563,619`): `_normalized_public_signatures()` extracts *any* `def` statement
   matching `^[ \t]*(?:async\s+)?def\s+NAME(...)`, filtering only leading-underscore names — it
   does not distinguish module-level (genuinely importable) functions from function-nested
   closures. All 4 flagged signatures in this run are confirmed, by direct source inspection, to be
   closures nested inside other functions (`_csharp_method_receiver_types`, `_ruby_local_class_
   bindings`, `_extract_generic`, and four `_python_*`/`_js_*` name-collection helpers respectively)
   — none returned or otherwise exposed outside their enclosing function, hence structurally
   uncallable from any other file. Combined with `evidence_files`'s unscoped, repo-wide text-
   substring search (no reachability check), the detector labeled genuinely private implementation
   details as a "public API"/"owner contract" and cited unrelated files' own independent,
   same-named local helpers as "evidence" of a dependency that does not exist. Of the baseline
   file's 116 functions, 105 (91%) are underscore-prefixed and thus entirely invisible to this
   detector regardless of whether they survive a candidate edit — meaning the detector's own
   4-signature report drastically under-stated the true scope of damage (106/116 functions
   actually missing by attempt 3). The detector's **rejection outcome** was still correct (the
   candidate was catastrophically broken for reasons the detector never actually measured), but its
   stated reasoning and the recovery scope it drove were not.
2. **RECOVERY_GAP-adjacent**: the `REPAIR_BEHAVIOR` phase of `API_CONTRACT_RECOVERY` discards the
   immediately-preceding deterministic restoration rather than building on it (Task 5).
3. Configured `num_ctx: 32768` appears not to have hard-bounded attempt 1's actual usage
   (23,626 input + 12,935 output = 36,561 tokens reported) — recorded as an open, unexplained
   discrepancy (see below), not diagnosed further; it did not change this run's terminal outcome
   either way.
4. Kriya's own log-file resolution behavior for this run did not match this session's own prior
   understanding of `logging.file`'s documented resolution rule (see Unexplained Findings) — noted
   for the record, not investigated to a root cause here, and not a Graphify- or model-facing
   issue.

### RECV-002

`RECV002 = PARTIAL_EVIDENCE`

Not `CLOSED`: the production recovery contract (`API_CONTRACT_RECOVERY`) was genuinely entered and
executed end-to-end in a real run, but did not produce a viable outcome, and this run directly
demonstrates two independent reasons it could not have: an incorrectly narrow recovery scope (Task
5) and a repair-phase implementation that discards its own prior restoration (Task 5/Secondary
Finding 2). Not `NEEDS_EVIDENCE`: this is no longer an absence of evidence — this run provides
direct, positive, reproducible evidence characterizing exactly how and why the mechanism falls
short in a real case, which is what distinguishes `PARTIAL_EVIDENCE` from the prior status. Closing
RECV-002 would require a run (or a targeted, separately-scoped test) demonstrating the recovery
contract correctly restoring a **complete, accurate** candidate end-to-end, including a scope
determination broad enough to cover actual damage — not demonstrated here.

### Unexplained findings (documented, not chased to a root cause)

- **"Engineering Triage" / `ownership_gate` / `API_CONTRACT_RECOVERY` machinery is not described in
  `CLAUDE.md`'s Architecture section.** The mechanism is real and load-bearing (it produced the
  entire terminal outcome of this run) but this project's own onboarding doc doesn't mention it —
  most likely explained by the untracked `Kriya_MA8_Task_Correctness_Control_Plane_Implementation_
  Instructions.md` / `Kriya_MA9_Obligation_Driven_Coordinated_Repair_Implementation_Instructions_
  v1.0.md` files already sitting untracked in this repo's root (per this session's own git-status
  snapshot) — plausibly a recently-landed feature whose CLAUDE.md update hasn't happened yet. Not
  investigated further; flagged so a future session doesn't re-derive this mechanism from scratch
  when `CLAUDE.md` doesn't mention it.
- **`logging.file` resolved relative to the invoking process's CWD** (`g1_run_worktree/logs/
  kriya.log`) rather than to `campaign_kriya.yaml`'s own config directory or "the install dir for
  the packaged default" as this session's own prior understanding of the documented SEC-009
  resolution rule would predict. `paths.logs` (explicitly overridden in `campaign_kriya.yaml`) DID
  resolve correctly to the evidence directory (`traces.db` landed exactly where expected). Only the
  *un*-overridden `logging.file` default behaved unexpectedly. Not investigated to a root cause —
  flagged as a discrepancy between documented and observed behavior for whoever next touches
  config-path resolution, not as a reopened SEC-009 finding (this run's evidence is insufficient to
  characterize it as a security issue one way or the other; it may simply reflect something about
  how this specific campaign config was constructed).
- **`num_ctx` vs. observed total token usage** (Secondary Finding 3 above) — not chased further.

None of the above blocks the primary classification, which rests on directly-observed,
independently-reproduced evidence (the context-tier/token-budget mismatch, the reconstructed
`ImportError`, the source-verified detector behavior) rather than on either unexplained item.

---

## 11. G1 RERUN (post-remediation) — prepared, not executed (2026-09-18)

Following D1/D2/D3-part-1 remediation (`docs/assurance/VAL_001_GRAPHIFY_G1_REMEDIATION_DESIGN.md`,
commits `7bc52b5` G1-R1, `1fc3210` G1-R2) and the user's own independent full-suite confirmation
(`.venv/bin/pytest`: 4279 passed, 0 failed), a fresh rerun of this exact experiment was prepared.

**Kriya checkpoint frozen and pushed**: `a8e81a1e5d5f15dd77cbdb16440d826142869e20`
(`origin/milestone-decomposition`). Contains G0 + this document + the forensic classification +
the remediation design + G1-R1 + G1-R2 + the full-suite-confirmation doc commit — nothing else.
Verified: `git diff 10b5523..a8e81a1 -- kriya/config/default_config.yaml` is empty (model config,
including `num_ctx`, is byte-identical to the original failed run).

**New, dedicated Graphify worktree** (never touched by the original run or any evaluator
reproduction): `~/kriya-live-validation/val001-g1-graphify-c3406/g1_rerun_worktree`, detached at
`67f99bd0059dd1bac9e44382907ef9f10098b39f`. `git status --porcelain` empty. `graphify/extractors/
engine.py` SHA-256 confirmed identical to the original G1 forensic record
(`1158691a0a856c90aac2c717f31246a286f4ac757ae717793889ba1684fd9d78`). Baseline defect
independently re-reproduced via the same calibrated `check_acceptance.py`: **2/5** — unchanged from
the original run, proving the defect this replay targets is still genuinely present and the
worktree itself is unmodified.

**Goal and config**: `~/kriya-live-validation/val001-g1-graphify-c3406/g1_rerun_evidence/goal.txt`
is byte-identical (SHA-256 `f96bf5a3400abdb3eddf4a6d39d14f7022cde66cf1621525f3edac759bfcb823`) to
the original run's `g1_evidence/goal.txt`. `campaign_kriya.yaml` is byte-identical content —
relative `paths.*` resolve against its own directory, so it relocates its own output to the new
evidence directory automatically, with zero textual change. No member hints, no context-window
change, no skill additions, no exposure of this document, the forensic classification, or the
remediation design to the model.

**Prepared, not executed**: `run_g1_rerun.sh` (preflight-checks the frozen Kriya commit and the
fresh worktree's SHA/cleanliness before prompting to proceed; no `--resume`/`--resume-id` anywhere)
and `run_g1_rerun_acceptance.sh` (sequences all 10 required acceptance checks, ground truth used
only starting at step 6, after generation has already terminated). Neither script has been run by
this agent. Classification of the outcome, once the user runs both, is not automatic — the same
ten-way classification contract from the original G1 applies unchanged.

## 12. Post-remediation rerun (run `d756a833`) — forensic classification

The user executed `run_g1_rerun.sh`. Result: `quality_gates_exhausted`, 8 real Developer attempts
(4 full-set + 4 targeted/fallback-targeted), model routing alternated `qwen3-coder:30b` /
`qwen3.6:35b-a3b-q4_K_M` via the pre-existing, unmodified production escalation ladder
(`resolve_fallback_model`) — confirmed NOT contamination (same config/chain as original G1; the
divergence is a legitimate, fully-traced consequence of D3-part-1 correctly eliminating the
false-positive brownfield-API detection that drove original G1 into `api_contract_recovery` mode
instead). `PRIMARY_CLASSIFICATION = CONTEXT_CORRECTNESS_GAP` (persisting through D1/D2/D3-part-1):
D1 correctly rejected every illegitimate full-file mutation (2/8 attempts) and the anchored-edit
gate correctly rejected every non-matching patch (4/8 attempts, all against a context tier that
never became exact); zero unauthorized changes, zero false success, fully atomic rejection across
all 8 attempts. `D1 = PASS`, `D2 = NOT_EXERCISED` (correctly — its own precondition never recurred),
`D3_PART1 = PASS`. Full forensic detail (per-attempt table, model routing trace, anchored-edit
root-cause classification, localization-quality assessment, model-understanding-vs-execution
comparison against maintainer ground truth) lives in this session's own transcript, not duplicated
here — see the CTX-001-P1-C3 investigation below for the root cause this forensic pass fed into.

## 13. CTX-001-P1-C3: failure-grounded member escalation (2026-09-18)

**Root cause of the rerun's persistent CONTEXT_CORRECTNESS_GAP**: the CTX-001 member-hint pipeline
(`kriya/workflow/context_source.py`) had exactly two candidate producers — SOURCE 1
(`resolve_member_hints_from_chunk_header`, needs a Graph-RAG chunk hit) and SOURCE 2
(`resolve_member_hints_from_failure_location`, needs a real `Failure.file_locations[i].line`).
Both were structurally silent for this entire run: no RAG retrieval ran at all (the Architect
already named `engine.py` directly), and neither `anchored_edit` nor `operation_contract` failures
ever populate a `FileLocation.line` (they are response-SHAPE failures, not "found at file:line"
failures) — `member_hint_paths` stayed `[]` across all 8 attempts despite the model's own rejected
SEARCH blocks already carrying real, current-file vocabulary (`fn_node`, `generic_name`,
`member_access_expression`, ...) that nothing consumed. Triage/risk-class was independently
confirmed to play no role in either the initial skeleton tier (file-size-vs-budget only) or any
escalation gating — a correction to the earlier G0/G1 forensic framing.

**Fix**: a THIRD, additive evidence source — `resolve_member_hints_from_search_evidence()` — reads
a rejected anchored-edit's own SEARCH text (`Failure.attempted_edits`, already captured, never
previously consumed) and grounds it against real, current structure via two deterministic rules
(never a fuzzy/highest-score pick, per explicit design correction before implementation): a
sole-evidence exact member-name match, or joint containment of 2+ distinctive tokens inside exactly
one structural boundary (nested Python/Java members collapse to the smallest enclosing one; real
sibling ambiguity returns no hint). Wired into `_resolve_retry_member_hints`
(`kriya/workflow/attempt.py`), gated on: at least one prior anchored-edit failure for that file
(`anchor_failure_counts >= 1`), the file's known-target context still has a real omission, and the
triggering failure carries non-empty SEARCH text — never overrides a SOURCE-2 (line-based) hint,
never widens the authorized target set, never triggers on a full-file rejection with no SEARCH
evidence at all. D1 is completely unmodified; this only ever increases the chance a legitimate
patch anchor exists.

**A real precision gap was found and fixed during self-testing, not merely proposed**: an earlier
version of the name-match rule grounded on ANY distinctive token matching a real member's name,
even when several OTHER distinctive tokens were present — a synthetic Java case (a method's own
SEARCH text calling a real, differently-named sibling method) proved this could ground to the
CALLED method instead of the one actually being edited. Fixed by requiring the name-match rule to
apply ONLY when it is the single, uncorroborated piece of evidence (see `resolve_member_hints_
from_search_evidence`'s own docstring for the full before/after account).

Empirically re-validated against this run's own real evidence (not merely a synthetic
approximation): attempt 3's real, rejected SEARCH text correctly and uniquely grounds to the real
structural member containing #3406's actual bug region (`_extract_generic.walk_calls`); attempts
4/7's SEARCH text, which invented local-variable names that don't exist anywhere in the real file,
correctly fails closed rather than fabricating a nearby guess — an honest, disclosed limitation
(this mechanism recovers real vocabulary, it does not repair a fully-hallucinated response).

**Budget-fit measured, not assumed**: `_extract_generic.walk_calls` (the real grounded member) is
~12,151 estimated tokens against run d756a833's own real, production `known_target_limit`
(~24,576 tokens, per the run's own log — `Escalating compilation attempt to fallback model:
qwen3.6:35b-a3b-q4_K_M (Limit: 24576 tokens)`) — comfortably inside budget with room left over for a
`signatures`-tier rendering of the rest of the file, zero omission. This is the single load-bearing
number for whether this package actually helps G1's real case, and it was measured directly against
the real, external `engine.py`, not assumed from a synthetic fixture. `walk_calls` spans 770 real
source lines (Python's AST-based member boundary - a `def`, the finest grain `python_member_ranges`
can express for a nested function) - a genuine, large reduction from the 6,318-line whole file, but
not isolation down to just the ~60-line C# branch actually containing the bug; stated plainly as an
inherent characteristic, not oversold as precise line-level isolation.

**Tests**: 11 new tests in `tests/test_context_source.py` (candidate-discovery/grounding rules in
isolation, including a G1-shaped synthetic fixture, a non-Python/Java case, and the Java
call-vs-declaration precision fix as its own regression test) + 12 new tests in
`tests/test_val001_g1_remediation.py` (escalation-trigger gating, context promotion to real
`tier=member_exact` at both a generous and a realistically-tight budget, the inverse
too-large-to-fit-omits-rather-than-exceeds case, D1-unchanged proofs including stale-revision
rejection). All 23 self-verified via direct execution, plus the full pre-existing
`test_context_source.py` (52/52), `test_val001_g1_remediation.py` (49/49), and `test_context_budget.py`
(30/30) suites, plus **11 specifically targeted, highest-risk `test_workflow.py` member/anchor
tests — NOT the full file** (per this campaign's own G1-R2 lesson: a clean targeted sweep at this
layer proves nothing about the rest of it; the user's own full `.venv/bin/pytest` run is what
actually confirms the remainder). Zero regressions found across everything actually executed. Full
`.venv/bin/pytest` confirmation is the user's own, per repository convention (this agent does not
run it).

No architecture change — an additive third evidence source into CTX-001 P1 C2's own,
already-designed extension point. `D3-part-2` (persisted graph evidence) remains correctly
deferred — proven unnecessary for this gap specifically (token-overlap against already-available
`member_boundaries_for()` output, needing no index, no `kriya analyze` prerequisite, no
persistence).

**Full-suite confirmation (user-run, 2026-09-18)**: `.venv/bin/pytest` — **4302 passed, 5
deselected, 140 warnings in 917.84s (0:15:17)**, 0 failed. Confirms commits `3e5c9c2` (CTX-001-P1-C3
implementation) and `9126def` (budget-fit test-evidence tightening per advisor review) on top of
`015430d` introduce zero regressions across the full suite, not just the targeted/self-verified
subset recorded above.

## 14. Rerun #2 preparation (post CTX-001-P1-C3, not executed)

Kriya checkpoint frozen and pushed: `7632e607d4b9c8c1cd48a828bf9bbf7b1802ea5c` (`015430d` + C3
implementation `3e5c9c2` + test-evidence tightening `9126def` + this full-suite-confirmation doc
commit `7632e60`, all on `origin/milestone-decomposition`). Diff `015430d..7632e60` touches exactly
the 5 expected files (`kriya/workflow/attempt.py`, `kriya/workflow/context_source.py`, two test
files, this doc) — zero unexplained production/config changes.

Fresh, dedicated Graphify worktree: `~/kriya-live-validation/val001-g1-graphify-c3406/
g1_rerun2_worktree` — never touched by run `d756a833` or any earlier reproduction/calibration check
(that stale state lives only in the now-abandoned `g1_rerun_worktree`). Verified: SHA
`67f99bd0059dd1bac9e44382907ef9f10098b39f`, clean, no `.kriya/` present, `engine.py` SHA-256
`1158691a0a856c90aac2c717f31246a286f4ac757ae717793889ba1684fd9d78` (unchanged), baseline
behavioral acceptance re-reproduced at 2/5, worktree unmutated by the check.

A/B control re-verified against run `d756a833`'s own setup: goal SHA-256
`f96bf5a3400abdb3eddf4a6d39d14f7022cde66cf1621525f3edac759bfcb823` (byte-identical),
`campaign_kriya.yaml` byte-identical, `git diff 10b5523..HEAD -- kriya/config/default_config.yaml`
still empty (model/fallback/capability profiles/`num_ctx=32768` all unchanged), ground-truth
isolation re-confirmed clean in both the new worktree and evidence directory. **The only intended
experimental variable relative to `d756a833` is the CTX-001-P1-C3 failure-grounded member
escalation implementation itself.**

Prepared, not executed: `run_g1_rerun2.sh` (preflight re-derived to the new frozen Kriya SHA; no
`--resume`/`--resume-id`) and `run_g1_rerun2_acceptance.sh` (all 18 required acceptance items,
items 1-15 via the expanded `inspect_g1_rerun2_trace.py`, ground truth used only starting at item
18). `inspect_g1_rerun2_trace.py` specifically instruments the CRITICAL C3 TRACE requirement
(skeleton → anchored_edit failure → SEARCH evidence → failure-grounded `MemberHintCandidate` →
structurally grounded member → `member_exact` → `REPAIR_WITH_PATCH`) directly from
`context.retry_member_hint_package`/`gate_outcomes` trace data, never inferred from final task
success. **Disclosed limitation, honestly**: today's instrumentation does not log a first-class
provenance/`is_exact`/`revision` field on that event — the script DERIVES provenance from the
logical impossibility of SOURCE 2 firing without a real line locator (a sound inference from
concrete trace facts, not a guess), reports `is_exact` as implied by `tier=="member_exact"`
per `ContextItem`'s own documented code contract, and reports `revision`/current-source evidence
as not directly observable from the trace at all. A small future production enhancement (logging
these three fields explicitly) would close this gap — out of scope for this preparation-only
session. Self-tested (not executed live) against the real `d756a833` trace: correctly reproduces
every previously-established forensic fact (8 attempts, `MEMBER_ESCALATION_FIRED=NO` since C3
didn't exist in that run, zero applied candidates, zero unauthorized changes).

Neither script has been run by this agent. Classification of the outcome, once the user runs both,
is not automatic — C3 mechanism success (did escalation correctly fire) is assessed separately
from G1 task success (did the candidate pass quality gates and all acceptance criteria); if
escalation fires correctly but the task still fails for a different reason, that is evidence for a
new bottleneck to classify, not evidence against C3.

## 15. G1-R2 observability fixes + PROVEN D1 defect (investigation, not fixed) — 2026-09-18

**Observability fixes implemented and tested (production behavior for D1/C3 decisions unchanged):**

1. **Termination trace loss (`kriya/workflow/workflow.py::_abort_without_applying`)**: the
   `human_rejected`/`approval_required` termination path's own `trace_logger.log_run()` call
   omitted `gate_outcomes`/`model_hops`/`run_events`/`evidence_records`/`generation_metrics` —
   proven the root cause of run `8cc2018a`'s own trace showing `model_hops=[]` despite 9 real
   Developer calls. Now mirrors the canonical terminal success/failure call's own field contract
   (same derivation for `failure_report`, same `files_modified=list(state.all_files_written)`
   semantic as every other termination path). Tests: `tests/test_workflow.py::
   test_workflow_human_rejected_preserves_full_forensic_trace`, the parallel `approval_required`
   extension on the existing no-callback test, and a synthetic-rich-state round-trip
   (`test_termination_trace_survives_human_rejected_with_synthetic_rich_state`).
2. **CTX-001-P1-C3 SOURCE 3 structured observability (`kriya/workflow/context_source.py`,
   `kriya/workflow/attempt.py`)**: `evaluate_member_hints_from_search_evidence()` (new) returns the
   same candidates `resolve_member_hints_from_search_evidence()` always has, plus a deterministic
   `outcome` code (`grounded_by_name`/`grounded_by_containment`/`ambiguous_name_conflict`/
   `ambiguous_containment`/`no_containment_match`/`no_distinctive_tokens`/`unsupported_language`/
   `empty_search_text`). `_resolve_retry_member_hints` now emits one
   `context.search_evidence_grounding` RunEvent per evaluated SEARCH edit — structured evidence
   only (a `content_revision()` hash + length of the SEARCH text, never the raw text itself),
   never a decision authority. Selection behavior is byte-identical before/after (proven: all 52
   pre-existing `test_context_source.py` tests unchanged). Tests: `TestC3StructuredObservability`
   in `tests/test_val001_g1_remediation.py` (accepted/ambiguous/no-grounding/not-triggered, all
   observable).

**PROVEN D1 DEFECT — investigated, NOT fixed this session (explicit STOP for review):**

`operation_for_attempt("targeted"/"fallback_targeted", ...)` (`kriya/workflow/operations.py:81-82`)
unconditionally returns `REPAIR_WITH_PATCH` as the attempt's own BASE operation, with no reference
to context completeness. `_completeness_gated_operation()`'s own first-line check
(`base_operation not in (CREATE_FULL_FILE, REPAIR_WITH_FULL_FILE): return base_operation, False`)
therefore ALWAYS short-circuits for these two modes — `mandatory_patch` is structurally `False`
regardless of whether the shown context was ever complete/exact/current. `validate_operation_
result()`'s own documented-permissive patch→full-file fallback (intended for an ordinary,
non-mandatory retry) is therefore **always** allowed to stand for a targeted/fallback_targeted-mode
attempt whose model ignores the SEARCH:/REPLACE: instruction and returns full file content instead
— exactly what happened at the real run's attempt 6 (fallback_targeted, ~744-line truncated
candidate accepted with zero operation-contract rejection). A second, propagating defect compounds
it: once that candidate is written to the sandbox worktree, the NEXT retry-package build reads the
now-small file fresh from disk, finds it fits the reference/target budget unelided, and legitimately
(by its own documented rules) records `tier=full/is_exact=True` for it — authorizing a SUBSEQUENT
`full_set`-mode attempt against content that is itself an unvalidated, already-corrupted candidate,
never the true original baseline. Both halves proven empirically and via deterministic tests in the
new `tests/test_d1_operation_mode_authority.py` (11 tests: 8 named adversarial scenarios per the
investigation task + 2 full attempt-2→attempt-6/8 state-transition reproductions + the control
proving attempt-2's own correct rejection). Real-workspace atomicity was never breached (the
post-approval, pre-workspace-mutation full-regression gate independently caught both bad
candidates) — this is an authorization-boundary defect in the pre-approval gate, not a data-loss
or atomicity defect.

**Task 4 answer (D1 exactness semantics)**: `is_exact=True` means B — exact for the represented
projection/member/region only, and D1's own `tier`/`member_id` check DOES correctly distinguish
`full+exact` from `member_exact+exact` (a member slice never authorizes whole-file replacement,
regardless of provenance — proven for both non-C3 and C3-sourced member_exact items). The proven
defect is orthogonal to that distinction: a `full+exact` grant can currently be produced from a
reference/target projection whose "current" content was never validated as safe — REFERENCE_
PROJECTION_AUTHORITY is currently unconditional (any content fitting the budget unelided is
treated as exact, with no distinction from genuinely-pristine baseline content).

No D1/C3 decision logic was modified. A fix (most likely: route targeted/fallback_targeted modes'
own model response through the SAME completeness check full_set already gets, regardless of the
attempt's own base operation) is a separate, explicit implementation decision, not made this
session.
