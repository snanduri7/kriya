"""CTX-001 final assurance replay (2026-09-18).

Runs the deterministic scenarios that remained after the C1-C6/S1-S4 test
suites built across Package 2 / C2 / WP9 / WP8, specifically the ONE gap
those suites didn't fully close: proving C2's member_exact retention is
reached through the REAL, AUTOMATIC production chain (a real vector chunk
header, parsed by the real parser, validated against real current source)
with NO manually-typed member name anywhere - plus a fresh S1 query-plan
before/after and a structural (not wall-clock) cold-indexing comparison.

No production code is modified. No live model/embedding calls anywhere -
run_attempt() is driven with an AsyncMock developer (the same pattern
tests/test_workflow.py already uses throughout), and the SQL/query-plan
evidence is pure sqlite3, no network of any kind.

Every OTHER C1/C3/C4/C5/C6/S2/S3 scenario is already covered by real,
passing, deterministic tests in:
  tests/test_context_budget.py    (C1, C5, C6, S3 - allocator level)
  tests/test_context_source.py    (C3, C4-adjacent member/resolver level)
  tests/test_workflow.py          (C1-C6, S3 - real run_attempt() level)
  tests/test_dependency_graph.py  (C4, S1 - graph/SQL level)
  tests/test_analyzer_cache.py    (S2 - analyze() reuse level)
This script does not re-implement those - see CTX_001_FINAL_ASSURANCE.md
for how each is cited as evidence.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402


def _load_test_workflow_module():
    """Reuses tests/test_workflow.py's own _minimal_attempt_ctx helper and
    already-imported run_attempt/GenerationState - the exact same
    production entry points every C1-C6 production-reachability test in
    this session's own test suite already goes through. Not a new harness;
    a direct reuse of the existing one."""
    path = os.path.join(REPO_ROOT, "tests", "test_workflow.py")
    spec = importlib.util.spec_from_file_location("test_workflow_replay", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# C2 (final): fully automatic member_exact reachability via a REAL chunk
# header, REAL parser, REAL current-source validation - zero manually
# typed member name anywhere in this scenario.
# ---------------------------------------------------------------------------

async def replay_c2_automatic():
    import asyncio
    from unittest.mock import AsyncMock

    from kriya.analyzer.analyzer import chunk_file_with_metadata_headers
    from kriya.workflow.context_source import parse_controlled_chunk_header_name

    tw = _load_test_workflow_module()

    with tempfile.TemporaryDirectory() as tmp:
        content = fixtures.build_large_file(target_lines=2500, placement="near_end")
        with open(os.path.join(tmp, "Large.py"), "w", encoding="utf-8") as fh:
            fh.write(content)

        # Step 1: the REAL chunker (production code, unmodified) produces
        # the chunk vector indexing would have embedded for the relevant
        # method - no live embedding call, we only need its own controlled
        # TEXT header, exactly what a real query_hybrid() hit would carry.
        chunks = chunk_file_with_metadata_headers(content, "Large.py")
        method_chunks = [c for c in chunks if "Method: calculate_total\n" in c["text"]]
        assert len(method_chunks) == 1, "expected exactly one real chunk for the relevant method"
        chunk_text = method_chunks[0]["text"]

        # Step 2: the REAL parser (production code, unmodified) - the exact
        # function workflow.py's own retrieval stage calls on every vector
        # hit's own chunk text.
        parsed_name = parse_controlled_chunk_header_name(chunk_text)
        assert parsed_name == "calculate_total", f"parser returned {parsed_name!r}"

        # Step 3: exactly what workflow.py's retrieval loop does with that
        # parsed name - populate retrieval_member_hints[filepath].append(name).
        # This is the ONE necessary simplification (a real query_hybrid()
        # call needs a live embedding endpoint, forbidden here) - everything
        # AFTER this point is the real, unmodified production chain.
        retrieval_member_hints = {"Large.py": [parsed_name]}

        state = tw.GenerationState()
        state.attempt_number = 0
        state.all_files_written = set()

        developer = AsyncMock()
        developer.run_generation = AsyncMock(return_value=[])
        ctx = tw._minimal_attempt_ctx(
            tmp, developer=developer,
            architect_files=["Large.py"], expected_files_upfront=["Large.py"],
            architect_basename_to_path={"Large.py": "Large.py"},
            retrieval_member_hints=retrieval_member_hints,
        )

        try:
            await tw.run_attempt(state, ctx)
        except tw.IncompleteGenerationError:
            pass  # expected - the mocked developer returns no files

        call_kwargs = developer.run_generation.call_args.kwargs
        existing_context = call_kwargs["existing_code_context"]
        events = [e for e in state.run_events if e.kind == "context.known_target_package"]

        return {
            "parsed_member_name_from_real_chunk": parsed_name,
            "member_hint_paths_from_real_event": events[-1].details["member_hint_paths"] if events else None,
            "member_exact_tier_reached": any(
                entry["tier"] == "member_exact" for entry in events[-1].details["tiers"]
            ) if events else False,
            "relevant_body_present_in_prompt": "subtotal * 0.05" in existing_context,
            "padding_method_body_count_in_source": content.count("total += i *"),
            "padding_method_body_count_in_prompt": existing_context.count("total += i *"),
        }


# ---------------------------------------------------------------------------
# S1 (final): fresh EXPLAIN QUERY PLAN before/after evidence, reproducing
# P0's own root-cause finding against the CURRENT (post-WP1) schema.
# ---------------------------------------------------------------------------

DELETE_SQL = """
DELETE FROM relations
WHERE source_file = ?
   OR (
        source_file IS NULL
        AND (
            source IN (SELECT name FROM symbols WHERE filepath = ?)
            OR target IN (SELECT name FROM symbols WHERE filepath = ?)
        )
      )
