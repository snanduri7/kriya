"""FAILURE-SIGNATURE-RUN-NOISE-001: the same failure keeps the same signature.

PRD-036 rc7 matrix trial 1 (C8): an identical pytest failure got a new
signature on every attempt only because pytest printed an object address,
so each repeat reset the targeted and fallback budgets as a "new failure
family" and the retry loop stayed on the primary until the global ceiling.
"""
import asyncio
import json
import os
import subprocess
from unittest.mock import MagicMock

from kriya.config import AppConfig
from kriya.config.config import FallbackModelConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.failure_grounding import build_failure_signature
from kriya.workflow.workflow import WorkflowEngine


def _pytest_failure(address, elapsed):
    return f"""============================= test session starts ==============================
collected 3 items

test_inventory.py ..F                                                    [100%]

=================================== FAILURES ===================================
__________________________________ test_total __________________________________

>       inv.add_item("cherry", 0)

test_inventory.py:23:
self = <inventory.Inventory object at {address}>, name = 'cherry', qty = 0

    def add_item(self, name, qty):
        if qty <= 0:
>           raise ValueError("qty must be positive")
E           ValueError: qty must be positive

inventory.py:7: ValueError
=========================== short test summary info ============================
FAILED test_inventory.py::test_total - ValueError: qty must be positive
========================= 1 failed, 2 passed in {elapsed} (0:00:{elapsed[2:4]}) ==========================
"""


def test_an_identical_pytest_failure_keeps_its_signature_across_runs():
    first = build_failure_signature("test", _pytest_failure("0x10b1b7b10", "0.02s"))
    again = build_failure_signature("test", _pytest_failure("0x106893b10", "0.31s"))
    assert first == again


def test_a_java_identity_hash_in_an_exception_message_is_not_identity():
    def trace(hash_code):
        return ("[ERROR] Exception in thread \"main\" java.lang.IllegalStateException: "
                f"stale handle com.example.Session@{hash_code}\n\tat com.example.App.main(App.java:12)\n")
    assert build_failure_signature("run_verification", trace("1b2c3d4e")) == \
        build_failure_signature("run_verification", trace("6d06d69c"))


def test_genuinely_different_failures_keep_different_signatures():
    base = _pytest_failure("0x10b1b7b10", "0.02s")
    other_message = base.replace("qty must be positive", "qty must be an integer")
    other_test = base.replace("test_total", "test_count")
    signatures = {build_failure_signature("test", text) for text in (base, other_message, other_test)}
    assert len(signatures) == 3
    java = "[ERROR] Caused by: java.lang.IllegalStateException: handle {}\n"
    assert build_failure_signature("test", java.format("open")) != build_failure_signature("test", java.format("closed"))


def test_short_hex_values_and_maven_timing_keep_their_existing_treatment():
    assert build_failure_signature("test", "E  assert flags == 0x10\n") != \
        build_failure_signature("test", "E  assert flags == 0x20\n")
    maven = "[ERROR] BUILD FAILURE\n[INFO] Total time: {}\n[INFO] Finished at: {}\n"
    assert build_failure_signature("compile", maven.format("2.1 s", "10:00")) == \
        build_failure_signature("compile", maven.format("9.8 s", "11:30"))


# --- the C8 shape through the real run_generation_workflow ---------------------------------

PRIMARY = "primary-coder:30b"
FALLBACK = "fallback-coder:35b"
BASELINE = "class Counter:\n    def value(self):\n        return 3\n"
GOOD = BASELINE + "\n    def double(self):\n        return 2 * self.value()\n"


def _primary_candidate(n):
    # Breaks value() inside calc.py (so attribution targets calc.py): the
    # regression test fails the same way every attempt, and pytest's report
    # names the Counter object by its per-run address (`self = <... at 0x...>`).
    return (f"# attempt {n}\nclass Counter:\n    def value(self):\n        raise ValueError(\"value unavailable\")\n\n"
            "    def double(self):\n        return 2 * self.value()\n")


def _workspace(tmp_path):
    (tmp_path / "calc.py").write_text(BASELINE)
    (tmp_path / "test_calc.py").write_text(
        "from calc import Counter\n\n\ndef test_value():\n    assert Counter().value() == 3\n"
    )
    for argv in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"],
                 ["add", "-A"], ["commit", "-q", "-m", "initial"]):
        subprocess.run(["git", *argv], cwd=str(tmp_path), check=True)
    return str(tmp_path)


def test_a_repeating_failure_exhausts_the_primary_budget_and_reaches_the_fallback(tmp_path):
    workspace = _workspace(tmp_path)
    cfg = AppConfig()
    cfg.llm.model = PRIMARY
    cfg.autonomy.run_verification_enabled = False
    cfg.llm_chain = [FallbackModelConfig(model=FALLBACK, base_url=cfg.llm.base_url)]
    llm = LLMClient(cfg)
    developer_calls = []
    calls = {"n": 0}

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return "Step 1: add double() to Counter in calc.py"
        if calls["n"] == 2:
            return json.dumps({"files": ["calc.py"]})
        if not system_prompt.startswith(("You are the Kriya Developer Agent", "You are the Kriya File List Planner")):
            return json.dumps({"verdict": "PASS", "requirements": []})
        model = kwargs.get("model_override") or PRIMARY
        developer_calls.append(model)
        content = GOOD if model == FALLBACK else _primary_candidate(len(developer_calls))
        if "FILE CONTENT:" in f"{system_prompt}\n{user_prompt}":
            return f"FIX ANALYSIS: repair calc.py\nFILE CONTENT:\n{content}"
        return json.dumps([{"filepath": "calc.py", "content": content}])

    llm.complete = complete
    result = asyncio.run(WorkflowEngine(Kernel(config=cfg), llm).run_generation_workflow(
        goal="Add a method double(self) to Counter in calc.py returning twice value().",
        workspace_path=workspace, approval_callback=MagicMock(return_value=True),
    ))

    assert result["quality_gates_passed"] is True, result.get("failure_category")
    assert FALLBACK in developer_calls
    with open(os.path.join(workspace, "calc.py")) as handle:
        assert handle.read() == GOOD


