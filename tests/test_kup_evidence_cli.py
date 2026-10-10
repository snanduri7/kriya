"""KUP evidence grammar through the real CLI (Run Cockpit v1.1 slice 1A-1): ``kriya evidence verify`` is byte-identical without the
KUP flags, ``evidence verify|manifest --json --kup-version 1`` answer with the envelope (typed errors are envelopes, exit 0),
the KUP path never bootstraps logging or loads anything but configuration, and the capability set advertises exactly what exists.
Plus Python <-> JSON Schema <-> TypeScript vocabulary parity (the lock-step edit sites)."""
from __future__ import annotations

import json
import os
import re
from unittest.mock import patch

from _kup_evidence_fixtures import write_store
from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig
from kriya.core.attempt_evidence import model, reader
from kriya.kup import policy

UI_KUP = os.path.join(os.path.dirname(__file__), "..", "ui", "kup")


def _invoke(args):
    with patch("kriya.cli.load_config", return_value=AppConfig()):
        return CliRunner().invoke(main, args)


def _env(args):
    res = _invoke(args)
    assert res.exit_code == 0, res.output
    env = json.loads(res.output)
    assert env["schema_version"] == 1 and set(env) == {"schema_version", "operation", "request_id", "observed_at", "source", "consistency", "data", "error"}
    return env


# ------------------------------------------------------------------ D-3: unchanged without the KUP flags (characterization of the pre-1A-1 output)

def test_evidence_verify_text_and_json_are_byte_identical_without_the_kup_flags():
    write_store("c-ok")
    res = _invoke(["evidence", "verify", "c-ok"])
    assert res.exit_code == 0 and res.output == "c-ok: VERIFIED (3 record(s))\n"
    res = _invoke(["evidence", "verify", "c-ok", "--json"])
    payload = {"run_id": "c-ok", "status": "VERIFIED", "records": 3, "sealed": True, "broken_seq": None, "detail": "", "blob_problems": []}
    assert res.exit_code == 0 and res.output == json.dumps(payload, indent=2, sort_keys=True) + "\n"
    write_store("c-un", seal=False)
    assert _invoke(["evidence", "verify", "c-un"]).exit_code == 1  # UNSEALED keeps its exit code
    assert _invoke(["evidence", "verify", "c-none"]).exit_code == 1  # NOT_FOUND likewise


def test_evidence_text_mode_still_bootstraps_logging_once_and_keeps_the_config_error_message_and_exit_code():
    write_store("c-log")
    with patch("kriya.cli._bootstrap_logging") as boot:
        assert _invoke(["evidence", "verify", "c-log"]).exit_code == 0
    assert boot.call_count == 1
    with patch("kriya.cli.load_config", side_effect=ValueError("paths.state must be absolute")):
        res = CliRunner().invoke(main, ["evidence", "verify", "c-log"])
    assert res.exit_code == 1 and "Error loading configuration: paths.state must be absolute" in res.output


# ------------------------------------------------------------------ the KUP grammar

def test_verify_kup_envelope_and_exit_code_zero_for_every_status():
    write_store("k-ok")
    env = _env(["evidence", "verify", "k-ok", "--json", "--kup-version", "1"])
    assert env["operation"] == "evidence.verify" and env["data"]["status"] == "VERIFIED" and env["consistency"]["kind"] == "sealed_store"
    env = _env(["evidence", "verify", "k-none", "--json", "--kup-version", "1"])  # typed data, not a process failure
    assert env["data"]["status"] == "NOT_FOUND"


def test_manifest_kup_envelope_with_and_without_a_snapshot_id():
    write_store("k-man")
    env = _env(["evidence", "manifest", "k-man", "--json", "--kup-version", "1"])
    assert env["operation"] == "evidence.manifest" and env["data"]["store"] == "present"
    env = _env(["evidence", "manifest", "k-man", "--json", "--kup-version", "1", "--snapshot-id", "20260101T000000000000Z-00000000"])
    assert env["error"]["code"] == policy.SNAPSHOT_MISSING


