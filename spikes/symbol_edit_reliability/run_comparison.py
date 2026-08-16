"""The actual comparison: for the same 6 hand-crafted scenarios, does Kriya's
REAL text-based apply_anchored_edits() succeed, and does the new symbol-based
replace_symbol_by_name() succeed? Deterministic, hand-crafted inputs, not
LLM-generated - isolates "does the edit MECHANISM work" from "does a model
generate good content", which is the specific thing this whole investigation
has kept carefully separate all along.

Do not run this until test_symbol_client.py passes in full first - this
script trusts symbol_client.py's correctness, it does not re-verify it.

Run: .venv/bin/pytest spikes/symbol_edit_reliability/test_symbol_client.py -v   # first
     .venv/bin/python spikes/symbol_edit_reliability/run_comparison.py         # then this
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from kriya.tools.lsp import find_jdtls  # noqa: E402
from kriya.workflow.edit_safety import apply_anchored_edits, find_structural_corruption  # noqa: E402
from symbol_client import SymbolAwareJdtlsClient, replace_symbol_by_name  # noqa: E402

FIXTURE_PROJECT = str(Path(__file__).resolve().parent / "project")
CALCULATOR_JAVA = str(Path(FIXTURE_PROJECT) / "src/main/java/com/example/Calculator.java")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


# Each scenario: (name, description, expect_text_success, expect_symbol_success,
# text_edit, symbol_name, symbol_new_declaration). expect_* document what this
# scenario is designed to demonstrate, checked against the ACTUAL outcome below -
# a mismatch is flagged loudly, not silently accepted, since a scenario behaving
# differently than designed would mean the comparison itself can't be trusted.
SCENARIOS = [
    dict(
        name="clean_edit",
        description="Unambiguous edit, no adversarial conditions - baseline sanity check.",
        expect_text_success=True,
        expect_symbol_success=True,
        text_edit={
            "search": "    public int add(int a, int b) {\n        return a + b;\n    }",
            "replace": "    public int add(int a, int b) {\n        return a + b + 1;\n    }",
        },
        symbol_name="add",
        symbol_new="public int add(int a, int b) {\n        return a + b + 1;\n    }",
    ),
    dict(
        name="whitespace_drift",
        description="Search block has different indentation/blank-line count than the real file "
                     "(the real gutter/formatting-drift shape observed live this session).",
        expect_text_success=True,  # apply_anchored_edits' own tolerant matching is built for exactly this
        expect_symbol_success=True,
        text_edit={
            "search": "public int add(int a, int b) {\n  return a + b;\n\n\n}",  # 2-space indent, extra blank lines
            "replace": "public int add(int a, int b) {\n    return a + b + 1;\n}",
        },
        symbol_name="add",
        symbol_new="public int add(int a, int b) {\n        return a + b + 1;\n    }",
    ),
    dict(
        name="ambiguous_duplicate_text",
        description="The target method's exact body text also appears verbatim in a comment "
                     "elsewhere in the file (constructed in Calculator.java's own fixture comment).",
        expect_text_success=False,  # apply_anchored_edits correctly REJECTS - matches twice
        expect_symbol_success=True,  # comments aren't symbols, name resolution is unaffected
        text_edit={
            "search": "return a + b + bonus;",
            "replace": "return a + b + bonus + 1;",
        },
        symbol_name="computeTotal",
        symbol_new="public int computeTotal(int a, int b, int bonus) {\n        return a + b + bonus + 1;\n    }",
    ),
    dict(
        name="overloaded_method_no_disambiguation",
        description="Two methods share the name 'describe' (different params). The TRADEOFF "
                     "scenario, not a pure win for either side: the body text naturally "
                     "disambiguates for text-based, but this spike's symbol resolution is "
                     "deliberately name-only (see symbol_client.py's own docstring) and must "
                     "fail loudly rather than guess.",
        expect_text_success=True,
        expect_symbol_success=False,
        text_edit={
            "search": '    public String describe() {\n        return "Calculator";\n    }',
            "replace": '    public String describe() {\n        return "Calc";\n    }',
        },
        symbol_name="describe",
        symbol_new='public String describe() {\n        return "Calc";\n    }',
    ),
    dict(
        name="misdirected_content",
        description="Content/symbol name actually belongs to a DIFFERENT file (Other.java's "
                     "subtract()), applied against Calculator.java - reproduces increment 9's "
                     "real bug shape (misdirected edit).",
        expect_text_success=False,  # matched 0 times - "return a - b;" isn't in Calculator.java
        expect_symbol_success=False,  # no symbol named 'subtract' in Calculator.java
        text_edit={
            "search": "return a - b;",
            "replace": "return b - a;",
        },
        symbol_name="subtract",
        symbol_new="irrelevant - should never be reached",
    ),
]


async def run_symbol_side(client, scenario, original):
    try:
        result = await replace_symbol_by_name(
            client, CALCULATOR_JAVA, original, scenario["symbol_name"], scenario["symbol_new"],
        )
        return True, result, None
    except ValueError as e:
        return False, None, str(e)


def run_text_side(scenario, original):
    try:
        result = apply_anchored_edits(original, [scenario["text_edit"]], original)
        return True, result, None
    except ValueError as e:
        return False, None, str(e)


async def main():
    if find_jdtls() is None:
        print("ERROR: jdtls not found on PATH.")
        sys.exit(1)

    client = SymbolAwareJdtlsClient(FIXTURE_PROJECT, find_jdtls())
    print("Starting jdtls (may take up to ~2 minutes for initial indexing)...")
    await client.start()

    results = []
    try:
        for scenario in SCENARIOS:
            original = _read(CALCULATOR_JAVA)  # fresh, unmutated read every scenario

            text_ok, text_result, text_error = run_text_side(scenario, original)
            symbol_ok, symbol_result, symbol_error = await run_symbol_side(client, scenario, original)

            # Sanity net: any SUCCESSFUL edit's output must still be structurally
            # valid Java - a corruption here means a real bug in this spike's own
            # code (most likely symbol_client.py's position math), not a finding
            # about the scenario itself.
            for label, ok, result in [("text", text_ok, text_result), ("symbol", symbol_ok, symbol_result)]:
                if ok:
                    corruption = find_structural_corruption("Calculator.java", result)
                    if corruption:
                        print(f"!!! SPIKE BUG: {scenario['name']}'s {label}-based result is structurally "
                              f"corrupt: {corruption} - this result cannot be trusted, fix the spike before "
                              f"drawing any conclusion from it.")

            text_matches_expectation = text_ok == scenario["expect_text_success"]
            symbol_matches_expectation = symbol_ok == scenario["expect_symbol_success"]

            results.append(dict(
                name=scenario["name"], text_ok=text_ok, symbol_ok=symbol_ok,
                text_matches_expectation=text_matches_expectation,
                symbol_matches_expectation=symbol_matches_expectation,
            ))

            unexpected_note = "  <<< UNEXPECTED - does not match this scenario's own design"
            print(f"\n{'=' * 78}\n{scenario['name']}\n{'=' * 78}")
            print(f"  {scenario['description']}")
            text_flag = "" if text_matches_expectation else unexpected_note
            print(f"  text-based:   {'SUCCESS' if text_ok else 'REJECTED'}{text_flag}")
            if not text_ok:
                print(f"                ({text_error[:150]})")
            symbol_flag = "" if symbol_matches_expectation else unexpected_note
            print(f"  symbol-based: {'SUCCESS' if symbol_ok else 'REJECTED'}{symbol_flag}")
            if not symbol_ok:
                print(f"                ({symbol_error[:150]})")
    finally:
        await client.shutdown()

    print(f"\n{'=' * 78}\nSummary\n{'=' * 78}")
    all_matched = all(r["text_matches_expectation"] and r["symbol_matches_expectation"] for r in results)
    for r in results:
        flag = "" if (r["text_matches_expectation"] and r["symbol_matches_expectation"]) else "  <<< CHECK THIS ONE"
        print(f"  {r['name']:<40} text={'ok' if r['text_ok'] else 'reject':<8} symbol={'ok' if r['symbol_ok'] else 'reject':<8}{flag}")
    print(f"\nAll scenarios behaved as designed: {all_matched}")
    if not all_matched:
        print("At least one scenario did NOT behave as designed - read the per-scenario output above "
              "carefully before drawing any conclusion; this may mean the scenario's own construction "
              "needs fixing, not that the finding itself is real.")


if __name__ == "__main__":
    asyncio.run(main())
