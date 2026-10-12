"""BACKEND-READINESS-V2, S4_A false success: a candidate that newly swallows Error/Throwable hides the crash it was asked
to fix. Mechanism measured on the exported S4_A candidate: `catch (Exception e)` was widened to `catch (Throwable e)` in the
shared path compiler; the goal's StackOverflowError still occurred (caught once per input) and the call then returned
normally, which is all the external tests observe. The rule is a deterministic delta gate (kriya/workflow/exception_masking.py)
run on every attempt; the unit matrix below pins its decision, the end-to-end tests drive the real direct workflow."""
import asyncio
import subprocess
from unittest.mock import AsyncMock, patch

import pytest
from _edit_protocol_harness import make_config
from _protocol_responses import sentinel

from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.exception_masking import find_unsafe_exception_masking, masking_clauses
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine

PATH = "src/main/java/demo/Parser.java"
BASE = '''package demo;

public class Parser {
    public static String compile(String path) {
        try {
            return read(path);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    static String read(String p) {
        return p;
    }
}
'''


def _with_catch(clause: str, body: str, base: str = BASE) -> str:
    old = "        } catch (Exception e) {\n            throw new IllegalStateException(e);\n        }\n"
    assert old in base
    return base.replace(old, f"        }} catch ({clause}) {{\n{body}\n        }}\n")


SWALLOW = "            return null;"
CONVERT = "            throw new IllegalStateException(e);"


@pytest.mark.parametrize("clause,body", [
    ("Throwable e", CONVERT),                       # the exported S4_A shape: widening, converted to a recoverable exception
    ("Throwable e", SWALLOW),
    ("Error e", SWALLOW),
    ("StackOverflowError e", SWALLOW),
    ("java.lang.StackOverflowError e", SWALLOW),
    ("OutOfMemoryError | IllegalStateException e", SWALLOW),
    ("final Throwable e", "            log(e);"),   # logged and fall through
    ("Throwable e", "            if (e instanceof RuntimeException) { throw (RuntimeException) e; }\n            return null;"),
    ("Throwable e", "            if (fatal(e)) { throw e; }\n            return null;"),  # rethrows on one path only
    ("Throwable e", "            throw (IllegalStateException) e;"),  # a cast is not a rethrow of the Error
    ("Throwable e", ""),                              # an empty catch body swallows
    ("MyCustomError e", SWALLOW),                    # any *Error-named type is the Error hierarchy
])
def test_a_candidate_that_newly_swallows_or_converts_an_error_is_rejected(clause, body):
    finding = find_unsafe_exception_masking(PATH, _with_catch(clause, body), BASE)
    assert finding is not None and finding.startswith("UNSAFE_EXCEPTION_MASKING: line 7 ")


def test_a_new_catch_of_throwable_is_rejected_for_a_new_file_and_for_an_added_clause():
    assert find_unsafe_exception_masking(PATH, _with_catch("Throwable e", SWALLOW), None) is not None
    added = BASE.replace("        } catch (Exception e) {", "        } catch (StackOverflowError so) {\n            return \"\";\n        } catch (Exception e) {")
    finding = find_unsafe_exception_masking(PATH, added, BASE)
    assert finding is not None and "StackOverflowError" in finding


@pytest.mark.parametrize("clause,body", [
    ("Throwable e", "            throw e;"),                                   # rethrown unchanged
    ("Error e", "            throw e;"),
    ("Throwable e", "            log(e);\n            throw e;"),
    ("Throwable t", "            throw new AssertionError(\"x\", t);"),         # re-raised as an Error
    ("Exception e", SWALLOW),                                                  # ordinary exception handling
    ("IllegalArgumentException | IllegalStateException e", SWALLOW),
    ("ErrorHandlerException e", SWALLOW),                                      # a name that merely contains "Error"
    ("ParseException e", CONVERT),
])
def test_rethrown_and_ordinary_exception_handling_is_allowed(clause, body):
    assert find_unsafe_exception_masking(PATH, _with_catch(clause, body), BASE) is None


def test_a_pre_existing_swallowing_catch_is_not_a_finding_by_presence():
    base = _with_catch("Throwable e", SWALLOW)
    assert masking_clauses(PATH, base)            # the base does swallow...
    assert find_unsafe_exception_masking(PATH, base, base) is None                      # ...unchanged: no finding
    edited = base.replace("static String read(String p) {\n        return p;", "static String read(String p) {\n        return p.trim();")
    assert find_unsafe_exception_masking(PATH, edited, base) is None                    # an unrelated edit
    reworded = base.replace(SWALLOW, "            return \"\";")
    assert find_unsafe_exception_masking(PATH, reworded, base) is None                  # same clause, other body
    # but a SECOND swallowing Throwable clause beyond the pre-existing one is the candidate's
    doubled = base.replace("    static String read", "    static String other() {\n        try {\n            return read(\"\");\n        } catch (Throwable x) {\n            return null;\n        }\n    }\n\n    static String read")
    assert find_unsafe_exception_masking(PATH, doubled, base) is not None


