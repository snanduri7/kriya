# Demo V2 findings (2026-10-14)

Evidence status per finding: MEASURED (observed), TRACED (code read), INFERRED (not proven). Nothing here changes product behaviour; items needing a product decision are registered or listed for the owner.

## F1. Scenario 02 (knowledge acquisition) is STOPPED and returned to the owner (stop rule 4)
**Observation (MEASURED).** Governed acquisition from the terminal, with the real `qwen3-coder:30b`, a local SearXNG and the technology py-tree-sitter 0.25:
- SearXNG answered, and its top hit for the library is the official `py-tree-sitter/examples/usage.py` (the modern API).
- The need is real: with no knowledge, the model's attempt failed (it confused the old and new `Language` API and the tests failed). Exploration 1.
- Kriya's KnowledgeGuard only checked the libraries once the workspace had a `requirements.txt` (Python stack detection, `RegistryAdapterFactory.detect_stack`) AND the goal stated pins in `name==version` form (`extract_library_versions`). Then it flagged both packages HIGH risk (released 2025-09, after the 2023-11-30 cutoff) and asked the operator to proceed; the answer was recorded in the governance log. Without those two, it reported "Knowledge resolution: cleared" and no acquisition was attempted (TRACED + MEASURED).
- After the acknowledgement, the run stopped in planning in all four runs that had `requirements.txt`: see F2. No lookup prompt, no reference review and no skill was ever produced, so steps 3-10 of the acceptance sequence are NOT measured.
**What is established:** steps 1 (skill absent) and part of 2 (acquisition invoked, governed prompt answered and logged) only.
**Not established / not claimed:** that this technology can pass steps 3-10; steps 8-10 also depend on a risk not yet tested (a freshly acquired skill is unverified, and the desktop declines the knowledge decisions an unverified relevant skill can require; the Run Setup text says so).
**Earlier evidence is unchanged:** Artemis (demo-02) acquired knowledge that did not cover the task, so it is not a candidate either.
**Options for the owner:** (a) classify and, if justified, fix F2, then re-attempt this candidate; (b) pick another candidate whose stack Kriya plans reliably (the Maven path plans fine but needs network dependency acquisition); (c) reduce scenario 02 to "mechanics only" (terminal acquisition and the GUI skill/provenance view, no claim about the offline task), which is the part already evidenced by the earlier LEARN-A run and the new Skills page; (d) drop scenario 02 from Demo V2.
The built groundwork stays in `demo-v2/scenarios/02-knowledge-skill/` (goal, configs, the prompt allowlist, `scripts/answer_prompts.py`); it is UNVALIDATED.

## F2. Planner/plan-validation: installed third-party package expressed as a capability prerequisite (registry: PLAN-EXTERNAL-PACKAGE-CAPABILITY-001, P2, DEFERRED)
**Observation (MEASURED, recorded planner answers).** The plan has two units. Unit s1's planned file `outline.py` carries `requires_capabilities: ["tree-sitter-python"]`; s1 has `requires: []` and no unit `provides` that capability. `kriya/workflow/plan_validation.py:824-830` rejects it (`PLANNED_ARTIFACT_PREREQUISITE_UNDECLARED`). The planner returned 2 distinct answers across 3 attempts and both had the same defect, so the two bounded repair rounds did not help.
**Counts:** 4 of 4 runs with a `requirements.txt` failed this way (goal wording variants included); the one run without a `requirements.txt` produced a valid plan (but then KnowledgeGuard did not check the libraries).
**Classification:** UNKNOWN (planner behaviour versus a plan-contract gap). Not fixed here: Demo V2 makes no product change, and the owner's rule is not to expand platform scope to rescue a scenario.

## F3. A localhost model stub is accepted through ordinary configuration, and Kriya verifies the provider (scenario 03)
MEASURED: the stub needs the same one-time operator approval as any non-default `llm.base_url`; Kriya refused a stub that reported 1 prompt token for an 8 KB prompt (`PROVIDER_PROMPT_TRUNCATED`), so the stub reports honest counts. This is the product doing its job, not a defect.

## F4. The packaged default fallback chain points at a real model server
MEASURED: with only `llm` overridden, a failed attempt escalated to `qwen3.6:35b-a3b-q4_K_M` on `localhost:11434`, so a "scripted" run was partly answered by a real model. Scenario configurations that must be model-free set `llm_chain` explicitly. Worth knowing for any isolated or air-gapped use of an overridden `llm`.

## F5. A GUI-launched run needs an operator approval of a workspace-supplied configuration (scenarios 01 and 03)
MEASURED: `kriya.yaml` in the workspace is discovered; 10 security-authority fields (model endpoints, state paths, workflow controller) are refused until `kriya authority approve`. An explicit `--config` outside the workspace needs the same approval for non-default endpoints. Documented in the scenario contracts.

## F6. Repository lint gate
MEASURED: `ruff check .` reports 7 findings on `main` (1064b9e), all in `handover/MODEL-ROUTING-V1/tools/*.py` (frozen files whose digests the Model Routing bank manifest records). Not touched. Excluding that folder in the ruff configuration, or re-freezing the bank, is an owner decision; `kriya`, `plugins` and `tests` are ruff and pylint clean.

## F7. Rail shortcuts
The cockpit numbers destinations by position. With Skills and Tools inserted before Settings, Settings moves from the third to the fifth destination.

## F8. `execution.context` Skills block: differences introduced by moving it onto SkillService (from the independent review)
MEASURED/TRACED: the block is otherwise unchanged (the 69 existing context tests pass), but (a) `total` counts unique skill ids, so two skills sharing a name are listed once (it used to count both); (b) `description` and `verified_context` are bounded to 500 characters; (c) provenance and a digest are now read for each skill (extra file reads and one hash per skill, per request); (d) the workspace handed to the engine is its realpath. A malformed `rule_provenance.json` can no longer break the block (one skill degrades to an explicit unknown).

## F9. Independent review (2026-10-14): findings and disposition
Fixed with a failing test first: unhashable provenance `text` aborted `execution.context`/`skills.*` (MAJOR); `ToolService` was not total (invalid UTF-8 store, non-string tool names; MAJOR); the prompt driver matched allowlist phrases against earlier output (MAJOR); a recorded source could carry a local path or URL credentials, contradicting the "no path" claim (MAJOR); a skill could show another skill's digest (MINOR); a store written for another workspace was counted as this workspace's records; `verify_demo.sh` deleted the workspace `.kriya/`; `assert_work_outside_repo` accepted a missing parent or a symlink into the repository, and nothing stopped `rm -rf` on a non-demo directory (a marker is now required).
Accepted, documented: schemas keep `additionalProperties: true` (the repository's convention; the blob-grep tests are the guard for the "never" claims); the catalog capability features are advertised and unconsumed; `ToolDetail` calls both `tools.describe` and `tools.approval_state` on purpose (it exercises the typed approval operation); scenario 01 cannot be prepared on a fresh machine without the local fixture source; the stub trusts `Content-Length` (localhost, single client); path-with-`#`/quote edge cases in the shell scripts.
Reworded: the "no MCP runtime import" test now says it checks direct imports; scenario 03's "second Developer request" is evidence that a repair was requested, not proof of why.
