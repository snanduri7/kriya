"""PRD-034 F9b: the EXTERNAL_PRIVATE_FIXTURE_REQUIRED disposition and the private-fixture certification gate (scripts/certification_private_gate.py).

A mandatory test whose fixture may not be redistributed to a hosted runner is not an unexpected skip, not an allowlist entry and never a pass: hosted certification can reach at best
HOSTED_PASS_PRIVATE_FIXTURE_REQUIRED, and only a private evidence record that binds to the SAME Kriya revision/tree/config and the contracted fixture identity makes the final verdict
CERTIFIED. Every check fails closed. The records hold digests, ids, outcomes and timestamps - never fixture source.
"""
import ast
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "tests" / "certification" / "sample_private_fixture.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load("certification_private_gate", ROOT / "scripts" / "certification_private_gate.py")
summary_module = _load("certification_summary_pf", ROOT / "scripts" / "certification_summary.py")


# ------------------------------------------------------------------------------------------------------------------ the typed skip disposition

def _run_sample(tmp_path, *args):
    report = tmp_path / "cert"
    completed = subprocess.run([sys.executable, "-m", "pytest", str(SAMPLE), "-p", "no:cacheprovider", "-q", "--certification", "--certification-report", str(report), *args],
                               cwd=str(ROOT), capture_output=True, text=True, timeout=300, env={**os.environ, "KRIYA_CERTIFICATION": "", "TMPDIR": str(tmp_path)})
    return completed, json.loads((report / "skips.json").read_text())


def test_a_skip_declared_external_private_fixture_is_recorded_under_its_gate_and_is_not_unexpected(tmp_path):
    completed, skips = _run_sample(tmp_path, "-k", "needs_the_private_fixture")
    assert completed.returncode == 0, completed.stdout[-1500:]
    assert skips["unexpected"] == 0 and skips["skipped"] == 1
    assert [(item["nodeid"].split("::")[-1], item["gate"]) for item in skips["external_private_fixture"]] == [("test_needs_the_private_fixture", "SAMPLE-GATE")]
    assert skips["skips"][0]["allowlisted"] is None, "an external private fixture is NOT an allowlist entry"


def test_an_ordinary_skip_or_a_marker_without_a_gate_excuses_nothing(tmp_path):
    ordinary, skips_ordinary = _run_sample(tmp_path / "a", "-k", "ordinary_skip")
    assert ordinary.returncode == 1 and skips_ordinary["unexpected"] == 1 and skips_ordinary["external_private_fixture"] == []
    bare, skips_bare = _run_sample(tmp_path / "b", "-k", "marker_without_a_gate")
    assert bare.returncode == 1 and skips_bare["unexpected"] == 1 and skips_bare["external_private_fixture"] == []
    assert "UNEXPECTED SKIP in certification mode" in bare.stdout


def _census(*records):
    return {"tests": [{"id": i, "markers": [], "skip_reasons": [], "docker": False, "requires_platform": None, "not_applicable_on": None, "external_private_fixture": g} for i, g in records]}


def test_the_execution_audit_lists_the_private_fixture_test_as_pending_never_as_a_pass_and_a_failure_is_still_a_failure():
    from _certification_collection import _junit_key, executed

    node = "tests/t.py::test_private"
    census = _census((node, "G"))
    for status in ("skipped", "passed", "missing"):
        report = executed(census, {_junit_key(node): status} if status != "missing" else {}, floors={})
        assert report["ok"] and report["private_fixture_pending"] == [{"id": node, "gate": "G", "local_outcome": status}], status
    failed = executed(census, {_junit_key(node): "failed"}, floors={})
    assert not failed["ok"] and any("private fixture gate G" in problem for problem in failed["problems"])


# ------------------------------------------------------------------------------------------------------------------ the hosted summary