def test_comments_strings_and_non_java_files_are_not_clauses():
    commented = BASE.replace("    static String read", "    // } catch (Throwable e) { return null; }\n    static String note = \"catch (Throwable e) { }\";\n    static String read")
    assert find_unsafe_exception_masking(PATH, commented, BASE) is None
    assert find_unsafe_exception_masking("notes.md", _with_catch("Throwable e", SWALLOW), BASE) is None
    assert find_unsafe_exception_masking(PATH, "", BASE) is None


def test_assertion_error_is_allowed_in_test_files_only():
    swallow = _with_catch("AssertionError e", SWALLOW)
    assert find_unsafe_exception_masking("src/test/java/demo/ParserTest.java", swallow, BASE) is None
    assert find_unsafe_exception_masking(PATH, swallow, BASE) is not None
    # every other Error is still judged in a test file
    assert find_unsafe_exception_masking("src/test/java/demo/ParserTest.java", _with_catch("Throwable e", SWALLOW), BASE) is not None


def _end_to_end(tmp_path, response, base=BASE):
    from kriya.workflow.state import (
        GenerationState as _State,  # noqa: F401  (imported at module level; kept for clarity)
    )

    model_runtime.clear_model_runtime_cache()
    cfg = make_config(tmp_path)
    workspace = tmp_path / "ws"
    (workspace / "src/main/java/demo").mkdir(parents=True)
    (workspace / PATH).write_text(base)
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=workspace, check=True,
                       capture_output=True)
    answers = list(response) if isinstance(response, list) else [response]
    sent, failures = [], []
    real_record = GenerationState.record_gate_outcome

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = '{"files": ["%s"]}' % PATH
        elif "Developer Agent" in first:
            sent.append(user_prompt)
            content = answers.pop(0) if len(answers) > 1 else answers[0]
        else:
            content = "Review: Approved"
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": len((system_prompt or "") + (user_prompt or "")) // 3,
                "completion_tokens": 5, "finish_reason": "stop", "provider_metadata": {}}

    def record(state, outcome):
        failures.append(outcome.get("type"))
        return real_record(state, outcome)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_gate_outcome", new=record), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=lambda *a, **k: {"success": True, "output": "1 passed"}):
        result = asyncio.run(engine.run_generation_workflow(
            goal="Make Parser.compile in src/main/java/demo/Parser.java throw IllegalArgumentException instead of IllegalStateException.",
            workspace_path=str(workspace), predetermined_plan=f"Repair {PATH}", predetermined_design="",
            predetermined_architect_files=[PATH], approval_callback=AsyncMock(return_value=True)))
    return bool(result.get("quality_gates_passed")), failures, (workspace / PATH).read_text(), sent


GOOD = sentinel(PATH, analysis="FIX ANALYSIS: change the exception type thrown by compile.",
                content=BASE.replace("IllegalStateException(e)", "IllegalArgumentException(e)"))
BAD = sentinel(PATH, analysis="FIX ANALYSIS: widen the catch so every failure is converted.",
               content=_with_catch("Throwable e", "            throw new IllegalArgumentException(e);"))


def test_end_to_end_an_ordinary_correct_edit_is_not_blocked(tmp_path):
    passed, failures, final, _ = _end_to_end(tmp_path, GOOD)
    assert "unsafe_exception_masking" not in failures
    assert passed is True and "IllegalArgumentException" in final and "Throwable" not in final


def test_end_to_end_an_unsafe_widening_is_rejected_on_every_attempt_and_never_written(tmp_path):
    """Run through the real workflow with gates stubbed green: only the new rule can stop the widening. A model that
    repeats it on the repair is rejected again, and the file is never changed."""
    passed, failures, final, sent = _end_to_end(tmp_path, BAD)
    assert passed is False and failures.count("unsafe_exception_masking") >= 2   # first attempt AND the repair
    assert final == BASE
    # the repair request carries the actionable reason
    assert all("do not widen a catch to Error/Throwable" in prompt for prompt in sent[1:])


def test_end_to_end_a_repair_that_removes_the_widening_is_accepted(tmp_path):
    passed, failures, final, _ = _end_to_end(tmp_path, [BAD, GOOD])
    assert failures.count("unsafe_exception_masking") == 1
    assert passed is True and "Throwable" not in final and "IllegalArgumentException" in final



# The exported S4_A candidate's hunk, verbatim (PathCompiler.compile, base on the left side, candidate on the right).
S4A_BASE = '''public class PathCompiler {
    public static Path compile(String path, final Predicate... filters) {
        try {
            CharacterIndex ci = new CharacterIndex(path);
            LinkedList<Predicate> filterStack = new LinkedList<Predicate>(asList(filters));
            return new PathCompiler(ci, filterStack).compile();
        } catch (Exception e) {
            InvalidPathException ipe;
            if (e instanceof InvalidPathException) {
                ipe = (InvalidPathException) e;
            } else {
                ipe = new InvalidPathException(e);
            }
            throw ipe;
        }
    }
}
'''
S4A_CANDIDATE = '''public class PathCompiler {
    public static Path compile(String path, final Predicate... filters) {
        try {
            CharacterIndex ci = new CharacterIndex(path);
            LinkedList<Predicate> filterStack = new LinkedList<Predicate>(asList(filters));
            return new PathCompiler(ci, filterStack).compile();
        } catch (Throwable e) {
            if (e instanceof InvalidPathException) {
                throw (InvalidPathException) e;
            }
            throw new InvalidPathException(e);
        }
    }
}
'''


