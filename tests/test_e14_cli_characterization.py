"""E14 Part 2 characterization (written BEFORE the CLI groups move, green at the pin commit; the pin2 campaign's
survivors): operator-visible decisions of the four command groups E14 moves to ``kriya/cli_commands/`` that no
existing test pinned - what each command prints and exits with, driven through ``kriya.cli.main`` exactly as an
operator runs it. Decision-to-killer table: handover/MODULARIZATION/E14-DECISION-TO-KILLER.md."""
import contextlib
import json
import os

import pytest
import yaml
from click.testing import CliRunner

from kriya.cli import main


@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


SECURITY = {"llm": {"base_url": "http://127.0.0.1:11434/v1"}, "logging": {"file_enabled": False}}
PLAIN = {"logging": {"file_enabled": False}}


@pytest.fixture(name="authority_ws")
def _authority_ws(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "kriya.yaml").write_text(yaml.safe_dump(SECURITY))
    (ws / "plain.yaml").write_text(yaml.safe_dump(PLAIN))
    home = tmp_path / "authority-home"
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(home))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)
    return ws, home


def _invoke(ws, *args, stdin=None):
    with _cwd(ws):
        return CliRunner().invoke(main, list(args), input=stdin)


def _artifacts(home):
    return sorted(str(p.relative_to(home)) for p in home.rglob("*") if p.is_file()) if home.exists() else []


def test_inspect_without_pending_fields_says_the_configuration_is_fully_authorized(authority_ws):
    ws, _ = authority_ws
    result = _invoke(ws, "--config", "plain.yaml", "authority", "inspect")
    assert result.exit_code == 0, result.output
    assert result.output == "No pending security-authority fields - current configuration is fully authorized.\n"


def test_inspect_lists_each_pending_field_with_its_classification_from_the_given_config(authority_ws):
    ws, _ = authority_ws
    (ws / "kriya.yaml").write_text(yaml.safe_dump(PLAIN))          # the auto-discovered file has nothing pending
    (ws / "sec.yaml").write_text(yaml.safe_dump(SECURITY))
    result = _invoke(ws, "--config", "sec.yaml", "authority", "inspect")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "1 security-authority field(s) pending approval:"
    assert lines[1] == "  - llm.base_url"
    assert lines[2].startswith("      classification: ") and lines[3].startswith("      source:         ")
    assert lines[4] == '      value:          "http://127.0.0.1:11434/v1"'
    assert "No local approval on file at " in result.output and "Run 'kriya authority approve' to grant it." in result.output


def test_approve_with_nothing_pending_writes_nothing(authority_ws):
    ws, home = authority_ws
    result = _invoke(ws, "--config", "plain.yaml", "authority", "approve", "--confirm")
    assert result.exit_code == 0 and result.output.endswith("Nothing to approve.\n")
    assert _artifacts(home) == []


def test_approve_asks_and_a_declined_approval_exits_1_without_an_artifact(authority_ws):
    ws, home = authority_ws
    result = _invoke(ws, "--config", "kriya.yaml", "authority", "approve", stdin="n\n")
    assert result.exit_code == 1
    assert "Grant explicit approval to exactly the security-authority field(s) listed above?" in result.output
    assert result.output.endswith("Not approved - no artifact written.\n")
    assert _artifacts(home) == []


def test_an_out_path_inside_the_workspace_is_refused_and_writes_nothing(authority_ws):
    ws, home = authority_ws
    result = _invoke(ws, "--config", "kriya.yaml", "authority", "approve", "--confirm", "--out", str(ws / "approval.json"))
    assert result.exit_code == 1, result.output
    assert "[TRUST_PATH_INSIDE_WORKSPACE]" in result.output and "Traceback" not in result.output
    assert not (ws / "approval.json").exists() and _artifacts(home) == []


