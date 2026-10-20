"""S2d (OI-153, was OI-144 part 4): positional secrets in argv - characterization FIRST.

Owner decisions (2026-10-20): (b) grammar-aware redaction over a CLOSED supported-command table at the ONE redaction owner
(kriya/foundation/redaction.py), argv/token-aware, command-specific and exact (an attached `mysql -p<pw>` vs a next-argument
`sshpass -p <pw>` / `docker login -p <pw>`; `mysql -P 3306`, a bare `mysql -p`, `ssh -p 22`, `docker run -p 8080:80` and
`docker login --password-stdin` stay readable); (c) the runtime-command REVIEW fails closed: a command whose exact bytes would
expose a recognized secret is never offered for review, never shown altered and then run as though reviewed, and never
persisted raw by the refusal; the operator-authorized paths (`--yes`, an autonomy mode without review, a command the person
wrote in the goal) are not changed in what they may run, only their outputs redact.

Governed controls (green before and after) and EXPECTED-TO-FLIP pins written as the corrected behaviour, strict-xfail
(AssertionError only) at the characterization commit; the correction commits remove the marks. Every channel is driven
through its production entry (``run_kup_operation``, the CLI, the log handler, the CLI callback, ``policy.approval.ask``,
the workflow's ``_approve_runtime_commands``), never a private helper - except the owner's own closed table (tripwire).
"""
import asyncio
import copy
import json
import logging
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from kriya.foundation.redaction import REDACTED, redact_text, redact_value

S = "Zq9SECRETv"
R = REDACTED
LID = "20261020T101010-abcdef01"
FLIP = pytest.mark.xfail(strict=True, raises=AssertionError, reason="S2d characterization: flips with the correction")
MISSING = "<S2d: not defined yet>"


@pytest.fixture(autouse=True)
def _homes(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "_authority_home"))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)


# =========================================================================== the grammar table: (id, text, expected, argv, expected argv)

GRAMMAR = [
    # MySQL / MariaDB client: -p<password> ATTACHED (a bare -p prompts; `-p x` makes x the database)
    ("mysql-attached", f"mysql -u root -p{S} db", f"mysql -u root -p{R} db"),
    ("mariadb-attached", f"mariadb -h db -u app -p{S}", f"mariadb -h db -u app -p{R}"),
    ("mysql-absolute-path", f"/usr/bin/mysql -p{S} shop", f"/usr/bin/mysql -p{R} shop"),
    ("mysql-through-sudo", f"sudo -u dba mysql -p{S}", f"sudo -u dba mysql -p{R}"),
    ("mysql-through-env", f"env -i MYSQL_HOST=db mysql -u app -p{S}", f"env -i MYSQL_HOST=db mysql -u app -p{R}"),
    ("mysql-through-docker-exec", f"docker exec -i db mysql -uroot -p{S} shop", f"docker exec -i db mysql -uroot -p{R} shop"),
    # sshpass (getopt "+f:d:p:P:heVv"): -p takes the NEXT argument or an attached one; options end at the wrapped command
    ("sshpass-next", f"sshpass -p {S} ssh -p 22 host", f"sshpass -p {R} ssh -p 22 host"),
    ("sshpass-attached", f"sshpass -p{S} ssh host", f"sshpass -p{R} ssh host"),
    ("sshpass-cluster", f"sshpass -vp {S} ssh host", f"sshpass -vp {R} ssh host"),
    ("sshpass-after-prompt-option", f"sshpass -P assword: -p {S} scp a host:b", f"sshpass -P assword: -p {R} scp a host:b"),
    # docker / podman login (pflag): -p <pw>, -p<pw>, -p=<pw>; a global option may precede `login`
    ("docker-login-next", f"docker login -u alice -p {S} registry.example", f"docker login -u alice -p {R} registry.example"),
    ("docker-login-attached", f"docker login -p{S} registry.example", f"docker login -p{R} registry.example"),
    ("docker-login-equals", f"docker login -u a -p={S} r", f"docker login -u a -p={R} r"),
    ("docker-global-option", f"docker --config /etc/dc login -u a -p {S}", f"docker --config /etc/dc login -u a -p {R}"),
    ("podman-login", f"podman login -u a -p {S} quay.io", f"podman login -u a -p {R} quay.io"),
    # npm config set <key> <value> (also `npm set`, `npm c|con|conf|confi set`): the value of a credential key
    ("npm-authtoken", f"npm config set //registry.npmjs.org/:_authToken {S}", f"npm config set //registry.npmjs.org/:_authToken {R}"),
    ("npm-set-auth", f"npm set _auth {S}", f"npm set _auth {R}"),
    ("npm-c-password", f"npm c set //r.example/:_password {S}", f"npm c set //r.example/:_password {R}"),
    ("npm-conf-with-option", f"npm conf set -L user //r.example/:_authToken {S}", f"npm conf set -L user //r.example/:_authToken {R}"),
]


