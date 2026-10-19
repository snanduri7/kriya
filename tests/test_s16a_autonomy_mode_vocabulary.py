"""S16a (OI-130, AUTONOMY-MODE-FAIL-OPEN): ``autonomy.mode`` is a closed vocabulary owned by the config authority.

Derived vocabulary (preflight, every production comparison site, schema/defaults, CLI, environment, KUP, GUI, docs and
tests): ``human-in-the-loop`` (the default; the only value any production site compares against) and ``guardrails``
(documented; every human-in-the-loop comparison's ``else`` branch). ``full-autonomy`` is produced and consumed nowhere;
``autonomous`` was a test-only shortcut. Measured defect: any other string was accepted at every ingress (a durable
approval of ``"human-in-the-loop "`` - trailing space, invisible in review - loaded, and every human-in-the-loop
decision then compared False: no source review before execution, no change review, a planned tool treated as
reviewed-elsewhere); null and non-strings were refused only by an untyped pydantic error AFTER an approval had been
written for them.

Characterization FIRST (S1 method): pins of the governed behaviour that must NOT change (green before and after) and
EXPECTED-TO-FLIP pins written as the corrected behaviour, strict-xfail at the characterization commit (each failing on
the unfixed bytes because the value was ACCEPTED or refused without a reason code); the correction removed the marks.
The expected reason codes are literals here on purpose: the pins fail on the unfixed bytes for the real reason (the
behaviour), not because a new symbol cannot be imported.
"""
import json
import os
import subprocess
import sys

import pytest
import yaml
from _strict_doubles import strict_config

from kriya.config import authority_approval as aa
from kriya.config.authority import ConfigAuthorityViolation, ConfigSource, FieldClassification, classify_field
from kriya.config.config import AppConfig, AutonomyConfig, load_config, resolve_config_state

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HITL = "human-in-the-loop"
GUARDRAILS = "guardrails"
VALID = (GUARDRAILS, HITL)

NULL, NOT_A_STRING, EMPTY = "AUTONOMY_MODE_NULL", "AUTONOMY_MODE_NOT_A_STRING", "AUTONOMY_MODE_EMPTY"
NOT_CANONICAL, UNKNOWN = "AUTONOMY_MODE_NOT_CANONICAL", "AUTONOMY_MODE_UNKNOWN"

# (value, expected reason code): unknown values, a typo of each valid value, case and whitespace variants of each, empty,
# null and non-strings.
INVALID = [
    ("full-autonomy", UNKNOWN), ("autonomous", UNKNOWN), ("bogus", UNKNOWN), ("human_in_the_loop", UNKNOWN),
    ("human-in-the-lop", UNKNOWN), ("guardrail", UNKNOWN), ("guard-rails", UNKNOWN),
    ("Human-in-the-loop", NOT_CANONICAL), ("HUMAN-IN-THE-LOOP", NOT_CANONICAL), ("Guardrails", NOT_CANONICAL), ("GUARDRAILS", NOT_CANONICAL),
    ("human-in-the-loop ", NOT_CANONICAL), (" human-in-the-loop", NOT_CANONICAL), ("\thuman-in-the-loop", NOT_CANONICAL),
    ("guardrails\n", NOT_CANONICAL), (" guardrails ", NOT_CANONICAL),
    ("", EMPTY), ("   ", EMPTY),
    (None, NULL),
    (1, NOT_A_STRING), (0, NOT_A_STRING), (True, NOT_A_STRING), (False, NOT_A_STRING), (1.5, NOT_A_STRING),
    (["guardrails"], NOT_A_STRING), ({"mode": "guardrails"}, NOT_A_STRING),
]
STRING_INVALID = [(v, c) for v, c in INVALID if isinstance(v, str)]
# one representative per reason code, for the expensive ingress paths (CLI, KUP, fresh interpreter)
ONE_PER_CODE = [("human-in-the-lop", UNKNOWN), ("human-in-the-loop ", NOT_CANONICAL), ("", EMPTY), (None, NULL), (1, NOT_A_STRING)]


@pytest.fixture(autouse=True)
def _homes(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "_authority_home"))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)


def _ids(cases):
    return [repr(v) for v, _ in cases]


def _workspace(tmp_path, monkeypatch, autonomy, name="ws"):
    ws = tmp_path / name
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    if autonomy is not ...:
        (ws / "kriya.yaml").write_text(yaml.safe_dump({"autonomy": autonomy}))
    monkeypatch.chdir(ws)
    return ws


