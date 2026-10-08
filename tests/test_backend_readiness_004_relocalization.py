"""PLAN-TARGET-LOCALIZATION-MISMATCH-001 (BACKEND-READINESS-004): deterministic relocalization at the edit-protocol stop.

T2 shape, synthetic: the goal names ``Element.absUrl``; the plan owns the facade Element.java (large, no exact locus);
the definition lives in Node.java. At CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE the structural index (code intelligence,
no model) resolves the goal's named member to its unique main-side definition outside the approved scope, and the run
records a plan-scope conflict PLAN_TARGET_RELOCALIZATION_REQUIRED with grounded locations - the existing plan
revision + re-invocation path owns the rest. Negatives: an ambiguous name, a test-side definition, a definition
inside the approved scope and no named symbol at all record nothing. No jsoup-specific logic.
"""
import os
from types import SimpleNamespace

from _edit_protocol_harness import UNLOCALIZED_GOAL, run_edit_protocol
from test_prd020_milestone_requirements import _probe

from kriya.code_intel.service import CodeIntelligenceService
from kriya.workflow import relocalization as rl
from kriya.workflow.edit_capability import CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE

ELEMENT = """package demo;

public class Element extends Node {
    public String attr(String key) { return key.startsWith("abs:") ? absUrl(key.substring(4)) : key; }
}
"""
NODE = """package demo;

public class Node {
    protected String baseUri = "";

    public String absUrl(String attributeKey) {
        return StringUtil.resolve(baseUri, attributeKey);
    }
}
"""
STRING_UTIL = """package demo;

public final class StringUtil {
    public static String resolve(String base, String rel) { return base + rel; }
    public static String resolve(java.net.URL base, String rel) { return rel; }
}
"""
NODE_TEST = """package demo;

public class NodeTest {
    public void absUrl() {}
}
"""
GOAL = ("Element.absUrl(...) resolves relative links incorrectly: Jsoup.parse(\"<a href='?page=2'>\", \"https://example.com/x/\")\n"
        "then doc.selectFirst(\"a\").absUrl(\"href\") loses the path. Please fix absolute URL resolution.\n")


def _index(tmp_path, files):
    repo = tmp_path / "repo"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    service = CodeIntelligenceService(str(repo), str(tmp_path / "ci.db"))
    service.refresh()
    return repo, service


def test_01_a_goal_named_member_defined_outside_the_plan_is_a_grounded_relocalization(tmp_path):
    repo, service = _index(tmp_path, {"src/main/java/demo/Element.java": ELEMENT, "src/main/java/demo/Node.java": NODE,
                                      "src/main/java/demo/StringUtil.java": STRING_UTIL, "src/test/java/demo/NodeTest.java": NODE_TEST})
    try:
        definitions, ambiguous = rl.goal_symbol_definitions(service, GOAL)
        assert [(d.name, d.lookup_key, d.path, d.line) for d in definitions] == [("absUrl", "demo.Node.absUrl", "src/main/java/demo/Node.java", 6)]
        assert ambiguous == []  # Jsoup.parse / doc.selectFirst resolve to nothing in this repository
        conflict = rl.relocalization_conflict(service, GOAL, infeasible_paths=["src/main/java/demo/Element.java"],
                                              allowed_paths={"src/main/java/demo/Element.java"},
                                              read_revision=lambda rel: "rev-" + os.path.basename(rel))
        assert conflict["reason_code"] == rl.PLAN_TARGET_RELOCALIZATION_REQUIRED and conflict["classification"] == "PLAN_SCOPE_DEFECT"
        assert conflict["required_files"] == ["src/main/java/demo/Node.java"] == conflict["grounded_owner_files"]
        assert conflict["grounded_locations"] == [{"filepath": "src/main/java/demo/Node.java", "line": 6, "revision": "rev-Node.java",
                                                   "symbol": "demo.Node.absUrl"}]
        assert conflict["attribution_tier"] == rl.ATTRIBUTION_TIER and "Element.java" in conflict["reason"]
        assert conflict["relocalization"]["infeasible_paths"] == ["src/main/java/demo/Element.java"]
        # the definition inside the approved scope is not a relocalization (it is a locus, handled elsewhere)
        assert rl.relocalization_conflict(service, GOAL, infeasible_paths=["src/main/java/demo/Node.java"],
                                          allowed_paths={"src/main/java/demo/Node.java"}, read_revision=lambda rel: "r") is None
        # no index, or a goal naming no member: nothing
        assert rl.relocalization_conflict(None, GOAL, infeasible_paths=["x"], allowed_paths=(), read_revision=lambda rel: "r") is None
        assert rl.relocalization_conflict(service, "Make resolution faster.\n", infeasible_paths=["src/main/java/demo/Element.java"],
                                          allowed_paths=(), read_revision=lambda rel: "r") is None
    finally:
        service.close()


