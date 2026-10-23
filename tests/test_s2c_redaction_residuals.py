"""S2c (OI-144 redaction residuals, OI-145 one endpoint rule, OI-146 the two raw-content surfaces): characterization FIRST.

Owner decisions (2026-10-20): (1)-(3) of OI-144 are fixed at the ONE owner (kriya/foundation/redaction.py), no per-surface
rule; (4) - positional secrets with no name or flag grammar (`mysql -p<pw>`, `sshpass -p`, `docker login -p`, `npm config set
... _authToken <pw>`) - was an open policy question pinned here strict-xfail; S2d (OI-153) decided and flipped the 8 pins
(tests/test_s2d_positional_secrets.py carries the full grammar and channel matrix). OI-145: one endpoint display rule at the owner. OI-146 (INTENTIONAL_CONTRACT, strict scope): the
change_approval.review DIFF and the opt-in evidence.call_content / change_content (`content_visibility: on`) are the ONLY
raw-content surfaces; they never reach evidence, logs, history or telemetry, and default/off WITHHOLDS (it does not redact).

Pins of governed behaviour (green before and after) and EXPECTED-TO-FLIP pins written as the corrected behaviour,
strict-xfail (AssertionError only) at the characterization commit; the correction commits remove the marks. Every channel
is driven through its production entry (``run_kup_operation``, the CLI callback, the log handler), never a private helper,
except the owner's own closed tables (tripwires).
"""
import copy
import itertools
import json
import logging
import os
import re

import pytest
from _review_channel_host import attach_review_channel, channel_decide

from kriya.foundation.redaction import REDACTED, redact_text, redact_value
from kriya.policy.approval import ApprovalOutcome

S = "Zq9SECRETv"
R = REDACTED
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLIP = pytest.mark.xfail(strict=True, raises=AssertionError, reason="S2c characterization: flips with the correction")


@pytest.fixture(autouse=True)
def _homes(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "_authority_home"))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)


# =========================================================================== governed behaviour that must NOT change

READABLE_TEXT = [
    # (1) negative controls: a compound word that merely CONTAINS a secret word, or ends in a short one that collides
    "TOKENIZER=bpe", "MAXTOKENS=5", "PASSWORDLESS=1", "PGPASSFILE=/home/u/.pgpass", "PGHOST=db", "PGUSER=alice",
    "SECRETARY=x", "KEYBOARD=us", "OLDPWD=/home/u/src", "alice ALL=(ALL) NOPASSWD: ALL", "max_tokens=4096",
    "SOURCE_CHANGED_SINCE_AUTHORIZATION: '/ws/a' changed", "PREAUTHORIZATION: x", "NEEDS YOUR AUTHORIZATION: the change",
    "tokens_used=12", "Tokenizer: bpe",
    # (2) near misses of the vendor token shapes
    "npm_config_registry=https://r", "my_npm_token_file=/p", "sk-learn-preprocessing-pipeline-v2", "risk-assessment-report-2026",
    "xoxo-hugs-and-kisses", "glpat-short", "AIzaShort", "hf_hub_download", "sk_live_short", "task-proj-abcdefghijklmnopqrstuvwxyz",
    # (3) an '@' after the authority is not userinfo, percent-encoded or not; encoded text without a secret stays as written
    "https://h/%75ser:pw@x", "https://h/user:pw@x", "https://e.com/a@b?q=c@d",
    "docker pull registry/v2/nginx:1.25@sha256:" + "ab" * 32, "https://h/x?q=%41%42%43&page=2",
    "https://h/x?redirect=https%3A%2F%2Fh%2Fp", "https://h/x?redirect=https%3A%2F%2Fh%2Fa%40b", "100%25 done",
    "q=%70age%3D2", "x%3Dpage%3D2",
]


@pytest.mark.parametrize("text", READABLE_TEXT)
def test_text_without_a_secret_stays_byte_identical(text):
    assert redact_text(text) == text


@pytest.mark.parametrize("value", [
    {"PGHOST": "db", "PGUSER": "alice", "PGPASSFILE": "/p", "TOKENIZER": "bpe", "MAXTOKENS": 5, "OLDPWD": "/x"},
    ["docker", "pull", "nginx:1.25"], ["npm", "config", "get", "registry"],
], ids=["env-like-dict", "docker-pull", "npm-get"])
def test_values_without_a_secret_stay_identical(value):
    assert redact_value(value) == value


