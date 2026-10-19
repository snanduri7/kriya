"""S2 (OI-98, registry SEC-CLI-URL-CREDENTIAL-REDACTION-001): one redaction owner.

Owner decision (2026-10-19): URL userinfo and every secret-bearing value are never displayed or logged; ONE redaction
owner; no per-field special cases; no re-export laundering.

Characterization FIRST (S1 method). Two kinds of test live here:
  * pins of today's governed redaction, green before and after the correction (api_key and every env value hidden with
    the key names kept, the approval artifact never storing a value, the identity/display canonicalizers that already
    drop userinfo, the readable values the telemetry scrubber promises to keep, launch-approval reviewability);
  * EXPECTED-TO-FLIP pins: the measured defect (every channel below printed or logged `user:password@`), written as
    the corrected behaviour and marked strict-xfail (AssertionError only) at the characterization commit 9e24597,
    shown failing on the unfixed bytes (--runxfail: 22 failed, each printing the secret); the correction removed the marks;
  * (added with the correction) the owner's own rule contracts and the one-owner tripwire the mutants run against.
Every channel is driven through its production entry (the CLI command, the record builder, the handler formatter, the
KUP envelope), never through a private helper, so the pins survive the removal of the three old redactors.
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
import urllib.error
from unittest.mock import AsyncMock

import pytest
import yaml
from click.testing import CliRunner

from kriya.config import authority_approval as aa
from kriya.config.authority import ConfigAuthorityViolation, ConfigSource, FieldClassification
from kriya.config.config import AppConfig, resolve_config_state

PW = "s3cretPW"
EMB_PW = "emb3dPW"
SRCH_PW = "srchPW"
KEY = "TOPSECRETKEY"
ENV_SECRET = "envSECRET"
LLM_URL = f"http://alice:{PW}@127.0.0.1:9/v1"
EMB_URL = f"http://bob:{EMB_PW}@127.0.0.1:9"
SRCH_URL = f"https://carol:{SRCH_PW}@search.internal/"
SECRETS = (PW, EMB_PW, SRCH_PW, KEY, ENV_SECRET)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _homes(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "_authority_home"))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)


def _workspace(tmp_path, monkeypatch, data):
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "kriya.yaml").write_text(yaml.safe_dump(data))
    monkeypatch.chdir(ws)
    return ws


def _credential_workspace(tmp_path, monkeypatch):
    return _workspace(tmp_path, monkeypatch, {
        "llm": {"base_url": LLM_URL, "api_key": KEY, "model": "readable-model"},
        "embedding": {"base_url": EMB_URL},
        "search": {"base_url": SRCH_URL},
        "mcp": {"srv": {"command": "/usr/bin/true", "args": ["--port", "1"], "env": {"API_TOKEN": ENV_SECRET}}},
    })


def _approve():
    state = resolve_config_state()
    artifact = aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root)
    path = aa.default_local_approval_path(state.workspace_root)
    aa.save_approval_artifact(path, artifact)
    return path


def _cli(args, stdin=None):
    from kriya.cli import main

    return CliRunner().invoke(main, args, input=stdin, catch_exceptions=False)


def _leaked(text):
    return [s for s in SECRETS if s in text]


def _pending(config_dict, *field_paths):
    violations = [ConfigAuthorityViolation(p, ConfigSource.AUTO_DISCOVERED_CWD, FieldClassification.SECURITY_AUTHORITY, "r")
                  for p in field_paths]
    return {p.field_path: p.redacted_value for p in aa.describe_pending(violations, config_dict)}


# =========================================================================== governed behaviour that must NOT change

def test_api_key_and_every_mcp_env_value_stay_hidden_with_their_key_names_kept():
    shown = _pending({"llm": {"api_key": KEY, "model": "m"},
                      "mcp": {"srv": {"command": "/usr/bin/true", "env": {"API_TOKEN": ENV_SECRET, "UNSET": ""}}}},
                     "mcp.srv")
    assert shown["mcp.srv"]["env"] == {"API_TOKEN": "***REDACTED***", "UNSET": ""}     # names kept, empty value shown
    assert shown["mcp.srv"]["command"] == "/usr/bin/true"
    whole = _pending({"llm": {"api_key": KEY, "model": "m"}}, "llm")
    assert whole["llm"] == {"api_key": "***REDACTED***", "model": "m"}


def test_the_config_command_hides_api_key_and_keeps_ordinary_values_readable(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch, {"llm": {"api_key": KEY, "model": "readable-model", "max_tokens": 1234}})
    out = _cli(["config"]).output
    dumped = json.loads(out)
    assert dumped["llm"]["api_key"] == "***REDACTED***"
    assert dumped["llm"]["model"] == "readable-model" and dumped["llm"]["max_tokens"] == 1234
    assert dumped["llm"]["base_url"] == AppConfig().llm.base_url                       # a credential-free URL stays readable
    assert KEY not in out


def test_the_durable_approval_artifact_never_stores_a_configured_value(tmp_path, monkeypatch):
    _credential_workspace(tmp_path, monkeypatch)
    path = _approve()
    text = open(path, encoding="utf-8").read()
    assert _leaked(text) == [] and "alice" not in text and "API_TOKEN" in text          # env NAMES only; values digested


def test_the_endpoint_canonicalizers_already_drop_userinfo_and_keep_their_bytes():
    from kriya.core.model_runtime import endpoint_identity
    from kriya.foundation.redaction import safe_endpoint

    assert safe_endpoint(LLM_URL) == "http://127.0.0.1:9/v1"
    assert endpoint_identity("http://user:secret@LOCALHOST:11434/v1/?k=v#x") == "http://localhost:11434/v1"


def test_a_plain_runtime_command_stays_reviewable_and_a_token_argument_does_not():
    from kriya.control.launch_approval import build_pending_runtime
    from kriya.policy.approval import RuntimeCommandSubject

    def subject(*argv):
        return RuntimeCommandSubject("u1", "criterion", "/ws", (tuple(argv),), "none", None, "reason")

    assert build_pending_runtime("l", "w", 1, subject("java", "-jar", "app.jar", "http://127.0.0.1:8080/health")) is not None
    assert build_pending_runtime("l", "w", 1, subject("run.sh", "--token=hunter2supersecret")) is None


def test_the_egress_record_keeps_hashes_and_plain_destinations_readable():
    from kriya.policy.egress import EgressCapability, EgressDecision

    sha = "a" * 64
    record = EgressDecision("model", EgressCapability.EXPLICIT_DESTINATIONS, True, "config",
                            destinations=("http://127.0.0.1:11434/v1",), reason=f"manifest {sha}").to_dict()
    assert record["destinations"] == ["http://127.0.0.1:11434/v1"] and record["reason"] == f"manifest {sha}"


@pytest.mark.parametrize("value", [
    {"max_tokens": 4096, "planner_max_tokens": 900, "token_budget": 12, "tokenizer": "bpe"},   # segment, not substring
    {"digest": "sha256:" + "ab" * 32, "commit": "1c2cfe2a4b9e8f0d3c5a7b6e9f1d2c3b4a5e6f7d"},  # hashes are not secrets
    {"base_url": "http://localhost:11434/v1", "health": "https://example.com/a@b"},           # no userinfo (`@` in the path)
    {"owner": "dev@example.com", "remote": "git@github.com:org/repo.git"},                    # email, scp-style remote
    {"author": "secretary", "keyboard": "us", "passage": "text", "monkey": "x"},              # names that merely contain a word
    {"env_keys": ["API_TOKEN"], "authority_source": "operator"},                              # names, not values
], ids=["token-counts", "hashes", "plain-urls", "email-scp", "lookalike-names", "names-only"])
def test_values_without_a_secret_stay_readable_in_the_authority_display(value):
    assert _pending({"mcp": {"srv": value}}, "mcp.srv")["mcp.srv"] == value


# =========================================================================== EXPECTED TO FLIP (the measured defect)

def test_authority_inspect_never_prints_url_credentials(tmp_path, monkeypatch):
    _credential_workspace(tmp_path, monkeypatch)
    out = _cli(["authority", "inspect"]).output
    assert "llm.base_url" in out and "127.0.0.1:9" in out and "search.internal" in out    # still says WHAT and WHERE
    assert _leaked(out) == [], out


def test_authority_approve_never_prints_url_credentials(tmp_path, monkeypatch):
    _credential_workspace(tmp_path, monkeypatch)
    out = _cli(["authority", "approve"], stdin="n\n").output
    assert "llm.base_url" in out
    assert _leaked(out) == [], out


def test_the_config_command_never_prints_url_credentials(tmp_path, monkeypatch):
    _credential_workspace(tmp_path, monkeypatch)
    _approve()
    out = _cli(["config"]).output
    dumped = json.loads(out)
    assert dumped["llm"]["model"] == "readable-model" and "127.0.0.1:9" in dumped["llm"]["base_url"]
    assert _leaked(out) == [], out


def test_doctor_never_prints_url_credentials(tmp_path, monkeypatch):
    import kriya.tools.validate as validate

    _credential_workspace(tmp_path, monkeypatch)
    _approve()
    monkeypatch.setattr(validate, "check_java_toolchain", lambda: {"java_found": False, "mvn_found": False, "mismatch": False})
    out = _cli(["doctor"]).output
    assert "Base URL:" in out and "127.0.0.1:9" in out
    assert _leaked(out) == [], out


def test_the_production_report_never_carries_url_credentials():
    from kriya import production_doctor as pd

    cfg = AppConfig()
    cfg.llm.base_url = "http://alice:" + PW + "@8.8.8.8/v1"                       # not local: the probe refuses
    with pytest.raises(Exception) as refused:
        pd.probe_llm_runtime(cfg)
    assert "8.8.8.8" in str(refused.value) and PW not in str(refused.value)
    cfg.embedding.base_url = EMB_URL
    check = pd._check_egress(pd._Context(cfg=cfg, workspace="/ws"))
    text = json.dumps(check.evidence)
    assert "8.8.8.8" in text and _leaked(text) == [], text


def test_the_log_handlers_never_write_url_credentials(tmp_path):
    from kriya.core import logging_setup

    path = str(tmp_path / "logs" / "kriya.log")
    handler = logging_setup._open_file_handler(path, logging.INFO)
    record = logging.LogRecord("kriya.x", logging.WARNING, __file__, 1, "observation failed at %s (%s)",
                               (LLM_URL, f"Authorization: Bearer {KEY}"), None)
    handler.handle(record)
    handler.close()
    text = open(path, encoding="utf-8").read()
    assert "observation failed at http://" in text and "127.0.0.1:9" in text
    assert _leaked(text) == [], text


def test_the_console_log_never_writes_url_credentials(tmp_path):
    """A fresh interpreter: configure_logging is a no-op under pytest (the root logger already has handlers)."""
    code = ("import logging; from kriya.config.config import AppConfig; from kriya.core.logging_setup import configure_logging; "
            "cfg = AppConfig(); cfg.logging.file_enabled = False; cfg.logging.run_file_enabled = False; configure_logging(cfg); "
            f"logging.getLogger('kriya.x').warning('failed at %s', {LLM_URL!r})")
    env = dict(os.environ, PYTHONPATH=REPO_ROOT, KRIYA_LOG_DIR=str(tmp_path / "logs"))
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120,
                          stdin=subprocess.DEVNULL, check=False)
    assert done.returncode == 0, done.stderr
    assert "failed at http://" in done.stderr and _leaked(done.stderr) == [], done.stderr


def _validation_error():
    from pydantic import ValidationError

    try:
        AppConfig.model_validate({"llm": {"temperature": LLM_URL}})
    except ValidationError as error:
        return error
    raise AssertionError("expected a validation error")


def test_a_configuration_error_message_never_echoes_url_credentials():
    from kriya.cli_commands.common import _user_error_text

    text = _user_error_text(_validation_error())
    assert "temperature" in text and _leaked(text) == [], text


def test_a_kup_error_envelope_never_carries_url_credentials():
    from kriya.kup.inspect import error_envelope

    env = error_envelope("workspace.describe", "r1", "CONFIG_LOAD_FAILED", str(_validation_error()))
    text = json.dumps(env)
    assert "CONFIG_LOAD_FAILED" in text and "temperature" in text and _leaked(text) == [], text


def test_policy_records_never_carry_url_credentials():
    from kriya.policy.egress import EgressCapability, EgressDecision

    record = EgressDecision("model", EgressCapability.EXPLICIT_DESTINATIONS, True, "config", destinations=(LLM_URL,),
                            reason=f"refused {EMB_URL}").to_dict()
    text = json.dumps(record)
    assert "127.0.0.1:9" in text and _leaked(text) == [], text


def test_a_runtime_command_carrying_url_credentials_is_not_offered_for_review():
    from kriya.control.launch_approval import build_pending_runtime
    from kriya.policy.approval import RuntimeCommandSubject

    subject = RuntimeCommandSubject("u1", "criterion", "/ws", (("curl", LLM_URL),), "none", None, "reason")
    assert build_pending_runtime("l", "w", 1, subject) is None


def test_egress_violation_messages_never_carry_url_credentials(monkeypatch):
    from kriya.core.llm import EgressViolationError, LLMClient
    from kriya.memory.embedding import OllamaEmbeddingClient

    outside = "http://alice:" + PW + "@8.8.8.8/v1"
    with pytest.raises(EgressViolationError) as embedding:
        OllamaEmbeddingClient(base_url=outside, model="m", egress_policy="local_only")._enforce_egress()
    cfg = AppConfig()
    cfg.llm.base_url = outside
    llm = LLMClient(cfg)
    monkeypatch.setattr(LLMClient, "_request_once", AsyncMock(side_effect=AssertionError("no request")))
    with pytest.raises(EgressViolationError) as completion:
        asyncio.run(llm.complete("system", "user"))
    monkeypatch.setattr(llm.client.chat.completions, "create", AsyncMock(side_effect=AssertionError("no request")))
    with pytest.raises(EgressViolationError) as tool_calling:
        asyncio.run(llm.complete_with_tools(messages=[{"role": "user", "content": "u"}], tools=[]))
    for error in (embedding.value, completion.value, tool_calling.value):
        assert "8.8.8.8" in str(error) and PW not in str(error), str(error)


def test_provider_probe_errors_never_carry_url_credentials(monkeypatch):
    import kriya.core.model_runtime as mr

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr(mr.urllib.request, "urlopen", refuse)
    with pytest.raises(ValueError) as failed:
        mr._http_json(LLM_URL + "/models", None, "")
    assert "HTTP 401" in str(failed.value) and PW not in str(failed.value), str(failed.value)


def test_the_routing_error_never_carries_url_credentials():
    from kriya.memory.embedding import EmbeddingError
    from kriya.routing import Router, RoutingModelUnavailable

    cfg = AppConfig()
    cfg.embedding.base_url = EMB_URL
    router = Router(cfg)
    router._embed_client.get_embeddings = AsyncMock(side_effect=EmbeddingError("down"))
    with pytest.raises(RoutingModelUnavailable) as failed:
        asyncio.run(router._ensure_fitted())
    assert "127.0.0.1:9" in str(failed.value) and EMB_PW not in str(failed.value), str(failed.value)


@pytest.mark.parametrize("value, readable", [
    ({"base_url": LLM_URL}, "127.0.0.1:9"),                                            # user:pass@host
    ({"url": f"https://{KEY}@github.com/org/repo.git"}, "github.com"),                  # token-as-username user@host
    ({"url": f"http://alice:{PW}%40x@127.0.0.1:9"}, "127.0.0.1:9"),                    # percent-encoded password
    ({"url": f"http://alice:p@{PW}@127.0.0.1:9"}, "127.0.0.1:9"),                     # unencoded @ inside the password
    ({"API_KEY": KEY, "Auth-Token": PW, "dbPassword": EMB_PW}, "API_KEY"),             # mixed-case / camel / dashed keys
    ({"servers": [{"headers": {"Authorization": f"Bearer {KEY}"}}]}, "headers"),       # nested list + dict
    ({"servers": [{"headers": {"authorization": f"Basic {KEY}"}}]}, "authorization"),
    ({"client_secret": KEY, "credentials": {"user": "u", "pass": PW}}, "client_secret"),
], ids=["userinfo", "username-token", "pct-encoded", "at-in-password", "mixed-case-keys", "bearer-nested", "basic", "secret-and-credentials"])
def test_every_secret_bearing_value_is_hidden_in_the_authority_display(value, readable):
    shown = json.dumps(_pending({"mcp": {"srv": value}}, "mcp.srv"))
    assert readable in shown and _leaked(shown) == [], shown


# =========================================================================== the owner's rules (S2 correction)

from kriya.foundation import redaction  # noqa: E402 - the owner exists from the correction commit on


@pytest.mark.parametrize("key", ["api_key", "API_KEY", "ApiKey", "openai-api-key", "password", "dbPassword", "DB_PASSWD",
                                 "pwd", "passphrase", "client_secret", "Secret", "token", "Auth-Token", "github_token",
                                 "authorization", "Authorization", "credentials", "credential", "access_key", "privateKey",
                                 "secret_key", "authKey", "cookie", "x-api-key"])
def test_a_secret_bearing_key_is_recognized_by_word(key):
    assert redaction.is_secret_key(key)


@pytest.mark.parametrize("key", ["max_tokens", "planner_max_tokens", "token_budget", "tokenizer", "secretary", "keyboard",
                                 "passage", "author", "authority_source", "env_keys", "monkey", "key", "base_url", "",
                                 "__", 3, None])
def test_a_key_that_merely_contains_a_secret_word_is_not_secret(key):
    assert not redaction.is_secret_key(key)


@pytest.mark.parametrize("text, hidden, kept", [
    ("http://alice:pw1@127.0.0.1:9/v1", "pw1", "http://***REDACTED***@127.0.0.1:9/v1"),
    ("HTTPS://tok123@github.com/o/r", "tok123", "HTTPS://***REDACTED***@github.com/o/r"),
    ("http://a:p%40w@h", "p%40w", "http://***REDACTED***@h"),
    ("http://a:p@w@h/x", "p@w", "http://***REDACTED***@h/x"),
    ("see git+ssh://u:pw2@host/r and more", "pw2", "see git+ssh://***REDACTED***@host/r and more"),
    ("Authorization: Bearer abc.def", "abc.def", "Authorization: ***REDACTED*** ***REDACTED***"),
    ("Basic dXNlcjpwYXNz", "dXNlcjpwYXNz", "Basic ***REDACTED***"),
    ("--password=hunter2", "hunter2", "--password=***REDACTED***"),
    ("credentials: c0ffee", "c0ffee", "credentials: ***REDACTED***"),
    ("key AKIAABCDEFGHIJKLMNOP x", "AKIAABCDEFGHIJKLMNOP", "key ***REDACTED*** x"),
    ("sk-abcdefghijklmnopqrstuvwx1234", "abcdefghijklmnopqrstuvwx", "***REDACTED***"),
    ("ghp_abcdefghijklmnopqrstuvwxyz012345", "abcdefghijklmnopqrstuvwxyz", "***REDACTED***"),
], ids=["userinfo", "username-token", "pct", "at-in-password", "embedded", "bearer", "basic", "kv", "kv-plural", "aws",
        "sk", "gh"])
def test_every_secret_shape_is_hidden_in_free_text(text, hidden, kept):
    out = redaction.redact_text(text)
    assert hidden not in out and out == kept


@pytest.mark.parametrize("text", ["http://localhost:11434/v1", "https://e.com/a@b?q=c@d", "dev@example.com",
                                  "git@github.com:org/repo.git", "sha256:" + "ab" * 32, "1c2cfe2a4b9e8f0d3c5a7b6e9f1d2c3b",
                                  "Basic setup done", "Basic authentication is not configured", "mvn -Dmaven.compiler.showWarnings=true", "", "plain words"])
def test_text_without_a_secret_is_returned_unchanged(text):
    assert redaction.redact_text(text) == text


def test_structures_hide_secret_keys_and_containers_with_names_kept_and_falsy_values_shown():
    value = {"llm": {"api_key": "k1", "API_KEY": "", "model": "m", "max_tokens": 5, "base_url": "http://u:p9@h/v1"},
             "mcp": {"s": {"env": {"A": "x1", "B": None, "C": 0}, "args": ["--x", "http://u:p8@h"], "env_keys": ["A"]}},
             "headers": ({"Authorization": "Bearer z1"},), "credentials": {"user": "u1", "port": 0, "nested": ["n1"]}}
    out = redaction.redact_value(value)
    assert out == {
        "llm": {"api_key": "***REDACTED***", "API_KEY": "", "model": "m", "max_tokens": 5,
                "base_url": "http://***REDACTED***@h/v1"},
        "mcp": {"s": {"env": {"A": "***REDACTED***", "B": None, "C": 0}, "args": ["--x", "http://***REDACTED***@h"],
                      "env_keys": ["A"]}},
        "headers": [{"Authorization": "***REDACTED***"}], "credentials": {"user": "***REDACTED***", "port": 0,
                                                                          "nested": ["***REDACTED***"]}}
    assert value["llm"]["api_key"] == "k1"                                   # never mutates its input
    assert redaction.redact_value({"env": "not-a-container"}) == {"env": "not-a-container"}
    assert redaction.redact_value(7) == 7 and redaction.redact_value(None) is None


def test_there_is_one_redaction_owner():
    """The three old redactors are gone (no delegate, no re-export) and no other kriya module carries a redaction literal
    or a credential pattern of its own."""
    import kriya.cli
    import kriya.config.authority_approval
    import kriya.policy.authority
    import kriya.policy.telemetry

    assert not hasattr(kriya.cli, "_redact_secrets")
    assert not hasattr(kriya.config.authority_approval, "_redact_display_value")
    assert not hasattr(kriya.policy.telemetry, "scrub_potential_secrets")
    assert not hasattr(kriya.policy.authority, "scrub_potential_secrets")
    offenders = []
    for directory, _, files in os.walk(os.path.join(REPO_ROOT, "kriya")):
        for name in files:
            path = os.path.join(directory, name)
            if not name.endswith(".py") or path.endswith(os.path.join("foundation", "redaction.py")):
                continue
            source = open(path, encoding="utf-8").read()
            if '= "***REDACTED***"' in source or "Bearer\\s+" in source or "AKIA[0-9A-Z]" in source:
                offenders.append(os.path.relpath(path, REPO_ROOT))
    assert offenders == []


def test_every_kriya_log_handler_formats_through_the_owner(tmp_path):
    from kriya.core import logging_setup

    handler = logging_setup._open_file_handler(str(tmp_path / "l" / "k.log"), logging.INFO)
    try:
        assert isinstance(handler.formatter, logging_setup.RedactingFormatter)
        error = RuntimeError("connect http://u:tb1@h failed")
        record = logging.LogRecord("k", logging.ERROR, __file__, 1, "boom", (), (RuntimeError, error, None))
        assert "tb1" not in handler.formatter.format(record) and "RuntimeError" in handler.formatter.format(record)
    finally:
        handler.close()


def test_a_kup_host_refusal_never_carries_url_credentials():
    from kriya.kup import serve

    refusal = serve._refusal("r1", "HOST_ERROR", f"internal fault: ConnectError for {LLM_URL}")
    assert refusal["error"]["code"] == "HOST_ERROR" and "127.0.0.1:9" in refusal["error"]["message"]
    assert _leaked(json.dumps(refusal)) == []


def test_the_kup_configuration_refusal_redacts_before_its_300_character_cut():
    """The message is cut at 300 characters: redacting after the cut would leave a userinfo without its '@' (unmatched)."""
    from kriya.application import configuration
    from kriya.application.services import ApplicationRefusal

    class Unreadable:
        def resolution_state(self):
            raise ValueError("x" * 265 + f" endpoint http://alice:{PW}LONGTAIL@127.0.0.1:9/v1")

    with pytest.raises(ApplicationRefusal) as refused:
        configuration._state(Unreadable())
    assert "endpoint http://" in str(refused.value) and "s3cret" not in str(refused.value), str(refused.value)


def test_a_served_model_pin_refusal_never_carries_url_credentials():
    from kriya.core.model_runtime import pin_served_configuration
    from kriya.core.provider_contract import ProviderContractError

    with pytest.raises(ProviderContractError) as refused:
        pin_served_configuration(base_url="http://alice:" + PW + "@8.8.8.8/v1", model="m", extra_body=None,
                                 requested_context_window=8192)
    assert "8.8.8.8" in str(refused.value) and PW not in str(refused.value), str(refused.value)