def test_02_an_ambiguous_member_name_is_never_a_candidate_and_test_definitions_are_ignored(tmp_path):
    repo, service = _index(tmp_path, {"src/main/java/demo/Element.java": ELEMENT, "src/main/java/demo/Node.java": NODE,
                                      "src/main/java/demo/StringUtil.java": STRING_UTIL, "src/test/java/demo/NodeTest.java": NODE_TEST})
    try:
        goal = "StringUtil.resolve(base, rel) drops the query; fix it.\n"
        definitions, ambiguous = rl.goal_symbol_definitions(service, goal)
        assert definitions == [] and ambiguous[0]["name"] == "StringUtil.resolve" and len(ambiguous[0]["definitions"]) == 2
        assert rl.relocalization_conflict(service, goal, infeasible_paths=["src/main/java/demo/Element.java"],
                                          allowed_paths=(), read_revision=lambda rel: "r") is None
        # a goal naming only the test-side definition: nothing (a test is never the edit target)
        only_test = "NodeTest.absUrl() is wrong.\n"
        definitions, ambiguous = rl.goal_symbol_definitions(service, only_test)
        assert all("src/test" not in d.path for d in definitions)
    finally:
        service.close()


def test_03_the_attempt_records_the_conflict_at_the_stop_and_the_run_reports_a_plan_scope_revision(tmp_path, monkeypatch):
    """The wiring: at the typed stop (nothing sent to the model) the conflict is on the state and the result, with
    the grounded location; without an index the stop is exactly what it was."""
    from kriya.workflow import attempt

    class _Symbol:
        def __init__(self, path, line):
            self.symbol_id, self.lookup_key, self.kind, self.path = "sym:1", "pkg.members.resolve_member", "function", path
            self.is_callable, self.is_type = True, False
            self.declaration = SimpleNamespace(start_line=line)

    class _Service:
        def find_symbol(self, name):
            return [_Symbol("pkg/members.py", 7)] if name in ("Resolver.resolve_member", "resolve_member") else []

    goal = "Resolver.resolve_member(...) never resolves members with type arguments; make them resolve to the declared member."
    monkeypatch.setattr(attempt, "_code_intelligence_for", lambda ctx: _Service())
    run = run_edit_protocol(tmp_path, monkeypatch, ["unused"], probe=_probe, goal=goal)
    assert run.developer == []  # still nothing sent to the model
    assert run.result["environment_failure"].startswith(CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE)
    conflict = run.result["plan_scope_conflict"]
    assert conflict["reason_code"] == rl.PLAN_TARGET_RELOCALIZATION_REQUIRED and conflict["required_files"] == ["pkg/members.py"]
    assert conflict["grounded_locations"][0]["line"] == 7 and run.result["failure_category"] == "plan_scope_revision_required"
    # control: no index -> the bare stop, no conflict
    monkeypatch.setattr(attempt, "_code_intelligence_for", lambda ctx: None)
    (tmp_path / "bare").mkdir()
    bare = run_edit_protocol(tmp_path / "bare", monkeypatch, ["unused"], probe=_probe, goal=UNLOCALIZED_GOAL)
    assert bare.result["failure_category"] == "context_edit_protocol_unsatisfiable" and not bare.result.get("plan_scope_conflict")
