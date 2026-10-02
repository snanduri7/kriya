"""E-06: vector query cost at 10k/30k/45k chunks (dimension 768, nomic-embed-text's).

    python benchmarks/code_intel/vector_bench.py [--sizes 10000 30000 45000] [--queries 5] [--out results.json]

Builds a LocalVectorStore of N current rows (random unit vectors, realistic
chunk-sized text) under one fingerprint and times LocalVectorStore.query()
exactly as retrieval calls it (fingerprint-filtered). Reports first-query
and steady-state latency and peak traced Python memory (tracemalloc) plus
the RSS high-water delta. No model calls.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import sys
import tempfile
import time
import tracemalloc

import numpy as np

from kriya.memory.embedding import PREPROCESSING_VERSION, SEGMENTATION_VERSION, EmbeddedSegment, EmbeddingFingerprint
from kriya.memory.vector import LocalVectorStore

DIM = 768
TEXT = ("Method: computeSomething\n" + "    int value = other.compute(input, offset) + this.base;\n" * 18)


def build(path: str, rows: int, fingerprint: EmbeddingFingerprint) -> None:
    store = LocalVectorStore(path)
    store.reset_index(fingerprint)
    rng = np.random.default_rng(1)
    per_file = 20
    for start in range(0, rows, per_file):
        vectors = rng.standard_normal((min(per_file, rows - start), DIM)).astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        segments = [EmbeddedSegment(i, 0, i * 20 + 1, i * 20 + 19, TEXT, v.tolist()) for i, v in enumerate(vectors)]
        store.publish_file(f"src/F{start // per_file}.java", segments, source_digest="x", fingerprint=fingerprint.digest)
    store.close()


def measure(path: str, fingerprint: EmbeddingFingerprint, queries: int) -> dict:
    rng = np.random.default_rng(2)
    store = LocalVectorStore(path)
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    tracemalloc.start()
    seconds = []
    for _ in range(queries):
        q = rng.standard_normal(DIM).astype(np.float32).tolist()
        start = time.perf_counter()
        hits = store.query(q, top_k=20, fingerprint=fingerprint.digest)
        seconds.append(time.perf_counter() - start)
        assert len(hits) == 20
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    store.close()
    scale = 1 if sys.platform == "darwin" else 1024  # ru_maxrss: bytes on macOS, KiB on Linux
    return {"first_query_ms": round(1000 * seconds[0], 1),
            "steady_median_ms": round(1000 * statistics.median(seconds[1:] or seconds), 1),
            "traced_peak_mb": round(peak / 2 ** 20, 1),
            "rss_highwater_delta_mb": round((rss_after - rss_before) * scale / 2 ** 20, 1)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=int, nargs="+", default=[10000, 30000, 45000])
    parser.add_argument("--queries", type=int, default=5)
    parser.add_argument("--out")
    args = parser.parse_args()
    fingerprint = EmbeddingFingerprint("bench", "bench", "bench/1", DIM, 2048, "none", PREPROCESSING_VERSION,
                                       SEGMENTATION_VERSION)
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        for size in args.sizes:
            path = os.path.join(tmp, f"v{size}.db")
            build(path, size, fingerprint)
            results[size] = {"db_mb": round(os.path.getsize(path) / 2 ** 20, 1),
                             **measure(path, fingerprint, args.queries)}
            print(size, results[size], flush=True)
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(results, handle, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