def test_kup_flags_require_json_and_manifest_and_records_are_kup_only():
    write_store("k-use")
    res = _invoke(["evidence", "verify", "k-use", "--kup-version", "1"])
    assert res.exit_code == 2 and "--kup-version requires --json" in res.output
    for command in ("manifest", "records"):
        for extra in ([], ["--json"], ["--kup-version", "1"]):
            res = _invoke(["evidence", command, "k-use", *extra])
            assert res.exit_code == 2 and "pass --json --kup-version 1" in res.output, (command, extra)


def test_records_grammar_maps_every_flag_into_the_request():
    from _kup_evidence_fixtures import rich_store
    rich_store("k-rec")
    argv = ["evidence", "records", "k-rec", "--json", "--kup-version", "1"]
    env = _env([*argv, "--unit", "u1", "--attempt", "1", "--kind", "gate.result", "--kind", "attempt.opened", "-n", "2"])
    assert env["operation"] == "evidence.records" and env["data"]["filters_applied"] == {"unit": "u1", "attempt": 1, "kind": ["attempt.opened", "gate.result"]}
    assert [r["seq"] for r in env["data"]["records"]] == [5, 8] and env["data"]["next_cursor"] is None
    page = _env([*argv, "-n", "4", "--phase", "attempt", "--role", "developer", "--from-seq", "2"])
    assert page["data"]["filters_applied"] == {"phase": "attempt", "role": "developer", "from_seq": 2} and [r["seq"] for r in page["data"]["records"]] == [6, 7]
    first = _env([*argv, "-n", "4"])
    second = _env([*argv, "-n", "4", "--cursor", first["data"]["next_cursor"]])
    assert [r["seq"] for r in first["data"]["records"]] + [r["seq"] for r in second["data"]["records"]][:1] == [1, 2, 3, 4, 5]
    assert _invoke([*argv, "--attempt", "x"]).exit_code == 2                      # click refuses a non-integer
    assert _env([*argv, "-n", "0"])["error"]["code"] == policy.INVALID_REQUEST       # Kriya refuses an out-of-range one, typed


def test_an_unsupported_schema_version_is_the_typed_envelope():
    env = _env(["evidence", "verify", "k-v", "--json", "--kup-version", "2"])
    assert env["error"]["code"] == policy.UNSUPPORTED_SCHEMA_VERSION and env["data"] is None
    env = _env(["evidence", "manifest", "k-v", "--json", "--kup-version", "0"])
    assert env["error"]["code"] == policy.UNSUPPORTED_SCHEMA_VERSION


def test_the_kup_path_never_bootstraps_logging_and_text_unreadable_stores_are_typed_envelopes():
    directory = write_store("k-cor")
    with open(os.path.join(directory, "manifest.json"), "wb") as handle:
        handle.write(b"{nope")
    with patch("kriya.cli._bootstrap_logging") as boot, patch("kriya.core.logging_setup.configure_logging") as configure:
        env = _env(["evidence", "manifest", "k-cor", "--json", "--kup-version", "1"])
        env2 = _env(["evidence", "verify", "k-cor", "--json", "--kup-version", "1"])
    assert boot.call_count == 0 and configure.call_count == 0
    assert env["error"]["code"] == policy.EVIDENCE_STORE_UNREADABLE and env2["error"]["code"] == policy.EVIDENCE_STORE_UNREADABLE