def _refusal(fn):
    """'ACCEPTED <value>' or the refusal's (type name, reason_code)."""
    try:
        value = fn()
    except Exception as error:  # noqa: BLE001 - the pin reports whatever shape the refusal has
        return (type(error).__name__, getattr(error, "reason_code", None))
    return f"ACCEPTED {value!r}"


def _typed(fn, code):
    outcome = _refusal(fn)
    assert outcome == ("AutonomyModeError", code), outcome


def _approve_mode(ws, value, path=None):
    """A digest-valid SEC-009 approval of exactly ``autonomy.mode = value`` from this workspace's kriya.yaml, built the way
    ``kriya authority approve`` builds one (a stale approval written before the vocabulary was closed)."""
    violation = ConfigAuthorityViolation("autonomy.mode", ConfigSource.AUTO_DISCOVERED_CWD, FieldClassification.SECURITY_AUTHORITY,
                                         "classification security_authority requires a trusted or explicitly authorized source")
    artifact = aa.build_approval_artifact([violation], {"autonomy": {"mode": value}}, os.path.realpath(str(ws)))
    target = path or aa.default_local_approval_path(os.path.realpath(str(ws)))
    aa.save_approval_artifact(target, artifact)
    return target


# =========================================================================== governed behaviour that must NOT change

def test_absent_key_is_the_documented_default_everywhere(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch, ...)
    assert load_config().autonomy.mode == HITL
    assert AppConfig().autonomy.mode == HITL and AutonomyConfig().mode == HITL
    assert strict_config().autonomy.mode == HITL
    with open(os.path.join(REPO_ROOT, "kriya", "config", "default_config.yaml"), encoding="utf-8") as handle:
        assert yaml.safe_load(handle)["autonomy"]["mode"] == HITL
    _workspace(tmp_path, monkeypatch, {"generation_gate_reserve_seconds": 77}, name="ws2")  # an autonomy section without the key (a repository-safe leaf)
    cfg = load_config()
    assert cfg.autonomy.mode == HITL and cfg.autonomy.generation_gate_reserve_seconds == 77


def test_autonomy_mode_stays_security_authority():
    assert classify_field("autonomy", "mode") is FieldClassification.SECURITY_AUTHORITY


@pytest.mark.parametrize("mode", VALID)
def test_a_valid_repository_mode_still_needs_the_operator_approval(tmp_path, monkeypatch, mode):
    _workspace(tmp_path, monkeypatch, {"mode": mode})
    state = resolve_config_state()
    assert [v.field_path for v in state.violations] == ["autonomy.mode"]
    assert state.config_dict["autonomy"]["mode"] == mode
    from kriya.config.authority import ConfigAuthorityError
    with pytest.raises(ConfigAuthorityError):
        load_config()


@pytest.mark.parametrize("mode", VALID)
def test_every_valid_value_loads_through_the_approval_path(tmp_path, monkeypatch, mode):
    ws = _workspace(tmp_path, monkeypatch, {"mode": mode})
    _approve_mode(ws, mode)
    assert load_config().autonomy.mode == mode


@pytest.mark.parametrize("mode", VALID)
def test_every_valid_value_loads_through_a_trust_file(tmp_path, monkeypatch, mode):
    ws = _workspace(tmp_path, monkeypatch, {"mode": mode})
    trust = _approve_mode(ws, mode, path=str(tmp_path / "trust.json"))
    assert load_config(trust_file=trust).autonomy.mode == mode


@pytest.mark.parametrize("inside", [True, False], ids=["inside", "outside"])
@pytest.mark.parametrize("mode", VALID)
def test_every_valid_value_resolves_from_an_explicit_config(tmp_path, monkeypatch, mode, inside):
    ws = _workspace(tmp_path, monkeypatch, ...)
    path = (ws if inside else tmp_path) / "explicit.yaml"
    path.write_text(yaml.safe_dump({"autonomy": {"mode": mode}}))
    assert resolve_config_state(str(path)).config_dict["autonomy"]["mode"] == mode


