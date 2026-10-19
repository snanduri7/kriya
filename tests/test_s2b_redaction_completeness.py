"""S2b (OI-98 completeness, OI-131 history-detail display): the one redaction owner covers every secret SHAPE the S2
verification measured leaking, and the KUP history projection the GUI receives goes through it.

Owner decisions (2026-10-19): one owner (kriya/foundation/redaction.py), no per-field or GUI-specific rule; OI-131: redact
at the APPLICATION projection before the payload reaches Electron, never in the renderer; stored evidence stays RAW
(digests bind exact bytes); a value that merely looks like a URL/token but carries no secret stays readable.

Characterization FIRST (S1 method). Pins of governed behaviour (green before and after: the readable-by-design values,
the raw stored row, the approval record holding no value, never-mutates, idempotence) and EXPECTED-TO-FLIP pins written as
the corrected behaviour, strict-xfail (AssertionError only) at the characterization commit, all failing on the unfixed
bytes with --runxfail; the correction removes the marks. Every channel is driven through its production entry (the CLI
command, the log handler, the KUP envelope, ``run_kup_operation``), never through a private helper.
"""
import copy
import itertools
import json
import logging
import sqlite3
import subprocess

import pytest
import yaml
from click.testing import CliRunner

from kriya.foundation.redaction import REDACTED, redact_text, redact_value

S = "Zq9SECRETv"
R = REDACTED


@pytest.fixture(autouse=True)
def _homes(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "_authority_home"))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)


# =========================================================================== governed behaviour that must NOT change

READABLE_TEXT = [
    "max_tokens=4096", "--max-tokens 512", "tokenizer=bpe", "secretary=x", "token_budget: 12", "--token-file /p/t",
    "mvn -Dmaven.compiler.showWarnings=true", "python -u script.py", "curl --user alice http://h", "//just/a/path",
    "https://h/x?page=2&sort=asc", "-----BEGIN CERTIFICATE-----\nMIIBdata\n-----END CERTIFICATE-----",
    "Authorization required", "git@github.com:org/repo.git", "the cookie jar is empty", "set the password in your vault",
    json.dumps({"command": ["java", "-jar", "app.jar"], "exit_code": 0}), json.dumps({"env_keys": ["API_TOKEN", "OTHER"]}),
    "Basic setup done", "sha256:" + "ab" * 32, "http://localhost:11434/v1", "dev@example.com", "",
]


@pytest.mark.parametrize("text", READABLE_TEXT)
def test_text_without_a_secret_stays_byte_identical(text):
    assert redact_text(text) == text


@pytest.mark.parametrize("value", [
    ["python", "-u", "script.py"], ["curl", "--user", "alice"], ["--token-file", "/p/t"], ["--port", "8080"],
    {"env_keys": ["API_TOKEN", "OTHER"]}, {"headers": [["Accept", "json"]]}, {"max_tokens": 4096, "tokens": 3},
    {"data": json.dumps([{"steps": [{"command": ["java", "-jar", "app.jar"], "exit_code": 0}]}])},
], ids=["python-u", "user-no-password", "token-file", "port", "names-list", "header-pair", "token-counts", "json-blob"])
def test_values_without_a_secret_stay_identical(value):
    assert redact_value(value) == value


def test_redact_value_never_mutates_its_input():
    value = {"a": ["--token", S], "b": {"password": S}, "c": json.dumps({"api_key": S}), "d": (f"DB_PASSWORD={S}",)}
    before = copy.deepcopy(value)
    redact_value(value)
    assert value == before


def _corpus():
    """A deterministic generated corpus: every shape inside every context (bare, prose, quoted, JSON-wrapped, argv)."""
    shapes = [f"Authorization: Token {S}", f"DB_PASSWORD={S}", f"api_key = 'a {S}'", f"--password {S}", f"-u al:{S}",
              f"Cookie: sid={S}; x=1", f"//u:{S}@h/p", f"ssh_key={S}", f"http://u:{S}@h", f"Bearer {S}", f"token: {S}",
              f"-----BEGIN PRIVATE KEY-----\n{S}\n-----END PRIVATE KEY-----", f"?access_token={S}&p=1", "max_tokens=5",
              f"client_secret=\"x {S}\"", f"--token={S}", f"sk-{S}abcdefghijklmnop12"]
    wrappers = [lambda s: s, lambda s: f"run failed: {s} (exit 1)", lambda s: f"'{s}'", lambda s: json.dumps({"m": s}),
                lambda s: json.dumps([s.split(" ")]), lambda s: json.dumps(json.dumps({"x": s}))]
    return [w(s) for s, w in itertools.product(shapes, wrappers)]


