"""B2 (Wave-2): every external dependency lookup has ONE policy owner (kriya/policy/egress.py: ``dependency_lookup_decision``).

MEASURED at 92d1c5f (WAVE2_B1_B4/B2/PREFIX_repro_output): with autonomy open and ``search.base_url`` unset, or with
``knowledge.offline_mode`` true, the javac (and Maven) compile gate still allowed the Maven Central class lookup while the egress
report said web lookup / registry metadata were denied: the gates recomputed the decision from two autonomy flags.

Invariant: the compile gates and the resolve_missing_dependency tool consume the decision (``PolymorphicValidator.dependency_lookup_allowed``);
the egress report records the same function's answer as the ``dependency_lookup`` channel; no other code reads ``web_lookup_enabled``.
Missing or unreadable policy state, and a validator built without the decision, deny.
"""
import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kriya.config import AppConfig
from kriya.policy.egress import EgressCapability, dependency_lookup_decision, web_lookup_allowed
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.egress_authority import describe_run_egress

ROOT = Path(__file__).resolve().parents[1] / "kriya"
SEARCH = "http://127.0.0.1:8888"
JAVAC_FAILURE = {"returncode": 1, "stdout": "", "stderr": "App.java:1: error: reached end of file while parsing"}
POM = ("<project><modelVersion>4.0.0</modelVersion><groupId>g</groupId><artifactId>a</artifactId><version>1</version>"
       "<properties><maven.compiler.source>17</maven.compiler.source></properties></project>")


def _cfg(*, egress="unrestricted", web_lookup=True, search=SEARCH, offline=False):
    cfg = AppConfig()
    cfg.autonomy.egress_policy, cfg.autonomy.web_lookup_enabled = egress, web_lookup
    cfg.search.base_url, cfg.knowledge.offline_mode = search, offline
    return cfg


# (label, config, expected decision)
CASES = [
    ("open, configured, online", _cfg(), True),
    ("autonomy allows, search.base_url unset", _cfg(search=""), False),
    ("autonomy allows, knowledge.offline_mode", _cfg(offline=True), False),
    ("web lookup disabled", _cfg(web_lookup=False), False),
    ("egress local_only", _cfg(egress="local_only"), False),
    ("everything closed", _cfg(egress="local_only", web_lookup=False, search="", offline=True), False),
]
IDS = [case[0] for case in CASES]


def _gate_flag_javac(tmp_path, cfg):
    (tmp_path / "App.java").write_text("class App {")
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=cfg.autonomy,
                                     dependency_lookup_allowed=dependency_lookup_decision(cfg).allowed)
    seen = []
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=lambda *a, **k: dict(JAVAC_FAILURE)), \
         patch("kriya.tools.resolver.enrich_java_compiler_errors",
               side_effect=lambda text, allow_external_lookup: seen.append(allow_external_lookup) or text):
        result = validator.run_compile_check(["App.java"])
    assert result["success"] is False
    return seen


def _gate_flag_maven(tmp_path, cfg):
    (tmp_path / "pom.xml").write_text(POM)
    source = tmp_path / "src" / "main" / "java" / "App.java"
    source.parent.mkdir(parents=True)
    source.write_text('class App { int x = "not an int"; }')
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=cfg.autonomy,
                                     dependency_lookup_allowed=dependency_lookup_decision(cfg).allowed)
    failure = {"returncode": 1, "stdout": f"[ERROR] {source}:[1,24] incompatible types: java.lang.String cannot be converted to int",
               "stderr": "", "timeout": False}
    seen = []
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=dict(failure)), \
         patch("kriya.tools.resolver.enrich_java_compiler_errors",
               side_effect=lambda text, allow_external_lookup: seen.append(allow_external_lookup) or text):
        result = validator.run_compile_check(["src/main/java/App.java"])
    assert result["success"] is False and "environment_reason_code" not in result
    return seen


# ---------------------------------------------------------------- the decision, and the report records the same one

@pytest.mark.parametrize("label, cfg, expected", CASES, ids=IDS)
def test_the_decision_combines_every_policy_input(label, cfg, expected):
    decision = dependency_lookup_decision(cfg)
    assert decision.allowed is expected, label
    assert decision.capability is (EgressCapability.APPROVED_PUBLIC_KNOWLEDGE if expected else EgressCapability.DENIED)
    assert bool(decision.destinations) is expected
    if not expected:
        assert decision.reason.startswith("refused: ")


@pytest.mark.parametrize("label, cfg, expected", CASES, ids=IDS)
def test_the_egress_report_records_the_same_decision(label, cfg, expected):
    rows = {d.channel: d for d in describe_run_egress(cfg)}
    assert rows["dependency_lookup"].allowed is expected and rows["dependency_lookup"] == dependency_lookup_decision(cfg)
    assert rows["web_lookup"].allowed is web_lookup_allowed(cfg)                # the web channel asks the same function too


@pytest.mark.parametrize("cfg", [None, object(), MagicMock(spec=[])], ids=["none", "bare object", "spec-less mock"])
def test_missing_or_unreadable_policy_state_denies(cfg):
    assert dependency_lookup_decision(cfg).allowed is False and web_lookup_allowed(cfg) is False


# ---------------------------------------------------------------- the gates consume it, and the report and the gates agree

@pytest.mark.parametrize("label, cfg, expected", CASES, ids=IDS)
def test_the_javac_gate_follows_the_decision_not_the_autonomy_flags(tmp_path, label, cfg, expected):
    assert _gate_flag_javac(tmp_path, cfg) == [expected], label


@pytest.mark.parametrize("label, cfg, expected", CASES, ids=IDS)
def test_the_maven_gate_follows_the_decision_not_the_autonomy_flags(tmp_path, label, cfg, expected):
    assert _gate_flag_maven(tmp_path, cfg) == [expected], label