@pytest.mark.parametrize("text, expected", [("pwd=/tmp/x", f"pwd={R}"), ("PWD=/home/u", f"PWD={R}")])
def test_the_recorded_pwd_over_redaction_is_unchanged(text, expected):
    """OI-144 note: `pwd` is a secret LAST WORD (MySQL/LDAP spell a password that way), so a shell's working directory is
    hidden too - fail-closed, recorded, not changed by S2c (the compound rule does not take short words, see below)."""
    assert redact_text(text) == expected


def test_an_authority_with_a_percent_encoded_user_name_is_already_hidden():
    """OI-144 (3) as the ledger wrote it (`https://h/%75ser:pw@x`) is NOT userinfo: the '@' comes after the authority's
    first '/', so it is path (RFC 3986), consistently with the decoded form. The authority form is hidden (MEASURED)."""
    assert redact_text(f"https://%75ser:{S}@h/p") == f"https://{R}@h/p"
    assert redact_text(f"https://u%3A{S}@h") == f"https://{R}@h"


def test_redact_value_never_mutates_its_input():
    value = {"PGPASSWORD": S, "cmd": ["mysql", f"-p{S}"], "t": f"xoxb-1234567890-0987654321-{S}", "u": (f"q=%70assword={S}",)}
    before = copy.deepcopy(value)
    redact_value(value)
    assert value == before


def _corpus():
    shapes = [f"PGPASSWORD={S}", f"GITHUBTOKEN: {S}", f"xoxb-1234567890-0987654321-{S}", f"glpat-{S}abcdefghij12",
              "npm_" + S + "abcdefghijklmnopqrstuvwxyz", f"sk-proj-{S}abcdefghijklmnopqrstuvwxyz",
              f"https://a/l?next=https%3A%2F%2Fal%3A{S}%40h%2Fp&x=1", f"https://h/x?%70assword={S}&p=2",
              f"mysql -u root -p{S}", f"sshpass -p {S} ssh h", "TOKENIZER=bpe", "pwd=/tmp/x", f"DB_PASSWORD={S}"]
    wrappers = [lambda s: s, lambda s: f"run failed: {s} (exit 1)", lambda s: f"'{s}'", lambda s: json.dumps({"m": s}),
                lambda s: json.dumps([s.split(" ")]), lambda s: json.dumps(json.dumps({"x": s}))]
    return [w(s) for s, w in itertools.product(shapes, wrappers)]


@pytest.mark.parametrize("text", _corpus())
def test_redaction_is_idempotent_over_the_generated_corpus(text):
    once = redact_text(text)
    assert redact_text(once) == once
    value = {"t": text, "l": text.split(" ")}
    assert redact_value(redact_value(value)) == redact_value(value)


# --------------------------------------------------------------------------- OI-145: the endpoint display rule's bytes

ENDPOINTS = [
    ("http://alice:pw@127.0.0.1:9/v1", "http://127.0.0.1:9/v1"), ("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1"),
    ("https://tok@github.com/o/r", "https://github.com/o/r"), ("HTTP://User:Pw@LOCALHOST:11434/V1/", "http://localhost:11434/V1/"),
    ("http://a:p%40w@h/x?api_key=k#frag", "http://h/x"), ("http://a:p@w@h/x", "http://h/x"), ("http://h/v1?token=x", "http://h/v1"),
    ("ftp://u:p@h", "ftp://h"), ("//u:p@h/p", "//h/p"), ("http://u@h:1/v1", "http://h:1/v1"), ("https://user:pa ss@h/v1", "https://h/v1"),
    ("http://h:8080", "http://h:8080"), ("https://h:443/v1", "https://h:443/v1"), ("http://h:0/v1", "http://h:0/v1"),
    ("http://h:99999/v1", "(unparseable endpoint)"), ("http://h:abc/v1", "(unparseable endpoint)"),
    # Display quirks of the rule, pinned byte-for-byte: an empty authority loses its '//', a newline is dropped by urlsplit.
    # S20 (OI-152, deliberate pin change): an IPv6 host keeps its brackets and an explicit port 0 is shown (both were dropped).
    ("http://[::1]:11434/v1", "http://[::1]:11434/v1"), ("unix:///var/run/ollama.sock", "unix:/var/run/ollama.sock"),
    ("http://h\n/v1", "http://h/v1"), ("localhost:11434", "localhost:11434"), ("", ""), ("no scheme at all", "no scheme at all"),
    ("mailto:a@b", "mailto:a@b"), ("http://ünï.example/v1", "http://ünï.example/v1"),
]


