# KNOWLEDGE-READPATH-001: learned knowledge is written but never read

## Status
OPEN, P1. Found during Batch 6 (PRD-027 retrieval work). Not fixed in Batch 6, by directive.

## Classification: P1
The documentation claims that `kriya learn` content is consumed, fenced as untrusted reference material:
- `docs/user_guide.md` §3.2: "Ingested content is treated as untrusted reference material in prompts (explicitly fenced ...)".
- `docs/design.md` §2.2: the "Untrusted-Content Fencing" and "Precedence Hierarchy" bullets.
- `CLAUDE.md` pipeline step 4, "Untrusted learned-knowledge RAG".
- `CLAUDE.md` Storage section: "`vector_index.db` mixes code-index vectors and a separate `learned_knowledge` table (from `kriya learn`)". This is the wrong-database assumption the workflow reader follows; `learn` actually writes `web_knowledge.db`.

In reality no command reads what `learn` writes. A documented feature silently does nothing, so this is P1.

## The write path (the only one)
`kriya learn` (`kriya/cli.py`, the `learn` command) writes through `LocalVectorStore.add_learned_knowledge`:
- database: `<paths.memory>/web_knowledge.db`;
- table: `learned_knowledge` (text, embedding, `model_name`, `dimensions`, `provenance_url`, `fetch_date`).

## The read paths (each misses it)
| Consumer | Database | Table | Result |
|---|---|---|---|
| `kriya ask` (`cli.py` `run_query`) | `web_knowledge.db` | `vector_chunks` (`LocalVectorStore.query`) | Wrong table. Nothing writes `vector_chunks` in `web_knowledge.db`, so it is always empty. |
| `kriya generate` CLI pre-step (`cli.py` `run_workflow`) | `web_knowledge.db` | `vector_chunks` | Wrong table, always empty. |
| `WorkflowEngine.run_generation_workflow` step 1.6 (`learned_rag_context`, fenced) | `vector_index.db` | `learned_knowledge` (`query_learned_knowledge`) | Right table, wrong database. `vector_index.db` holds only the code index, so it is always empty. |

`kriya prompt generate` reads no knowledge store at all; it is not affected.

## Expected semantics
- One read path for learned knowledge: `learned_knowledge` in the database `learn` writes to, queried with the configured embedding model and dimensions (`query_learned_knowledge`), with provenance (`provenance_url`, `fetch_date`) shown.
- Learned knowledge stays a separate, lower-trust namespace from the code index, and is never merged into code retrieval.
- Every consumer that shows it to a model uses the existing fencing (`=== Begin/End Untrusted Reference Context ===` plus the do-not-follow warning), and the precedence order: repository facts, then skills/rules, then untrusted references.

## Security requirement (prompt injection and authority)
**Update (f3707c4, AUTH-GOAL-CONTAMINATION-001):** the pre-step no longer touches the goal. Its text now travels as fenced `reference_context`. Items 1 and 3 below are therefore met for the current read paths and stay requirements for the repair. This defect remains OPEN for the read-path mismatch itself.

Before that fix, the `generate` CLI pre-step appended retrieved text to the goal string itself, unfenced (`=== Web Reference Documentation Context ===`). The goal is authoritative user intent:
- PRD-020 derives original requirements from it;
- PRD-025 takes expected-nonzero-exit authority only from it;
- PRD-023 derives DIRECT contract authorizations from it.

That was dormant, because the table it reads is always empty. Redirecting that reader to `learned_knowledge` without changing where the text goes would let ingested web content declare requirements, admit a nonzero exit, or authorize a public API change. The fix must:
1. never append learned (or any retrieved) text to the goal or any other authority-bearing field;
2. carry it only as fenced, non-authoritative reference context, such as `learned_rag_context`, with the fencing that `ask` currently lacks;
3. add tests proving that learned text containing "exits non-zero", a requirement-like statement, or "change X.total to ..." grants no exit authority, creates no requirement and no contract authorization.

## Suggested tests for the fix
- `learn -t` followed by `ask`: the answer prompt contains the learned chunk inside the untrusted fence, with provenance.
- The workflow's `learned_rag_context` is non-empty after `learn`, and fenced.
- The authority tests from item 3 above.
- A regression test that nothing reads `vector_chunks` from `web_knowledge.db`.

