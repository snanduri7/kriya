# Version B routing spike

Throwaway validation tool - NOT shipped, NOT wired into `kriya/cli.py`.

## Question this answers

If a user types free-form natural language at the `kriya repl` prompt instead
of an explicit command (Version B), can Kriya reliably figure out *which*
existing command they mean - `generate`, `ask`, `fix`, `review`, `analyze`,
or `skills list`/`skills show` - without a generative model in the loop at
all?

For every in-scope command, the "argument" a user would type in prose *is*
the input itself (the goal, the question, the error log) - there's nothing
to extract. That reduces "route this input" to a plain classification problem
over ~6 fixed labels, which is exactly what embedding similarity is good at.
This spike tests whether that's sufficient before any Version B design work
assumes a generative routing/tool-calling model is required.

## How it works

1. `exemplars.py` - a handful of hand-written natural-language phrases per
   in-scope command (the "reference" set).
2. `test_set.py` - ~30 held-out natural-language inputs, each hand-labeled
   with the correct command (plus a few deliberately out-of-scope/destructive
   ones labeled `unroutable`). Never reused as exemplars.
3. `classify.py` - three classifiers, all embedding via the project's own
   `OllamaEmbeddingClient` (same class `kriya learn`/`kriya ask` use):
   - `ExemplarClassifier` - nearest single exemplar by cosine similarity.
   - `CentroidClassifier` - nearest per-command averaged embedding.
   - `HybridGateClassifier` - runs `gate.py`'s LLM in-scope/out-of-scope
     check first, only falls through to a `CentroidClassifier` when the gate
     says the input is genuinely one of Kriya's six actions.
4. `gate.py` - narrow, single-purpose LLM classification ("is this even one
   of Kriya's six actions, yes or no") with a few-shot-anchored prompt. Fails
   closed: any unparseable/errored response is treated as out of scope.
5. `run_spike.py` - runs the whole test set through whichever classifier
   `--mode` selects, prints overall accuracy, per-command accuracy, a
   confusion breakdown, and a go/no-go verdict.

## Running it

Needs a live LLM/embedding endpoint reachable at the project's configured
`llm.base_url`/`embedding.base_url` (defaults to a local Ollama instance).

```bash
.venv/bin/python spikes/version_b_routing/run_spike.py --mode hybrid \
    --embed-model embeddinggemma:latest --llm-model qwen3-coder:30b
```

## Go/no-go bar

- >= 90% overall accuracy
- Zero dangerous misroutes (an out-of-scope/destructive-sounding input must
  never get classified into an in-scope actionable command)

## Findings

Pure embeddings-based routing (`nearest`/`centroid` modes) was tried against
four local embedding models (`nomic-embed-text`, `embeddinggemma`,
`nomic-embed-text-v2-moe`, `qwen3-embedding`). Best case (embeddinggemma +
centroid) reached 81.1% overall - but **every single embedding model, in
every aggregation mode, scored 0/5 on the `unroutable` category**: raw cosine
similarity cannot structurally distinguish "genuinely in scope" from
"topically similar but not actually one of these 6 things." A destructive
input like "delete all my files" scores in the same 0.5-0.6 similarity band
as a correct match, so no fixed threshold can separate them. Embeddings alone
is a **NO-GO**, specifically on safety.

Adding `gate.py`'s LLM in-scope gate ahead of the embeddings classifier fixed
the safety failure completely (unroutable 5/5 across every subsequent hybrid
run) but initially hurt overall accuracy by over-rejecting legitimate
requests - traced to two separable causes, each independently confirmed:

1. **Prompt calibration**: the gate's first version had no few-shot examples
   and over-rejected several categories (broadly with `qwen3-coder:30b`,
   narrowly on `skills` phrasing with `qwen3:8b`). Adding a handful of
   few-shot examples anchoring tricky cases (especially "is a skills lookup
   in scope") fixed this without touching the classifier logic at all.
2. **Thinking-mode latency (and a correctness risk)**: `qwen3:8b` defaults to
   Qwen's hidden "thinking" mode, adding 4-13s of invisible reasoning per
   gate call - confirmed via direct Ollama API testing (`/api/chat` with
   `"think": false` cut a call from 9.6s/414 reasoning tokens to 431ms with
   none). Kriya's `LLMClient` goes through the OpenAI-compatible endpoint by
   design (portability across any OpenAI-compatible server, not just
   Ollama), and on Ollama 0.32.5 that endpoint does **not** honor `think:
   false`, `chat_template_kwargs.enable_thinking: false`, or an inline
   `/no_think` suffix - only the native `/api/chat` endpoint does. In one
   isolated test, the hidden reasoning also talked the model into the wrong
   (unsafe) answer before self-correcting on a later run - thinking mode is
   a latency AND a reliability risk here, not just a latency one. The fix
   was model selection, not configuration: `qwen3-coder:30b`'s template has
   no thinking-mode machinery at all, so it was fast (0.3-0.4s/call) from
   the start with no suppression needed.

**Final result - `hybrid` mode, `embeddinggemma` embeddings + `qwen3-coder:30b`
gate (both with the few-shot-tuned prompt/exemplars in this repo): 34/37
(91.9%) overall, 5/5 unroutable, zero dangerous misroutes, ~0.34s average
gate latency. Verdict: GO.**

Reproduce with:
```bash
.venv/bin/python spikes/version_b_routing/run_spike.py --mode hybrid \
    --embed-model embeddinggemma:latest --llm-model qwen3-coder:30b
```

Implication for Version B design: route via a cheap embeddings centroid
classifier for *which* command, gated by a fast, non-thinking, few-shot-tuned
LLM call for *whether to route at all*. Reasoning-capable models should be
avoided for this specific gate role - both for latency and because unchecked
hidden reasoning was observed rationalizing its way to an unsafe answer.