@pytest.mark.parametrize("text", _corpus())
def test_redaction_is_idempotent_over_the_generated_corpus(text):
    once = redact_text(text)
    assert redact_text(once) == once
    value = {"t": text, "l": text.split(" ")}
    assert redact_value(redact_value(value)) == redact_value(value)


# --------------------------------------------------------------------------- history detail: what is stored stays RAW

GATES = [{"attempt": 1, "type": "compile", "success": True},
         {"attempt": 1, "type": "run_verification", "success": True, "steps": [
             {"command": ["run.sh", "--token", S + "1", f"--password={S}2"], "exit_code": 0,
              "stdout": f"connected with DB_PASSWORD={S}3\n", "stderr": "", "timed_out": False}]}]


def _snapshot(goal=f"deploy with api_key = 'x {S}4'"):
    from kriya.config import AppConfig
    from kriya.core.state_paths import trace_db_path
    from kriya.core.trace import TraceLogger
    from kriya.kup.cli_ops import run_kup_operation

    db = trace_db_path(AppConfig())
    TraceLogger(db).log_run(run_id="r-s2b", goal=goal, duration_sec=1.0, attempts=1, status="SUCCESS", files_modified=[],
                            gate_outcomes=GATES)
    acquired = run_kup_operation(AppConfig(), {"operation": "snapshot.acquire"})
    assert acquired["error"] is None, acquired
    return db, acquired["data"]["snapshot_id"]


def _kup(operation, **request):
    from kriya.config import AppConfig
    from kriya.kup.cli_ops import run_kup_operation
    from kriya.kup.inspect import encode_response

    envelope = run_kup_operation(AppConfig(), {"operation": operation, **request})
    assert envelope["error"] is None, envelope
    return envelope, encode_response(envelope)        # the bytes the KUP host writes to Electron


def test_the_stored_run_row_stays_raw_after_a_history_detail_read():
    db, snap = _snapshot()
    _kup("history.detail", snapshot_id=snap, run_id="r-s2b")
    conn = sqlite3.connect(db)
    try:
        stored, goal = conn.execute("SELECT gate_outcomes, goal FROM runs WHERE run_id = 'r-s2b'").fetchone()
    finally:
        conn.close()
    assert json.loads(stored) == GATES and goal.endswith(f"{S}4'")      # the authoritative evidence is never rewritten


def test_the_history_detail_blob_and_its_parsed_section_say_the_same_thing():
    _, snap = _snapshot()
    envelope, _ = _kup("history.detail", snapshot_id=snap, run_id="r-s2b")
    data = envelope["data"]
    assert json.loads(data["fields"]["gate_outcomes"]["data"]) == data["gate_outcomes"]["data"]


def test_the_durable_approval_record_holds_no_configured_value(tmp_path, monkeypatch):
    from kriya.config import authority_approval as aa
    from kriya.config.config import resolve_config_state

    _workspace(tmp_path, monkeypatch)
    state = resolve_config_state()
    path = aa.default_local_approval_path(state.workspace_root)
    aa.save_approval_artifact(path, aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root))
    assert S not in open(path, encoding="utf-8").read()


# =========================================================================== EXPECTED TO FLIP (the measured defect)

FLIP = pytest.mark.xfail(strict=True, raises=AssertionError, reason="S2b characterization: the measured leak (flips with the correction)")