# --- line numbers an edit shifts (PRD-036 rc8 matrix trial 2, C10) --------------------------

def _static_rule(line, snippet, path="test_sub.py", rule="markdown_inline_code_leak"):
    return (f"STATIC RULE VIOLATION: [{rule}] {path} line {line} contains a literal backtick "
            f"character - leaked into the file's actual content instead of real code: {snippet!r}.")


def test_a_static_rule_violation_is_identified_by_its_check_and_file():
    first = build_failure_signature("static_rule_violation", _static_rule(10, "x = `a`"))
    assert first == build_failure_signature("static_rule_violation", _static_rule(16, "total = `sub`"))
    assert first != build_failure_signature("static_rule_violation", _static_rule(10, "x = `a`", path="calc.py"))
    assert first != build_failure_signature("static_rule_violation", _static_rule(10, "x", rule="bare_verification_marker"))


def test_a_traceback_whose_frames_moved_keeps_its_signature():
    def trace(frame_line, def_line):
        return (f"test_calc.py:{frame_line}: in test_value\n    assert Counter().value() == 3\n"
                f'  File "calc.py", line {def_line}, in value\nE   RuntimeError: value unavailable\n')
    assert build_failure_signature("test", trace(5, 3)) == build_failure_signature("test", trace(9, 12))


def test_a_repeating_static_rule_violation_reaches_the_fallback(tmp_path):
    """The C10 shape: the primary keeps leaking Markdown into a new file, one
    line lower each time; the violation is the same, so the targeted budget
    runs out and the run escalates."""
    workspace = str(tmp_path)
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    for argv in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"],
                 ["add", "-A"], ["commit", "-q", "-m", "initial"]):
        subprocess.run(["git", *argv], cwd=workspace, check=True)
    good = "def sub(a, b):\n    return a - b\n"

    def leaking(n):
        padding = "".join(f"PAD_{i} = {i}\n" for i in range(n))
        return f"{good}\n{padding}Use `sub` for subtraction.\n"  # prose leak: not valid Python

    cfg = AppConfig()
    cfg.llm.model = PRIMARY
    cfg.autonomy.run_verification_enabled = False
    cfg.llm_chain = [FallbackModelConfig(model=FALLBACK, base_url=cfg.llm.base_url)]
    llm = LLMClient(cfg)
    developer_calls = []
    calls = {"n": 0}

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return "Step 1: create helpers.py with sub(a, b)"
        if calls["n"] == 2:
            return json.dumps({"files": ["helpers.py"]})
        if not system_prompt.startswith(("You are the Kriya Developer Agent", "You are the Kriya File List Planner")):
            return json.dumps({"verdict": "PASS", "requirements": []})
        model = kwargs.get("model_override") or PRIMARY
        developer_calls.append(model)
        content = good if model == FALLBACK else leaking(len(developer_calls))
        if "FILE CONTENT:" in f"{system_prompt}\n{user_prompt}":
            return f"FIX ANALYSIS: repair helpers.py\nFILE CONTENT:\n{content}"
        return json.dumps([{"filepath": "helpers.py", "content": content}])

    llm.complete = complete
    result = asyncio.run(WorkflowEngine(Kernel(config=cfg), llm).run_generation_workflow(
        goal="Create helpers.py with a function sub(a, b) returning a - b.",
        workspace_path=workspace, approval_callback=MagicMock(return_value=True),
    ))

    assert result["quality_gates_passed"] is True, result.get("failure_category")
    assert FALLBACK in developer_calls
    with open(os.path.join(workspace, "helpers.py")) as handle:
        assert handle.read() == good


def test_timestamps_uuids_temp_names_and_pids_are_not_identity():
    def server_failure(stamp, request_id, temp, pid):
        return (f"{stamp} INFO server started pid={pid} scratch=/var/folders/T/{temp}\n"
                f"{stamp} ERROR request {request_id} failed: connection refused on /health\n")
    first = build_failure_signature("run_verification", server_failure(
        "2026-09-28 20:21:21,468", "3f2b8c1e-9a4d-4e2f-8b7c-1d2e3f4a5b6c", "tmpab12cd34", 4242))
    again = build_failure_signature("run_verification", server_failure(
        "2026-09-29T07:02:11.9Z", "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d", "tmpzz98yy76", 91))
    assert first == again
    other = build_failure_signature("run_verification", server_failure(
        "2026-09-28 20:21:21,468", "3f2b8c1e-9a4d-4e2f-8b7c-1d2e3f4a5b6c", "tmpab12cd34", 4242).replace("/health", "/ready"))
    assert other != first