def test_the_default_store_explains_the_ci_hand_off_and_an_out_path_does_not(authority_ws, tmp_path):
    ws, home = authority_ws
    approved = _invoke(ws, "--config", "kriya.yaml", "authority", "approve", "--confirm")
    assert approved.exit_code == 0, approved.output
    assert "Approved. Artifact written to " in approved.output
    assert "This is the default local store for this workspace" in approved.output and _artifacts(home)
    out = tmp_path / "ci" / "approval.json"
    exported = _invoke(ws, "--config", "kriya.yaml", "authority", "approve", "--confirm", "--out", str(out))
    assert exported.exit_code == 0, exported.output
    assert out.is_file() and "This is the default local store" not in exported.output


def test_revoke_says_what_it_removed(authority_ws):
    ws, _ = authority_ws
    assert _invoke(ws, "--config", "kriya.yaml", "authority", "approve", "--confirm").exit_code == 0
    revoked = _invoke(ws, "--config", "kriya.yaml", "authority", "revoke")
    assert revoked.exit_code == 0 and revoked.output.startswith("Revoked local approval at ")
    again = _invoke(ws, "--config", "kriya.yaml", "authority", "revoke")
    assert again.exit_code == 0 and again.output.startswith("No local approval on file at ")
    assert again.output.endswith(" - nothing to revoke.\n")


# --------------------------------------------------------------------------------------------- metrics (PRD-033)

@pytest.fixture(name="metrics_ws")
def _metrics_ws(tmp_path, monkeypatch):
    from pathlib import Path

    from kriya.core.trace import TraceLogger
    from kriya.metrics import adjudication as adj

    monkeypatch.setenv(adj.ENV_HOME_OVERRIDE, str(tmp_path / "trusted" / "adjudications"))
    ws = tmp_path / "ws"
    ws.mkdir()
    TraceLogger(str(Path(os.environ["KRIYA_STATE_DIR"]) / "traces.db")).log_run(
        run_id="run-1", goal="g", duration_sec=1.0, attempts=1, status="success", files_modified=[],
        generation_metrics={"total_wall_seconds": 1.0})
    return ws, tmp_path / "trusted" / "adjudications" / adj.STORE_FILENAME


def test_a_metrics_report_includes_run_record_facts_only_for_a_given_workspace(metrics_ws, tmp_path):
    from kriya.metrics.derive import MEASURED, NOT_PROVIDED

    ws, _ = metrics_ws
    other = tmp_path / "other"
    other.mkdir()
    without = json.loads(_invoke(ws, "metrics", "report", "--json").output)
    measured = _invoke(ws, "metrics", "report", "--json", "--workspace", str(other))
    assert measured.exit_code == 0, measured.output
    block = json.loads(measured.output)["content"]["run_records"]
    assert without["content"]["run_records"] == {"status": NOT_PROVIDED}
    assert (block["status"], block["workspaces"], block["missing_workspaces"], block["run_records"]) == (MEASURED, 1, [], 0)


def test_an_adjudication_confirmed_at_the_prompt_is_recorded(metrics_ws):
    from kriya.metrics import adjudication as adj

    ws, store = metrics_ws
    result = _invoke(ws, "metrics", "adjudicate", "run-1", "--verdict", "confirmed_success", "--evidence", "review",
                     "--adjudicator", "qa", stdin="y\n")
    assert result.exit_code == 0, result.output
    assert "Record this adjudication?" in result.output and "Recorded " in result.output
    assert [a.verdict for a in adj.load_adjudications(str(store)).records] == ["confirmed_success"]


def test_an_invalid_adjudication_store_is_listed_and_exits_2(metrics_ws):
    ws, store = metrics_ws
    store.parent.mkdir(parents=True)
    store.write_text("not json")
    result = _invoke(ws, "metrics", "adjudications", "--json")
    assert result.exit_code == 2
    assert json.loads(result.output)["status"] == "invalid"


# --------------------------------------------------------------------------------------------- runs (PRD-008)