def test_config_authority_refusal_and_load_failure_are_typed_for_the_evidence_kup_path():
    from kriya.config.authority import ConfigAuthorityError, ConfigAuthorityViolation, ConfigSource, FieldClassification
    violation = ConfigAuthorityViolation(field_path="mcp.evil.command", source=ConfigSource.EXPLICIT_CONFIG_PATH_INSIDE_WORKSPACE,
                                         classification=FieldClassification.SECURITY_AUTHORITY, reason="repository-sourced security authority")
    for argv in (["evidence", "verify", "r", "--json", "--kup-version", "1"], ["evidence", "manifest", "r", "--json", "--kup-version", "1"]):
        with patch("kriya.cli.load_config", side_effect=ConfigAuthorityError([violation])):
            env = json.loads(CliRunner().invoke(main, argv).output)
        assert env["error"]["code"] == policy.CONFIG_AUTHORITY_REFUSED and "SEC-009" in env["error"]["message"] and env["data"] is None
        with patch("kriya.cli.load_config", side_effect=ValueError("paths.state must be absolute")):
            assert json.loads(CliRunner().invoke(main, argv).output)["error"]["code"] == policy.CONFIG_LOAD_FAILED


# ------------------------------------------------------------------ capability set 1.1

def test_capabilities_advertise_set_1_1_and_only_what_exists():
    data = _env(["traces", "--json", "--capabilities"])["data"]
    assert data["kup_versions"] == [1] and {"evidence.manifest", "evidence.verify", "evidence.records"} <= set(data["operations"])
    features = data["features"]
    assert features["capability_set"] == "1.1" and features["evidence"] is True
    for absent in ("evidence_content", "qualification", "authority", "knowledge", "tools", "experiments", "live", "actions"):
        assert features[absent] is False, absent  # nothing is advertised before it exists (fail closed)
    assert features["consistency_kinds"][-1] == "sealed_store" and set(features["consistency_kinds"]) == set(policy.CONSISTENCY_KINDS)
    assert features["evidence_capture_modes"] == list(model.CAPTURE_MODES)
    assert features["evidence_verification_states"] == [reader.VERIFIED, reader.UNSEALED, reader.NOT_FOUND, reader.CHAIN_BROKEN, reader.TRUNCATED_TAIL,
                                                        reader.BLOB_CORRUPT, reader.SEAL_MISMATCH]
    assert features["evidence_kinds"] == sorted(model.KINDS) and features["evidence_provenances"] == sorted(model.PROVENANCES)
    assert features["diagnosis_evidence_classes"] == list(model.EVIDENCE_CLASSES)


# ------------------------------------------------------------------ lock-step vocabulary parity (policy.py / index.ts / common.schema.json)

def _schema(name):
    with open(os.path.join(UI_KUP, "schema", name), encoding="utf-8") as handle:
        return json.load(handle)


