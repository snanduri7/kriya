# `retrieved_chunks` and the eight stored `fields` columns: findings and the smallest proposed treatment (2026-10-05)

Investigation only. Nothing here is implemented in this batch; the protocol is unchanged. Facts are TRACED to the cited source
lines or MEASURED on the two real exports of the acceptance run (shape only; no recorded text is copied).

## 1. Writers (Kriya)

| column | writer | stored form |
|---|---|---|
| `runs.retrieved_chunks` | `kriya/workflow/graph_retrieval.py:430` (fused candidates: `{"filepath", "score", "text": "<kind> <lookup_key>: <signature>"[:300], "channels": [...]}`), `:460` (legacy-only hybrid hits: `{"filepath", "score", "text"[:300], "channels": ["legacy_hybrid"]}`), `:542` (legacy path: `{"filepath", "score", "text"[:300] + "..."}`); initialised empty at `workflow.py:2154`; passed to `TraceLogger.log_run(retrieved_chunks=...)` at `workflow.py:4472 / 5470 / 5846` | JSON text of a list of dicts (`kriya/core/trace.py:132`); `[]` when retrieval recorded nothing |
| `runs.active_skills` | the run's active skill names, `",".join(active_skills)` (`trace.py:133`); passed at `workflow.py:3940 / 4473 / 5471 / 5847` | comma-joined plain text, `""` when no skill was active |
| `runs.gate_outcomes`, `model_hops`, `run_events`, `evidence_records`, `generation_metrics`, `failure_report` | documented elsewhere (`GATE_OUTCOME_SHAPES.md`, `FIXTURE_FIDELITY.md`) | JSON text |

## 2. Adapter output (`kriya/kup/inspect.py::history_detail`)

- `fields.<col>` for each of the eight `DETAIL_TEXT_COLUMNS` (`retrieved_chunks, active_skills, gate_outcomes, model_hops,
  run_events, evidence_records, generation_metrics, failure_report`): availability `recorded` when the column is not NULL, `data`
  = the raw stored TEXT, provenance `runs.<col>`.
- A parsed section for each of the seven `JSON_TEXT_COLUMNS` (the eight minus `active_skills`): `retrieved_chunks` is therefore a
  real section of every `history.detail` answer, with the parsed list as `data`.
- `active_skills` has no parsed section: it exists only as `fields.active_skills` (raw comma-joined text).

Measured on the real exports (runs `1df27f7a`, `280bd867`): `retrieved_chunks` recorded with an empty list in both;
`fields.active_skills` recorded as an empty string in both; all eight `fields` entries recorded (text lengths 2 to 44,831 bytes).

## 3. Contract and UI treatment today

- `ui/kup/schema/history.schema.json#/$defs/RunDetail` requires the seven known sections and lists no `retrieved_chunks`;
  `additionalProperties: true` keeps it, so validation passes and the KUP validators report nothing.
- `ui/shared/src/model/normalize.ts::normalizeDetail` spreads the whole payload (`...d`), so `retrieved_chunks` survives in the
  normalized object, and `fields` is kept as-is; the generated `RunDetail` type does not name either as a typed section.
- No panel reads `retrieved_chunks` or any `fields.*` entry. The Inspector's Context tab shows only the context events; the
  offline report lists `fields.*` availability per column and lists `retrieved_chunks` under "unknown detail sections preserved".
- The evidence checker reports `retrieved_chunks` as `EVC-UNK-001` (informational) on every real export - correct under the
  current contract, but noise once the section is documented.

## 4. What recorded information is hidden

1. **Retrieval evidence**: which files the retrieval stage surfaced for the run, with their scores and channels (fused Code
   Intelligence vs legacy hybrid). This is the only persisted record of the retrieval decision for a run; today it is invisible.
2. **Active skill names**: which skills were active for the run (`fields.active_skills`), useful beside the context package.
3. The raw TEXT of every stored column (`fields.*`) is available for inspection of exactly what the row holds; today only its
   availability is shown (offline report) and nothing in the UI.

## 5. Sensitivity

`retrieved_chunks[].text` carries up to 300 characters of the candidate's signature or chunk text from the repository: source
excerpts, like `prompt_rendered`. It must follow the prompt's rule (P-23/P-25): fetched or revealed only on explicit request,
never copied into summaries or committed documentation. `filepath`, `score` and `channels` are paths and numbers (no source
text) and can be shown directly. `active_skills` is a list of skill names (no content). The raw `fields.*` text duplicates the
parsed sections and, for `run_events`, can be large (44 KB here); it needs the Payload's bounded, reveal-in-chunks rendering.

## 6. Smallest proposed change (NOT implemented; needs the owner's approval as a contract topic)

Contract (`history.schema.json`, additive, a new topic folder per the gate's rule on authority/consistency changes):
- `RetrievedChunk`: `{ filepath: string, score: number, text?: string, channels?: string[] }`, `additionalProperties: true`.
- `RunDetail.retrieved_chunks`: `Section` whose `data` is `RetrievedChunk[]`; optional (older hosts keep working), so no
  validator regeneration is forced on Kriya; regenerate TS/Java/validators once.
- `RunDetail.fields` keeps its shape; document the eight column names and that `active_skills` is comma-joined raw text.

Inspector (Context tab, two additions, no inference):
- "Retrieved chunks (recorded)": one row per chunk, `filepath · score · channels`, in recorded order; the `text` excerpt behind
  an explicit "Show excerpt" per row (sensitive, like the prompt), rendered through `Payload`.
- "Active skills (recorded)": the raw `fields.active_skills` text, or "none recorded" for an empty string / "not recorded" when
  the column is NULL.

Offline tools: the report lists the chunk rows (paths, scores, channels; text length only); the checker treats `retrieved_chunks`
as a documented section (no `EVC-UNK-001`) and checks the documented shape informationally. Fixtures: a serializer-produced
`retrieved_chunks` record through `graph_retrieval`'s own writer shape (`fixtures/serializer_*.py`), plus `fields` carrying all
eight columns as the adapter does.

Out of scope on purpose: rendering any other `fields.*` raw text beyond availability; interpreting scores; a new filter.
