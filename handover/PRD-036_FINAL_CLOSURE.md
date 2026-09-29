# PRD-036 Final Closure: Canary Rollout and Final Production Gate

**Status: CLOSED. Final gate VERIFIED.**

**Certified executable release candidate: `prd-036-rc9` at `7bc686f`.** The commit carrying this document is evidence-only and is not a release candidate; see `handover/PRD-036_CERTIFICATION.md` for every gate result and its evidence.

## What PRD-036 delivered

- **Release-candidate identity** (`kriya/core/release_candidate.py`, `scripts/release_candidate.py`): one digest over the release identity, the operator production config (rule-pack content included), every role's exact model identity and the case set. Any drift makes it STALE.
- **Release streak** (`model_certification.record_trial`): a sealed, append-only trial log keyed to the candidate. Anything but an 11/11 matrix on that exact identity resets it to 0.
- **Production canary** (`scripts/prd036_canary.{sh,py}`): a two-run production-profile brownfield fix. It checks both runs, and it proves each run's starting worktree state and bounded worktree reuse.
- **Final gate** (`scripts/prd036_gate.py`): it reads the evidence and decides VERIFIED only when every criterion holds, hosted CI included.

## Release history

Each candidate below had to earn its own evidence. A production change always meant a new candidate and a full redo: certification, canary and a fresh streak. No failed attempt was rewritten as a pass. Its evidence stays under `evidence/PRD-036/`.

| Candidate | Deterministic | Canary | Matrices | Outcome |
|---|---|---|---|---|
| rc2 `a8351d2` | CERTIFIED (7141) | FAIL (harness check) | — | harness defect: retained worktrees counted as leaks |
| rc3 `6cb0b7f` | aborted | — | — | superseded by WORKTREE-CANONICAL-ROOT-001 |
| rc4 `b73edb2` | CERTIFIED (7181) | FAIL | — | stale leftover state, plus two defects |
| rc5 `355417d` | CERTIFIED (7190) | PASS | 11/11, 11/11, **10/11 (C6)** | P1 WORKFLOW-RECOVERY-HANDBACK-001 |
| rc6 `29c26d2` | CERTIFIED (7201) | **FAIL, run 2** | — | P1 PLAN-OBLIGATION-SUPERSEDED-001 |
| rc7 `2ab3e83` | CERTIFIED (7210) | PASS | **10/11 (C8)** | P1 FAILURE-SIGNATURE-RUN-NOISE-001 |
| rc8 `5727f99` | CERTIFIED (7215) | PASS | 11/11, **10/11 (C10)** | P1 FAILURE-SIGNATURE-SHIFTING-VALUES-001 (own bug) |
| **rc9 `7bc686f`** | **CERTIFIED (7219)** | **PASS** | **11/11, 11/11, 11/11** | **VERIFIED, with hosted CI 36500271929** |

rc5 is the clearest case. Deterministic certification passed, the canary passed, and two complete live matrices passed 11/11. The third matrix then exposed a P1 workflow defect that none of the earlier gates could reach. Repeated live trials exist to find exactly that.

## Defects found and fixed

Every defect has its own commit, a regression test that fails without the fix, mutation checks, lint at zero, a subsystem test run, and a CLOSED registry row. Nothing was left in a backlog.

| Found in | Defect | Class | Fix |
|---|---|---|---|
| rc2 canary | canary counted any retained worktree as a leak | harness | `bf36fb5`: retained worktrees checked by invariant; bounded reuse over two runs |
| rc2/rc3 review | nested candidate worktree inside the plan worktree | product, WORKTREE-CANONICAL-ROOT-001 | `a520879`, `59bf1bb`, `b73edb2`: every managed worktree rooted at the canonical workspace; nesting refused |
| rc4 canary | canary "fresh workspace" kept a leftover nested worktree (`git clean` skips `.git`-file directories) | own bug (harness), CANARY-FRESH-WORKSPACE-001 | `5deadce`: `prd036_canary.py reset`, plus a start-state check for each run |
| rc4 canary | Kriya never removed the nested worktree an older layout left | own bug (upgrade gap), WORKTREE-LEGACY-NESTED-001 | `355417d`: legacy nested worktrees removed innermost first; fails closed |
| rc5 matrix (C6) | an exhausted API-contract recovery ended the run before the configured fallback, and kept later retries pinned to the primary | product P1, WORKFLOW-RECOVERY-HANDBACK-001 | `29c26d2`: recovery owns restoration only; once the contract is restored and recovery's budget is spent, it hands back to the ordinary retry/fallback policy (no new budget; an unrestored contract still fails closed); the loop and attempt-mode decisions share one state reader |
| rc6 canary (run 2) | an obligation recorded against a rejected plan draft blocked a run whose accepted plan fully succeeded | product P1, PLAN-OBLIGATION-SUPERSEDED-001 | `2ab3e83`: unresolved terminal plan obligations that a later draft no longer contains stop being terminal (history kept) |
| rc7 matrix (C8) | per-run object addresses made an identical failure a "new family", resetting the retry budgets, so the fallback was never reached | product P1, FAILURE-SIGNATURE-RUN-NOISE-001 | `5727f99`: run noise normalized in the failure signature |
| rc8 matrix (C10) | same class, still open: static-rule line and snippet, edit-shifted line numbers, timestamps, UUIDs, temp names, PIDs | own bug (incomplete rc8 fix), FAILURE-SIGNATURE-SHIFTING-VALUES-001 | `7bc686f`: a static rule is identified by its check and file; shifting values normalized |

## Decisions that stand

- **PRD-026 no-progress bound: unchanged and intentional.** A primary that keeps producing non-progressing output hits the global no-progress safety bound, and the run stops without a fallback attempt. That is a global safety invariant, not a subsystem-local counter suppressing a legal transition. There is no evidence it is wrong. Whether a fallback should ever be tried before that ceiling belongs to the queued post-PRD-036 state-machine hardening audit (user decision, 2026-09-28).
- **Test cases were never changed to avoid an observed failure, and failed candidates were never re-run hoping for variance** (user decisions at rc5).
- **Superseded tags stay local.** Only `prd-036-rc9` is published; `prd-036-rc2` to `rc8` are not.

## Registry

- **LIVE-CERTIFICATION-REPEATED-TRIALS-001: CLOSED** at streak 3 on `prd-036-rc9`.
- **CERT-NETWORK-DEPENDENCY-001 (P3): retargeted.** The real Maven Central acquisition in the deterministic tier did not time out in any PRD-036 certification run (rc2 and rc4 to rc9). It was not implemented in PRD-036 and remains open for post-PRD-036 certification infrastructure.
- Open P0/P1: none.
