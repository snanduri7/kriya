# Kriya v0.9.0 Demo Validated

## Release Identity
Tag: `kriya-v0.9.0-demo-validated`
Commit: `740ddfd9cb4322d06f0479870f56a53d9eaff0b4`
Branch: `milestone-decomposition`
Date: 2026-09-11

## Purpose
This is the immutable Kriya baseline qualified for the September 2026
technical demonstration (Demo 01-04: skill knowledge transfer, live
knowledge acquisition, bounded brownfield authority, and the policy
control boundary).

**This tag does not represent general production-readiness certification.**
`docs/assurance/KRIYA_PRODUCTION_RISK_REGISTER.md` at this commit lists
multiple `NEEDS_EVIDENCE`/`NEEDS_IMPLEMENTATION` items - see "Known
Limitations / Open Assurance Items" below. This tag certifies only that
this exact commit is what the four demo packages were qualified/executed
against.

## Validation Baseline

**Full pytest:**
- Last exact, independently-verified numeric result: `3407 passed, 5
  deselected, 124 warnings, 0 failures`, at commit `cbc5d86` (2026-09-10;
  `docs/assurance/KRIYA_PRODUCTION_RISK_REGISTER.md:461`).
- Three commits landed on top of `cbc5d86` before this release commit:
  `9726ff4` (+8 tests), `3e27e54` (+12 tests, 3 corrected - a real
  independent user pytest run at that point caught 1 failure, `3426
  passed / 1 failed`, which this commit fixed), and `740ddfd` itself
  (+17 tests: 9 in `tests/test_service_runtime.py`, 8 in the new
  `tests/test_finite_command_artifact_preparation.py`).
- The user independently ran the full suite again after `740ddfd` and
  confirmed **"pytest all green"** (this session, 2026-09-11) - a real,
  user-executed confirmation at the exact release commit, though the
  precise updated pass count from that specific run was not captured
  verbatim in this session's transcript. Recorded here honestly as a
  qualitative "0 failures" confirmation layered on top of the last exact
  numeric anchor above, not as a fabricated new count.

**Demo qualification:** each of the four demo packages (outside this
repo, at `~/kriya-live-demo/`) records `kriya_commit`/`kriya_repo_sha` in
its own real evidence files, all confirmed to equal `740ddfd...` (this
release commit) by direct grep of those files during release
preparation:
- `demo-02-knowledge-acquisition/evidence/acquisition/run-meta.json`
- `demo-02-knowledge-acquisition/evidence/offline-use/run-meta.json`
- `demo-03-brownfield/evidence/baseline/baseline-meta.json`
- `demo-04-control/evidence/run/run-meta.json` and
  `evidence/run/control-boundary-evidence.json`

**Known successful scenarios:**
- Demo 01, Run A (no-skill Ignite baseline): executed live during
  rehearsal. Surfaced and led to two real correctness fixes now part of
  this commit's ancestry (`9726ff4`, `3e27e54` - Reviewer narrative
  falsely describing a rejected/unapplied candidate as a working
  deliverable).
- Demo 02 (Apache Artemis knowledge acquisition + offline reuse):
  executed live end to end. Acquisition step passed Quality Gates on
  attempt 5/5 (four earlier attempts were genuine model/library-API
  mistakes against a post-knowledge-cutoff library, not Kriya defects -
  including the `finite_command` artifact-preparation gap this exact
  commit fixes). Offline-reuse step re-ran the same goal with web lookup
  structurally disabled, using only the acquired skill.
- Demo 03 (bounded-authority brownfield fix, graphify-poc): qualification
  evidence captured (pristine baseline compile PASS, 53/0/0/0 tests,
  reset reproducible) - **not yet executed** by the user as of this
  release.
- Demo 04 (control boundary / policy enforcement, zero-LLM): executed
  live. Real `AuthorizedFileWriter` genuinely blocked an out-of-workspace
  write (`PolicyDeniedError`, nothing written) and genuinely allowed an
  in-workspace write (content verified byte-for-byte); audit-vs-enforce
  differential and structured audit evidence both confirmed. `RESULT:
  PASS`, exit code 0.

