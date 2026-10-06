"""PROSPECTIVE Graphify acceptance suite - UNAPPROVED, for owner review before any live Graphify run.

Written only from the frozen goal (goal.txt sha256 f96bf5a3...): its own reproducer files and its own table
(cases 1, 2, 5 must gain a `calls` edge; controls 3, 4 keep theirs) and graphify's public `extract()` API as the
frozen base's own tests use it. No hidden-evaluator content was read or used."""
import os
from pathlib import Path

import pytest
from graphify.extract import extract

SETTINGS = (
    "namespace Demo\n{\n    public class Settings\n    {\n"
    "        public T Get<T>(string key) { return default(T); }\n"
    "        public string GetRaw(string key) { return key; }\n    }\n}\n"
)
READER = (
    "namespace Demo\n{\n    public class Reader : Settings\n    {\n"
    "        public int A() { return Get<int>(\"port\"); }\n"
    "        public int B() { return this.Get<int>(\"port\"); }\n"
    "        public string C() { return GetRaw(\"host\"); }\n"
    "        public string D() { return this.GetRaw(\"host\"); }\n    }\n}\n"
)
LOCAL = (
    "namespace Demo\n{\n    public class Local\n    {\n"
    "        public T Make<T>() { return default(T); }\n"
    "        public int E() { return Make<int>(); }\n    }\n}\n"
)


@pytest.fixture
def graph(tmp_path):
    files = {"src/Settings.cs": SETTINGS, "src/Reader.cs": READER, "src/Local.cs": LOCAL}
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    old = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = extract([Path(n) for n in files], cache_root=tmp_path / ".cache", root=tmp_path)
    finally:
        os.chdir(old)
    calls = {(e["source"], e["target"]) for e in result["edges"] if e["relation"] == "calls"}
    return calls, result


def _node(result, label, file_stem):
    matches = [n["id"] for n in result["nodes"] if n["label"] == label and file_stem in n["id"].lower()]
    assert len(matches) == 1, (label, file_stem, matches)
    return matches[0]


def _edge(graph, caller, caller_file, callee, callee_file):
    calls, result = graph
    return (_node(result, caller, caller_file), _node(result, callee, callee_file)) in calls


@pytest.mark.kriya_requirement("REQ-15")
def test_case1_unqualified_generic_call_to_a_base_class_method_gets_a_calls_edge(graph):
    assert _edge(graph, ".A()", "reader", ".Get()", "settings")


@pytest.mark.kriya_requirement("REQ-15")
def test_case2_generic_call_through_this_gets_a_calls_edge(graph):
    assert _edge(graph, ".B()", "reader", ".Get()", "settings")


@pytest.mark.kriya_requirement("REQ-15")
def test_case5_unqualified_generic_call_to_a_same_class_method_gets_a_calls_edge(graph):
    assert _edge(graph, ".E()", "local", ".Make()", "local")


@pytest.mark.kriya_requirement("REQ-15")
def test_control3_unqualified_non_generic_call_keeps_its_calls_edge(graph):
    assert _edge(graph, ".C()", "reader", ".GetRaw()", "settings")


@pytest.mark.kriya_requirement("REQ-15")
def test_control4_non_generic_call_through_this_keeps_its_calls_edge(graph):
    assert _edge(graph, ".D()", "reader", ".GetRaw()", "settings")