@FLIP
@pytest.mark.parametrize("text, expected", [(t, e) for _, t, e in GRAMMAR], ids=[i for i, _, _ in GRAMMAR])
def test_a_positional_secret_is_hidden_in_the_text_and_the_rest_kept(text, expected):
    assert redact_text(text) == expected


@FLIP
@pytest.mark.parametrize("text, expected", [(t, e) for _, t, e in GRAMMAR], ids=[i for i, _, _ in GRAMMAR])
def test_a_positional_secret_is_hidden_in_the_argv_and_the_rest_kept(text, expected):
    assert redact_value(text.split(" ")) == expected.split(" ")


TEXT_ONLY = [
    ("python-repr", repr(["mysql", "-u", "root", f"-p{S}"]), repr(["mysql", "-u", "root", f"-p{R}"])),
    ("json-list-after-a-name", "cmd=" + json.dumps(["sshpass", "-p", S, "ssh", "h"]), "cmd=" + json.dumps(["sshpass", "-p", R, "ssh", "h"])),
    ("escaped-json-in-a-log", 'detail={\\"cmd\\": \\"mysql -p' + S + '\\"}', 'detail={\\"cmd\\": \\"mysql -p' + R + '\\"}'),
    ("quoted-value-with-space", f"sshpass -p 'a {S}' ssh h", f"sshpass -p '{R}' ssh h"),
    ("in-a-log-line", f"Runtime command failed: docker login -u a -p {S} r (exit 1)", f"Runtime command failed: docker login -u a -p {R} r (exit 1)"),
    ("two-commands", f"mysql -p{S}1 db; sshpass -p {S}2 ssh -p 22 h", f"mysql -p{R} db; sshpass -p {R} ssh -p 22 h"),
    ("review-reason", f"Allow?\n  Command(s):\n    1. mysql -u root -p{S} db\n  Looking for: rows", f"Allow?\n  Command(s):\n    1. mysql -u root -p{R} db\n  Looking for: rows"),
    ("key-value-log", f"cmd=mysql -p{S} rc=1", f"cmd=mysql -p{R} rc=1"),
]


@FLIP
@pytest.mark.parametrize("text, expected", [(t, e) for _, t, e in TEXT_ONLY], ids=[i for i, _, _ in TEXT_ONLY])
def test_a_positional_secret_is_hidden_wherever_the_text_quotes_the_command(text, expected):
    assert redact_text(text) == expected


@FLIP
@pytest.mark.parametrize("value, expected", [
    (["bash", "-c", f"mysql -p{S} db"], ["bash", "-c", f"mysql -p{R} db"]),
    ({"command": ["docker", "login", "-p", S], "exit_code": 0}, {"command": ["docker", "login", "-p", R], "exit_code": 0}),
    ([["sshpass", "-p", S, "ssh", "h"], ["npm", "set", "_authToken", S]], [["sshpass", "-p", R, "ssh", "h"], ["npm", "set", "_authToken", R]]),
    (("mysql", f"-p{S}"), ["mysql", f"-p{R}"]),
], ids=["shell-string-element", "gate-step", "nested-argv-lists", "tuple"])
def test_a_positional_secret_is_hidden_in_a_structure(value, expected):
    assert redact_value(value) == expected


# =========================================================================== governed: readable look-alikes stay byte-identical

