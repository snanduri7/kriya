# Scenario B (knowledge reuse): bounded task isolation, pre-registration

Written 2026-10-14, BEFORE any live model run of this step, on `demo/v2` after `c1bc693`. Owner instruction: "Scenario B final bounded task isolation". Nothing below is edited after a run; results go in `FINDINGS.md` (F11) and the evidence folders.

## Why a new task
The earlier application task (outline.py + test_outline.py, nested-function traversal) is classified `SCENARIO-B-COMPLEX-TASK-NOT-DEMO-SUITABLE`: it exercised multi-unit planning, a cross-unit behavioural defect, write-scope recovery, the Developer response protocol, fallback judgement and regeneration targeting, none of which is knowledge reuse. It is NOT a Kriya defect. Its evidence (steps 1-7, skills, GUI captures) is kept unchanged in `evidence/scenario-02-live-retry-1/`; the goal is kept as `goal.rejected-complex-task.md`.

## Candidate 1 (built and proven deterministically before any live run)
- Goal (`demo-v2/scenarios/02-knowledge-skill/goal.md`): implement `count_top_level_functions(source) -> int` in the existing `outline.py` stub (top-level functions incl. async; not nested, not methods; never raises on a syntax error; counts what it can recognise). Names `py-tree-sitter (tree-sitter==0.25.2)` and `tree-sitter-python==0.25.0` as required libraries (KnowledgeGuard needs the pins; a library name is not an API fact). The goal states no API fact.
- Fixture: stub `outline.py` (one production file), behavioural `test_outline.py` (8 tests: 1 passes on the stub, 7 fail), `requirements.txt` with the pins, README. No file the model can read contains an API token (enforced by `tests/test_demo_v2_knowledge_task.py`).
- Proven before any run (real pytest, no model): the 0.25 reference passes 8/8; `Language(ptr, "python")` raises TypeError, `Parser().set_language` and `Language.build_library` raise AttributeError on 0.25.2; a tolerant `ast` variant fails the syntax-error test and the library-use test; a regex variant fails the library-use test. Mutating the fixture (expected count, library-use assertion, leaking an API token) makes tests fail.
- The acquired-rule facts the task depends on (from `evidence/scenario-02-live-retry-1/acquired-skills/tree-sitter-python/rules.txt`): "The Python language object can be loaded using `tree_sitter_python.language()` after importing `tree_sitter_python as tspython` and `from tree_sitter import Language, Parser`", and "use `tree.root_node` to access the root node". The acquired `tree-sitter` skill also contains JavaScript-oriented rules (`new TreeSitter()`, `setLanguage()`), unedited.

## Candidate 2 (only if candidate 1 is rejected; designed then, not now)
At most one alternate task, built and proven deterministically before its no-skill run, leaning on a rule-covered fact other than parser construction where possible (node `type` / `children`). Noted limit: every task in this library shares the parser-construction dependency, so a no-skill PASS on candidate 1 makes a candidate-2 PASS likely too; a double reject is a legitimate result (owner section 13: STOP, return to the owner).

## Run plan (each live run once; no reruns; background; read on completion)
1. NO-SKILL baseline: same model route (`qwen3-coder:30b` digest recorded; fallback `qwen3.6:35b-a3b-q4_K_M`), same goal, same fixture, empty skills directory, `web_lookup_enabled: false`, `search.base_url: ""`. Terminal entry point behind the transparent recording proxy (record mode, no overrides), prompts answered only from the allowlist `prompt_rules.json`. Fresh state/memory/skills per run.
2. SKILL-ENABLED offline run: identical in every respect except `paths.skills` holds a COPY (`cp -R`, never moved) of the acquired skills from the preserved evidence. Skills directory digest recorded before and after (unchanged = no acquisition, no write).
3. Only the skills directory differs. Same entry point, same rendered config, same allowlist, same proxy.

## Classification of the no-skill result (decided now)
- KNOWLEDGE-RELATED: the generated code, or the run's test/traceback output, shows obsolete or wrong current-library usage: `set_language`, a two-argument `Language(...)`, `Language.build_library`, `get_parser`/`get_language` helpers, `tree_sitter_languages`, a missing or wrong `tspython.language()` use, or a library exception (TypeError/AttributeError) raised while constructing the language or parser.
- NOT KNOWLEDGE-RELATED (task unsuitable if this is the only failure): malformed edit protocol, unrelated syntax error, wrong target file, planner scope error, timeout, a counting-logic mistake with correct library construction, an aborted run.
- A no-skill run that passes completely: the task does not demonstrate acquisition value and is REJECTED (no rerun).

## Skill-enabled run: what is measured
A. which skill(s) were selected; B. which acquired rules were supplied (the actual Developer request text from the proxy log, not the skill directory); C. proof no public lookup occurred (config flags, no search endpoint, search container not running, zero "Send this search?" lines in the governance log, skills digest unchanged); D. generated code; E. compile/test result (Kriya's gates plus an independent pytest run); F. final Kriya outcome.
- One identical clean retry is allowed only if the failure is generic model/edit behaviour. A failure that follows the acquired text (for example the model copying the JavaScript `setLanguage()` rule) is ACQUISITION RELEVANCE, not generic behaviour: no retry clause applies, it is reported as is.
- Two skill-enabled failures for generic model behaviour: STOP, return to the owner.

## Wording rule
"Without the acquired skill, this measured run failed on knowledge needed for the pinned API. With the local acquired skill available and public lookup disabled, this measured run used the acquired rules and completed the same task successfully." An observed demonstration of one pair, not a statistical claim; "skill caused success" is not claimed.

## Freeze
No product change (planner, developer prompts, validators, repair, scope rules, KnowledgeGuard, SkillService, ToolService, routing, model configuration). A newly found deterministic Kriya defect: STOP and report, do not fix.