def test_a_clean_workspace_status_advises_nothing(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    result = _invoke(ws, "runs", "status", "--workspace", str(ws))
    assert result.exit_code == 0, result.output
    assert result.output.endswith("\nNothing needs recovery.\n") and "Status: CLEAN" in result.output


def test_the_recovery_display_names_states_and_shows_detail_only_for_unapplied_operations(capsys):
    from types import SimpleNamespace

    from kriya import cli
    from kriya.control import recovery

    assert cli._runs_short("0123456789abcdef") == "0123456789ab" and cli._runs_short(None) == "-"
    assert cli._runs_state({"outside_workspace": True}) == "outside the workspace"
    assert cli._runs_state({"exists": True, "sha256": "f" * 64, "mode": 0o644}) == f"{'f' * 12} mode 0o644"
    def op(classification, path, before):
        return SimpleNamespace(classification=classification, kind="write", target_path=path,
                               expected_before=before, expected_after={"text_revision": "a" * 20},
                               current={"outside_workspace": True})

    finding = SimpleNamespace(transaction_id="tx-1", state="PENDING", outcome="PARTIAL", owner_run_id=None, error=None,
                              operations=[op(recovery.OP_APPLIED, "done.py", {"not_a_regular_file": True}),
                                          op("PENDING", "todo.py", {"exists": False})],
                              roll_forward_refusal=None, leftover_staged_files=[])
    assessment = SimpleNamespace(workspace_path="/w", status="MANUAL_ACTION_REQUIRED", run_active=None, records=[],
                                 evidence=[finding], unreadable_records=[], evidence_error=None, nested_workspace_state=(), nested_scan_complete=True)
    cli._print_recovery_assessment(assessment)
    lines = capsys.readouterr().out.splitlines()
    detail = [line for line in lines if line.strip().startswith("before ")]
    assert detail == ["      before absent | after text aaaaaaaaaaaa | now outside the workspace"]
    assert any(line.strip().startswith("PENDING") and line.endswith("todo.py") for line in lines)


# --------------------------------------------------------------------------------------------- static-analysis (PRD-031A)

@pytest.fixture(name="plain_ws")
def _plain_ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "kriya.yaml").write_text(yaml.safe_dump(PLAIN))
    return ws


def test_static_analysis_status_json_is_one_json_document_for_the_real_workspace(plain_ws):
    from unittest.mock import patch

    with patch("kriya.static_analysis.doctor.static_analysis_status_report", return_value={"b": 1, "a": [2]}) as report:
        result = _invoke(plain_ws, "static-analysis", "status", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"a": [2], "b": 1}
    assert report.call_args.args[1] == os.path.realpath(str(plain_ws))


def test_a_static_analysis_scan_exits_by_whether_the_result_permits_a_commit(plain_ws):
    from types import SimpleNamespace
    from unittest.mock import patch

    from kriya.static_analysis.operator_scan import OperatorScanError

    def result(permits):
        return SimpleNamespace(evidence={"evidence_path": "e.json"}, permits_commit=permits, gap=None,
                               outcome=SimpleNamespace(value="PASS" if permits else "BLOCKED"))

    with patch("kriya.static_analysis.service.banner", return_value="BANNER"):
        for permits, code in ((True, 0), (False, 1)):
            with patch("kriya.static_analysis.operator_scan.run_operator_scan", return_value=result(permits)):
                outcome = _invoke(plain_ws, "static-analysis", "scan")
            assert outcome.exit_code == code, outcome.output
            assert f"permits_commit: {permits}" in outcome.output
        with patch("kriya.static_analysis.operator_scan.run_operator_scan", side_effect=OperatorScanError("no git")):
            failed = _invoke(plain_ws, "static-analysis", "scan")
    assert failed.exit_code == 2 and "static-analysis scan failed: no git" in failed.output


def test_an_unusable_waiver_store_exits_2(plain_ws):
    from unittest.mock import patch

    with patch("kriya.static_analysis.waivers.load_waivers", side_effect=ValueError("bad store")):
        result = _invoke(plain_ws, "static-analysis", "waivers")
    assert result.exit_code == 2 and "waiver store unusable: bad store" in result.output