READABLE = [
    "mysql -P 3306 -u root shop", "mysql -u root -p shop", "mysql -h db -u root -p", "mysql -u root -p -h db",
    "docker login --password-stdin -u alice registry.example", "docker login -u alice registry.example",
    "docker run -p 8080:80 nginx", "docker run -d -p 3306:3306 -e MYSQL_DATABASE=shop mysql:8",
    "docker compose -p proj up -d", "docker-compose -p proj up", "podman run -p 8080:80 nginx", "docker -H tcp://h:2375 ps",
    "ssh -p 22 host", "scp -P 2222 a host:b", "psql -p 5432 -h db -U app", "curl -p -x http://proxy:3128 https://h", "ls -p /tmp",
    "npm config set registry https://registry.example", "npm config get //registry.npmjs.org/:_authToken",
    "npm set init-author-name alice", "npm run build -- -p 3000", "npm config set //r.example/:certfile /etc/c.pem",
    "sshpass -f /run/secrets/pw ssh -p 22 host", "sshpass -e ssh -p 22 host", "sshpass -P assword: -f pw ssh host",
    "sshpass -d 3 ssh -p 22 host", "cat /etc/mysql/my.cnf", "ls -p /usr/bin/mysql", "./scripts/mysql-backup.sh -p 5",
    "vim docker-compose.yml", "mysqldump -P 3306 shop", "apt-get install -y mysql-client sshpass",
]


@pytest.mark.parametrize("text", READABLE)
def test_a_readable_look_alike_stays_byte_identical_as_text_and_argv(text):
    assert redact_text(text) == text
    argv = text.split(" ")
    assert redact_value(argv) == argv


@pytest.mark.parametrize("text, expected", [
    (f"docker login --password {S} r", f"docker login --password {R} r"),
    (f"docker login --password={S} r", f"docker login --password={R} r"),
    (f"mysql --password={S} shop", f"mysql --password={R} shop"),
    (f"npm config set //registry.npmjs.org/:_authToken={S}", f"npm config set //registry.npmjs.org/:_authToken={R}"),
    ("mysql --password shop -h db", f"mysql --password {R} -h db"),
], ids=["docker-password-flag", "docker-password-eq", "mysql-password-eq", "npm-key-eq", "mysql-password-space-recorded"])
def test_the_name_and_flag_rules_keep_their_bytes(text, expected):
    """S2/S2b behaviour, unchanged: the long `--password` flag and every `name=value` pair. `mysql --password shop`: there
    `shop` is the database (mysql reads only an attached value), hidden by the generic flag rule - recorded, unchanged."""
    assert redact_text(text) == expected
    assert redact_value(text.split(" ")) == expected.split(" ")


def test_redact_value_never_mutates_its_input():
    value = {"cmd": ["mysql", f"-p{S}"], "steps": [["sshpass", "-p", S]], "t": (f"docker login -p {S}",)}
    before = copy.deepcopy(value)
    redact_value(value)
    assert value == before


def _corpus():
    shapes = [t for _, t, _ in GRAMMAR] + [t for _, t, _ in TEXT_ONLY] + READABLE[:8]
    wrappers = [lambda s: s, lambda s: f"run failed: {s} (exit 1)", lambda s: f"'{s}'", lambda s: json.dumps({"m": s}),
                lambda s: json.dumps([s.split(" ")]), lambda s: repr(s.split(" "))]
    return [w(s) for s in shapes for w in wrappers]


@pytest.mark.parametrize("text", _corpus())
def test_redaction_is_idempotent_over_the_generated_corpus(text):
    once = redact_text(text)
    assert redact_text(once) == once
    value = {"t": text, "l": text.split(" ")}
    assert redact_value(redact_value(value)) == redact_value(value)


@FLIP
@pytest.mark.parametrize("text, expected", [
    ("sshpass -p is the unsafe form", f"sshpass -p {R} the unsafe form"),
    ("docker run --name mysql -p3306:3306 mysql:8", f"docker run --name mysql -p{R} mysql:8"),
], ids=["sshpass-prose", "container-named-like-the-client"])
def test_the_measured_cost_of_the_grammar_is_fail_closed(text, expected):
    """The price of recognizing a table command WHEREVER its word appears (`docker exec db mysql -p...`, `timeout 30 sshpass
    ...`): prose that names `sshpass -p`, and an attached `-p<port>` after a word that merely names a table command, are hidden."""
    assert redact_text(text) == expected


