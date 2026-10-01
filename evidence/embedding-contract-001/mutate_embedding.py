"""EMBEDDING-CONTRACT-001 mutations: each must make a test fail. Usage: python mutate_embedding.py <repo root>"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
TESTS = ["tests/test_embedding_contract_001.py", "tests/test_indexing.py", "tests/test_knowledge_readpath_001.py",
         "tests/test_learn_embedding_failure_warning.py", "tests/test_routing.py"]
E, V, A = "kriya/memory/embedding.py", "kriya/memory/vector.py", "kriya/analyzer/analyzer.py"
G = "kriya/workflow/graph_retrieval.py"
START = sys.argv[2] if len(sys.argv) > 2 else None
M = [
 # E1
 ("zero-vector-substitution-restored", E, "                raise EmbeddingUnavailableError(f\"{type(error).__name__}: {error}\") from error\n",
  "                return MagicResponse()\n"),
 ("no-count-check", E, "    if not isinstance(vectors, list) or len(vectors) != expected:", "    if not isinstance(vectors, list):"),
 ("no-dimension-check", E, "        if len(vector) != dimension:", "        if False:"),
 ("no-finite-check", E, "        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector):",
  "        if not all(isinstance(v, (int, float)) for v in vector):"),
 ("no-norm-check", E, "        if math.fsum(float(v) * float(v) for v in vector) <= 0.0:", "        if False:"),
 ("failed-file-keeps-current-vectors", V, "            self.conn.execute(\"UPDATE vector_chunks SET is_current = 0 WHERE filepath = ?\", (filepath,))\n", ""),
 ("failed-file-keeps-lexical-text", V, "            self.conn.execute(f\"DELETE FROM {lexical} WHERE filepath = ?\", (filepath,))\n\n    def load",
  "            pass\n\n    def load"),
 ("failed-file-cached", A, "                    if rel_path in store.file_metadata:\n                        del store.file_metadata[rel_path]\n",
  "                    store.file_metadata[rel_path] = {\"mtime\": mtime, \"hash\": file_hash}\n"),
 ("query-ignores-current-flag", V, "                \" WHERE is_current = 1 AND fingerprint = ?\", (fingerprint,))", "                \" WHERE fingerprint = ?\", (fingerprint,))"),
 # E2
 ("truncate-flag-removed", E, "{\"model\": self.model, \"input\": batch, \"truncate\": False}", "{\"model\": self.model, \"input\": batch}"),
 ("refusal-not-typed", E, "                if response.status_code == 400 and \"context length\" in response.text:", "                if False:"),
 ("no-segmentation", E, "            halves = _halves(piece) if depth < MAX_SEGMENT_DEPTH else None", "            halves = None"),
 ("unbounded-segmentation", E, "            halves = _halves(piece) if depth < MAX_SEGMENT_DEPTH else None", "            halves = _halves(piece) or (piece, piece)"),
 ("no-admission-miss-record", E, "                client.admission_misses += 1\n", ""),
 ("segment-span-guessed", E, "            out.extend(EmbeddedSegment(base + offset, index, start, end, text, vector)",
  "            out.extend(EmbeddedSegment(base + offset, index, start + index, end, text, vector)"),
 # E3
 ("fingerprint-name-plus-dimension-only", E, "        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()",
  "        return hashlib.sha256(f\"{self.model}:{self.dimension}\".encode()).hexdigest()"),
 ("query-ignores-row-identity", V, "                \" WHERE is_current = 1 AND fingerprint = ?\", (fingerprint,))", "                \" WHERE is_current = 1\")"),
 ("identity-change-silently-mixed", A, "            elif active != fingerprint.digest:", "            elif False:"),
 ("retrieval-ignores-identity", G, "    if vector_store.active_fingerprint() != fingerprint:", "    if False:"),
 # E4
 ("no-retry-on-transient", E, "                if attempt == 1:\n                    logger.warning(\"Embedding request failed",
  "                if False:\n                    logger.warning(\"Embedding request failed"),
 ("retry-everything", E, "            if response.status_code >= 500 and attempt == 1:", "            if attempt == 1:"),
 ("deadline-ignored", E, "                if timeout <= 0:\n                    raise _deadline_error(INFERENCE_DEADLINE_EXHAUSTED, deadline)\n", ""),
 ("trust-env-on", E, "        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:\n            for start",
  "        async with httpx.AsyncClient(timeout=self.timeout, trust_env=True) as client:\n            for start"),
 # degradation
 ("query-failure-not-recorded", G, "    except EmbeddingError as error:\n        result.semantic_unavailable = error.reason_code\n        logger.warning(\"Semantic retrieval unavailable (%s); lexical retrieval only.\", error)\n        return None, fingerprint",
  "    except EmbeddingError as error:\n        logger.warning(\"Semantic retrieval unavailable (%s); lexical retrieval only.\", error)\n        return None, fingerprint"),
]
if START:
    M = M[[m[0] for m in M].index(START):]
surv = 0
for label, rel, old, new in M:
    p = ROOT / rel
    t = p.read_text()
    if t.count(old) != 1:
        print(label, "ANCHOR", t.count(old))
        surv += 1
        continue
    p.write_text(t.replace(old, new))
    try:
        r = subprocess.run([str(ROOT / ".venv/bin/pytest"), "-q", "-x", "-p", "no:randomly", *TESTS], cwd=ROOT,
                           capture_output=True, text=True, timeout=300)
    finally:
        p.write_text(t)
    k = r.returncode != 0
    surv += not k
    fail = [line for line in r.stdout.splitlines() if line.startswith("FAILED")][:1]
    print(label, "KILLED" if k else "SURVIVED", fail[0][7:115] if fail else "", flush=True)
print(f"{len(M)-surv}/{len(M)} killed")
