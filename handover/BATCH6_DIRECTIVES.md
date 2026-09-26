# Batch 6 (PRD-025..029): user-approved directives (2026-09-26)

This is the user's approval of the Batch 6 plan, including corrections. It is the authority for every Batch 6 decision. Each PRD handover cites it.

## Process
- One local commit per PRD, plus narrow correctness-fix commits.
- No push. Stop once, at READY_FOR_PYTEST_VERIFICATION, with one focused and one full pytest command.
- pylint 0 and ruff 0 before every commit. Strict doubles. Mutation checks for new logic.
- No changes to packaged models, the demo-03 model config or PRD-019 routes.
- The user runs the full pytest and all live runs.
- Test every new result or status field on every terminal branch, prove its serialization and persistence, and prove its CLI/result exposure.
- Confirmed suspected defects are P0/P1: fix them in the batch and never defer them:
  1. PRD-025: an LLM PASS overriding a non-zero exit.
  2. PRD-028: candidate text granting pristine authority.
  3. PRD-029: a corrupt or missing registry failing open, or being overwritten.

## PRD-025: verifier evidence
- Deterministic runtime evidence always outranks the grader. An LLM PASS can never override:
  - a non-zero authoritative exit;
  - a timeout or a hung process;
  - a deterministic command or runtime failure.
- Two kinds of truncation, kept distinct:
  - CAPTURE_TRUNCATION: bytes the ProcessController lost;
  - PACKAGE_TRUNCATION: bytes Kriya retained but left out of the package.
  - Never claim to have scanned lost bytes.
- One scan over the whole retained capture for markers, assertions, exceptions, tracebacks and fatal/error signatures.
- Verdicts:
  - FAIL: deterministic failure.
  - PASS: the deterministic evidence is sufficient, or the grader says PASS and no material evidence is missing.
  - UNKNOWN when any of these holds:
    - capture loss could hide decisive evidence and no deterministic evidence closes the question;
    - decisive windows don't fit the actual request;
    - the verifier's own result is incomplete.
  - A merely verbose program is NOT automatically UNKNOWN.
- Build the package for the verifier model actually selected. On a fallback, rebuild it from the same retained evidence for that fallback's budget, and record which package each model saw.
- Record per package:
  - command, exit status, and timeout/hung state;
  - capture truncation, with lost characters where known;
  - package truncation;
  - included and omitted windows;
  - head and tail samples;
  - the verifier identity and token budget;
  - the final deterministic and semantic disposition.

## PRD-026: retry progress
- A ProgressVector covers:
  - the failure signature;
  - the candidate/workspace hash;
  - the implicated and missing files;
  - the evidence fingerprint;
  - the context revision set;
  - the action and protocol;
  - the request-profile identity;
  - the plan and repair-contract revisions;
  - deterministic diagnostics.
- Keep a set of vectors already seen. Returning to a seen vector (A→B→A→B) is NOT progress.
- Sampling is NOT progress. It is a separate typed allowance, SAMPLING_RESAMPLE, allowed only when all of these hold: the effective temperature is above 0, the retry family permits it, and budget remains.
  - It consumes budget.
  - It never resets the no-progress counter and never erases seen vectors.
  - It is never reported as progress.
  - A genuinely new resulting vector may count as progress. An identical, seen or unchanged result means terminate or transition.
- Keep the bounded behaviour of `test_workflow_fallback_chain`.
- API contract recovery gets the same cycle detection, and keeps its hard maximum.
- Telemetry:
  - `retry.strategy_transition`;
  - the vector digest and the changed dimensions;
  - SAMPLING_RESAMPLE;
  - a terminal NO_PROGRESS reason.
- Tests for every retry family, plus alternating cycles.

## PRD-027: context recall
- Extract retrieval out of `workflow.py`, byte-identical first.
- Measure recall AND precision; indiscriminate retrieval must not pass. Class targets are fixed and version-controlled; never lower one to pass.
- Classes, at minimum: same-class member, direct caller, interface/contract, one-hop, two-hop, test precedent, build metadata, configuration.
- Miss reasons: NOT_RETRIEVED, BUDGET_EXHAUSTED or TIER_INSUFFICIENT, plus other typed reasons if needed.
- The CI run uses a deterministic fake embedder and proves the mechanics only, NOT a real embedding model.
- Live certification binds to the exact embedding identity, retrieval policy, chunker/index version, limits and suite version. `kriya context certify` persists the identity and the measurements.
- `doctor --production` NEVER runs the benchmark; it reads the stored record. Missing or stale certification is a FAIL only when the active production config uses a retrieval path that needs it; a disabled or not-applicable path doesn't fail.

## PRD-028: language adapters and authority escalation
- An explicit adapter contract plus a registry, not a giant switch, and not the full plugin framework.
- Capabilities: symbol identity, member boundaries, references, exact-source recovery, editable region, verification hooks.
- Java and Python adapt existing behaviour; other languages are UNSUPPORTED.
- Escalation never creates path or write authority: context/source expansion != mutation-scope expansion.
- Candidate/worktree text never grants pristine authority. Reproduce the member-hint worktree-first read and fix it if confirmed.
- Pristine authority comes from an immutable baseline.
- Each expansion records:
  - the source revision;
  - the adapter and capability;
  - the exact member;
  - the evidence;
  - GRANTED or INDETERMINATE;
  - the mutation boundary it stays inside.
- Recheck the revision when the expansion is used.
- Report capability status in telemetry and doctor. Doctor fails only when an active production operation needs an unsupported capability.

## PRD-029: ContractRegistry lifecycle
- One lifecycle for both milestone capabilities and direct/enforce public API contracts. No Planner provides/consumes fields.
- Direct/enforce records come from:
  - brownfield public-API change detection;
  - PRD-023 classification;
  - deterministic signatures.
  - Only AUTHORIZED_DIRECT and AUTHORIZED_HUMAN changes are recorded; derived and indeterminate ones are never recorded as fact.
- A record holds:
  - its kind;
  - its owner/source;
  - the public-signature hash;
  - the post-commit revision;
  - consumers with provenance (never claiming completeness when it's unknown);
  - the authorization evidence;
  - the schema version.
- Milestone records are a separate kind under the same framework.
- A fresh run may start a valid, versioned empty registry. An absent registry never means verification is off.
- On resume, an expected registry identity that is absent, corrupt or mismatched fails closed.
- A corrupt registry raises a typed error. It is never treated as empty, never overwritten automatically, and never lets a resume or commit proceed.
- The registry schema, revision and hash are part of the PRD-008 resume fingerprints.
- Transaction:
  - Before the source commit: derive the delta, validate it, compute the expected digest and put it in the precommit evidence.
  - After: bind to the post-commit revision, then persist the registry and the RunRecord under the commit/recovery protocol.
  - A crash in between leaves a recoverable incomplete transaction: never SUCCESS, never stale state silently exposed.
  - Recovery either completes the transition exactly or marks the run NEEDS_REVIEW.
  - No post-commit error is hidden.
- When a contract changes, invalidate its known consumers, record why, and require downstream verification before global success.