"""


def replay_s1_query_plan():
    from kriya.analyzer.graph import DependencyGraph

    with tempfile.TemporaryDirectory() as tmp:
        # BEFORE: reproduce the pre-WP1 schema shape directly (source_file
        # column present, no index on it) - the exact state P0 found live.
        db_before = os.path.join(tmp, "before.db")
        conn = sqlite3.connect(db_before)
        c = conn.cursor()
        c.execute("CREATE TABLE files (filepath TEXT PRIMARY KEY, mtime REAL, hash TEXT)")
        c.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY AUTOINCREMENT, filepath TEXT, name TEXT, type TEXT, start_line INTEGER, end_line INTEGER)")
        c.execute("CREATE TABLE relations (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, target TEXT, type TEXT, source_file TEXT)")
        c.execute("CREATE INDEX idx_symbols_filepath ON symbols(filepath)")
        c.execute("CREATE INDEX idx_symbols_name ON symbols(name)")
        c.execute("CREATE INDEX idx_relations_source ON relations(source)")
        c.execute("CREATE INDEX idx_relations_target ON relations(target)")
        for i in range(200):
            c.execute(
                "INSERT INTO relations (source, target, type, source_file) VALUES (?,?,?,?)",
                (f"s{i}", f"t{i}", "calls", f"File{i}.java"),
            )
        conn.commit()
        plan_before = c.execute(
            "EXPLAIN QUERY PLAN " + DELETE_SQL, ("File0.java", "File0.java", "File0.java"),
        ).fetchall()
        conn.close()

        # AFTER: the REAL, current, unmodified DependencyGraph - production
        # code, verifying WP1's index is still present and still used.
        db_after = os.path.join(tmp, "after.db")
        graph = DependencyGraph(db_after)
        for i in range(200):
            graph.index_file(f"File{i}.java", f"public class File{i} {{ void handle(){{}} }}\n", float(i))
        cur = graph.conn.cursor()
        plan_after = cur.execute(
            "EXPLAIN QUERY PLAN " + DELETE_SQL, ("File0.java", "File0.java", "File0.java"),
        ).fetchall()
        indexes = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='relations'",
        ).fetchall()
        graph.close()

        plan_before_str = " | ".join(str(r) for r in plan_before)
        plan_after_str = " | ".join(str(r) for r in plan_after)

        return {
            "plan_before": plan_before_str,
            "plan_after": plan_after_str,
            "full_scan_before": "SCAN relations" in plan_before_str,
            "full_scan_after": "SCAN relations" in plan_after_str,
            "index_used_after": "idx_relations_source_file" in plan_after_str,
            "indexes_present": [row[0] for row in indexes],
        }


# ---------------------------------------------------------------------------
# Cold indexing (final): structural comparison, not wall-clock - counts the
# number of SQLite "full table scan" query-plan steps clear_file() would
# perform across N sequential file indexes, before vs. after WP1's index.
# ---------------------------------------------------------------------------

def replay_cold_indexing_structural(n_files: int = 500):
    from kriya.analyzer.graph import DependencyGraph

    with tempfile.TemporaryDirectory() as tmp:
        # BEFORE: same pre-WP1 schema shape as replay_s1_query_plan, scaled
        # up to make the O(N^2) structural contributor concrete: N clear_file
        # calls, each a "SCAN relations" over a table that grows by ~1 row
        # per prior file - so total scanned-row-work grows O(N^2), not O(N).
        db_before = os.path.join(tmp, "before.db")
        conn = sqlite3.connect(db_before)
        c = conn.cursor()
        c.execute("CREATE TABLE files (filepath TEXT PRIMARY KEY, mtime REAL, hash TEXT)")
        c.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY AUTOINCREMENT, filepath TEXT, name TEXT, type TEXT, start_line INTEGER, end_line INTEGER)")
        c.execute("CREATE TABLE relations (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, target TEXT, type TEXT, source_file TEXT)")
        c.execute("CREATE INDEX idx_relations_source ON relations(source)")
        c.execute("CREATE INDEX idx_relations_target ON relations(target)")
        conn.commit()

        full_scan_steps_before = 0
        for i in range(n_files):
            plan = c.execute(
                "EXPLAIN QUERY PLAN " + DELETE_SQL, (f"File{i}.java", f"File{i}.java", f"File{i}.java"),
            ).fetchall()
            plan_str = " | ".join(str(r) for r in plan)
            if "SCAN relations" in plan_str:
                full_scan_steps_before += 1
            c.execute(
                "INSERT INTO relations (source, target, type, source_file) VALUES (?,?,?,?)",
                (f"s{i}", f"t{i}", "calls", f"File{i}.java"),
            )
            conn.commit()
        conn.close()

        # AFTER: the real, current DependencyGraph - each index_file() call
        # internally runs clear_file() first, exactly as production does.
        db_after = os.path.join(tmp, "after.db")
        graph = DependencyGraph(db_after)
        full_scan_steps_after = 0
        for i in range(n_files):
            cur = graph.conn.cursor()
            plan = cur.execute(
                "EXPLAIN QUERY PLAN " + DELETE_SQL, (f"File{i}.java", f"File{i}.java", f"File{i}.java"),
            ).fetchall()
            plan_str = " | ".join(str(r) for r in plan)
            if "SCAN relations" in plan_str:
                full_scan_steps_after += 1
            graph.index_file(f"File{i}.java", f"public class File{i} {{ void handle(){{}} }}\n", float(i))
        graph.close()

        return {
            "n_files": n_files,
            "full_table_scan_delete_calls_before": full_scan_steps_before,
            "full_table_scan_delete_calls_after": full_scan_steps_after,
        }


async def main():
    print("=== C2 (automatic, real chunk header, zero manual member_hints) ===")
    c2 = await replay_c2_automatic()
    for k, v in c2.items():
        print(f"  {k}: {v}")

    print("\n=== S1 (SQL query plan, before vs. after WP1) ===")
    s1 = replay_s1_query_plan()
    for k, v in s1.items():
        print(f"  {k}: {v}")

    print("\n=== Cold indexing (structural full-scan-call count, before vs. after WP1) ===")
    cold = replay_cold_indexing_structural(n_files=300)
    for k, v in cold.items():
        print(f"  {k}: {v}")

    import json
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "p1_final_replay.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"c2_automatic": c2, "s1_query_plan": s1, "cold_indexing": cold}, fh, indent=2)
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
