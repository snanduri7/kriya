"""Wave 2 Slice A: the operator control plane is inaccessible from project/model-controlled containment - on REAL Docker (the packaged default backend), not the test stand-in.

`kriya runs tool abandon|retry` edits `.kriya/control/state.json` and appends to `.kriya/control/decisions.jsonl` on the host. A contained child (the shape every project-controlled
execution takes) must be unable to (a) read, write, replace, rename, truncate or unlink those files, (b) rename/replace `.kriya` itself to plant a forged copy at the host-visible
name, or (c) run the operator command at all (no `kriya` in the container). The negative control removes the structural anchor and shows the SAME attack then forges the state the
disposition command would trust - so a pass of the positive test is evidence about the boundary, not about the attack script."""
import json

from test_d2_authority_namespace import NEEDS_DOCKER, _validator_run

from kriya.control.decisions import DecisionLedger
from kriya.control.persistence import control_state_path, load_control_state, save_control_state
from kriya.control.state import SUBTASK_STATE_TOOL_FAILED, ControlState
from kriya.tools import containment_oci

_ATTACK = r'''
import json, os, shutil, subprocess, sys
WS = "/kriya/workspace"; K = WS + "/.kriya"; C = K + "/control"
out = {}
def attempt(name, fn):
    try:
        fn(); out[name] = "SUCCEEDED"
    except BaseException as error:
        out[name] = type(error).__name__ + ":" + str(getattr(error, "errno", ""))
FORGED = json.dumps({"schema_version": 1, "run_id": "r1", "subtask_states": {"t1": "pending"}, "tool_attempts": {"t1": 9}})
def overwrite_state():
    open(C + "/state.json", "w").write(FORGED)
def append_ledger():
    open(C + "/decisions.jsonl", "a").write('{"type": "tool_disposition"}\n')
def truncate_state():
    os.truncate(C + "/state.json", 0)
def unlink_state():
    os.unlink(C + "/state.json")
def replace_state():
    open(WS + "/forged.json", "w").write(FORGED)
    os.replace(WS + "/forged.json", C + "/state.json")
def plant_forged_control():
    os.makedirs(WS + "/decoy/control", exist_ok=True)
    open(WS + "/decoy/control/state.json", "w").write(FORGED)
    os.rename(K, WS + "/.kriya.aside")
    os.rename(WS + "/decoy", K)
for name, fn in (("overwrite_state", overwrite_state), ("append_ledger", append_ledger), ("truncate_state", truncate_state), ("unlink_state", unlink_state),
                 ("replace_state", replace_state), ("plant_forged_control", plant_forged_control)):
    attempt(name, fn)
out["state_visible_to_the_child"] = os.path.exists(C + "/state.json") and os.path.getsize(C + "/state.json") > 0
out["kriya_command_on_path"] = shutil.which("kriya") is not None
r = subprocess.run([sys.executable, "-c", "import kriya"], capture_output=True, text=True)
out["kriya_importable"] = r.returncode == 0
r = subprocess.run(["sh", "-c", "kriya runs tool retry t1 --attempt 1 --confirm"], capture_output=True, text=True)
out["operator_command_ran"] = r.returncode == 0
print(json.dumps(out))
'''


def _workspace_with_a_terminal_failed_tool(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".kriya" / "control").mkdir(parents=True)
    (ws / ".kriya" / "checkpoints").mkdir()
    save_control_state(str(ws), ControlState(schema_version=1, run_id="r1", subtask_states={"t1": SUBTASK_STATE_TOOL_FAILED}, tool_attempts={"t1": 1}), durable=True)
    ledger = DecisionLedger()
    ledger.append_to_file(str(ws), ledger.record("subtask_attempt", subtask_id="t1", attempt=1, execution_method="tool", status="failed"), durable=True)
    (ws / "attack.py").write_text(_ATTACK, encoding="utf-8")
    return ws


@NEEDS_DOCKER
def test_real_oci_a_contained_child_cannot_reach_the_operator_control_plane(tmp_path):
    ws = _workspace_with_a_terminal_failed_tool(tmp_path)
    state_before = (ws / ".kriya" / "control" / "state.json").read_bytes()
    ledger_before = (ws / ".kriya" / "control" / "decisions.jsonl").read_bytes()
    report = _validator_run(ws, ["python3", "attack.py"])
    for attack in ("overwrite_state", "append_ledger", "truncate_state", "unlink_state", "replace_state", "plant_forged_control"):
        assert report[attack] != "SUCCEEDED", (attack, report)
    assert report["state_visible_to_the_child"] is False, report                  # the control leaf is masked: the child does not even see the state
    assert report["kriya_command_on_path"] is False and report["kriya_importable"] is False and report["operator_command_ran"] is False, report
    assert (ws / ".kriya" / "control" / "state.json").read_bytes() == state_before               # the host-side bytes the command trusts are unchanged
    assert (ws / ".kriya" / "control" / "decisions.jsonl").read_bytes() == ledger_before
    assert not (ws / ".kriya.aside").exists()
    assert load_control_state(str(ws)).subtask_states["t1"] == SUBTASK_STATE_TOOL_FAILED and load_control_state(str(ws)).tool_attempts == {"t1": 1}


@NEEDS_DOCKER
def test_real_oci_negative_control_without_the_anchor_the_same_attack_forges_the_state_the_disposition_would_trust(tmp_path, monkeypatch):
    """If this stops reproducing, the positive test above no longer proves the boundary does anything."""
    ws = _workspace_with_a_terminal_failed_tool(tmp_path)
    monkeypatch.setattr(containment_oci, "workspace_anchor_args", lambda workspace_host: [])
    report = _validator_run(ws, ["python3", "attack.py"])
    assert report["plant_forged_control"] == "SUCCEEDED", report
    forged = json.loads(open(control_state_path(str(ws)), encoding="utf-8").read())
    assert forged["tool_attempts"] == {"t1": 9} and forged["subtask_states"]["t1"] == "pending"      # the host-visible state is now the attacker's