@pytest.mark.parametrize("url, shown", ENDPOINTS)
def test_the_endpoint_display_rule_keeps_its_bytes(url, shown):
    """The name the KUP model probe and the application's routing rows use, resolved where each caller resolves it."""
    import kriya.application.model as model
    import kriya.kup.context_ops as context_ops

    assert context_ops.safe_endpoint(url) == shown
    assert model.safe_endpoint(url) == shown


@pytest.mark.parametrize("source, shown", [
    ("human_url:http://alice:pw@127.0.0.1:9/v1", "human_url:http://127.0.0.1:9/v1"),
    ("human_url:https://tok@github.com/o/r?x=1#f", "human_url:https://github.com/o/r"),
    ("human_url:http://h:99999/v1", "human_url:unparseable-url"), ("human_url:http://[::1]:11434/v1", "human_url:http://[::1]:11434/v1"),
    ("https://u:p@h/x", "https://h/x"), ("human_url://u:p@h/p", "human_url:p"), ("human_file:/Users/a/secret.md", "human_file:secret.md"),
    ("plain words", "plain words"),
])
def test_the_skill_source_display_keeps_its_bytes(source, shown):
    from kriya.skills.service import _safe_source

    assert _safe_source(source) == shown


# --------------------------------------------------------------------------- OI-146: the two raw-content surfaces

LID = "20261020T101010-abcdef01"
SECRET_DIFF = ("--- a/conf/app.env\n+++ b/conf/app.env\n@@ -1,2 +1,3 @@\n APP=demo\n-DB_PASSWORD=old\n"
               f"+DB_PASSWORD={S}1\n+SLACK=xoxb-1234567890-0987654321-{S}2\n")
SECRET_FILES = [{"filepath": "conf/app.env", "content": SECRET_DIFF}]


def _hold_change_review(ws, files, reason, scenario):
    """A real launch session in a real run lock: the change gate asks for a decision, ``scenario(canonical, pending)`` plays
    the desktop host, then rejects (test_kup_change_review's protocol)."""
    import asyncio

    from kriya.context.workspace_identity import canonical_workspace, workspace_identity
    from kriya.control import launch as launch_module
    from kriya.control.launch import LaunchSession
    from kriya.control.persistence import load_launch_record
    from kriya.control.run_coordinator import begin_mutating_run

    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), LID, "goal", {"ready": True}, {})
    attach_review_channel(session)
    session.open()

    async def main_():
        awaiting = launch_module.request_change_approval(files, reason)
        assert awaiting is not None
        task = asyncio.ensure_future(awaiting)
        for _ in range(500):
            pending = (load_launch_record(canonical, LID) or {}).get("pending_approval")
            if pending:
                break
            await asyncio.sleep(0.01)
        await asyncio.to_thread(scenario, canonical, pending)
        await asyncio.to_thread(channel_decide, canonical, LID, pending["review_id"], pending["candidate_digest"], "reject")
        return await asyncio.wait_for(task, 20)

    try:
        with begin_mutating_run(canonical):
            approved = asyncio.run(main_())
    finally:
        session.finish(0, None, None)
    assert approved == ApprovalOutcome.decline()  # the person's reject: DECLINED
    return canonical


def _change_review(ws, review_id):
    from kriya.config import AppConfig
    from kriya.kup.cli_ops import run_kup_operation

    return run_kup_operation(AppConfig(), {"operation": "change_approval.review", "workspace": str(ws), "launch_id": LID,
                                           "review_id": review_id})