def test_the_exported_s4a_false_success_candidate_is_rejected_and_its_baseline_is_not():
    path = "json-path/src/main/java/com/jayway/jsonpath/internal/path/PathCompiler.java"
    finding = find_unsafe_exception_masking(path, S4A_CANDIDATE, S4A_BASE)
    assert finding is not None and "newly catches Throwable" in finding
    assert find_unsafe_exception_masking(path, S4A_BASE, S4A_BASE) is None
    # the same candidate judged against itself (it already was the file) is by-presence unchanged
    assert find_unsafe_exception_masking(path, S4A_CANDIDATE, S4A_CANDIDATE) is None


FILLER = "".join(f"    static int helper{i}(int v) {{\n        int a = v + {i};\n        int b = a * 2;\n        return b - a;\n    }}\n\n" for i in range(400))
BIG = BASE.replace("    static String read", FILLER + "    static String read")
EDIT_OLD = "        } catch (Exception e) {\n            throw new IllegalStateException(e);\n        }\n"


def _edit(replace: str) -> str:
    return sentinel(PATH, analysis="FIX ANALYSIS: change the catch.", edits=[(EDIT_OLD.rstrip("\n"), replace.rstrip("\n"))])


def test_end_to_end_the_anchored_edit_path_is_gated_too(tmp_path):
    """A large target is repaired by SEARCH/REPLACE edits, a different staging branch from the full-file one above."""
    bad = _edit("        } catch (Throwable e) {\n            throw new IllegalArgumentException(e);\n        }\n")
    good = _edit("        } catch (Exception e) {\n            throw new IllegalArgumentException(e);\n        }\n")
    passed, failures, final, _ = _end_to_end(tmp_path / "bad", bad, BIG)
    assert passed is False and failures.count("unsafe_exception_masking") >= 1 and final == BIG
    passed, failures, final, _ = _end_to_end(tmp_path / "good", good, BIG)
    assert "unsafe_exception_masking" not in failures and passed is True and "IllegalArgumentException" in final


def test_the_run_start_content_decides_what_is_new_not_the_last_staged_state():
    from types import SimpleNamespace

    from kriya.workflow.attempt import _reject_exception_masking
    from kriya.workflow.failure import QualityGateFailure

    state = SimpleNamespace(all_original_contents={PATH: BASE}, attempt_number=3, record_gate_outcome=lambda outcome: None)
    widened = _with_catch("Throwable e", SWALLOW)
    # the file a previous attempt left (already widened) is not the baseline: the run-start base is
    with pytest.raises(QualityGateFailure):
        _reject_exception_masking(state, PATH, widened, widened)
    # a file with no recorded run-start content falls back to the pre-write content
    state.all_original_contents = {}
    _reject_exception_masking(state, PATH, widened, widened)


def test_a_test_that_asserts_no_error_escapes_is_not_masking():
    """Found by replaying the guard over the recorded S4_A candidates: the Developer's own regression test wrote
    `catch (java.lang.Error e) { throw new AssertionError(..., e); }` - an Error re-raised as an Error - and a first version
    of the rule flagged it, because AssertionError is exempt as a CAUGHT type in test files and so was not read as an Error
    when thrown."""
    test_path = "src/test/java/demo/ParserTest.java"
    test = """package demo;
public class ParserTest {
    @org.junit.Test
    public void noError() {
        try {
            Parser.compile("[");
        } catch (java.lang.Error e) {
            throw new AssertionError("must not throw java.lang.Error, got: " + e, e);
        }
    }
}
"""
    assert find_unsafe_exception_masking(test_path, test, None) is None
    assert find_unsafe_exception_masking(PATH, test, None) is None   # the same shape in main source is also a re-raise
    # but swallowing it in a test is still judged
    assert find_unsafe_exception_masking(test_path, test.replace('throw new AssertionError("must not throw java.lang.Error, got: " + e, e);', "return;"), None) is not None


def test_the_real_recorded_s4a_file_bytes_are_rejected_against_the_real_base():
    """The whole PathCompiler.java at the S4_A base and the exact file Kriya's run applied (the false success)."""
    import pathlib

    directory = pathlib.Path(__file__).parent / "fixtures" / "exception_masking"
    base = (directory / "PathCompiler.base.java.txt").read_text()
    candidate = (directory / "PathCompiler.s4a_candidate.java.txt").read_text()
    path = "json-path/src/main/java/com/jayway/jsonpath/internal/path/PathCompiler.java"
    finding = find_unsafe_exception_masking(path, candidate, base)
    assert finding is not None and "line 76 newly catches Throwable" in finding
    assert find_unsafe_exception_masking(path, base, base) is None