@pytest.mark.parametrize("mode", VALID)
def test_every_valid_value_is_accepted_programmatically(mode):
    assert AppConfig(autonomy={"mode": mode}).autonomy.mode == mode
    assert AppConfig.model_validate({"autonomy": {"mode": mode}}).autonomy.mode == mode
    assert AutonomyConfig(mode=mode).mode == mode
    assert strict_config(autonomy={"mode": mode}).autonomy.mode == mode
    cfg = AppConfig()
    cfg.autonomy.mode = mode
    assert cfg.autonomy.mode == mode
    assert AppConfig().autonomy.model_copy(update={"mode": mode}).mode == mode
    assert AppConfig().model_copy(update={"autonomy": AutonomyConfig(mode=mode)}).autonomy.mode == mode
    assert AppConfig(autonomy={"mode": mode}).model_copy(deep=True).autonomy.mode == mode


def test_project_setup_profile_is_unchanged():
    from kriya.application import project_init

    assert project_init._AUTONOMY == {"mode": HITL, "web_lookup_enabled": False, "web_lookup_auto_approve": False, "run_verification_enabled": True,
                                      "self_correction_loop_enabled": False, "generation_time_budget_seconds": 900}
    assert project_init.recommended_profile()["autonomy"]["mode"] == HITL


def test_the_consumers_decide_the_same_for_each_valid_value():
    from kriya.application.run import _interaction
    from kriya.policy.approval import defer_execution_until_source_review
    from kriya.tools.tool import ModelToolInvocation

    assert defer_execution_until_source_review(True, HITL, True) is True
    assert defer_execution_until_source_review(True, GUARDRAILS, True) is False
    assert ModelToolInvocation(autonomy_mode=HITL).reviews_every_change is True
    assert ModelToolInvocation(autonomy_mode=GUARDRAILS).reviews_every_change is False
    assert ModelToolInvocation(autonomy_mode=None).reviews_every_change is True  # unreadable = the strictest (kept)
    assert _interaction(AppConfig(autonomy={"mode": HITL}))["approvals"] == "change_review"
    shown = _interaction(AppConfig(autonomy={"mode": GUARDRAILS}))
    assert shown["approvals"] == "configured_mode" and shown["autonomy_mode"] == GUARDRAILS


# =========================================================================== EXPECTED TO FLIP (the measured defect)

@pytest.mark.parametrize("value, code", INVALID, ids=_ids(INVALID))
def test_resolution_refuses_an_invalid_mode_before_any_authority_comparison(tmp_path, monkeypatch, value, code):
    """resolve_config_state is what load_config, `kriya authority inspect|approve` and the desktop's configuration
    operations share: the typed error comes from it, before compute_violations, so there is nothing to approve."""
    _workspace(tmp_path, monkeypatch, {"mode": value})
    _typed(resolve_config_state, code)


@pytest.mark.parametrize("inside", [True, False], ids=["inside", "outside"])
@pytest.mark.parametrize("value, code", ONE_PER_CODE, ids=_ids(ONE_PER_CODE))
def test_an_explicit_config_is_refused_the_same_way(tmp_path, monkeypatch, value, code, inside):
    ws = _workspace(tmp_path, monkeypatch, ...)
    path = (ws if inside else tmp_path) / "explicit.yaml"
    path.write_text(yaml.safe_dump({"autonomy": {"mode": value}}))
    _typed(lambda: resolve_config_state(str(path)), code)
    _typed(lambda: load_config(str(path)), code)


@pytest.mark.parametrize("value, code", INVALID, ids=_ids(INVALID))
def test_an_approval_does_not_make_an_invalid_mode_valid(tmp_path, monkeypatch, value, code):
    """A digest-valid durable approval of the invalid value (stale, granted before the vocabulary was closed) is never
    consulted: the value is a validation error first."""
    ws = _workspace(tmp_path, monkeypatch, {"mode": value})
    _approve_mode(ws, value)
    _typed(load_config, code)


@pytest.mark.parametrize("value, code", ONE_PER_CODE, ids=_ids(ONE_PER_CODE))
def test_a_trust_file_does_not_make_an_invalid_mode_valid(tmp_path, monkeypatch, value, code):
    ws = _workspace(tmp_path, monkeypatch, {"mode": value})
    trust = _approve_mode(ws, value, path=str(tmp_path / "trust.json"))
    _typed(lambda: load_config(trust_file=trust), code)
    monkeypatch.setenv("KRIYA_TRUST_FILE", trust)
    _typed(load_config, code)