def test_the_approval_diff_is_served_raw_and_byte_faithful_and_persisted_only_as_digests(tmp_path, monkeypatch, caplog):
    """OI-146 (INTENTIONAL_CONTRACT): reviewed bytes must equal approved bytes, so the change review shows the diff RAW - a
    redactor applied to it fails this test (S2c/S20 never alter approval bytes). What Kriya keeps about the review in its
    launch record and decision document is identity (digests, counts, decision), never the diff text; reading the review
    logs nothing that carries it. The review-diff sidecar (<authority home>/launches/<identity>/<id>.review-diff-<rid>.json) is the one raw
    copy, read only by this operation after re-proving its digests (traced in S2c-DECISION-TO-KILLER.md)."""
    from kriya.control.persistence import launch_record_path, launch_review_decision_path, launch_review_diff_path
    from kriya.policy.approval import candidate_digest

    ws = tmp_path / "proj"
    ws.mkdir()
    seen = {}

    def scenario(canonical, pending):
        with caplog.at_level(logging.DEBUG):
            seen["env"] = _change_review(canonical, pending["review_id"])
        seen["pending"] = pending

    canonical = _hold_change_review(ws, SECRET_FILES, "Human-in-the-loop review policy", scenario)
    env, pending = seen["env"], seen["pending"]
    assert env["error"] is None, env["error"]
    data = env["data"]
    assert data["verified"] is True and data["candidate_digest"] == candidate_digest(SECRET_FILES)
    lines = [line for h in data["files"][0]["hunks"] for line in h["lines"]]
    rebuilt = "".join({"add": "+", "remove": "-", "context": " "}[line["kind"]] + line["text"] + "\n" for line in lines)
    assert rebuilt == SECRET_DIFF.split("@@\n", 1)[1]                          # byte-faithful, the secret included
    assert f"+DB_PASSWORD={S}1" in SECRET_DIFF and f"DB_PASSWORD={S}1" in json.dumps(data)
    record = open(launch_record_path(canonical, LID), encoding="utf-8").read()
    decision = open(launch_review_decision_path(canonical, LID, pending["review_id"]), encoding="utf-8").read()
    assert S not in record and S not in decision                               # identity only: digests, counts, decision
    assert S in open(launch_review_diff_path(canonical, LID, pending["review_id"]), encoding="utf-8").read()
    assert all(S not in r.getMessage() for r in caplog.records)


def test_the_terminal_approval_prompt_shows_the_diff_raw(monkeypatch, capsys):
    """The CLI form of the same approval surface (outside a desktop launch): the person reads the exact lines."""
    import click

    from kriya.cli import _approval_decision

    monkeypatch.setattr(click, "prompt", lambda *a, **k: "n")
    assert _approval_decision(SECRET_FILES, "policy", yes=False) is False
    assert f"+DB_PASSWORD={S}1" in capsys.readouterr().out


def _content_store(run_id):
    from kriya.config import AppConfig
    from kriya.core.attempt_evidence import model
    from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter
    from kriya.core.state_paths import resolve_state_directory

    writer = AttemptEvidenceWriter(resolve_state_directory(AppConfig())[0], run_id, capture=model.CAPTURE_FULL, manifest={"workspace_name": "f"})
    writer.append("run.opened", {})
    ids = {"unit_id": "u1", "unit_kind": "task", "invocation_seq": 1, "phase": "attempt", "attempt_number": 1, "role": "developer"}
    seqs = {"change": writer.append("candidate.change", {"decision": "STAGED", "path": "conf/app.env"}, identity=ids,
                                    content={"diff": SECRET_DIFF}),
            "call": writer.append("model.request", {"n": 1}, identity={**ids, "call_seq": 1},
                                  content={"messages": f"use DB_PASSWORD={S}3 in the test"})}
    assert writer.seal("run_closed")
    return seqs


def _content(operation, run_id, seq, part, visibility):
    from _strict_doubles import strict_config

    from kriya.kup.cli_ops import run_kup_operation

    cfg = strict_config(evidence={"attempt_recorder": {"content_visibility": visibility}}) if visibility else strict_config()
    return run_kup_operation(cfg, {"operation": operation, "run_id": run_id, "seq": seq, "part": part})


@pytest.mark.parametrize("operation, key, part, expected", [
    ("evidence.change_content", "change", "diff", SECRET_DIFF), ("evidence.call_content", "call", "messages", f"use DB_PASSWORD={S}3 in the test"),
], ids=["change", "call"])
def test_content_visibility_on_serves_the_recorded_bytes_raw(operation, key, part, expected):
    seqs = _content_store("s2c-on")
    env = _content(operation, "s2c-on", seqs[key], part, "on")
    assert env["error"] is None and env["data"]["content"] == expected


@pytest.mark.parametrize("visibility", ["off", None], ids=["off", "default"])
@pytest.mark.parametrize("operation, key, part", [("evidence.change_content", "change", "diff"), ("evidence.call_content", "call", "messages")],
                         ids=["change", "call"])
def test_content_visibility_off_or_default_withholds_never_redacts(operation, key, part, visibility):
    """WITHHOLDS: a typed refusal with no content at all - not a redacted copy (a redacted copy would still disclose the
    rest of the bytes and suggest it is the recorded content)."""
    from kriya.kup import policy

    seqs = _content_store("s2c-off")
    env = _content(operation, "s2c-off", seqs[key], part, visibility)
    assert env["data"] is None and env["error"]["code"] == policy.EVIDENCE_CONTENT_POLICY
    assert env["error"]["reason"] == "content_visibility_off" and S not in json.dumps(env) and R not in json.dumps(env)


