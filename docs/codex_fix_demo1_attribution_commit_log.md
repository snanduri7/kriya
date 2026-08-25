# `codex/fix-demo1-attribution` Implementation Commit Log

This log records the commits created locally while completing the MA7 authority and attribution work on `codex/fix-demo1-attribution`. It intentionally excludes commits imported from the read-only `milestone-decomposition` source branch. Nothing described here was pushed by Codex.

## Imported committed source

The committed `milestone-decomposition` tip merged into this branch was `fead434f6059a8ae556b229fa137b8ffe5d4a772` (`Give ReviewerAgent real Quality Gate evidence instead of a blind second guess`). The integration was a fast-forward from the prior local tip `65faf16`; imported history is not reproduced in this log.

## `7ed2ee6` — Complete MA7 authority and production hardening

### What changed

- Made structured enforce execution a real write boundary by projecting local context per subtask, bypassing redundant planning stages, and passing each subtask's declared `planned_files` into `AuthorizedFileWriter` as an allowlist.
- Added deterministic, fail-closed capability-provider resolution and strengthened contract lifecycle handling, artifact derivation, coordinate-drift checks, and milestone consumer invalidation.
- Added workspace ownership metadata and validation across ControlState, milestone sidecars, checkpoints, and decision-ledger persistence so authoritative state copied between workspaces is rejected.
- Made authoritative ControlState and artifact persistence failures terminal instead of silently continuing with incomplete recovery state.
- Added coherent `legacy`, `validated`, and `hardened` runtime presets with configuration validation that prevents ambiguous combinations.
- Expanded `kriya doctor` with local workspace/control-plane diagnostics and local-only LLM configuration reporting.
- Added content-free runtime-path comparison tooling, strengthened blocking/scheduled live-model CI coverage, and updated design, README, benchmark, and repository guidance.
- Added focused regression coverage for write authorization, workspace identity, configuration, contract/provider ambiguity, lifecycle validation, milestone enforcement, and controller behavior.

### Why

MA7's schemas and control-plane records existed, but several were still advisory or only partially connected to production execution. This change made the validated plan, filesystem scope, contract/artifact state, and workspace identity enforceable runtime boundaries. The fail-closed behavior prevents execution from continuing when Kriya cannot prove that its plan, persisted state, or artifact coordinates remain trustworthy, while preserving legacy behavior outside authoritative modes.

### Verification at handoff

- 31 focused regression checks passed.
- Python `compileall` passed.
- Focused Ruff F/B checks passed.
- The full deterministic suite was intentionally left for the user under the repository's test-running convention.

## `7a61077` — Align artifact failure regression with fail-closed mode

### What changed

- Updated the enforce-mode regression for artifact derivation failure to expect `needs_review` and failed Quality Gates instead of treating the underlying generation result as successful.
- Kept assertions that the original generation result and derivation failure details remain available for diagnosis.

### Why

The regression still encoded the earlier non-fatal artifact behavior after authoritative artifact persistence had deliberately become fail-closed. That stale expectation caused the otherwise-complete deterministic suite to report one failure. The test was corrected to reflect the production safety contract rather than weakening the implementation back to permissive behavior.

### Verification at handoff

- The user reran the full deterministic suite after this change and reported that all tests passed.

## `295c56d` — Enforce verification and transitive invalidation

### What changed

- Required authoritative MODEL subtasks to declare a non-empty `planned_files` scope; unbounded plans now stop with `UNBOUNDED_MODEL_SUBTASK` without falling back to whole-goal legacy generation.
- Threaded each subtask's declared verification methods through the mature generation workflow and returned identity-preserving verification evidence.
- Allowed only explicit built-in compile/test Quality Gate aliases to be resolved from the existing gate result. Arbitrary tools and judgment checks remain unresolved until real evidence exists, producing `NEEDS_REVIEW` rather than self-certification.
- Expanded contract-shape invalidation from direct consumers to the complete downstream milestone dependency graph.
- Revalidated replacement milestone plans, persisted a canonical plan hash and timestamped invalidation evidence, removed affected checkpoints, cleared completed IDs, and synchronized stale/done states with ControlState.
- Added milestone group/index metadata to new workflow checkpoints so affected recovery state can be identified precisely.
- Added focused regression coverage and documented the resulting authority and invalidation model in `docs/design.md` section 7.50.

### Why

Four authority gaps remained after the initial hardening: MODEL work could still be unbounded, declared verification was not authoritative per subtask, contract invalidation stopped at direct consumers, and recovery state did not retain proof that the replacement plan had been revalidated. Closing these gaps prevents stale downstream work or checkpoints from being trusted and prevents successful Quality Gates alone from silently satisfying unrelated judgment or custom-tool requirements.

### Verification at handoff

- Python `compileall` passed.
- Focused Ruff F/B checks passed for the changed production modules and focused tests.
- A direct assertion of the verification-evidence mapping passed.
- Pytest was intentionally not run by Codex; the user owns deterministic-suite execution under `CLAUDE.md`.

## Commit-message convention from this point forward

New implementation commits will use an imperative subject plus a body that records:

- what changed;
- why the change was necessary;
- compatibility and safety implications; and
- verification performed, including tests intentionally left for the user.