@FLIP
def test_the_supported_command_table_is_closed():
    """Tripwire: adding, widening or dropping a command or one of its option tables is a reviewed change of this table."""
    from kriya.foundation import redaction

    grammars = getattr(redaction, "_COMMAND_GRAMMARS", None) or {}
    assert {name: fn.__name__ for name, fn in grammars.items()} == {
        "mysql": "_mysql", "mariadb": "_mysql", "sshpass": "_sshpass", "docker": "_docker_login", "podman": "_podman_login", "npm": "_npm"}
    assert getattr(redaction, "_SSHPASS_OPTIONS", None) == {"f": 1, "d": 1, "p": 1, "P": 1, "e": 2, "h": 0, "V": 0, "v": 0}
    assert getattr(redaction, "_DOCKER_LOGIN_VALUES", None) == ("-u", "--username")
    assert getattr(redaction, "_PODMAN_LOGIN_VALUES", None) == ("-u", "--username", "--authfile", "--cert-dir", "--compat-auth-file", "--secret")
    assert getattr(redaction, "_NPM_CONFIG", None) == ("c", "con", "conf", "confi", "config")


# =========================================================================== the output channels (S2c channel table)

SHAPES = [t for _, t, _ in GRAMMAR[:1] + GRAMMAR[6:7] + GRAMMAR[10:11] + GRAMMAR[15:16]]


@FLIP
def test_the_log_file_hides_every_positional_shape(tmp_path):
    from kriya.core import logging_setup

    path = str(tmp_path / "logs" / "kriya.log")
    handler = logging_setup._open_file_handler(path, logging.INFO)
    handler.handle(logging.LogRecord("kriya.x", logging.WARNING, __file__, 1, "runtime command failed: %s", (" | ".join(SHAPES),), None))
    handler.close()
    text = open(path, encoding="utf-8").read()
    assert "runtime command failed: mysql -u root -p" in text and S not in text, text


@FLIP
def test_a_kup_error_envelope_and_the_cli_error_text_hide_every_positional_shape():
    from kriya.cli_commands.common import _user_error_text
    from kriya.kup.inspect import error_envelope

    message = " ; ".join(SHAPES)
    assert S not in json.dumps(error_envelope("x.y", "r1", "HOST_ERROR", message))
    assert S not in _user_error_text(RuntimeError(message))


@FLIP
def test_authority_inspect_hides_a_positional_secret_in_a_configured_command(tmp_path, monkeypatch):
    from kriya.cli import main

    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "kriya.yaml").write_text(yaml.safe_dump({"mcp": {"srv": {
        "command": "/usr/bin/true", "args": ["sshpass", "-p", S, "ssh", "-p", "22", "h"], "description": " ; ".join(SHAPES)}}}))
    monkeypatch.chdir(ws)
    out = CliRunner().invoke(main, ["authority", "inspect"], catch_exceptions=False).output
    assert "sshpass" in out and S not in out, out


def _kup(operation, **request):
    from kriya.config import AppConfig
    from kriya.kup.cli_ops import run_kup_operation
    from kriya.kup.inspect import encode_response

    envelope = run_kup_operation(AppConfig(), {"operation": operation, **request})
    return envelope, encode_response(envelope)


@FLIP
def test_the_run_history_the_gui_receives_hides_every_positional_shape(tmp_path, monkeypatch):
    """history.detail (each recorded step's argv and output), history.list, history.prompt and `kriya traces`. The row is
    redacted at write by its one writer (S20) - with the grammar, the stored row holds no positional secret either."""
    import sqlite3

    from kriya.cli import main
    from kriya.config import AppConfig
    from kriya.core.state_paths import trace_db_path
    from kriya.core.trace import TraceLogger

    argvs = [text.split(" ") for text in SHAPES]
    gates = [{"attempt": 1, "type": "run_verification", "success": True,
              "steps": [{"command": argv, "exit_code": 0, "stdout": " | ".join(SHAPES), "stderr": "", "timed_out": False} for argv in argvs]}]
    db = trace_db_path(AppConfig())
    TraceLogger(db).log_run(run_id="r-s2d", goal="deploy: " + SHAPES[0], duration_sec=1.0, attempts=1, status="SUCCESS",
                            files_modified=[], gate_outcomes=gates, prompt_rendered=" | ".join(SHAPES))
    snap = _kup("snapshot.acquire")[0]["data"]["snapshot_id"]
    detail, wire = _kup("history.detail", snapshot_id=snap, run_id="r-s2d")
    assert detail["error"] is None and S not in wire
    assert detail["data"]["gate_outcomes"]["data"][0]["steps"][2]["command"] == ["docker", "login", "-u", "alice", "-p", R, "registry.example"]
    assert S not in _kup("history.list", snapshot_id=snap)[1] and S not in _kup("history.prompt", snapshot_id=snap, run_id="r-s2d")[1]
    with sqlite3.connect(db) as conn:
        assert S not in json.dumps(conn.execute("SELECT goal, gate_outcomes, prompt_rendered FROM runs").fetchall())
    monkeypatch.chdir(tmp_path)
    table = CliRunner().invoke(main, ["traces"], catch_exceptions=False).output
    assert "deploy: mysql -u root" in table and S not in table, table