def test_an_opt_in_content_read_writes_nothing_and_logs_nothing(caplog):
    from kriya.config import AppConfig
    from kriya.core.state_paths import resolve_state_directory

    seqs = _content_store("s2c-quiet")
    state = resolve_state_directory(AppConfig())[0]

    def tree():
        return sorted((os.path.relpath(os.path.join(d, f), state), os.path.getsize(os.path.join(d, f)))
                      for d, _, fs in os.walk(state) for f in fs)

    before = tree()
    with caplog.at_level(logging.DEBUG):
        assert _content("evidence.change_content", "s2c-quiet", seqs["change"], "diff", "on")["error"] is None
        assert _content("evidence.call_content", "s2c-quiet", seqs["call"], "messages", "on")["error"] is None
    assert tree() == before and all(S not in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- OI-144 (4) = OI-153: decided, flipped by S2d

POSITIONAL = [
    ("mysql-p", f"mysql -u root -p{S} db", ["mysql", "-u", "root", f"-p{S}", "db"]),
    ("sshpass-p", f"sshpass -p {S} ssh host", ["sshpass", "-p", S, "ssh", "host"]),
    ("docker-login-p", f"docker login -u alice -p {S} registry", ["docker", "login", "-u", "alice", "-p", S, "registry"]),
    ("npm-authtoken", f"npm config set //registry.npmjs.org/:_authToken {S}", ["npm", "config", "set", "//registry.npmjs.org/:_authToken", S]),
]


@pytest.mark.parametrize("text, argv", [(t, a) for _, t, a in POSITIONAL], ids=[i for i, _, _ in POSITIONAL])
def test_a_positional_secret_would_be_hidden_in_text_and_argv(text, argv):
    """S2d (OI-153, owner decision (b)) flipped this strict-xfail pin: the owner reads each form through the command's own
    grammar. Strengthened from `or` to the exact bytes of BOTH forms (the pin passed with either path alone)."""
    assert redact_text(text) == text.replace(S, R) and redact_value(argv) == [R if a == S else a.replace(S, R) for a in argv]


@pytest.mark.parametrize("argv", [a for _, _, a in POSITIONAL], ids=[i for i, _, _ in POSITIONAL])
def test_a_runtime_command_with_a_positional_secret_would_not_be_offered_for_review(argv):
    """S2d (OI-153, owner decision (c)) flipped this strict-xfail pin: the review is not offered (the S2b rule, now through
    the authority's `commands_carry_secret`), so nothing is recorded raw and runtime_command.review has nothing to show."""
    from kriya.control.launch_approval import build_pending_runtime
    from kriya.policy.approval import RuntimeCommandSubject

    assert build_pending_runtime("l", "w", 1, RuntimeCommandSubject("u1", "c", "/ws", (tuple(argv),), "none", None, "r")) is None


# =========================================================================== EXPECTED TO FLIP (the measured defects)

COMPOUND = [
    ("pgpassword", f"PGPASSWORD={S} psql -h db", f"PGPASSWORD={R} psql -h db"),
    ("export-pgpassword", f"export PGPASSWORD={S}", f"export PGPASSWORD={R}"),
    ("githubtoken", f"GITHUBTOKEN={S}", f"GITHUBTOKEN={R}"),
    ("npmtoken-colon", f"NPMTOKEN: {S}", f"NPMTOKEN: {R}"),
    ("awssecretaccesskey", f"AWSSECRETACCESSKEY={S}", f"AWSSECRETACCESSKEY={R}"),
    ("clientsecret", f"CLIENTSECRET={S}", f"CLIENTSECRET={R}"),
    ("passphrase", f"SSHPASSPHRASE='a {S}'", f"SSHPASSPHRASE='{R}'"),
    ("json", json.dumps({"PGPASSWORD": S, "PGHOST": "db"}), json.dumps({"PGPASSWORD": R, "PGHOST": "db"})),
    ("flag", f"run --PGPASSWORD {S} x", f"run --PGPASSWORD {R} x"),
]


@pytest.mark.parametrize("text, expected", [(t, e) for _, t, e in COMPOUND], ids=[i for i, _, _ in COMPOUND])
def test_a_compound_all_caps_secret_name_is_hidden(text, expected):
    assert redact_text(text) == expected


def test_a_compound_all_caps_secret_key_is_hidden_in_a_structure():
    assert redact_value({"PGPASSWORD": S, "PGHOST": "db", "argv": ["--GITHUBTOKEN", S]}) == {
        "PGPASSWORD": R, "PGHOST": "db", "argv": ["--GITHUBTOKEN", R]}



@pytest.mark.parametrize("text, expected", [("NOTOKEN=1", f"NOTOKEN={R}"), ("TOPSECRET=x", f"TOPSECRET={R}"), ("MAXTOKEN=5", f"MAXTOKEN={R}"),
                                            ("token%3Dabc%3Dxyz", f"token%3D{R}"), ("x%3Dpassword%3Dsecret", f"x%3Dpassword%3D{R}")])
def test_the_measured_cost_of_the_new_rules_is_fail_closed(text, expected):
    """The price of the compound rule (a compound name that merely ENDS in a long secret word is hidden) and the proof
    that an encoded separator never joins a name, so a secret pair is never swallowed by a longer non-secret name."""
    assert redact_text(text) == expected

VENDOR = [
    ("slack-bot", f"token xoxb-1234567890-0987654321-{S}abcd", f"token {R}"),
    ("slack-user", f"xoxp-1234567890-0987654321-1111-{S}", R),
    ("gitlab-pat", f"use glpat-{S}abcdefghij12 now", f"use {R} now"),
    ("npm", "npm_" + S + "abcdefghijklmnopqrstuvwxyz", R),
    ("openai-project", f"sk-proj-{S}abcdefghijklmnopqrstuvwxyz_0123", R),
    ("anthropic", f"key=sk-ant-api03-{S}abcdefghijklmnopqrstuvwxyz-0123", f"key={R}"),
    ("stripe-live", f"sk_live_{S}abcdefghijklmnop", R),
    ("stripe-restricted-test", f"rk_test_{S}abcdefghijklmnop", R),
    ("google", "AIza" + S + "abcdefghijklmnopqrstuvwxy", R),
    ("hugging-face", "hf_" + S + "abcdefghijklmnopqrstuvwx", R),
]


@pytest.mark.parametrize("text, expected", [(t, e) for _, t, e in VENDOR], ids=[i for i, _, _ in VENDOR])
def test_a_vendor_token_shape_is_hidden(text, expected):
    assert redact_text(text) == expected


def test_the_token_shape_and_compound_name_tables_are_closed():
    """Tripwire: adding, widening or dropping a shape or a compound suffix is a reviewed change of this table."""
    from kriya.foundation import redaction

    assert tuple(p.pattern for p in redaction._SHAPED_SECRETS) == (
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bsk-[A-Za-z0-9]{20,}\b",
        r"\bgh[pousr]_[A-Za-z0-9]{20,}\b",
        r"\bgithub_pat_[A-Za-z0-9_]{20,}\b",
        r"\bsk-(?:proj|svcacct|admin|ant)-[A-Za-z0-9_\-]{20,}",
        r"\bxox[abprs]-[A-Za-z0-9\-]{10,}",
        r"\bglpat-[A-Za-z0-9_\-]{20,}",
        r"\bnpm_[A-Za-z0-9]{36}\b",
        r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{20,}",
        r"\bAIza[0-9A-Za-z_\-]{35}(?![0-9A-Za-z_\-])",
        r"\bhf_[A-Za-z0-9]{34}\b",
    )
    assert getattr(redaction, "_COMPOUND_SUFFIXES", None) == (
        "PASSWORD", "PASSPHRASE", "SECRET", "TOKEN", "CREDENTIAL", "CREDENTIALS", "APIKEY", "ACCESSKEY", "PRIVATEKEY", "SECRETKEY")


ENCODED = [
    ("nested-url", f"https://a/login?next=https%3A%2F%2Falice%3A{S}%40h%2Fp&x=1", f"https://a/login?next=https%3A%2F%2F{R}%40h%2Fp&x=1"),
    ("nested-url-lower", f"https://a/l?next=https%3a%2f%2falice%3a{S}%40h", f"https://a/l?next=https%3a%2f%2f{R}%40h"),
    ("nested-url-token-user", f"cb=git%2Bssh%3A%2F%2F{S}%40host%2Fr", f"cb=git%2Bssh%3A%2F%2F{R}%40host%2Fr"),
    ("encoded-name", f"https://h/x?%70assword={S}", f"https://h/x?%70assword={R}"),
    ("encoded-name-underscore", f"https://h/x?api%5Fkey={S}&p=2", f"https://h/x?api%5Fkey={R}&p=2"),
    ("encoded-name-and-separator", f"q=pass%77ord%3D{S}", f"q=pass%77ord%3D{R}"),
    ("nested-encoded-query", f"next=%2Fcb%3Faccess_token%3D{S}%26p%3D2", f"next=%2Fcb%3Faccess_token%3D{R}"),
]


@pytest.mark.parametrize("text, expected", [(t, e) for _, t, e in ENCODED], ids=[i for i, _, _ in ENCODED])
def test_a_percent_encoded_secret_is_found_and_never_shown_decoded(text, expected):
    """Decode for DETECTION only: the output keeps the bytes as written around the marker, never a decoded copy. An encoded
    bare value is hidden to the end of its parameter (the encoded '&' is part of the value: fail-closed)."""
    assert redact_text(text) == expected


def test_there_is_one_endpoint_display_rule():
    """OI-145: ONE implementation at the redaction owner (``kriya.client.safe_endpoint`` was already the application's
    function through two lazy tables - MEASURED; the real second copy was kriya/skills/service.py::_safe_source). No
    re-export: the client and application tables no longer carry the name; callers import the owner. The identity
    canonicalizer ``core.model_runtime.endpoint_identity`` is digest input, not display, and keeps its own bytes."""
    import kriya.application
    import kriya.application.model as model
    import kriya.client
    from kriya.foundation import redaction

    assert model.safe_endpoint is getattr(redaction, "safe_endpoint", None)
    assert "safe_endpoint" not in kriya.client.__all__ and "safe_endpoint" not in kriya.application.__all__
    assert "safe_endpoint" not in model.__all__
    copies = []
    for directory, _, files in os.walk(os.path.join(REPO_ROOT, "kriya")):
        for name in files:
            path = os.path.join(directory, name)
            rel = os.path.relpath(path, REPO_ROOT)
            if name.endswith(".py") and rel not in (os.path.join("kriya", "foundation", "redaction.py"),
                                                    os.path.join("kriya", "core", "model_runtime.py")):
                if re.search(r'netloc = f"\{host\}:\{', open(path, encoding="utf-8").read()):
                    copies.append(rel)
    assert copies == []


# --------------------------------------------------------------------------- survey: projections the S2b table missed

def test_the_launch_status_the_gui_receives_hides_a_recorded_secret(tmp_path):
    """MEASURED (S2c channels): a runtime command carrying `--token x` is NOT offered for review (S2b), and the CLI callback
    then records it as `approval_required` details - raw - in the launch record; launch.status served that record as is.
    The APPLICATION projection now redacts (the OI-131 rule). S2d (OI-153 (c)): that refusal path no longer persists the raw
    command or the reason quoting it - it records the operator refusal, the REDACTED commands and the fingerprint (the two
    assertions below that encoded the raw record changed; the projection's killer for a note still stored raw is
    tests/test_s2d_positional_secrets.py::test_a_note_still_recorded_raw_is_projected_redacted_by_launch_status)."""
    from kriya.cli import _runtime_command_decision
    from kriya.config import AppConfig
    from kriya.context.workspace_identity import canonical_workspace, workspace_identity
    from kriya.control.launch import LaunchSession
    from kriya.control.persistence import launch_record_path
    from kriya.kup.cli_ops import run_kup_operation
    from kriya.kup.inspect import encode_response
    from kriya.policy.approval import RUNTIME_COMMAND_SECRET_REFUSAL, RuntimeCommandSubject

    ws = tmp_path / "proj"
    ws.mkdir()
    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), LID, "goal", {"ready": True}, {})
    attach_review_channel(session)
    session.open()
    try:
        decision = _runtime_command_decision(RuntimeCommandSubject("u2", "c", canonical, (("run.sh", "--token", S),), "none", None,
                                                                   f"needs DB_PASSWORD={S}"), yes=False)
        assert decision.approved_fingerprint is None
        assert S not in open(launch_record_path(canonical, LID), encoding="utf-8").read()   # S2d: the refusal stores no raw command
        envelope = run_kup_operation(AppConfig(), {"operation": "launch.status", "workspace": canonical, "launch_id": LID})
    finally:
        session.finish(0, None, None)
    assert envelope["error"] is None, envelope["error"]
    required = envelope["data"]["launch"]["approval_required"]
    assert required["details"]["commands"] == [f"run.sh --token {R}"] and required["reason"] == RUNTIME_COMMAND_SECRET_REFUSAL
    assert S not in encode_response(envelope)