def _hosted_dir(tmp_path, *, status_pending=True, revision="r" * 40, problems=()):
    out = tmp_path / "hosted"
    out.mkdir(parents=True, exist_ok=True)
    stages = {name: {"stage": name, "status": "PASS"} for name in summary_module.MANDATORY}
    stages["doctor"] = {"stage": "doctor", "status": "RECORDED"}
    (out / "stages.jsonl").write_text("".join(json.dumps(record) + "\n" for record in stages.values()))
    (out / "pytest").mkdir(exist_ok=True)
    (out / "pytest" / "skips.json").write_text(json.dumps({"skipped": 1, "unexpected": 0}))
    (out / "doctor.json").write_text(json.dumps({"checks": [], "production_ready": True}))
    (out / "release-identity.json").write_text(json.dumps({"digest": "d", "kriya": {"revision": revision, "dirty": False}}))
    (out / "tier-execution.json").write_text(json.dumps({"private_fixture_pending": [{"id": "tests/x.py::test_private", "gate": "G", "local_outcome": "skipped"}] if status_pending else []}))
    return out


def test_the_hosted_summary_reads_hosted_pass_private_fixture_required_and_never_certified_while_one_is_pending(tmp_path):
    summary = summary_module.summarize(str(_hosted_dir(tmp_path)))
    assert summary["status"] == "HOSTED_PASS_PRIVATE_FIXTURE_REQUIRED" and summary["problems"] == []
    assert summary["private_fixture_pending"][0]["gate"] == "G"
    assert "EXTERNAL_PRIVATE_FIXTURE_REQUIRED (G) - NOT A PASS" in summary_module.render({**summary, "stages": {}, "tiers": {}, "doctor": None, "environment": {}, "release_identity": {}})
    none_pending = summary_module.summarize(str(_hosted_dir(tmp_path / "n", status_pending=False)))
    assert none_pending["status"] == "CERTIFIED", "with nothing pending the same evidence is CERTIFIED (the pending list is what withholds it)"


def test_the_summary_exit_code_for_hosted_pass_private_fixture_required_is_its_own(tmp_path):
    assert summary_module.main(str(_hosted_dir(tmp_path))) == 4
    assert summary_module.main(str(_hosted_dir(tmp_path / "n", status_pending=False))) == 0


# ------------------------------------------------------------------------------------------------------------------ the contract and the marker agree

def test_the_contract_names_exactly_the_tests_that_carry_the_marker():
    contract = json.loads((ROOT / "tests" / "certification" / "private_fixtures.json").read_text())
    marked = {}
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "external_private_fixture" and decorator.args):
                        marked.setdefault(decorator.args[0].value, []).append(f"tests/{path.name}::{node.name}")
    assert {name: sorted(nodes) for name, nodes in marked.items()} == {name: sorted(spec["nodeids"]) for name, spec in contract["gates"].items()}
    for spec in contract["gates"].values():
        assert len(spec["bundle_sha256"]) == 64 and len(spec["fixture_commit"]) == 40 and len(spec["target_sha256"]) == 64


def test_the_recovered_fixture_identity_in_the_contract_is_the_one_scenario_01_pins():
    contract = json.loads((ROOT / "tests" / "certification" / "private_fixtures.json").read_text())["gates"]["PRD034-F9B-SCENARIO01"]
    env = dict(line.split("=", 1) for line in (ROOT / "demo-v2" / "fixtures" / "brownfield-spring" / "fixture.env").read_text().splitlines() if "=" in line and not line.startswith("#"))
    assert contract["fixture_commit"] == env["FIXTURE_COMMIT"] and contract["target_sha256"] == env["FIXTURE_TARGET_SHA256"] and contract["target_file"] == env["FIXTURE_TARGET_FILE"]


# ------------------------------------------------------------------------------------------------------------------ the gate, end to end on a synthetic fixture