# =========================================================================== (c) the runtime-command review fails closed

POSITIONAL = [("mysql", ["mysql", "-u", "root", f"-p{S}", "db"]), ("sshpass", ["sshpass", "-p", S, "ssh", "-p", "22", "h"]),
              ("docker-login", ["docker", "login", "-u", "a", "-p", S, "r"]), ("npm", ["npm", "config", "set", "//registry.npmjs.org/:_authToken", S]),
              ("podman-attached", ["podman", "login", f"-p{S}", "r"]), ("mariadb", ["mariadb", f"-p{S}"])]
FLAG = ("token-flag", ["run.sh", "--token", S])   # the S2b-covered shape: its terminal prompt was shown redacted (S2c) and ran raw
REFUSING = POSITIONAL + [FLAG]


def _subject(argv):
    from kriya.policy.approval import build_subject

    return build_subject(unit_id="u1", criterion="prints ok", working_directory="/wt", commands=[argv], input_channel="none", stdin_payload=None,
                         reason="Allow Kriya to run?\n  Command(s):\n    1. " + " ".join(argv) + "\n  Looking for: prints ok")


def _refusal_text():
    from kriya.policy import approval

    return getattr(approval, "RUNTIME_COMMAND_SECRET_REFUSAL", MISSING)


@FLIP
@pytest.mark.parametrize("argv", [a for _, a in POSITIONAL], ids=[i for i, _ in POSITIONAL])
def test_a_runtime_command_with_a_positional_secret_is_not_offered_for_review(argv):
    from kriya.control.launch_approval import build_pending_runtime

    assert build_pending_runtime("l", "w", 1, _subject(argv)) is None


def _session(tmp_path):
    from kriya.context.workspace_identity import canonical_workspace, workspace_identity
    from kriya.control.launch import LaunchSession

    ws = tmp_path / "proj"
    ws.mkdir()
    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), LID, "goal", {"ready": True}, {})
    session.open()
    return canonical, session


@FLIP
@pytest.mark.parametrize("argv", [a for _, a in REFUSING], ids=[i for i, _ in REFUSING])
@pytest.mark.parametrize("yes", [False, True], ids=["prompt", "yes"])
def test_inside_a_launch_the_refusal_is_recorded_redacted_with_the_digest_of_the_exact_bytes(tmp_path, argv, yes):
    """Not offered (no pending review exists, so runtime_command.review has nothing to serve); the launch record's
    `approval_required` says why in operator words and holds the REDACTED command and the fingerprint of the exact bytes -
    never the raw command, never the raw reason (which quotes it). `--yes` never takes effect inside a launch."""
    from kriya.cli import _runtime_command_decision
    from kriya.control.persistence import launch_record_path
    from kriya.policy.approval import RuntimeCommandDecision

    canonical, session = _session(tmp_path)
    subject = _subject(argv)
    try:
        answer = _runtime_command_decision(subject, yes)
        if asyncio.iscoroutine(answer):
            answer.close()
        record_text = open(launch_record_path(canonical, LID), encoding="utf-8").read()
        status, wire = _kup("launch.status", workspace=canonical, launch_id=LID)
    finally:
        session.finish(0, None, None)
    assert answer == RuntimeCommandDecision(None)
    record = json.loads(record_text)
    assert record["pending_approval"] is None and record["approval_history"] == []
    required = record["approval_required"]
    assert required["kind"] == "runtime_command" and required["reason"] == _refusal_text()[:400]
    assert required["details"] == {"commands": [" ".join(redact_value(argv))], "command_fingerprint": subject.fingerprint}
    assert S not in record_text and S not in wire and status["error"] is None