LEAKING_TEXT = [
    ("auth-token-scheme", f"Authorization: Token {S}", f"Authorization: {R} {R}"),
    ("auth-digest", f'Authorization: Digest username="alice", realm="r", response="{S}"', f"Authorization: {R} {R}"),
    ("curl-H-single-quoted", f"curl -H 'Authorization: Token {S}' http://h", f"curl -H 'Authorization: {R} {R}' http://h"),
    ("env-underscore", f"DB_PASSWORD={S}", f"DB_PASSWORD={R}"),
    ("env-export", f"export AWS_SECRET_ACCESS_KEY={S}", f"export AWS_SECRET_ACCESS_KEY={R}"),
    ("auth-token-name", f"auth_token={S}", f"auth_token={R}"),
    ("ssh-key", f"ssh_key={S}", f"ssh_key={R}"),
    ("camel-name", f"dbPassword={S}", f"dbPassword={R}"),
    ("single-quoted-spaces", f"client_secret='a {S}'", f"client_secret='{R}'"),
    ("spaced-eq-quoted", f"api_key = 'abc {S}'", f"api_key = '{R}'"),
    ("spaced-colon", f"token : {S}", f"token : {R}"),
    ("double-quoted-spaces", f'password="pass {S}"', f'password="{R}"'),
    ("argv-space", f"run.sh --password {S} --port 8080", f"run.sh --password {R} --port 8080"),
    ("argv-curl-user", f"curl -u alice:{S} http://h", f"curl -u alice:{R} http://h"),
    ("cookie", f"Cookie: session={S}; theme=dark", f"Cookie: {R}"),
    ("set-cookie", f"Set-Cookie: sid={S}; Path=/; HttpOnly", f"Set-Cookie: {R}"),
    ("pem", f"-----BEGIN RSA PRIVATE KEY-----\nMIIE{S}\nBBBB\n-----END RSA PRIVATE KEY-----",
     f"-----BEGIN RSA PRIVATE KEY-----{R}-----END RSA PRIVATE KEY-----"),
    ("pem-escaped-newlines", f"-----BEGIN OPENSSH PRIVATE KEY-----\\nb3Bl{S}\\n-----END OPENSSH PRIVATE KEY-----",
     f"-----BEGIN OPENSSH PRIVATE KEY-----{R}-----END OPENSSH PRIVATE KEY-----"),
    ("pem-cut", f"key: -----BEGIN EC PRIVATE KEY-----\nMHc{S}", f"key: -----BEGIN EC PRIVATE KEY-----{R}"),
    ("scheme-relative", f"see //alice:{S}@host/path now", f"see //{R}@host/path now"),
    ("query-access-token", f"https://h/x?access_token={S}", f"https://h/x?access_token={R}"),
    ("query-keeps-other-params", f"https://h/x?api_key={S}&page=2", f"https://h/x?api_key={R}&page=2"),
    ("jvm-property", f"java -Djavax.net.ssl.keyStorePassword={S} -jar a.jar", f"java -Djavax.net.ssl.keyStorePassword={R} -jar a.jar"),
    ("jvm-property-bare", f"java -Dpassword={S} -jar a.jar", f"java -Dpassword={R} -jar a.jar"),
    ("percent-encoded-separator", f"q=password%3D{S}", f"q=password%3D{R}"),
    ("python-repr", repr({"password": S}), repr({"password": R})),
    ("json-in-string", json.dumps({"password": f"a {S}"}), json.dumps({"password": R})),
    ("json-argv-in-string", json.dumps({"command": ["java", "--token", S]}), json.dumps({"command": ["java", "--token", R]})),
    ("json-argv-eq-keeps-structure", json.dumps({"command": ["java", f"--token={S}"], "exit_code": 0}),
     json.dumps({"command": ["java", f"--token={R}"], "exit_code": 0})),
    ("json-env-in-string", json.dumps({"env": {"A": S}, "n": 1}), json.dumps({"env": {"A": R}, "n": 1})),
    ("json-twice-encoded", json.dumps(json.dumps({"api_key": S})), json.dumps(json.dumps({"api_key": R}))),
    ("github-fine-grained", f"github_pat_11ABCDEFG0{S}abcdefghijklmnopqrstuvwxyz", R),
]


@FLIP
@pytest.mark.parametrize("text, expected", [(t, e) for _, t, e in LEAKING_TEXT], ids=[i for i, _, _ in LEAKING_TEXT])
def test_every_measured_shape_is_hidden_and_the_rest_kept(text, expected):
    assert redact_text(text) == expected