def test_the_evidence_records_the_gui_receives_hide_a_secret_in_a_record_payload():
    """MEASURED: a record's envelope payload is content-free by design, but a free-text field (tool.execution ``error``)
    can quote a credential; evidence.records served it raw. Redacted at the APPLICATION page (before the byte budget is
    counted); the stored chain and its digests stay raw; content stays behind content_visibility."""
    from kriya.config import AppConfig
    from kriya.core.attempt_evidence import model
    from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter
    from kriya.core.state_paths import resolve_state_directory
    from kriya.kup.cli_ops import run_kup_operation
    from kriya.kup.inspect import encode_response

    writer = AttemptEvidenceWriter(resolve_state_directory(AppConfig())[0], "s2c-rec", capture=model.CAPTURE_FULL, manifest={"workspace_name": "f"})
    writer.append("run.opened", {})
    writer.append("tool.execution", {"tool_name": "shell", "status": "FAILED", "error": f"curl -H 'Authorization: Token {S}' failed",
                                     "reason_codes": [], "tool_output": {}}, content={"tool_output": "x"})
    assert writer.seal("run_closed")
    envelope = run_kup_operation(AppConfig(), {"operation": "evidence.records", "run_id": "s2c-rec"})
    assert envelope["error"] is None, envelope["error"]
    record = envelope["data"]["records"][1]
    assert record["payload"]["error"] == f"curl -H 'Authorization: {R} {R}' failed" and record["payload"]["tool_name"] == "shell"
    assert S not in encode_response(envelope)