@FLIP
@pytest.mark.parametrize("argv", [a for _, a in REFUSING], ids=[i for i, _ in REFUSING])
def test_the_terminal_prompt_refuses_instead_of_showing_an_altered_command(argv, monkeypatch, capsys):
    """`kriya generate` outside a launch: the person is NOT asked (asking would show either the secret or bytes that are not
    what runs); the refusal says why and points at the operator's own paths; nothing is approved."""
    import click

    from kriya.cli import _runtime_command_decision
    from kriya.policy.approval import RuntimeCommandDecision

    asked = []
    monkeypatch.setattr(click, "confirm", lambda *a, **k: asked.append(a) or True)
    assert _runtime_command_decision(_subject(argv), False) == RuntimeCommandDecision(None)
    out = capsys.readouterr().out
    assert asked == [] and S not in out and _refusal_text() in out


@pytest.mark.parametrize("argv", [a for _, a in REFUSING], ids=[i for i, _ in REFUSING])
def test_the_operator_yes_still_runs_what_it_could_run(argv, capsys):
    """Operator-authorized path (terminal `--yes`): what it may run is unchanged (approved, bound to the exact bytes)."""
    from kriya.cli import _runtime_command_decision

    subject = _subject(argv)
    assert _runtime_command_decision(subject, True).approved_fingerprint == subject.fingerprint
    assert "[Auto-Approving]" in capsys.readouterr().out


@FLIP
@pytest.mark.parametrize("argv", [a for _, a in POSITIONAL], ids=[i for i, _ in POSITIONAL])
def test_the_operator_yes_output_hides_the_secret(argv, capsys):
    from kriya.cli import _runtime_command_decision

    _runtime_command_decision(_subject(argv), True)
    assert S not in capsys.readouterr().out


def test_the_operator_yes_output_already_hid_a_flag_secret(capsys):
    """Governed (S2c): the `--yes` line is redacted display text."""
    from kriya.cli import _runtime_command_decision

    _runtime_command_decision(_subject(FLAG[1]), True)
    out = capsys.readouterr().out
    assert S not in out and f"run.sh --token {R}" in out


@FLIP
@pytest.mark.parametrize("argv", [POSITIONAL[0][1], FLAG[1]], ids=["mysql", "token-flag"])
def test_the_legacy_prompt_is_not_asked_and_nothing_is_approved(argv, tmp_path, monkeypatch):
    """`kriya proposal execute` (a plain callback: policy.approval.ask's legacy branch) printed the redacted reason and the
    person approved bytes they could not read. The authority does not ask a plain callback for such a subject."""
    import kriya.workflow.proposal_promotion as promotion
    from kriya.cli import main
    from kriya.policy.approval import ask

    subject = _subject(argv)
    seen = {}

    async def fake_execute(proposal_id, workspace, we, **kwargs):
        seen["decision"] = await ask(kwargs["approval_callback"], subject)
        return {"quality_gates_passed": True}

    monkeypatch.setattr(promotion, "execute_approved_proposal", fake_execute)
    monkeypatch.chdir(tmp_path)
    out = CliRunner().invoke(main, ["proposal", "execute", "p1"], input="y\n", catch_exceptions=False).output
    assert seen["decision"].approved is False and "Escalation Review Needed" not in out and S not in out


def _ctx(callback, goal="Add a feature"):
    return SimpleNamespace(worktree_path="/wt", current_subtask_id="u1", approval_callback=callback, goal=goal, grounding_goal="", exit_authority_goal="")


def _judgment(argv):
    return {"should_run": True, "run_commands": [argv], "success_criteria": "prints ok", "input_channel": "none", "command_source": "inferred"}