@pytest.mark.parametrize("value, code", INVALID, ids=_ids(INVALID))
def test_programmatic_construction_refuses_an_invalid_mode(value, code):
    _typed(lambda: AppConfig(autonomy={"mode": value}), code)
    _typed(lambda: AppConfig.model_validate({"autonomy": {"mode": value}}), code)
    _typed(lambda: AutonomyConfig(mode=value), code)
    _typed(lambda: strict_config(autonomy={"mode": value}), code)


@pytest.mark.parametrize("value, code", INVALID, ids=_ids(INVALID))
def test_assignment_refuses_an_invalid_mode_and_keeps_the_old_value(value, code):
    cfg = AppConfig(autonomy={"mode": GUARDRAILS})

    def assign():
        cfg.autonomy.mode = value
    _typed(assign, code)
    assert cfg.autonomy.mode == GUARDRAILS


@pytest.mark.parametrize("value, code", INVALID, ids=_ids(INVALID))
def test_model_copy_updates_refuse_an_invalid_mode(value, code):
    _typed(lambda: AppConfig().autonomy.model_copy(update={"mode": value}), code)
    _typed(lambda: AppConfig().model_copy(update={"autonomy": {"mode": value}}), code)


@pytest.mark.parametrize("value, code", ONE_PER_CODE, ids=_ids(ONE_PER_CODE))
def test_the_cli_refuses_typed_and_approves_nothing(tmp_path, monkeypatch, value, code):
    from click.testing import CliRunner

    from kriya.cli import main
    ws = _workspace(tmp_path, monkeypatch, {"mode": value})
    _approve_mode(ws, value)  # even a pre-existing approval of it
    store = aa.default_local_approval_path(os.path.realpath(str(ws)))
    before = open(store, "rb").read()
    for argv in (["config"], ["generate", "--help"], ["authority", "inspect"], ["authority", "approve", "--confirm"]):
        result = CliRunner().invoke(main, argv, catch_exceptions=False)
        assert result.exit_code == 1, (argv, result.exit_code, result.output)
        assert code in result.output and "autonomy.mode" in result.output, (argv, result.output)
    assert open(store, "rb").read() == before  # `approve` wrote nothing


@pytest.mark.parametrize("value, code", ONE_PER_CODE, ids=_ids(ONE_PER_CODE))
def test_the_desktop_configuration_paths_refuse_typed(tmp_path, monkeypatch, value, code):
    from kriya.config.service import CONFIG_LOAD_FAILED, ConfigService
    from kriya.kup import serve
    from kriya.kup.cli_ops import run_kup_operation
    ws = _workspace(tmp_path, monkeypatch, {"mode": value})
    _approve_mode(ws, value)
    resolved = ConfigService().resolve_here()
    assert resolved.app is None and resolved.failure is not None
    assert resolved.failure["code"] == CONFIG_LOAD_FAILED and code in resolved.failure["message"], resolved.failure
    status = run_kup_operation(None, {"operation": "configuration.approval_status", "workspace": str(ws)})
    assert status["error"]["code"] == CONFIG_LOAD_FAILED and code in status["error"]["message"], status
    approve = run_kup_operation(None, {"operation": "configuration.approve", "workspace": str(ws), "fingerprint": "sha256:" + "0" * 64})
    assert approve["error"]["code"] == CONFIG_LOAD_FAILED and code in approve["error"]["message"], approve
    line = json.loads(serve.answer({"id": "r1", "operation": "execution.context", "arguments": {"workspace": str(ws)}}, None, None))
    assert line["envelope"]["error"]["code"] == "CONFIG_LOAD_FAILED" and code in line["envelope"]["error"]["message"], line


_RUNNER = r'''
import json, os, sys
out_path, *argv = sys.argv[1:]
import kriya.cli
watched = tuple(p for p in os.environ["S16A_WATCH"].split(os.pathsep) if p)
events = []
def hook(event, args):
    if event in ("open", "os.listdir", "os.scandir", "os.mkdir", "os.remove", "os.rename", "subprocess.Popen", "sqlite3.connect",
                 "shutil.rmtree", "os.walk", "socket.connect"):
        target = str(args[0]) if args else ""
        if event in ("subprocess.Popen", "sqlite3.connect", "socket.connect") or target.startswith(watched):
            mode = args[1] if event == "open" and len(args) > 1 else None
            events.append([event, target, str(mode)])
sys.addaudithook(hook)
code = None
try:
    kriya.cli.main.main(args=argv, prog_name="kriya", standalone_mode=True)
except SystemExit as stop:
    code = stop.code
with open(out_path, "w") as handle:
    json.dump({"code": code, "events": events}, handle)
'''