def _g(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "-c", "maintenance.auto=false", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


SECRET_LINE = "class Secret { /* PRIVATE FIXTURE SOURCE: must never reach evidence */ }"


@pytest.fixture
def world(tmp_path):
    """A throw-away Kriya checkout carrying only the gate's contract/config files and a one-test gate, plus a tiny fixture repository and its bundle."""
    fixture = tmp_path / "fixture-src"
    fixture.mkdir()
    _g(fixture, "init", "-q")
    (fixture / "Target.java").write_text(SECRET_LINE + "\n")
    _g(fixture, "add", "-A")
    _g(fixture, "commit", "-qm", "fixture")
    commit = _g(fixture, "rev-parse", "HEAD")
    bundle = tmp_path / "fixture.bundle"
    _g(fixture, "branch", "-M", "main")
    _g(fixture, "bundle", "create", str(bundle), "main")
    target_sha = gate._sha256_bytes((SECRET_LINE + "\n").encode())
    checkout = tmp_path / "kriya"
    checkout.mkdir()
    _g(checkout, "init", "-q")
    for name in gate.CONFIG_FILES:
        (checkout / name).parent.mkdir(parents=True, exist_ok=True)
        (checkout / name).write_text('[tool.sample]\nname = "kriya"\n' if name == "pyproject.toml" else f"{name}\n")
    node = "tests/test_private_sample.py::test_uses_the_fixture"
    contract = {"contract": "kriya.private-fixture-gates.v1", "gates": {"G": {
        "nodeids": [node], "fixture_commit": commit, "fixture_tree": _g(fixture, "rev-parse", f"{commit}^{{tree}}"), "bundle_sha256": gate._sha256_file(str(bundle)),
        "target_file": "Target.java", "target_sha256": target_sha}}}
    (checkout / gate.CONTRACT_PATH).write_text(json.dumps(contract, indent=2))
    (checkout / "tests" / "test_private_sample.py").write_text(
        "import os, pathlib, subprocess\n\ndef test_uses_the_fixture():\n    src = pathlib.Path(os.environ['DEMO_V2_BASELINE_SRC'])\n    assert (src / '.git').is_dir()\n"
        f"    subprocess.run(['git', '-C', str(src), 'cat-file', '-e', '{commit}^{{commit}}'], check=True)\n")   # like reset_demo.sh: the pinned commit is in the source repository
    _g(checkout, "add", "-A")
    _g(checkout, "commit", "-qm", "kriya")
    return {"tmp": tmp_path, "checkout": str(checkout), "bundle": str(bundle), "contract": contract, "node": node, "head": _g(checkout, "rev-parse", "HEAD")}


def _hosted_for(world, **kwargs):
    out = _hosted_dir(world["tmp"], revision=world["head"], **kwargs)
    (out / "tier-execution.json").write_text(json.dumps({"private_fixture_pending": [{"id": world["node"], "gate": "G", "local_outcome": "skipped"}]}))
    (out / "environment.json").write_text(json.dumps({"git_revision": world["head"]}))
    summary = summary_module.summarize(str(out))
    (out / "certification-summary.json").write_text(json.dumps(summary))
    return out


def _record(world):
    return gate.run_gate("G", world["bundle"], root=world["checkout"])


def test_the_private_gate_runs_the_node_against_the_verified_fixture_and_the_record_holds_no_fixture_source(world):
    record = _record(world)
    assert record["results"] == {world["node"]: "passed"} and record["pytest_exit_code"] == 0
    assert record["kriya"]["head"] == world["head"] and record["fixture"]["bundle_sha256"] == world["contract"]["gates"]["G"]["bundle_sha256"]
    assert record["evidence_digest"] == gate._evidence_digest(record)
    text = json.dumps(record)
    assert "PRIVATE FIXTURE SOURCE" not in text and "class Secret" not in text, "the record must not carry fixture source"


def test_the_final_verdict_is_certified_only_when_the_record_binds_to_the_hosted_run(world):
    verdict = gate.verify_binding(str(_hosted_for(world)), _record(world), root=world["checkout"])
    assert verdict["status"] == "CERTIFIED" and verdict["problems"] == []


def _tampered(world, mutate):
    record = copy.deepcopy(_record(world))
    mutate(record)
    record["evidence_digest"] = gate._evidence_digest(record)   # re-sealed: the CHECK must catch it, not the digest
    return gate.verify_binding(str(_hosted_for(world)), record, root=world["checkout"])


@pytest.mark.parametrize("name, mutate, expected", [
    ("different kriya head", lambda r: r["kriya"].__setitem__("head", "0" * 40), "private record Kriya head"),
    ("different kriya tree", lambda r: r["kriya"].__setitem__("tree", "0" * 40), "Kriya tree differs"),
    ("different config identity", lambda r: r["kriya"].__setitem__("config_identity", "0" * 64), "config identity differs"),
    ("different fixture commit", lambda r: r["fixture"].__setitem__("fixture_commit", "0" * 40), "fixture fixture_commit"),
    ("different bundle digest", lambda r: r["fixture"].__setitem__("bundle_sha256", "0" * 64), "fixture bundle_sha256"),
    ("different target digest", lambda r: r["fixture"].__setitem__("target_sha256", "0" * 64), "fixture target_sha256"),
    ("scenario failed", lambda r: r["results"].__setitem__(next(iter(r["results"])), "failed"), "scenario result is not PASSED"),
    ("scenario skipped", lambda r: r["results"].__setitem__(next(iter(r["results"])), "skipped"), "scenario result is not PASSED"),
    ("node set differs", lambda r: r["results"].__setitem__("tests/other.py::t", "passed"), "recorded node ids differ"),
    ("pytest exit code", lambda r: r.__setitem__("pytest_exit_code", 1), "exit code"),
    ("unknown gate", lambda r: r.__setitem__("gate", "NOPE"), "not in the contract"),
    ("missing timestamps", lambda r: r.pop("started"), "missing started"),
    ("wrong record contract", lambda r: r.__setitem__("contract", "x"), "wrong contract id"),
], ids=lambda value: value if isinstance(value, str) and " " in value else None)
def test_every_binding_failure_is_refused_even_when_the_record_is_re_sealed(world, name, mutate, expected):
    verdict = _tampered(world, mutate)
    assert verdict["status"] == "NOT_CERTIFIED", name
    assert any(expected in problem for problem in verdict["problems"]), (name, verdict["problems"])


def test_an_unsealed_edit_is_caught_by_the_evidence_digest(world):
    record = _record(world)
    record["results"][world["node"]] = "passed"
    record["kriya"]["head"] = "1" * 40
    verdict = gate.verify_binding(str(_hosted_for(world)), record, root=world["checkout"])
    assert verdict["status"] == "NOT_CERTIFIED" and any("evidence digest" in problem for problem in verdict["problems"])


def test_hosted_evidence_must_itself_be_a_hosted_pass_with_the_pending_entry_and_a_clean_revision(world):
    record = _record(world)
    nothing_pending = _hosted_for(world)
    (nothing_pending / "tier-execution.json").write_text(json.dumps({"private_fixture_pending": []}))
    (nothing_pending / "certification-summary.json").write_text(json.dumps(summary_module.summarize(str(nothing_pending))))
    verdict = gate.verify_binding(str(nothing_pending), record, root=world["checkout"])
    assert verdict["status"] == "NOT_CERTIFIED" and any("not HOSTED_PASS_PRIVATE_FIXTURE_REQUIRED" in p or "nothing to discharge" in p for p in verdict["problems"])
    failing = _hosted_for(world)
    (failing / "stages.jsonl").write_text("")      # every mandatory stage missing -> NOT_CERTIFIED hosted
    (failing / "certification-summary.json").write_text(json.dumps(summary_module.summarize(str(failing))))
    assert gate.verify_binding(str(failing), record, root=world["checkout"])["status"] == "NOT_CERTIFIED"
    other = _hosted_for(world)
    (other / "release-identity.json").write_text(json.dumps({"digest": "d", "kriya": {"revision": "9" * 40, "dirty": False}}))
    (other / "environment.json").write_text(json.dumps({"git_revision": "9" * 40}))
    verdict = gate.verify_binding(str(other), record, root=world["checkout"])
    assert verdict["status"] == "NOT_CERTIFIED" and any("Kriya head" in problem for problem in verdict["problems"])
    dirty = _hosted_for(world)
    (dirty / "release-identity.json").write_text(json.dumps({"digest": "d", "kriya": {"revision": world["head"], "dirty": True}}))
    assert gate.verify_binding(str(dirty), record, root=world["checkout"])["status"] == "NOT_CERTIFIED"


def test_the_fixture_is_refused_when_its_identity_differs_before_anything_runs(world, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _g(other, "init", "-q")
    (other / "Target.java").write_text("different\n")
    _g(other, "add", "-A")
    _g(other, "commit", "-qm", "other")
    _g(other, "branch", "-M", "main")
    wrong = tmp_path / "wrong.bundle"
    _g(other, "bundle", "create", str(wrong), "main")
    with pytest.raises(gate.GateError, match="bundle sha256"):
        gate.run_gate("G", str(wrong), root=world["checkout"])
    with pytest.raises(gate.GateError, match="not found"):
        gate.run_gate("G", str(tmp_path / "absent.bundle"), root=world["checkout"])
    tampered = copy.deepcopy(world["contract"]["gates"]["G"])
    tampered["target_sha256"] = "0" * 64
    with pytest.raises(gate.GateError, match="target file"):
        gate.materialize_fixture(world["bundle"], tampered, str(tmp_path / "x"))
    tampered = copy.deepcopy(world["contract"]["gates"]["G"])
    tampered["fixture_tree"] = "0" * 40
    with pytest.raises(gate.GateError, match="fixture tree"):
        gate.materialize_fixture(world["bundle"], tampered, str(tmp_path / "y"))


def test_the_private_gate_refuses_a_dirty_checkout_and_reports_a_failing_scenario(world):
    (Path(world["checkout"]) / gate.CONFIG_FILES[0]).write_text("changed\n")
    with pytest.raises(gate.GateError, match="dirty"):
        gate.run_gate("G", world["bundle"], root=world["checkout"])
    _g(world["checkout"], "checkout", "--", gate.CONFIG_FILES[0])
    (Path(world["checkout"]) / "tests" / "test_private_sample.py").write_text("def test_uses_the_fixture():\n    assert False\n")
    _g(world["checkout"], "commit", "-qam", "break the scenario")
    record = gate.run_gate("G", world["bundle"], root=world["checkout"])
    assert record["results"] == {world["node"]: "failed"} and record["pytest_exit_code"] != 0


def test_the_hosted_workflow_accepts_only_the_private_fixture_status_as_a_hosted_pass():
    """The Certify step turns exactly exit 4 (HOSTED_PASS_PRIVATE_FIXTURE_REQUIRED) into a green step with a NOT-CERTIFIED notice; 1, 3 and anything else still fail it."""
    import yaml

    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    [step] = [s for s in workflow["jobs"]["production-certification"]["steps"] if s.get("name") == "Certify"]
    script = step["run"]
    assert script.count('"$rc" = 4') == 1 and "HOSTED PASS - NOT CERTIFIED" in script and script.rstrip().endswith('exit "$rc"')
    for other in ("= 0", "= 1", "= 3"):
        assert f'"$rc" {other}' not in script
    bash = shutil.which("bash")
    for code, expected in ((0, 0), (1, 1), (3, 3), (4, 0)):
        fake = f"certify() {{ return {code}; }}\n" + script.replace("scripts/certify.sh certification-out/ci", "certify")
        assert subprocess.run([bash, "-e", "-c", fake], capture_output=True, text=True, check=False, timeout=30).returncode == expected, code