## Runtime Environment
macOS: 26.6.2 (build 25G83)
Architecture: arm64 (Apple M1 Max)
Python: 3.14.6 (both system `python3` and the project's `.venv`)
Ollama: 0.33.3

## Local Models

Kriya's `agent_llms` config (`kriya/config/config.py::AgentRolesConfig`)
lets Planner/Architect/Reviewer/RunVerifier/SkillGap/SpecCompliance each
take an independent model override; Developer always uses the top-level
`llm`/`llm_chain`. **None of the four demo configs set `agent_llms` at
all**, so at this commit, with these configs, every role below in fact
resolves to the SAME single model - stated explicitly per phase rather
than assumed, since the config mechanism to differ does exist.

| Phase | Model | Digest (`ollama list`) |
|---|---|---|
| Planner | `qwen3-coder:30b` (top-level `llm.model`, no `agent_llms.planner` override) | `06c1097efce0` |
| Architect | `qwen3-coder:30b` (no `agent_llms.architect` override) | `06c1097efce0` |
| Developer | `qwen3-coder:30b` (`llm.model`, escalates via `llm_chain` on Quality-Gate failure) | `06c1097efce0` |
| Verifier (RunVerifierAgent) | `qwen3-coder:30b` (no `agent_llms.run_verifier` override) | `06c1097efce0` |
| Fallback (`llm_chain[0]`, all roles) | `qwen3.6:35b-a3b-q4_K_M` | `07d35212591f` |
| Embedding (RAG index) | `nomic-embed-text:latest` (Demo 01/03 configs; packaged default) | `0a109f422b47` |
| Routing/classification (`kriya repl` NL routing, `routing.embed_model`) | `embeddinggemma:latest` | `85462619ee72` |

`ollama show qwen3-coder:30b`: architecture `qwen3moe`, 30.5B params,
262144 context length, Q4_K_M quantization.
`ollama show qwen3.6:35b-a3b-q4_K_M`: architecture `qwen35moe`, 36.0B
params, 262144 context length, Q4_K_M quantization.

## Important Configuration

Values below are the packaged defaults (`kriya/config/default_config.yaml`)
at this commit, which every demo config either inherits unchanged or
overrides narrowly and disclosedly in its own `qualification.md`
(Demo 02/03/04 each documents its own deltas - not restated here to avoid
duplication/drift):

- **Execution policy:** `execution_policy.enabled: true`, `mode: audit`
  (packaged default - `enforce` is selectable since POL-001-P2 but not
  the shipped default). `AuthorizedFileWriter`
  (`kriya/policy/filesystem.py`, MA4.16) is the one real-enforcement
  policy code path, unconditional regardless of `execution_policy.mode`.
- **Workflow controller:** `enabled: false`, `mode: shadow` (packaged
  default). Demo 03's config explicitly sets `enabled: true, mode:
  enforce` for its bounded-authority scenario - a narrow, disclosed
  per-demo override, not a change to the shipped default.
- **`runtime_profile`:** unset (`null`) in the packaged default. Demo 01
  and Demo 02 configs explicitly do NOT set `runtime_profile: hardened` -
  removed deliberately after a real live incident
  (`RECOVERY_GENERATION_TARGET_REJECTED`/`NO_AUTHORIZED_REPAIR_TARGET`)
  on a multi-file fix that crossed a subtask authorization boundary.
- **Retries/time budgets:** Developer/Quality-Gates retry loop bounded by
  `max(4, 1 + len(llm_chain))`; `autonomy.generation_time_budget_seconds`
  unset by default (null - unbounded), set to conservative fixed values
  per-demo (e.g. Demo 03: 900s) where used.
- **Context:** `llm.extra_body.options.num_ctx: 32768` (packaged
  default); `llm_chain[0].context_window: 32768`.
- **Outward lookup:** `search.base_url` and `autonomy.web_lookup_enabled`
  both packaged-on by default, but Demo 02's offline-use config and Demo
  03/04 (which have no knowledge-gap dimension at all) explicitly disable
  both.
- **Tools/MCP:** `mcp: {}` in the packaged default - no MCP servers
  configured for any of the four demos.
- **LSP:** optional, degrades cleanly if `jdtls` is absent
  (`kriya/tools/lsp.py`). Confirmed present on this machine
  (`/opt/homebrew/bin/jdtls`, requires Java 21+, available independently
  of any project's own JAVA_HOME) during Demo 03 qualification.
- **Skills:** `skills.load_global: true, load_cwd: true` packaged default;
  every demo config narrows this (`false`/`false`, pointed at an
  explicitly empty or purpose-built skill directory) to keep its own
  scenario deterministic.
- **Compile/test gates:** `PolymorphicValidator`
  (`kriya/tools/validate.py`) auto-detects the target stack from real
  markers (Java: `pom.xml`/`build.gradle`; Python:
  `requirements.txt`/`pyproject.toml`/etc.) - no demo-specific override.

## Direct Dependencies (`pyproject.toml`, unchanged at this commit)

`click>=8.0.0` (installed 8.4.2), `pydantic>=2.0.0` (2.13.4),
`pyyaml>=6.0` (6.0.3), `openai>=1.0.0` (2.50.0), `jinja2>=3.0.0` (3.1.6),
`mcp>=2.0.0` (2.0.0), `httpx>=0.24.0` (0.28.1), `numpy>=1.24.0` (2.5.1),
`prompt_toolkit>=3.0.0` (3.0.53). `requires-python = ">=3.10"`. No lock
file exists in this repo - `pyproject.toml` itself (already in the tagged
tree) is the canonical dependency record; installed versions above are
`pip freeze` supporting evidence from the exact demo-validation
environment, not a replacement for it. 55 packages total resolved
(including transitive deps) in that environment.

## Demonstrated Capabilities

Only capabilities actually qualified/executed during demo preparation:

1. **Skill-based knowledge transfer** (Demo 01): the same local model,
   given the same goal, produces materially different (and materially
   more correct, on a documented library-quirk class) output with vs.
   without a pre-built engineering skill available.
2. **Live knowledge acquisition -> skill build -> offline reuse** (Demo
   02): KnowledgeGuard detects a real post-cutoff library gap, Kriya
   performs a live, approval-gated outward web lookup, builds a
   persisted skill from what it finds, and a later run with outward
   lookup structurally disabled reuses that skill successfully.
3. **Bounded-authority brownfield modification** (Demo 03, qualified,
   not yet executed): repository/LSP structural analysis of a real,
   pre-existing repository, a single-subtask `workflow_controller`
   enforce-mode authorization scoped to exactly one file, compile/test
   regression verification (53/0/0/0) before acceptance.
4. **Policy control boundary** (Demo 04): a model-proposed file write
   outside the authorized workspace root is genuinely blocked
   (`PolicyDeniedError`, nothing written) by `AuthorizedFileWriter`; the
   identical write inside the authorized root genuinely succeeds; the
   same denied request additionally shown, correctly, to be only
   audit-logged (never gated) via the generic `ExecutionPolicy` path
   every other MA4 call site uses today.
5. **`finite_command` runtime-verification artifact preparation**
   (this commit's own fix): a `java -jar`/`java -cp` runtime-verification
   command now runs `mvn package` first (previously only
   `managed_service` verification did), fixing a real
   "Unable to access jarfile" misclassified-as-environment-failure gap
   found live during Demo 02 preparation.

## Known Limitations / Open Assurance Items

Per `docs/assurance/KRIYA_PRODUCTION_RISK_REGISTER.md` (Pass 3) at this
commit - stated as-is, not softened:

- **VER-006** (runtime-verification LLM fallback can upgrade
  deterministically distrusted evidence into terminal success):
  **Disposition: NEEDS_EVIDENCE, not CLOSED.** Deterministic containment
  is implemented and self-tested (28 tests, pytest-confirmed clean), and
  a real production-path validation exists (real subprocess, real local
  `RunVerifierAgent`) - but the single most decisive live differential (a
  real LLM independently returning `passed: true` over distrusted
  evidence, with Kriya's deterministic caller blocking it anyway) has not
  yet been observed against a real model.
- **CORR-018** (unauthorized behavioral drift within authorized files):
  **Disposition: NEEDS_IMPLEMENTATION.** The A3-bound proposal-promotion
  slice (an explicitly authorized semantic region set) is implemented,
  committed, and live-validated (Demo/A4 run against
  `graphify-poc/spring-boot-application-example`). The general case - a
  plain hand-typed `kriya generate <goal>` with no persisted/approved
  proposal and no authorized-region set - remains open.
- **SEC-001** (hostile-code containment for generated/executed code):
  **Disposition: NEEDS_IMPLEMENTATION**, Effective Evidence Level E1.
- Several other register rows (SEC-002/003/004, VER-004/005, etc.) also
  carry `NEEDS_EVIDENCE`/`NEEDS_IMPLEMENTATION` dispositions - see the
  full register at this commit for the complete, current picture. This
  tag does not close or alter any of them.

## Reproducing This Version

### A. Inspect/run the immutable tagged source

```bash
git fetch --tags
git switch --detach kriya-v0.9.0-demo-validated
```

This puts the working tree in a **detached HEAD** state - expected and
correct for inspecting or running this exact historical version. Do not
make new commits here for normal development; switch back to
`milestone-decomposition` (or another branch) first.

### B. Create an experimental branch from the release

```bash
git switch -c demo-v0.9-replay kriya-v0.9.0-demo-validated
```

Use this if you want to make changes on top of this exact baseline
(e.g. to replay or extend a demo scenario) without disturbing
`milestone-decomposition`.

### C. Recommended: a dedicated worktree

```bash
git fetch --tags
git worktree add ../kriya-v0.9.0-demo kriya-v0.9.0-demo-validated
```

This lets you have both checked out side by side, on disk, at the same
time, with no branch-switching required:

```text
kriya/               current development (milestone-decomposition)
kriya-v0.9.0-demo/   historical demo-qualified version (this tag)
```

Remove it when done (this never removes the tag itself):

```bash
git worktree remove ../kriya-v0.9.0-demo
```