@FLIP
@pytest.mark.parametrize("value, expected", [
    (["run.sh", "--token", S], ["run.sh", "--token", R]),
    (["run.sh", "--password", S, "--port", "8080"], ["run.sh", "--password", R, "--port", "8080"]),
    (["curl", "-u", f"alice:{S}", "http://h"], ["curl", "-u", f"alice:{R}", "http://h"]),
    (["curl", "-H", f"Authorization: Token {S}"], ["curl", "-H", f"Authorization: {R} {R}"]),
    ({"ssh_key": S, "name": "n"}, {"ssh_key": R, "name": "n"}),
    ({"data": json.dumps({"env": {"A": S}})}, {"data": json.dumps({"env": {"A": R}})}),
    ({"data": json.dumps([{"steps": [{"command": ["java", f"--token={S}"], "exit_code": 0}]}])},
     {"data": json.dumps([{"steps": [{"command": ["java", f"--token={R}"], "exit_code": 0}]}])}),
], ids=["argv-token", "argv-password", "argv-curl-user", "argv-header", "ssh-key", "json-env-blob", "json-argv-blob"])
def test_every_measured_structure_is_hidden(value, expected):
    assert redact_value(value) == expected


def _workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "kriya.yaml").write_text(yaml.safe_dump({"mcp": {"srv": {
        "command": "/usr/bin/true", "args": ["--password", S, "-u", f"alice:{S}", "-H", f"Authorization: Token {S}"],
        "description": f"DB_PASSWORD={S}; Cookie: sid={S}; //bob:{S}@host"}}}))
    monkeypatch.chdir(ws)
    return ws


def _cli(args, stdin=None):
    from kriya.cli import main

    return CliRunner().invoke(main, args, input=stdin, catch_exceptions=False).output


@FLIP
def test_authority_inspect_and_approve_hide_every_shape(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    inspect, approve = _cli(["authority", "inspect"]), _cli(["authority", "approve"], stdin="n\n")
    assert "mcp.srv" in inspect and "/usr/bin/true" in inspect and "--password" in inspect
    assert S not in inspect and S not in approve


@FLIP
def test_the_log_file_hides_every_shape(tmp_path):
    from kriya.core import logging_setup

    path = str(tmp_path / "logs" / "kriya.log")
    handler = logging_setup._open_file_handler(path, logging.INFO)
    handler.handle(logging.LogRecord("kriya.x", logging.WARNING, __file__, 1, "launch %s failed: %s",
                                     (f"run.sh --password {S}", f"Authorization: Token {S}; DB_PASSWORD={S}"), None))
    handler.close()
    text = open(path, encoding="utf-8").read()
    assert "launch run.sh --password" in text and S not in text, text


@FLIP
def test_a_kup_error_envelope_and_the_cli_error_text_hide_every_shape():
    from kriya.cli_commands.common import _user_error_text
    from kriya.kup.inspect import error_envelope

    message = f"refused: Cookie: sid={S} and DB_PASSWORD={S} and //u:{S}@h"
    assert S not in json.dumps(error_envelope("x.y", "r1", "HOST_ERROR", message))
    assert S not in _user_error_text(RuntimeError(message))


@FLIP
def test_the_gui_receives_a_history_detail_with_every_secret_hidden():
    """OI-131: through the KUP adapter (the bytes written to Electron), both forms - the stored JSON text blob in
    ``fields`` and the parsed section - and the derived application outcome."""
    _, snap = _snapshot()
    envelope, wire = _kup("history.detail", snapshot_id=snap, run_id="r-s2b")
    assert S not in wire, wire
    data = envelope["data"]
    steps = data["gate_outcomes"]["data"][1]["steps"][0]
    assert steps["command"] == ["run.sh", "--token", R, f"--password={R}"] and steps["exit_code"] == 0
    blob = json.loads(data["fields"]["gate_outcomes"]["data"])                         # still valid JSON
    assert blob == data["gate_outcomes"]["data"]
    assert data["application_outcome"]["data"]["command"] == f"run.sh --token {R} --password={R}"
    assert data["run"]["goal"] == f"deploy with api_key = '{R}'"
    assert redact_value(data) == data                                                  # the projection is a fixed point


@FLIP
def test_the_history_list_and_the_traces_table_hide_a_secret_in_the_goal(tmp_path, monkeypatch):
    _, snap = _snapshot()
    _, wire = _kup("history.list", snapshot_id=snap)
    assert "deploy with api_key" in wire and S not in wire
    monkeypatch.chdir(tmp_path)
    table = _cli(["traces"])
    assert "deploy with api_key" in table and S not in table, table
