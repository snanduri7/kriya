# KNOWLEDGE-READPATH-001: learned knowledge is written but never read

## Status
OPEN, P1. Found during Batch 6 (PRD-027 retrieval work). Not fixed in Batch 6, by directive.

## Classification: P1
The documentation claims that `kriya learn` content is consumed, fenced as untrusted reference material:
- `docs/user_guide.md` §3.2: "Ingested content is treated as untrusted reference material in prompts (explicitly fenced ...)".
- `docs/design.md` §2.2: the "Untrusted-Content Fencing" and "Precedence Hierarchy" bullets.
- `CLAUDE.md` pipeline step 4, "Untrusted learned-knowledge RAG".

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
The `generate` CLI pre-step appends retrieved text to the goal string itself, unfenced (`=== Web Reference Documentation Context ===`). The goal is authoritative user intent:
- PRD-020 derives original requirements from it;
- PRD-025 takes expected-nonzero-exit authority only from it;
- PRD-023 derives DIRECT contract authorizations from it.

Today this is dormant, because the table it reads is always empty. Redirecting that reader to `learned_knowledge` without changing where the text goes would let ingested web content declare requirements, admit a nonzero exit, or authorize a public API change. The fix must:
1. never append learned (or any retrieved) text to the goal or any other authority-bearing field;
2. carry it only as fenced, non-authoritative reference context, such as `learned_rag_context`, with the fencing that `ask` currently lacks;
3. add tests proving that learned text containing "exits non-zero", a requirement-like statement, or "change X.total to ..." grants no exit authority, creates no requirement and no contract authorization.

## Suggested tests for the fix
- `learn -t` followed by `ask`: the answer prompt contains the learned chunk inside the untrusted fence, with provenance.
- The workflow's `learned_rag_context` is non-empty after `learn`, and fenced.
- The authority tests from item 3 above.
- A regression test that nothing reads `vector_chunks` from `web_knowledge.db`.
