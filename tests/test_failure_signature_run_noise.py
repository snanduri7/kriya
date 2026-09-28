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