@pytest.mark.parametrize("label, cfg, expected", CASES, ids=IDS)
def test_report_and_gate_agree_on_the_same_configuration(tmp_path, label, cfg, expected):
    reported = next(d.allowed for d in describe_run_egress(cfg) if d.channel == "dependency_lookup")
    assert _gate_flag_javac(tmp_path, cfg) == [reported]


def test_a_validator_built_without_the_decision_never_looks_up(tmp_path):
    (tmp_path / "App.java").write_text("class App {")
    cfg = _cfg()                                                # the autonomy flags alone would have allowed it
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=cfg.autonomy)
    assert validator.dependency_lookup_allowed is False
    seen = []
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=lambda *a, **k: dict(JAVAC_FAILURE)), \
         patch("kriya.tools.resolver.enrich_java_compiler_errors",
               side_effect=lambda text, allow_external_lookup: seen.append(allow_external_lookup) or text):
        validator.run_compile_check(["App.java"])
    assert seen == [False]


# ---------------------------------------------------------------- the real transport is never touched when denied, and is when allowed

@pytest.mark.parametrize("label, cfg, expected", CASES, ids=IDS)
def test_no_http_request_leaves_the_resolver_unless_the_decision_allows_it(tmp_path, label, cfg, expected):
    (tmp_path / "App.java").write_text("class App {")
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=cfg.autonomy,
                                     dependency_lookup_allowed=dependency_lookup_decision(cfg).allowed)
    failure = {"returncode": 1, "stdout": "", "stderr": "App.java:1: error: package org.apache.commons.lang3 does not exist"}
    client = MagicMock()
    client.__enter__.return_value.get.return_value.status_code = 404
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=lambda *a, **k: dict(failure)), \
         patch("kriya.tools.resolver.httpx.Client", return_value=client) as http:
        validator.run_compile_check(["App.java"])
    assert (http.call_count > 0) is expected, label


# ---------------------------------------------------------------- the repair tool

@pytest.mark.asyncio
@pytest.mark.parametrize("label, cfg, expected", CASES, ids=IDS)
async def test_the_resolve_missing_dependency_tool_follows_the_decision(label, cfg, expected):
    from kriya.workflow.self_correction import run_self_correction_loop

    llm = MagicMock()
    llm.complete_with_tools = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{"id": "1", "name": "resolve_missing_dependency",
                                        "arguments": {"symbol": "org.apache.qpid.jms.JmsConnectionFactory"}}]},
        {"content": "done", "tool_calls": []},
    ])
    validator = MagicMock()
    validator.dependency_lookup_allowed = dependency_lookup_decision(cfg).allowed
    validator.autonomy_cfg.egress_policy, validator.autonomy_cfg.web_lookup_enabled = "unrestricted", True   # the old inputs: ignored
    with patch("kriya.workflow.self_correction.resolve_maven_class",
               return_value={"groupId": "g", "artifactId": "a", "version": "1"}) as lookup:
        result = await run_self_correction_loop(llm=llm, worktree_path="/nonexistent", validator=validator, files_in_scope=[],
                                                compile_error_output="err", active_code_context="")
    assert (lookup.call_count > 0) is expected, label
    if not expected:
        assert "disabled by the egress policy" in result.transcript[0]["result"]


# ---------------------------------------------------------------- no alternate decision path

def test_only_the_egress_policy_module_reads_web_lookup_enabled():
    """Every consumer asks policy/egress.py; a second reader of the flag is a second authority."""
    readers = []
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("config/") or relative == "policy/egress.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        readers += [relative for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == "web_lookup_enabled"]
    assert readers == []


def test_the_resolver_is_only_reachable_through_a_caller_supplied_decision():
    """kriya/tools/resolver.py's lookup defaults to allowed; every call site must pass the decision explicitly."""
    offenders = []
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if relative == "tools/resolver.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if name in ("resolve_maven_class", "enrich_java_compiler_errors") and \
                        "allow_external_lookup" not in {kw.arg for kw in node.keywords}:
                    offenders.append(f"{relative}:{node.lineno}")
    assert offenders == []


def test_every_validator_built_where_the_full_configuration_is_in_scope_is_handed_the_decision():
    """The callers that hold ``<...>.config.autonomy`` also hold ``config.search`` / ``config.knowledge``: they must pass the decision
    (a missing kwarg is a silent default-deny, i.e. a quiet loss of the lookup for an operator who enabled it)."""
    missing, seen = [], 0
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "PolymorphicValidator":
                keywords = {kw.arg: kw.value for kw in node.keywords}
                autonomy = keywords.get("autonomy_cfg")
                if isinstance(autonomy, ast.Attribute) and ast.unparse(autonomy).endswith("config.autonomy"):
                    seen += 1
                    if "dependency_lookup_allowed" not in keywords:
                        missing.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    assert seen >= 5 and missing == []


@pytest.mark.parametrize("label, cfg, expected", CASES, ids=IDS)
def test_the_candidate_gate_validator_carries_the_decision(tmp_path, label, cfg, expected):
    """The validator every candidate attempt compiles with (kriya/workflow/gate_orchestrator.py::_new_candidate_validator)."""
    from types import SimpleNamespace

    from test_prv17_preflight import _minimal_ctx

    from kriya.core.kernel import Kernel
    from kriya.workflow import gate_orchestrator

    ctx = _minimal_ctx(tmp_path, kernel=Kernel(config=cfg), migration_resolution=None)
    deps = SimpleNamespace(_ctx_toolchain_declaration_mutable=lambda _ctx: False)
    assert gate_orchestrator._new_candidate_validator(deps, ctx).dependency_lookup_allowed is expected, label