def _ts_const(name):
    with open(os.path.join(UI_KUP, "src", "index.ts"), encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(rf"export const {name} = \[([^\]]*)\] as const;", text)
    assert match, name
    return re.findall(r"'([^']+)'", match.group(1))


def test_vocabularies_are_identical_in_python_json_schema_and_typescript():
    common = _schema("common.schema.json")["$defs"]
    assert list(policy.OPERATIONS) == common["Envelope"]["properties"]["operation"]["enum"] == _ts_const("KUP_OPERATIONS")
    assert list(policy.ERROR_CODES) + ["HOST_ERROR"] == common["ErrorCode"]["enum"] == _ts_const("KUP_ERROR_CODES")
    assert list(policy.CONSISTENCY_KINDS) == common["Consistency"]["properties"]["kind"]["enum"] == _ts_const("CONSISTENCY_KINDS")


def test_every_error_code_has_a_golden_fixture_entry_in_the_ui_fixture_generator():
    """ui/fixtures/generated is a build output (gitignored); its source of truth is generate.mjs, whose ERRORS table feeds the
    TypeScript contract test (one errors/<code>.json per code)."""
    with open(os.path.join(UI_KUP, "..", "fixtures", "generate.mjs"), encoding="utf-8") as handle:
        text = handle.read()
    for code in policy.ERROR_CODES:
        assert f"['{code}'," in text, code


def _validate(schema_file, definition, instance):
    import jsonschema

    schema = _schema(schema_file)
    jsonschema.validate(instance, {"$ref": f"#/$defs/{definition}", "$defs": schema["$defs"]})


def test_real_responses_validate_against_the_json_schemas_the_ui_is_generated_from():
    """The lock-step proof: what Kriya really emits (recorder-written stores, not hand-written fixtures) satisfies common.schema.json
    (envelope, source, consistency, error) and evidence.schema.json (payloads) for every verification status and absence case."""
    from _kup_evidence_fixtures import acquire_snapshot_id, rewrite_record_line, seed_trace_run

    write_store("s-ok")
    write_store("s-un", seal=False)
    broken = write_store("s-br")
    rewrite_record_line(broken, 1, lambda record: model.canonical_bytes({**record, "payload": {"n": 9}}))
    seed_trace_run("s-pruned", {"run_id": "s-pruned", "status": "CLOSED", "capture": "full", "path": "/gone"})
    snapshot_id = acquire_snapshot_id()
    seen_statuses = set()
    for run_id in ("s-ok", "s-un", "s-br", "s-none"):
        env = _env(["evidence", "verify", run_id, "--json", "--kup-version", "1"])
        _validate("common.schema.json", "Envelope", env)
        _validate("evidence.schema.json", "EvidenceVerify", env["data"])
        seen_statuses.add(env["data"]["status"])
    assert seen_statuses == {"VERIFIED", "UNSEALED", "CHAIN_BROKEN", "NOT_FOUND"}
    from _kup_evidence_fixtures import rich_store
    rich_store("s-rich")
    for argv in (["evidence", "records", "s-rich", "--json", "--kup-version", "1", "-n", "5"], ["evidence", "records", "s-un", "--json", "--kup-version", "1"]):
        env = _env(argv)
        _validate("common.schema.json", "Envelope", env)
        _validate("evidence.schema.json", "EvidenceRecords", env["data"])
    for argv in (["evidence", "records", "s-none", "--json", "--kup-version", "1"], ["evidence", "records", "s-br", "--json", "--kup-version", "1"]):
        env = _env(argv)
        _validate("common.schema.json", "Envelope", env)
        assert env["error"]["code"] in ("EVIDENCE_STORE_NOT_FOUND", "EVIDENCE_VERIFY_FAILED")
    for run_id, extra in (("s-ok", []), ("s-pruned", ["--snapshot-id", snapshot_id]), ("s-none", [])):
        env = _env(["evidence", "manifest", run_id, "--json", "--kup-version", "1", *extra])
        _validate("common.schema.json", "Envelope", env)
        _validate("evidence.schema.json", "EvidenceManifest", env["data"])
    directory = write_store("s-cor")
    with open(os.path.join(directory, "manifest.json"), "wb") as handle:
        handle.write(b"{nope")
    env = _env(["evidence", "manifest", "s-cor", "--json", "--kup-version", "1"])
    _validate("common.schema.json", "Envelope", env)
    assert env["error"]["code"] == "EVIDENCE_STORE_UNREADABLE"


def test_from_trace_flag_resolves_through_the_cli():
    from _kup_evidence_fixtures import acquire_snapshot_id, seed_trace_run
    write_store("ev-cli")
    seed_trace_run("t-cli", {"run_id": "ev-cli", "status": "OPEN", "capture": "full", "path": "/x"})
    sid = acquire_snapshot_id()
    env = _env(["evidence", "manifest", "t-cli", "--json", "--kup-version", "1", "--snapshot-id", sid, "--from-trace"])
    assert env["data"]["run_id"] == "ev-cli" and env["data"]["store"] == "present" and env["data"]["link"]["trace_run_id"] == "t-cli"
    assert _env(["evidence", "manifest", "t-cli", "--json", "--kup-version", "1", "--from-trace"])["error"]["code"] == policy.INVALID_REQUEST