@pytest.mark.parametrize("argv", [["config"], ["generate", "--help"], ["authority", "approve", "--confirm"]],
                         ids=["config", "generate", "approve"])
def test_a_malformed_mode_stops_before_anything_runs_or_is_read(tmp_path, argv):
    """Fresh interpreter, audit hook: with an approved ``human-in-the-loop `` (trailing space) the entry refuses typed and
    the only workspace/home file it touched is kriya.yaml, read; no process, database, socket or write."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "kriya.yaml").write_text(yaml.safe_dump({"autonomy": {"mode": "human-in-the-loop "}}))
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("KRIYA_")}
    env.update(PYTHONPATH=REPO_ROOT, HOME=str(home), S16A_WATCH=os.pathsep.join((str(ws), str(home))), KRIYA_STATE_DIR=str(home / "state"),
               KRIYA_LOG_DIR=str(home / "logs"), KRIYA_AUTHORITY_HOME=str(home / "auth"), KRIYA_MODEL_RUNTIME_PROBE="0",
               KRIYA_QUALIFICATION_HOME=str(home / "q"), KRIYA_CERTIFICATION_HOME=str(home / "c"))
    violation = ConfigAuthorityViolation("autonomy.mode", ConfigSource.AUTO_DISCOVERED_CWD, FieldClassification.SECURITY_AUTHORITY, "r")
    artifact = aa.build_approval_artifact([violation], {"autonomy": {"mode": "human-in-the-loop "}}, os.path.realpath(str(ws)))
    os.makedirs(home / "auth", exist_ok=True)
    env_store = os.path.join(os.path.realpath(str(home / "auth")), os.path.basename(aa.default_local_approval_path(os.path.realpath(str(ws)))))
    aa.save_approval_artifact(env_store, artifact)
    runner = tmp_path / "runner.py"
    runner.write_text(_RUNNER)
    out = tmp_path / "out.json"
    done = subprocess.run([sys.executable, str(runner), str(out), *argv], cwd=str(ws), env=env, capture_output=True, text=True,
                          timeout=120, stdin=subprocess.DEVNULL, check=False)
    result = json.loads(out.read_text())
    assert result["code"] == 1, (result["code"], done.stderr[-600:], done.stdout[-600:])
    assert NOT_CANONICAL in done.stderr and "autonomy.mode" in done.stderr, done.stderr[-600:]
    touched = {(event, os.path.basename(target)) for event, target, _mode in result["events"]}
    assert touched <= {("open", "kriya.yaml")}, result["events"]
    assert all(mode in ("r", "rb", "None") for event, _t, mode in result["events"] if event == "open"), result["events"]


# =========================================================================== the correction's own contract (S16a)

def test_the_vocabulary_and_the_reason_table_are_closed():
    from kriya.config import authority

    assert authority.AUTONOMY_MODES == (GUARDRAILS, HITL)
    assert authority.AUTONOMY_MODE_DEFAULT == HITL == AutonomyConfig.model_fields["mode"].default
    assert set(authority.AUTONOMY_MODE_REASONS) == {NULL, NOT_A_STRING, EMPTY, NOT_CANONICAL, UNKNOWN}
    for code in authority.AUTONOMY_MODE_REASONS:
        assert getattr(authority, code) == code
    with pytest.raises(ValueError):
        authority.AutonomyModeError("AUTONOMY_MODE_SOMETHING_ELSE")
    assert not issubclass(authority.AutonomyModeError, ValueError)  # pydantic must not wrap it into an untyped error
    from kriya.foundation.errors import KriyaError
    assert issubclass(authority.AutonomyModeError, KriyaError)


@pytest.mark.parametrize("mode", VALID)
def test_the_parser_returns_exactly_the_valid_value(mode):
    from kriya.config.authority import parse_autonomy_mode

    assert parse_autonomy_mode(mode) == mode and type(parse_autonomy_mode(mode)) is str


def test_a_str_subclass_is_not_a_mode():
    from kriya.config.authority import parse_autonomy_mode

    class Sneaky(str):
        pass
    _typed(lambda: parse_autonomy_mode(Sneaky(HITL)), NOT_A_STRING)


@pytest.mark.parametrize("value, code", INVALID, ids=_ids(INVALID))
def test_the_error_names_the_field_and_the_allowed_set_never_the_value(value, code):
    from kriya.config.authority import AutonomyModeError, parse_autonomy_mode

    with pytest.raises(AutonomyModeError) as caught:
        parse_autonomy_mode(value)
    text = str(caught.value)
    assert caught.value.reason_code == code and text.startswith(f"{code}: autonomy.mode ")
    assert "'guardrails', 'human-in-the-loop'" in text and caught.value.field == "autonomy.mode"
    assert caught.value.allowed == (GUARDRAILS, HITL)


def test_a_secret_looking_value_is_never_echoed(tmp_path, monkeypatch):
    secret = "sk-live-0123456789abcdef"
    _workspace(tmp_path, monkeypatch, {"mode": secret})
    from kriya.config.authority import AutonomyModeError
    with pytest.raises(AutonomyModeError) as caught:
        resolve_config_state()
    assert secret not in str(caught.value) and secret not in repr(caught.value.args)


def test_the_case_and_whitespace_hint_names_the_canonical_value():
    from kriya.config.authority import AutonomyModeError, parse_autonomy_mode

    for value, canonical in (("Guardrails", GUARDRAILS), (" human-in-the-loop", HITL)):
        with pytest.raises(AutonomyModeError) as caught:
            parse_autonomy_mode(value)
        assert f"differs from {canonical!r} only in letter case or surrounding whitespace" in str(caught.value)


def _comparison_sites():
    """Every production comparison of an autonomy mode with a string literal: ``<...autonomy...>.mode <op> "x"``,
    ``autonomy_mode <op> "x"`` and ``_autonomy_mode(...) <op> "x"`` (``in`` tuples included)."""
    import ast

    def is_mode(node):
        if isinstance(node, ast.Attribute) and node.attr == "mode" and "autonomy" in ast.unparse(node.value):
            return True
        if isinstance(node, ast.Name) and node.id == "autonomy_mode":
            return True
        return isinstance(node, ast.Call) and "autonomy_mode" in ast.unparse(node.func)

    def literals(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        return []

    sites = []
    for base in ("kriya", os.path.join("plugins", "core_tools")):
        for directory, _dirs, names in os.walk(os.path.join(REPO_ROOT, base)):
            for name in names:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(directory, name)
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Compare):
                        operands = [node.left, *node.comparators]
                        if any(is_mode(o) for o in operands):
                            for operand in operands:
                                sites.extend((os.path.relpath(path, REPO_ROOT), node.lineno, lit) for lit in literals(operand))
    return sites


def test_every_production_mode_comparison_uses_the_closed_vocabulary():
    """A consumer comparing against a value outside the vocabulary (e.g. "full-autonomy") could never be true: a tripwire."""
    from kriya.config.authority import AUTONOMY_MODES
    from kriya.tools import tool

    sites = _comparison_sites()
    # S18 / OI-135: every consumer now reads the mode through the one fail-closed reader (kriya.config.authority
    # is_human_in_the_loop / autonomy_mode_of), so NO production code compares the mode against a literal any more
    # (before S18: 7 literal sites - stage_apply x2, spec_compliance, runtime_verification x2, policy.approval,
    # subtask_executor). Stronger than the vocabulary check it replaces; tests/test_s18_planned_tool_governance.py pins
    # each migrated site's behaviour on an unvalidated mode.
    assert sites == [], sites
    assert [s for s in sites if s[2] not in AUTONOMY_MODES] == []
    assert tool.HUMAN_IN_THE_LOOP in AUTONOMY_MODES


def test_a_real_generate_with_a_malformed_mode_runs_nothing(tmp_path):
    """Post-fix only (on the unfixed bytes this would start a real run against the model): the main group refuses
    before the subcommand, so no process, database, socket or write happens."""
    test_a_malformed_mode_stops_before_anything_runs_or_is_read(tmp_path, ["generate", "add a function"])


@pytest.mark.parametrize("value, code", ONE_PER_CODE, ids=_ids(ONE_PER_CODE))
def test_an_unvalidated_autonomy_object_cannot_be_swapped_in_through_model_copy(value, code):
    """``model_construct`` validates nothing (pydantic's contract; no production caller): an AutonomyConfig built that
    way is still refused when it is put into an AppConfig through ``model_copy(update=...)``."""
    smuggled = AutonomyConfig.model_construct(mode=value)
    _typed(lambda: AppConfig().model_copy(update={"autonomy": smuggled}), code)