@FLIP
@pytest.mark.parametrize("argv", [a for _, a in REFUSING], ids=[i for i, _ in REFUSING])
def test_the_workflow_declines_through_the_typed_channel_and_executes_nothing(argv, monkeypatch):
    """The executor's own choke point (both runtime call sites route through it): the desktop/terminal typed channel answers
    with no approval, so the run records a declined check (REQUIRED_RUNTIME_VERIFICATION_DECLINED when required)."""
    import click

    from kriya.cli import TypedApprovalCallback, _runtime_command_decision
    from kriya.workflow import attempt

    monkeypatch.setattr(click, "confirm", lambda *a, **k: True)
    typed = TypedApprovalCallback(lambda f, r: pytest.fail("the change channel"), lambda s: _runtime_command_decision(s, False))
    state = SimpleNamespace(run_verification_approved_fingerprint=None)
    with patch.object(attempt, "_resolve_run_command", side_effect=lambda cmd, _wt: list(cmd)):
        assert asyncio.run(attempt._approve_runtime_commands(_ctx(typed), state, _judgment(argv), "intro")) is False
    assert state.run_verification_approved_fingerprint is None


@pytest.mark.parametrize("argv", [a for _, a in REFUSING], ids=[i for i, _ in REFUSING])
def test_the_other_operator_paths_are_not_asked_and_not_changed(argv):
    """An autonomy mode without review runs without asking; in the legacy channel a command the person wrote in the goal is
    their own authority (not reviewed). The fingerprint stays the digest of the exact bytes."""
    from kriya.workflow import attempt

    state = SimpleNamespace(run_verification_approved_fingerprint=None)
    with patch.object(attempt, "_resolve_run_command", side_effect=lambda cmd, _wt: list(cmd)):
        assert attempt._runtime_commands_need_approval(_ctx(lambda f, r: True), state, _judgment(argv), "guardrails") is False
        authored = _ctx(lambda f, r: True, goal="please run " + " ".join(argv) + " now")
        assert attempt._runtime_commands_need_approval(authored, state, _judgment(argv), "human-in-the-loop") is False
        assert attempt._derive_runtime_command_subject(_ctx(None), _judgment(argv), "").commands == (tuple(argv),)


def _install_raw_review(session, argv):
    """A runtime review recorded RAW by a Kriya before S2d (the record survives an upgrade): consistent fingerprint."""
    from kriya.policy.approval import fingerprint_of

    fingerprint = fingerprint_of("/wt", [tuple(argv)], "none", None)
    entry = {"review_id": "ra-" + "a" * 24, "sequence": 1, "kind": "runtime_command", "status": "PENDING", "requested_at": "2026-10-20T00:00:00.000+00:00",
             "expires_at": "2026-10-20T01:00:00.000+00:00", "candidate_digest": fingerprint, "command_fingerprint": fingerprint, "unit_id": "u1",
             "criterion": "c", "commands": [argv], "working_directory": "/wt", "working_directory_kind": "ISOLATED_WORKTREE", "input_channel": "none",
             "stdin_present": False, "stdin_sha256": None, "environment_names": [], "reason_sha256": "sha256:" + "b" * 64, "decision": None}
    assert session._install_pending(entry)
    return entry


@FLIP
def test_a_review_recorded_raw_before_s2d_is_refused_not_served(tmp_path):
    from kriya.kup import policy

    canonical, session = _session(tmp_path)
    try:
        entry = _install_raw_review(session, POSITIONAL[0][1])
        envelope, wire = _kup("runtime_command.review", workspace=canonical, launch_id=LID, review_id=entry["review_id"])
    finally:
        session.finish(0, None, None)
    assert envelope["data"] is None and envelope["error"]["code"] == policy.REVIEW_COMMAND_UNVERIFIED
    assert envelope["error"]["reason"] == "commands_carry_secret" and S not in wire


def test_a_plain_runtime_command_is_still_offered_and_served(tmp_path):
    """Governed: the S2d refusal is not a blanket one - a command with no recognized secret is reviewed exactly as before."""
    canonical, session = _session(tmp_path)
    try:
        entry = _install_raw_review(session, ["docker", "run", "-p", "8080:80", "nginx"])
        envelope, _ = _kup("runtime_command.review", workspace=canonical, launch_id=LID, review_id=entry["review_id"])
    finally:
        session.finish(0, None, None)
    assert envelope["error"] is None and envelope["data"]["commands"] == [["docker", "run", "-p", "8080:80", "nginx"]]
    from kriya.control.launch_approval import build_pending_runtime

    assert build_pending_runtime("l", "w", 1, _subject(["mysql", "-P", "3306", "-u", "root", "-p", "shop"])) is not None
