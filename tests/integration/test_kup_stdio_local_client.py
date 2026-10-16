"""The real desktop chain below the Electron host (full modularization, slice B4; W2 completion check).

A REAL ``kriya kup-stdio`` child process (the one the Electron host spawns: ``main(['kup-stdio'])``) answers newline-delimited requests;
inside it the KUP adapter translates each request into ONE ``LocalKriyaClient`` call, which reaches the application service and the
existing domain behaviour. Every envelope it prints must be byte-identical, after the goldens' own normalization, to the envelope the
same operation answered BEFORE the extraction (``tests/golden/kup/application*/``, written from e91f05d and 03f2932): ``capabilities``,
``history.list`` and ``execution.preflight`` (the W2 minimum) plus ``history.detail``, ``launch.status``, ``change_approval.review`` and
``configuration.approval_status``.

The child gets the test's isolated state/authority directories through its environment, runs from a folder with no ``kriya.yaml`` (so it
loads the packaged configuration, per request, as the host does) and has exactly one thing pinned, in-process before ``main``: the
toolchain probe ``kriya.kup.execution.tool_visibility`` (the documented patch point), because the golden records a fixed healthy git
and this machine's PATH would otherwise decide the ``toolchains`` block. The configuration review fingerprint is salted per process by
design, so only its shape is compared (see the normalizer below).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from typing import Any, Callable, Dict, List, Tuple

import test_application_services as reads
import test_application_write_services as writes

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

read_scenario = reads.scenario     # the B2 golden state (trace runs, evidence stores, an acquired snapshot)
write_scenario = writes.scenario   # the B3 golden state (held reviews, a guarded folder)


def _serve(requests: List[Tuple[str, Dict[str, Any]]], cwd: str) -> Tuple[List[Dict[str, Any]], str]:
    preamble = textwrap.dedent(f"""
        import kriya.kup.execution as _execution
        _execution.tool_visibility = lambda: [dict(t) for t in {writes.TOOLS!r}]
    """)
    code = preamble + "\nfrom kriya.cli import main\nmain(['kup-stdio'])\n"
    env = {**os.environ, "PYTHONPATH": ROOT}
    lines = "".join(json.dumps({"id": "golden", "schema_version": 1, "operation": op, "arguments": args}) + "\n" for op, args in requests)
    proc = subprocess.run([sys.executable, "-c", code], input=lines.encode(), capture_output=True, env=env, cwd=cwd, timeout=180, check=False)
    out = [json.loads(line) for line in proc.stdout.decode().splitlines()]
    assert proc.returncode == 0 and out and out[0].get("event") == "ready", proc.stderr.decode()[-3000:]
    return out[1:], proc.stderr.decode()


def _compare(cases: List[Tuple[str, str, Dict[str, Any]]], golden_dir, normalize: Callable[[Any], Any], cwd: str) -> Dict[str, str]:
    responses, stderr = _serve([(op, args) for _name, op, args in cases], cwd)
    assert len(responses) == len(cases), stderr[-3000:]
    mismatched = {}
    for (name, _op, _args), response in zip(cases, responses, strict=True):
        assert response["id"] == "golden" and response["ok"] is True, response
        text = json.dumps(normalize(response["envelope"]), indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        if text != (golden_dir / f"{name}.json").read_text(encoding="utf-8"):
            mismatched[name] = text

    return mismatched


def test_capabilities_and_history_through_the_real_child_are_the_pre_extraction_envelopes(read_scenario, tmp_path):
    cwd = tmp_path / "host-cwd"
    cwd.mkdir()
    snap = read_scenario["snapshot_id"]
    cases = [("capabilities", "capabilities", {}),
             ("history_list", "history.list", {"snapshot_id": snap, "limit": 10}),
             ("history_list_scoped", "history.list", {"snapshot_id": snap, "workspace": read_scenario["ws"], "limit": 5}),
             ("history_detail", "history.detail", {"snapshot_id": snap, "run_id": "t-gate"}),
             ("history_list_no_snapshot", "history.list", {})]
    mismatched = _compare(cases, reads.GOLDEN, lambda env: reads._normalize(env, read_scenario), str(cwd))
    assert mismatched == {}, f"the kup-stdio child answered differently from the pre-B2 envelopes: {sorted(mismatched)}"


def test_preflight_status_review_and_configuration_through_the_real_child_are_the_pre_extraction_envelopes(write_scenario, tmp_path):
    cwd = tmp_path / "host-cwd"
    cwd.mkdir()
    ws = write_scenario["ws"]
    cases = [("execution_preflight", "execution.preflight", {"workspace": ws}),
             ("execution_preflight_missing", "execution.preflight", {"workspace": ws + "-missing"}),
             ("launch_status", "launch.status", {"workspace": ws, "launch_id": writes.LID}),
             ("change_review", "change_approval.review", {"workspace": ws, "launch_id": writes.LID, "review_id": write_scenario["review"]}),
             ("configuration_status_required", "configuration.approval_status", {"workspace": os.path.join(write_scenario["tmp"], "guarded")})]
    def normalize(env: Dict[str, Any]) -> Any:
        # The review fingerprint is salted per PROCESS by design (kriya/config/authority_approval.py: _REVIEW_FINGERPRINT_SALT, "a review
        # taken before a restart of the Kriya service is refused as stale"), so the child's value cannot equal this process's: its shape is
        # checked and it is replaced by the token the golden carries.
        data = env.get("data") or {}
        if env.get("operation") == "configuration.approval_status":
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", data["fingerprint"]), data
            data["fingerprint"] = write_scenario["fingerprint"]
        return writes.normalize(env, write_scenario)

    mismatched = _compare(cases, writes.GOLDEN, normalize, str(cwd))
    assert mismatched == {}, f"the kup-stdio child answered differently from the pre-B3 envelopes: {sorted(mismatched)}"