def test_the_change_review_reason_is_redacted_while_its_diff_stays_raw(tmp_path, monkeypatch):
    """Strict scope of OI-146: the DIFF is the raw surface; the review's reason (policy note + the automated review, model
    text) is not approval bytes (not in the candidate digest) and goes through the owner like every other display text."""
    ws = tmp_path / "proj"
    ws.mkdir()
    seen = {}
    reason = f"Human-in-the-loop review policy\n\n=== Automated Code Review ===\nThe change sets DB_PASSWORD={S}9 in app.env."
    _hold_change_review(ws, SECRET_FILES, reason, lambda c, p: seen.setdefault("env", _change_review(c, p["review_id"])))
    data = seen["env"]["data"]
    assert data["reason"] == reason.replace(f"{S}9", R) and data["review_summary"] == f"The change sets DB_PASSWORD={R} in app.env."
    assert data["policy_note"] == "Human-in-the-loop review policy" and f"DB_PASSWORD={S}1" in json.dumps(data)


def test_the_terminal_approval_prompt_redacts_its_reason_not_its_diff(monkeypatch, capsys):
    import click

    from kriya.cli import _approval_decision

    monkeypatch.setattr(click, "prompt", lambda *a, **k: "n")
    _approval_decision(SECRET_FILES, f"review: DB_PASSWORD={S}9", yes=False)
    _approval_decision([], f"auto: api_key={S}8", yes=True)
    out = capsys.readouterr().out
    assert f"{S}9" not in out and f"{S}8" not in out and f"DB_PASSWORD={R}" in out and f"+DB_PASSWORD={S}1" in out



