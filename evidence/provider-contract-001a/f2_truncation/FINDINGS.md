# F-2 measurements (PROVIDER-CONTRACT-001A), 2026-10-01

Runtime: Ollama 0.34.4, `/v1`, `qwen3-coder:30b-kriya-e52213655394` (served window 32,768, /api/ps), plus `qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a` in `v1_measurements.jsonl`. Requests are raw (no Kriya admission). Kriya numbers are computed for the same messages with the production qualification record's limits (floor 2.1101 bytes/token, consumption ceiling 8.0).

Files: `v1_measurements.jsonl` (first pass, sized by Kriya's count), `v1_signature.jsonl` (sized by real tokens; its "dense" calibration request was itself truncated, so its `expected_real_tokens` for dense are wrong), `native_check.jsonl` (exact counts from native `/api/chat` with `truncate:false`). Probes: `*.py.txt`.

## MEASURED
1. **/v1 truncation signature.** Every prompt larger than the served window was reported as exactly **16,386 = 32,768/2 + 2** prompt tokens. This held from 1.02x to 4x the window, for code and for dense text. No prompt below the window was truncated: at 0.9x and 0.98x, the reported count equalled the real count.
2. **Native `truncate:false`** refuses every over-window prompt with HTTP 400 `exceed_context_size_error`. The error carries the exact `n_prompt_tokens` (33,987 to 73,614 here) and arrives in about 0.1 s, served from the prompt cache.
3. **The byte check (ceiling 8.0) misses real truncation:**
   - code at 1.02x the window (real 33,387, reported 16,386, lower bound 16,188);
   - dense text at 2.07x and 2.25x the window (real 67,953 and 73,614 tokens per the native count, lower bounds 12,450 and 13,488).
4. **Admission undercounts high-entropy text.** Dense identifier-like text runs at about 1.47 bytes/token. The qualified floor is 2.11, so admission predicted 47,238 tokens for a 67,953-token prompt. For such content the "conservative" count is not an upper bound, so a production request can be admitted and then silently truncated.
5. **Legitimate reported/predicted ratios overlap truncated ones.** Legitimate code was about 0.49. Truncated prompts ranged from 0.06 to 0.35. Truncated dense text overlaps the legitimate range of other content classes, so no ratio envelope separates them.

## UNKNOWN
- Whether the signature (window/2 + 2) holds for other windows. The review's earlier 32,770 reading suggests it does for a 65,536 window, but that was not measured here.
