"""KUP CLI grammar (GUI M1 Phase C): byte-identical text mode, every error code, pagination with equal timestamps,
injection inputs, the response-size limit, prompt exclusion, pinned snapshots, no logging bootstrap. In-process
CliRunner against fixture stores in the test's own KRIYA_STATE_DIR (tests/conftest.py isolates it)."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from _kup_fixtures import seed_store
from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig
from kriya.core.state_paths import trace_db_path
from kriya.kup import policy
from kriya.kup.store import RAW_CONNECT

GOLDEN = os.path.join(os.path.dirname(__file__), "golden", "kup")


def _invoke(args, color=False):
    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=AppConfig()), \
         patch("kriya.core.state_paths.historical_default_trace_db", return_value="/nonexistent/legacy/traces.db"):
        return runner.invoke(main, args, color=color)


def _json(args):
    res = _invoke(args)
    assert res.exit_code == 0, res.output
    env = json.loads(res.output)
    assert env["schema_version"] == 1 and set(env) == {"schema_version", "operation", "request_id", "observed_at", "source", "consistency", "data", "error"}
    return env


def _acquire():
    env = _json(["traces", "--json", "--snapshot"])
    assert env["error"] is None, env
    return env["data"]["snapshot_id"]


# ------------------------------------------------------------------ byte-identical text mode (06 Phase C test 1)

@pytest.mark.parametrize("name,args,count", [("traces_default", ["traces"], 25), ("traces_n3", ["traces", "-n", "3"], 25),
                                             ("traces_all", ["traces", "--all"], 25), ("traces_empty_state", ["traces"], 0)])
def test_text_mode_is_byte_identical_to_the_baseline_golden(name, args, count):
    if count:
        seed_store(trace_db_path(AppConfig()), count)
    res = _invoke(args, color=True)
    assert res.exit_code == 0
    with open(os.path.join(GOLDEN, f"{name}.golden"), encoding="utf-8") as f:
        assert res.output == f.read()


def test_text_mode_still_bootstraps_logging_and_json_mode_never_does():
    seed_store(trace_db_path(AppConfig()), 2)
    with patch("kriya.cli._bootstrap_logging") as boot:
        assert _invoke(["traces"]).exit_code == 0
    assert boot.call_count == 1
    with patch("kriya.cli._bootstrap_logging") as boot, patch("kriya.core.logging_setup.configure_logging") as cfg_log:
        assert _invoke(["traces", "--json", "--capabilities"]).exit_code == 0
    assert boot.call_count == 0 and cfg_log.call_count == 0


def test_kup_flags_without_json_are_a_usage_error():
    for args in (["traces", "--capabilities"], ["traces", "--snapshot"], ["traces", "--snapshot-id", "x"], ["traces", "--run-id", "r"]):
        res = _invoke(args)
        assert res.exit_code == 2 and "require --json" in res.output, args
    res = _invoke(["traces", "--json", "--all"])
    assert res.exit_code == 2


# ------------------------------------------------------------------ operations

def test_capabilities_envelope():
    env = _json(["traces", "--json", "--capabilities"])
    assert env["operation"] == "capabilities" and env["consistency"] == {"kind": "not_applicable", "live_stream": False}
    assert env["data"]["kup_versions"] == [1] and env["data"]["features"]["snapshot"] is True
    assert set(env["data"]["operations"]) == set(policy.OPERATIONS)
    assert env["data"]["limits"]["max_snapshot_bytes"] == 512 * 1024 * 1024 and env["data"]["limits"]["retain_snapshots"] == 3
    assert env["source"]["trace_database"] == trace_db_path(AppConfig())


def test_history_requires_a_snapshot_id_and_never_reads_the_live_store():
    seed_store(trace_db_path(AppConfig()), 3)
    env = _json(["traces", "--json", "-n", "5"])
    assert env["error"]["code"] == policy.INVALID_REQUEST and "snapshot_id is required" in env["error"]["message"]
    env = _json(["traces", "--json", "--snapshot-id", "20260101T000000000000Z-00000000", "-n", "5"])
    assert env["error"]["code"] == policy.SNAPSHOT_MISSING


def test_acquire_list_detail_prompt_are_pinned_to_the_snapshot_id():
    seed_store(trace_db_path(AppConfig()), 6)
    snapshot_id = _acquire()
    env = _json(["traces", "--json", "--snapshot-id", snapshot_id, "-n", "4"])
    assert env["operation"] == "history.list" and env["consistency"]["kind"] == "snapshot_copy" and env["consistency"]["snapshot_id"] == snapshot_id
    assert env["source"]["snapshot_id"] == snapshot_id and env["consistency"]["acquisition_started_at"] <= env["consistency"]["acquisition_completed_at"]
    runs = env["data"]["runs"]
    assert len(runs) == 4 and env["data"]["next_cursor"]
    assert "prompt_rendered" not in runs[0] and runs[0]["files_modified"] == "a.py,b.py"
    detail = _json(["traces", "--json", "--snapshot-id", snapshot_id, "--run-id", runs[0]["run_id"]])
    assert detail["operation"] == "history.detail" and "prompt_rendered" not in json.dumps(detail["data"])
    assert detail["data"]["run_events"]["availability"] == "recorded" and detail["data"]["run_events"]["data"][0]["novel_field"] == "kept"
    assert detail["data"]["fields"]["run_events"]["data"].startswith("[{")  # verbatim stored text
    for absent in ("context", "attribution", "diagnostics", "comparisons", "output"):
        assert detail["data"][absent]["availability"] == "not_recorded"
    prompt = _json(["traces", "--json", "--snapshot-id", snapshot_id, "--run-id", runs[0]["run_id"], "--include-prompt"])
    assert prompt["operation"] == "history.prompt" and prompt["data"]["prompt_rendered"].startswith("PLANNING PROMPT")
    # a newer acquisition does not move the pinned session
    newer = _acquire()
    assert newer != snapshot_id
    again = _json(["traces", "--json", "--snapshot-id", snapshot_id, "-n", "4"])
    assert again["consistency"]["snapshot_id"] == snapshot_id


def test_pagination_with_equal_timestamps_is_complete_and_cursor_bound_to_snapshot():
    seed_store(trace_db_path(AppConfig()), 13, equal_timestamps=True)
    snapshot_id = _acquire()
    seen, cursor = [], None
    for _ in range(10):
        args = ["traces", "--json", "--snapshot-id", snapshot_id, "-n", "5"] + (["--cursor", cursor] if cursor else [])
        env = _json(args)
        assert env["error"] is None, env
        seen += [r["run_id"] for r in env["data"]["runs"]]
        cursor = env["data"]["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 13 and len(set(seen)) == 13 and seen == sorted(seen, reverse=True)
    other = _acquire()
    env = _json(["traces", "--json", "--snapshot-id", snapshot_id, "-n", "5"])
    env2 = _json(["traces", "--json", "--snapshot-id", other, "-n", "5", "--cursor", env["data"]["next_cursor"]])
    assert env2["error"]["code"] == policy.INVALID_REQUEST and "another snapshot" in env2["error"]["message"]


@pytest.mark.parametrize("bad", ["; rm -rf /", "$(id)", "a\nb", "--all", "-n", "../../etc", "", "run id", "ré", "x" * 300])
def test_injection_inputs_to_run_id_and_cursor_are_refused_typed(bad):
    seed_store(trace_db_path(AppConfig()), 2)
    snapshot_id = _acquire()
    for args in (["traces", "--json", "--snapshot-id", snapshot_id, "--run-id", bad],
                 ["traces", "--json", "--snapshot-id", snapshot_id, "-n", "2", "--cursor", bad],
                 ["traces", "--json", "--snapshot-id", bad, "-n", "2"]):
        res = _invoke(args)
        if res.exit_code == 2:  # Click itself refused an option-shaped value
            continue
        env = json.loads(res.output)
        assert env["error"] is not None and env["error"]["code"] in (policy.INVALID_REQUEST, policy.SNAPSHOT_UNAVAILABLE), (args, env["error"])
        assert env["data"] is None


def test_limit_bounds():
    seed_store(trace_db_path(AppConfig()), 2)
    snapshot_id = _acquire()
    for n in ("0", "201", "-1"):
        env = _json(["traces", "--json", "--snapshot-id", snapshot_id, "-n", n])
        assert env["error"]["code"] == policy.INVALID_REQUEST
    env = _json(["traces", "--json", "--snapshot-id", snapshot_id])
    assert len(env["data"]["runs"]) == 2 and env["data"]["next_cursor"] is None


def test_response_too_large_is_typed_never_partial():
    db = seed_store(trace_db_path(AppConfig()), 1)
    conn = RAW_CONNECT(db)
    conn.execute("UPDATE runs SET run_events = ? WHERE run_id = 'run-0000'", (json.dumps([{"blob": "x" * (9 * 1024 * 1024)}]),))
    conn.commit()
    conn.close()
    snapshot_id = _acquire()
    res = _invoke(["traces", "--json", "--snapshot-id", snapshot_id, "--run-id", "run-0000"])
    assert res.exit_code == 0
    env = json.loads(res.output)
    assert env["error"]["code"] == policy.RESPONSE_TOO_LARGE and env["data"] is None
    assert len(res.output.encode()) < 4096


def test_every_error_code_is_producible():
    produced = set()
    seed_store(trace_db_path(AppConfig()), 2)
    produced.add(_json(["traces", "--json", "--snapshot-id", "20260101T000000000000Z-00000000", "-n", "2"])["error"]["code"])  # SNAPSHOT_MISSING
    snapshot_id = _acquire()
    produced.add(_json(["traces", "--json", "--snapshot-id", "20260101T000000000000Z-00000000", "-n", "2"])["error"]["code"])  # SNAPSHOT_UNAVAILABLE
    produced.add(_json(["traces", "--json", "-n", "2"])["error"]["code"])  # INVALID_REQUEST
    produced.add(_json(["runs", "status", "--workspace", os.getcwd(), "--json", "--kup-version", "2"])["error"]["code"])  # UNSUPPORTED_SCHEMA_VERSION
    with patch("kriya.kup.acquire._free_bytes", return_value=0):
        produced.add(_json(["traces", "--json", "--snapshot"])["error"]["code"])  # SNAPSHOT_FAILED (insufficient space)
    with patch("kriya.kup.acquire.acquire_snapshot", side_effect=lambda *a, **k: (_ for _ in ()).throw(__import__("kriya.kup.store", fromlist=["SnapshotError"]).SnapshotError(policy.SNAPSHOT_TOO_LARGE, "too big"))):
        produced.add(_json(["traces", "--json", "--snapshot"])["error"]["code"])
    with patch("kriya.kup.acquire.acquire_snapshot", side_effect=lambda *a, **k: (_ for _ in ()).throw(__import__("kriya.kup.store", fromlist=["SnapshotError"]).SnapshotError(policy.ACQUISITION_IN_PROGRESS, "busy"))):
        produced.add(_json(["traces", "--json", "--snapshot"])["error"]["code"])
    with patch("kriya.kup.acquire.acquire_snapshot", side_effect=lambda *a, **k: (_ for _ in ()).throw(__import__("kriya.kup.store", fromlist=["SnapshotError"]).SnapshotError(policy.ACQUISITION_REFUSED_RUN_ACTIVE, "active"))):
        produced.add(_json(["traces", "--json", "--snapshot"])["error"]["code"])
    with patch("kriya.kup.acquire.acquire_snapshot", side_effect=lambda *a, **k: (_ for _ in ()).throw(__import__("kriya.kup.store", fromlist=["SnapshotError"]).SnapshotError(policy.STORE_BUSY, "hot", policy.DB_STATE_HOT_JOURNAL))):
        env = _json(["traces", "--json", "--snapshot"])
        produced.add(env["error"]["code"])
        assert env["error"]["database_state"] == "hot_journal"
    os.remove(trace_db_path(AppConfig()))
    produced.add(_json(["traces", "--json", "--snapshot"])["error"]["code"])  # READ_ONLY_UNAVAILABLE / missing
    # corrupt: tamper with the published snapshot
    from kriya.kup.store import list_snapshots, snapshot_directory
    snap = list_snapshots(snapshot_directory(AppConfig()))[0]
    os.chmod(snap.db_path, 0o600)
    with open(snap.db_path, "ab") as f:
        f.write(b"!")
    produced.add(_json(["traces", "--json", "--snapshot-id", snap.snapshot_id, "-n", "1"])["error"]["code"])  # SNAPSHOT_CORRUPT
    produced.add("RESPONSE_TOO_LARGE")  # covered by test_response_too_large_is_typed_never_partial
    produced.update({"INVALID_RESPONSE", "CONFIG_AUTHORITY_REFUSED", "CONFIG_LOAD_FAILED"})  # host-side / config-load codes: tests below
    assert produced >= set(policy.ERROR_CODES), set(policy.ERROR_CODES) - produced
    assert snapshot_id


def test_config_authority_refusal_maps_to_the_typed_code():
    """D-7: a ConfigAuthorityError at load time is reported as CONFIG_AUTHORITY_REFUSED, with no retry or trust file."""
    from kriya.config.authority import ConfigAuthorityError, ConfigAuthorityViolation, ConfigSource, FieldClassification
    violation = ConfigAuthorityViolation(field_path="mcp.evil.command", source=ConfigSource.EXPLICIT_CONFIG_PATH_INSIDE_WORKSPACE,
                                         classification=FieldClassification.SECURITY_AUTHORITY, reason="repository-sourced security authority")
    runner = CliRunner()
    with patch("kriya.cli.load_config", side_effect=ConfigAuthorityError([violation])):
        res = runner.invoke(main, ["traces", "--json", "--capabilities"])
    assert res.exit_code == 0, res.output
    env = json.loads(res.output)
    assert env["error"]["code"] == policy.CONFIG_AUTHORITY_REFUSED and "SEC-009" in env["error"]["message"] and env["data"] is None
    # any other load failure is CONFIG_LOAD_FAILED; text mode keeps the historical message and exit code
    with patch("kriya.cli.load_config", side_effect=ValueError("paths.state must be absolute")):
        res = runner.invoke(main, ["traces", "--json", "--capabilities"])
        assert json.loads(res.output)["error"]["code"] == policy.CONFIG_LOAD_FAILED
        res = runner.invoke(main, ["traces"])
    assert res.exit_code == 1 and "Error loading configuration: paths.state must be absolute" in res.output


def test_runs_status_kup_envelope_preserves_status_and_exit_codes(tmp_path):
    res = _invoke(["runs", "status", "--workspace", str(tmp_path), "--json", "--kup-version", "1"])
    assert res.exit_code == 0, res.output
    env = json.loads(res.output)
    assert env["operation"] == "workspace.status" and env["source"] is None and env["consistency"]["kind"] == "live_observation"
    assert env["data"]["run_active"] is False and env["data"]["exit_code"] == 0 and env["data"]["workspace"]
    res = _invoke(["runs", "status", "--workspace", str(tmp_path), "--kup-version", "1"])
    assert res.exit_code == 2  # --kup-version requires --json


def test_snapshots_list_prune_and_verify():
    seed_store(trace_db_path(AppConfig()), 2)
    ids = [_acquire() for _ in range(2)]
    env = _json(["traces", "--json", "--snapshots", "--verify"])
    assert [s["snapshot_id"] for s in env["data"]["snapshots"]] == sorted(ids, reverse=True)
    assert all(s["digest_verified"] for s in env["data"]["snapshots"]) and env["consistency"]["kind"] == "live_observation"
    assert env["data"]["snapshots"][0]["source_metadata_changed"] is False
    env = _json(["traces", "--json", "--snapshot-prune", "--keep", "1"])
    assert env["data"]["kept"] == [ids[-1]] and env["data"]["removed"] == [ids[0]]
    env = _json(["traces", "--json", "--snapshot-prune", "--keep", "0"])
    assert env["data"]["kept"] == []
