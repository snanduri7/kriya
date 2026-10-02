"""Structural coverage: the tree-sitter model vs the pre-R1 regex extractors.

Usage: python benchmarks/code_intel/coverage.py <repo> [<repo> ...]
Ground truth is the tree-sitter structure of each file (types, methods +
constructors, Python functions incl. async). Reports how many of those the
dependency-graph regex parser (graph.py) and java_members recover, and the
tree-sitter parse states and time. No model calls.
"""
import os
import subprocess
import sys
import time
from collections import Counter

from kriya.analyzer.graph import DependencyGraph
from kriya.analyzer.java_members import extract_java_members
from kriya.code_intel.model import TYPE_KINDS
from kriya.code_intel.parsing import language_for_path, parse_file


def tracked(repo):
    out = subprocess.run(["git", "-C", repo, "ls-files"], capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if language_for_path(p)]


def main(repos):
    graph = DependencyGraph(":memory:")
    for repo in repos:
        states, c = Counter(), Counter()
        parse_seconds = 0.0
        for rel in tracked(repo):
            data = open(os.path.join(repo, rel), "rb").read()
            t0 = time.perf_counter()
            fs = parse_file(rel, data)
            parse_seconds += time.perf_counter() - t0
            states[fs.state.value] += 1
            text = data.decode("utf-8", "replace")
            if fs.language == "java":
                types = {s.lookup_key for s in fs.symbols if s.is_type}
                calls = [s for s in fs.symbols if s.kind in ("method", "constructor")]
                try:
                    gsyms, _ = graph._parse_java(rel, text)
                except Exception:
                    gsyms = []
                gtypes = {s["name"] for s in gsyms if s["type"].replace("nested_", "") in TYPE_KINDS}
                gmethod_lines = {s["start_line"] for s in gsyms if s["type"] in ("method", "constructor")}
                c["java_types"] += len(types)
                c["java_types_graph"] += len(types & gtypes)
                c["java_callables"] += len(calls)
                c["java_callables_graph"] += sum(1 for s in calls if s.signature.start_line in gmethod_lines
                                                 or s.declaration.start_line in gmethod_lines)
                primary = [s for s in fs.symbols if s.is_type and s.parent_id is None][:1]
                if primary:
                    direct = [s for s in calls if s.parent_id == primary[0].symbol_id]
                    jm = {(m.name, m.start_line) for m in extract_java_members(text)}
                    c["java_primary_callables"] += len(direct)
                    c["java_primary_callables_java_members"] += sum(
                        1 for s in direct if (s.name, s.signature.start_line) in jm)
            elif fs.language == "python":
                funcs = [s for s in fs.symbols if s.kind in ("function", "method")]
                try:
                    gsyms, _ = graph._parse_python(rel, text)
                except Exception:
                    gsyms = []
                gstarts = {s["start_line"] for s in gsyms if s["type"] == "function"}
                c["py_functions"] += len(funcs)
                c["py_async"] += sum(1 for s in funcs if "async" in s.modifiers)
                c["py_functions_graph"] += sum(1 for s in funcs if s.signature.start_line in gstarts)
        print(f"== {repo}")
        print("  parse states:", dict(states), f"parse time {parse_seconds:.2f}s")
        for key in sorted(c):
            print(f"  {key}: {c[key]}")
        for a, b in (("java_types_graph", "java_types"), ("java_callables_graph", "java_callables"),
                     ("java_primary_callables_java_members", "java_primary_callables"),
                     ("py_functions_graph", "py_functions")):
            if c[b]:
                print(f"  {a}/{b} = {100 * c[a] / c[b]:.1f}%")


if __name__ == "__main__":
    main(sys.argv[1:])