## Fix (Backlog 6.5, 2026-09-27) - FIXED, awaiting the user's pytest run
### Canonical contract
- **Writer and store (unchanged).** The one writer is `kriya learn`. It writes `<paths.memory>/web_knowledge.db`, table `learned_knowledge`.
- **Legacy stores.** Every historical `learn` wrote this same table:
  - 959fcb0 wrote it to `web_knowledge.json`, which `LocalVectorStore` maps to `web_knowledge.db`;
  - 490a94e onward wrote `web_knowledge.db`.

  No other table or database ever held learned rows, so no migration or adapter is needed.
- **The one reader** is `kriya/memory/learned_knowledge.py::retrieve_learned_references(cfg, query)`. It returns a `LearnedRetrieval` holding references with provenance, counts of malformed and other-model rows, and `unavailable_reason`.
  - Top-k is 5 and the score threshold is 0.4, set as one constant each.
  - `LocalVectorStore.query_learned_knowledge` now filters in SQL on `model_name` and `dimensions`. Before, it ignored both arguments and loaded every blob. It returns `LearnedKnowledgeMatches`.

### Readers after the fix
| Consumer | Query | Channel |
|---|---|---|
| `ask` | the question | fenced after `User Question:`, with provenance |
| `generate` (direct and enforce) | the goal | `reference_context`, retrieved once per invocation and reused by the knowledge-gap retries |
| `generate --from-milestones` | the plan's `original_goal` | `reference_context` in every unit (via `run_milestones(reference_context=)` and `generation_kwargs`) |
| `run_generation_workflow` step 1.6 | none (it no longer retrieves) | fences the `reference_context` it is given into `learned_rag_context`, which reaches the Developer and `convention_prompt` |

Not wired, by decision:
- **`kriya fix`.** There is no user goal: the goal is Kriya's placeholder and the user input is an error log.
- **Proposal promotion.** It is deterministic.

### Failure behaviour ("fail closed where required")
Reference material is never an input to a correctness decision, so a failure never aborts a run. It contributes nothing and says why on stderr. It never reads a store partially or silently:
- **Store cannot be read** (`sqlite3.Error`): `unavailable_reason`.
- **Query embedding is all zero** (the client's outage degradation): `unavailable_reason`.
- **Egress-refused embedding endpoint** (`EgressViolationError`): `unavailable_reason`; no request is sent.
- **A row that cannot be decoded, or disagrees with its declared dimensions**: excluded and counted.
- **A row embedded by another model**: excluded and counted, and the user is told to re-run `learn`.

The broad `except Exception` blocks were removed; the reader catches only the named types.

### Injection hardening, live now that the read works
- **Fence markers.** `fence_untrusted_reference` neutralizes marker lines inside the body, so the text cannot close its own fence.
- **Skill-conventions reminders.** The Planner, Architect and Developer reminders ("apply the Engineering Skill Conventions ... must not contradict any Rule") are now keyed on `outside_untrusted_reference(...)`. Before, fenced learned text containing that phrase earned the reminder.

### Related own bug (separate commit a314d45)
f3707c4 never gave the Developer the `reference_context`.

### Tests
**tests/test_knowledge_readpath_001.py, 23 tests:**
- write→read end to end through the real `learn` CLI and each reader (ask, direct generate, milestone generate, every milestone and integration unit), with the exact query text asserted;
- the workflow embeds nothing itself;
- the store contract: normal output, other model, malformed rows, corrupt file, missing store (never created), unusable query vector, the egress boundary, CLI warnings;
- hostile learned text through a real run: fenced once, no reminder, and no requirement, exit, mutation-scope, contract or resume authority, with a control;
- the fence neutralizes markers, and an unterminated fence hides everything after it;
- structural: one module owns the store, and `reference_context`/`learned_rag_context` appear only in the prompt-path modules.

**Other test changes:**
- `tests/test_rag_queries.py` was vacuous. It passed through `ask`'s key-files scan of the seeded JSON file, while the RAG read returned nothing. It is rewritten and fails on the pre-fix code.
- `tests/test_auth_goal_contamination_001.py` patches `_learned_reference_context`. Its `vector_chunks`-seeded test encoded the defect and was removed.

**Mutations: 11 of 12 killed.** The survivor, renaming the store constant, is killed by `test_learn_command` and the structural owner test outside the `-k` filter.

**Certification identity.** `index_implementation_digest` does not cover `query_learned_knowledge` (learned text never enters code retrieval), so the stored CERTIFIED record stays valid.