@pytest.mark.parametrize("yes", [False, True], ids=["prompt", "yes"])
def test_the_proposal_execution_prompt_redacts_its_reason_not_its_diff(tmp_path, monkeypatch, yes):
    """`kriya proposal execute` keeps its own copy of the terminal prompt (kept inline: tests/test_kup_execution.py reads
    `_approval_decision`'s source for the record-before-prompt order); it follows the same OI-146 rule. Driven through the
    CLI with the promotion use case replaced by one that asks the callback exactly as the generation workflow would."""
    from click.testing import CliRunner

    import kriya.workflow.proposal_promotion as promotion
    from kriya.cli import main

    seen = {}

    async def fake_execute(proposal_id, workspace, we, **kwargs):
        seen["approved"] = kwargs["approval_callback"](SECRET_FILES, f"review: DB_PASSWORD={S}9")
        return {"quality_gates_passed": True}

    monkeypatch.setattr(promotion, "execute_approved_proposal", fake_execute)
    monkeypatch.chdir(tmp_path)
    args = ["proposal", "execute", "p1"] + (["-y"] if yes else [])
    out = CliRunner().invoke(main, args, input="n\n", catch_exceptions=False).output
    assert seen["approved"] is yes and f"{S}9" not in out and f"DB_PASSWORD={R}" in out
    assert (f"+DB_PASSWORD={S}1" in out) is (not yes)                     # the diff lines are shown raw when prompted
