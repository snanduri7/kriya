"""Runs the Version B routing spike: classifies every input in test_set.py
and reports strict accuracy, "effective" accuracy (which counts a CLARIFY
outcome as a success when the offered candidates include the true answer -
asking a good disambiguating question is a good outcome, not a failure),
a category breakdown, and a go/no-go verdict. See README.md.

Usage:
    .venv/bin/python spikes/version_b_routing/run_spike.py --mode hybrid-ask \
        --embed-model embeddinggemma:latest --llm-model qwen3-coder:30b
"""
import argparse
import asyncio
from collections import defaultdict

from kriya.config.config import load_config
from kriya.core.llm import LLMClient
from kriya.memory.vector import OllamaEmbeddingClient

from classify import (
    AskWhenUncertainClassifier,
    CentroidClassifier,
    CLARIFY,
    ExemplarClassifier,
    HybridGateClassifier,
    UNROUTABLE,
)
from exemplars import EXEMPLARS
from test_set import TEST_SET


def _build_classifier(args, cfg, embed_client):
    if args.mode == "nearest":
        return ExemplarClassifier(embed_client, threshold=args.threshold)
    if args.mode == "centroid":
        return CentroidClassifier(embed_client, threshold=args.threshold)
    if args.mode == "ask":
        inner = CentroidClassifier(embed_client, threshold=args.threshold)
        return AskWhenUncertainClassifier(inner, reject_threshold=args.threshold, margin=args.margin)
    if args.mode in ("hybrid", "hybrid-ask"):
        llm_client = LLMClient(cfg)
        if args.mode == "hybrid-ask":
            inner = AskWhenUncertainClassifier(
                CentroidClassifier(embed_client, threshold=args.threshold),
                reject_threshold=args.threshold, margin=args.margin,
            )
        else:
            inner = CentroidClassifier(embed_client, threshold=args.threshold)
        return HybridGateClassifier(llm_client, inner, gate_model_override=args.llm_model)
    raise ValueError(f"Unknown mode: {args.mode}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", "-c", default=None, help="Path to kriya.yaml")
    parser.add_argument("--threshold", type=float, default=0.3, help="Min cosine similarity to accept a match")
    parser.add_argument("--margin", type=float, default=0.05,
                         help="Min score gap between top-2 candidates before committing (else CLARIFY)")
    parser.add_argument("--mode", choices=["nearest", "centroid", "ask", "hybrid", "hybrid-ask"], default="hybrid-ask",
                         help="nearest/centroid = forced top-1 embeddings pick; ask = centroid + ask-when-uncertain; "
                              "hybrid = LLM gate + centroid; hybrid-ask = LLM gate + ask-when-uncertain (recommended)")
    parser.add_argument("--embed-model", default=None, help="Override the configured embedding model")
    parser.add_argument("--llm-model", default=None, help="Override the configured LLM model (hybrid modes' gate)")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-item lines, print only the summary")
    args = parser.parse_args()

    cfg = load_config(args.config)
    embed_model = args.embed_model or cfg.embedding.model
    embed_client = OllamaEmbeddingClient(base_url=cfg.embedding.base_url, model=embed_model)
    classifier = _build_classifier(args, cfg, embed_client)

    exemplar_count = sum(len(v) for v in EXEMPLARS.values())
    print(f"[mode={args.mode}] embed-model={embed_model} llm-model={args.llm_model or cfg.llm.model} "
          f"threshold={args.threshold} margin={args.margin}")
    print(f"Embedding {exemplar_count} exemplars across {len(EXEMPLARS)} commands...")
    await classifier.fit(EXEMPLARS)

    strict_correct = 0
    effective_correct = 0
    per_category_total = defaultdict(int)
    per_category_effective_correct = defaultdict(int)
    dangerous_misroutes = []   # unroutable input confidently routed to one actionable command
    soft_misses = []           # unroutable input offered a CLARIFY between two actionable commands
    bad_clarifies = []         # a real command's input got CLARIFY but the true label wasn't offered
    hard_misses = []           # anything else wrong

    print(f"\nClassifying {len(TEST_SET)} test inputs...\n")
    for case in TEST_SET:
        text, expected, category = case["text"], case["expected"], case["category"]
        result = await classifier.predict(text)
        label, score, candidates = result["label"], result["score"], result["candidates"]

        per_category_total[category] += 1
        is_strict_correct = label in expected and label != CLARIFY
        if is_strict_correct:
            strict_correct += 1

        is_effective_correct = False
        if is_strict_correct:
            is_effective_correct = True
        elif label == CLARIFY and candidates and any(c in expected for c in candidates):
            is_effective_correct = True
        elif label == UNROUTABLE and expected == [UNROUTABLE]:
            is_effective_correct = True

        if is_effective_correct:
            effective_correct += 1
            per_category_effective_correct[category] += 1
        else:
            if expected == [UNROUTABLE] and label not in (UNROUTABLE, CLARIFY):
                dangerous_misroutes.append((text, label, score))
            elif expected == [UNROUTABLE] and label == CLARIFY:
                soft_misses.append((text, candidates))
            elif label == CLARIFY:
                bad_clarifies.append((text, expected, candidates))
            else:
                hard_misses.append((text, expected, label, score))

        if not args.quiet:
            marker = "OK" if is_effective_correct else "XX"
            extra = f" candidates={candidates}" if candidates else ""
            print(f"[{marker}] expected={expected!s:<20} predicted={label:<10} score={score:.3f}{extra}  {text!r}")

    n = len(TEST_SET)
    strict_accuracy = strict_correct / n if n else 0.0
    effective_accuracy = effective_correct / n if n else 0.0

    print("\n" + "=" * 70)
    print(f"Strict accuracy (forced top-1 only):    {strict_correct}/{n} ({strict_accuracy:.1%})")
    print(f"Effective accuracy (CLARIFY counts if right choices offered): {effective_correct}/{n} ({effective_accuracy:.1%})")

    print("\nEffective accuracy by category:")
    for category in sorted(per_category_total):
        total = per_category_total[category]
        right = per_category_effective_correct[category]
        print(f"  {category:<20} {right}/{total} ({right / total:.1%})")

    if hard_misses:
        print("\nHard misses (wrong, non-clarify prediction):")
        for text, expected, label, score in hard_misses:
            print(f"  expected={expected} got={label} (score={score:.3f}): {text!r}")

    if bad_clarifies:
        print("\nBad clarifies (asked, but true answer wasn't among the offered candidates):")
        for text, expected, candidates in bad_clarifies:
            print(f"  expected={expected} offered={candidates}: {text!r}")

    print("\nSoft misses (unroutable input offered a CLARIFY between two real commands - annoying, not dangerous):")
    if soft_misses:
        for text, candidates in soft_misses:
            print(f"  offered={candidates}: {text!r}")
    else:
        print("  none")

    print("\nDangerous misroutes (unroutable input confidently routed to one actionable command):")
    if dangerous_misroutes:
        for text, label, score in dangerous_misroutes:
            print(f"  ROUTED TO {label} (score={score:.3f}): {text!r}")
    else:
        print("  none")

    print(f"\nGo/no-go bar: >=90% effective accuracy, zero dangerous misroutes.")
    verdict = "GO" if effective_accuracy >= 0.9 and not dangerous_misroutes else "NO-GO"
    print(f"Verdict: {verdict}")


if __name__ == "__main__":
    asyncio.run(main())
