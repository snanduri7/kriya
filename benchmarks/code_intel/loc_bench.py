"""loc-N: deterministic localization benchmark (no chat-model or embedding calls).

    python benchmarks/code_intel/loc_bench.py <repo> [--commits 4000] [--cases 200] [--synthetic 30]
        [--out results.json]

Case generation (cheap, automatic; nothing is hand-curated):
- mined: a non-merge commit whose non-test source change touches one or two
  members (callables or fields, via the tree-sitter structure of the
  commit's own file version and its zero-context diff hunks). Goal = the
  commit subject. Gold = those members, kept only when the same qualified
  key + parameter types still exist at HEAD (evaluation runs at HEAD).
- synthetic: a seeded sample of HEAD callables turned into a compiler error
  (``Path.java:[line,col] error: ...``), a Java stack frame or a Python
  traceback pointing inside the member body.

Categories: ``symbol`` (the subject names a gold member or its owner type),
``error_test`` (failure/test vocabulary, and every synthetic case),
``behavior`` (everything else).

Systems compared at HEAD, member level (hit = a gold member in the top k):
- ``code_intel``: CodeIntelligenceService.locate (deterministic channels).
- ``baseline_lexical``: the pre-R1 retrieval lexical leg - chunks from
  ``chunk_file_with_metadata_headers`` in a LocalVectorStore, ranked by
  ``query_hybrid`` with no query embedding (the vector leg needs a live
  embedding model and is not part of this benchmark), each chunk mapped to a
  member by its controlled ``Method:``/``Class:`` header.
- ``baseline_failure_line`` (synthetic cases): the pre-R1 failure-line path,
  ``resolve_member_hints_from_failure_location`` on the HEAD file.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from kriya.analyzer.analyzer import chunk_file_with_metadata_headers
from kriya.code_intel.model import FileStructure
from kriya.code_intel.parsing import language_for_path, parse_file
from kriya.code_intel.service import CodeIntelligenceService, discover_source_files

_MEMBER_KINDS = ("method", "constructor", "function", "field", "attribute", "enum_constant")
_ERROR_WORDS = re.compile(r"(?i)\b(npe|null ?pointer\w*|exception\w*|errors?|fail\w*|crash\w*|bugs?|overflow\w*"
                          r"|throws?|tests?|broken|incorrect\w*|wrong)\b")
_TRIVIAL = re.compile(r"(?i)^(javadoc|typo|format|checkstyle|sort members|use final|whitespace|spelling|"
                      r"better (param|local|variable)|inline|reuse|refactor|camel-case|normalize)\b")


def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, errors="replace").stdout


def is_test_path(path: str) -> bool:
    return bool(re.search(r"(^|/)(src/test|tests?)/|(^|/)test_[^/]*\.py$|Test\.java$|Tests\.java$", path))


def _hunk_lines(diff: str) -> Dict[str, List[int]]:
    """path -> new-side changed line numbers (deletion-only hunks give the
    line after which content was removed)."""
    lines: Dict[str, List[int]] = defaultdict(list)
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and path:
            match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if match:
                start, count = int(match.group(1)), int(match.group(2) or 1)
                lines[path].extend(range(start, start + max(count, 1)))
    return lines


def _members_at(structure: FileStructure, line_numbers: List[int]) -> List[Tuple[str, Tuple[str, ...]]]:
    found = []
    for line in line_numbers:
        best = None
        for symbol in structure.symbols:
            if symbol.kind in _MEMBER_KINDS and symbol.declaration.contains_line(line):
                if best is None or symbol.declaration.line_count < best.declaration.line_count:
                    best = symbol
        if best is None:
            return []  # a change outside every member (imports, comments, class header): not a member case
        key = (best.lookup_key, best.parameter_types)
        if key not in found:
            found.append(key)
    return found


def _category(subject: str, gold_names: List[str]) -> str:
    words = set(re.findall(r"[A-Za-z_][\w$]*", subject))
    if any(name in words for name in gold_names):
        return "symbol"
    if _ERROR_WORDS.search(subject):
        return "error_test"
    return "behavior"


def mine(repo: str, head: Dict[str, FileStructure], max_commits: int, max_cases: int) -> List[dict]:
    head_index: Dict[Tuple[str, Tuple[str, ...]], List[str]] = defaultdict(list)
    for structure in head.values():
        for symbol in structure.symbols:
            head_index[(symbol.lookup_key, symbol.parameter_types)].append(symbol.symbol_id)
    cases: List[dict] = []
    unlocatable: List[str] = []
    log = git(repo, "log", "--no-merges", f"-n{max_commits}", "--format=%H%x1f%s")
    for entry in log.splitlines():
        commit, _, subject = entry.partition("\x1f")
        subject = re.sub(r"\s*\(#\d+\)\s*$", "", subject).strip()
        if len(subject.split()) < 3 or _TRIVIAL.search(subject):
            continue
        status = git(repo, "diff-tree", "--no-commit-id", "-r", "--name-status", commit)
        changed = [line.split("\t") for line in status.splitlines() if line]
        sources = [parts[-1] for parts in changed if language_for_path(parts[-1]) and not is_test_path(parts[-1])]
        if not 1 <= len(sources) <= 2 or any(parts[0] != "M" for parts in changed if parts[-1] in sources):
            continue
        hunks = _hunk_lines(git(repo, "show", "--format=", "--unified=0", commit, "--", *sources))
        gold: List[Tuple[str, Tuple[str, ...]]] = []
        for path in sources:
            if not hunks.get(path):
                gold = []
                break
            version = git(repo, "show", f"{commit}:{path}").encode()
            members = _members_at(parse_file(path, version), hunks[path])
            if not members:
                gold = []
                break
            gold.extend(members)
        if not 1 <= len(gold) <= 2:
            continue
        gold_ids = [i for key in gold for i in head_index.get(key, [])]
        if len(gold_ids) < len(gold) or any(not head_index.get(key) for key in gold):
            continue
        if not _locatable(subject, [git(repo, "show", f"HEAD:{p}") for p in sources]):
            unlocatable.append(commit[:12])
            continue
        names = sorted({key[0].rsplit(".", 1)[-1] for key in gold} | {key[0].rsplit(".", 2)[-2] for key in gold
                                                                       if key[0].count(".") >= 1})
        cases.append({"id": commit[:12], "kind": "mined", "goal": subject, "category": _category(subject, names),
                      "gold_ids": gold_ids, "gold_keys": sorted({k[0] for k in gold})})
        if len(cases) >= max_cases:
            break
    mine.unlocatable = unlocatable
    return cases


_SUBJECT_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]{3,}")
_GENERIC = frozenset("""
this that with from into when then than more less some only also use uses used using make makes better simpler
remove removes removed reduce merge merges single return returns inline extra code value values string strings
method methods class classes field fields variable variables local private public internal type types call calls
statement statements whitespace vertical literal literals format formatting javadoc comment comments test tests
""".split())


def _locatable(subject: str, gold_files: List[str]) -> bool:
    """System-neutral filter: the subject shares at least one non-generic word
    (4+ letters) with an identifier of a gold file. A cleanup subject such as
    "Reduce vertical whitespace" carries no localization signal for any
    system and is counted, not scored."""
    vocabulary = {t for text in gold_files for t in re.findall(r"[a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2",
                                                                                      text).lower())}
    words = {w.lower() for w in _SUBJECT_WORD.findall(subject)} - _GENERIC
    return bool(words & vocabulary)


def synthesize(head: Dict[str, FileStructure], count: int, seed: int = 7) -> List[dict]:
    rng = random.Random(seed)
    candidates = [s for st in head.values() for s in st.symbols
                  if s.kind in ("method", "constructor", "function") and s.body is not None
                  and s.body.end_line - s.body.start_line >= 2 and not is_test_path(s.path)]
    rng.shuffle(candidates)
    cases = []
    for index, symbol in enumerate(candidates[:count]):
        line = rng.randint(symbol.body.start_line + 1, symbol.body.end_line - 1)
        if symbol.language == "python":
            goal = (f"Traceback (most recent call last):\n  File \"{symbol.path}\", line {line}, in {symbol.name}\n"
                    "TypeError: unsupported operand type(s)")
        elif index % 2 == 0:
            goal = f"[ERROR] {symbol.path}:[{line},17] error: incompatible types: int cannot be converted to String"
        else:
            owner = symbol.lookup_key.rsplit(".", 1)[0]
            goal = (f"java.lang.IllegalStateException: boom\n\tat {owner}.{symbol.name}"
                    f"({os.path.basename(symbol.path)}:{line})\n\tat java.base/java.lang.Thread.run(Thread.java:1583)")
        cases.append({"id": f"synthetic-{index}", "kind": "synthetic", "goal": goal, "category": "error_test",
                      "gold_ids": [symbol.symbol_id], "gold_keys": [symbol.lookup_key],
                      "failure": {"path": symbol.path, "line": line}})
    return cases


def evaluate_code_intel(service: CodeIntelligenceService, cases: List[dict]) -> List[dict]:
    results = []
    for case in cases:
        start = time.perf_counter()
        hits = service.locate(case["goal"], limit=10)
        elapsed = time.perf_counter() - start
        ids = [h.symbol_id for h in hits]
        keys = [h.lookup_key for h in hits]
        gold = set(case["gold_ids"])
        results.append({
            "id": case["id"], "rank": next((i + 1 for i, s in enumerate(ids) if s in gold), None),
            "key_rank": next((i + 1 for i, k in enumerate(keys) if k in case["gold_keys"]), None),
            "top_exact": bool(hits and hits[0].exact), "seconds": elapsed,
            "top": [(h.lookup_key, h.score, [c for c, _ in h.channels]) for h in hits[:3]],
        })
    return results


def evaluate_baseline_lexical(repo: str, files: List[str], cases: List[dict]) -> List[dict]:
    from kriya.memory.embedding import (
        PREPROCESSING_VERSION,
        SEGMENTATION_VERSION,
        EmbeddedSegment,
        EmbeddingFingerprint,
    )
    from kriya.memory.vector import LocalVectorStore
    from kriya.workflow.context_source import parse_controlled_chunk_header_name

    fingerprint = EmbeddingFingerprint("bench", "bench", "bench/1", 1, 2048, "none", PREPROCESSING_VERSION,
                                       SEGMENTATION_VERSION)
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalVectorStore(os.path.join(tmp, "v.db"))
        store.reset_index(fingerprint)
        for path in files:
            with open(os.path.join(repo, path), encoding="utf-8", errors="replace") as handle:
                content = handle.read()
            chunks = [c for c in chunk_file_with_metadata_headers(content, path) if c["text"].strip()]
            store.publish_file(path, [EmbeddedSegment(i, 0, c["start"], c["end"], c["text"], [1.0])
                                      for i, c in enumerate(chunks)], source_digest="bench",
                               fingerprint=fingerprint.digest)
        results = []
        for case in cases:
            start = time.perf_counter()
            hits = store.query_hybrid(case["goal"], None, top_k=10, fingerprint=fingerprint.digest)[:10]
            elapsed = time.perf_counter() - start
            gold = {(k.rsplit(".", 1)[-1]) for k in case["gold_keys"]}
            gold_files = {i.split(":", 1)[1].split("#", 1)[0] for i in case["gold_ids"]}
            member_rank = file_rank = None
            for rank, hit in enumerate(hits, 1):
                name = parse_controlled_chunk_header_name(hit["text"])
                if file_rank is None and hit["filepath"] in gold_files:
                    file_rank = rank
                if member_rank is None and hit["filepath"] in gold_files and name in gold:
                    member_rank = rank
            results.append({"id": case["id"], "rank": member_rank, "file_rank": file_rank, "seconds": elapsed})
        store.close()
    return results


def evaluate_baseline_failure_line(repo: str, cases: List[dict]) -> List[dict]:
    from kriya.workflow.context_source import resolve_member_hints_from_failure_location

    results = []
    for case in cases:
        failure = case.get("failure")
        if not failure:
            continue
        with open(os.path.join(repo, failure["path"]), encoding="utf-8", errors="replace") as handle:
            content = handle.read()
        hints = resolve_member_hints_from_failure_location(failure["path"], content, failure["line"])
        gold = {k.split(".")[-2] + "." + k.split(".")[-1] if k.count(".") else k for k in case["gold_keys"]}
        gold_simple = {k.rsplit(".", 1)[-1] for k in case["gold_keys"]}
        hit = any(h.member_id in gold or h.member_id.rsplit(".", 1)[-1] in gold_simple for h in hints)
        results.append({"id": case["id"], "rank": 1 if hit else None})
    return results


def summarize(cases: List[dict], results: List[dict], label: str) -> dict:
    by_id = {r["id"]: r for r in results}
    out: Dict[str, dict] = {}
    groups = defaultdict(list)
    for case in cases:
        if case["id"] in by_id:
            groups["all"].append(by_id[case["id"]])
            groups[case["category"]].append(by_id[case["id"]])
            groups[case["kind"]].append(by_id[case["id"]])
    for group, rows in groups.items():
        n = len(rows)
        entry = {
            "n": n,
            "recall@1": round(sum(1 for r in rows if r["rank"] == 1) / n, 3),
            "recall@5": round(sum(1 for r in rows if r["rank"] and r["rank"] <= 5) / n, 3),
        }
        if "file_rank" in rows[0]:
            entry["file_recall@5"] = round(sum(1 for r in rows if r["file_rank"] and r["file_rank"] <= 5) / n, 3)
        if "key_rank" in rows[0]:
            entry["member_name_recall@5"] = round(sum(1 for r in rows if r["key_rank"] and r["key_rank"] <= 5) / n, 3)
            entry["false_confident@1"] = round(sum(1 for r in rows if r["top_exact"] and r["rank"] != 1) / n, 3)
        if "seconds" in rows[0]:
            seconds = sorted(r["seconds"] for r in rows)
            entry["latency_p50_ms"] = round(1000 * statistics.median(seconds), 1)
            entry["latency_p95_ms"] = round(1000 * seconds[min(n - 1, int(0.95 * n))], 1)
        out[group] = entry
    return {label: out}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("--commits", type=int, default=4000)
    parser.add_argument("--cases", type=int, default=200)
    parser.add_argument("--synthetic", type=int, default=30)
    parser.add_argument("--out")
    parser.add_argument("--no-baseline", action="store_true")
    args = parser.parse_args(argv)
    repo = os.path.abspath(args.repo)
    files = discover_source_files(repo)
    head = {}
    for path in files:
        with open(os.path.join(repo, path), "rb") as handle:
            head[path] = parse_file(path, handle.read())
    cases = mine(repo, head, args.commits, args.cases) + synthesize(head, args.synthetic)
    report = {"repo": repo, "head": git(repo, "rev-parse", "HEAD").strip(), "cases": len(cases),
              "mined_unlocatable_skipped": len(getattr(mine, "unlocatable", [])),
              "categories": {c: sum(1 for x in cases if x["category"] == c) for c in ("symbol", "behavior",
                                                                                        "error_test")}}
    with tempfile.TemporaryDirectory() as tmp:
        service = CodeIntelligenceService(repo, os.path.join(tmp, "ci.db"))
        start = time.perf_counter()
        service.refresh(files)
        report["refresh_seconds"] = round(time.perf_counter() - start, 2)
        ci = evaluate_code_intel(service, cases)
        service.close()
    report.update(summarize(cases, ci, "code_intel"))
    if not args.no_baseline:
        report.update(summarize(cases, evaluate_baseline_lexical(repo, files, cases), "baseline_lexical"))
        report.update(summarize(cases, evaluate_baseline_failure_line(repo, cases), "baseline_failure_line"))
    print(json.dumps({k: v for k, v in report.items()}, indent=1))
    if args.out:
        with open(args.out, "w") as handle:
            json.dump({"report": report, "cases": cases, "code_intel": ci}, handle, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
